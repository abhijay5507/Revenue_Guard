import argparse
import hashlib
import hmac
import json
import os
import random
import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import httpx


def make_event(index: int) -> dict:
    status = random.choices(["succeeded", "failed", "pending"], [0.78, 0.14, 0.08])[0]
    event = {
        "event_id": f"evt_{uuid.uuid4().hex}",
        "transaction_id": f"txn_{uuid.uuid4().hex[:16]}",
        "event_type": f"payment.{status}",
        "occurred_at": (datetime.now(timezone.utc) - timedelta(minutes=random.randint(0, 10000))).isoformat(),
        "amount": str(Decimal(random.randint(100, 500000)) / 100),
        "currency": random.choice(["USD", "EUR", "GBP", "INR"]),
        "status": status,
        "gateway": random.choice(["stripe", "paypal", "freemius", "adyen"]),
        "browser": random.choice(["Chrome", "Firefox", "Safari", "Edge"]),
        "country": random.choice(["US", "GB", "DE", "IN", "CA"]),
        "error_code": random.choice(["card_declined", "insufficient_funds", None]) if status == "failed" else None,
        "sequence": index,
    }
    return event


def main() -> None:
    parser = argparse.ArgumentParser(description="Send synthetic RevenueGuard payment events")
    parser.add_argument("--endpoint", default=os.getenv("REVENUEGUARD_ENDPOINT", "http://127.0.0.1:8000/api/v1/webhooks/payment"))
    parser.add_argument("--count", type=int, default=int(os.getenv("REVENUEGUARD_EVENT_COUNT", "10")))
    args = parser.parse_args()
    secret = os.getenv("WEBHOOK_SECRET")
    if not secret:
        parser.error("Set WEBHOOK_SECRET in the environment before sending events")
    if args.count < 1:
        parser.error("--count must be at least 1")

    with httpx.Client(timeout=10) as client:
        for index in range(args.count):
            body = json.dumps(make_event(index), separators=(",", ":")).encode("utf-8")
            signature = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
            try:
                response = client.post(args.endpoint, content=body, headers={"Content-Type": "application/json", "X-RevenueGuard-Signature": signature})
                print(f"event {index + 1}/{args.count}: HTTP {response.status_code} {response.text[:180]}")
            except httpx.HTTPError as exc:
                print(f"event {index + 1}/{args.count}: request failed ({type(exc).__name__})")


if __name__ == "__main__":
    main()
