from datetime import datetime
from decimal import Decimal
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Request, BackgroundTasks, status
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select

import stripe
from sqlalchemy.orm import selectinload

from notifications import EmailSenderInterface
from schemas.payments import PaymentCreate, PaymentResponse, PaymentItemResponse
from database.models import (
    Order,
    User,
    Payment,
    PaymentStatusEnum,
    PaymentItem,
    OrderItem,
)

from config.dependencies import get_current_user_id, get_accounts_email_notificator
from database import get_db
from config import settings as settings

router = APIRouter()

stripe.api_key = settings.STRIPE_SECRET_KEY
STRIPE_CURRENCY = settings.STRIPE_CURRENCY
STRIPE_WEBHOOK_SECRET = settings.STRIPE_WEBHOOK_SECRET


async def create_stripe_payment_intent(amount: Decimal):
    """
    Creates a Stripe payment intent with the given amount.
    """
    try:
        intent = stripe.PaymentIntent.create(
            amount=int(amount * 100),
            currency=STRIPE_CURRENCY,
            payment_method_types=["card"],
        )
        return intent
    except stripe.error.StripeError as e:
        raise HTTPException(status_code=500, detail=f"Stripe error: {str(e)}")


@router.post("/", response_model=PaymentResponse, status_code=status.HTTP_201_CREATED)
async def create_payment(
    payment_data: PaymentCreate,
    db: AsyncSession = Depends(get_db),
    current_user_id: int = Depends(get_current_user_id),
):
    # 1. Check if the order exists
    order = await db.get(Order, payment_data.order_id)
    if not order:
        raise HTTPException(status_code=404, detail="Order not found")

    # 2. Create a new payment
    new_payment = Payment(
        user_id=current_user_id,
        order_id=order.id,
        amount=order.total_amount,
        status="pending",
        external_payment_id=payment_data.external_payment_id,
        payment_method=payment_data.payment_method,
    )
    db.add(new_payment)
    await db.flush()  # Generates ID for new_payment

    # 3. Retrieve order_items from the database
    result = await db.execute(select(OrderItem).where(OrderItem.order_id == order.id))
    order_items = result.scalars().all()
    if not order_items:
        raise HTTPException(status_code=400, detail="No items in order")

    # 4. Create payment_items
    payment_items = [
        PaymentItem(
            payment_id=new_payment.id,
            order_item_id=item.id,
            price_at_payment=item.price_at_order,
        )
        for item in order_items
    ]
    db.add_all(payment_items)
    await db.commit()

    # 5. Refresh payment and retrieve payment_items
    await db.refresh(new_payment)
    result = await db.execute(
        select(PaymentItem).where(PaymentItem.payment_id == new_payment.id)
    )
    payment_item_objs = result.scalars().all()

    # 6. Manually form the response (including client_secret)
    return PaymentResponse(
        id=new_payment.id,
        user_id=new_payment.user_id,
        order_id=new_payment.order_id,
        created_at=new_payment.created_at,
        status=new_payment.status,
        amount=new_payment.amount,
        external_payment_id=new_payment.external_payment_id,
        payment_method=new_payment.payment_method,
        client_secret="mock_client_secret_123",  # or generate dynamically
        payment_items=[
            PaymentItemResponse(
                id=item.id,
                payment_id=item.payment_id,
                order_item_id=item.order_item_id,
                price_at_payment=item.price_at_payment,
            )
            for item in payment_item_objs
        ],
    )


