from __future__ import annotations

import logging
import time
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from backend.app.db.session import SessionLocal
from backend.app.models.transaction import Transaction
from backend.app.workers.celery_app import celery_app

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Configuration constants (will be overridden by detection engine defaults)
# ---------------------------------------------------------------------------
HISTORY_WINDOW_MINUTES: int = 60  # look-back for gateway history
BASELINE_WINDOW_MINUTES: int = 1440  # 24h baseline window


def _serialize_transaction(txn: Transaction) -> dict[str, Any]:
    """Convert a Transaction ORM object to a plain dict for the detection engine."""
    return {
        "event_id": txn.event_id,
        "transaction_id": txn.transaction_id,
        "event_type": txn.event_type,
        "occurred_at": txn.occurred_at,
        "received_at": txn.received_at,
        "amount": float(txn.amount),
        "currency": txn.currency,
        "status": txn.status,
        "gateway": txn.gateway,
        "browser": txn.browser,
        "country": txn.country,
        "error_code": txn.error_code,
    }


def _load_transaction(db: Session, event_id: str) -> Transaction | None:
    """Load a single transaction by event_id."""
    return db.execute(
        select(Transaction).where(Transaction.event_id == event_id)
    ).scalar_one_or_none()


def _load_gateway_history(
    db: Session,
    gateway: str,
    window_start: datetime,
    window_end: datetime,
) -> list[dict[str, Any]]:
    """Load recent transactions for a gateway within a time window."""
    rows = db.execute(
        select(Transaction)
        .where(
            Transaction.gateway == gateway,
            Transaction.occurred_at >= window_start,
            Transaction.occurred_at <= window_end,
        )
        .order_by(Transaction.occurred_at.desc())
    ).scalars().all()
    return [_serialize_transaction(r) for r in rows]


def _load_txn_id_history(
    db: Session,
    transaction_id: str,
    window_start: datetime,
    window_end: datetime,
) -> list[dict[str, Any]]:
    """Load recent events for a specific transaction_id."""
    rows = db.execute(
        select(Transaction)
        .where(
            Transaction.transaction_id == transaction_id,
            Transaction.occurred_at >= window_start,
            Transaction.occurred_at <= window_end,
        )
        .order_by(Transaction.occurred_at.desc())
    ).scalars().all()
    return [_serialize_transaction(r) for r in rows]


def _count_gateway_transactions(
    db: Session,
    gateway: str,
    window_start: datetime,
    window_end: datetime,
) -> int:
    """Count transactions for a gateway in a time window."""
    result = db.execute(
        select(func.count())
        .select_from(Transaction)
        .where(
            Transaction.gateway == gateway,
            Transaction.occurred_at >= window_start,
            Transaction.occurred_at <= window_end,
        )
    ).scalar()
    return result or 0


@celery_app.task(
    name="revenueguard.process_payment_event",
    bind=True,
    max_retries=3,
    default_retry_delay=10,
    acks_late=True,
    reject_on_worker_lost=True,
)
def process_payment_event(self, event_id: str) -> dict[str, Any]:
    
    start_time = time.monotonic()
    logger.info("Processing payment event: %s", event_id)

    db: Session = SessionLocal()
    try:
        # --- Step 1: Load the persisted transaction ----------------------
        txn = _load_transaction(db, event_id)
        if txn is None:
            logger.warning(
                "Transaction not found for event_id=%s — may have been "
                "deleted or the persistence step failed. Skipping.",
                event_id,
            )
            return {"status": "skipped", "reason": "transaction_not_found", "event_id": event_id}

        txn_data = _serialize_transaction(txn)
        txn_db_id = txn.id  # PK needed for alert persistence

        # --- Step 2: Gather historical context ---------------------------
        now = txn.occurred_at  # use the event's own timestamp as reference
        history_start = now - timedelta(minutes=HISTORY_WINDOW_MINUTES)
        baseline_start = now - timedelta(minutes=BASELINE_WINDOW_MINUTES)

        # Recent gateway transactions (for failure spike + volume spike)
        gateway_txns = _load_gateway_history(db, txn.gateway, history_start, now)

        # Baseline gateway count (24h)
        gateway_baseline_count = _count_gateway_transactions(
            db, txn.gateway, baseline_start, history_start,
        )

        # Repeated-failure history for this transaction_id
        txn_id_txns = _load_txn_id_history(db, txn.transaction_id, history_start, now)

        history = {
            "gateway_transactions": gateway_txns,
            "txn_id_transactions": txn_id_txns,
            "gateway_current_count": len(gateway_txns),
            "gateway_baseline_count": gateway_baseline_count,
        }

        # --- Step 3: Run anomaly detection -------------------------------
        try:
            from backend.app.detection.engine import AnomalyDetectionEngine

            engine = AnomalyDetectionEngine()
            result = engine.evaluate(txn_data, history)
        except ImportError:
            # Detection engine not yet implemented — log and succeed
            logger.info(
                "Detection engine not available; skipping analysis for %s",
                event_id,
            )
            elapsed = time.monotonic() - start_time
            return {
                "status": "processed",
                "detection": "unavailable",
                "event_id": event_id,
                "elapsed_seconds": round(elapsed, 4),
            }

        # --- Step 4: Persist alerts if triggered -------------------------
        alerts_created = 0
        if result.has_anomalies:
            try:
                from backend.app.services.alerts import persist_alerts

                alerts_created = persist_alerts(
                    db=db,
                    transaction_db_id=txn_db_id,
                    event_id=event_id,
                    detection_result=result,
                )
            except ImportError:
                logger.info(
                    "Alert persistence not available; %d anomalies detected "
                    "but not persisted for %s",
                    len(result.triggered_rules),
                    event_id,
                )

        elapsed = time.monotonic() - start_time
        logger.info(
            "Processed event %s in %.4fs — risk_score=%.1f severity=%s "
            "rules_triggered=%d alerts_created=%d",
            event_id,
            elapsed,
            result.risk_score,
            result.severity.value,
            len(result.triggered_rules),
            alerts_created,
        )

        return {
            "status": "processed",
            "event_id": event_id,
            "risk_score": result.risk_score,
            "severity": result.severity.value,
            "rules_triggered": [r.rule_code for r in result.triggered_rules],
            "alerts_created": alerts_created,
            "elapsed_seconds": round(elapsed, 4),
        }

    except Exception as exc:
        logger.exception("Error processing event %s: %s", event_id, exc)
        try:
            self.retry(exc=exc)
        except self.MaxRetriesExceededError:
            logger.error(
                "Max retries exceeded for event %s — marking as failed",
                event_id,
            )
            return {"status": "failed", "event_id": event_id, "error": str(exc)}
        # Should not reach here, but satisfy type checkers
        return {"status": "retrying", "event_id": event_id}
    finally:
        db.close()
