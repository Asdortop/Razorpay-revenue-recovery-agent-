"""
Shared schemas and enums for the Payment Recovery Agent pipeline.

THIS IS THE SHARED CONTRACT. All chunks import from here.
Do NOT rename fields or change enums without updating all consumers.
"""

from dataclasses import dataclass, field, asdict
from typing import Literal
import json

# ─── Enums (use these exact strings everywhere) ───────────────────────────────

ERROR_CODES = [
    "payment_timed_out",
    "gateway_technical_error",
    "payment_cancelled",
    "card_declined",
    "insufficient_funds",
    "card_not_enrolled",
    "bank_technical_error",
    "card_disabled_for_online_payments",
    "authentication_failed",
    "payment_risk_check_failed",
    "payment_failed",
    "incorrect_cvv",
    "debit_instrument_inactive",
    "debit_instrument_blocked",
    "card_expired",
    "transaction_limit_exceeded",
]

ERROR_SOURCES = ["customer", "gateway", "business", "razorpay"]

ROOT_CAUSE_CATEGORIES = [
    "transient_infra",       # gateway_technical_error, bank_technical_error
    "customer_instrument",   # card_declined, insufficient_funds, card_not_enrolled, etc.
    "customer_behavior",     # payment_timed_out, payment_cancelled, authentication_failed
    "fraud_risk",            # payment_risk_check_failed
    "generic_decline",       # payment_failed
]

ACTIONS = [
    "retry_immediate",
    "retry_scheduled",
    "notify_customer",
    "escalate_human",
    "no_action_fraud_flagged",
]

EXECUTION_OUTCOMES = [
    "recovered",
    "failed",
    "escalated",
    "notified_pending",
    "blocked_by_guardrail",
]

PAYMENT_METHODS = ["card", "upi", "netbanking", "wallet"]

# ─── Error code metadata ──────────────────────────────────────────────────────

ERROR_DESCRIPTIONS = {
    "payment_timed_out": "Customer exceeded the time limit to complete payment.",
    "gateway_technical_error": "Partner bank or payment gateway is experiencing downtime. Transient issue.",
    "payment_cancelled": "Customer actively cancelled or backed out of the payment flow.",
    "card_declined": "Bank declined the card. Specific reason usually unknown.",
    "insufficient_funds": "Customer's account does not have enough balance.",
    "card_not_enrolled": "Card is not enabled for online/e-commerce transactions.",
    "bank_technical_error": "Customer's bank is experiencing technical issues. Transient.",
    "card_disabled_for_online_payments": "Card is not activated for online payments (same as card_not_enrolled).",
    "authentication_failed": "Customer entered wrong OTP or abandoned the authentication screen.",
    "payment_risk_check_failed": "Bank flagged this transaction as a fraud risk. DO NOT auto-retry.",
    "payment_failed": "Generic bank decline. No specific reason available.",
    "incorrect_cvv": "Customer entered the wrong CVV number.",
    "debit_instrument_inactive": "Card has not been activated for online use by the customer.",
    "debit_instrument_blocked": "Card is blocked by the bank or by the customer.",
    "card_expired": "Card has expired. Cannot retry on the same card.",
    "transaction_limit_exceeded": "Daily or per-transaction limit has been reached.",
}

# error_code → default error_source
ERROR_SOURCE_MAP = {
    "payment_timed_out": "customer",
    "gateway_technical_error": "gateway",
    "payment_cancelled": "customer",
    "card_declined": "customer",
    "insufficient_funds": "customer",
    "card_not_enrolled": "customer",
    "bank_technical_error": "gateway",
    "card_disabled_for_online_payments": "customer",
    "authentication_failed": "customer",
    "payment_risk_check_failed": "customer",
    "payment_failed": "gateway",
    "incorrect_cvv": "customer",
    "debit_instrument_inactive": "customer",
    "debit_instrument_blocked": "customer",
    "card_expired": "customer",
    "transaction_limit_exceeded": "customer",
}

# ─── Deterministic fallback tables (used when LLM fails) ─────────────────────

DEFAULT_ACTIONS = {
    "payment_timed_out":                 "retry_scheduled",
    "gateway_technical_error":           "retry_immediate",
    "payment_cancelled":                 "notify_customer",
    "card_declined":                     "notify_customer",
    "insufficient_funds":                "notify_customer",
    "card_not_enrolled":                 "notify_customer",
    "bank_technical_error":              "retry_immediate",
    "card_disabled_for_online_payments": "notify_customer",
    "authentication_failed":             "notify_customer",
    "payment_risk_check_failed":         "no_action_fraud_flagged",
    "payment_failed":                    "notify_customer",
    "incorrect_cvv":                     "notify_customer",
    "debit_instrument_inactive":         "notify_customer",
    "debit_instrument_blocked":          "notify_customer",
    "card_expired":                      "notify_customer",
    "transaction_limit_exceeded":        "retry_scheduled",
}

