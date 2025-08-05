from fastapi import APIRouter, Depends, HTTPException, status, BackgroundTasks
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select
from sqlalchemy.orm import joinedload

from config import get_current_user_id, get_accounts_email_notificator
from database import get_db, User, UserGroupEnum, UserGroup
from database.models import Movie
from database.models.cart import Cart, CartItem, Purchased
from notifications import EmailSenderInterface

from schemas.cart import CartResponseSchema, CartItemResponseSchema

router = APIRouter()


@router.post(
    "/",
    summary="Add movie to the cart.",
    description="Add movie (create cart item) to the cart",
)
async def create_cart(
    movie_id: int,
    user_id: int = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalars().first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found. Please sign up.")

    result = await db.execute(
        select(Purchased).where(Purchased.user_id == user_id, Purchased.movie_id == movie_id)
    )
    purchase = result.scalars().first()
    if purchase:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="You have already bought this movie",
        )

    result = await db.execute(select(Movie).where(Movie.id == movie_id))
    movie = result.scalars().first()
    if not movie:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="Movie not found"
        )

    result = await db.execute(select(Cart).where(Cart.user_id == user_id))
    cart = result.scalars().first()
    if not cart:
        cart = Cart(user_id=user_id)
        db.add(cart)
        await db.flush()
        await db.refresh(cart)

    result = await db.execute(
        select(CartItem).where(CartItem.cart_id == cart.id, CartItem.movie_id == movie_id)
    )
    existing_item = result.scalars().first()
    if existing_item:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Movie is already in the cart.",
        )

    try:
        cart_item = CartItem(cart_id=cart.id, movie_id=movie_id)
        db.add(cart_item)
        await db.commit()
        return {"message": f"{movie.name} added in cart successfully"}
    except SQLAlchemyError:
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid input data",
        )


@router.get(
    "/{cart_id}/",
    summary="Get movie from the cart",
    description="Get cart item (movie) from the cart).",
    response_model=CartResponseSchema,
)
async def get_cart(
    cart_id: int, db: AsyncSession = Depends(get_db), user_id: User = Depends(get_current_user_id)
):
    result = await db.execute(
        select(User).where(User.id == user_id).options(joinedload(User.group))
    )
    user = result.scalars().first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found. Please sign up.")

    if user.group.name != UserGroupEnum.ADMIN and user.id != user_id:
        raise HTTPException(status_code=403, detail="Not authorized to view this cart.")

    result = await db.execute(
        select(Cart)
        .where(Cart.id == cart_id, Cart.user_id == user_id)
        .options(
            joinedload(Cart.cart_items)
            .joinedload(CartItem.movie)
            .joinedload(Movie.genres)
        )
    )
    cart = result.scalars().first()

    if not cart:
        raise HTTPException(status_code=404, detail="Cart not found.")

    items = [
        CartItemResponseSchema(
            id=item.movie.id,
            title=item.movie.name,
            price=item.movie.price,
            genre=[genre.name for genre in item.movie.genres],
            release_year=item.movie.year,
        )
        for item in cart.cart_items
        if item.movie
    ]

    return CartResponseSchema(id=cart.id, items=items)


@router.delete(
    "/{cart_id}/clear/",
    description="Clear a cart from all cart items (movies).",
)
async def clear_cart(
    cart_id: int, db: AsyncSession = Depends(get_db), user_id: User = Depends(get_current_user_id)
):
    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalars().first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found. Please sign up.")

    result = await db.execute(
        select(Cart)
        .where(Cart.id == cart_id, Cart.user_id == user_id)
        .options(joinedload(Cart.cart_items))
    )
    cart = result.scalars().first()
    if not cart:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="Cart not found"
        )

    if not cart.cart_items:
        raise HTTPException(status_code=400, detail="Cart is already empty.")

    try:
        for item in cart.cart_items:
            await db.delete(item)
        await db.commit()
    except Exception:
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to clear cart",
        )

    return {"detail": "Cart cleared successfully."}


@router.delete(
    "/{cart_id}/{movie_id}/",
    description="Remove a cart item (movie) from cart.",
)
async def remove_movie_from_cart(
    movie_id: int,
    cart_id: int,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
    user_id: int = Depends(get_current_user_id),
    email_sender: EmailSenderInterface = Depends(get_accounts_email_notificator),
):
    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalars().first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found. Please sign up.")

    result = await db.execute(select(Movie).where(Movie.id == movie_id))
    movie = result.scalars().first()
    if not movie:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="Movie not found"
        )

    result = await db.execute(select(Cart).where(Cart.id == cart_id, Cart.user_id == user_id))
    cart = result.scalars().first()
    if not cart:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="Cart not found"
        )

    result = await db.execute(
        select(CartItem).where(CartItem.cart_id == cart.id, CartItem.movie_id == movie_id)
    )
    cart_item = result.scalars().first()
    if not cart_item:
        raise HTTPException(status_code=404, detail="Movie not found in cart")

    try:
        await db.delete(cart_item)
        await db.commit()

        result = await db.execute(
            select(User)
            .join(UserGroup)
            .where(UserGroup.name == UserGroupEnum.MODERATOR)
        )
        moderators = result.scalars().all()

        for moderator in moderators:
            background_tasks.add_task(
                email_sender.send_remove_movie, moderator.email, movie.name, cart_id
            )
    except SQLAlchemyError:
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Please try again later.",
        )

    return {"message": f"{movie.name} removed from cart id {cart.id} successfully"}
