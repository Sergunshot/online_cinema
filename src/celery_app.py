import os
from celery import Celery
from celery.schedules import crontab

BROKER_URL = os.getenv("CELERY_BROKER_URL", "redis://redis:6379/0")
RESULT_BACKEND = os.getenv("CELERY_RESULT_BACKEND", "redis://redis:6379/1")

celery_app = Celery(
    "online_cinema",
    broker=BROKER_URL,
    backend=RESULT_BACKEND,
    include=["src.tasks.tokens"]
)

celery_app.autodiscover_tasks(["src"])

celery_app.conf.imports = ("src.tasks.tokens",)

celery_app.conf.timezone = "UTC"
celery_app.conf.enable_utc = True

celery_app.conf.beat_schedule = {
    "cleanup-activation-tokens-every-15-min": {
        "task": "cleanup_expired_activation_tokens",
        "schedule": 15 * 60,  # seconds
    },
}
