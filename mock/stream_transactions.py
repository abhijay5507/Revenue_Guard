from __future__ import annotations

import argparse
import hashlib
import hmac
import json
import os
import random
import sys
import time
import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

import httpx

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
GATEWAYS = ["stripe", "paypal", "freemius", "adyen"]
CURRENCIES = ["USD", "EUR", "GBP", "INR"]
BROWSERS = ["Chrome", "Firefox", "Safari", "Edge"]
COUNTRIES = ["US", "GB", "DE", "IN", "CA"]
ERROR_CODES = ["card_declined", "insufficient_funds", "expired_card", "processing_error"]

# Normal traffic distribution
NORMAL_STATUS_WEIGHTS = [0.78, 0.14, 0.08]  # succeeded, failed, pending


def _make_event_id() -> str:
    return f"evt_{uuid.uuid4().hex}"


def _make_txn_id() -> str:
    return f"txn_{uuid.uuid4().hex[:16]}"


def _normal_event(
    rng: random.Random,
    base_time: datetime,
    offset_minutes: int = 0,
) -> dict[str, Any]:
    """Generate a normal payment event."""
    status = rng.choices(["succeeded", "failed", "pending"], NORMAL_STATUS_WEIGHTS)[0]
    return {
        "event_id": _make_event_id(),
        "transaction_id": _make_txn_id(),
        "event_type": f"payment.{status}",
        "occurred_at": (base_time - timedelta(minutes=offset_minutes)).isoformat(),
        "amount": str(Decimal(rng.randint(100, 50000)) / 100),
        "currency": rng.choice(CURRENCIES),
        "status": status,
        "gateway": rng.choice(GATEWAYS),
        "browser": rng.choice(BROWSERS),
        "country": rng.choice(COUNTRIES),
        "error_code": rng.choice(ERROR_CODES) if status == "failed" else None,
    }


def _large_payment_event(
    rng: random.Random,
    base_time: datetime,
    offset_minutes: int = 0,
) -> dict[str, Any]:
    """Generate a large payment that should trigger LARGE_TRANSACTION rule."""
    currency = rng.choice(CURRENCIES)
    # Amounts well above default thresholds
    large_amounts = {
        "USD": rng.randint(600000, 5000000),
        "EUR": rng.randint(500000, 4000000),
        "GBP": rng.randint(500000, 3500000),
        "INR": rng.randint(50000000, 200000000),
    }
    return {
        "event_id": _make_event_id(),
        "transaction_id": _make_txn_id(),
        "event_type": "payment.succeeded",
        "occurred_at": (base_time - timedelta(minutes=offset_minutes)).isoformat(),
        "amount": str(Decimal(large_amounts[currency]) / 100),
        "currency": currency,
        "status": "succeeded",
        "gateway": rng.choice(GATEWAYS),
        "browser": rng.choice(BROWSERS),
        "country": rng.choice(COUNTRIES),
        "error_code": None,
    }


def _failure_burst_events(
    rng: random.Random,
    base_time: datetime,
    count: int = 15,
    gateway: str = "stripe",
) -> list[dict[str, Any]]:
    """Generate a burst of payment failures on a single gateway.

    Creates enough failures (with a few successes) to trigger
    FAILURE_SPIKE rule.
    """
    events = []
    for i in range(count):
        # ~85% failure rate
        status = "failed" if rng.random() < 0.85 else "succeeded"
        events.append({
            "event_id": _make_event_id(),
            "transaction_id": _make_txn_id(),
            "event_type": f"payment.{status}",
            "occurred_at": (base_time - timedelta(seconds=rng.randint(0, 300))).isoformat(),
            "amount": str(Decimal(rng.randint(500, 20000)) / 100),
            "currency": rng.choice(CURRENCIES),
            "status": status,
            "gateway": gateway,
            "browser": rng.choice(BROWSERS),
            "country": rng.choice(COUNTRIES),
            "error_code": rng.choice(ERROR_CODES) if status == "failed" else None,
        })
    return events


def _volume_spike_events(
    rng: random.Random,
    base_time: datetime,
    count: int = 25,
    gateway: str = "paypal",
) -> list[dict[str, Any]]:
    """Generate a volume spike on a single gateway.

    Creates many transactions in a short window to trigger VOLUME_SPIKE.
    """
    events = []
    for i in range(count):
        status = rng.choices(["succeeded", "failed", "pending"], NORMAL_STATUS_WEIGHTS)[0]
        events.append({
            "event_id": _make_event_id(),
            "transaction_id": _make_txn_id(),
            "event_type": f"payment.{status}",
            "occurred_at": (base_time - timedelta(seconds=rng.randint(0, 120))).isoformat(),
            "amount": str(Decimal(rng.randint(100, 50000)) / 100),
            "currency": rng.choice(CURRENCIES),
            "status": status,
            "gateway": gateway,
            "browser": rng.choice(BROWSERS),
            "country": rng.choice(COUNTRIES),
            "error_code": rng.choice(ERROR_CODES) if status == "failed" else None,
        })
    return events


def _repeated_failure_events(
    rng: random.Random,
    base_time: datetime,
    count: int = 5,
) -> list[dict[str, Any]]:
    """Generate repeated failures for the same transaction_id.

    Triggers REPEATED_PAYMENT_FAILURES rule.
    """
    txn_id = _make_txn_id()
    events = []
    for i in range(count):
        events.append({
            "event_id": _make_event_id(),
            "transaction_id": txn_id,
            "event_type": "payment.failed",
            "occurred_at": (base_time - timedelta(minutes=rng.randint(0, 30))).isoformat(),
            "amount": str(Decimal(rng.randint(500, 20000)) / 100),
            "currency": rng.choice(CURRENCIES),
            "status": "failed",
            "gateway": rng.choice(GATEWAYS),
            "browser": rng.choice(BROWSERS),
            "country": rng.choice(COUNTRIES),
            "error_code": rng.choice(ERROR_CODES),
        })
    return events


