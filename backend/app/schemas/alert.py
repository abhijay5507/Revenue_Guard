
from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class AlertResponse(BaseModel):

    model_config = ConfigDict(from_attributes=True)

    id: int
    transaction_id: int
    fingerprint: str
    rule_code: str
    severity: str
    risk_score: float
    description: str
    detection_details: dict[str, Any]
    status: str
    created_at: datetime
    updated_at: datetime
    resolved_at: datetime | None = None


class AlertListResponse(BaseModel):

    alerts: list[AlertResponse]
    total: int
    offset: int
    limit: int


class AlertStatusUpdate(BaseModel):

    status: str = Field(
        description="New status: OPEN, ACKNOWLEDGED, or RESOLVED",
        pattern="^(OPEN|ACKNOWLEDGED|RESOLVED)$",
    )


class AlertStatsResponse(BaseModel):

    total_alerts: int
    open_alerts: int
    critical_alerts: int
    by_severity: dict[str, int]
    by_rule: dict[str, int]
    recent_24h: int
