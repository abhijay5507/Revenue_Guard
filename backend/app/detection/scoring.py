"""Explainable risk scoring engine.

Produces a heuristic risk indicator (0-100) from triggered detection rules.
This is NOT a statistically calibrated fraud probability.
"""
from __future__ import annotations

from dataclasses import dataclass

from backend.app.detection.schemas import DetectionResult, RuleResult, Severity


@dataclass(frozen=True)
class ScoringConfig:
    """Thresholds for mapping raw score to severity."""

    low_max: float = 25.0
    medium_max: float = 50.0
    high_max: float = 75.0
    # Above high_max → CRITICAL


def calculate_risk_score(
    triggered_rules: list[RuleResult],
    config: ScoringConfig | None = None,
    transaction_event_id: str = "",
) -> DetectionResult:
    """Calculate aggregated risk score and severity from triggered rules.

    - De-duplicates by rule_code (retains the instance with highest weight)
    - Sums the weights of triggered rules, capped at 100
    - Maps the score to severity using ScoringConfig thresholds
    - Collects all reasons from triggered rules
    """
    if isinstance(config, str) and not transaction_event_id:
        transaction_event_id = config
        config = None

    cfg = config or ScoringConfig()

    active_rules = [r for r in triggered_rules if r.triggered]

    deduped: dict[str, RuleResult] = {}
    for rule in active_rules:
        existing = deduped.get(rule.rule_code)
        if existing is None or rule.weight > existing.weight:
            deduped[rule.rule_code] = rule

    final_rules = list(deduped.values())
    raw_score = sum(r.weight for r in final_rules)
    risk_score = round(min(100.0, max(0.0, float(raw_score))), 2)

    if risk_score <= cfg.low_max:
        severity = Severity.LOW
    elif risk_score <= cfg.medium_max:
        severity = Severity.MEDIUM
    elif risk_score <= cfg.high_max:
        severity = Severity.HIGH
    else:
        severity = Severity.CRITICAL

    reasons = [r.reason for r in final_rules if r.reason]

    return DetectionResult(
        transaction_event_id=transaction_event_id,
        triggered_rules=final_rules,
        risk_score=risk_score,
        severity=severity,
        reasons=reasons,
    )
