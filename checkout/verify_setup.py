"""Quick verification that credentials and Razorpay SDK are working."""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv
load_dotenv(os.path.join(os.path.dirname(os.path.dirname(__file__)), ".env"))

KEY_ID     = os.environ.get("RAZORPAY_KEY_ID", "")
KEY_SECRET = os.environ.get("RAZORPAY_KEY_SECRET", "")

print("\n=== Razorpay Checkout Setup Verification ===\n")

if KEY_ID:
    print(f"  KEY_ID     : {KEY_ID[:12]}...  OK")
else:
    print("  KEY_ID     : MISSING — check .env"); sys.exit(1)

if KEY_SECRET:
    print(f"  KEY_SECRET : {KEY_SECRET[:4]}****  OK")
else:
    print("  KEY_SECRET : MISSING — check .env"); sys.exit(1)

import razorpay
client = razorpay.Client(auth=(KEY_ID, KEY_SECRET))
print("  SDK        : razorpay client initialized  OK")

print("\n  Testing API connectivity...")
try:
    result = client.order.all({"count": 1})
    items = result.get("items", [])
    print(f"  Razorpay API : Connected OK  ({len(items)} existing orders)")
except Exception as e:
    print(f"  Razorpay API : ERROR — {e}"); sys.exit(1)

import hmac, hashlib
test_sig = hmac.new(
    KEY_SECRET.encode("utf-8"),
    msg=b"order_test|pay_test",
    digestmod=hashlib.sha256,
).hexdigest()
print(f"  HMAC-SHA256  : Working OK  (sample: {test_sig[:16]}...)")

print("\n=== ALL CHECKS PASSED ===")
print("\n  Start the server with:")
print("    python checkout/app.py")
print("\n  Then open: http://localhost:5000\n")