@router.post("/{payment_id}/refund/", response_model=PaymentResponse)
async def refund_payment(
    payment_id: int,
    db: AsyncSession = Depends(get_db),
    current_user_id: int = Depends(get_current_user_id),
    background_tasks: BackgroundTasks = BackgroundTasks(),
    email_sender: EmailSenderInterface = Depends(get_accounts_email_notificator()),
):
    """
    Processes a refund for a successful payment.
    """
    print("Refund attempt for payment_id:", payment_id, "by user_id:", current_user_id)

    result = await db.execute(
        select(Payment).filter(
            Payment.id == payment_id, Payment.user_id == current_user_id
        )
    )
    payment = result.scalars().first()

    if not payment:
        print("Payment not found!")
        raise HTTPException(status_code=404, detail="Payment not found")

    if payment.status != PaymentStatusEnum.successful:
        raise HTTPException(
            status_code=400, detail="Only successful payments can be refunded"
        )

    # # For test - uncomment this block and comment block below
    # if payment.external_payment_id == "mock-id-123":
    #     # This is a test, return success without calling Stripe
    #     payment.status = PaymentStatus.refunded
    #     await db.commit()
    #     await db.refresh(payment)
    #
    #     user_result = await db.execute(select(User).filter(User.id == current_user_id))
    #     user = user_result.scalars().first()
    #     if user:
    #         background_tasks.add_task(
    #             email_sender.send_refund_email, user.email, payment.amount
    #         )
    #
    #     return payment

    # Stripe refund inside try because it can catch errors from the external API
    try:
        refund = stripe.Refund.create(payment_intent=payment.external_payment_id)
    except stripe.error.StripeError as e:
        raise HTTPException(status_code=500, detail=f"Stripe refund error: {str(e)}")

    if refund.status == "succeeded":
        payment.status = PaymentStatusEnum.refunded
        await db.commit()
        await db.refresh(payment)

        user_result = await db.execute(select(User).filter(User.id == current_user_id))
        user = user_result.scalars().first()
        if user:
            background_tasks.add_task(
                email_sender.send_refund_email, user.email, payment.amount
            )

        return payment

    raise HTTPException(
        status_code=400,
        detail="Refund was not successful. Stripe returned status: " + refund.status,
    )


@router.get("/history/", response_model=List[PaymentResponse])
async def get_payment_history(
    db: AsyncSession = Depends(get_db),
    current_user_id: int = Depends(get_current_user_id),
):
    """
    Retrieves the payment history for the currently authenticated user.
    """
    result = await db.execute(
        select(Payment)
        .filter(Payment.user_id == current_user_id)
        .order_by(Payment.created_at.desc())
        .options(selectinload(Payment.payment_items))
    )
    return result.scalars().all()


@router.get("/admin/payments/", response_model=List[PaymentResponse])
async def get_admin_payment_history(
    user_id: Optional[int] = None,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    payment_status: Optional[PaymentStatusEnum] = None,
    db: AsyncSession = Depends(get_db),
):
    """
    Retrieve the payment history for administrators with optional filters.
    """
    query = select(Payment).options(selectinload(Payment.payment_items))

    if user_id is not None:
        query = query.filter(Payment.user_id == user_id)

    if start_date and end_date:
        try:
            start_dt = datetime.fromisoformat(start_date)
            end_dt = datetime.fromisoformat(end_date)
            query = query.filter(Payment.created_at.between(start_dt, end_dt))
        except ValueError:
            raise HTTPException(
                status_code=400,
                detail="Invalid date format. Use ISO format: YYYY-MM-DDTHH:MM:SS",
            )

    if payment_status is not None:
        query = query.filter(Payment.status == payment_status)

    result = await db.execute(query)
    return result.scalars().all()


@router.post("/stripe/webhook/")
async def stripe_webhook(
    request: Request,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
    email_sender: EmailSenderInterface = Depends(get_accounts_email_notificator),
):
    payload = await request.body()
    sig_header = request.headers.get("Stripe-Signature")

    if not sig_header:
        raise HTTPException(status_code=400, detail="Missing Stripe-Signature header")

    # # For test - uncomment this block and comment block below
    # import json
    #
    # try:
    #     event = json.loads(payload)
    # except Exception:
    #     raise HTTPException(status_code=400, detail="Invalid JSON")

    try:
        event = stripe.Webhook.construct_event(
            payload, sig_header, STRIPE_WEBHOOK_SECRET
        )
    except (ValueError, stripe.error.SignatureVerificationError):
        raise HTTPException(status_code=400, detail="Invalid webhook request")

    event_type = event["type"]
    data = event["data"]["object"]

    async def update_payment_status(
        external_id: str,
        new_status: PaymentStatusEnum,
        notify_func,
    ):
        result = await db.execute(
            select(Payment)
            .options(selectinload(Payment.user))
            .filter(Payment.external_payment_id == external_id)
        )
        payment = result.scalars().first()

        payment.status = new_status
        await db.commit()
        user_email = payment.user.email
        amount = payment.amount

        background_tasks.add_task(notify_func, user_email, amount)

    if event_type == "payment_intent.succeeded":
        await update_payment_status(
            data["id"], PaymentStatusEnum.successful, email_sender.send_payment_email
        )
    elif event_type == "payment_intent.canceled":
        await update_payment_status(
            data["id"], PaymentStatusEnum.canceled, email_sender.send_cancellation_email
        )
    elif event_type == "charge.refunded":
        await update_payment_status(
            data["payment_intent"],
            PaymentStatusEnum.refunded,
            email_sender.send_refund_email,
        )

    return {"status": "success"}
