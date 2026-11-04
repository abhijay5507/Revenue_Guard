"""REST API routes for anomaly detection alert management."""
from __future__ import annotations

import logging
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from backend.app.db.session import get_db
from backend.app.schemas.alert import (
    AlertListResponse,
    AlertResponse,
    AlertStatsResponse,
    AlertStatusUpdate,
)
from backend.app.services.alerts import (
    get_alert_by_id,
    get_alert_stats,
    get_alerts,
    update_alert_status,
)

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/alerts", tags=["alerts"])


@router.get("", response_model=AlertListResponse)
def list_alerts(
    severity: str | None = Query(default=None, description="Filter by severity: LOW, MEDIUM, HIGH, CRITICAL"),
    alert_status: str | None = Query(default=None, alias="status", description="Filter by status: OPEN, ACKNOWLEDGED, RESOLVED"),
    date_from: datetime | None = Query(default=None, description="Filter alerts created after this ISO 8601 timestamp"),
    date_to: datetime | None = Query(default=None, description="Filter alerts created before this ISO 8601 timestamp"),
    offset: int = Query(default=0, ge=0, description="Pagination offset"),
    limit: int = Query(default=50, ge=1, le=200, description="Page size"),
    db: Session = Depends(get_db),  # noqa: B008
) -> AlertListResponse:
    """Retrieve alerts with optional filtering and pagination."""
    alerts, total = get_alerts(
        db,
        severity=severity,
        status=alert_status,
        date_from=date_from,
        date_to=date_to,
        offset=offset,
        limit=limit,
    )
    return AlertListResponse(
        alerts=[AlertResponse.model_validate(a) for a in alerts],
        total=total,
        offset=offset,
        limit=limit,
    )


@router.get("/stats", response_model=AlertStatsResponse)
def alert_statistics(
    db: Session = Depends(get_db),  # noqa: B008
) -> AlertStatsResponse:
    """Return aggregate alert statistics."""
    stats = get_alert_stats(db)
    return AlertStatsResponse(**stats)


@router.get("/{alert_id}", response_model=AlertResponse)
def get_alert(
    alert_id: int,
    db: Session = Depends(get_db),  # noqa: B008
) -> AlertResponse:
    """Retrieve a specific alert by ID."""
    alert = get_alert_by_id(db, alert_id)
    if alert is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Alert {alert_id} not found",
        )
    return AlertResponse.model_validate(alert)


@router.patch("/{alert_id}", response_model=AlertResponse)
def patch_alert_status(
    alert_id: int,
    body: AlertStatusUpdate,
    db: Session = Depends(get_db),  # noqa: B008
) -> AlertResponse:
    """Update an alert's status (OPEN → ACKNOWLEDGED → RESOLVED)."""
    try:
        alert = update_alert_status(db, alert_id, body.status)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=str(exc),
        ) from None

    if alert is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Alert {alert_id} not found",
        )
    return AlertResponse.model_validate(alert)
