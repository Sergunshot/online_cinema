from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select
from sqlalchemy.orm import joinedload, selectinload
from sqlalchemy import delete
from datetime import datetime, timezone
from config.dependencies import get_current_user_id

from database.models.orders import OrderItem, Order
from database import get_db
from database.models.cart import Cart, CartItem
from database.models.movies import Movie
from schemas.cart import CartResponseSchema, CartItemResponseSchema
from fastapi import status

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
        await db.refresh(cart) # Refresh to load default values if needed, after flushing

    return cart


@router.get("/", response_model=CartResponseSchema)
async def view_cart(
    db: AsyncSession = Depends(get_db),
    user_id: int = Depends(get_current_user_id),
) -> CartResponseSchema:
    stmt = (
        select(Cart)
        .options(
            selectinload(Cart.cart_items)
            .selectinload(CartItem.movie)
            .selectinload(Movie.genres)
        )
        .where(Cart.user_id == user_id)
    )
    result = await db.execute(stmt)
    cart = result.scalars().first()

    if not cart:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Cart not found"
        )

    if not cart.cart_items:
        raise HTTPException(
            status_code=status.HTTP_200_OK,
            detail="Cart is empty"
        )

    return cart


@router.post("/{movie_id}/add", response_model=CartItemResponseSchema)
async def add_movie(movie_id: int, db: AsyncSession = Depends(get_db), user_id: int = Depends(get_current_user_id)) -> CartItemResponseSchema:
    """Add a movie to the user's cart, ensuring it's not already purchased."""
    try:
        cart = await get_cart_by_user(user_id, db)

        movie_result = await db.execute(select(Movie).options(joinedload(Movie.genres)).filter_by(id=movie_id))
        movie = movie_result.scalars().first()
        if not movie:
            raise HTTPException(status_code=404, detail="Movie not found")

        existing_item = await db.execute(select(CartItem).filter_by(cart_id=cart.id, movie_id=movie_id))
        if existing_item.scalars().first():
            raise HTTPException(status_code=400, detail="Movie is already in the cart")

        purchased_movie = await db.execute(
            select(OrderItem)
            .join(Order)
            .filter(Order.user_id == user_id)
            .filter(OrderItem.movie_id == movie_id)
            .filter(Order.status == "paid")
        )
        if purchased_movie.scalars().first():
            raise HTTPException(status_code=400, detail="You have already purchased this movie")

        # async with db.begin():
        cart_item = CartItem(
            cart_id=cart.id, movie_id=movie_id, added_at=datetime.now(timezone.utc).replace(tzinfo=None)
        )
        db.add(cart_item)
        # await db.flush()
        await db.commit()
        await db.refresh(cart_item)

        return CartItemResponseSchema(id=cart_item.id, cart_id=cart_item.cart_id, movie=movie, added_at=cart_item.added_at)

    except HTTPException as http_error:
        raise http_error
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Unexpected error: {str(e)}")


@router.delete("/{movie_id}/remove")
async def remove_movie(movie_id: int, db: AsyncSession = Depends(get_db), user_id: int = Depends(get_current_user_id)):
    """Remove a movie from the user's cart and log the event."""

    try:
        cart = await get_cart_by_user(user_id, db)

        cart_item = await db.execute(select(CartItem).filter_by(cart_id=cart.id, movie_id=movie_id))
        cart_item = cart_item.scalars().first()

        if not cart_item:
            raise HTTPException(status_code=404, detail="Movie is not in the cart")

        await db.execute(delete(CartItem).where(CartItem.id == cart_item.id))
        await db.commit()

        print(f"Moderator Alert: User {user_id} removed movie {movie_id} from their cart.")

        return {"message": "Movie removed from cart"}

    except HTTPException as http_error:
        raise http_error
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Unexpected error: {str(e)}")


@router.delete("/clear")
async def empty_cart(db: AsyncSession = Depends(get_db), user_id: int = Depends(get_current_user_id)):
    """Clear all items from the user's cart."""
    try:
        # Start a single transaction for all operations (read and write).
        async with db.begin():
            # Get the cart within the active transaction.
            cart = await get_cart_by_user(user_id, db)

            # Check if the cart is empty.
            if not cart or not cart.cart_items:
                raise HTTPException(status_code=400, detail="Cart is already empty")

            # Delete all items from the cart within this transaction.
            await db.execute(delete(CartItem).where(CartItem.cart_id == cart.id))

        # Transaction automatically committed on success.
        return {"message": "Cart cleared"}

    except HTTPException as http_error:
        # HTTPExceptions automatically roll back the transaction.
        raise http_error
    except Exception as e:
        # Rollback for any other unexpected errors.
        await db.rollback()
        raise HTTPException(status_code=500, detail=f"Unexpected error: {str(e)}")


@router.get("/admin/{user_id}", response_model=CartResponseSchema)
async def view_user_cart(user_id: int, db: AsyncSession = Depends(get_db)) -> CartResponseSchema:
    """Admin route to view a user's cart."""
    try:
        cart = await get_cart_by_user(user_id, db)
        if not cart.cart_items:
            raise HTTPException(status_code=404, detail="Cart is empty")

        return CartResponseSchema.model_validate(cart)

    except HTTPException as http_error:
        raise http_error
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Unexpected error: {str(e)}")
