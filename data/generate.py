"""
Chunk 1 — Synthetic failed payment data generator.
Generates 100 realistic failed payment records using real Razorpay error codes.
"""

import sys
import os

# Fix Windows console encoding
if sys.stdout.encoding != 'utf-8':
    sys.stdout.reconfigure(encoding='utf-8')
if sys.stderr.encoding != 'utf-8':
    sys.stderr.reconfigure(encoding='utf-8')

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import random
import math
import json
from datetime import datetime, timedelta, timezone
from schemas.models import (
    FailureRecord, CustomerHistory, ERROR_CODES, PAYMENT_METHODS,
    ERROR_SOURCE_MAP, validate_failure_record, save_json
)

# ─── Distribution table (must sum to 100) ────────────────────────────────────

ERROR_DISTRIBUTION = {
    "insufficient_funds": 18,
    "card_declined": 15,
    "payment_timed_out": 12,
    "gateway_technical_error": 10,
    "authentication_failed": 9,
    "payment_cancelled": 8,
    "bank_technical_error": 6,
    "payment_failed": 5,
    "card_not_enrolled": 4,
    "incorrect_cvv": 3,
    "card_disabled_for_online_payments": 2,
    "card_expired": 2,
    "debit_instrument_inactive": 2,
    "payment_risk_check_failed": 2,
    "debit_instrument_blocked": 1,
    "transaction_limit_exceeded": 1,
}

# Card-only error codes — these MUST use payment_method="card"
CARD_ONLY_ERRORS = {
    "card_declined", "card_not_enrolled", "card_disabled_for_online_payments",
    "card_expired", "incorrect_cvv", "debit_instrument_inactive",
    "debit_instrument_blocked",
}

# Methods allowed per non-card-only error
MULTI_METHOD_ERRORS = {
    "payment_timed_out": ["card", "upi", "netbanking"],
    "gateway_technical_error": ["card", "upi", "netbanking"],
    "payment_cancelled": ["card", "upi", "netbanking", "wallet"],
    "insufficient_funds": ["card", "upi"],
    "bank_technical_error": ["card", "netbanking"],
    "authentication_failed": ["card", "netbanking"],
    "payment_risk_check_failed": ["card", "upi"],
    "payment_failed": ["card", "upi"],
    "transaction_limit_exceeded": ["card", "upi"],
}


def _generate_amount(rng: random.Random) -> float:
    """Log-normal distribution centered around ₹1,500. Range ₹100–₹50,000."""
    # mu and sigma for log-normal with median ~1500
    mu = math.log(1500)
    sigma = 0.8
    amount = rng.lognormvariate(mu, sigma)
    amount = max(100.0, min(50000.0, amount))
    return round(amount, 2)


def _generate_customer_pool(rng: random.Random, n_customers: int = 30) -> list[dict]:
    """Generate a pool of ~30 unique customers with varied histories."""
    customers = []
    for i in range(1, n_customers + 1):
        cid = f"cust_{i:05d}"
        months = rng.randint(0, 60)

        # 3-4 customers are chronic failers
        if i <= 4:
            past_fail = rng.randint(6, 18)
            past_success = rng.randint(0, 5)
        else:
            past_success = rng.randint(0, 100)
            past_fail = rng.randint(0, 5)

        customers.append({
            "customer_id": cid,
            "customer_history": CustomerHistory(
                months_as_customer=months,
                past_success_count=past_success,
                past_failure_count=past_fail,
            )
        })
    return customers


def _generate_timestamp(rng: random.Random) -> str:
    """Random timestamp on 2026-09-05 between 09:00–23:00 IST."""
    ist = timezone(timedelta(hours=5, minutes=30))
    base = datetime(2026, 9, 5, 9, 0, 0, tzinfo=ist)
    # 14 hours window (09:00 to 23:00)
    offset_seconds = rng.randint(0, 14 * 3600)
    ts = base + timedelta(seconds=offset_seconds)
    return ts.isoformat()


def generate_failed_payments(n: int = 100, seed: int = 42) -> list[dict]:
    """Generate n synthetic failed payment records."""
    rng = random.Random(seed)

    # Build the flat list of error codes according to distribution
    error_list = []
    for code, count in ERROR_DISTRIBUTION.items():
        error_list.extend([code] * count)

    assert len(error_list) == n, f"Distribution sums to {len(error_list)}, expected {n}"
    rng.shuffle(error_list)

    # Customer pool
    customers = _generate_customer_pool(rng, n_customers=30)

    records = []
    for i, error_code in enumerate(error_list, start=1):
        # Pick customer (some repeat)
        customer = rng.choice(customers)

        # Payment method
        if error_code in CARD_ONLY_ERRORS:
            method = "card"
        else:
            method = rng.choice(MULTI_METHOD_ERRORS.get(error_code, ["card"]))

        record = FailureRecord(
            payment_id=f"pay_{i:05d}",
            amount_inr=_generate_amount(rng),
            error_code=error_code,
            error_source=ERROR_SOURCE_MAP[error_code],
            payment_method=method,
            customer_id=customer["customer_id"],
            customer_history=customer["customer_history"],
            timestamp=_generate_timestamp(rng),
        )
        records.append(record.to_dict())

    # Validate all records
    validation_errors = []
    for r in records:
        errs = validate_failure_record(r)
        if errs:
            validation_errors.append((r["payment_id"], errs))

    if validation_errors:
        print("❌ VALIDATION ERRORS:")
        for pid, errs in validation_errors:
            print(f"  {pid}: {errs}")
        raise ValueError(f"{len(validation_errors)} records failed validation")

    # Print distribution summary
    print(f"\n  Generated {len(records)} failed payment records")
    print(f"  Amount range: ₹{min(r['amount_inr'] for r in records):,.2f} — ₹{max(r['amount_inr'] for r in records):,.2f}")
    print(f"  Total at risk: ₹{sum(r['amount_inr'] for r in records):,.2f}")
    print(f"  Unique customers: {len(set(r['customer_id'] for r in records))}")
    print(f"\n  Distribution:")
    dist = {}
    for r in records:
        dist[r["error_code"]] = dist.get(r["error_code"], 0) + 1
    for code, count in sorted(dist.items(), key=lambda x: -x[1]):
        expected = ERROR_DISTRIBUTION[code]
        status = "✓" if count == expected else "✗"
        print(f"    {status} {code}: {count} (expected {expected})")

    # Save
    output_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "failed_payments.json")
    save_json(records, output_path)

    return records


if __name__ == "__main__":
    print("=" * 60)
    print("  CHUNK 1 — Synthetic Data Generator")
    print("=" * 60)
    generate_failed_payments(n=100, seed=42)
    print("\n  Done.")
