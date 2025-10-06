from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select
from sqlalchemy.orm import joinedload, selectinload
from sqlalchemy import delete
from config.dependencies import get_current_user_id

from database.models.orders import OrderItem, Order
from database import get_db
from database.models.cart import Cart, CartItem
from database.models.movies import Movie
from schemas.cart import CartItemResponseSchema, CartItemBaseSchema

router = APIRouter()


async def fetch_existing_cart(user_id: int, db: AsyncSession) -> Optional[Cart]:
    result = await db.execute(
        select(Cart).options(joinedload(Cart.cart_items))
        .filter(Cart.user_id == user_id)
    )
    return result.scalars().first()


async def get_cart_by_user(user_id: int, db: AsyncSession) -> Cart:
    """Retrieve the user's cart or create a new one if it does not exist."""
    result = await db.execute(
        select(Cart).options(joinedload(Cart.cart_items).selectinload(CartItem.movie).joinedload(Movie.genres))
        .filter(Cart.user_id == user_id)
    )
    cart = result.scalars().first()

    if not cart:
        cart = Cart(user_id=user_id)
        db.add(cart)
        # We need to flush here to get the 'id' for the new cart object
        # but NOT commit, so the transaction is managed by the caller.
        await db.flush()
        await db.refresh(cart)

    return cart


@router.post("/items", response_model=CartItemResponseSchema)
async def add_movie(
    payload: CartItemBaseSchema,
    db: AsyncSession = Depends(get_db),
    user_id: int = Depends(get_current_user_id),
) -> CartItemResponseSchema:
    movie_id = payload.movie_id
    cart = await get_cart_by_user(user_id, db)

    movie = (await db.execute(select(Movie).options(joinedload(Movie.genres))
                              .filter_by(id=movie_id))).scalars().first()
    if not movie:
        raise HTTPException(status_code=404, detail="Movie not found")

    exists = (await db.execute(select(CartItem).filter_by(cart_id=cart.id, movie_id=movie_id))).scalars().first()
    if exists:
        raise HTTPException(status_code=400, detail="Movie is already in cart")

    purchased = (await db.execute(
        select(OrderItem).join(Order)
        .filter(Order.user_id == user_id, OrderItem.movie_id == movie_id, Order.status == "paid")
    )).scalars().first()
    if purchased:
        raise HTTPException(status_code=400, detail="This movie has already been purchased")

    cart_item = CartItem(cart_id=cart.id, movie_id=movie_id)
    db.add(cart_item)
    await db.commit()
    await db.refresh(cart_item)

    return CartItemResponseSchema(id=cart_item.id, cart_id=cart_item.cart_id, movie=movie, added_at=cart_item.added_at)


@router.delete("/items/{movie_id}")
async def remove_movie(movie_id: int, db: AsyncSession = Depends(get_db), user_id: int = Depends(get_current_user_id)):
    cart = await get_cart_by_user(user_id, db)
    ci = (await db.execute(select(CartItem).filter_by(cart_id=cart.id, movie_id=movie_id))).scalars().first()
    if not ci:
        raise HTTPException(status_code=404, detail="Movie not found in cart")
    await db.execute(delete(CartItem).where(CartItem.id == ci.id))
    await db.commit()
    return {"message": "Movie removed from cart"}


@router.delete("/")
async def empty_cart(db: AsyncSession = Depends(get_db), user_id: int = Depends(get_current_user_id)):
    cart = await get_cart_by_user(user_id, db)
    await db.execute(delete(CartItem).where(CartItem.cart_id == cart.id))
    await db.commit()
    return {"message": "Cart cleared successfully"}


@router.get("/", response_model=dict)
async def view_cart(db: AsyncSession = Depends(get_db), user_id: int = Depends(get_current_user_id)):
    stmt = (
        select(Cart)
        .options(
            selectinload(Cart.cart_items)
            .selectinload(CartItem.movie)
            .selectinload(Movie.genres)
        )
        .where(Cart.user_id == user_id)
    )
    cart = (await db.execute(stmt)).scalars().first()
    if not cart:
        cart = await get_cart_by_user(user_id, db)

    items = [
        CartItemResponseSchema.model_validate(ci)
        for ci in (cart.cart_items or [])
    ]
    return {"id": cart.id, "user_id": cart.user_id, "items": items}
