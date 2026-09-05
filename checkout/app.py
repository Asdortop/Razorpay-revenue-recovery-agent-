"""
Razorpay Standard Web Checkout — Flask Backend
===============================================
Endpoints:
  GET  /                      -> Checkout demo page (live recovery counter)
  POST /api/create-order      -> Creates a Razorpay order, returns order_id
  POST /api/verify-payment    -> Verifies HMAC-SHA256 signature after checkout
  POST /webhook/razorpay      -> Receives Razorpay payment events, updates ledger
  GET  /api/recovery-stats    -> Live recovery stats (polled by frontend)
  GET  /api/health            -> Health check

Security rules:
  - KEY_SECRET never leaves this file (never sent to frontend)
  - Webhook signature verified with WEBHOOK_SECRET (separate from KEY_SECRET)
  - Both signatures use HMAC-SHA256 with constant-time compare
  - Amount validated >= 100 paise before calling Razorpay
"""

import os
import sys
import hmac
import hashlib
import uuid
import json

# Fix Windows console encoding (cp1252 can't handle Unicode symbols)
if sys.stdout.encoding and sys.stdout.encoding.lower() != 'utf-8':
    sys.stdout.reconfigure(encoding='utf-8')
if sys.stderr.encoding and sys.stderr.encoding.lower() != 'utf-8':
    sys.stderr.reconfigure(encoding='utf-8')

import razorpay
from flask import Flask, request, jsonify, render_template
from dotenv import load_dotenv

# ─── Load environment variables ───────────────────────────────────────────────
# Searches current dir and all parent dirs for .env
load_dotenv()

KEY_ID          = os.environ.get("RAZORPAY_KEY_ID", "")
KEY_SECRET      = os.environ.get("RAZORPAY_KEY_SECRET", "")
WEBHOOK_SECRET  = os.environ.get("RAZORPAY_WEBHOOK_SECRET", "")
FLASK_SECRET    = os.environ.get("FLASK_SECRET_KEY", "dev-secret-change-me")

if not KEY_ID or not KEY_SECRET:
    raise EnvironmentError(
        "RAZORPAY_KEY_ID and RAZORPAY_KEY_SECRET must be set in your .env file.\n"
        "Copy .env.example -> .env and fill in your credentials."
    )

# ─── Razorpay client ─────────────────────────────────────────────────────────
rz_client = razorpay.Client(auth=(KEY_ID, KEY_SECRET))
rz_client.set_app_details({"title": "Razorpay-Revive", "version": "1.0"})

# ─── Flask app ────────────────────────────────────────────────────────────────
app = Flask(__name__)
app.secret_key = FLASK_SECRET

# ─── Recovery Ledger (lazy import to avoid circular issues) ───────────────────
import sys as _sys
import os as _os
_sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))
from recovery_ledger import record_payment, get_stats


# =============================================================================
# ROUTES
# =============================================================================

@app.get("/")
def index():
    """Serve the checkout + live recovery dashboard page."""
    return render_template("index.html", razorpay_key_id=KEY_ID)


# ─── Create Order ─────────────────────────────────────────────────────────────

@app.post("/api/create-order")
def create_order():
    """
    Creates a Razorpay order.

    Request JSON:
        { "amount": <int, paise>, "currency": "INR" }

    Response JSON:
        { "order_id": "...", "amount": <int>, "currency": "INR" }

    Errors:
        400 -- amount < 100 paise or missing fields
        500 -- Razorpay API error
    """
    data     = request.get_json(silent=True) or {}
    amount   = data.get("amount")
    currency = data.get("currency", "INR")
    receipt  = data.get("receipt", f"rcpt_{uuid.uuid4().hex[:10]}")

    if amount is None:
        return jsonify({"error": "amount is required"}), 400
    try:
        amount = int(amount)
    except (ValueError, TypeError):
        return jsonify({"error": "amount must be an integer (paise)"}), 400
    if amount < 100:
        return jsonify({"error": "amount must be at least 100 paise (Rs.1.00)"}), 400

    try:
        order = rz_client.order.create({
            "amount":          amount,
            "currency":        currency,
            "receipt":         receipt,
            "payment_capture": 1,
        })
        print(f"  [Order] Created: {order['id']} | Rs.{amount/100:,.2f}")
        return jsonify({
            "order_id": order["id"],
            "amount":   order["amount"],
            "currency": order["currency"],
        }), 200

    except razorpay.errors.BadRequestError as e:
        return jsonify({"error": f"Razorpay bad request: {str(e)}"}), 400
    except razorpay.errors.ServerError as e:
        return jsonify({"error": f"Razorpay server error: {str(e)}"}), 500
    except Exception as e:
        return jsonify({"error": f"Unexpected error: {str(e)}"}), 500


# ─── Verify Payment (after checkout modal) ────────────────────────────────────

