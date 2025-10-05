import os
import logging
from celery import shared_task
from sqlalchemy import create_engine, delete, func
from sqlalchemy.orm import sessionmaker

from database.models.accounts import ActivationToken

logger = logging.getLogger(__name__)

SYNC_DATABASE_URL = os.getenv("SYNC_DATABASE_URL")
if not SYNC_DATABASE_URL:
    async_url = os.getenv("DATABASE_URL", "")
    if async_url.startswith("postgresql+asyncpg://"):
        SYNC_DATABASE_URL = async_url.replace("postgresql+asyncpg://", "postgresql+psycopg2://", 1)
    else:
        SYNC_DATABASE_URL = async_url

engine = create_engine(
    SYNC_DATABASE_URL,
    future=True,
    pool_pre_ping=True,
)
SessionLocal = sessionmaker(bind=engine, expire_on_commit=False, future=True)

@shared_task(
    name="cleanup_expired_activation_tokens",
    bind=True,
    autoretry_for=(Exception,),
    retry_backoff=True,
    retry_kwargs={"max_retries": 3},
)
def cleanup_expired_activation_tokens(self):
    with SessionLocal() as session:
        stmt = (
            delete(ActivationToken)
            .where(ActivationToken.expires_at < func.now())
            .returning(ActivationToken.id)
        )
        result = session.execute(stmt)
        deleted = len(result.fetchall())
        session.commit()

    logger.info("Deleted %s expired activation tokens", deleted)
    return deleted
