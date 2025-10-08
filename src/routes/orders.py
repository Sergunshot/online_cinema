from fastapi import APIRouter, HTTPException, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select
from database import get_db
from database.models.orders import OrderStatusEnum
from schemas.orders import (
    OrderResponseSchema,
    OrderItemResponseSchema,
    OrderWithMoviesResponseSchema,
    OrderListResponseSchema,
)
from database.models import (
    Order,
    OrderItem,
    Movie,
    User,
)
from config.dependencies import get_current_user_id
from fastapi import status
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import joinedload
from sqlalchemy import func
from typing import Optional
from datetime import datetime
from routes.cart import fetch_existing_cart

router = APIRouter()


async def check_user_access(
    db: AsyncSession,
    current_user_id: int,
    resource_owner_id: int
) -> None:
    """
    Checks if the user with current_user_id has access to a resource
    owned by resource_owner_id.
    Access is granted to the admin or the resource owner.
    Raises HTTPException if access is denied.
    """
    if current_user_id == resource_owner_id:
        return

    result = await db.execute(
        select(User).filter(User.id == current_user_id)
    )
    current_user = result.scalar_one_or_none()

    if not current_user:
        raise HTTPException(status_code=404, detail="User not found")

    if current_user.group.name != "admin":
        raise HTTPException(status_code=403, detail="Access forbidden")


@router.get("/", response_model=OrderListResponseSchema)
async def get_orders(
        page: int = Query(1, ge=1, description="Page number (1-based index)"),
        per_page: int = Query(10, ge=1, le=20, description="Number of items per page"),
        status: Optional[str] = Query(None,
                                      description="Filter orders by status (e.g., 'pending', 'paid', 'canceled')"),
        user_id: Optional[int] = Query(None, description="Filter orders by user ID"),
        order_date: Optional[str] = Query(None, description="Filter orders by a specific date (YYYY-MM-DD)"),
        db: AsyncSession = Depends(get_db),
        current_user_id: int = Depends(get_current_user_id)
) -> OrderListResponseSchema:
    # Get the current user
    current_user_result = await db.execute(
        select(User).options(joinedload(User.group)).filter(User.id == current_user_id)
    )
    current_user = current_user_result.scalar_one_or_none()

    if not current_user:
        raise HTTPException(status_code=404, detail="User not found")

    # Check if the current user is an admin or if filters are applied by non-admin users
    if current_user.group.name != "admin" and (status or user_id or order_date):
        raise HTTPException(status_code=403, detail="Access forbidden for non-admin users")

    # Base query to select orders with joined load for items and movies
    query = select(Order).options(joinedload(Order.items).selectinload(OrderItem.movie))

    # Apply filters based on query parameters
    if status:
        try:
            query = query.filter(Order.status == OrderStatusEnum(status))
        except ValueError:
            raise HTTPException(
                status_code=400,
                detail=f"Invalid status: '{status}'. Possible values: pending, paid, canceled."
            )

    if user_id:
        query = query.filter(Order.user_id == user_id)
    else:
        if current_user.group.name != "admin":
            query = query.filter(Order.user_id == current_user.id)

    if order_date:
        try:
            order_date_obj = datetime.strptime(order_date, "%Y-%m-%d")
            query = query.filter(func.date(Order.created_at) == order_date_obj.date())
        except ValueError:
            raise HTTPException(status_code=400, detail="Invalid date format. Use YYYY-MM-DD.")

    # Order by creation date in descending order
    query = query.order_by(Order.created_at.desc())

    total_items_result = await db.execute(select(func.count()).select_from(query.subquery()))
    total_items = total_items_result.scalar() or 0
    offset = (page - 1) * per_page
    query = query.offset(offset).limit(per_page)

    # Execute the query and get the orders
    result = await db.execute(query)
    orders = result.unique().scalars().all()

    # Prepare the response data
    order_responses = [
        OrderWithMoviesResponseSchema(
            id=order.id,
            user_id=order.user_id,
            created_at=order.created_at.isoformat(),
            status=order.status.value,
            total_amount=order.total_amount,
            movies=[item.movie.name for item in order.items],
        )
        for order in orders
    ]

    # Generate information for pagination
    total_pages = (total_items + per_page - 1) // per_page
    prev_page = f"/orders?page={page - 1}&per_page={per_page}" if page > 1 else None
    next_page = f"/orders?page={page + 1}&per_page={per_page}" if page < total_pages else None

    return OrderListResponseSchema(
        orders=order_responses,
        prev_page=prev_page,
        next_page=next_page,
        total_pages=total_pages,
        total_items=total_items,
    )


