import asyncio
import os

import pytest
import pytest_asyncio
from fastapi import FastAPI
from httpx import AsyncClient, ASGITransport
from sqlalchemy import insert
from sqlalchemy.ext.asyncio import AsyncSession

from config.dependencies import (
    get_settings,
    get_accounts_email_notificator,
    get_s3_storage_client, get_current_user_id
)
from database import get_db_contextmanager
from database.models import Certification, Genre, Star, Director
from database.models.accounts import UserGroupEnum, UserGroup, User
from database.models.cart import Cart
from database.populate import CSVDatabaseSeeder
from database.session_sqlite import reset_sqlite_database as reset_database
from main import app as fastapi_app
from security.interfaces import JWTAuthManagerInterface
from security.token_manager import JWTAuthManager
from storages import S3StorageClient
from tests.doubles.fakes.storage import FakeS3Storage
from tests.doubles.stubs.emails import StubEmailSender

from database.models.orders import Order, OrderStatusEnum


def pytest_configure(config):
    config.addinivalue_line(
        "markers", "e2e: End-to-end tests"
    )
    config.addinivalue_line(
        "markers", "order: Specify the order of test execution"
    )
    config.addinivalue_line(
        "markers", "unit: Unit tests"
    )


@pytest.fixture(scope="session")
def event_loop():
    loop = asyncio.new_event_loop()
    yield loop
    loop.run_until_complete(_dispose_async_resources())
    loop.close()


async def _dispose_async_resources():
    try:
        from database.session_sqlite import sqlite_engine
        await sqlite_engine.dispose()
    except Exception:
        pass


@pytest.fixture(scope="session", autouse=True)
def _set_testing_env():
    os.environ["ENVIRONMENT"] = "testing"


@pytest_asyncio.fixture(scope="function", autouse=True)
async def reset_db(request):
    """
    Reset the SQLite database before each test function, except for tests marked with 'e2e'.

    By default, this fixture ensures that the database is cleared and recreated before every
    test function to maintain test isolation. However, if the test is marked with 'e2e',
    the database reset is skipped to allow preserving state between end-to-end tests.
    """
    if "e2e" in request.keywords:
        yield
    else:
        await reset_database()
        yield


@pytest_asyncio.fixture(scope="session")
async def reset_db_once_for_e2e(request):
    """
    Reset the database once for end-to-end tests.

    This fixture is intended to be used for end-to-end tests at the session scope,
    ensuring the database is reset before running E2E tests.
    """
    await reset_database()


@pytest_asyncio.fixture(scope="session")
async def settings():
    """
    Provide application settings.

    This fixture returns the application settings by calling get_settings().
    """
    return get_settings()


@pytest_asyncio.fixture(scope="function")
async def email_sender_stub():
    """
    Provide a stub implementation of the email sender.

    This fixture returns an instance of StubEmailSender for testing purposes.
    """
    return StubEmailSender()


@pytest_asyncio.fixture(scope="function")
async def s3_storage_fake():
    """
    Provide a fake S3 storage client.

    This fixture returns an instance of FakeS3Storage for testing purposes.
    """
    return FakeS3Storage()


@pytest_asyncio.fixture(scope="session")
async def s3_client(settings):
    """
    Provide an S3 storage client.

    This fixture returns an instance of S3StorageClient configured with the application settings.
    """
    return S3StorageClient(
        endpoint_url=settings.S3_STORAGE_ENDPOINT,
        access_key=settings.S3_STORAGE_ACCESS_KEY,
        secret_key=settings.S3_STORAGE_SECRET_KEY,
        bucket_name=settings.S3_BUCKET_NAME
    )


@pytest_asyncio.fixture(scope="function")
async def client(email_sender_stub, s3_storage_fake):
    """
    Provide an asynchronous HTTP client for testing.


    Overrides the dependencies for email sender and S3 storage with test doubles.
    """
    app.dependency_overrides[get_accounts_email_notificator] = (
        lambda: email_sender_stub
    )
    app.dependency_overrides[get_s3_storage_client] = lambda: s3_storage_fake

    async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
    ) as async_client:
        yield async_client

    app.dependency_overrides.clear()


@pytest_asyncio.fixture(scope="session")
async def e2e_client():
    """
    Provide an asynchronous HTTP client for end-to-end tests.

    This client is available at the session scope.
    """
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as async_client:
        yield async_client


@pytest_asyncio.fixture(scope="function")
async def db_session():
    """
    Provide an async database session for database interactions.

    This fixture yields an async session using `get_db_contextmanager`, ensuring that the session
    is properly closed after each test.
    """
    async with get_db_contextmanager() as session:
        yield session


