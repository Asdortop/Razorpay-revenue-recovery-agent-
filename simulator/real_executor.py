"""
Real Executor — Approach C

Replaces the simulated executor with real Razorpay API calls.

Architecture:
  - notify_customer  → Create Razorpay Payment Link + SMS notification
  - retry_immediate  → Create Razorpay Payment Link (immediate)
  - retry_scheduled  → Create Razorpay Payment Link (with 24h delay note)
  - escalate_human   → Log to audit, no API call
  - no_action_fraud  → Hard block, no API call

CRITICAL: This executor ENFORCES guardrails before ANY API call.
LLM-recommended actions are verified by policy before reaching here.
"""

import sys
import os

if sys.stdout.encoding != 'utf-8':
    sys.stdout.reconfigure(encoding='utf-8')
if sys.stderr.encoding != 'utf-8':
    sys.stderr.reconfigure(encoding='utf-8')

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from datetime import datetime, timezone
from razorpay_client.client import RazorpayClient
from schemas.models import ActionResult, save_json, load_json


# ─── Guardrail constants (same as simulated executor) ────────────────────────

DEAD_INSTRUMENT_CODES = {"card_expired", "debit_instrument_blocked"}
FRAUD_CODE = "payment_risk_check_failed"
MAX_AMOUNT_INR = 200_000  # Rs. 2 lakh hard cap per payment link


# ─── Description templates for Payment Links ─────────────────────────────────

LINK_DESCRIPTIONS = {
    "notify_customer":  "Recovery: Please complete your pending payment of Rs.{amount:.2f}. Ref: {pid}",
    "retry_immediate":  "Retry: Your previous payment failed. Please retry Rs.{amount:.2f}. Ref: {pid}",
    "retry_scheduled":  "Scheduled retry: Complete your payment of Rs.{amount:.2f} at your convenience. Ref: {pid}",
}


def _check_guardrails(diagnosis: dict) -> tuple[list[str], str | None]:
    """
    Hard guardrail check BEFORE any API call.
    Returns (guardrails_triggered, override_action).
    If override_action is set, skip API and use it directly.
    """
    triggered = []
    override = None
    error_code = diagnosis["error_code"]
    action = diagnosis["recommended_action"]

    # Rule 1: Fraud — absolute block
    if error_code == FRAUD_CODE:
        triggered.append("fraud_block")
        override = "no_action_fraud_flagged"
        return triggered, override

    # Rule 2: Dead instrument — never retry
    if error_code in DEAD_INSTRUMENT_CODES and action in ("retry_immediate", "retry_scheduled"):
        triggered.append("dead_instrument_block")
        override = "notify_customer"  # redirect to notify (link still useful)

    # Rule 3: Amount cap
    if diagnosis["amount_inr"] > MAX_AMOUNT_INR:
        triggered.append("high_value_review")
        override = "escalate_human"

    return triggered, override


def _build_link_description(action: str, amount_inr: float, payment_id: str) -> str:
    template = LINK_DESCRIPTIONS.get(action, "Payment recovery — Ref: {pid}")
    return template.format(amount=amount_inr, pid=payment_id)


def _build_audit_entry(
    diagnosis: dict,
    action_taken: str,
    outcome: str,
    amount_recovered: float,
    payment_link_id: str | None,
    short_url: str | None,
    guardrails: list[str],
    api_error: str | None,
    dry_run: bool,
) -> str:
    """Build human-readable audit trail entry."""
    parts = [
        f"DIAGNOSIS: {diagnosis['error_code']} ({diagnosis['error_source']})",
        f"ACTION: {action_taken}",
        f"REASONING: {diagnosis['action_reasoning'][:120]}",
        f"OUTCOME: {outcome}",
        f"DRY_RUN: {dry_run}",
    ]

    if payment_link_id:
        parts.append(f"PAYMENT_LINK_ID: {payment_link_id}")
    if short_url:
        parts.append(f"PAYMENT_LINK_URL: {short_url}")
    if outcome == "recovered":
        parts.append(f"RECOVERED Rs.{amount_recovered:,.2f}")
    if api_error:
        parts.append(f"API_ERROR: {api_error}")
    if guardrails:
        parts.append(f"GUARDRAILS: {', '.join(guardrails)}")

    return ". ".join(parts) + "."


