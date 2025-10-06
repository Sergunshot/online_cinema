import json
from decimal import Decimal
from datetime import datetime, timezone

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from config.dependencies import get_current_user_id, get_accounts_email_notificator
from database.models.orders import Order, OrderItem, OrderStatusEnum
from database.models.payments import Payment, PaymentItem, PaymentStatusEnum
from database.models.accounts import User

PAYMENTS = "/api/v1/payments"


# -------- helpers --------

async def _first(db: AsyncSession, stmt):
    res = await db.execute(stmt)
    return res.scalars().first()


def _dummy_email_sender():
    class DummySender:
        async def send_payment_email(self, *a, **k): pass
        async def send_cancellation_email(self, *a, **k): pass
        async def send_refund_email(self, *a, **k): pass
    return DummySender()


async def _create_order_with_item(
    db: AsyncSession,
    user: User,
    movie,
    status: OrderStatusEnum = OrderStatusEnum.PENDING,
):
    order = Order(user_id=user.id, status=status, total_amount=Decimal("0.00"))
    db.add(order)
    await db.flush()

    item = OrderItem(
        order_id=order.id,
        movie_id=movie.id,
        price_at_order=movie.price,
    )
    db.add(item)

    order.total_amount = item.price_at_order
    await db.commit()
    await db.refresh(order)
    return order, item


# -------- tests --------

@pytest.mark.asyncio
async def test_create_payment_success(
    app, client: AsyncClient, db_session: AsyncSession, test_user: User, test_movie
):
    # override current user
    async def _current_user():
        return test_user.id
    app.dependency_overrides[get_current_user_id] = _current_user

    order, item = await _create_order_with_item(db_session, test_user, test_movie)

    payload = {
        "order_id": order.id,
        "amount": str(order.total_amount),
        "payment_method": "card",
        "external_payment_id": "pi_123",
        "payment_items": [{"order_item_id": item.id, "price_at_payment": str(item.price_at_order)}],
    }

    r = await client.post(f"{PAYMENTS}/", json=payload)
    assert r.status_code == 201, r.text
    data = r.json()
    assert data["order_id"] == order.id
    assert data["user_id"] == test_user.id
    assert data["amount"] == str(order.total_amount)
    assert data["client_secret"]  # "mock_client_secret_123"
    assert len(data["payment_items"]) == 1
    assert data["payment_items"][0]["order_item_id"] == item.id

    # cleanup overrides
    app.dependency_overrides.pop(get_current_user_id, None)


@pytest.mark.asyncio
async def test_create_payment_order_not_found(app, client: AsyncClient, test_user: User):
    async def _current_user():
        return test_user.id
    app.dependency_overrides[get_current_user_id] = _current_user

    payload = {
        "order_id": 99999,
        "amount": "0.00",
        "payment_method": "card",
        "external_payment_id": "pi_missing",
        "payment_items": [],
    }
    r = await client.post(f"{PAYMENTS}/", json=payload)
    assert r.status_code == 404
    assert r.json()["detail"] == "Order not found"

    app.dependency_overrides.pop(get_current_user_id, None)


@pytest.mark.asyncio
async def test_refund_payment_success(
    app, client: AsyncClient, db_session: AsyncSession, test_user: User, test_movie
):
    # overrides
    async def _current_user():
        return test_user.id
    app.dependency_overrides[get_current_user_id] = _current_user
    app.dependency_overrides[get_accounts_email_notificator] = _dummy_email_sender

    order, item = await _create_order_with_item(db_session, test_user, test_movie, status=OrderStatusEnum.PAID)

    payment = Payment(
        user_id=test_user.id,
        order_id=order.id,
        status=PaymentStatusEnum.successful,
        amount=order.total_amount,
        external_payment_id="mock-id-123",
        payment_method="card",
    )
    db_session.add(payment)
    await db_session.flush()

    db_session.add(PaymentItem(
        payment_id=payment.id,
        order_item_id=item.id,
        price_at_payment=item.price_at_order,
    ))
    await db_session.commit()
    await db_session.refresh(payment)

    r = await client.post(f"{PAYMENTS}/{payment.id}/refund/")
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "refunded"

    # cleanup
    app.dependency_overrides.pop(get_current_user_id, None)
    app.dependency_overrides.pop(get_accounts_email_notificator, None)


