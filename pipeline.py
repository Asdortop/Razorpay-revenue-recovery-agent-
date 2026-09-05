"""
Payment Recovery Agent — End-to-end pipeline.

Architecture: Generate → Diagnose (LLM) → Policy Engine → Execute → Report → Dashboard

CRITICAL: LLMs are NEVER trusted for monetary authorization.
The LLM recommends actions. The Policy Engine approves/overrides.
Only policy-approved actions reach execution.

Usage:
    python pipeline.py
    
Set GROQ_API_KEY env variable for LLM diagnosis (optional — falls back to rules).
"""

import sys
import os

# Fix Windows console encoding
if sys.stdout.encoding != 'utf-8':
    sys.stdout.reconfigure(encoding='utf-8')
if sys.stderr.encoding != 'utf-8':
    sys.stderr.reconfigure(encoding='utf-8')

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from data.generate import generate_failed_payments
from data.razorpay_adapter import convert_batch, get_sample_responses
from engine.diagnosis import diagnose_batch
from engine.policy import apply_policy_batch
from simulator.executor import execute_batch
from reporting.aggregator import generate_report
from reporting.html_report import generate_html_report
from schemas.models import load_json, save_json


def main():
    print()
    print("=" * 64)
    print("   PAYMENT RECOVERY AGENT — PIPELINE")
    print("   Track 03: AI Revenue Recovery | Razorpay Buildathon 2026")
    print("=" * 64)
    print()
    print("   Architecture:")
    print("   ┌──────────┐   ┌─────────────┐   ┌───────────────┐")
    print("   │ LLM      │──▶│ POLICY      │──▶│ EXECUTION     │")
    print("   │ Reasoning │   │ ENGINE      │   │ (Bounded)     │")
    print("   └──────────┘   └─────────────┘   └───────────────┘")
    print("   Recommends      Approves/Blocks   Only approved")
    print("   actions         via hard rules    actions execute")
    print()
    print("   ⚠ LLMs are NEVER trusted for monetary authorization.")

    base = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")

    # Step 0: Demo Razorpay API adapter
    print("\n" + "─" * 64)
    print("  [0/6] RAZORPAY API ADAPTER DEMO")
    print("─" * 64)
    rz_samples = get_sample_responses()
    rz_records = convert_batch(rz_samples)
    print(f"  Converted {len(rz_records)} real Razorpay API responses → FailureRecords")
    for r in rz_records:
        print(f"    {r['payment_id']:20s} | Rs.{r['amount_inr']:>10,.2f} | {r['error_code']}")
    save_json(rz_records, os.path.join(base, "razorpay_sample_records.json"))
    print(f"  ✓ Adapter works with real Razorpay format")

    # Step 1: Generate synthetic data
    print("\n" + "─" * 64)
    print("  [1/6] GENERATING FAILED PAYMENT RECORDS")
    print("─" * 64)
    records = generate_failed_payments(n=100, seed=42)

    # Step 2: LLM Diagnosis (reasoning only — NOT trusted for decisions)
    print("\n" + "─" * 64)
    print("  [2/6] LLM DIAGNOSIS — Reasoning + Action Recommendation")
    print("        (LLM output is advisory only — policy engine decides)")
    print("─" * 64)
    diagnoses = diagnose_batch(records)

    # Step 3: POLICY ENGINE — Deterministic gatekeeper
    print("\n" + "─" * 64)
    print("  [3/6] POLICY ENGINE — Deterministic Approval / Override")
    print("        (Hard rules enforce compliance — LLM cannot bypass)")
    print("─" * 64)
    policy_reviewed = apply_policy_batch(diagnoses, records)

    # Save policy-reviewed diagnoses
    save_json(policy_reviewed, os.path.join(base, "diagnosis_results.json"))
    print(f"  ✓ Saved policy-reviewed diagnoses")

    # Step 4: Execute (only policy-approved actions)
    print("\n" + "─" * 64)
    print("  [4/6] EXECUTING RECOVERY ACTIONS (Policy-Approved Only)")
    print("─" * 64)
    results = execute_batch(policy_reviewed, seed=42)

    # Step 5: Generate console report
    print("\n" + "─" * 64)
    print("  [5/6] GENERATING RESULTS REPORT")
    print("─" * 64)
    summary = generate_report(records, results)

    # Step 6: Generate HTML dashboard
    print("\n" + "─" * 64)
    print("  [6/6] GENERATING HTML DASHBOARD")
    print("─" * 64)
    diag_data = load_json(os.path.join(base, "diagnosis_results.json"))
    html = generate_html_report(summary, diag_data)
    html_path = os.path.join(base, "report.html")
    with open(html_path, "w", encoding="utf-8") as f:
        f.write(html)
    print(f"  ✓ Saved HTML dashboard to data/report.html")

    print(f"\n  Output files:")
    print(f"    data/failed_payments.json        — {len(records)} input records")
    print(f"    data/razorpay_sample_records.json — {len(rz_records)} Razorpay API demo")
    print(f"    data/diagnosis_results.json       — {len(policy_reviewed)} policy-reviewed diagnoses")
    print(f"    data/action_results.json          — {len(results)} execution results")
    print(f"    data/batch_summary.json           — full summary + audit log")
    print(f"    data/audit_log.json               — per-record audit trail")
    print(f"    data/report.html                  — visual HTML dashboard")

    print("\n" + "=" * 64)
    print("   PIPELINE COMPLETE")
    print("=" * 64)
    print()


if __name__ == "__main__":
    main()