# ─── Core execution function ─────────────────────────────────────────────────

def _execute_single(diagnosis: dict, client: RazorpayClient) -> dict:
    """
    Execute a single recovery action via Razorpay API.
    Returns an ActionResult-compatible dict.
    """
    pid = diagnosis["payment_id"]
    amount = diagnosis["amount_inr"]
    error_code = diagnosis["error_code"]
    action = diagnosis["recommended_action"]
    customer_id = diagnosis.get("customer_id", "unknown")
    recovery_msg = diagnosis.get("recovery_message", "")

    # ── Guardrail check ───────────────────────────────────────────────────
    guardrails_triggered, override_action = _check_guardrails(diagnosis)
    if override_action:
        action = override_action

    # ── Hard blocks: no API call ──────────────────────────────────────────
    if action == "no_action_fraud_flagged":
        audit = _build_audit_entry(
            diagnosis, action, "blocked_by_guardrail",
            0.0, None, None, guardrails_triggered, None, client.dry_run
        )
        return ActionResult(
            payment_id=pid, amount_inr=amount, error_code=error_code,
            action_taken=action, execution_outcome="blocked_by_guardrail",
            amount_recovered=0.0, retries_used=0,
            guardrails_triggered=guardrails_triggered,
            audit_entry=audit,
            timestamp=datetime.now(timezone.utc).isoformat(),
        ).to_dict()

    if action == "escalate_human":
        audit = _build_audit_entry(
            diagnosis, action, "escalated",
            0.0, None, None, guardrails_triggered, None, client.dry_run
        )
        return ActionResult(
            payment_id=pid, amount_inr=amount, error_code=error_code,
            action_taken=action, execution_outcome="escalated",
            amount_recovered=0.0, retries_used=0,
            guardrails_triggered=guardrails_triggered,
            audit_entry=audit,
            timestamp=datetime.now(timezone.utc).isoformat(),
        ).to_dict()

    # ── Actions that create a Payment Link ────────────────────────────────
    # notify_customer, retry_immediate, retry_scheduled
    description = _build_link_description(action, amount, pid)

    # Use recovery_message from LLM as the payment link description if available
    if recovery_msg and recovery_msg != "N/A - internal action" and len(recovery_msg) > 10:
        description = recovery_msg[:255]

    api_result = client.create_payment_link(
        amount_inr=amount,
        customer_id=customer_id,
        payment_id=pid,
        description=description,
        sms_notify=(action == "notify_customer"),
        email_notify=False,
        expire_by_hours=48,
    )

    # Determine outcome based on API success
    if api_result["success"]:
        # For live mode: Payment Link created = "notified_pending" (customer must click)
        # For dry-run: same — we track as notified_pending until webhook confirms payment
        outcome = "notified_pending"
        amount_recovered = 0.0  # Only count as recovered when webhook confirms payment

        if action in ("retry_immediate", "retry_scheduled"):
            # Payment Links for retry are also "pending" until customer completes
            outcome = "notified_pending"

        short_url = api_result.get("short_url", "")
        link_id = api_result.get("payment_link_id", "")

        print(
            f"  ✓ Created Payment Link: {link_id} → {short_url} | "
            f"Rs.{amount:,.2f} | {action}"
        )
    else:
        outcome = "failed"
        amount_recovered = 0.0
        short_url = None
        link_id = None
        guardrails_triggered.append("api_call_failed")
        print(f"  ✗ API Error for {pid}: {api_result.get('error')}")

    audit = _build_audit_entry(
        diagnosis, action, outcome, amount_recovered,
        api_result.get("payment_link_id"),
        api_result.get("short_url"),
        guardrails_triggered,
        api_result.get("error"),
        client.dry_run,
    )

    return ActionResult(
        payment_id=pid,
        amount_inr=amount,
        error_code=error_code,
        action_taken=action,
        execution_outcome=outcome,
        amount_recovered=amount_recovered,
        retries_used=1 if action == "retry_immediate" else 0,
        guardrails_triggered=guardrails_triggered,
        audit_entry=audit,
        timestamp=datetime.now(timezone.utc).isoformat(),
    ).to_dict()


