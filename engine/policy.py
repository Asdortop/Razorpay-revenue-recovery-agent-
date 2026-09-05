"""
Policy Engine — Deterministic gatekeeper between LLM reasoning and monetary execution.

CRITICAL DESIGN PRINCIPLE:
  LLMs are NEVER trusted for monetary authorization.
  The LLM recommends. The Policy Engine approves, modifies, or blocks.
  Every override is logged in the audit trail.

This module enforces ALL hard rules regardless of what the LLM outputs.
"""

import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from schemas.models import ACTIONS


# ═══════════════════════════════════════════════════════════════════════════════
#  POLICY RULES — Each rule is a function that returns (action, override_reason)
#  or (None, None) if the rule doesn't apply.
# ═══════════════════════════════════════════════════════════════════════════════

def rule_fraud_block(diagnosis: dict, record: dict) -> tuple:
    """RULE 1: Fraud-flagged payments are NEVER retried or acted upon."""
    if record["error_code"] == "payment_risk_check_failed":
        if diagnosis["recommended_action"] != "no_action_fraud_flagged":
            return (
                "no_action_fraud_flagged",
                f"POLICY OVERRIDE: LLM recommended '{diagnosis['recommended_action']}' "
                f"but payment_risk_check_failed MUST be blocked. Fraud-flagged payments "
                f"are never retried or notified — this is a hard compliance rule."
            )
    return (None, None)


def rule_dead_instrument_no_retry(diagnosis: dict, record: dict) -> tuple:
    """RULE 2: Expired/blocked cards are NEVER retried — same card always fails."""
    dead_codes = {"card_expired", "debit_instrument_blocked"}
    retry_actions = {"retry_immediate", "retry_scheduled"}

    if record["error_code"] in dead_codes and diagnosis["recommended_action"] in retry_actions:
        return (
            "notify_customer",
            f"POLICY OVERRIDE: LLM recommended '{diagnosis['recommended_action']}' "
            f"but {record['error_code']} means the instrument is permanently unusable. "
            f"Retrying would always fail. Changed to notify_customer to suggest "
            f"updating payment method."
        )
    return (None, None)


def rule_high_value_human_review(diagnosis: dict, record: dict) -> tuple:
    """RULE 3: Payments above ₹5,000 with auto-retry get escalated to human review."""
    if (record["amount_inr"] > 5000
            and diagnosis["recommended_action"] == "retry_immediate"
            and record["error_code"] not in ("gateway_technical_error", "bank_technical_error")):
        return (
            "escalate_human",
            f"POLICY OVERRIDE: LLM recommended 'retry_immediate' for ₹{record['amount_inr']:,.2f} "
            f"but non-transient errors above ₹5,000 require human review before auto-retry. "
            f"This prevents monetary risk on large transactions."
        )
    return (None, None)


def rule_chronic_failer_escalate(diagnosis: dict, record: dict) -> tuple:
    """RULE 4: Customers with >5 past failures get escalated — pattern suggests deeper issue."""
    ch = record["customer_history"]
    if (ch["past_failure_count"] > 5
            and diagnosis["recommended_action"] not in ("no_action_fraud_flagged", "escalate_human")):
        return (
            "escalate_human",
            f"POLICY OVERRIDE: LLM recommended '{diagnosis['recommended_action']}' "
            f"but customer has {ch['past_failure_count']} past failures (chronic failer). "
            f"Automated recovery is unlikely to work — escalating to human agent "
            f"who can investigate the root cause pattern."
        )
    return (None, None)


def rule_cancelled_no_retry(diagnosis: dict, record: dict) -> tuple:
    """RULE 5: Customer-cancelled payments should NEVER be auto-retried."""
    if (record["error_code"] == "payment_cancelled"
            and diagnosis["recommended_action"] in ("retry_immediate", "retry_scheduled")):
        return (
            "notify_customer",
            f"POLICY OVERRIDE: LLM recommended '{diagnosis['recommended_action']}' "
            f"but the customer explicitly cancelled this payment. Auto-retrying a "
            f"cancelled payment violates customer intent. Changed to gentle notification."
        )
    return (None, None)


