"""
Recovery Ledger — Thread-safe JSON persistence for recovered payments.

Every time a Razorpay webhook confirms a payment, we write to this ledger.
It's the single source of truth for "what money actually came back."

No database needed — JSON file is sufficient for hackathon scale.
Uses a file lock so concurrent webhook events don't corrupt data.
"""

import os
import json
import threading
from datetime import datetime, timezone

# ─── Ledger file location ─────────────────────────────────────────────────────
_BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LEDGER_PATH = os.path.join(_BASE, "data", "recovery_ledger.json")

# Thread lock — Flask handles concurrent requests
_lock = threading.Lock()


# ─── Default structure ────────────────────────────────────────────────────────

def _empty_ledger() -> dict:
    return {
        "total_recovered_inr": 0.0,
        "total_recovered_count": 0,
        "payments": [],           # list of confirmed payment dicts
        "last_updated": None,
    }


# ─── Read / Write ─────────────────────────────────────────────────────────────

def _load() -> dict:
    if not os.path.exists(LEDGER_PATH):
        return _empty_ledger()
    try:
        with open(LEDGER_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return _empty_ledger()


def _save(ledger: dict) -> None:
    os.makedirs(os.path.dirname(LEDGER_PATH), exist_ok=True)
    with open(LEDGER_PATH, "w", encoding="utf-8") as f:
        json.dump(ledger, f, indent=2, ensure_ascii=False)


# ─── Public API ───────────────────────────────────────────────────────────────

def record_payment(
    payment_id: str,
    order_id: str,
    amount_paise: int,
    event: str,
    raw_payload: dict,
) -> dict:
    """
    Record a confirmed payment from a Razorpay webhook.

    Returns the updated ledger summary.
    Idempotent — duplicate payment_ids are silently ignored.
    """
    amount_inr = round(amount_paise / 100, 2)
    now = datetime.now(timezone.utc).isoformat()

    with _lock:
        ledger = _load()

        # Idempotency: skip if already recorded
        existing_ids = {p["payment_id"] for p in ledger["payments"]}
        if payment_id in existing_ids:
            print(f"  [Ledger] Duplicate webhook ignored: {payment_id}")
            return get_stats()

        entry = {
            "payment_id":   payment_id,
            "order_id":     order_id,
            "amount_inr":   amount_inr,
            "amount_paise": amount_paise,
            "event":        event,
            "recorded_at":  now,
        }

        ledger["payments"].append(entry)
        ledger["total_recovered_count"] += 1
        ledger["total_recovered_inr"]   = round(
            ledger["total_recovered_inr"] + amount_inr, 2
        )
        ledger["last_updated"] = now

        _save(ledger)
        print(
            f"  [Ledger] RECOVERED: {payment_id} | "
            f"Rs.{amount_inr:,.2f} | "
            f"Total: Rs.{ledger['total_recovered_inr']:,.2f} "
            f"({ledger['total_recovered_count']} payments)"
        )
        return {
            "total_recovered_inr":   ledger["total_recovered_inr"],
            "total_recovered_count": ledger["total_recovered_count"],
        }


def get_stats() -> dict:
    """Return current recovery stats (read-only, no lock needed for reads)."""
    ledger = _load()
    payments = ledger.get("payments", [])

    # Calculate recovery rate vs simulated pipeline
    SIMULATED_AT_RISK_INR = 191_350.09  # from pipeline run

    recovered = ledger.get("total_recovered_inr", 0.0)
    count     = ledger.get("total_recovered_count", 0)
    rate      = round((recovered / SIMULATED_AT_RISK_INR) * 100, 2) if recovered > 0 else 0.0

    return {
        "total_recovered_inr":    recovered,
        "total_recovered_count":  count,
        "recovery_rate_pct":      rate,
        "at_risk_inr":            SIMULATED_AT_RISK_INR,
        "last_updated":           ledger.get("last_updated"),
        "recent_payments":        payments[-5:][::-1],  # last 5, newest first
    }


def reset_ledger() -> None:
    """Reset for testing. Remove in production."""
    with _lock:
        _save(_empty_ledger())
    print("  [Ledger] Reset complete.")
