from __future__ import annotations

import hashlib
import hmac
import json
import time
from datetime import UTC, datetime

from sqlalchemy import select

from backend.app.models.alert import Alert
from backend.app.models.transaction import Transaction
from backend.app.workers.tasks import process_payment_event


def signed(payload: dict, secret: str = "test-secret") -> tuple[bytes, str]:
    body = json.dumps(payload, separators=(",", ":")).encode("utf-8")
    signature = hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()
    return body, signature


def test_end_to_end_anomaly_flow(client, monkeypatch):

    events_enqueued = []

    def mock_delay(event_id: str):
        events_enqueued.append(event_id)

        monkeypatch.setattr("backend.app.workers.tasks.SessionLocal", client.testing_sessions)
        process_payment_event.apply(args=[event_id]).get()

    monkeypatch.setattr("backend.app.api.routes.webhooks.process_payment_event.delay", mock_delay)


    normal_payload = {
        "event_id": "evt_e2e_normal",
        "transaction_id": "txn_e2e_1",
        "event_type": "payment.succeeded",
        "occurred_at": datetime.now(UTC).isoformat(),
        "amount": "45.0000",
        "currency": "USD",
        "status": "succeeded",
        "gateway": "stripe",
    }
    body, sig = signed(normal_payload)
    resp = client.post("/api/v1/webhooks/payment", content=body, headers={"X-RevenueGuard-Signature": sig})
    assert resp.status_code == 200
    assert resp.json()["status"] == "accepted"


    stats_resp = client.get("/api/v1/alerts/stats")
    assert stats_resp.json()["total_alerts"] == 0


    anom_payload = {
        "event_id": "evt_e2e_anom",
        "transaction_id": "txn_e2e_2",
        "event_type": "payment.succeeded",
        "occurred_at": datetime.now(UTC).isoformat(),
        "amount": "35000.0000",
        "currency": "USD",
        "status": "succeeded",
        "gateway": "stripe",
    }
    body_anom, sig_anom = signed(anom_payload)
    resp_anom = client.post("/api/v1/webhooks/payment", content=body_anom, headers={"X-RevenueGuard-Signature": sig_anom})
    assert resp_anom.status_code == 200
    assert resp_anom.json()["status"] == "accepted"


    alerts_resp = client.get("/api/v1/alerts?severity=CRITICAL")
    assert alerts_resp.status_code == 200
    alerts_data = alerts_resp.json()
    assert alerts_data["total"] == 1
    alert = alerts_data["alerts"][0]
    assert alert["rule_code"] == "LARGE_TRANSACTION"
    assert alert["severity"] == "CRITICAL"
    assert alert["status"] == "OPEN"
    alert_id = alert["id"]


    patch_ack = client.patch(f"/api/v1/alerts/{alert_id}", json={"status": "ACKNOWLEDGED"})
    assert patch_ack.status_code == 200
    assert patch_ack.json()["status"] == "ACKNOWLEDGED"

    patch_resolve = client.patch(f"/api/v1/alerts/{alert_id}", json={"status": "RESOLVED"})
    assert patch_resolve.status_code == 200
    assert patch_resolve.json()["status"] == "RESOLVED"
    assert patch_resolve.json()["resolved_at"] is not None


    final_stats = client.get("/api/v1/alerts/stats").json()
    assert final_stats["total_alerts"] == 1
    assert final_stats["open_alerts"] == 0
