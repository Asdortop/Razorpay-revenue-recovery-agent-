"""
HTML Report Generator — Creates a visual dashboard from batch_summary.json.
Reads the JSON outputs and generates a self-contained HTML report.

Usage:
    python reporting/html_report.py          (after running pipeline.py)
    OR: Called automatically from pipeline.py
"""

import sys
import os
import json

if sys.stdout.encoding != 'utf-8':
    sys.stdout.reconfigure(encoding='utf-8')

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from schemas.models import load_json


def generate_html_report(summary: dict, diagnoses: list[dict] = None) -> str:
    """Generate an HTML report from the batch summary."""

    total = summary["total_records"]
    at_risk = summary["total_amount_at_risk_inr"]
    recovered = summary["total_amount_recovered_inr"]
    rate = summary["recovery_rate_pct"]
    guardrails = summary["guardrail_stats"]

    # Build error code table rows
    error_rows = ""
    sorted_errors = sorted(
        summary["breakdown_by_error_code"].items(),
        key=lambda x: -x[1]["recovery_rate_pct"]
    )
    for ec, data in sorted_errors:
        rec_rate = data["recovery_rate_pct"]
        bar_color = "#10b981" if rec_rate > 50 else "#f59e0b" if rec_rate > 0 else "#6b7280"
        error_rows += f"""
        <tr>
            <td><code>{ec}</code></td>
            <td class="num">{data['count']}</td>
            <td class="num">₹{data['amount_at_risk_inr']:,.0f}</td>
            <td class="num">₹{data['amount_recovered_inr']:,.0f}</td>
            <td>
                <div class="bar-cell">
                    <div class="bar" style="width:{min(rec_rate, 100)}%;background:{bar_color}"></div>
                    <span>{rec_rate:.1f}%</span>
                </div>
            </td>
        </tr>"""

    # Build action table rows
    action_rows = ""
    for act, data in summary["breakdown_by_action"].items():
        if data["attempted"] == 0:
            continue
        rec_rate = data["success_rate_pct"]
        bar_color = "#10b981" if rec_rate > 50 else "#f59e0b" if rec_rate > 0 else "#6b7280"
        label = act.replace("_", " ").title()
        action_rows += f"""
        <tr>
            <td>{label}</td>
            <td class="num">{data['attempted']}</td>
            <td class="num">{data['recovered']}</td>
            <td>
                <div class="bar-cell">
                    <div class="bar" style="width:{min(rec_rate, 100)}%;background:{bar_color}"></div>
                    <span>{rec_rate:.1f}%</span>
                </div>
            </td>
        </tr>"""

    # Build audit samples
    audit_entries = summary.get("audit_log", [])
    recovered_sample = next((r for r in audit_entries if r["execution_outcome"] == "recovered"), None)
    blocked_sample = next((r for r in audit_entries if r["execution_outcome"] == "blocked_by_guardrail"), None)

    # Build recovery message samples from diagnoses
    message_samples_html = ""
    if diagnoses:
        seen_errors = set()
        msg_count = 0
        for d in diagnoses:
            msg = d.get("recovery_message", "")
            if msg and msg != "N/A - internal action" and d["error_code"] not in seen_errors and msg_count < 5:
                seen_errors.add(d["error_code"])
                msg_count += 1
                message_samples_html += f"""
                <div class="message-card">
                    <div class="msg-header">
                        <span class="msg-patient">{d['payment_id']}</span>
                        <span class="msg-error"><code>{d['error_code']}</code></span>
                        <span class="msg-amount">₹{d['amount_inr']:,.0f}</span>
                    </div>
                    <div class="msg-body">💬 {msg}</div>
                </div>"""

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Payment Recovery Agent — Results Dashboard</title>
<style>
    @import url('https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600;700;800&display=swap');

    * {{ margin: 0; padding: 0; box-sizing: border-box; }}

    body {{
        font-family: 'Inter', -apple-system, sans-serif;
        background: #0a0e17;
        color: #e2e8f0;
        min-height: 100vh;
        padding: 2rem;
    }}

    .container {{ max-width: 1100px; margin: 0 auto; }}

    /* Header */
    .header {{
        text-align: center;
        margin-bottom: 2.5rem;
        padding: 2rem;
        background: linear-gradient(135deg, rgba(59, 130, 246, 0.1), rgba(139, 92, 246, 0.1));
        border: 1px solid rgba(99, 102, 241, 0.2);
        border-radius: 16px;
    }}
    .header h1 {{
        font-size: 1.8rem;
        font-weight: 800;
        background: linear-gradient(135deg, #60a5fa, #a78bfa);
        -webkit-background-clip: text;
        -webkit-text-fill-color: transparent;
        margin-bottom: 0.3rem;
    }}
    .header .sub {{
        color: #94a3b8;
        font-size: 0.9rem;
        font-weight: 400;
    }}

    /* KPI Cards */
    .kpi-grid {{
        display: grid;
        grid-template-columns: repeat(auto-fit, minmax(200px, 1fr));
        gap: 1rem;
        margin-bottom: 2rem;
    }}
    .kpi {{
        background: rgba(30, 41, 59, 0.7);
        border: 1px solid rgba(148, 163, 184, 0.1);
        border-radius: 12px;
        padding: 1.5rem;
        text-align: center;
        transition: transform 0.2s, box-shadow 0.2s;
    }}
    .kpi:hover {{
        transform: translateY(-2px);
        box-shadow: 0 8px 24px rgba(0,0,0,0.3);
    }}
    .kpi .value {{
        font-size: 2rem;
        font-weight: 800;
        margin-bottom: 0.3rem;
    }}
    .kpi .label {{
        font-size: 0.75rem;
        text-transform: uppercase;
        letter-spacing: 0.05em;
        color: #94a3b8;
        font-weight: 500;
    }}
    .kpi.green .value {{ color: #10b981; }}
    .kpi.blue .value {{ color: #60a5fa; }}
    .kpi.amber .value {{ color: #f59e0b; }}
    .kpi.red .value {{ color: #ef4444; }}
    .kpi.purple .value {{ color: #a78bfa; }}

    /* Sections */
    .section {{
        background: rgba(30, 41, 59, 0.5);
        border: 1px solid rgba(148, 163, 184, 0.1);
        border-radius: 12px;
        padding: 1.5rem;
        margin-bottom: 1.5rem;
    }}
    .section h2 {{
        font-size: 1rem;
        font-weight: 700;
        text-transform: uppercase;
        letter-spacing: 0.08em;
        color: #94a3b8;
        margin-bottom: 1rem;
        padding-bottom: 0.5rem;
        border-bottom: 1px solid rgba(148, 163, 184, 0.1);
    }}

    /* Tables */
    table {{
        width: 100%;
        border-collapse: collapse;
        font-size: 0.85rem;
    }}
    th {{
        text-align: left;
        font-size: 0.7rem;
        text-transform: uppercase;
        letter-spacing: 0.06em;
        color: #64748b;
        padding: 0.6rem 0.8rem;
        border-bottom: 1px solid rgba(148, 163, 184, 0.15);
        font-weight: 600;
    }}
    td {{
        padding: 0.6rem 0.8rem;
        border-bottom: 1px solid rgba(148, 163, 184, 0.06);
    }}
    td.num {{ text-align: right; font-variant-numeric: tabular-nums; }}
    tr:hover td {{ background: rgba(99, 102, 241, 0.05); }}
    code {{
        background: rgba(99, 102, 241, 0.15);
        padding: 2px 6px;
        border-radius: 4px;
        font-size: 0.8rem;
        color: #a5b4fc;
    }}

    /* Bar chart cells */
    .bar-cell {{
        display: flex;
        align-items: center;
        gap: 8px;
    }}
    .bar {{
        height: 8px;
        border-radius: 4px;
        min-width: 2px;
        transition: width 0.6s ease;
    }}
    .bar-cell span {{
        font-size: 0.8rem;
        font-weight: 600;
        min-width: 48px;
    }}

    /* Guardrail badges */
    .guardrail-grid {{
        display: grid;
        grid-template-columns: repeat(auto-fit, minmax(240px, 1fr));
        gap: 0.8rem;
    }}
    .guardrail {{
        display: flex;
        align-items: center;
        gap: 0.8rem;
        padding: 0.8rem 1rem;
        border-radius: 8px;
        background: rgba(16, 185, 129, 0.08);
        border: 1px solid rgba(16, 185, 129, 0.2);
    }}
    .guardrail.fail {{
        background: rgba(239, 68, 68, 0.08);
        border-color: rgba(239, 68, 68, 0.2);
    }}
    .guardrail .icon {{ font-size: 1.4rem; }}
    .guardrail .text {{
        font-size: 0.85rem;
        font-weight: 500;
    }}

    /* Audit trail cards */
    .audit-card {{
        background: rgba(15, 23, 42, 0.5);
        border: 1px solid rgba(148, 163, 184, 0.1);
        border-radius: 10px;
        padding: 1.2rem;
        margin-bottom: 1rem;
        font-size: 0.85rem;
        line-height: 1.6;
    }}
    .audit-card .audit-header {{
        display: flex;
        justify-content: space-between;
        align-items: center;
        margin-bottom: 0.8rem;
    }}
    .audit-card .badge {{
        padding: 3px 10px;
        border-radius: 20px;
        font-size: 0.7rem;
        font-weight: 700;
        text-transform: uppercase;
        letter-spacing: 0.05em;
    }}
    .badge.recovered {{ background: rgba(16, 185, 129, 0.15); color: #10b981; }}
    .badge.blocked {{ background: rgba(239, 68, 68, 0.15); color: #ef4444; }}
    .audit-field {{ color: #64748b; }}

    /* Recovery message cards */
    .message-card {{
        background: rgba(15, 23, 42, 0.5);
        border: 1px solid rgba(148, 163, 184, 0.1);
        border-radius: 10px;
        padding: 1rem 1.2rem;
        margin-bottom: 0.8rem;
    }}
    .msg-header {{
        display: flex;
        gap: 1rem;
        align-items: center;
        margin-bottom: 0.5rem;
        font-size: 0.8rem;
    }}
    .msg-patient {{ font-weight: 700; color: #60a5fa; }}
    .msg-amount {{ color: #f59e0b; font-weight: 600; margin-left: auto; }}
    .msg-body {{
        background: rgba(16, 185, 129, 0.06);
        border: 1px solid rgba(16, 185, 129, 0.15);
        border-radius: 8px;
        padding: 0.8rem 1rem;
        font-size: 0.85rem;
        line-height: 1.5;
        color: #d1d5db;
    }}

    /* Footer */
    .footer {{
        text-align: center;
        color: #475569;
        font-size: 0.75rem;
        margin-top: 2rem;
        padding-top: 1rem;
        border-top: 1px solid rgba(148, 163, 184, 0.1);
    }}
</style>
</head>
<body>
<div class="container">

    <div class="header">
        <h1>Payment Recovery Agent</h1>
        <p class="sub">Track 03 — AI Revenue Recovery | Razorpay Buildathon 2026</p>
    </div>

    <!-- KPI Cards -->
    <div class="kpi-grid">
        <div class="kpi blue">
            <div class="value">{total}</div>
            <div class="label">Failed Payments</div>
        </div>
        <div class="kpi amber">
            <div class="value">₹{at_risk:,.0f}</div>
            <div class="label">Amount at Risk</div>
        </div>
        <div class="kpi green">
            <div class="value">₹{recovered:,.0f}</div>
            <div class="label">Amount Recovered</div>
        </div>
        <div class="kpi purple">
            <div class="value">{rate:.1f}%</div>
            <div class="label">Recovery Rate</div>
        </div>
        <div class="kpi red">
            <div class="value">{guardrails['fraud_flagged_blocked']}</div>
            <div class="label">Fraud Blocked</div>
        </div>
    </div>

    <!-- Breakdown by Error Code -->
    <div class="section">
        <h2>Recovery by Error Code</h2>
        <table>
            <thead>
                <tr>
                    <th>Error Code</th>
                    <th style="text-align:right">Count</th>
                    <th style="text-align:right">At Risk</th>
                    <th style="text-align:right">Recovered</th>
                    <th>Recovery Rate</th>
                </tr>
            </thead>
            <tbody>
                {error_rows}
            </tbody>
        </table>
    </div>

    <!-- Breakdown by Action -->
    <div class="section">
        <h2>Action Effectiveness</h2>
        <table>
            <thead>
                <tr>
                    <th>Action</th>
                    <th style="text-align:right">Attempted</th>
                    <th style="text-align:right">Recovered</th>
                    <th>Success Rate</th>
                </tr>
            </thead>
            <tbody>
                {action_rows}
            </tbody>
        </table>
    </div>

    <!-- Guardrail Compliance -->
    <div class="section">
        <h2>Guardrail Compliance</h2>
        <div class="guardrail-grid">
            <div class="guardrail {'fail' if guardrails['fraud_retried_violations'] > 0 else ''}">
                <span class="icon">{'❌' if guardrails['fraud_retried_violations'] > 0 else '✅'}</span>
                <span class="text">Fraud payments never retried</span>
            </div>
            <div class="guardrail">
                <span class="icon">✅</span>
                <span class="text">Max retry limit enforced ({guardrails['max_retry_enforced']})</span>
            </div>
            <div class="guardrail {'fail' if guardrails['dead_retried_violations'] > 0 else ''}">
                <span class="icon">{'❌' if guardrails['dead_retried_violations'] > 0 else '✅'}</span>
                <span class="text">Dead instruments never retried</span>
            </div>
            <div class="guardrail">
                <span class="icon">🛡️</span>
                <span class="text">{guardrails['fraud_flagged_blocked']} fraud-flagged payments blocked</span>
            </div>
        </div>
    </div>

    <!-- Recovery Messages -->
    {"" if not message_samples_html else f'''
    <div class="section">
        <h2>Personalized Recovery Messages (AI-Generated)</h2>
        {message_samples_html}
    </div>
    '''}

    <!-- Sample Audit Trails -->
    <div class="section">
        <h2>Sample Audit Trails</h2>
        {"" if not recovered_sample else f'''
        <div class="audit-card">
            <div class="audit-header">
                <span><strong>{recovered_sample['payment_id']}</strong> — ₹{recovered_sample['amount_inr']:,.0f}</span>
                <span class="badge recovered">Recovered</span>
            </div>
            <span class="audit-field">Error:</span> <code>{recovered_sample['error_code']}</code><br>
            <span class="audit-field">Action:</span> {recovered_sample['action_taken'].replace('_', ' ').title()}<br>
            <span class="audit-field">Retries:</span> {recovered_sample['retries_used']}<br>
            <span class="audit-field">Trail:</span> {recovered_sample['audit_entry'][:300]}
        </div>
        '''}
        {"" if not blocked_sample else f'''
        <div class="audit-card">
            <div class="audit-header">
                <span><strong>{blocked_sample['payment_id']}</strong> — ₹{blocked_sample['amount_inr']:,.0f}</span>
                <span class="badge blocked">Fraud Blocked</span>
            </div>
            <span class="audit-field">Error:</span> <code>{blocked_sample['error_code']}</code><br>
            <span class="audit-field">Action:</span> {blocked_sample['action_taken'].replace('_', ' ').title()}<br>
            <span class="audit-field">Guardrails:</span> {', '.join(blocked_sample.get('guardrails_triggered', []))}<br>
            <span class="audit-field">Trail:</span> {blocked_sample['audit_entry'][:300]}
        </div>
        '''}
    </div>

    <div class="footer">
        Payment Recovery Agent — Built for Razorpay Buildathon 2026 | Track 03: AI Revenue Recovery
    </div>

</div>
</body>
</html>"""

    return html


def build_html_report():
    """Load data and generate HTML report file."""
    base = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data")

    summary = load_json(os.path.join(base, "batch_summary.json"))

    # Try to load diagnoses for recovery messages
    diagnoses = None
    diag_path = os.path.join(base, "diagnosis_results.json")
    if os.path.exists(diag_path):
        diagnoses = load_json(diag_path)

    html = generate_html_report(summary, diagnoses)

    output_path = os.path.join(base, "report.html")
    with open(output_path, "w", encoding="utf-8") as f:
        f.write(html)

    print(f"  ✓ Saved HTML report to {output_path}")
    return output_path


if __name__ == "__main__":
    build_html_report()
