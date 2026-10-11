"""
Creates an invoice, prints the CLIENT's payment link, then waits while YOU pay it in the browser
(log in at sandbox.paypal.com with a sandbox PERSONAL account). Run:  python scripts/smoke_test_real_pay.py
"""
import datetime as dt
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from app import paypal_client as pp  # noqa: E402

email = os.getenv("TEST_BUYER_EMAIL", "sb-buyer@personal.example.com")
due = (dt.date.today() + dt.timedelta(days=7)).isoformat()
inv = pp.create_invoice("Acme Studio", email, [("Logo design", 450.00)], due)
print("send response:", pp.send_invoice(inv))
print("\nOPEN THIS LINK, log in as a sandbox PERSONAL account, and pay:\n", pp.payer_url(inv), "\n")
for _ in range(60):
    status = pp.get_invoice(inv)["status"]
    print("status:", status)
    if pp.is_paid(status):
        print("Payment detected!")
        break
    time.sleep(5)
