from celery import Celery
from celery.schedules import crontab
from .dependencies import get_settings


settings = get_settings()

celery_app = Celery(
    "tasks",
    broker=settings.CELERY_BROKER_URL,
    backend=settings.CELERY_RESULT_BACKEND,
    include=["src.tasks"],
)

celery_app.conf.update(
    result_expires=3600,
    timezone="UTC",
    broker_connection_retry_on_startup=True,
)

celery_app.conf.beat_schedule = {
    "delete_expired_tokens_every_hour": {
        "task": "src.tasks.delete_expired_activation_tokens",
        "schedule": crontab(hour="*/1"),
    },
}


if __name__ == "__main__":
    celery_app.start()
