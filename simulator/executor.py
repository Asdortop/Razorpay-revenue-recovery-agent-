"""
Chunk 3 — Execution simulator + guardrail enforcement.
Simulates recovery action execution with realistic success probabilities
and enforces hard guardrails (fraud block, max retries, dead instruments).
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
from datetime import datetime, timezone
from schemas.models import (
    ActionResult, SUCCESS_PROBABILITIES, ACTIONS, EXECUTION_OUTCOMES,
    validate_action_result, save_json, load_json
)

# ─── Guardrail Definitions ───────────────────────────────────────────────────

DEAD_INSTRUMENT_CODES = {"card_expired", "debit_instrument_blocked"}
FRAUD_CODE = "payment_risk_check_failed"


def _check_guardrails(diagnosis: dict) -> tuple[list[str], str | None]:
    """
    Check guardrails BEFORE execution.
    Returns (guardrails_triggered, override_action).
    If override_action is not None, skip simulation and use that action.
    """
    triggered = []
    override = None
    error_code = diagnosis["error_code"]
    action = diagnosis["recommended_action"]

    # Rule 1: Fraud block — never retry or act on fraud-flagged
    if error_code == FRAUD_CODE:
        triggered.append("fraud_block")
        override = "no_action_fraud_flagged"
        return triggered, override

    # Rule 2: Dead instrument — never retry expired/blocked cards
    if error_code in DEAD_INSTRUMENT_CODES and action in ("retry_immediate", "retry_scheduled"):
        triggered.append("dead_instrument_block")
        override = "notify_customer"

    return triggered, override


# ─── Execution Simulation ────────────────────────────────────────────────────

def _simulate_retry(error_code: str, action: str, max_retries: int, rng: random.Random) -> tuple[str, float, int]:
    """
    Simulate retry_immediate or retry_scheduled.
    Returns (outcome, amount_multiplier, retries_used).
    amount_multiplier is 1.0 if recovered, 0.0 if not.
    """
    prob = SUCCESS_PROBABILITIES.get(error_code, {}).get(action, 0.0)
    retries_used = 0

    for attempt in range(max_retries):
        retries_used = attempt + 1
        if rng.random() < prob:
            return "recovered", 1.0, retries_used

    return "failed", 0.0, retries_used


def _simulate_notify(error_code: str, rng: random.Random) -> tuple[str, float]:
    """
    Simulate notify_customer action.
    Returns (outcome, amount_multiplier).
    """
    prob = SUCCESS_PROBABILITIES.get(error_code, {}).get("notify_customer", 0.0)
    if rng.random() < prob:
        return "recovered", 1.0
    return "notified_pending", 0.0


def _build_audit_entry(diagnosis: dict, action_taken: str, outcome: str,
                       amount_recovered: float, retries_used: int,
                       guardrails: list[str]) -> str:
    """Build a human-readable audit trail entry."""
    amount_inr = diagnosis["amount_inr"]
    parts = [
        f"DIAGNOSIS: {diagnosis['error_code']} ({diagnosis['error_source']})",
        f"ACTION: {action_taken}",
        f"REASONING: {diagnosis['action_reasoning']}",
        f"RETRIES: {retries_used}",
        f"OUTCOME: {outcome}",
    ]

    if outcome == "recovered":
        parts.append(f"RECOVERED Rs.{amount_inr:,.2f}")
    elif outcome == "blocked_by_guardrail":
        parts.append("BLOCKED BY GUARDRAIL")
    elif outcome == "escalated":
        parts.append("ESCALATED TO HUMAN")
    elif outcome == "notified_pending":
        parts.append("CUSTOMER NOTIFIED - PENDING RESPONSE")
    else:
        parts.append("NOT RECOVERED")

    if guardrails:
        parts.append(f"GUARDRAILS: {', '.join(guardrails)}")

    return ". ".join(parts) + "."


# ─── Main Batch Executor ─────────────────────────────────────────────────────

def execute_batch(diagnoses: list[dict], seed: int = 42) -> list[dict]:
    """Execute recovery actions for all diagnoses with guardrail enforcement."""
    rng = random.Random(seed)
    results = []

    stats = {"recovered": 0, "failed": 0, "escalated": 0,
             "notified_pending": 0, "blocked_by_guardrail": 0}

    for i, diag in enumerate(diagnoses):
        pid = diag["payment_id"]
        amount = diag["amount_inr"]
        error_code = diag["error_code"]
        action = diag["recommended_action"]
        max_retries = diag.get("max_retries_remaining", 2)

        # ── Guardrail check ───────────────────────────────────────────
        guardrails_triggered, override_action = _check_guardrails(diag)

        if override_action is not None:
            action = override_action

        # ── Simulate execution ────────────────────────────────────────
        outcome = ""
        amount_recovered = 0.0
        retries_used = 0

        if action == "no_action_fraud_flagged":
            outcome = "blocked_by_guardrail"
            amount_recovered = 0.0
            retries_used = 0

        elif action == "escalate_human":
            outcome = "escalated"
            amount_recovered = 0.0
            retries_used = 0

        elif action in ("retry_immediate", "retry_scheduled"):
            outcome, multiplier, retries_used = _simulate_retry(
                error_code, action, max_retries, rng
            )
            amount_recovered = amount * multiplier
            # Track max_retry guardrail
            if retries_used >= max_retries and outcome == "failed":
                guardrails_triggered.append("max_retry_limit")

        elif action == "notify_customer":
            outcome, multiplier = _simulate_notify(error_code, rng)
            amount_recovered = amount * multiplier
            retries_used = 0

        else:
            # Unknown action — treat as escalation
            outcome = "escalated"
            amount_recovered = 0.0
            guardrails_triggered.append("unknown_action_escalated")

        # Round recovered amount
        amount_recovered = round(amount_recovered, 2)

        # ── Build audit entry ─────────────────────────────────────────
        audit = _build_audit_entry(
            diag, action, outcome, amount_recovered,
            retries_used, guardrails_triggered
        )

        # ── Build result ──────────────────────────────────────────────
        result = ActionResult(
            payment_id=pid,
            amount_inr=amount,
            error_code=error_code,
            action_taken=action,
            execution_outcome=outcome,
            amount_recovered=amount_recovered,
            retries_used=retries_used,
            guardrails_triggered=guardrails_triggered,
            audit_entry=audit,
            timestamp=datetime.now(timezone.utc).isoformat(),
        )
        results.append(result.to_dict())
        stats[outcome] = stats.get(outcome, 0) + 1

        # Progress
        status_icon = "💰" if outcome == "recovered" else "·"
        print(f"  {i+1:3d}/100  {pid}  {error_code:<35s}  {action:<25s}  → {outcome} {status_icon}")

    # Validate all results
    validation_errors = []
    for r in results:
        errs = validate_action_result(r)
        if errs:
            validation_errors.append((r["payment_id"], errs))

    if validation_errors:
        print(f"\n  ❌ {len(validation_errors)} validation errors:")
        for pid, errs in validation_errors:
            print(f"    {pid}: {errs}")

    # Summary
    total_recovered = sum(r["amount_recovered"] for r in results)
    total_at_risk = sum(r["amount_inr"] for r in results)
    rate = (total_recovered / total_at_risk * 100) if total_at_risk > 0 else 0

    print(f"\n  Execution complete:")
    print(f"    Recovered:          {stats['recovered']}")
    print(f"    Failed:             {stats['failed']}")
    print(f"    Escalated:          {stats['escalated']}")
    print(f"    Notified (pending): {stats['notified_pending']}")
    print(f"    Blocked (guardrail):{stats['blocked_by_guardrail']}")
    print(f"    Amount recovered:   Rs.{total_recovered:,.2f} / Rs.{total_at_risk:,.2f} ({rate:.1f}%)")

    # Save
    output_path = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "data", "action_results.json"
    )
    save_json(results, output_path)

    return results


if __name__ == "__main__":
    print("=" * 60)
    print("  CHUNK 3 — Execution Simulator + Guardrails")
    print("=" * 60)

    input_path = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "data", "diagnosis_results.json"
    )
    diagnoses = load_json(input_path)
    print(f"  Loaded {len(diagnoses)} diagnosis results\n")

    execute_batch(diagnoses, seed=42)
    print("\n  Done.")