@router.post("/", response_model=OrderResponseSchema, status_code=status.HTTP_201_CREATED)
async def create_order(
        db: AsyncSession = Depends(get_db),
        current_user_id: int = Depends(get_current_user_id),
) -> OrderResponseSchema:
    try:
        # Check for any pending orders
        existing_orders = await db.execute(
            select(Order).filter(Order.user_id == current_user_id, Order.status == OrderStatusEnum.PENDING)
        )
        if existing_orders.scalars().all():
            raise HTTPException(status_code=400, detail="You have unpaid order")

        # Get existing cart (do NOT create)
        user_cart = await fetch_existing_cart(current_user_id, db)
        if not user_cart or not user_cart.cart_items:
            raise HTTPException(status_code=400, detail="Your cart is empty")

        movie_ids = [item.movie_id for item in user_cart.cart_items]
        user_movies = await db.execute(select(Movie).where(Movie.id.in_(movie_ids)))
        movies_in_cart = user_movies.scalars().all()

        if not movies_in_cart:
            raise HTTPException(status_code=400, detail="Movies in your cart are no longer available")

        total_amount = sum(movie.price for movie in movies_in_cart)

        order = Order(user_id=current_user_id, status=OrderStatusEnum.PENDING, total_amount=total_amount)
        db.add(order)
        await db.flush()

        for movie in movies_in_cart:
            order_item = OrderItem(order_id=order.id, movie_id=movie.id, price_at_order=movie.price)
            db.add(order_item)

        for item in user_cart.cart_items:
            await db.delete(item)
        await db.delete(user_cart)

        await db.commit()

        order_res = await db.execute(
            select(Order)
            .options(joinedload(Order.items).joinedload(OrderItem.movie))
            .filter(Order.id == order.id)
        )
        return order_res.scalars().first()

    except SQLAlchemyError as e:
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Internal server error: {str(e)}"
        )


@router.get("/{order_id}", response_model=OrderWithMoviesResponseSchema)
async def get_order(
        order_id: int,
        db: AsyncSession = Depends(get_db),
        current_user_id: int = Depends(get_current_user_id),
):
    """
    Get the details of a specific order.
    Returns a 404 if the order is not found or is canceled.
    """
    # Get the order
    result = await db.execute(
        select(Order)
        .options(joinedload(Order.items).joinedload(OrderItem.movie))
        .filter(Order.id == order_id)
    )
    order = result.unique().scalar_one_or_none()

    if order is None:
        raise HTTPException(status_code=404, detail="Order not found")

    # Access rights check
    await check_user_access(db, current_user_id, order.user_id)

    if order.status == OrderStatusEnum.CANCELED:
        raise HTTPException(status_code=400, detail="Order is canceled and cannot be accessed")

    return OrderWithMoviesResponseSchema(
        id=order.id,
        user_id=order.user_id,
        created_at=order.created_at.isoformat(),
        status=order.status,
        total_amount=order.total_amount,
        movies=[item.movie.name for item in order.items],
    )


