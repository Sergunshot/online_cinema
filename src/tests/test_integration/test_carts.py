import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from database.models import User, Movie, Cart, CartItem
from database.models.orders import OrderStatusEnum, Order, OrderItem


@pytest.mark.asyncio
async def test_add_movie_to_cart(
        auth_client: AsyncClient,
        db_session: AsyncSession,
        test_user: User,
        test_movie: Movie,
):
    """Test adding a movie to cart."""
    response = await auth_client.post(
        "/api/v1/cart/items",
        json={"movie_id": test_movie.id},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["movie"]["id"] == test_movie.id

    cart = await db_session.get(Cart, data["cart_id"])
    assert cart is not None, "Cart should exist"

    stmt = select(CartItem).where(CartItem.cart_id == cart.id)
    result = await db_session.execute(stmt)
    items = result.scalars().all()
    assert len(items) == 1
    assert items[0].movie_id == test_movie.id


@pytest.mark.asyncio
async def test_add_duplicate_movie_to_cart(
        auth_client: AsyncClient,
        db_session: AsyncSession,
        test_user: User,
        test_movie: Movie,
):
    """Test adding the same movie twice to cart."""
    await auth_client.post(
        "/api/v1/cart/items",
        json={"movie_id": test_movie.id},
    )

    response = await auth_client.post(
        "/api/v1/cart/items",
        json={"movie_id": test_movie.id},
    )
    assert response.status_code == 400
    assert "already in cart" in response.json()["detail"].lower()


@pytest.mark.asyncio
async def test_add_purchased_movie_to_cart(
        auth_client: AsyncClient,
        db_session: AsyncSession,
        test_user: User,
        test_movie: Movie,
):
    """Test adding a movie that was already purchased."""
    order = Order(user_id=test_user.id, status=OrderStatusEnum.PAID)
    db_session.add(order)
    await db_session.flush()

    order_item = OrderItem(
        order_id=order.id,
        movie_id=test_movie.id,
        price_at_order=test_movie.price,
    )
    db_session.add(order_item)
    await db_session.commit()

    response = await auth_client.post(
        "/api/v1/cart/items",
        json={"movie_id": test_movie.id},
    )
    assert response.status_code == 400
    assert "already been purchased" in response.json()["detail"].lower()


@pytest.mark.asyncio
async def test_remove_movie_from_cart(
        auth_client: AsyncClient,
        db_session: AsyncSession,
        test_user: User,
        test_movie: Movie,
):
    """Test removing a movie from cart."""
    await auth_client.post(
        "/api/v1/cart/items",
        json={"movie_id": test_movie.id},
    )

    response = await auth_client.delete(f"/api/v1/cart/items/{test_movie.id}")
    assert response.status_code == 200
    assert response.json()["message"] == "Movie removed from cart"

    stmt = select(Cart).where(Cart.user_id == test_user.id)
    result = await db_session.execute(stmt)
    cart = result.scalar_one()
    stmt = select(CartItem).where(CartItem.cart_id == cart.id)
    result = await db_session.execute(stmt)
    items = result.scalars().all()
    assert len(items) == 0


@pytest.mark.asyncio
async def test_clear_cart(
        auth_client: AsyncClient,
        db_session: AsyncSession,
        test_user: User,
):
    """Test clearing the entire cart."""
    movie1 = Movie(
        name="Test Movie 1",
        description="Test Description 1",
        price=10.0,
        year=2024,
        time=120,
        certification_id=1,
    )
    movie2 = Movie(
        name="Test Movie 2",
        description="Test Description 2",
        price=15.0,
        year=2024,
        time=120,
        certification_id=1,
    )
    db_session.add_all([movie1, movie2])
    await db_session.commit()

    await auth_client.post("/api/v1/cart/items", json={"movie_id": movie1.id})
    await auth_client.post("/api/v1/cart/items", json={"movie_id": movie2.id})

    response = await auth_client.delete("/api/v1/cart/")
    assert response.status_code == 200
    assert response.json()["message"] == "Cart cleared successfully"

    stmt = select(Cart).where(Cart.user_id == test_user.id)
    result = await db_session.execute(stmt)
    cart = result.scalar_one()
    stmt = select(CartItem).where(CartItem.cart_id == cart.id)
    result = await db_session.execute(stmt)
    items = result.scalars().all()
    assert len(items) == 0


@pytest.mark.asyncio
async def test_get_cart_contents(
        auth_client: AsyncClient,
        db_session: AsyncSession,
        test_user: User,
):
    """Test getting cart contents."""
    movie1 = Movie(
        name="Test Movie 1",
        description="Test Description 1",
        price=10.0,
        year=2024,
        time=120,
        certification_id=1,
    )
    movie2 = Movie(
        name="Test Movie 2",
        description="Test Description 2",
        price=15.0,
        year=2024,
        time=120,
        certification_id=1,
    )
    db_session.add_all([movie1, movie2])
    await db_session.commit()

    await auth_client.post("/api/v1/cart/items", json={"movie_id": movie1.id})
    await auth_client.post("/api/v1/cart/items", json={"movie_id": movie2.id})

    response = await auth_client.get("/api/v1/cart/")
    assert response.status_code == 200
    data = response.json()

    assert len(data["items"]) == 2
    movie_ids = {item["movie"]["id"] for item in data["items"]}
    assert movie_ids == {movie1.id, movie2.id}


@pytest.mark.asyncio
async def test_cart_total_price(
        auth_client: AsyncClient,
        db_session: AsyncSession,
        test_user: User,
):
    """Test cart total price calculation."""
    movie1 = Movie(
        name="Test Movie 1",
        description="Test Description 1",
        price=10.0,
        year=2024,
        time=120,
        certification_id=1,
    )
    movie2 = Movie(
        name="Test Movie 2",
        description="Test Description 2",
        price=15.0,
        year=2024,
        time=120,
        certification_id=1,
    )
    db_session.add_all([movie1, movie2])
    await db_session.commit()

    await auth_client.post("/api/v1/cart/items", json={"movie_id": movie1.id})
    await auth_client.post("/api/v1/cart/items", json={"movie_id": movie2.id})

    response = await auth_client.get("/api/v1/cart/")
    assert response.status_code == 200
    data = response.json()

    total_price = sum(item["movie"]["price"] for item in data["items"])
    assert total_price == 25.0  # 10.0 + 15.0


@pytest.mark.asyncio
async def test_remove_nonexistent_movie_from_cart(
        auth_client: AsyncClient,
        db_session: AsyncSession,
        test_user: User,
):
    """Test removing a movie that's not in the cart."""
    response = await auth_client.delete("/api/v1/cart/items/999")
    assert response.status_code == 404
    assert "not found" in response.json()["detail"].lower()


@pytest.mark.asyncio
async def test_add_nonexistent_movie_to_cart(
        auth_client: AsyncClient,
        db_session: AsyncSession,
        test_user: User,
):
    """Test adding a movie that doesn't exist."""
    response = await auth_client.post(
        "/api/v1/cart/items",
        json={"movie_id": 999},
    )
    assert response.status_code == 404
    assert "not found" in response.json()["detail"].lower()
