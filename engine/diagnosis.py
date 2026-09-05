"""
Chunk 2 — LLM-powered diagnosis + action selection engine.
Uses Grok (xAI) via OpenAI-compatible SDK to diagnose each failed payment
and select the optimal recovery action.
"""

import sys
import os

# Fix Windows console encoding
if sys.stdout.encoding != 'utf-8':
    sys.stdout.reconfigure(encoding='utf-8')
if sys.stderr.encoding != 'utf-8':
    sys.stderr.reconfigure(encoding='utf-8')

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import json
import time
from openai import OpenAI

from schemas.models import (
    DiagnosisResult, ERROR_DESCRIPTIONS, DEFAULT_ACTIONS, DEFAULT_ROOT_CAUSES,
    ACTIONS, ROOT_CAUSE_CATEGORIES, validate_diagnosis_result, save_json, load_json
)

# ─── LLM Client Setup ────────────────────────────────────────────────────────

def _get_client() -> OpenAI:
    """Initialize Groq client via OpenAI-compatible API."""
    api_key = os.environ.get("GROQ_API_KEY")
    if not api_key:
        print("  ⚠ GROQ_API_KEY not set — will use deterministic fallback for all records")
        return None
    return OpenAI(api_key=api_key, base_url="https://api.groq.com/openai/v1")

MODEL = "qwen/qwen3.8-27b"

# ─── Prompt Construction ─────────────────────────────────────────────────────

SYSTEM_PROMPT = """Payment recovery agent. Pick ONE action for a failed payment. Respond ONLY with JSON:
{"root_cause_category":"...","recommended_action":"...","action_reasoning":"...","recovery_message":"..."}

Actions: retry_immediate (transient infra only), retry_scheduled (timing issues), notify_customer (customer must act), escalate_human (ambiguous), no_action_fraud_flagged (fraud ONLY)
Categories: transient_infra, customer_instrument, customer_behavior, fraud_risk, generic_decline
Rules: payment_risk_check_failed→no_action_fraud_flagged ALWAYS. card_expired/debit_instrument_blocked→NEVER retry. past_failure_count>5→escalate_human.
recovery_message: Write a short, friendly SMS/notification (1-2 lines) to send the customer. Mention the specific issue and suggest a fix. For retry/escalate/fraud actions, write "N/A - internal action"."""

def _build_user_prompt(record: dict) -> str:
    ch = record["customer_history"]
    return (
        f"Error:{record['error_code']} Src:{record['error_source']} "
        f"Method:{record['payment_method']} Amt:{record['amount_inr']} "
        f"Cust:{record['customer_id']}({ch['months_as_customer']}mo,"
        f"{ch['past_success_count']}ok,{ch['past_failure_count']}fail) "
        f"Meaning:{ERROR_DESCRIPTIONS[record['error_code']]}"
    )


# ─── Diagnosis Logic ─────────────────────────────────────────────────────────

def _diagnose_single_llm(client: OpenAI, record: dict, max_retries: int = 3) -> dict | None:
    """Call LLM with retry-on-429 logic. Returns parsed dict or None on failure."""
    for attempt in range(max_retries):
        try:
            response = client.chat.completions.create(
                model=MODEL,
                messages=[
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user", "content": _build_user_prompt(record)},
                ],
                response_format={"type": "json_object"},
                temperature=0.3,
                max_tokens=250,
            )
            content = response.choices[0].message.content
            parsed = json.loads(content)

            # Validate the parsed fields
            if parsed.get("root_cause_category") not in ROOT_CAUSE_CATEGORIES:
                return None
            if parsed.get("recommended_action") not in ACTIONS:
                return None
            if not parsed.get("action_reasoning"):
                return None

            return parsed
        except Exception as e:
            err_str = str(e)
            if "429" in err_str or "rate_limit" in err_str:
                wait = 5 * (attempt + 1)
                print(f" [rate limit, wait {wait}s]", end="", flush=True)
                time.sleep(wait)
                continue
            elif "json_validate_failed" in err_str:
                # Groq JSON mode failed — retry once
                if attempt < max_retries - 1:
                    time.sleep(2)
                    continue
                return None
            else:
                print(f" ⚠ {e}", file=sys.stderr)
                return None
    return None


# Default recovery messages per error code
DEFAULT_MESSAGES = {
    "payment_timed_out": "Hi! Your payment of Rs.{amount} timed out. Please retry — make sure to complete within the time limit. Need help? Reply to this message.",
    "gateway_technical_error": "N/A - internal action",
    "payment_cancelled": "Hi! We noticed you cancelled your payment of Rs.{amount}. Your cart is still saved — tap here to complete your purchase when ready.",
    "card_declined": "Hi! Your card was declined for Rs.{amount}. Please try a different card or use UPI for instant payment.",
    "insufficient_funds": "Hi! Your payment of Rs.{amount} couldn't go through due to insufficient balance. Try again with a different payment method or retry after your next credit.",
    "card_not_enrolled": "Hi! Your card isn't enabled for online payments. Please enable it via your bank app, then retry your Rs.{amount} payment.",
    "bank_technical_error": "N/A - internal action",
    "card_disabled_for_online_payments": "Hi! Your card isn't activated for online transactions. Enable it in your bank app and retry your Rs.{amount} payment.",
    "authentication_failed": "Hi! Your payment of Rs.{amount} failed due to incorrect OTP. Please retry and enter the OTP carefully.",
    "payment_risk_check_failed": "N/A - internal action",
    "payment_failed": "Hi! Your payment of Rs.{amount} didn't go through. Please try again with a different payment method.",
    "incorrect_cvv": "Hi! Your payment of Rs.{amount} failed — the CVV entered was incorrect. Please retry with the correct 3-digit CVV from the back of your card.",
    "debit_instrument_inactive": "Hi! Your card isn't activated for online use. Please activate it via your bank app, then retry your Rs.{amount} payment.",
    "debit_instrument_blocked": "Hi! The card used for Rs.{amount} appears to be blocked. Please use a different card or contact your bank.",
    "card_expired": "Hi! The card used for Rs.{amount} has expired. Please update your card details or use a different payment method.",
    "transaction_limit_exceeded": "Hi! Your payment of Rs.{amount} exceeded your daily transaction limit. Try again tomorrow or use a different card/UPI.",
}