DEFAULT_ROOT_CAUSES = {
    "payment_timed_out":                 "customer_behavior",
    "gateway_technical_error":           "transient_infra",
    "payment_cancelled":                 "customer_behavior",
    "card_declined":                     "customer_instrument",
    "insufficient_funds":                "customer_instrument",
    "card_not_enrolled":                 "customer_instrument",
    "bank_technical_error":              "transient_infra",
    "card_disabled_for_online_payments": "customer_instrument",
    "authentication_failed":             "customer_behavior",
    "payment_risk_check_failed":         "fraud_risk",
    "payment_failed":                    "generic_decline",
    "incorrect_cvv":                     "customer_instrument",
    "debit_instrument_inactive":         "customer_instrument",
    "debit_instrument_blocked":          "customer_instrument",
    "card_expired":                      "customer_instrument",
    "transaction_limit_exceeded":        "customer_instrument",
}

# ─── Success probability table (Chunk 3 uses this) ───────────────────────────

# success_probabilities[error_code][action] = probability of recovery
# Notification rates calibrated to real-world: ~20% SMS open rate, ~10-15% conversion
SUCCESS_PROBABILITIES = {
    "payment_timed_out":                 {"retry_immediate": 0.40, "retry_scheduled": 0.55, "notify_customer": 0.15},
    "gateway_technical_error":           {"retry_immediate": 0.70, "retry_scheduled": 0.85, "notify_customer": 0.00},
    "payment_cancelled":                 {"retry_immediate": 0.05, "retry_scheduled": 0.20, "notify_customer": 0.12},
    "card_declined":                     {"retry_immediate": 0.08, "retry_scheduled": 0.12, "notify_customer": 0.10},
    "insufficient_funds":                {"retry_immediate": 0.03, "retry_scheduled": 0.30, "notify_customer": 0.18},
    "card_not_enrolled":                 {"retry_immediate": 0.00, "retry_scheduled": 0.00, "notify_customer": 0.15},
    "bank_technical_error":              {"retry_immediate": 0.60, "retry_scheduled": 0.80, "notify_customer": 0.00},
    "card_disabled_for_online_payments": {"retry_immediate": 0.00, "retry_scheduled": 0.00, "notify_customer": 0.12},
    "authentication_failed":             {"retry_immediate": 0.25, "retry_scheduled": 0.35, "notify_customer": 0.18},
    "payment_risk_check_failed":         {"retry_immediate": 0.00, "retry_scheduled": 0.00, "notify_customer": 0.00},
    "payment_failed":                    {"retry_immediate": 0.08, "retry_scheduled": 0.12, "notify_customer": 0.10},
    "incorrect_cvv":                     {"retry_immediate": 0.03, "retry_scheduled": 0.25, "notify_customer": 0.22},
    "debit_instrument_inactive":         {"retry_immediate": 0.00, "retry_scheduled": 0.00, "notify_customer": 0.10},
    "debit_instrument_blocked":          {"retry_immediate": 0.00, "retry_scheduled": 0.00, "notify_customer": 0.05},
    "card_expired":                      {"retry_immediate": 0.00, "retry_scheduled": 0.00, "notify_customer": 0.10},
    "transaction_limit_exceeded":        {"retry_immediate": 0.00, "retry_scheduled": 0.40, "notify_customer": 0.15},
}

# ─── Dataclasses ──────────────────────────────────────────────────────────────

@dataclass
class CustomerHistory:
    months_as_customer: int
    past_success_count: int
    past_failure_count: int

    def to_dict(self) -> dict:
        return asdict(self)

@dataclass
class FailureRecord:
    payment_id: str
    amount_inr: float
    error_code: str
    error_source: str
    payment_method: str
    customer_id: str
    customer_history: CustomerHistory
    timestamp: str

    def to_dict(self) -> dict:
        d = asdict(self)
        return d

    @staticmethod
    def from_dict(d: dict) -> "FailureRecord":
        ch = d["customer_history"]
        if isinstance(ch, dict):
            ch = CustomerHistory(**ch)
        return FailureRecord(
            payment_id=d["payment_id"],
            amount_inr=d["amount_inr"],
            error_code=d["error_code"],
            error_source=d["error_source"],
            payment_method=d["payment_method"],
            customer_id=d["customer_id"],
            customer_history=ch,
            timestamp=d["timestamp"],
        )

