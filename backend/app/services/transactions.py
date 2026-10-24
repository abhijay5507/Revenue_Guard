from datetime import UTC, datetime

from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.orm import Session

from backend.app.models.transaction import Transaction
from backend.app.schemas.payment_event import PaymentEvent


class TransactionStoreError(Exception):
    """A database failure prevented reliable event persistence."""


def persist_event(db: Session, event: PaymentEvent, raw_payload: dict) -> bool:
    # Payment providers may include additional metadata. Persist only fields
    # represented by the Transaction model while retaining all fields in JSONB.
    transaction_fields = {
        "event_id",
        "transaction_id",
        "event_type",
        "occurred_at",
        "amount",
        "currency",
        "status",
        "gateway",
        "browser",
        "country",
        "error_code",
    }
    transaction = Transaction(
        **event.model_dump(include=transaction_fields),
        received_at=datetime.now(UTC),
        raw_payload=raw_payload,
    )
    try:
        db.add(transaction)
        db.commit()
        return True
    except IntegrityError:
        db.rollback()
        if db.query(Transaction.id).filter(Transaction.event_id == event.event_id).first():
            return False
        raise TransactionStoreError("Event persistence constraint failed") from None
    except SQLAlchemyError:
        db.rollback()
        raise TransactionStoreError("Event persistence failed") from None
