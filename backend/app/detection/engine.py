
from __future__ import annotations

import logging
from datetime import datetime
from typing import Any

from backend.app.detection.rules import (
    DetectionConfig,
    detect_large_transaction,
    detect_payment_failure_spike,
    detect_repeated_failures,
    detect_volume_spike,
)
from backend.app.detection.schemas import DetectionResult, RuleResult
from backend.app.detection.scoring import ScoringConfig, calculate_risk_score

logger = logging.getLogger(__name__)


def _parse_datetime(val: Any) -> datetime | None:
    if isinstance(val, datetime):
        return val
    if isinstance(val, str):
        try:
            return datetime.fromisoformat(val)
        except (ValueError, TypeError):
            return None
    return None


class AnomalyDetectionEngine:


    def __init__(
        self,
        detection_config: DetectionConfig | None = None,
        scoring_config: ScoringConfig | None = None,
    ) -> None:
        self.detection_config = detection_config or DetectionConfig()
        self.scoring_config = scoring_config or ScoringConfig()

    def evaluate(
        self,
        transaction: dict[str, Any] | Any,
        history: dict[str, Any] | None = None,
    ) -> DetectionResult:
        history = history or {}

        if isinstance(transaction, dict):
            event_id = str(transaction.get("event_id", ""))
            txn_id = str(transaction.get("transaction_id", ""))
            amount = transaction.get("amount")
            currency = str(transaction.get("currency", ""))
            gateway = str(transaction.get("gateway", ""))
            occurred_at_raw = transaction.get("occurred_at")
        else:
            event_id = str(getattr(transaction, "event_id", ""))
            txn_id = str(getattr(transaction, "transaction_id", ""))
            amount = getattr(transaction, "amount", None)
            currency = str(getattr(transaction, "currency", ""))
            gateway = str(getattr(transaction, "gateway", ""))
            occurred_at_raw = getattr(transaction, "occurred_at", None)

        occurred_at = _parse_datetime(occurred_at_raw)

        logger.info(
            "Evaluating transaction %s (event_id=%s, gateway=%s)",
            txn_id,
            event_id,
            gateway,
            extra={"transaction_id": txn_id, "event_id": event_id, "gateway": gateway},
        )

        all_results: list[RuleResult] = []


        try:
            res_a = detect_large_transaction(
                amount=amount,
                currency=currency,
                config=self.detection_config,
            )
            all_results.append(res_a)
        except Exception:
            logger.exception("Failed evaluating Rule A (Large Transaction) for event %s", event_id)


        try:
            gw_txs = history.get("gateway_transactions", [])
            res_b = detect_payment_failure_spike(
                gateway=gateway,
                recent_transactions=gw_txs,
                config=self.detection_config,
                reference_time=occurred_at,
            )
            all_results.append(res_b)
        except Exception:
            logger.exception("Failed evaluating Rule B (Failure Spike) for event %s", event_id)


        try:
            current_count = history.get("gateway_current_count")
            if current_count is None and "gateway_transactions" in history:
                current_count = len(history["gateway_transactions"])
            baseline_count = history.get("gateway_baseline_count")

            res_c = detect_volume_spike(
                gateway=gateway,
                current_count=current_count,
                baseline_count=baseline_count,
                config=self.detection_config,
            )
            all_results.append(res_c)
        except Exception:
            logger.exception("Failed evaluating Rule C (Volume Spike) for event %s", event_id)


        try:
            txn_txs = history.get("txn_id_transactions", [])
            res_d = detect_repeated_failures(
                transaction_id=txn_id,
                recent_transactions=txn_txs,
                config=self.detection_config,
                reference_time=occurred_at,
            )
            all_results.append(res_d)
        except Exception:
            logger.exception("Failed evaluating Rule D (Repeated Failures) for event %s", event_id)

        triggered_rules = [r for r in all_results if r.triggered]

        result = calculate_risk_score(
            triggered_rules=triggered_rules,
            config=self.scoring_config,
            transaction_event_id=event_id,
        )

        logger.info(
            "Evaluation complete for event %s: risk_score=%.1f, severity=%s, triggered=%d",
            event_id,
            result.risk_score,
            result.severity.value,
            len(result.triggered_rules),
            extra={
                "event_id": event_id,
                "risk_score": result.risk_score,
                "severity": result.severity.value,
                "anomalies_count": len(result.triggered_rules),
            },
        )

        return result