def generate_scenario(
    scenario: str,
    rng: random.Random,
    count: int,
    base_time: datetime,
) -> list[dict[str, Any]]:
    """Generate events for a specific anomaly scenario.

    Args:
        scenario: One of 'normal', 'large_payment', 'failure_burst',
                  'volume_spike', 'repeated_failures', 'mixed'.
        rng: Seeded random number generator.
        count: Number of events to generate.
        base_time: Reference timestamp.

    Returns:
        List of payment event dicts.
    """
    if scenario == "normal":
        return [
            _normal_event(rng, base_time, offset_minutes=rng.randint(0, 600))
            for _ in range(count)
        ]

    if scenario == "large_payment":
        events = [
            _normal_event(rng, base_time, offset_minutes=rng.randint(0, 600))
            for _ in range(max(1, count - 3))
        ]
        for _ in range(min(3, count)):
            events.append(_large_payment_event(rng, base_time))
        rng.shuffle(events)
        return events

    if scenario == "failure_burst":
        # Some normal traffic, then a failure burst
        normal_count = max(1, count // 3)
        burst_count = count - normal_count
        events = [
            _normal_event(rng, base_time, offset_minutes=rng.randint(10, 600))
            for _ in range(normal_count)
        ]
        events.extend(_failure_burst_events(rng, base_time, burst_count))
        return events

    if scenario == "volume_spike":
        normal_count = max(1, count // 3)
        spike_count = count - normal_count
        events = [
            _normal_event(rng, base_time, offset_minutes=rng.randint(60, 600))
            for _ in range(normal_count)
        ]
        events.extend(_volume_spike_events(rng, base_time, spike_count))
        return events

    if scenario == "repeated_failures":
        normal_count = max(1, count - 5)
        events = [
            _normal_event(rng, base_time, offset_minutes=rng.randint(0, 600))
            for _ in range(normal_count)
        ]
        events.extend(_repeated_failure_events(rng, base_time, min(5, count)))
        rng.shuffle(events)
        return events

    if scenario == "mixed":
        # Generate a mix of all anomaly types
        per_scenario = max(3, count // 5)
        events: list[dict[str, Any]] = []
        events.extend([
            _normal_event(rng, base_time, offset_minutes=rng.randint(0, 600))
            for _ in range(per_scenario)
        ])
        events.extend([
            _large_payment_event(rng, base_time) for _ in range(max(1, per_scenario // 3))
        ])
        events.extend(_failure_burst_events(rng, base_time, per_scenario))
        events.extend(_volume_spike_events(rng, base_time, per_scenario))
        events.extend(_repeated_failure_events(rng, base_time, max(3, per_scenario // 2)))
        rng.shuffle(events)
        return events[:count]

    raise ValueError(f"Unknown scenario: {scenario}")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Send synthetic RevenueGuard payment events"
    )
    parser.add_argument(
        "--endpoint",
        default=os.getenv(
            "REVENUEGUARD_ENDPOINT",
            "http://127.0.0.1:8000/api/v1/webhooks/payment",
        ),
    )
    parser.add_argument(
        "--count",
        type=int,
        default=int(os.getenv("REVENUEGUARD_EVENT_COUNT", "10")),
    )
    parser.add_argument(
        "--scenario",
        choices=["normal", "large_payment", "failure_burst",
                 "volume_spike", "repeated_failures", "mixed"],
        default="normal",
        help="Anomaly scenario to generate",
    )
    parser.add_argument(
        "--seed",
        type=int,
        default=None,
        help="Random seed for reproducible scenarios",
    )
    parser.add_argument(
        "--delay",
        type=float,
        default=0.0,
        help="Delay in seconds between events",
    )
    args = parser.parse_args()

    secret = os.getenv("WEBHOOK_SECRET")
    if not secret:
        parser.error("Set WEBHOOK_SECRET in the environment before sending events")
    if args.count < 1:
        parser.error("--count must be at least 1")

    rng = random.Random(args.seed)
    base_time = datetime.now(UTC)
    events = generate_scenario(args.scenario, rng, args.count, base_time)

    print(f"Sending {len(events)} events (scenario={args.scenario}, seed={args.seed})")

    with httpx.Client(timeout=10) as client:
        for index, event in enumerate(events):
            body = json.dumps(event, separators=(",", ":")).encode("utf-8")
            signature = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
            try:
                response = client.post(
                    args.endpoint,
                    content=body,
                    headers={
                        "Content-Type": "application/json",
                        "X-RevenueGuard-Signature": signature,
                    },
                )
                status_text = response.json().get("status", "?")
                print(
                    f"  [{index + 1}/{len(events)}] "
                    f"HTTP {response.status_code} — {status_text} "
                    f"({event['status']}, {event['gateway']}, "
                    f"{event['currency']} {event['amount']})"
                )
            except httpx.HTTPError as exc:
                print(
                    f"  [{index + 1}/{len(events)}] "
                    f"request failed ({type(exc).__name__})"
                )

            if args.delay > 0:
                time.sleep(args.delay)

    print("Done.")


if __name__ == "__main__":
    main()
