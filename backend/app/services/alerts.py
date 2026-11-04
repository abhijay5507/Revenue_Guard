from __future__ import annotations

import hashlib
import logging
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import select, func, update
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.orm import Session

from backend.app.models.alert import Alert

logger = logging.getLogger(__name__)


def _make_fingerprint(rule_code: str, event_id: str) -> str:
    
    raw = f"{rule_code}:{event_id}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:64]


def persist_alerts(
    db: Session,
    transaction_db_id: int,
    event_id: str,
    detection_result: Any,
) -> int:
    
    created = 0
    now = datetime.now(UTC)

    for rule in detection_result.triggered_rules:
        fingerprint = _make_fingerprint(rule.rule_code, event_id)

        # Check if this alert already exists (idempotency guard)
        existing = db.execute(
            select(Alert.id).where(Alert.fingerprint == fingerprint)
        ).scalar_one_or_none()

        if existing is not None:
            logger.debug(
                "Alert already exists for rule=%s event=%s (fingerprint=%s)",
                rule.rule_code,
                event_id,
                fingerprint,
            )
            continue

        alert = Alert(
            transaction_id=transaction_db_id,
            fingerprint=fingerprint,
            rule_code=rule.rule_code,
            severity=detection_result.severity.value,
            risk_score=detection_result.risk_score,
            description=rule.reason,
            detection_details={
                "rule_code": rule.rule_code,
                "weight": rule.weight,
                "severity": rule.severity.value,
                "reason": rule.reason,
                **rule.details,
            },
            status="OPEN",
            created_at=now,
            updated_at=now,
        )
        try:
            db.add(alert)
            db.flush()  # Detect integrity errors before commit
            created += 1
        except IntegrityError:
            # Race condition: another worker created the alert first
            db.rollback()
            logger.debug(
                "Concurrent alert creation detected for fingerprint=%s",
                fingerprint,
            )

    if created > 0:
        try:
            db.commit()
            logger.info(
                "Created %d alert(s) for event %s", created, event_id
            )
        except SQLAlchemyError:
            db.rollback()
            logger.exception(
                "Failed to commit alerts for event %s", event_id
            )
            raise

    return created


def get_alerts(
    db: Session,
    *,
    severity: str | None = None,
    status: str | None = None,
    date_from: datetime | None = None,
    date_to: datetime | None = None,
    offset: int = 0,
    limit: int = 50,
) -> tuple[list[Alert], int]:
    """Retrieve alerts with filtering and pagination.

    Returns:
        Tuple of (alerts list, total count).
    """
    query = select(Alert).order_by(Alert.created_at.desc())
    count_query = select(func.count()).select_from(Alert)

    if severity:
        query = query.where(Alert.severity == severity.upper())
        count_query = count_query.where(Alert.severity == severity.upper())
    if status:
        query = query.where(Alert.status == status.upper())
        count_query = count_query.where(Alert.status == status.upper())
    if date_from:
        query = query.where(Alert.created_at >= date_from)
        count_query = count_query.where(Alert.created_at >= date_from)
    if date_to:
        query = query.where(Alert.created_at <= date_to)
        count_query = count_query.where(Alert.created_at <= date_to)

    total = db.execute(count_query).scalar() or 0
    alerts = db.execute(query.offset(offset).limit(limit)).scalars().all()

    return list(alerts), total


def get_alert_by_id(db: Session, alert_id: int) -> Alert | None:
    """Retrieve a single alert by its primary key."""
    return db.execute(
        select(Alert).where(Alert.id == alert_id)
    ).scalar_one_or_none()


def update_alert_status(
    db: Session,
    alert_id: int,
    new_status: str,
) -> Alert | None:
    
    alert = get_alert_by_id(db, alert_id)
    if alert is None:
        return None

    valid_statuses = {"OPEN", "ACKNOWLEDGED", "RESOLVED"}
    new_status = new_status.upper()
    if new_status not in valid_statuses:
        raise ValueError(
            f"Invalid status '{new_status}'. Must be one of {valid_statuses}"
        )

    now = datetime.now(UTC)
    alert.status = new_status
    alert.updated_at = now
    if new_status == "RESOLVED":
        alert.resolved_at = now

    try:
        db.commit()
        db.refresh(alert)
        return alert
    except SQLAlchemyError:
        db.rollback()
        logger.exception("Failed to update alert %d status to %s", alert_id, new_status)
        raise


def get_alert_stats(db: Session) -> dict[str, Any]:
    """Return aggregate alert statistics."""
    total = db.execute(
        select(func.count()).select_from(Alert)
    ).scalar() or 0

    open_count = db.execute(
        select(func.count()).select_from(Alert).where(Alert.status == "OPEN")
    ).scalar() or 0

    critical_count = db.execute(
        select(func.count()).select_from(Alert).where(Alert.severity == "CRITICAL")
    ).scalar() or 0

    # Counts by severity
    severity_rows = db.execute(
        select(Alert.severity, func.count())
        .group_by(Alert.severity)
    ).all()
    by_severity = {row[0]: row[1] for row in severity_rows}

    # Counts by rule_code
    rule_rows = db.execute(
        select(Alert.rule_code, func.count())
        .group_by(Alert.rule_code)
    ).all()
    by_rule = {row[0]: row[1] for row in rule_rows}

    # Recent activity (last 24h)
    cutoff = datetime.now(UTC) - __import__("datetime").timedelta(hours=24)
    recent_count = db.execute(
        select(func.count()).select_from(Alert).where(Alert.created_at >= cutoff)
    ).scalar() or 0

    return {
        "total_alerts": total,
        "open_alerts": open_count,
        "critical_alerts": critical_count,
        "by_severity": by_severity,
        "by_rule": by_rule,
        "recent_24h": recent_count,
    }
