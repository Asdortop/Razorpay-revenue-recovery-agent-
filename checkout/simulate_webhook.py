"""
Webhook Simulator — Local Testing Tool

Sends a fake Razorpay payment.captured event to your local webhook endpoint.
Use this to test the full webhook flow WITHOUT ngrok or internet exposure.

Usage:
    python checkout/simulate_webhook.py                    # Rs.500 default
    python checkout/simulate_webhook.py --amount 1500      # Rs.1500
    python checkout/simulate_webhook.py --count 5          # 5 payments
    python checkout/simulate_webhook.py --reset            # Reset ledger

This script generates a valid HMAC-SHA256 signature, so the endpoint
treats it exactly like a real Razorpay event.
"""

import os
import sys
import hmac
import hashlib
import json
import time
import uuid
import argparse
import requests
from dotenv import load_dotenv

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
load_dotenv()

WEBHOOK_URL     = "http://localhost:5000/webhook/razorpay"
WEBHOOK_SECRET  = os.environ.get("RAZORPAY_WEBHOOK_SECRET", "")
STATS_URL       = "http://localhost:5000/api/recovery-stats"


def make_payload(amount_paise: int, payment_id: str, order_id: str) -> dict:
    """Build a Razorpay payment.captured webhook payload."""
    return {
        "entity":    "event",
        "account_id": "acc_test_revive",
        "event":     "payment.captured",
        "contains":  ["payment"],
        "payload": {
            "payment": {
                "entity": {
                    "id":          payment_id,
                    "entity":      "payment",
                    "amount":      amount_paise,
                    "currency":    "INR",
                    "status":      "captured",
                    "order_id":    order_id,
                    "method":      "card",
                    "captured":    True,
                    "description": "Simulated recovery payment",
                    "created_at":  int(time.time()),
                }
            }
        },
        "created_at": int(time.time()),
    }


def sign_payload(body_bytes: bytes) -> str:
    """Generate HMAC-SHA256 signature like Razorpay does."""
    if not WEBHOOK_SECRET:
        return "no-secret-set"
    return hmac.new(
        WEBHOOK_SECRET.encode("utf-8"),
        msg=body_bytes,
        digestmod=hashlib.sha256,
    ).hexdigest()


def send_webhook(amount_paise: int, idx: int = 1) -> bool:
    payment_id = f"pay_SIM{uuid.uuid4().hex[:14].upper()}"
    order_id   = f"order_SIM{uuid.uuid4().hex[:12].upper()}"
    amount_inr = amount_paise / 100

    payload     = make_payload(amount_paise, payment_id, order_id)
    body_bytes  = json.dumps(payload).encode("utf-8")
    signature   = sign_payload(body_bytes)

    print(f"\n  [{idx}] Sending webhook: {payment_id} | Rs.{amount_inr:,.2f}")

    try:
        res = requests.post(
            WEBHOOK_URL,
            data=body_bytes,
            headers={
                "Content-Type":         "application/json",
                "X-Razorpay-Signature": signature,
            },
            timeout=10,
        )
        if res.status_code == 200:
            print(f"      Response: {res.status_code} OK -- {res.json().get('message','')}")
            return True
        else:
            print(f"      Response: {res.status_code} ERROR -- {res.text[:200]}")
            return False
    except requests.ConnectionError:
        print("      ERROR: Could not connect to localhost:5000")
        print("      Make sure Flask server is running: python checkout/app.py")
        return False
    except Exception as e:
        print(f"      ERROR: {e}")
        return False


def show_stats():
    try:
        res = requests.get(STATS_URL, timeout=5)
        if res.ok:
            data = res.json()
            print("\n  === Live Recovery Stats ===")
            print(f"  Total recovered : Rs.{data['total_recovered_inr']:,.2f}")
            print(f"  Payments        : {data['total_recovered_count']}")
            print(f"  Recovery rate   : {data['recovery_rate_pct']}%")
            print(f"  At risk         : Rs.{data['at_risk_inr']:,.2f}")
    except Exception:
        pass


def main():
    parser = argparse.ArgumentParser(description="Simulate Razorpay webhooks locally")
    parser.add_argument("--amount", type=float, default=500,   help="Amount in INR (default: 500)")
    parser.add_argument("--count",  type=int,   default=1,     help="Number of webhook events to send")
    parser.add_argument("--reset",  action="store_true",       help="Reset the recovery ledger")
    parser.add_argument("--delay",  type=float, default=0.5,   help="Delay between events (seconds)")
    args = parser.parse_args()

    print()
    print("=" * 55)
    print("  Razorpay Webhook Simulator")
    print("=" * 55)
    print(f"  Target  : {WEBHOOK_URL}")
    print(f"  Secret  : {'SET (' + WEBHOOK_SECRET[:6] + '...)' if WEBHOOK_SECRET else 'NOT SET (server will skip verification)'}")

    if args.reset:
        sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
        from recovery_ledger import reset_ledger
        reset_ledger()
        print("\n  Ledger reset.")
        return

    amount_paise = int(args.amount * 100)
    success = 0

    for i in range(args.count):
        ok = send_webhook(amount_paise, i + 1)
        if ok:
            success += 1
        if i < args.count - 1:
            time.sleep(args.delay)

    print(f"\n  Sent {success}/{args.count} webhooks successfully.")
    show_stats()
    print(f"\n  Open http://localhost:5000 to see the live counter update.")
    print()


if __name__ == "__main__":
    main()