@pytest.mark.asyncio
async def test_refund_wrong_status(
    app, client: AsyncClient, db_session: AsyncSession, test_user: User, test_movie
):
    async def _current_user():
        return test_user.id
    app.dependency_overrides[get_current_user_id] = _current_user
    app.dependency_overrides[get_accounts_email_notificator] = _dummy_email_sender

    order, item = await _create_order_with_item(db_session, test_user, test_movie)

    payment = Payment(
        user_id=test_user.id,
        order_id=order.id,
        status=PaymentStatusEnum.pending,
        amount=order.total_amount,
        external_payment_id="pi_pending",
        payment_method="card",
    )
    db_session.add(payment)
    await db_session.flush()
    db_session.add(PaymentItem(
        payment_id=payment.id, order_item_id=item.id, price_at_payment=item.price_at_order
    ))
    await db_session.commit()

    r = await client.post(f"{PAYMENTS}/{payment.id}/refund/")
    assert r.status_code == 400
    assert r.json()["detail"] == "Only successful payments can be refunded"

    app.dependency_overrides.pop(get_current_user_id, None)
    app.dependency_overrides.pop(get_accounts_email_notificator, None)


@pytest.mark.asyncio
async def test_get_payment_history_only_current_user(
    app, client: AsyncClient, db_session: AsyncSession, test_user: User, test_movie
):
    other = User.create(email="other@mate.com", raw_password="Qwerty123!", group_id=1)
    other.is_active = True
    db_session.add(other)
    await db_session.commit()

    async def _current_user():
        return test_user.id
    app.dependency_overrides[get_current_user_id] = _current_user

    o1, i1 = await _create_order_with_item(db_session, test_user, test_movie, status=OrderStatusEnum.PAID)
    p1 = Payment(user_id=test_user.id, order_id=o1.id, status=PaymentStatusEnum.successful,
                 amount=o1.total_amount, external_payment_id="p1", payment_method="card")
    db_session.add(p1)
    await db_session.flush()
    db_session.add(PaymentItem(payment_id=p1.id, order_item_id=i1.id, price_at_payment=i1.price_at_order))

    o2, i2 = await _create_order_with_item(db_session, other, test_movie, status=OrderStatusEnum.PAID)
    p2 = Payment(user_id=other.id, order_id=o2.id, status=PaymentStatusEnum.successful,
                 amount=o2.total_amount, external_payment_id="p2", payment_method="card")
    db_session.add(p2)
    await db_session.flush()
    db_session.add(PaymentItem(payment_id=p2.id, order_item_id=i2.id, price_at_payment=i2.price_at_order))
    await db_session.commit()

    r = await client.get(f"{PAYMENTS}/history/")
    assert r.status_code == 200
    items = r.json()
    assert all(p["user_id"] == test_user.id for p in items)
    assert {p["external_payment_id"] for p in items} == {"p1"}

    app.dependency_overrides.pop(get_current_user_id, None)


@pytest.mark.asyncio
async def test_get_admin_payment_history_filters(
    client: AsyncClient, db_session: AsyncSession, test_user: User, test_movie
):
    o1, i1 = await _create_order_with_item(db_session, test_user, test_movie, status=OrderStatusEnum.PAID)
    p1 = Payment(user_id=test_user.id, order_id=o1.id, status=PaymentStatusEnum.successful,
                 amount=o1.total_amount, external_payment_id="a1", payment_method="card",
                 created_at=datetime(2024, 1, 1, tzinfo=timezone.utc))
    o2, i2 = await _create_order_with_item(db_session, test_user, test_movie, status=OrderStatusEnum.PAID)
    p2 = Payment(user_id=test_user.id, order_id=o2.id, status=PaymentStatusEnum.canceled,
                 amount=o2.total_amount, external_payment_id="a2", payment_method="card",
                 created_at=datetime(2024, 1, 10, tzinfo=timezone.utc))
    o3, i3 = await _create_order_with_item(db_session, test_user, test_movie, status=OrderStatusEnum.PAID)
    p3 = Payment(user_id=test_user.id, order_id=o3.id, status=PaymentStatusEnum.refunded,
                 amount=o3.total_amount, external_payment_id="a3", payment_method="card",
                 created_at=datetime(2024, 2, 1, tzinfo=timezone.utc))
    db_session.add_all([p1, p2, p3])
    await db_session.flush()
    db_session.add_all([
        PaymentItem(payment_id=p1.id, order_item_id=i1.id, price_at_payment=i1.price_at_order),
        PaymentItem(payment_id=p2.id, order_item_id=i2.id, price_at_payment=i2.price_at_order),
        PaymentItem(payment_id=p3.id, order_item_id=i3.id, price_at_payment=i3.price_at_order),
    ])
    await db_session.commit()

    qs = "?user_id={uid}&start_date=2024-01-01T00:00:00&end_date=2024-01-31T23:59:59&payment_status=successful".format(
        uid=test_user.id
    )
    r = await client.get(f"{PAYMENTS}/admin/payments/{qs}")
    assert r.status_code == 200
    ids = {p["external_payment_id"] for p in r.json()}
    assert ids == {"a1"}


