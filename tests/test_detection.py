from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
import random

import pytest

from backend.app.detection.engine import AnomalyDetectionEngine
from backend.app.detection.rules import (
    RULE_LARGE_TRANSACTION,
    RULE_PAYMENT_FAILURE_SPIKE,
    RULE_REPEATED_FAILURES,
    RULE_TRANSACTION_VOLUME_SPIKE,
    DetectionConfig,
    detect_large_transaction,
    detect_payment_failure_spike,
    detect_repeated_failures,
    detect_volume_spike,
)
from backend.app.detection.schemas import RuleResult, Severity
from backend.app.detection.scoring import ScoringConfig, calculate_risk_score
from mock.stream_transactions import generate_scenario





def test_detect_large_transaction_normal():
    res = detect_large_transaction(amount=100.0, currency="USD")
    assert not res.triggered
    assert res.severity == Severity.LOW
    assert res.rule_code == RULE_LARGE_TRANSACTION


def test_detect_large_transaction_threshold_boundary():

    res = detect_large_transaction(amount=5000, currency="USD")
    assert not res.triggered


    res = detect_large_transaction(amount=5001, currency="USD")
    assert res.triggered
    assert res.severity == Severity.MEDIUM


def test_detect_large_transaction_severity_scaling():

    res_high = detect_large_transaction(amount=10000, currency="USD")
    assert res_high.triggered
    assert res_high.severity == Severity.HIGH


    res_crit = detect_large_transaction(amount=25000, currency="USD")
    assert res_crit.triggered
    assert res_crit.severity == Severity.CRITICAL


def test_detect_large_transaction_currencies():

    res_inr_normal = detect_large_transaction(amount=300000, currency="INR")
    assert not res_inr_normal.triggered

    res_inr_large = detect_large_transaction(amount=500000, currency="INR")
    assert res_inr_large.triggered


    res_eur = detect_large_transaction(amount=5000, currency="EUR")
    assert res_eur.triggered


def test_detect_large_transaction_invalid_inputs():
    assert not detect_large_transaction(amount=None, currency="USD").triggered
    assert not detect_large_transaction(amount="invalid", currency="USD").triggered
    assert not detect_large_transaction(amount=-100, currency="USD").triggered
    assert not detect_large_transaction(amount=10000, currency="").triggered






def test_payment_failure_spike_insufficient_samples():
    txs = [
        {"transaction_id": f"tx_{i}", "gateway": "stripe", "status": "failed"}
        for i in range(5)
    ]
    res = detect_payment_failure_spike(gateway="stripe", recent_transactions=txs)
    assert not res.triggered
    assert "Insufficient sample size" in res.reason


def test_payment_failure_spike_triggered():

    txs = []
    for i in range(8):
        txs.append({"transaction_id": f"tx_{i}", "gateway": "stripe", "status": "failed"})
    for i in range(8, 10):
        txs.append({"transaction_id": f"tx_{i}", "gateway": "stripe", "status": "succeeded"})

    res = detect_payment_failure_spike(gateway="stripe", recent_transactions=txs)
    assert res.triggered
    assert res.severity == Severity.CRITICAL


def test_payment_failure_spike_deduplicates_retries():

    txs = [
        {"transaction_id": "tx_same_1", "gateway": "stripe", "status": "failed"}
        for _ in range(5)
    ] + [
        {"transaction_id": "tx_same_2", "gateway": "stripe", "status": "failed"}
        for _ in range(5)
    ]
    res = detect_payment_failure_spike(gateway="stripe", recent_transactions=txs)

    assert not res.triggered
    assert res.details["total_count"] == 2






def test_volume_spike_insufficient_baseline():
    res = detect_volume_spike(gateway="stripe", current_count=20, baseline_count=2)
    assert not res.triggered
    assert "below minimum requirement" in res.reason


def test_volume_spike_triggered():

    res = detect_volume_spike(gateway="stripe", current_count=35, baseline_count=10)
    assert res.triggered
    assert res.severity == Severity.MEDIUM


    res_crit = detect_volume_spike(gateway="stripe", current_count=105, baseline_count=10)
    assert res_crit.triggered
    assert res_crit.severity == Severity.CRITICAL






def test_repeated_failures_triggered():
    txs = [
        {"event_id": f"evt_{i}", "transaction_id": "txn_target", "status": "failed"}
        for i in range(4)
    ]
    res = detect_repeated_failures(transaction_id="txn_target", recent_transactions=txs)
    assert res.triggered
    assert res.details["failed_event_count"] == 4


def test_repeated_failures_deduplicates_event_ids():

    txs = [
        {"event_id": "evt_duplicate", "transaction_id": "txn_target", "status": "failed"}
        for _ in range(5)
    ]
    res = detect_repeated_failures(transaction_id="txn_target", recent_transactions=txs)
    assert not res.triggered
    assert res.details["failed_event_count"] == 1






def test_risk_scoring_aggregation_and_deduplication():
    rule1 = RuleResult(
        rule_code=RULE_LARGE_TRANSACTION,
        triggered=True,
        severity=Severity.HIGH,
        weight=65.0,
        reason="Large payment",
    )

    rule1_dup = RuleResult(
        rule_code=RULE_LARGE_TRANSACTION,
        triggered=True,
        severity=Severity.MEDIUM,
        weight=35.0,
        reason="Large payment lower",
    )
    rule2 = RuleResult(
        rule_code=RULE_PAYMENT_FAILURE_SPIKE,
        triggered=True,
        severity=Severity.MEDIUM,
        weight=35.0,
        reason="Failure spike",
    )

    res = calculate_risk_score([rule1, rule1_dup, rule2], transaction_event_id="evt_test")

    assert res.risk_score == 100.0
    assert res.severity == Severity.CRITICAL
    assert len(res.triggered_rules) == 2


def test_anomaly_detection_engine_evaluate():
    engine = AnomalyDetectionEngine()

    transaction = {
        "event_id": "evt_eval_1",
        "transaction_id": "txn_eval_1",
        "amount": 25000.0,
        "currency": "USD",
        "gateway": "stripe",
        "status": "succeeded",
        "occurred_at": datetime.now(UTC).isoformat(),
    }
    history = {
        "gateway_transactions": [],
        "txn_id_transactions": [],
        "gateway_baseline_count": 10,
        "gateway_current_count": 10,
    }

    result = engine.evaluate(transaction, history)
    assert result.has_anomalies
    assert result.risk_score > 0
    assert any(r.rule_code == RULE_LARGE_TRANSACTION for r in result.triggered_rules)


def test_mock_generator_seed_labels_and_anomaly_rate():
    base_time = datetime(2026, 1, 1, tzinfo=UTC)
    first = generate_scenario("normal", random.Random(42), 8, base_time, 1.0)
    second = generate_scenario("normal", random.Random(42), 8, base_time, 1.0)

    assert first == second
    assert all(event["scenario"] == "large_payment" for event in first)
    assert len({event["event_id"] for event in first}) == 8
