# Razorpay Payment Recovery Agent

AI agent that diagnoses failed payments, selects optimal recovery strategies, and reports measured results with a full audit trail.

**Track 03 — AI Revenue Recovery** | Razorpay Buildathon 2026

## Quick Start

```bash
pip install -r requirements.txt
export XAI_API_KEY="your-grok-api-key"   # or set in .env
python pipeline.py
```

## What It Does

1. **Ingests** 100 failed payment records with real Razorpay error codes
2. **Diagnoses** root cause for each failure using AI (Grok)
3. **Selects** recovery action from 5 bounded strategies
4. **Simulates** execution with realistic success probabilities
5. **Reports** measured money recovered, breakdown by error type, guardrail compliance, and full per-record audit trail

## Guardrails

- Fraud-flagged payments (`payment_risk_check_failed`) are **never** auto-retried
- Max 2 retries per payment
- Dead instruments (expired/blocked cards) are never retried

## Project Structure

```
├── pipeline.py              # End-to-end runner
├── schemas/models.py        # Shared contract (enums, types, validation)
├── data/generate.py         # Synthetic data generator
├── engine/diagnosis.py      # LLM-powered diagnosis + action selection
├── simulator/executor.py    # Execution simulation + guardrails
└── reporting/aggregator.py  # Results aggregation + audit report
```