@pytest_asyncio.fixture(scope="function")
async def e2e_db_session():
    """
    Provide an async database session for end-to-end tests.

    This fixture yields an async session using `get_db_contextmanager` at the session scope,
    ensuring that the same session is used throughout the E2E test suite.
    Note: Using a session-scoped DB session in async tests may lead to shared state between tests,
    so use this fixture with caution if tests run concurrently.
    """
    async with get_db_contextmanager() as session:
        yield session


@pytest_asyncio.fixture(scope="function")
async def jwt_manager() -> JWTAuthManagerInterface:
    """
    Asynchronous fixture to create a JWT authentication manager instance.

    This fixture retrieves the application settings via `get_settings()` and uses them to
    instantiate a `JWTAuthManager`. The manager is configured with the secret keys for
    access and refresh tokens, as well as the JWT signing algorithm specified in the settings.

    Returns:
        JWTAuthManagerInterface: An instance of JWTAuthManager configured with the appropriate
        secret keys and algorithm.
    """
    settings = get_settings()
    return JWTAuthManager(
        secret_key_access=settings.SECRET_KEY_ACCESS,
        secret_key_refresh=settings.SECRET_KEY_REFRESH,
        algorithm=settings.JWT_SIGNING_ALGORITHM,
    )


@pytest_asyncio.fixture(scope="function")
async def seed_user_groups(db_session: AsyncSession):
    """
    Asynchronously seed the UserGroupModel table with default user groups.

    This fixture inserts all user groups defined in UserGroupEnum into the database and commits the transaction.
    It then yields the asynchronous database session for further testing.
    """
    groups = [{"name": group.value} for group in UserGroupEnum]
    await db_session.execute(insert(UserGroup).values(groups))
    await db_session.commit()
    yield db_session


@pytest_asyncio.fixture(scope="function")
async def seed_database(db_session):
    """
    Seed the database with test data if it is empty.

    This fixture initializes a `CSVDatabaseSeeder` and ensures the test database is populated before
    running tests that require existing data.

    :param db_session: The async database session fixture.
    :type db_session: AsyncSession
    """
    settings = get_settings()
    seeder = CSVDatabaseSeeder(
        csv_file_path=settings.PATH_TO_MOVIES_CSV, db_session=db_session
    )

    if not await seeder.is_db_populated():
        await seeder.seed()

    yield db_session


@pytest_asyncio.fixture(scope="function")
async def test_user(db_session, seed_user_groups):
    """
    Create a test user for validation tests.

    This fixture creates a test user with the following properties:
    - email: test@mate.com
    - password: TestPassword123!
    - group_id: 1 (User group)
    - is_active: True

    The user is created before each test and cleaned up after.
    """
    user = User.create(email="test@mate.com", raw_password="TestPassword123!", group_id=1)
    user.is_active = True
    db_session.add(user)
    await db_session.commit()
    return user


@pytest_asyncio.fixture(scope="function")
async def auth_client(
        client: AsyncClient,
        test_user: User,
        jwt_manager: JWTAuthManagerInterface,
):
    """
    Provide an authenticated async HTTP client for testing with regular user privileges.
    """
    access_token = jwt_manager.create_access_token({"user_id": test_user.id})
    client.headers["Authorization"] = f"Bearer {access_token}"
    return client


@pytest_asyncio.fixture(scope="function")
async def test_moderator(db_session, seed_user_groups):
    """
    Create a test moderator for validation tests.

    This fixture creates a test moderator with the following properties:
    - email: moderator@mate.com
    - password: TestPassword123!
    - group_id: 2 (Moderator group)
    - is_active: True

    The moderator is created before each test and cleaned up after.
    """
    moderator = User.create(email="moderator@mate.com", raw_password="TestPassword123!", group_id=2)
    moderator.is_active = True
    db_session.add(moderator)
    await db_session.commit()
    return moderator


@pytest_asyncio.fixture(scope="function")
async def auth_moderator_client(
        client: AsyncClient,
        test_moderator: User,
        jwt_manager: JWTAuthManagerInterface,
):
    """
    Provide an authenticated async HTTP client for testing with moderator privileges.
    """
    access_token = jwt_manager.create_access_token({"user_id": test_moderator.id})
    client.headers["Authorization"] = f"Bearer {access_token}"
    return client


@pytest_asyncio.fixture(scope="function")
async def test_movie(db_session: AsyncSession):
    """Create a test movie for shopping cart tests."""
    from database.models.movies import Movie, Certification

    certification = Certification(name="PG-13")
    db_session.add(certification)
    await db_session.flush()

    movie = Movie(
        name="Test Movie",
        description="Test Description",
        price=10.0,
        year=2024,
        time=120,
        certification_id=certification.id,
    )
    db_session.add(movie)
    await db_session.commit()
    await db_session.refresh(movie)
    return movie


