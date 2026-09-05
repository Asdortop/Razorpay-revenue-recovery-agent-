"""
Chunk 4 — Results aggregation + formatted audit report.
Reads original records + action results, produces summary tables,
guardrail compliance report, and sample audit trails.
"""

import sys
import os

# Fix Windows console encoding
if sys.stdout.encoding != 'utf-8':
    sys.stdout.reconfigure(encoding='utf-8')
if sys.stderr.encoding != 'utf-8':
    sys.stderr.reconfigure(encoding='utf-8')

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from datetime import datetime
from schemas.models import save_json, load_json, ERROR_CODES, ACTIONS

# ─── Formatting Helpers ──────────────────────────────────────────────────────

def format_inr(amount: float) -> str:
    """Format amount in Indian numbering system (lakhs, thousands)."""
    if amount < 0:
        return "-" + format_inr(-amount)

    amount = round(amount, 2)
    int_part = int(amount)
    dec_part = f"{amount - int_part:.2f}"[1:]  # .XX

    s = str(int_part)
    if len(s) <= 3:
        return "Rs." + s + dec_part

    # Last 3 digits
    result = s[-3:]
    s = s[:-3]

    # Then groups of 2
    while s:
        result = s[-2:] + "," + result
        s = s[:-2]

    return "Rs." + result + dec_part


# ─── Report Generation ───────────────────────────────────────────────────────

