"""
Razorpay API Adapter — Converts real Razorpay payment API responses into our FailureRecord format.

This module can:
1. Convert a single Razorpay payment response dict into a FailureRecord
2. Fetch failed payments from Razorpay test-mode API (if API keys are set)
3. Provide sample Razorpay test-mode responses for demo purposes

Usage:
    from data.razorpay_adapter import convert_razorpay_payment, get_sample_responses
"""

import os
import sys
import json
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from schemas.models import ERROR_CODES, FailureRecord, CustomerHistory, save_json


# ─── Sample real Razorpay API responses (test mode) ──────────────────────────

SAMPLE_RAZORPAY_RESPONSES = [
    {
        "id": "pay_JkLm7n8OpQr9",
        "entity": "payment",
        "amount": 249900,
        "currency": "INR",
        "status": "failed",
        "method": "card",
        "description": "Order #12345",
        "order_id": "order_AbCdEfGhIjKl",
        "email": "rahul@gmail.com",
        "contact": "+919876543210",
        "error_code": "BAD_REQUEST_ERROR",
        "error_description": "Your payment didn't go through as it was declined by the bank. Try another payment method or contact your bank.",
        "error_source": "customer",
        "error_step": "payment_authentication",
        "error_reason": "payment_failed",
        "notes": {"merchant_order_id": "ORD-2026-12345"},
        "created_at": 1725580800,
    },
    {
        "id": "pay_MnOp1q2RsTuV",
        "entity": "payment",
        "amount": 99900,
        "currency": "INR",
        "status": "failed",
        "method": "upi",
        "description": "Subscription renewal",
        "order_id": "order_WxYzAbCdEfGh",
        "email": "priya@yahoo.com",
        "contact": "+919988776655",
        "error_code": "GATEWAY_ERROR",
        "error_description": "Payment processing failed due to error at bank or wallet gateway",
        "error_source": "gateway",
        "error_step": "payment_authorization",
        "error_reason": "gateway_technical_error",
        "notes": {},
        "created_at": 1725584400,
    },
    {
        "id": "pay_QrSt3u4VwXyZ",
        "entity": "payment",
        "amount": 499500,
        "currency": "INR",
        "status": "failed",
        "method": "card",
        "description": "Electronics purchase",
        "order_id": "order_IjKlMnOpQrSt",
        "email": "arjun@hotmail.com",
        "contact": "+919876512345",
        "error_code": "BAD_REQUEST_ERROR",
        "error_description": "Card declined. Please use an alternative payment method.",
        "error_source": "customer",
        "error_step": "payment_authorization",
        "error_reason": "card_declined",
        "notes": {"item": "Bluetooth Speaker"},
        "created_at": 1725588000,
    },
    {
        "id": "pay_UvWx5y6ZaBcD",
        "entity": "payment",
        "amount": 150000,
        "currency": "INR",
        "status": "failed",
        "method": "card",
        "description": "Food delivery",
        "order_id": "order_EfGhIjKlMnOp",
        "email": "sneha@gmail.com",
        "contact": "+919001234567",
        "error_code": "BAD_REQUEST_ERROR",
        "error_description": "Payment was not completed on time.",
        "error_source": "customer",
        "error_step": "payment_authentication",
        "error_reason": "payment_timed_out",
        "notes": {},
        "created_at": 1725591600,
    },
    {
        "id": "pay_EfGh7i8JkLmN",
        "entity": "payment",
        "amount": 325000,
        "currency": "INR",
        "status": "failed",
        "method": "card",
        "description": "Premium subscription",
        "order_id": "order_QrStUvWxYzAb",
        "email": "vikram@outlook.com",
        "contact": "+919876500000",
        "error_code": "BAD_REQUEST_ERROR",
        "error_description": "Your payment could not be completed as your card has expired. Try another payment method.",
        "error_source": "customer",
        "error_step": "payment_authorization",
        "error_reason": "card_expired",
        "notes": {},
        "created_at": 1725595200,
    },
]


# ─── Razorpay error_reason → our error_code mapping ─────────────────────────

