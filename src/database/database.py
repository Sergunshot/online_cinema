from contextlib import asynccontextmanager
from typing import AsyncGenerator

from sqlalchemy.ext.asyncio import (
    create_async_engine,
    async_sessionmaker,
    AsyncSession
)
from sqlalchemy.orm import declarative_base

from config.dependencies import get_settings

from database.models.base import Base

settings = get_settings()
# DATABASE_URL = settings.PATH_TO_DB
DATABASE_URL = f"sqlite+aiosqlite:///{settings.PATH_TO_DB}"
engine = create_async_engine(DATABASE_URL, echo=True)
async_session = async_sessionmaker(bind=engine, expire_on_commit=False)
Base = declarative_base()


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    async with async_session() as session:
        yield session


@asynccontextmanager
async def get_db_contextmanager() -> AsyncSession:
    async with async_session() as session:
        yield session


async def reset_database():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)