def generate_report(records: list[dict], results: list[dict]) -> dict:
    """Generate full batch report. Prints to console and returns BatchSummary dict."""

    total_records = len(results)
    total_at_risk = sum(r["amount_inr"] for r in results)
    total_recovered = sum(r["amount_recovered"] for r in results)
    recovery_rate = (total_recovered / total_at_risk * 100) if total_at_risk > 0 else 0

    # ── Guardrail stats ───────────────────────────────────────────────
    fraud_blocked = sum(1 for r in results if "fraud_block" in r.get("guardrails_triggered", []))
    max_retry_hit = sum(1 for r in results if "max_retry_limit" in r.get("guardrails_triggered", []))
    dead_instrument_blocked = sum(1 for r in results if "dead_instrument_block" in r.get("guardrails_triggered", []))

    # ── Breakdown by error code ───────────────────────────────────────
    by_error = {}
    for r in results:
        ec = r["error_code"]
        if ec not in by_error:
            by_error[ec] = {
                "count": 0, "amount_at_risk_inr": 0.0,
                "amount_recovered_inr": 0.0, "actions": {}
            }
        by_error[ec]["count"] += 1
        by_error[ec]["amount_at_risk_inr"] += r["amount_inr"]
        by_error[ec]["amount_recovered_inr"] += r["amount_recovered"]
        act = r["action_taken"]
        by_error[ec]["actions"][act] = by_error[ec]["actions"].get(act, 0) + 1

    for ec in by_error:
        risk = by_error[ec]["amount_at_risk_inr"]
        rec = by_error[ec]["amount_recovered_inr"]
        by_error[ec]["recovery_rate_pct"] = round((rec / risk * 100) if risk > 0 else 0, 1)

    # ── Breakdown by action ───────────────────────────────────────────
    by_action = {}
    for action in ACTIONS:
        by_action[action] = {"attempted": 0, "recovered": 0}
    for r in results:
        act = r["action_taken"]
        if act not in by_action:
            by_action[act] = {"attempted": 0, "recovered": 0}
        by_action[act]["attempted"] += 1
        if r["execution_outcome"] == "recovered":
            by_action[act]["recovered"] += 1
    for act in by_action:
        a = by_action[act]["attempted"]
        r = by_action[act]["recovered"]
        by_action[act]["success_rate_pct"] = round((r / a * 100) if a > 0 else 0, 1)

    # ═══════════════════════════════════════════════════════════════════
    #  CONSOLE OUTPUT
    # ═══════════════════════════════════════════════════════════════════

    w = 64

    # ── Header box ────────────────────────────────────────────────────
    print()
    print("=" * w)
    print("   PAYMENT RECOVERY AGENT  -  BATCH RESULTS")
    print("=" * w)
    print(f"   Total Failed Payments:     {total_records}")
    print(f"   Total Amount at Risk:      {format_inr(total_at_risk)}")
    print(f"   Total Amount Recovered:    {format_inr(total_recovered)}")
    print(f"   Recovery Rate:             {recovery_rate:.1f}%")
    print(f"   Fraud-Flagged (Blocked):   {fraud_blocked}")
    print(f"   Max-Retry Guardrail Hit:   {max_retry_hit}")
    print("=" * w)

    # ── Breakdown by error code ───────────────────────────────────────
    print(f"\n{'─' * w}")
    print("   BREAKDOWN BY ERROR CODE")
    print(f"{'─' * w}")
    header = f"  {'Error Code':<36s} {'Cnt':>4s} {'At Risk':>14s} {'Recovered':>14s} {'Rate':>6s}"
    print(header)
    print(f"  {'─'*36} {'─'*4} {'─'*14} {'─'*14} {'─'*6}")

    # Sort by recovery rate descending
    sorted_errors = sorted(by_error.items(), key=lambda x: -x[1]["recovery_rate_pct"])
    for ec, data in sorted_errors:
        print(f"  {ec:<36s} {data['count']:>4d} {format_inr(data['amount_at_risk_inr']):>14s} "
              f"{format_inr(data['amount_recovered_inr']):>14s} {data['recovery_rate_pct']:>5.1f}%")

    # ── Breakdown by action ───────────────────────────────────────────
    print(f"\n{'─' * w}")
    print("   BREAKDOWN BY ACTION")
    print(f"{'─' * w}")
    header = f"  {'Action':<28s} {'Attempted':>10s} {'Recovered':>10s} {'Rate':>8s}"
    print(header)
    print(f"  {'─'*28} {'─'*10} {'─'*10} {'─'*8}")

    for act in ACTIONS:
        data = by_action[act]
        rate_str = f"{data['success_rate_pct']:.1f}%" if data["attempted"] > 0 else "N/A"
        print(f"  {act:<28s} {data['attempted']:>10d} {data['recovered']:>10d} {rate_str:>8s}")

    # ── Guardrail compliance ──────────────────────────────────────────
    print(f"\n{'─' * w}")
    print("   GUARDRAIL COMPLIANCE")
    print(f"{'─' * w}")

    # Check: were any fraud-flagged payments retried?
    fraud_retried = sum(
        1 for r in results
        if r["error_code"] == "payment_risk_check_failed"
        and r["action_taken"] in ("retry_immediate", "retry_scheduled")
    )
    status = "PASS" if fraud_retried == 0 else "FAIL"
    icon = "+" if fraud_retried == 0 else "X"
    print(f"  [{icon}] Fraud-flagged payments auto-retried: {fraud_retried} ({status})")

    status = "enforced" if max_retry_hit > 0 else "PASS (none needed)"
    print(f"  [+] Max retry limit:                   {max_retry_hit} ({status})")

    # Check: were any dead instruments retried?
    dead_retried = sum(
        1 for r in results
        if r["error_code"] in ("card_expired", "debit_instrument_blocked")
        and r["action_taken"] in ("retry_immediate", "retry_scheduled")
    )
    status = "PASS" if dead_retried == 0 else "FAIL"
    icon = "+" if dead_retried == 0 else "X"
    print(f"  [{icon}] Dead instruments retried:           {dead_retried} ({status})")

    print(f"  [+] Dead instruments guardrail applied: {dead_instrument_blocked}")

    # ── Sample audit entries ──────────────────────────────────────────
    print(f"\n{'─' * w}")
    print("   SAMPLE AUDIT TRAILS")
    print(f"{'─' * w}")

    # Find one recovered, one blocked, one failed
    recovered_sample = next((r for r in results if r["execution_outcome"] == "recovered"), None)
    blocked_sample = next((r for r in results if r["execution_outcome"] == "blocked_by_guardrail"), None)
    failed_sample = next((r for r in results if r["execution_outcome"] == "failed"), None)

    samples = [
        ("RECOVERED PAYMENT", recovered_sample),
        ("FRAUD-BLOCKED PAYMENT", blocked_sample),
        ("FAILED RECOVERY", failed_sample),
    ]

    for label, sample in samples:
        if sample:
            print(f"\n  [{label}] {sample['payment_id']} — {format_inr(sample['amount_inr'])}")
            print(f"  {'-' * (w - 4)}")
            # Wrap audit entry for readability
            entry = sample["audit_entry"]
            sentences = entry.split(". ")
            for s in sentences:
                s = s.strip()
                if s:
                    print(f"    {s}")
            print()

    print("=" * w)
    print("   REPORT COMPLETE")
    print("=" * w)

    # ═══════════════════════════════════════════════════════════════════
    #  BUILD BATCH SUMMARY
    # ═══════════════════════════════════════════════════════════════════

    summary = {
        "run_id": f"run_{datetime.now().strftime('%Y%m%d_%H%M%S')}",
        "total_records": total_records,
        "total_amount_at_risk_inr": round(total_at_risk, 2),
        "total_amount_recovered_inr": round(total_recovered, 2),
        "recovery_rate_pct": round(recovery_rate, 1),
        "breakdown_by_error_code": {
            ec: {
                "count": data["count"],
                "amount_at_risk_inr": round(data["amount_at_risk_inr"], 2),
                "amount_recovered_inr": round(data["amount_recovered_inr"], 2),
                "recovery_rate_pct": data["recovery_rate_pct"],
                "actions": data["actions"],
            }
            for ec, data in by_error.items()
        },
        "breakdown_by_action": by_action,
        "guardrail_stats": {
            "fraud_flagged_blocked": fraud_blocked,
            "max_retry_enforced": max_retry_hit,
            "dead_instrument_blocked": dead_instrument_blocked,
            "fraud_retried_violations": fraud_retried,
            "dead_retried_violations": dead_retried,
        },
        "audit_log": results,
    }

    # Save files
    base = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data")
    save_json(summary, os.path.join(base, "batch_summary.json"))
    save_json(results, os.path.join(base, "audit_log.json"))

    return summary


if __name__ == "__main__":
    print("=" * 60)
    print("  CHUNK 4 — Results Aggregation + Audit Report")
    print("=" * 60)

    base = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data")
    records = load_json(os.path.join(base, "failed_payments.json"))
    results = load_json(os.path.join(base, "action_results.json"))
    print(f"  Loaded {len(records)} records + {len(results)} results\n")

    generate_report(records, results)
    print("\n  Done.")