def _diagnose_single_fallback(record: dict) -> dict:
    """Deterministic fallback when LLM is unavailable."""
    error_code = record["error_code"]
    ch = record["customer_history"]
    amount = record["amount_inr"]

    action = DEFAULT_ACTIONS[error_code]
    root_cause = DEFAULT_ROOT_CAUSES[error_code]

    # Chronic failer override
    if ch["past_failure_count"] > 5 and action not in ("no_action_fraud_flagged",):
        action = "escalate_human"
        reasoning = (
            f"FALLBACK: LLM unavailable. Customer has {ch['past_failure_count']} past failures "
            f"(chronic failer). Escalating to human review instead of default action for {error_code}."
        )
    else:
        reasoning = (
            f"FALLBACK: LLM unavailable. Using default action '{action}' for error "
            f"'{error_code}' ({ERROR_DESCRIPTIONS[error_code]}). "
            f"Customer tenure: {ch['months_as_customer']}mo, "
            f"success rate: {ch['past_success_count']}/{ch['past_success_count'] + ch['past_failure_count']}."
        )

    msg = DEFAULT_MESSAGES.get(error_code, "").format(amount=f"{amount:,.2f}")

    return {
        "root_cause_category": root_cause,
        "recommended_action": action,
        "action_reasoning": reasoning,
        "recovery_message": msg,
    }


def diagnose_batch(records: list[dict]) -> list[dict]:
    """Diagnose all records, using LLM where available, fallback otherwise."""
    client = _get_client()
    diagnoses = []
    llm_count = 0
    fallback_count = 0

    for i, record in enumerate(records):
        pid = record["payment_id"]
        print(f"  Processing {i+1}/{len(records)}: {pid} ({record['error_code']})...", end="", flush=True)

        llm_result = None
        if client is not None:
            llm_result = _diagnose_single_llm(client, record)
            if llm_result is not None:
                # Delay for Groq rate limiting
                time.sleep(3)

        if llm_result is not None:
            parsed = llm_result
            llm_count += 1
            print(" ✓ LLM")
        else:
            parsed = _diagnose_single_fallback(record)
            fallback_count += 1
            print(" → fallback")

        # Build DiagnosisResult
        is_fraud = record["error_code"] == "payment_risk_check_failed"

        diagnosis = DiagnosisResult(
            payment_id=pid,
            amount_inr=record["amount_inr"],
            error_code=record["error_code"],
            error_source=record["error_source"],
            root_cause_category=parsed["root_cause_category"],
            recommended_action=parsed["recommended_action"],
            action_reasoning=parsed["action_reasoning"],
            is_fraud_flagged=is_fraud,
            recovery_message=parsed.get("recovery_message", ""),
            max_retries_remaining=2,
        )
        diagnoses.append(diagnosis.to_dict())

    # ─── Post-processing guardrail ────────────────────────────────────────
    overrides = 0
    for d in diagnoses:
        if d["error_code"] == "payment_risk_check_failed" and d["recommended_action"] != "no_action_fraud_flagged":
            d["recommended_action"] = "no_action_fraud_flagged"
            d["action_reasoning"] = "GUARDRAIL OVERRIDE: " + d["action_reasoning"]
            d["is_fraud_flagged"] = True
            overrides += 1

    # Validate all
    validation_errors = []
    for d in diagnoses:
        errs = validate_diagnosis_result(d)
        if errs:
            validation_errors.append((d["payment_id"], errs))

    if validation_errors:
        print(f"\n  ❌ {len(validation_errors)} validation errors:")
        for pid, errs in validation_errors:
            print(f"    {pid}: {errs}")

    # Print summary
    action_dist = {}
    for d in diagnoses:
        a = d["recommended_action"]
        action_dist[a] = action_dist.get(a, 0) + 1

    print(f"\n  Diagnosis complete:")
    print(f"    LLM responses: {llm_count}")
    print(f"    Fallback used: {fallback_count}")
    print(f"    Guardrail overrides: {overrides}")
    print(f"\n  Action distribution:")
    for action in ACTIONS:
        count = action_dist.get(action, 0)
        print(f"    {action}: {count}")

    # Save
    output_path = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "data", "diagnosis_results.json"
    )
    save_json(diagnoses, output_path)

    return diagnoses


if __name__ == "__main__":
    print("=" * 60)
    print("  CHUNK 2 — Diagnosis + Action Selection Engine")
    print("=" * 60)

    input_path = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "data", "failed_payments.json"
    )
    records = load_json(input_path)
    print(f"  Loaded {len(records)} records from failed_payments.json\n")

    diagnose_batch(records)
    print("\n  Done.")