# ─── Batch executor ──────────────────────────────────────────────────────────

def execute_batch_live(diagnoses: list[dict], dry_run: bool = True) -> list[dict]:
    """
    Execute all recovery actions via Razorpay API.

    Args:
        diagnoses: Policy-approved diagnosis results
        dry_run: If True, log API calls without hitting Razorpay

    Returns:
        List of ActionResult dicts (same schema as simulated executor)
    """
    client = RazorpayClient.from_env(dry_run=dry_run)

    # Test connectivity before bulk run (skip in dry-run)
    if not dry_run:
        ok = client.test_connection()
        if not ok:
            raise RuntimeError(
                "Razorpay API connection failed.\n"
                "Check RAZORPAY_KEY_ID and RAZORPAY_KEY_SECRET in your .env file.\n"
                "Or run with --dry-run flag."
            )

    results = []
    stats = {
        "notified_pending": 0,
        "blocked_by_guardrail": 0,
        "escalated": 0,
        "failed": 0,
    }
    links_created = 0
    api_errors = 0

    print(f"\n  Running {'DRY-RUN' if dry_run else 'LIVE'} execution...")
    print(f"  {'─' * 60}")

    for i, diag in enumerate(diagnoses):
        pid = diag["payment_id"]
        action = diag["recommended_action"]
        print(f"  {i+1:3d}/{len(diagnoses)}  {pid}  {action:<25s}", end="", flush=True)

        result = _execute_single(diag, client)
        results.append(result)

        outcome = result["execution_outcome"]
        stats[outcome] = stats.get(outcome, 0) + 1

        if outcome == "notified_pending":
            links_created += 1
        if result["guardrails_triggered"] and "api_call_failed" in result["guardrails_triggered"]:
            api_errors += 1

    # ── Summary ───────────────────────────────────────────────────────────
    print(f"\n  {'─' * 60}")
    print(f"  Execution complete ({'DRY-RUN' if dry_run else 'LIVE'}):")
    print(f"    Payment Links created: {links_created}")
    print(f"    Escalated to human:    {stats.get('escalated', 0)}")
    print(f"    Blocked by guardrail:  {stats.get('blocked_by_guardrail', 0)}")
    print(f"    API errors:            {api_errors}")
    print(f"\n  NOTE: 'notified_pending' = Payment Link sent to customer.")
    print(f"  Actual recovery is confirmed via Razorpay webhook when customer pays.")

    # Save
    base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    output_path = os.path.join(base, "data", "action_results_live.json")
    save_json(results, output_path)
    print(f"  ✓ Saved to data/action_results_live.json")

    return results


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Real executor — creates Razorpay Payment Links")
    parser.add_argument("--dry-run", action="store_true", default=True,
                        help="Log API calls without hitting Razorpay (default: True)")
    parser.add_argument("--live", action="store_true",
                        help="Run in LIVE mode (requires RAZORPAY_KEY_ID + SECRET env vars)")
    args = parser.parse_args()

    dry_run = not args.live

    print("=" * 60)
    print(f"  REAL EXECUTOR — {'DRY-RUN' if dry_run else 'LIVE'} mode")
    print("=" * 60)

    base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    input_path = os.path.join(base, "data", "diagnosis_results.json")
    diagnoses = load_json(input_path)
    print(f"  Loaded {len(diagnoses)} policy-approved diagnoses\n")

    execute_batch_live(diagnoses, dry_run=dry_run)
    print("\n  Done.")
