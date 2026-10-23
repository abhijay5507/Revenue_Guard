from celery import Celery

from backend.app.core.config import settings

celery_app = Celery("revenueguard", broker=settings.redis_url)
celery_app.conf.update(
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    broker_connection_retry_on_startup=False,
)
celery_app.autodiscover_tasks(["backend.app.workers"])