@pytest_asyncio.fixture(scope="function")
async def test_cart(db_session: AsyncSession, test_user: User):
    """Create a test cart for the test user."""
    cart = Cart(user_id=test_user.id)
    db_session.add(cart)
    await db_session.commit()
    await db_session.refresh(cart)
    return cart


@pytest_asyncio.fixture(scope="function")
async def seed_movie_relations(db_session: AsyncSession):
    """
    Ensure that at least one genre, star, director, and certification exist in the database with id=1.
    """
    # Certification
    cert = await db_session.get(Certification, 1)
    if not cert:
        cert = Certification(id=1, name="PG-13")
        db_session.add(cert)
    # Genre
    genre = await db_session.get(Genre, 1)
    if not genre:
        genre = Genre(id=1, name="Action")
        db_session.add(genre)
    # Star
    star = await db_session.get(Star, 1)
    if not star:
        star = Star(id=1, name="Leonardo DiCaprio")
        db_session.add(star)
    # Director
    director = await db_session.get(Director, 1)
    if not director:
        director = Director(id=1, name="Christopher Nolan")
        db_session.add(director)
    await db_session.commit()
    yield


@pytest_asyncio.fixture(scope="function")
async def auth_user_token(test_user: User, jwt_manager: JWTAuthManagerInterface):
    return jwt_manager.create_access_token({"user_id": test_user.id})


@pytest_asyncio.fixture(scope="function")
async def auth_admin_token(test_moderator: User, jwt_manager: JWTAuthManagerInterface):
    return jwt_manager.create_access_token({"user_id": test_moderator.id})


@pytest_asyncio.fixture(scope="function")
async def auth_user_client(
        client: AsyncClient,
        auth_user_token: str,
):
    client.headers["Authorization"] = f"Bearer {auth_user_token}"
    return client


@pytest_asyncio.fixture(scope="function")
async def auth_admin_client(
        client: AsyncClient,
        auth_admin_token: str,
):
    client.headers["Authorization"] = f"Bearer {auth_admin_token}"
    return client


@pytest_asyncio.fixture(scope="function")
async def test_order(db_session: AsyncSession, test_user: User, test_movie):
    """Create a test order for payment tests."""
    order = Order(
        user_id=test_user.id,
        status=OrderStatusEnum.PENDING,
        total_amount=test_movie.price
    )
    db_session.add(order)
    await db_session.commit()
    await db_session.refresh(order)
    return order


@pytest_asyncio.fixture(scope="function")
async def test_order_negative_amount(db_session: AsyncSession, test_user: User, test_movie):
    """Create a test order with a negative amount for negative test cases."""
    order = Order(
        user_id=test_user.id,
        status=OrderStatusEnum.PENDING,
        total_amount=-10.0
    )
    db_session.add(order)
    await db_session.commit()
    await db_session.refresh(order)
    return order


class DummyEmailSender:
    def __init__(self):
        self.calls = []

    async def send_payment_email(self, email, amount):
        self.calls.append(("payment", email, amount))

    async def send_cancellation_email(self, email, amount):
        self.calls.append(("cancel", email, amount))

    async def send_refund_email(self, email, amount):
        self.calls.append(("refund", email, amount))


@pytest.fixture(scope="session")
def app() -> FastAPI:
    return fastapi_app


@pytest_asyncio.fixture(scope="function")
async def client(app: FastAPI, email_sender_stub, s3_storage_fake):
    app.dependency_overrides[get_accounts_email_notificator] = lambda: email_sender_stub
    app.dependency_overrides[get_s3_storage_client] = lambda: s3_storage_fake

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        yield ac

    app.dependency_overrides.clear()


@pytest.fixture
def dummy_email_sender(app: FastAPI):
    """Swap out the mailer for a no-op (dummy) implementation so no emails are sent."""
    sender = DummyEmailSender()
    app.dependency_overrides[get_accounts_email_notificator] = lambda: sender
    try:
        yield sender
    finally:
        app.dependency_overrides.pop(get_accounts_email_notificator, None)


@pytest.fixture
def override_current_user(app: FastAPI):
    """
    Convenient helper: override_current_user(user_id) -> cleanup()
    Example:
        cleanup = override_current_user(1)
        try:
            ... test ...
        finally:
            cleanup()
    (Our tests don’t have to use this — you can override directly in the test.)
    """

    def _set(user_id: int):
        async def _dep():
            return user_id

        app.dependency_overrides[get_current_user_id] = _dep

        def _cleanup():
            app.dependency_overrides.pop(get_current_user_id, None)

        return _cleanup

    return _set