@dataclass
class DiagnosisResult:
    payment_id: str
    amount_inr: float
    error_code: str
    error_source: str
    root_cause_category: str
    recommended_action: str
    action_reasoning: str
    is_fraud_flagged: bool
    recovery_message: str = ""  # Personalized message to send to customer
    max_retries_remaining: int = 2

    def to_dict(self) -> dict:
        return asdict(self)

    @staticmethod
    def from_dict(d: dict) -> "DiagnosisResult":
        return DiagnosisResult(
            payment_id=d["payment_id"],
            amount_inr=d["amount_inr"],
            error_code=d["error_code"],
            error_source=d["error_source"],
            root_cause_category=d["root_cause_category"],
            recommended_action=d["recommended_action"],
            action_reasoning=d["action_reasoning"],
            is_fraud_flagged=d["is_fraud_flagged"],
            recovery_message=d.get("recovery_message", ""),
            max_retries_remaining=d.get("max_retries_remaining", 2),
        )

@dataclass
class ActionResult:
    payment_id: str
    amount_inr: float
    error_code: str
    action_taken: str
    execution_outcome: str
    amount_recovered: float
    retries_used: int
    guardrails_triggered: list
    audit_entry: str
    timestamp: str

    def to_dict(self) -> dict:
        return asdict(self)

    @staticmethod
    def from_dict(d: dict) -> "ActionResult":
        return ActionResult(
            payment_id=d["payment_id"],
            amount_inr=d["amount_inr"],
            error_code=d["error_code"],
            action_taken=d["action_taken"],
            execution_outcome=d["execution_outcome"],
            amount_recovered=d["amount_recovered"],
            retries_used=d["retries_used"],
            guardrails_triggered=d.get("guardrails_triggered", []),
            audit_entry=d["audit_entry"],
            timestamp=d["timestamp"],
        )


# ─── Validation helpers ──────────────────────────────────────────────────────

def validate_failure_record(d: dict) -> list[str]:
    """Returns list of validation errors. Empty list = valid."""
    errors = []
    if d.get("error_code") not in ERROR_CODES:
        errors.append(f"Invalid error_code: {d.get('error_code')}")
    if d.get("error_source") not in ERROR_SOURCES:
        errors.append(f"Invalid error_source: {d.get('error_source')}")
    if d.get("payment_method") not in PAYMENT_METHODS:
        errors.append(f"Invalid payment_method: {d.get('payment_method')}")
    if not isinstance(d.get("amount_inr"), (int, float)) or d["amount_inr"] <= 0:
        errors.append(f"Invalid amount_inr: {d.get('amount_inr')}")
    if not isinstance(d.get("customer_history"), dict):
        errors.append("customer_history must be a dict")
    return errors


def validate_diagnosis_result(d: dict) -> list[str]:
    """Returns list of validation errors. Empty list = valid."""
    errors = []
    if d.get("root_cause_category") not in ROOT_CAUSE_CATEGORIES:
        errors.append(f"Invalid root_cause_category: {d.get('root_cause_category')}")
    if d.get("recommended_action") not in ACTIONS:
        errors.append(f"Invalid recommended_action: {d.get('recommended_action')}")
    # Hard guardrail check
    if d.get("error_code") == "payment_risk_check_failed" and d.get("recommended_action") != "no_action_fraud_flagged":
        errors.append(f"GUARDRAIL VIOLATION: payment_risk_check_failed must use no_action_fraud_flagged, got {d.get('recommended_action')}")
    return errors


def validate_action_result(d: dict) -> list[str]:
    """Returns list of validation errors. Empty list = valid."""
    errors = []
    if d.get("action_taken") not in ACTIONS:
        errors.append(f"Invalid action_taken: {d.get('action_taken')}")
    if d.get("execution_outcome") not in EXECUTION_OUTCOMES:
        errors.append(f"Invalid execution_outcome: {d.get('execution_outcome')}")
    if not isinstance(d.get("amount_recovered"), (int, float)):
        errors.append(f"Invalid amount_recovered: {d.get('amount_recovered')}")
    return errors


# ─── I/O helpers ──────────────────────────────────────────────────────────────

def save_json(data: list[dict] | dict, filepath: str) -> None:
    """Save data to a JSON file with pretty formatting."""
    with open(filepath, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
    print(f"  ✓ Saved {filepath}")


def load_json(filepath: str) -> list[dict] | dict:
    """Load data from a JSON file."""
    with open(filepath, "r", encoding="utf-8") as f:
        return json.load(f)
