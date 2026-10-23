import hashlib
import hmac
import json
from datetime import datetime, timezone

from sqlalchemy import func, select

from backend.app.models import Transaction
from backend.app.services.transactions import TransactionStoreError, persist_event
from backend.app.schemas.payment_event import PaymentEvent


def signed(payload: dict) -> tuple[bytes, str]:
    body = json.dumps(payload, separators=(",", ":")).encode()
    return body, hmac.new(b"test-secret", body, hashlib.sha256).hexdigest()


def event_payload(event_id="evt_test"):
    return {
        "event_id": event_id,
        "transaction_id": "txn_1",
        "event_type": "payment.succeeded",
        "occurred_at": datetime.now(timezone.utc).isoformat(),
        "amount": "12.3400",
        "currency": "USD",
        "status": "succeeded",
        "gateway": "stripe",
        "browser": "Firefox",
        "country": "US",
    }


def test_health_and_db_health(client, monkeypatch):
    assert client.get("/health").json()["status"] == "ok"
    monkeypatch.setattr("backend.app.main.test_database_connection", lambda: True)
    assert client.get("/health/db").json()["database"] == "connected"


def test_metadata_and_signed_webhook(client):
    assert "transactions" in Transaction.metadata.tables
    body, signature = signed(event_payload())
    response = client.post("/api/v1/webhooks/payment", content=body, headers={"X-RevenueGuard-Signature": signature})
    assert response.status_code == 200
    assert response.json()["status"] == "accepted"


def test_signed_webhook_accepts_provider_extra_fields(client):
    payload = event_payload("evt_extra_field")
    payload["sequence"] = 0
    body, signature = signed(payload)
    response = client.post(
        "/api/v1/webhooks/payment",
        content=body,
        headers={"X-RevenueGuard-Signature": signature},
    )
    assert response.status_code == 200
    assert response.json()["status"] == "accepted"


def test_missing_or_invalid_signature_rejected(client):
    body, _ = signed(event_payload())
    assert client.post("/api/v1/webhooks/payment", content=body).status_code == 401
    assert client.post("/api/v1/webhooks/payment", content=body, headers={"X-RevenueGuard-Signature": "bad"}).status_code == 401


def test_invalid_payload_rejected(client):
    payload = event_payload()
    payload["amount"] = "not-a-number"
    body, signature = signed(payload)
    assert client.post("/api/v1/webhooks/payment", content=body, headers={"X-RevenueGuard-Signature": signature}).status_code == 422


def test_duplicate_event_is_idempotent(client):
    body, signature = signed(event_payload())
    headers = {"X-RevenueGuard-Signature": signature}
    assert client.post("/api/v1/webhooks/payment", content=body, headers=headers).json()["status"] == "accepted"
    duplicate = client.post("/api/v1/webhooks/payment", content=body, headers=headers)
    assert duplicate.status_code == 200
    assert duplicate.json()["status"] == "already_received"
    with client.testing_sessions() as db:
        assert db.scalar(select(func.count()).select_from(Transaction)) == 1


def test_queue_failure_is_reported_after_persistence(client, monkeypatch):
    monkeypatch.setattr(
        "backend.app.api.routes.webhooks.process_payment_event.delay",
        lambda event_id: (_ for _ in ()).throw(RuntimeError("queue unavailable")),
    )
    body, signature = signed(event_payload("evt_queue_failure"))
    response = client.post(
        "/api/v1/webhooks/payment",
        content=body,
        headers={"X-RevenueGuard-Signature": signature},
    )
    assert response.status_code == 503
    assert response.json()["detail"] == "Event was saved but downstream queue is unavailable"


def test_service_reports_database_failure():
    payload = event_payload()
    event = PaymentEvent.model_validate(payload)

    class BrokenSession:
        def add(self, item):
            pass

        def commit(self):
            from sqlalchemy.exc import OperationalError
            raise OperationalError("insert", {}, Exception("database unavailable"))

        def rollback(self):
            pass

    try:
        persist_event(BrokenSession(), event, payload)  # type: ignore[arg-type]
        assert False, "expected TransactionStoreError"
    except TransactionStoreError:
        pass
