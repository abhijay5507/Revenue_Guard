import json
import logging

from fastapi import APIRouter, Depends, Header, HTTPException, Request, status
from pydantic import ValidationError
from sqlalchemy.orm import Session

from backend.app.db.session import get_db
from backend.app.schemas.payment_event import PaymentEvent
from backend.app.security.webhooks import verify_signature
from backend.app.services.transactions import TransactionStoreError, persist_event
from backend.app.workers.tasks import process_payment_event

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/webhooks", tags=["webhooks"])


@router.post("/payment")
async def receive_payment(
    request: Request,
    signature: str | None = Header(default=None, alias="X-RevenueGuard-Signature"),
    db: Session = Depends(get_db),  
):
    body = await request.body()
    if not verify_signature(body, signature):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid webhook signature")
    try:
        payload = json.loads(body)
        if not isinstance(payload, dict):
            raise ValueError("JSON event must be an object")
        event = PaymentEvent.model_validate(payload)
    except (json.JSONDecodeError, UnicodeDecodeError, ValueError, ValidationError):
        raise HTTPException(status_code= status.HTTP_422_UNPROCESSABLE_ENTITY, detail="Invalid payment event") from None
    try:
        accepted = persist_event(db, event, payload)
    except TransactionStoreError:
        logger.error("Payment event persistence failed for event %s", event.event_id)
        raise HTTPException(status_code=503, detail="Event could not be persisted") from None
    if not accepted:
        return {"status": "already_received", "event_id": event.event_id}
    try:
        process_payment_event.delay(event.event_id)
    except Exception:
        logger.error("Background enqueue failed for persisted event %s", event.event_id)
        raise HTTPException(status_code=503, detail="Event was saved but downstream queue is unavailable") from None
    return {"status": "accepted", "event_id": event.event_id}
