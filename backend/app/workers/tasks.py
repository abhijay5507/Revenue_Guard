import logging

from backend.app.workers.celery_app import celery_app

logger = logging.getLogger(__name__)


@celery_app.task(name="revenueguard.process_payment_event")
def process_payment_event(event_id: str) -> None:
    logger.info("Accepted transaction event ready for downstream processing: %s", event_id)