@pytest.mark.asyncio
async def test_stripe_webhook_updates_status_succeeded(
    app, client: AsyncClient, db_session: AsyncSession, test_user: User, test_movie
):
    app.dependency_overrides[get_accounts_email_notificator] = _dummy_email_sender

    order, item = await _create_order_with_item(
        db_session, test_user, test_movie, status=OrderStatusEnum.PAID
    )
    payment = Payment(
        user_id=test_user.id,
        order_id=order.id,
        status=PaymentStatusEnum.pending,
        amount=order.total_amount,
        external_payment_id="pi_success",
        payment_method="card",
    )
    db_session.add(payment)
    await db_session.flush()
    db_session.add(
        PaymentItem(
            payment_id=payment.id,
            order_item_id=item.id,
            price_at_payment=item.price_at_order,
        )
    )
    await db_session.commit()

    event = {"type": "payment_intent.succeeded", "data": {"object": {"id": "pi_success"}}}
    r = await client.post(
        f"{PAYMENTS}/stripe/webhook/",
        content=json.dumps(event),
        headers={"Stripe-Signature": "anything"},
    )
    assert r.status_code == 200

    db_session.expire_all()
    res = await db_session.execute(
        select(Payment.status).where(Payment.external_payment_id == "pi_success")
    )
    (status_value,) = res.one()
    assert status_value == PaymentStatusEnum.successful

    app.dependency_overrides.pop(get_accounts_email_notificator, None)


@pytest.mark.asyncio
async def test_stripe_webhook_canceled_and_refunded(
    app, client: AsyncClient, db_session: AsyncSession, test_user: User, test_movie
):
    app.dependency_overrides[get_accounts_email_notificator] = _dummy_email_sender

    oc, ic = await _create_order_with_item(
        db_session, test_user, test_movie, status=OrderStatusEnum.PAID
    )
    pc = Payment(
        user_id=test_user.id,
        order_id=oc.id,
        status=PaymentStatusEnum.pending,
        amount=oc.total_amount,
        external_payment_id="pi_cancel",
        payment_method="card",
    )
    db_session.add(pc)
    await db_session.flush()
    db_session.add(
        PaymentItem(
            payment_id=pc.id,
            order_item_id=ic.id,
            price_at_payment=ic.price_at_order,
        )
    )

    orf, irf = await _create_order_with_item(
        db_session, test_user, test_movie, status=OrderStatusEnum.PAID
    )
    prf = Payment(
        user_id=test_user.id,
        order_id=orf.id,
        status=PaymentStatusEnum.successful,
        amount=orf.total_amount,
        external_payment_id="pi_refund",
        payment_method="card",
    )
    db_session.add(prf)
    await db_session.flush()
    db_session.add(
        PaymentItem(
            payment_id=prf.id,
            order_item_id=irf.id,
            price_at_payment=irf.price_at_order,
        )
    )
    await db_session.commit()

    ev1 = {"type": "payment_intent.canceled", "data": {"object": {"id": "pi_cancel"}}}
    r1 = await client.post(
        f"{PAYMENTS}/stripe/webhook/",
        content=json.dumps(ev1),
        headers={"Stripe-Signature": "anything"},
    )
    assert r1.status_code == 200

    ev2 = {"type": "charge.refunded", "data": {"object": {"payment_intent": "pi_refund"}}}
    r2 = await client.post(
        f"{PAYMENTS}/stripe/webhook/",
        content=json.dumps(ev2),
        headers={"Stripe-Signature": "anything"},
    )
    assert r2.status_code == 200

    db_session.expire_all()

    res_cancel = await db_session.execute(
        select(Payment.status).where(Payment.external_payment_id == "pi_cancel")
    )
    (status_cancel,) = res_cancel.one()

    res_refund = await db_session.execute(
        select(Payment.status).where(Payment.external_payment_id == "pi_refund")
    )
    (status_refund,) = res_refund.one()

    assert status_cancel == PaymentStatusEnum.canceled
    assert status_refund == PaymentStatusEnum.refunded

    app.dependency_overrides.pop(get_accounts_email_notificator, None)
