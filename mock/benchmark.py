from __future__ import annotations

import random
import time
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from backend.app.detection.engine import AnomalyDetectionEngine


def run_benchmark():
    engine = AnomalyDetectionEngine()
    total_evals = 1000


    start = time.perf_counter()
    for i in range(total_evals):
        txn = {
            "event_id": f"bench_{i}",
            "transaction_id": f"txn_{i}",
            "amount": 100.0 + (i % 50),
            "currency": "USD",
            "gateway": "stripe",
            "status": "succeeded",
            "occurred_at": datetime.now(UTC),
        }
        history = {
            "gateway_transactions": [],
            "txn_id_transactions": [],
            "gateway_current_count": 10,
            "gateway_baseline_count": 10,
        }
        engine.evaluate(txn, history)
    duration = time.perf_counter() - start
    avg_latency_ms = (duration / total_evals) * 1000
    ops_per_sec = total_evals / duration

    print("=== RevenueGuard Benchmark Results ===")
    print(f"Total Evaluations: {total_evals}")
    print(f"Total Duration:    {duration:.4f}s")
    print(f"Average Latency:   {avg_latency_ms:.4f} ms/eval")
    print(f"Throughput:        {ops_per_sec:.2f} evals/sec\n")



    rng = random.Random(42)
    labeled_samples = []


    for i in range(100):
        labeled_samples.append(({
            "event_id": f"norm_{i}",
            "transaction_id": f"txn_norm_{i}",
            "amount": rng.randint(10, 4500),
            "currency": "USD",
            "gateway": "stripe",
            "status": "succeeded",
            "occurred_at": datetime.now(UTC),
        }, False))


    for i in range(50):
        labeled_samples.append(({
            "event_id": f"anom_{i}",
            "transaction_id": f"txn_anom_{i}",
            "amount": rng.randint(6000, 50000),
            "currency": "USD",
            "gateway": "stripe",
            "status": "succeeded",
            "occurred_at": datetime.now(UTC),
        }, True))

    tp = fp = tn = fn = 0
    for txn, true_label in labeled_samples:
        res = engine.evaluate(txn, {"gateway_current_count": 10, "gateway_baseline_count": 10})
        pred_label = res.has_anomalies
        if pred_label and true_label:
            tp += 1
        elif pred_label and not true_label:
            fp += 1
        elif not pred_label and not true_label:
            tn += 1
        elif not pred_label and true_label:
            fn += 1

    precision = tp / (tp + fp) if (tp + fp) > 0 else 0.0
    recall = tp / (tp + fn) if (tp + fn) > 0 else 0.0
    fpr = fp / (fp + tn) if (fp + tn) > 0 else 0.0

    print("=== Quality Metrics (Synthetic Labeled Set) ===")
    print(f"True Positives:  {tp}")
    print(f"False Positives: {fp}")
    print(f"True Negatives:  {tn}")
    print(f"False Negatives: {fn}")
    print(f"Precision:       {precision:.2%}")
    print(f"Recall:          {recall:.2%}")
    print(f"FPR:             {fpr:.2%}")


if __name__ == "__main__":
    run_benchmark()
