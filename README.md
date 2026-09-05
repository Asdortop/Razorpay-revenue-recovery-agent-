# ⚡ Razorpay Revive — AI Revenue Recovery Agent

> **Track 03: AI Revenue Recovery** | Razorpay Buildathon 2026

An autonomous AI agent that diagnoses failed payments, selects recovery strategies through a deterministic policy engine, creates real Razorpay Payment Links, and tracks money recovered in real time via webhooks.

---

## The Core Problem

Payment failures cost businesses money every day. Most systems either:
- Blindly retry everything (bad — retrying fraud triggers more flags)
- Do nothing (worse — money just disappears)

**Razorpay Revive** takes a third path: diagnose *why* each payment failed, apply a guardrail-enforced recovery action specific to that failure, and prove that money came back.

---

## Architecture

```
Failed Payments (100 records)
        │
        ▼
┌─────────────────┐      Advisory only
│   LLM Engine    │ ──────────────────────────────┐
│ (Groq / Qwen)   │  root_cause + recommended_action + recovery_message
└─────────────────┘                               │
                                                  ▼
                                  ┌──────────────────────────┐
                                  │    POLICY ENGINE         │  ← Deterministic
                                  │  (6 hard rules)          │  ← LLM cannot bypass
                                  │                          │
                                  │  FRAUD_BLOCK             │
                                  │  DEAD_INSTRUMENT_BLOCK   │
                                  │  CANCELLED_NO_RETRY      │
                                  │  HIGH_VALUE_REVIEW       │
                                  │  CHRONIC_FAILER          │
                                  │  LOW_SUCCESS_RATE        │
                                  └──────────────┬───────────┘
                                                 │  Policy-approved actions only
                                                 ▼
                                  ┌──────────────────────────┐
                                  │   Razorpay API           │
                                  │  Payment Links           │
                                  │  Standard Checkout       │
                                  └──────────────┬───────────┘
                                                 │
                                                 ▼
                                  ┌──────────────────────────┐
                                  │   Webhook Receiver       │
                                  │  payment.captured        │
                                  │  → Recovery Ledger       │
                                  │  → Live Dashboard        │
                                  └──────────────────────────┘
```

> **Critical design principle**: LLMs are never trusted for monetary authorization. The LLM reasons. The Policy Engine decides. This matches Razorpay's own internal architecture for financial systems.

---

## Results (Simulated Batch — 100 Failed Payments)

| Metric | Value |
|--------|-------|
| Total at risk | ₹1,91,350.09 |
| Recovered | ₹40,871.00 |
| **Recovery rate** | **21.4%** |
| LLM success rate | 100% (0 fallbacks) |
| Policy overrides | 17% (guardrails fired) |
| Fraud blocked | 2 payments auto-blocked |
| Dead instruments retried | 0 (guardrail prevented all) |

### Breakdown by Error Code

| Error | Action | Recovery Rate |
|-------|--------|--------------|
| `gateway_technical_error` | retry_immediate | ~70% |
| `bank_technical_error` | retry_immediate | ~60% |
| `payment_timed_out` | retry_scheduled | ~55% |
| `insufficient_funds` | notify_customer | ~18% |
| `authentication_failed` | notify_customer | ~18% |
| `payment_risk_check_failed` | **BLOCKED** | 0% (by design) |
| `card_expired` | notify_customer | ~10% |

---

## Policy Engine — Compliance & Stopping Rules

The Policy Engine is the gatekeeper between the LLM and any real action:

```
Rule 1: FRAUD_BLOCK        — payment_risk_check_failed → no_action. Always.
Rule 2: DEAD_INSTRUMENT    — expired/blocked card → never retry, redirect to notify
Rule 3: CANCELLED_NO_RETRY — customer-cancelled → no immediate retry
Rule 4: HIGH_VALUE_REVIEW  — amount > ₹50,000 → escalate to human
Rule 5: CHRONIC_FAILER     — >5 past failures → escalate, don't retry
Rule 6: LOW_SUCCESS_RATE   — error code with <5% retry success → notify instead
```

**Audit trail**: Every decision (LLM recommendation, policy verdict, action taken, outcome) is logged per-record to `data/audit_log.json`.

---

## Live Webhook Recovery

When a customer completes a Payment Link:

1. Razorpay fires `POST /webhook/razorpay` with a `payment.captured` event
2. Server verifies HMAC-SHA256 signature using `RAZORPAY_WEBHOOK_SECRET`
3. Payment is written to `data/recovery_ledger.json` (idempotent)
4. Dashboard at `http://localhost:5000` updates every 3 seconds

---

## Project Structure

