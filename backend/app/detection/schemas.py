"""Pydantic schemas for the anomaly detection engine."""
from __future__ import annotations

from enum import Enum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class Severity(str, Enum):
    """Alert severity levels."""

    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"
    CRITICAL = "CRITICAL"


class RuleResult(BaseModel):
    """Result of a single detection rule evaluation."""

    model_config = ConfigDict(frozen=True)

    rule_code: str = Field(description="Unique rule identifier, e.g. LARGE_TRANSACTION")
    triggered: bool = Field(description="Whether the rule was triggered")
    severity: Severity = Field(description="Severity when triggered")
    weight: float = Field(ge=0, le=100, description="Weight for risk scoring")
    reason: str = Field(description="Human-readable explanation")
    details: dict[str, Any] = Field(default_factory=dict, description="Additional context")


class DetectionResult(BaseModel):
    """Aggregated result from the detection engine for a single evaluation."""

    model_config = ConfigDict(frozen=True)

    transaction_event_id: str
    triggered_rules: list[RuleResult] = Field(default_factory=list)
    risk_score: float = Field(ge=0, le=100, default=0)
    severity: Severity = Field(default=Severity.LOW)
    reasons: list[str] = Field(default_factory=list)

    @property
    def has_anomalies(self) -> bool:
        return len(self.triggered_rules) > 0
