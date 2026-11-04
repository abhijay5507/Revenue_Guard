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




GATEWAYS = ["stripe", "paypal", "freemius", "adyen"]
CURRENCIES = ["USD", "EUR", "GBP", "INR"]
BROWSERS = ["Chrome", "Firefox", "Safari", "Edge"]
COUNTRIES = ["US", "GB", "DE", "IN", "CA"]
ERROR_CODES = ["card_declined", "insufficient_funds", "expired_card", "processing_error"]


NORMAL_STATUS_WEIGHTS = [0.78, 0.14, 0.08]


def _make_event_id() -> str:
    return f"evt_{uuid.uuid4().hex}"


def _make_txn_id() -> str:
    return f"txn_{uuid.uuid4().hex[:16]}"


def _finalize_events(
    events: list[dict[str, Any]], rng: random.Random, scenario: str
) -> list[dict[str, Any]]:

    transaction_ids: dict[str, str] = {}
    for event in events:
        event["event_id"] = f"evt_mock_{rng.getrandbits(128):032x}"
        original_transaction_id = event["transaction_id"]
        if original_transaction_id not in transaction_ids:
            transaction_ids[original_transaction_id] = f"txn_mock_{rng.getrandbits(64):016x}"
        event["transaction_id"] = transaction_ids[original_transaction_id]
        event.setdefault("scenario", scenario)
    return events


def _normal_event(
    rng: random.Random,
    base_time: datetime,
    offset_minutes: int = 0,
) -> dict[str, Any]:

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

    currency = rng.choice(CURRENCIES)

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





    events = []
    for i in range(count):

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
    anomaly_rate: float = 0.0,
) -> list[dict[str, Any]]:












    if scenario == "normal":
        events = []
        for _ in range(count):
            anomalous = rng.random() < anomaly_rate
            event = (
                _large_payment_event(rng, base_time)
                if anomalous
                else _normal_event(rng, base_time, offset_minutes=rng.randint(0, 600))
            )
            event["scenario"] = "large_payment" if anomalous else "normal"
            events.append(event)
        return _finalize_events(events, rng, scenario)

    if scenario == "large_payment":
        events = [
            _normal_event(rng, base_time, offset_minutes=rng.randint(0, 600))
            for _ in range(max(1, count - 3))
        ]
        for _ in range(min(3, count)):
            events.append(_large_payment_event(rng, base_time))
        rng.shuffle(events)
        return _finalize_events(events, rng, scenario)

    if scenario == "failure_burst":

        normal_count = max(1, count // 3)
        burst_count = count - normal_count
        events = [
            _normal_event(rng, base_time, offset_minutes=rng.randint(10, 600))
            for _ in range(normal_count)
        ]
        events.extend(_failure_burst_events(rng, base_time, burst_count))
        return _finalize_events(events, rng, scenario)

    if scenario == "volume_spike":
        normal_count = max(1, count // 3)
        spike_count = count - normal_count
        events = [
            _normal_event(rng, base_time, offset_minutes=rng.randint(60, 600))
            for _ in range(normal_count)
        ]
        events.extend(_volume_spike_events(rng, base_time, spike_count))
        return _finalize_events(events, rng, scenario)

    if scenario == "repeated_failures":
        normal_count = max(1, count - 5)
        events = [
            _normal_event(rng, base_time, offset_minutes=rng.randint(0, 600))
            for _ in range(normal_count)
        ]
        events.extend(_repeated_failure_events(rng, base_time, min(5, count)))
        rng.shuffle(events)
        return _finalize_events(events, rng, scenario)

    if scenario == "mixed":

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
        return _finalize_events(events[:count], rng, scenario)

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
        "--anomaly-rate",
        type=float,
        default=0.0,
        help="For --scenario normal, fraction of generated events that are large payments (0-1)",
    )
    parser.add_argument(
        "--delay",
        type=float,
        default=0.0,
        help="Delay in seconds between events (generation rate is 1/delay per second)",
    )
    parser.add_argument(
        "--rate",
        type=float,
        default=None,
        help="Target events per second (overrides --delay)",
    )
    args = parser.parse_args()

    secret = os.getenv("WEBHOOK_SECRET")
    if not secret:
        parser.error("Set WEBHOOK_SECRET in the environment before sending events")
    if args.count < 1:
        parser.error("--count must be at least 1")
    if args.rate is not None and args.rate <= 0:
        parser.error("--rate must be greater than zero")
    if not 0 <= args.anomaly_rate <= 1:
        parser.error("--anomaly-rate must be between 0 and 1")

    rng = random.Random(args.seed)
    base_time = datetime.now(UTC)
    events = generate_scenario(
        args.scenario, rng, args.count, base_time, anomaly_rate=args.anomaly_rate
    )

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

            delay = 1 / args.rate if args.rate is not None else args.delay
            if delay > 0:
                time.sleep(delay)

    print("Done.")


if __name__ == "__main__":
    main()
