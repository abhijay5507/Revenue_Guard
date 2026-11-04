from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime

from sqlalchemy import select

from backend.app.detection.schemas import DetectionResult, RuleResult, Severity
from backend.app.models.alert import Alert
from backend.app.models.transaction import Transaction
from backend.app.services.alerts import persist_alerts
from backend.app.workers.tasks import process_payment_event


def test_alert_persistence_and_deduplication(client):
    with client.testing_sessions() as db:

        txn = Transaction(
            event_id="evt_test_alert",
            transaction_id="txn_test_alert",
            event_type="payment.failed",
            occurred_at=datetime.now(UTC),
            received_at=datetime.now(UTC),
            amount=10000.0,
            currency="USD",
            status="failed",
            gateway="stripe",
            raw_payload={},
        )
        db.add(txn)
        db.commit()
        db.refresh(txn)

        det_result = DetectionResult(
            transaction_event_id=txn.event_id,
            triggered_rules=[
                RuleResult(
                    rule_code="LARGE_TRANSACTION",
                    triggered=True,
                    severity=Severity.HIGH,
                    weight=65.0,
                    reason="Large payment",
                )
            ],
            risk_score=65.0,
            severity=Severity.HIGH,
            reasons=["Large payment"],
        )


        created = persist_alerts(db, txn.id, txn.event_id, det_result)
        assert created == 1


        alerts = db.execute(select(Alert).where(Alert.transaction_id == txn.id)).scalars().all()
        assert len(alerts) == 1
        assert alerts[0].rule_code == "LARGE_TRANSACTION"
        assert alerts[0].status == "OPEN"


        created_again = persist_alerts(db, txn.id, txn.event_id, det_result)
        assert created_again == 0


        alerts_after = db.execute(select(Alert).where(Alert.transaction_id == txn.id)).scalars().all()
        assert len(alerts_after) == 1


def test_alert_api_endpoints(client):
    with client.testing_sessions() as db:
        txn = Transaction(
            event_id="evt_api_test",
            transaction_id="txn_api_test",
            event_type="payment.succeeded",
            occurred_at=datetime.now(UTC),
            received_at=datetime.now(UTC),
            amount=50000.0,
            currency="USD",
            status="succeeded",
            gateway="paypal",
            raw_payload={},
        )
        db.add(txn)
        db.commit()
        db.refresh(txn)

        alert = Alert(
            transaction_id=txn.id,
            fingerprint="fp_api_test",
            rule_code="LARGE_TRANSACTION",
            severity="CRITICAL",
            risk_score=85.0,
            description="Exorbitant amount",
            detection_details={"amount": 50000.0},
            status="OPEN",
            created_at=datetime.now(UTC),
            updated_at=datetime.now(UTC),
        )
        db.add(alert)
        db.commit()
        db.refresh(alert)
        alert_id = alert.id


    resp = client.get("/api/v1/alerts")
    assert resp.status_code == 200
    data = resp.json()
    assert data["total"] >= 1
    assert any(a["id"] == alert_id for a in data["alerts"])


    resp_stats = client.get("/api/v1/alerts/stats")
    assert resp_stats.status_code == 200
    stats = resp_stats.json()
    assert stats["total_alerts"] >= 1
    assert stats["open_alerts"] >= 1


    resp_single = client.get(f"/api/v1/alerts/{alert_id}")
    assert resp_single.status_code == 200
    assert resp_single.json()["severity"] == "CRITICAL"


    resp_patch = client.patch(f"/api/v1/alerts/{alert_id}", json={"status": "ACKNOWLEDGED"})
    assert resp_patch.status_code == 200
    assert resp_patch.json()["status"] == "ACKNOWLEDGED"


    resp_resolve = client.patch(f"/api/v1/alerts/{alert_id}", json={"status": "RESOLVED"})
    assert resp_resolve.status_code == 200
    assert resp_resolve.json()["status"] == "RESOLVED"
    assert resp_resolve.json()["resolved_at"] is not None


def test_full_process_payment_event_task(client, monkeypatch):
    with client.testing_sessions() as db:

        txn = Transaction(
            event_id="evt_worker_test",
            transaction_id="txn_worker_test",
            event_type="payment.succeeded",
            occurred_at=datetime.now(UTC),
            received_at=datetime.now(UTC),
            amount=50000.0,
            currency="USD",
            status="succeeded",
            gateway="stripe",
            raw_payload={},
        )
        db.add(txn)
        db.commit()


    monkeypatch.setattr("backend.app.workers.tasks.SessionLocal", client.testing_sessions)


    result = process_payment_event.apply(args=["evt_worker_test"]).get()
    assert result["status"] == "processed"
    assert result["risk_score"] > 0
    assert result["alerts_created"] >= 1

    with client.testing_sessions() as db:
        alerts = db.execute(select(Alert)).scalars().all()
        assert len(alerts) >= 1
        assert alerts[0].rule_code == "LARGE_TRANSACTION"