@app.post("/api/verify-payment")
def verify_payment():
    """
    Verifies Razorpay payment signature using HMAC-SHA256.
    Called by the frontend after the checkout modal succeeds.

    Also records the payment in the recovery ledger.
    """
    data = request.get_json(silent=True) or {}

    order_id   = data.get("razorpay_order_id")
    payment_id = data.get("razorpay_payment_id")
    signature  = data.get("razorpay_signature")
    amount_paise = data.get("amount_paise", 0)

    if not all([order_id, payment_id, signature]):
        return jsonify({
            "status":  "error",
            "message": "Missing required fields: razorpay_order_id, razorpay_payment_id, razorpay_signature",
        }), 400

    # HMAC-SHA256 verification
    # Razorpay spec: HMAC-SHA256(order_id + "|" + payment_id, KEY_SECRET)
    body = f"{order_id}|{payment_id}"
    expected = hmac.new(
        KEY_SECRET.encode("utf-8"),
        msg=body.encode("utf-8"),
        digestmod=hashlib.sha256,
    ).hexdigest()

    if not hmac.compare_digest(expected, signature):
        return jsonify({
            "status":  "error",
            "message": "Signature mismatch -- payment not verified.",
        }), 400

    # Signature valid -- record in recovery ledger
    stats = record_payment(
        payment_id=payment_id,
        order_id=order_id,
        amount_paise=int(amount_paise) if amount_paise else 0,
        event="payment.verified_checkout",
        raw_payload=data,
    )

    print(f"  [Verify] Payment verified: {payment_id} | order: {order_id}")

    return jsonify({
        "status":     "ok",
        "message":    "Payment verified successfully",
        "payment_id": payment_id,
        "order_id":   order_id,
        "stats":      stats,
    }), 200


# ─── Webhook Endpoint ─────────────────────────────────────────────────────────

@app.post("/webhook/razorpay")
def razorpay_webhook():
    """
    Receives payment events from Razorpay (server-to-server).

    Razorpay sends this after EVERY payment event, even for Payment Links
    created by pipeline_live.py. This is the real-time closure mechanism.

    Verification:
        Header:    X-Razorpay-Signature
        Algorithm: HMAC-SHA256(raw_request_body, WEBHOOK_SECRET)

    NOTE: Webhook secret is DIFFERENT from KEY_SECRET.
          Set RAZORPAY_WEBHOOK_SECRET in .env after creating one in
          Razorpay Dashboard -> Settings -> Webhooks.

    Handled events:
        payment.captured  -- Money settled, definitive recovery
        payment.authorized -- Auth only (capture pending)

    Ignored events:
        payment.failed, order.paid (duplicate), etc.
    """
    # ── Read raw body BEFORE any parsing (required for HMAC) ──────────────
    raw_body = request.get_data()

    # ── Signature verification ────────────────────────────────────────────
    incoming_sig = request.headers.get("X-Razorpay-Signature", "")

    if WEBHOOK_SECRET:
        expected_sig = hmac.new(
            WEBHOOK_SECRET.encode("utf-8"),
            msg=raw_body,
            digestmod=hashlib.sha256,
        ).hexdigest()

        if not hmac.compare_digest(expected_sig, incoming_sig):
            print(f"  [Webhook] SIGNATURE MISMATCH -- rejected")
            return jsonify({"status": "error", "message": "Signature mismatch"}), 400
    else:
        # No webhook secret configured -- accept but warn
        # In production this MUST be set. For local testing it's OK.
        print("  [Webhook] WARNING: RAZORPAY_WEBHOOK_SECRET not set -- skipping verification")

    # ── Parse event ───────────────────────────────────────────────────────
    try:
        payload = json.loads(raw_body)
    except json.JSONDecodeError:
        return jsonify({"status": "error", "message": "Invalid JSON"}), 400

    event      = payload.get("event", "")
    entity     = payload.get("payload", {}).get("payment", {}).get("entity", {})
    payment_id = entity.get("id", "")
    order_id   = entity.get("order_id", "")
    amount_p   = entity.get("amount", 0)
    status     = entity.get("status", "")

    print(f"  [Webhook] Event: {event} | payment: {payment_id} | status: {status}")

    # ── Handle events ─────────────────────────────────────────────────────
    if event in ("payment.captured", "payment.authorized"):
        if not payment_id:
            return jsonify({"status": "error", "message": "Missing payment entity"}), 400

        record_payment(
            payment_id=payment_id,
            order_id=order_id,
            amount_paise=int(amount_p),
            event=event,
            raw_payload=payload,
        )
        return jsonify({"status": "ok", "message": f"Recorded {event}"}), 200

    # All other events -- acknowledge but don't act
    print(f"  [Webhook] Ignored event: {event}")
    return jsonify({"status": "ok", "message": f"Event {event} acknowledged"}), 200


# ─── Live Recovery Stats ──────────────────────────────────────────────────────

@app.get("/api/recovery-stats")
def recovery_stats():
    """
    Live recovery statistics polled by the frontend every 3 seconds.
    Reads from recovery_ledger.json -- updates in real time as webhooks arrive.
    """
    return jsonify(get_stats()), 200


# ─── Health Check ─────────────────────────────────────────────────────────────

@app.get("/api/health")
def health():
    return jsonify({
        "status":          "ok",
        "key_id":          KEY_ID[:12] + "...",
        "webhook_secret":  "set" if WEBHOOK_SECRET else "NOT SET (local testing only)",
    }), 200


# ─── Run ──────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    print()
    print("=" * 60)
    print("  Razorpay Standard Checkout + Webhook Server")
    print("=" * 60)
    print(f"  Key ID         : {KEY_ID[:12]}...")
    print(f"  Mode           : {'TEST' if 'test' in KEY_ID else 'LIVE'}")
    print(f"  Webhook secret : {'SET' if WEBHOOK_SECRET else 'NOT SET (skipping verification)'}")
    print()
    print("  Endpoints:")
    print("    GET  http://localhost:5000/              -- Checkout page")
    print("    POST http://localhost:5000/api/create-order")
    print("    POST http://localhost:5000/api/verify-payment")
    print("    POST http://localhost:5000/webhook/razorpay")
    print("    GET  http://localhost:5000/api/recovery-stats")
    print()
    app.run(debug=True, port=5000)
