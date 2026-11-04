"""Anomaly detection rules for RevenueGuard.

Each rule is an independent function that evaluates transaction data and returns
a RuleResult without dependencies on database or API layers.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from decimal import Decimal, InvalidOperation
from typing import Any

from backend.app.detection.schemas import RuleResult, Severity

RULE_LARGE_TRANSACTION = "LARGE_TRANSACTION"
RULE_PAYMENT_FAILURE_SPIKE = "PAYMENT_FAILURE_SPIKE"
RULE_TRANSACTION_VOLUME_SPIKE = "TRANSACTION_VOLUME_SPIKE"
RULE_REPEATED_FAILURES = "REPEATED_FAILURES"


def _parse_datetime(val: Any) -> datetime | None:
    if isinstance(val, datetime):
        return val
    if isinstance(val, str):
        try:
            return datetime.fromisoformat(val)
        except (ValueError, TypeError):
            return None
    return None


@dataclass
class DetectionConfig:
    """Configurable thresholds and parameters for anomaly detection rules."""

    currency_thresholds: dict[str, Decimal] = field(
        default_factory=lambda: {
            "USD": Decimal("5000"),
            "EUR": Decimal("4500"),
            "GBP": Decimal("4000"),
            "INR": Decimal("400000"),
            "DEFAULT": Decimal("5000"),
        }
    )
    large_transaction_multiplier_critical: float = 5.0
    large_transaction_multiplier_high: float = 2.0

    failure_spike_window_minutes: int = 15
    failure_spike_min_sample_size: int = 10
    failure_spike_rate_threshold: float = 0.5
    failure_spike_rate_high: float = 0.65
    failure_spike_rate_critical: float = 0.8

    volume_spike_multiplier: float = 3.0
    volume_spike_min_baseline: int = 5
    volume_spike_multiplier_high: float = 5.0
    volume_spike_multiplier_critical: float = 10.0

    repeated_failures_threshold: int = 3
    repeated_failures_window_minutes: int = 60
    repeated_failures_high: int = 6
    repeated_failures_critical: int = 10

    severity_weights: dict[Severity, float] = field(
        default_factory=lambda: {
            Severity.LOW: 20.0,
            Severity.MEDIUM: 35.0,
            Severity.HIGH: 65.0,
            Severity.CRITICAL: 85.0,
        }
    )

    rule_weights: dict[str, float] = field(default_factory=dict)

    def get_weight(self, rule_code: str, severity: Severity) -> float:
        if rule_code in self.rule_weights:
            return float(self.rule_weights[rule_code])
        return float(self.severity_weights.get(severity, 35.0))


def detect_large_transaction(
    amount: Decimal | float | int | str | None,
    currency: str | None,
    config: DetectionConfig | None = None,
) -> RuleResult:
    cfg = config or DetectionConfig()

    if amount is None or amount == "":
        return RuleResult(
            rule_code=RULE_LARGE_TRANSACTION,
            triggered=False,
            severity=Severity.LOW,
            weight=0.0,
            reason="Missing transaction amount",
            details={},
        )

    try:
        dec_amount = Decimal(str(amount))
    except (InvalidOperation, TypeError, ValueError):
        return RuleResult(
            rule_code=RULE_LARGE_TRANSACTION,
            triggered=False,
            severity=Severity.LOW,
            weight=0.0,
            reason=f"Invalid transaction amount: {amount}",
            details={},
        )

    if dec_amount <= 0:
        return RuleResult(
            rule_code=RULE_LARGE_TRANSACTION,
            triggered=False,
            severity=Severity.LOW,
            weight=0.0,
            reason="Transaction amount must be positive",
            details={"amount": float(dec_amount)},
        )

    if not currency or not isinstance(currency, str):
        return RuleResult(
            rule_code=RULE_LARGE_TRANSACTION,
            triggered=False,
            severity=Severity.LOW,
            weight=0.0,
            reason="Missing or invalid transaction currency",
            details={"amount": float(dec_amount)},
        )

    curr_clean = currency.strip().upper()
    threshold = cfg.currency_thresholds.get(
        curr_clean, cfg.currency_thresholds.get("DEFAULT", Decimal("5000"))
    )

    ratio = float(dec_amount / threshold)

    if dec_amount <= threshold:
        return RuleResult(
            rule_code=RULE_LARGE_TRANSACTION,
            triggered=False,
            severity=Severity.LOW,
            weight=0.0,
            reason=(
                f"Transaction amount {dec_amount} {curr_clean} does not exceed "
                f"threshold of {threshold} {curr_clean}"
            ),
            details={
                "amount": float(dec_amount),
                "currency": curr_clean,
                "threshold": float(threshold),
                "ratio": round(ratio, 2),
            },
        )

    if ratio >= cfg.large_transaction_multiplier_critical:
        severity = Severity.CRITICAL
    elif ratio >= cfg.large_transaction_multiplier_high:
        severity = Severity.HIGH
    else:
        severity = Severity.MEDIUM

    weight = cfg.get_weight(RULE_LARGE_TRANSACTION, severity)
    reason = (
        f"Transaction amount {dec_amount} {curr_clean} exceeds threshold "
        f"{threshold} {curr_clean} by {ratio:.2f}x ({severity.value})"
    )

    return RuleResult(
        rule_code=RULE_LARGE_TRANSACTION,
        triggered=True,
        severity=severity,
        weight=weight,
        reason=reason,
        details={
            "amount": float(dec_amount),
            "currency": curr_clean,
            "threshold": float(threshold),
            "ratio": round(ratio, 2),
        },
    )


def detect_payment_failure_spike(
    gateway: str | None,
    recent_transactions: list[dict[str, Any] | Any] | None,
    config: DetectionConfig | None = None,
    reference_time: datetime | None = None,
) -> RuleResult:
    cfg = config or DetectionConfig()

    if not gateway or recent_transactions is None:
        return RuleResult(
            rule_code=RULE_PAYMENT_FAILURE_SPIKE,
            triggered=False,
            severity=Severity.LOW,
            weight=0.0,
            reason="Missing gateway or recent transactions data",
            details={},
        )

    gw_clean = str(gateway).strip().lower()
    window_minutes = cfg.failure_spike_window_minutes
    cutoff = reference_time - timedelta(minutes=window_minutes) if reference_time else None

    total_tx_ids: set[str] = set()
    failed_tx_ids: set[str] = set()

    for item in recent_transactions:
        item_gw = item.get("gateway") if isinstance(item, dict) else getattr(item, "gateway", None)
        if not item_gw or str(item_gw).strip().lower() != gw_clean:
            continue

        if cutoff is not None:
            ts = item.get("occurred_at") or item.get("received_at") if isinstance(item, dict) else getattr(item, "occurred_at", None) or getattr(item, "received_at", None)
            dt = _parse_datetime(ts)
            if dt is not None and (dt < cutoff or (reference_time and dt > reference_time)):
                continue

        tx_id = item.get("transaction_id") if isinstance(item, dict) else getattr(item, "transaction_id", None)
        if not tx_id:
            continue

        str_tx_id = str(tx_id)
        total_tx_ids.add(str_tx_id)

        status = item.get("status") if isinstance(item, dict) else getattr(item, "status", None)
        if status and str(status).strip().lower() == "failed":
            failed_tx_ids.add(str_tx_id)

    total_count = len(total_tx_ids)
    failed_count = len(failed_tx_ids)

    if total_count < cfg.failure_spike_min_sample_size:
        return RuleResult(
            rule_code=RULE_PAYMENT_FAILURE_SPIKE,
            triggered=False,
            severity=Severity.LOW,
            weight=0.0,
            reason=(
                f"Insufficient sample size for gateway '{gateway}' "
                f"({total_count} < {cfg.failure_spike_min_sample_size})"
            ),
            details={
                "gateway": gateway,
                "total_count": total_count,
                "failed_count": failed_count,
                "min_sample_size": cfg.failure_spike_min_sample_size,
            },
        )

    rate = failed_count / total_count

    if rate < cfg.failure_spike_rate_threshold:
        return RuleResult(
            rule_code=RULE_PAYMENT_FAILURE_SPIKE,
            triggered=False,
            severity=Severity.LOW,
            weight=0.0,
            reason=(
                f"Payment failure rate for gateway '{gateway}' is {rate:.1%} "
                f"(below threshold {cfg.failure_spike_rate_threshold:.1%})"
            ),
            details={
                "gateway": gateway,
                "total_count": total_count,
                "failed_count": failed_count,
                "failure_rate": round(rate, 4),
                "threshold": cfg.failure_spike_rate_threshold,
            },
        )

    if rate >= cfg.failure_spike_rate_critical:
        severity = Severity.CRITICAL
    elif rate >= cfg.failure_spike_rate_high:
        severity = Severity.HIGH
    else:
        severity = Severity.MEDIUM

    weight = cfg.get_weight(RULE_PAYMENT_FAILURE_SPIKE, severity)
    reason = (
        f"Payment failure spike detected for gateway '{gateway}': "
        f"{rate:.1%} failure rate ({failed_count}/{total_count} transactions) ({severity.value})"
    )

    return RuleResult(
        rule_code=RULE_PAYMENT_FAILURE_SPIKE,
        triggered=True,
        severity=severity,
        weight=weight,
        reason=reason,
        details={
            "gateway": gateway,
            "total_count": total_count,
            "failed_count": failed_count,
            "failure_rate": round(rate, 4),
            "threshold": cfg.failure_spike_rate_threshold,
        },
    )


def detect_volume_spike(
    gateway: str | None,
    current_count: int | None,
    baseline_count: float | int | None,
    config: DetectionConfig | None = None,
) -> RuleResult:
    cfg = config or DetectionConfig()

    if not gateway or current_count is None or baseline_count is None:
        return RuleResult(
            rule_code=RULE_TRANSACTION_VOLUME_SPIKE,
            triggered=False,
            severity=Severity.LOW,
            weight=0.0,
            reason="Missing gateway or volume count parameters",
            details={},
        )

    try:
        curr_c = float(current_count)
        base_c = float(baseline_count)
    except (TypeError, ValueError):
        return RuleResult(
            rule_code=RULE_TRANSACTION_VOLUME_SPIKE,
            triggered=False,
            severity=Severity.LOW,
            weight=0.0,
            reason="Invalid volume count values",
            details={"current_count": current_count, "baseline_count": baseline_count},
        )

    if base_c < cfg.volume_spike_min_baseline:
        return RuleResult(
            rule_code=RULE_TRANSACTION_VOLUME_SPIKE,
            triggered=False,
            severity=Severity.LOW,
            weight=0.0,
            reason=(
                f"Baseline count ({base_c:.1f}) is below minimum requirement "
                f"({cfg.volume_spike_min_baseline}) for gateway '{gateway}'"
            ),
            details={
                "gateway": gateway,
                "current_count": curr_c,
                "baseline_count": base_c,
                "min_baseline": cfg.volume_spike_min_baseline,
            },
        )

    ratio = curr_c / base_c

    if ratio < cfg.volume_spike_multiplier:
        return RuleResult(
            rule_code=RULE_TRANSACTION_VOLUME_SPIKE,
            triggered=False,
            severity=Severity.LOW,
            weight=0.0,
            reason=(
                f"Transaction volume ratio for gateway '{gateway}' is {ratio:.2f}x "
                f"(below threshold {cfg.volume_spike_multiplier:.1f}x)"
            ),
            details={
                "gateway": gateway,
                "current_count": curr_c,
                "baseline_count": base_c,
                "ratio": round(ratio, 2),
                "threshold": cfg.volume_spike_multiplier,
            },
        )

    if ratio >= cfg.volume_spike_multiplier_critical:
        severity = Severity.CRITICAL
    elif ratio >= cfg.volume_spike_multiplier_high:
        severity = Severity.HIGH
    else:
        severity = Severity.MEDIUM

    weight = cfg.get_weight(RULE_TRANSACTION_VOLUME_SPIKE, severity)
    reason = (
        f"Transaction volume spike detected for gateway '{gateway}': "
        f"{ratio:.2f}x baseline ({curr_c:.0f} vs {base_c:.1f}) ({severity.value})"
    )

    return RuleResult(
        rule_code=RULE_TRANSACTION_VOLUME_SPIKE,
        triggered=True,
        severity=severity,
        weight=weight,
        reason=reason,
        details={
            "gateway": gateway,
            "current_count": curr_c,
            "baseline_count": base_c,
            "ratio": round(ratio, 2),
            "threshold": cfg.volume_spike_multiplier,
        },
    )


def detect_repeated_failures(
    transaction_id: str | None,
    recent_transactions: list[dict[str, Any] | Any] | None,
    config: DetectionConfig | None = None,
    reference_time: datetime | None = None,
) -> RuleResult:
    cfg = config or DetectionConfig()

    if not transaction_id or recent_transactions is None:
        return RuleResult(
            rule_code=RULE_REPEATED_FAILURES,
            triggered=False,
            severity=Severity.LOW,
            weight=0.0,
            reason="Missing transaction_id or recent transactions data",
            details={},
        )

    target_txn_id = str(transaction_id).strip()
    window_minutes = cfg.repeated_failures_window_minutes
    cutoff = reference_time - timedelta(minutes=window_minutes) if reference_time else None

    failed_event_ids: set[str] = set()
    anon_failed_count = 0

    for item in recent_transactions:
        item_txn = item.get("transaction_id") if isinstance(item, dict) else getattr(item, "transaction_id", None)
        if item_txn and str(item_txn).strip() != target_txn_id:
            continue

        status = item.get("status") if isinstance(item, dict) else getattr(item, "status", None)
        if not status or str(status).strip().lower() != "failed":
            continue

        if cutoff is not None:
            ts = item.get("occurred_at") or item.get("received_at") if isinstance(item, dict) else getattr(item, "occurred_at", None) or getattr(item, "received_at", None)
            dt = _parse_datetime(ts)
            if dt is not None and (dt < cutoff or (reference_time and dt > reference_time)):
                continue

        event_id = item.get("event_id") if isinstance(item, dict) else getattr(item, "event_id", None)
        if event_id:
            failed_event_ids.add(str(event_id))
        else:
            anon_failed_count += 1

    total_failed = len(failed_event_ids) + anon_failed_count

    if total_failed < cfg.repeated_failures_threshold:
        return RuleResult(
            rule_code=RULE_REPEATED_FAILURES,
            triggered=False,
            severity=Severity.LOW,
            weight=0.0,
            reason=(
                f"Repeated failures count for transaction '{transaction_id}' is "
                f"{total_failed} (below threshold {cfg.repeated_failures_threshold})"
            ),
            details={
                "transaction_id": transaction_id,
                "failed_event_count": total_failed,
                "threshold": cfg.repeated_failures_threshold,
                "window_minutes": cfg.repeated_failures_window_minutes,
            },
        )

    if total_failed >= cfg.repeated_failures_critical:
        severity = Severity.CRITICAL
    elif total_failed >= cfg.repeated_failures_high:
        severity = Severity.HIGH
    else:
        severity = Severity.MEDIUM

    weight = cfg.get_weight(RULE_REPEATED_FAILURES, severity)
    reason = (
        f"Repeated payment failures detected for transaction '{transaction_id}': "
        f"{total_failed} failed attempts ({severity.value})"
    )

    return RuleResult(
        rule_code=RULE_REPEATED_FAILURES,
        triggered=True,
        severity=severity,
        weight=weight,
        reason=reason,
        details={
            "transaction_id": transaction_id,
            "failed_event_count": total_failed,
            "threshold": cfg.repeated_failures_threshold,
            "window_minutes": cfg.repeated_failures_window_minutes,
        },
    )