```
razorpay-revive/
│
├── pipeline.py                  # Batch pipeline (simulated execution)
├── pipeline_live.py             # Live pipeline (real Razorpay API)
│
├── schemas/
│   └── models.py                # Shared contract: enums, dataclasses, validation
│
├── data/
│   ├── generate.py              # Synthetic data generator (100 records, seeded)
│   ├── razorpay_adapter.py      # Converts real Razorpay API responses → internal format
│   ├── failed_payments.json     # Input batch
│   ├── diagnosis_results.json   # LLM + policy-approved diagnoses
│   ├── action_results.json      # Simulated execution results
│   ├── action_results_live.json # Real API execution results
│   ├── audit_log.json           # Per-record audit trail
│   ├── batch_summary.json       # Full summary + metrics
│   ├── recovery_ledger.json     # Webhook-confirmed recoveries (live)
│   └── report.html              # Self-contained HTML dashboard
│
├── engine/
│   ├── diagnosis.py             # LLM diagnosis (Groq / Qwen-3.8b)
│   └── policy.py                # Policy Engine — 6 deterministic rules
│
├── simulator/
│   ├── executor.py              # Simulated executor (probability-based)
│   └── real_executor.py         # Real executor (Razorpay Payment Links API)
│
├── reporting/
│   ├── aggregator.py            # Console report + audit trail
│   └── html_report.py           # Dark-theme HTML dashboard generator
│
├── razorpay_client/
│   └── client.py                # Razorpay Payment Links API client (dry-run + live)
│
└── checkout/
    ├── app.py                   # Flask server (checkout + webhook + live stats)
    ├── recovery_ledger.py       # Thread-safe recovery persistence
    ├── simulate_webhook.py      # Local webhook testing tool
    └── templates/
        └── index.html           # Live checkout + recovery dashboard UI
```

---

## Setup

### 1. Install dependencies

```bash
pip install -r requirements.txt
```

### 2. Configure environment

```bash
copy .env.example .env
```

Edit `.env`:

```env
RAZORPAY_KEY_ID=rzp_test_YOUR_KEY_ID
RAZORPAY_KEY_SECRET=YOUR_KEY_SECRET
GROQ_API_KEY=gsk_YOUR_GROQ_KEY
RAZORPAY_WEBHOOK_SECRET=YOUR_WEBHOOK_SECRET   # optional for local testing
```

### 3. Run the batch pipeline (simulated)

```bash
python pipeline.py
```

Outputs:
- Console report with recovery metrics
- `data/report.html` — full HTML dashboard
- `data/audit_log.json` — per-record decisions

---

## Testing Live Checkout

### Start the checkout server

```bash
python checkout/app.py
```

Open **http://localhost:5000**

### Test cards (Razorpay test mode)

| Card Number | Result |
|-------------|--------|
| `4111 1111 1111 1111` | Success |
| `4000 0000 0000 0002` | Insufficient funds |
| UPI: `success@razorpay` | Success |
| OTP: `1234` | Passes |

Expiry: any future date. CVV: any 3 digits.

### Simulate webhook events (no ngrok needed)

```bash
# Simulate 5 recovered payments of Rs.500 each
python checkout/simulate_webhook.py --count 5 --amount 500

# Reset recovery ledger between demos
python checkout/simulate_webhook.py --reset
```

---

## Run Live Pipeline (Real Razorpay Payment Links)

```bash
# Dry-run (logs calls, no real API)
python pipeline_live.py --dry-run --skip-llm

# Live mode (creates real Payment Links)
python pipeline_live.py --live --skip-llm
```

---

## Tech Stack

| Component | Technology |
|-----------|-----------|
| LLM | Groq API (Qwen-3.8b-27b) — free tier |
| LLM client | OpenAI-compatible SDK |
| Policy Engine | Pure Python — deterministic rules |
| Payment API | Razorpay (Orders + Payment Links + Webhooks) |
| Web server | Flask |
| Persistence | JSON files (no database) |
| Frontend | Vanilla HTML/CSS/JS |

---

## Key Design Decisions

**Why a policy engine instead of letting the LLM decide?**

LLMs are probabilistic. Financial decisions must be deterministic. The policy engine enforces hard rules regardless of what the LLM recommends. This mirrors how Razorpay's own fraud and risk systems work internally.

**Why Groq (free tier) over GPT-4?**

Hackathon constraint: zero cost. Qwen-3.8b on Groq achieves 100% valid JSON output with correct action selection. The constraint forced a cleaner architecture — LLM for reasoning, not for decisions.

**Why JSON files instead of a database?**

No database setup required to run the project. The `recovery_ledger.py` module is thread-safe and idempotent — it can be swapped for Postgres/Redis in production by changing one file.

---

## Audit Trail Sample

```json
{
  "payment_id": "pay_00024",
  "error_code": "payment_risk_check_failed",
  "action_taken": "no_action_fraud_flagged",
  "execution_outcome": "blocked_by_guardrail",
  "guardrails_triggered": ["fraud_block"],
  "audit_entry": "DIAGNOSIS: payment_risk_check_failed (customer). ACTION: no_action_fraud_flagged. REASONING: Fraud risk detected — policy mandates no action. OUTCOME: blocked_by_guardrail. BLOCKED BY GUARDRAIL. GUARDRAILS: fraud_block."
}
```

---

*Built for Razorpay Buildathon 2026 — Track 03: AI Revenue Recovery*
