"""
Live Pipeline — Approach C

Uses real Razorpay Payment Links API instead of simulation.
Runs all the same steps as pipeline.py but replaces the executor.

Usage:
    python pipeline_live.py --dry-run         # Test without Razorpay keys
    python pipeline_live.py --live            # Real API (needs .env or env vars)
    python pipeline_live.py --live --skip-llm # Use existing diagnosis_results.json

Architecture (same as pipeline.py):
    Generate → Diagnose (LLM) → Policy Engine → REAL Execute → Report → Dashboard

CRITICAL: LLMs are NEVER trusted for monetary authorization.
"""

import sys
import os
import argparse

if sys.stdout.encoding != 'utf-8':
    sys.stdout.reconfigure(encoding='utf-8')
if sys.stderr.encoding != 'utf-8':
    sys.stderr.reconfigure(encoding='utf-8')

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# Load .env file if it exists
_env_file = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")
if os.path.exists(_env_file):
    with open(_env_file, "r") as _f:
        for _line in _f:
            _line = _line.strip()
            if _line and not _line.startswith("#") and "=" in _line:
                _k, _v = _line.split("=", 1)
                os.environ.setdefault(_k.strip(), _v.strip())
    print(f"  Loaded .env")

from data.generate import generate_failed_payments
from engine.diagnosis import diagnose_batch
from engine.policy import apply_policy_batch
from simulator.real_executor import execute_batch_live
from reporting.aggregator import generate_report
from reporting.html_report import generate_html_report
from schemas.models import load_json, save_json


def main():
    parser = argparse.ArgumentParser(
        description="Payment Recovery Agent — Live Pipeline (Approach C)"
    )
    parser.add_argument("--dry-run", action="store_true", default=True,
                        help="Run executor in dry-run mode (no real API calls)")
    parser.add_argument("--live", action="store_true",
                        help="Run in LIVE mode — creates real Razorpay Payment Links")
    parser.add_argument("--skip-llm", action="store_true",
                        help="Skip LLM diagnosis — reuse existing diagnosis_results.json")
    parser.add_argument("--n", type=int, default=100,
                        help="Number of payment records to generate (default: 100)")
    args = parser.parse_args()

    dry_run = not args.live

    print()
    print("=" * 64)
    print("   PAYMENT RECOVERY AGENT — LIVE PIPELINE (Approach C)")
    print("   Track 03: AI Revenue Recovery | Razorpay Buildathon 2026")
    print("=" * 64)
    print()
    print(f"   Mode: {'DRY-RUN (no real API calls)' if dry_run else '⚡ LIVE — Real Razorpay API'}")
    print()
    print("   Architecture:")
    print("   ┌──────────┐   ┌─────────────┐   ┌───────────────────┐")
    print("   │ LLM      │──▶│ POLICY      │──▶│ RAZORPAY          │")
    print("   │ Reasoning │   │ ENGINE      │   │ PAYMENT LINKS API │")
    print("   └──────────┘   └─────────────┘   └───────────────────┘")
    print("   Recommends      Approves/Blocks   Creates real links")
    print("   actions         via hard rules    for customer recovery")
    print()
    print("   ⚠ LLMs are NEVER trusted for monetary authorization.")
    if dry_run:
        print("   ℹ DRY-RUN: All API calls are logged, not executed.")
    else:
        print("   ⚡ LIVE: Real Razorpay Payment Links will be created!")

    base = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")

    # Step 1: Generate data
    print("\n" + "─" * 64)
    print("  [1/5] GENERATING FAILED PAYMENT RECORDS")
    print("─" * 64)
    records = generate_failed_payments(n=args.n, seed=42)

    # Step 2: LLM Diagnosis
    if args.skip_llm:
        print("\n" + "─" * 64)
        print("  [2/5] SKIPPING LLM — Loading existing diagnosis_results.json")
        print("─" * 64)
        diag_path = os.path.join(base, "diagnosis_results.json")
        if not os.path.exists(diag_path):
            print("  ✗ diagnosis_results.json not found. Run without --skip-llm first.")
            sys.exit(1)
        policy_reviewed = load_json(diag_path)
        print(f"  ✓ Loaded {len(policy_reviewed)} existing diagnoses")
    else:
        print("\n" + "─" * 64)
        print("  [2/5] LLM DIAGNOSIS — Reasoning + Action Recommendation")
        print("        (LLM output is advisory only — policy engine decides)")
        print("─" * 64)
        diagnoses = diagnose_batch(records)

        # Step 3: Policy Engine
        print("\n" + "─" * 64)
        print("  [3/5] POLICY ENGINE — Deterministic Approval / Override")
        print("─" * 64)
        policy_reviewed = apply_policy_batch(diagnoses, records)
        save_json(policy_reviewed, os.path.join(base, "diagnosis_results.json"))

    # Step 4: Real execution (Razorpay Payment Links)
    print("\n" + "─" * 64)
    print(f"  [4/5] REAL EXECUTION — Creating Razorpay Payment Links")
    print(f"        Mode: {'DRY-RUN' if dry_run else 'LIVE'}")
    print("─" * 64)
    results = execute_batch_live(policy_reviewed, dry_run=dry_run)

    # Step 5: Reports
    print("\n" + "─" * 64)
    print("  [5/5] GENERATING REPORTS")
    print("─" * 64)
    summary = generate_report(records, results)

    diag_data = load_json(os.path.join(base, "diagnosis_results.json"))
    html = generate_html_report(summary, diag_data)
    html_path = os.path.join(base, "report_live.html")
    with open(html_path, "w", encoding="utf-8") as f:
        f.write(html)
    print(f"  ✓ Saved HTML dashboard to data/report_live.html")

    print(f"\n  Output files:")
    print(f"    data/diagnosis_results.json   — Policy-approved diagnoses")
    print(f"    data/action_results_live.json — {'DRY-RUN' if dry_run else 'LIVE'} execution results")
    print(f"    data/report_live.html         — HTML dashboard")

    if dry_run:
        print()
        print("  ─" * 32)
        print("  To run in LIVE mode with real Razorpay Payment Links:")
        print("    1. Add your keys to .env (see .env.example)")
        print("    2. Run: python pipeline_live.py --live --skip-llm")
        print("  ─" * 32)

    print("\n" + "=" * 64)
    print("   LIVE PIPELINE COMPLETE")
    print("=" * 64)
    print()


if __name__ == "__main__":
    main()
