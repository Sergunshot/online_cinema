import os
from contextlib import asynccontextmanager
from typing import AsyncGenerator
from sqlalchemy.ext.asyncio import AsyncSession

from .session_postgresql import get_postgresql_db
from .session_sqlite import get_sqlite_db
from .validators import accounts as account_validators

environment = os.getenv("ENVIRONMENT", "developing")


def _is_testing() -> bool:
    env = os.getenv("ENVIRONMENT", "").lower()
    return env in {"testing", "test"} or os.getenv("PYTEST_CURRENT_TEST") is not None


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    if _is_testing():
        from .session_sqlite import AsyncSQLiteSessionLocal
        async with AsyncSQLiteSessionLocal() as session:
            yield session
    else:
        from .session_postgresql import AsyncPostgresqlSessionLocal
        async with AsyncPostgresqlSessionLocal() as session:
            yield session


@asynccontextmanager
async def get_db_contextmanager() -> AsyncGenerator[AsyncSession, None]:
    if _is_testing():
        from .session_sqlite import AsyncSQLiteSessionLocal
        session = AsyncSQLiteSessionLocal()
    else:
        from .session_postgresql import AsyncPostgresqlSessionLocal
        session = AsyncPostgresqlSessionLocal()
    try:
        yield session
    finally:
        await session.close()

from .models import (  # noqa: E402,F401
    Base,
    UserGroupEnum, GenderEnum, UserGroup, User, UserProfile,
    ActivationToken, PasswordResetToken, RefreshToken,
    Cart, CartItem,
    MovieGenres, MovieDirectors, MovieStars, Genre, Star, Director, Certification, Movie,
    Like, Dislike, Comment, AnswerComment, Favorite, Rating,
    OrderItem, Order, OrderStatusEnum,
    Payment, PaymentItem, PaymentStatusEnum,
)
