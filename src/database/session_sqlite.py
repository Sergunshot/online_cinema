from contextlib import asynccontextmanager
from typing import AsyncGenerator

from sqlalchemy import NullPool
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker

from config.settings import Settings
from .models.base import Base

settings = Settings()

SQLITE_DATABASE_URL = f"sqlite+aiosqlite:///{settings.PATH_TO_DB}"
sqlite_engine = create_async_engine(SQLITE_DATABASE_URL, echo=False, poolclass=NullPool)
AsyncSQLiteSessionLocal = sessionmaker(  # type: ignore
    bind=sqlite_engine,
    class_=AsyncSession,
    expire_on_commit=False,
)


async def get_sqlite_db() -> AsyncGenerator[AsyncSession, None]:
    """
    Provide an asynchronous database session.

    This function returns an async generator yielding a new database session.
    It ensures that the session is properly closed after use.

    :return: An asynchronous generator yielding an AsyncSession instance.
    """
    async with AsyncSQLiteSessionLocal() as session:
        yield session


@asynccontextmanager
async def get_sqlite_db_contextmanager() -> AsyncGenerator[AsyncSession, None]:
    """
    Provide an asynchronous database session using a context manager.

    This function allows for managing the database session within a `with` statement.
    It ensures that the session is properly initialized and closed after execution.

    :return: An asynchronous generator yielding an AsyncSession instance.
    """
    async with AsyncSQLiteSessionLocal() as session:
        yield session


async def reset_sqlite_database() -> None:
    """
    Reset the SQLite database.

    This function drops all existing tables and recreates them.
    It is useful for testing purposes or when resetting the database is required.

    Warning: This action is irreversible and will delete all stored data.

    :return: None
    """
    async with sqlite_engine.begin() as conn:
        await conn.run_sync(lambda c: c.exec_driver_sql("PRAGMA foreign_keys=OFF"))
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)
        await conn.run_sync(lambda c: c.exec_driver_sql("PRAGMA foreign_keys=ON"))