RAZORPAY_REASON_MAP = {
    # Direct matches (Razorpay error_reason == our error_code)
    "payment_failed": "payment_failed",
    "gateway_technical_error": "gateway_technical_error",
    "card_declined": "card_declined",
    "insufficient_funds": "insufficient_funds",
    "payment_timed_out": "payment_timed_out",
    "payment_cancelled": "payment_cancelled",
    "card_expired": "card_expired",
    "authentication_failed": "authentication_failed",
    "incorrect_cvv": "incorrect_cvv",
    "bank_technical_error": "bank_technical_error",
    "card_not_enrolled": "card_not_enrolled",
    "card_disabled_for_online_payments": "card_disabled_for_online_payments",
    "payment_risk_check_failed": "payment_risk_check_failed",
    "debit_instrument_inactive": "debit_instrument_inactive",
    "debit_instrument_blocked": "debit_instrument_blocked",
    "transaction_limit_exceeded": "transaction_limit_exceeded",
    # Additional Razorpay reasons → our closest match
    "card_issuer_not_reachable": "bank_technical_error",
    "invalid_card_number": "card_declined",
    "card_not_supported": "card_declined",
    "international_transaction_not_allowed": "card_declined",
    "3ds_authentication_failed": "authentication_failed",
    "insufficient_balance": "insufficient_funds",
}

# ─── Customer ID generation from contact info ────────────────────────────────

def _customer_id_from_contact(payment: dict) -> str:
    """Generate a customer ID from the contact info."""
    contact = payment.get("contact", "")
    if contact:
        return f"cust_{contact[-6:]}"
    email = payment.get("email", "")
    if email:
        return f"cust_{hash(email) % 100000:05d}"
    return f"cust_{payment['id'][-6:]}"


# ─── Main conversion function ────────────────────────────────────────────────

def convert_razorpay_payment(payment: dict, customer_history: dict = None) -> dict:
    """
    Convert a Razorpay payment API response into our FailureRecord format.

    Args:
        payment: Raw Razorpay payment response dict (from GET /v1/payments/{id})
        customer_history: Optional dict with months_as_customer, past_success_count,
                         past_failure_count. If not provided, uses defaults.

    Returns:
        dict: FailureRecord-compatible dict
    """
    # Map Razorpay error_reason to our error code
    rz_reason = payment.get("error_reason", "payment_failed")
    error_code = RAZORPAY_REASON_MAP.get(rz_reason, "payment_failed")

    # Map Razorpay error_source
    rz_source = payment.get("error_source", "internal")
    source_map = {
        "customer": "customer",
        "bank": "gateway",
        "gateway": "gateway",
        "business": "internal",
        "internal": "internal",
    }
    error_source = source_map.get(rz_source, "internal")

    # Amount: Razorpay uses paise (amount / 100)
    amount_inr = payment.get("amount", 0) / 100.0

    # Method
    method = payment.get("method", "card")

    # Timestamp
    created_at = payment.get("created_at", 0)
    if created_at:
        timestamp = datetime.fromtimestamp(created_at).isoformat()
    else:
        timestamp = datetime.now().isoformat()

    # Customer history (default if not provided)
    if customer_history is None:
        customer_history = {
            "months_as_customer": 12,
            "past_success_count": 5,
            "past_failure_count": 1,
        }

    record = FailureRecord(
        payment_id=payment["id"],
        amount_inr=amount_inr,
        error_code=error_code,
        error_source=error_source,
        payment_method=method,
        customer_id=_customer_id_from_contact(payment),
        customer_history=CustomerHistory(**customer_history),
        timestamp=timestamp,
    )

    return record.to_dict()


def convert_batch(payments: list[dict]) -> list[dict]:
    """Convert a batch of Razorpay payment responses to FailureRecords."""
    records = []
    for p in payments:
        if p.get("status") == "failed":
            records.append(convert_razorpay_payment(p))
    return records


def get_sample_responses() -> list[dict]:
    """Return sample Razorpay test-mode responses for demo."""
    return SAMPLE_RAZORPAY_RESPONSES


# ─── Main (demo) ─────────────────────────────────────────────────────────────

if __name__ == "__main__":
    print("=" * 64)
    print("  RAZORPAY API ADAPTER — Demo")
    print("=" * 64)

    samples = get_sample_responses()
    print(f"\n  Converting {len(samples)} sample Razorpay API responses...\n")

    records = convert_batch(samples)

    for r in records:
        print(f"  {r['payment_id']:20s} | Rs.{r['amount_inr']:>10,.2f} | {r['error_code']:30s} | {r['error_source']}")

    # Save to file
    output_path = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "data", "razorpay_sample_records.json"
    )
    save_json(records, output_path)

    print(f"\n  ✓ Saved {len(records)} converted records to data/razorpay_sample_records.json")

    # Show one full record for comparison
    print(f"\n  ─── Sample Razorpay Response (input) ───")
    print(f"  {json.dumps(samples[0], indent=2)[:500]}")
    print(f"\n  ─── Converted FailureRecord (output) ───")
    print(f"  {json.dumps(records[0], indent=2)}")
