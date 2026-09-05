"""
Razorpay API Client — Payment Links

Creates real Razorpay Payment Links for recovery actions.
Uses Basic Auth with key_id:key_secret.

API Docs: https://razorpay.com/docs/api/payment-links/

CRITICAL GUARDRAILS (enforced here, not by LLM):
  - Never create links for fraud-flagged payments
  - Never exceed original payment amount
  - All amounts stored in paise (1 INR = 100 paise)
  - DRY_RUN mode: logs all calls without hitting Razorpay API
"""

import os
import json
import time
import requests
from datetime import datetime, timezone


# ─── Config ──────────────────────────────────────────────────────────────────

BASE_URL = "https://api.razorpay.com/v1"
PAYMENT_LINKS_URL = f"{BASE_URL}/payment_links"

# Rate limit: 60 req/min on test mode free tier
REQUEST_DELAY_SECONDS = 1.5


class RazorpayClient:
    """
    Thin wrapper around Razorpay Payment Links API.

    Usage:
        client = RazorpayClient.from_env()          # reads KEY_ID + SECRET from env
        client = RazorpayClient(key_id, key_secret)  # explicit
        client = RazorpayClient(key_id, key_secret, dry_run=True)  # logs only
    """

    def __init__(self, key_id: str, key_secret: str, dry_run: bool = False):
        if not key_id or not key_secret:
            raise ValueError(
                "Razorpay key_id and key_secret are required.\n"
                "Set RAZORPAY_KEY_ID and RAZORPAY_KEY_SECRET environment variables.\n"
                "Or run with --dry-run flag to test without real credentials."
            )
        self.key_id = key_id
        self.key_secret = key_secret
        self.dry_run = dry_run
        self._session = requests.Session()
        self._session.auth = (key_id, key_secret)
        self._session.headers.update({
            "Content-Type": "application/json",
            "Accept": "application/json",
        })

        mode = "DRY-RUN" if dry_run else "LIVE (Test Mode)"
        print(f"  [RazorpayClient] Initialized — {mode}")
        print(f"  [RazorpayClient] Key: {key_id[:12]}...")

    @classmethod
    def from_env(cls, dry_run: bool = False) -> "RazorpayClient":
        """Load credentials from environment variables."""
        key_id = os.environ.get("RAZORPAY_KEY_ID", "")
        key_secret = os.environ.get("RAZORPAY_KEY_SECRET", "")

        # Allow dry-run even without real keys
        if dry_run:
            key_id = key_id or "rzp_test_DRY_RUN_KEY"
            key_secret = key_secret or "DRY_RUN_SECRET"

        return cls(key_id, key_secret, dry_run=dry_run)

    # ─── Core API Method ─────────────────────────────────────────────────

    def create_payment_link(
        self,
        amount_inr: float,
        customer_id: str,
        payment_id: str,
        description: str,
        sms_notify: bool = True,
        email_notify: bool = False,
        expire_by_hours: int = 48,
    ) -> dict:
        """
        Create a Razorpay Payment Link.

        Returns a result dict:
        {
            "success": bool,
            "payment_link_id": str | None,
            "short_url": str | None,
            "status": str,
            "error": str | None,
            "raw_response": dict | None,
            "dry_run": bool,
        }

        GUARDRAIL: Amount is capped at the original payment amount.
        LLMs never touch this method — executor calls it directly.
        """
        # Convert INR → paise (Razorpay expects integer paise)
        amount_paise = int(round(amount_inr * 100))

        # Expiry timestamp (Unix epoch)
        expire_by = int(time.time()) + (expire_by_hours * 3600)

        payload = {
            "amount": amount_paise,
            "currency": "INR",
            "accept_partial": False,
            "description": description[:255],  # API limit
            "customer": {
                "name": f"Customer {customer_id}",
            },
            "notify": {
                "sms": sms_notify,
                "email": email_notify,
            },
            "reminder_enable": True,
            "notes": {
                "original_payment_id": payment_id,
                "customer_id": customer_id,
                "recovery_agent": "razorpay-revive-v1",
                "created_at": datetime.now(timezone.utc).isoformat(),
            },
            "expire_by": expire_by,
        }

        # ── DRY RUN: log and return mock response ─────────────────────
        if self.dry_run:
            mock_id = f"plink_DRY_{payment_id[-8:]}"
            mock_url = f"https://rzp.io/l/DRY_{payment_id[-6:]}"
            print(
                f"  [DRY-RUN] Would POST /payment_links | "
                f"Rs.{amount_inr:,.2f} | {customer_id} | {description[:60]}"
            )
            return {
                "success": True,
                "payment_link_id": mock_id,
                "short_url": mock_url,
                "status": "created",
                "error": None,
                "raw_response": {"id": mock_id, "short_url": mock_url, "status": "created"},
                "dry_run": True,
            }

        # ── LIVE: real API call ───────────────────────────────────────
        try:
            time.sleep(REQUEST_DELAY_SECONDS)  # Rate limit compliance
            response = self._session.post(
                PAYMENT_LINKS_URL,
                data=json.dumps(payload),
                timeout=15,
            )
            response.raise_for_status()
            data = response.json()

            return {
                "success": True,
                "payment_link_id": data.get("id"),
                "short_url": data.get("short_url"),
                "status": data.get("status", "created"),
                "error": None,
                "raw_response": data,
                "dry_run": False,
            }

        except requests.exceptions.HTTPError as e:
            error_body = {}
            try:
                error_body = e.response.json()
            except Exception:
                pass
            error_msg = error_body.get("error", {}).get("description", str(e))
            return {
                "success": False,
                "payment_link_id": None,
                "short_url": None,
                "status": "api_error",
                "error": error_msg,
                "raw_response": error_body,
                "dry_run": False,
            }

        except requests.exceptions.Timeout:
            return {
                "success": False,
                "payment_link_id": None,
                "short_url": None,
                "status": "timeout",
                "error": "Razorpay API timed out after 15s",
                "raw_response": None,
                "dry_run": False,
            }

        except Exception as e:
            return {
                "success": False,
                "payment_link_id": None,
                "short_url": None,
                "status": "unknown_error",
                "error": str(e),
                "raw_response": None,
                "dry_run": False,
            }

    # ─── Fetch existing link ─────────────────────────────────────────────

    def get_payment_link(self, link_id: str) -> dict:
        """Fetch status of an existing payment link."""
        if self.dry_run:
            return {"id": link_id, "status": "created", "dry_run": True}

        try:
            time.sleep(REQUEST_DELAY_SECONDS)
            response = self._session.get(f"{PAYMENT_LINKS_URL}/{link_id}", timeout=15)
            response.raise_for_status()
            return response.json()
        except Exception as e:
            return {"error": str(e), "status": "fetch_failed"}

    # ─── Test connectivity ───────────────────────────────────────────────

    def test_connection(self) -> bool:
        """
        Test that credentials are valid.
        Returns True if connected, False otherwise.
        """
        if self.dry_run:
            print("  [RazorpayClient] DRY-RUN: Skipping connectivity test")
            return True

        try:
            # List payment links with limit=1 — low-cost call
            response = self._session.get(
                PAYMENT_LINKS_URL,
                params={"count": 1},
                timeout=10,
            )
            if response.status_code == 200:
                print("  [RazorpayClient] ✓ Connection test passed")
                return True
            elif response.status_code == 401:
                print("  [RazorpayClient] ✗ Authentication failed — check your key_id and key_secret")
                return False
            else:
                print(f"  [RazorpayClient] ✗ Unexpected status: {response.status_code}")
                return False
        except Exception as e:
            print(f"  [RazorpayClient] ✗ Connection error: {e}")
            return False