def rule_low_success_rate_escalate(diagnosis: dict, record: dict) -> tuple:
    """RULE 6: Customers with <20% historical success rate get escalated."""
    ch = record["customer_history"]
    total = ch["past_success_count"] + ch["past_failure_count"]
    if total >= 5:  # Only apply with enough history
        success_rate = ch["past_success_count"] / total
        if (success_rate < 0.20
                and diagnosis["recommended_action"] in ("retry_immediate", "retry_scheduled")):
            return (
                "escalate_human",
                f"POLICY OVERRIDE: LLM recommended '{diagnosis['recommended_action']}' "
                f"but customer's historical success rate is {success_rate:.0%} "
                f"({ch['past_success_count']}/{total}). Auto-retry is unlikely to succeed — "
                f"escalating for human investigation."
            )
    return (None, None)


# ═══════════════════════════════════════════════════════════════════════════════
#  POLICY ENGINE — Runs all rules in priority order
# ═══════════════════════════════════════════════════════════════════════════════

# Rules in priority order (first match wins)
POLICY_RULES = [
    ("FRAUD_BLOCK",             rule_fraud_block),
    ("DEAD_INSTRUMENT_BLOCK",   rule_dead_instrument_no_retry),
    ("CANCELLED_NO_RETRY",      rule_cancelled_no_retry),
    ("HIGH_VALUE_REVIEW",       rule_high_value_human_review),
    ("CHRONIC_FAILER",          rule_chronic_failer_escalate),
    ("LOW_SUCCESS_RATE",        rule_low_success_rate_escalate),
]


def apply_policy(diagnosis: dict, record: dict) -> dict:
    """
    Run the diagnosis through the policy engine.

    Args:
        diagnosis: DiagnosisResult dict (from LLM or fallback)
        record: Original FailureRecord dict

    Returns:
        Updated diagnosis dict with policy decisions applied.
        Adds fields:
            - policy_approved: bool (True if LLM action was approved as-is)
            - policy_overrides: list of {rule, original_action, enforced_action, reason}
            - llm_original_action: str (what the LLM originally recommended)
    """
    result = dict(diagnosis)  # Copy
    result["llm_original_action"] = diagnosis["recommended_action"]
    result["policy_approved"] = True
    result["policy_overrides"] = []

    for rule_name, rule_fn in POLICY_RULES:
        enforced_action, override_reason = rule_fn(result, record)
        if enforced_action is not None:
            result["policy_overrides"].append({
                "rule": rule_name,
                "llm_recommended": result["recommended_action"],
                "policy_enforced": enforced_action,
                "reason": override_reason,
            })
            result["recommended_action"] = enforced_action
            result["policy_approved"] = False
            # Update reasoning to include override
            result["action_reasoning"] = (
                f"[POLICY OVERRIDE — {rule_name}] {override_reason}\n"
                f"[LLM ORIGINAL] {result['action_reasoning']}"
            )
            break  # First matching rule wins

    return result


def apply_policy_batch(diagnoses: list[dict], records: list[dict]) -> list[dict]:
    """
    Apply policy engine to a batch of diagnoses.
    Returns updated diagnoses + prints summary.
    """
    record_map = {r["payment_id"]: r for r in records}

    results = []
    override_count = 0
    approved_count = 0
    overrides_by_rule = {}

    for diag in diagnoses:
        record = record_map.get(diag["payment_id"])
        if record is None:
            results.append(diag)
            continue

        updated = apply_policy(diag, record)
        results.append(updated)

        if updated["policy_approved"]:
            approved_count += 1
        else:
            override_count += 1
            for ov in updated["policy_overrides"]:
                rule = ov["rule"]
                overrides_by_rule[rule] = overrides_by_rule.get(rule, 0) + 1

    # Print summary
    total = len(results)
    print(f"\n  Policy Engine Results:")
    print(f"    Total diagnoses reviewed:  {total}")
    print(f"    LLM approved as-is:        {approved_count} ({approved_count/total*100:.0f}%)")
    print(f"    Policy overrides:          {override_count} ({override_count/total*100:.0f}%)")

    if overrides_by_rule:
        print(f"\n  Overrides by rule:")
        for rule, count in sorted(overrides_by_rule.items(), key=lambda x: -x[1]):
            print(f"    {rule}: {count}")

    return results


# ─── Standalone demo ─────────────────────────────────────────────────────────

if __name__ == "__main__":
    print("=" * 64)
    print("  POLICY ENGINE — Demo")
    print("=" * 64)
    print()
    print("  Rules enforced (in priority order):")
    for i, (name, _) in enumerate(POLICY_RULES, 1):
        print(f"    {i}. {name}")
    print()
    print("  Principle: LLM reasons → Policy Engine approves/overrides → Execution")
    print("  LLMs are NEVER trusted for monetary authorization.")