@router.put("/{order_id}", response_model=OrderResponseSchema)
async def update_order_status(
        order_id: int,
        status: str,
        db: AsyncSession = Depends(get_db),
        current_user_id: int = Depends(get_current_user_id),
):
    """
    Update the status of an order.
    Valid statuses are "pending", "paid", and "canceled".
    """
    # Check for valid status
    try:
        requested_status_enum = OrderStatusEnum(status)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid status")

    # Get the order
    result = await db.execute(select(Order).filter(Order.id == order_id))
    order = result.scalar_one_or_none()

    if order is None:
        raise HTTPException(status_code=404, detail="Order not found")

    # Access rights check
    await check_user_access(db, current_user_id, order.user_id)

    if order.status in [OrderStatusEnum.PAID, OrderStatusEnum.CANCELED]:
        raise HTTPException(status_code=400, detail="Cannot update a paid or canceled order")

    # Update the order status
    order.status = requested_status_enum

    # Commit changes
    await db.commit()
    await db.refresh(order)

    # Get the order items
    order_for_response_result = await db.execute(
        select(Order)
        .options(joinedload(Order.items).joinedload(OrderItem.movie))
        .filter(Order.id == order_id)
    )
    order_for_response = order_for_response_result.unique().scalar_one_or_none()

    if order_for_response is None:
        raise HTTPException(status_code=500, detail="Order not found after update and refresh.")

    order_items_response = [
        OrderItemResponseSchema(movie_id=item.movie.id, price_at_order=item.price_at_order)
        for item in order_for_response.items
    ]

    return OrderResponseSchema(
        id=order_for_response.id,
        user_id=order_for_response.user_id,
        created_at=order_for_response.created_at,
        status=order_for_response.status.value,
        total_amount=order_for_response.total_amount,
        items=order_items_response,
    )


@router.delete("/{order_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_order(
        order_id: int,
        db: AsyncSession = Depends(get_db),
        current_user_id: int = Depends(get_current_user_id)
):
    """
    Delete an order if its status is "pending".
    """
    # Get the order
    result = await db.execute(
        select(Order).options(joinedload(Order.items)).filter(Order.id == order_id))
    order = result.scalars().first()

    if not order:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Order not found")

    # Access rights check
    await check_user_access(db, current_user_id, order.user_id)

    # Status check
    if order.status != OrderStatusEnum.PENDING:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Cannot delete a paid or canceled order")

    # Delete all related OrderItems if needed
    # await db.execute(select(OrderItem).filter(OrderItem.order_id == order_id))
    # order_items = result.scalars().all()
    for item in order.items:
        await db.delete(item)

    # Delete the order itself
    await db.delete(order)
    await db.commit()

    return {"detail": "Order deleted successfully"}


@router.put("/{order_id}/cancel", response_model=OrderResponseSchema)
async def cancel_order(
        order_id: int,
        db: AsyncSession = Depends(get_db),
        current_user_id: int = Depends(get_current_user_id),
):
    """
    Cancel an order if it is still "pending".
    """
    # Get the order
    result = await db.execute(select(Order).filter(Order.id == order_id))
    order = result.scalar_one_or_none()

    if order is None:
        raise HTTPException(status_code=404, detail="Order not found")

    # Access rights check
    await check_user_access(db, current_user_id, order.user_id)

    if order.status != OrderStatusEnum.PENDING:
        raise HTTPException(status_code=400, detail="Only pending orders can be canceled")

    # Update the order status to "canceled"
    order.status = OrderStatusEnum.CANCELED

    # Commit changes
    await db.commit()
    await db.refresh(order)

    # Get the order items
    order_items = await db.execute(select(OrderItem).filter(OrderItem.order_id == order_id))
    items = order_items.scalars().all()

    order_items_response = [
        OrderItemResponseSchema(movie_id=item.movie_id, price_at_order=item.price_at_order) for item in items
    ]

    return OrderResponseSchema(
        id=order.id,
        user_id=order.user_id,
        created_at=order.created_at,
        status=order.status,
        total_amount=order.total_amount,
        items=order_items_response,
    )
