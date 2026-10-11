"""
FIRST THING TO RUN with real sandbox keys.  From the project folder:   python scripts/smoke_test.py
It creates an invoice, sends it, sends a reminder, records a payment and reads the status back.
"""
import datetime as dt
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from app import config, paypal_client as pp  # noqa: E402

print("PayPal mode:", pp.mode())
if config.PAYPAL_MOCK:
    print("-> Mock mode. Put PAYPAL_CLIENT_ID / PAYPAL_SECRET / MERCHANT_EMAIL in .env to test the real sandbox.")

due = (dt.date.today() + dt.timedelta(days=7)).isoformat()
PERSONAL_EMAIL = os.getenv("TEST_BUYER_EMAIL", "sb-buyer@personal.example.com")  # a sandbox PERSONAL account email
inv = pp.create_invoice("Acme Studio", PERSONAL_EMAIL, [("Logo design", 450.00)], due, note="Smoke test")
print("1. created :", inv)
pp.send_invoice(inv);                                        print("2. sent")
pp.send_reminder(inv, "Friendly reminder", "Test reminder.");  print("3. reminder sent")
print("4. payer link:", pp.payer_url(inv))
pp.record_payment(inv, 450.00);                              print("5. payment recorded")
print("6. status :", pp.get_invoice(inv)["status"])
print("\nALL GOOD - the PayPal part works.")
