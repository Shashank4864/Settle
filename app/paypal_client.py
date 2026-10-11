"""
Talks to PayPal. Two modes:
  * REAL  : PayPal sandbox REST Invoicing API (needs PAYPAL_CLIENT_ID / PAYPAL_SECRET)
  * MOCK  : a fake PayPal stored in data/mock_paypal.json (no internet, no keys)
The rest of the app does not care which one is used.
"""
import json
import os
import time
from datetime import date

import httpx

from . import config

# =====================================================================
# MOCK MODE
# =====================================================================
MOCK_FILE = os.path.join(os.path.dirname(config.DB_PATH) or ".", "mock_paypal.json")


def _mock_load():
    if os.path.exists(MOCK_FILE):
        with open(MOCK_FILE) as f:
            return json.load(f)
    return {}


def _mock_save(d):
    os.makedirs(os.path.dirname(MOCK_FILE) or ".", exist_ok=True)
    with open(MOCK_FILE, "w") as f:
        json.dump(d, f)


# =====================================================================
# REAL MODE helpers
# =====================================================================
_tok = {"v": None, "exp": 0.0}


def _token() -> str:
    if _tok["v"] and time.time() < _tok["exp"] - 30:
        return _tok["v"]
    r = httpx.post(
        f"{config.PAYPAL_BASE}/v1/oauth2/token",
        auth=(config.PAYPAL_CLIENT_ID, config.PAYPAL_SECRET),
        data={"grant_type": "client_credentials"},
        timeout=30,
    )
    r.raise_for_status()
    j = r.json()
    _tok.update(v=j["access_token"], exp=time.time() + j["expires_in"])
    return _tok["v"]


def _req(method: str, path: str, **kw):
    r = httpx.request(
        method, f"{config.PAYPAL_BASE}{path}",
        headers={"Authorization": f"Bearer {_token()}", "Content-Type": "application/json"},
        timeout=30, **kw,
    )
    if r.status_code >= 400:
        # PayPal puts a debug_id in errors - keep it, support teams ask for it.
        raise RuntimeError(f"PayPal {method} {path} -> {r.status_code}: {r.text}")
    return r.json() if r.text else {}


# =====================================================================
# PUBLIC FUNCTIONS (same names in both modes)
# =====================================================================
def mode() -> str:
    return "mock" if config.PAYPAL_MOCK else "sandbox"


def create_invoice(client_name, client_email, items, due_date, note=""):
    """items = [(description, amount), ...]; due_date = 'YYYY-MM-DD'. Returns the invoice id."""
    if config.PAYPAL_MOCK:
        d = _mock_load()
        pid = f"MOCK-INV-{len(d) + 1:04d}"
        d[pid] = {"status": "DRAFT", "amount": sum(a for _, a in items), "due": due_date,
                  "client": client_name, "payments": []}
        _mock_save(d)
        return pid
    body = {
        "detail": {
            "currency_code": "USD",
            "note": note,
            "payment_term": {"term_type": "DUE_ON_DATE_SPECIFIED", "due_date": due_date},
        },
        "invoicer": {
            "name": {"given_name": "Settle", "surname": "Demo"},
            "email_address": config.MERCHANT_EMAIL,
        },
        "primary_recipients": [{
            "billing_info": {"name": {"given_name": client_name}, "email_address": client_email}
        }],
        "items": [
            {"name": n[:200], "quantity": "1",
             "unit_amount": {"currency_code": "USD", "value": f"{a:.2f}"}}
            for n, a in items
        ],
    }
    res = _req("POST", "/v2/invoicing/invoices", json=body)
    return res["href"].rstrip("/").split("/")[-1]  # the id is the last part of the link


def send_invoice(invoice_id):
    if config.PAYPAL_MOCK:
        d = _mock_load()
        d[invoice_id]["status"] = "SENT"
        _mock_save(d)
        return {"mock": True}
    return _req("POST", f"/v2/invoicing/invoices/{invoice_id}/send",
                json={"send_to_recipient": True, "send_to_invoicer": False})


def send_reminder(invoice_id, subject, note):
    if config.PAYPAL_MOCK:
        d = _mock_load()
        d[invoice_id].setdefault("reminders", []).append({"subject": subject, "note": note})
        _mock_save(d)
        return {"mock": True}
    return _req("POST", f"/v2/invoicing/invoices/{invoice_id}/remind",
                json={"subject": subject[:100], "note": note[:4000],
                      "send_to_recipient": True, "send_to_invoicer": False})


def cancel_invoice(invoice_id, subject="Invoice replaced by a payment plan",
                   note="This invoice was replaced by agreed installment invoices."):
    if config.PAYPAL_MOCK:
        d = _mock_load()
        d[invoice_id]["status"] = "CANCELLED"
        _mock_save(d)
        return {"mock": True}
    return _req("POST", f"/v2/invoicing/invoices/{invoice_id}/cancel",
                json={"subject": subject, "note": note,
                      "send_to_invoicer": False, "send_to_recipient": True})


def record_payment(invoice_id, amount, pay_date=None):
    """Demo helper: marks the invoice as paid (like the client paid by cash/bank)."""
    pay_date = pay_date or date.today().isoformat()
    if config.PAYPAL_MOCK:
        d = _mock_load()
        d[invoice_id]["status"] = "PAID"
        d[invoice_id]["payments"].append({"date": pay_date, "amount": amount})
        _mock_save(d)
        return {"mock": True}
    return _req("POST", f"/v2/invoicing/invoices/{invoice_id}/payments",
                json={"method": "OTHER", "payment_date": pay_date,
                      "amount": {"currency_code": "USD", "value": f"{amount:.2f}"}})


def get_invoice(invoice_id):
    if config.PAYPAL_MOCK:
        d = _mock_load()[invoice_id]
        return {"id": invoice_id, "status": d["status"],
                "payments": {"transactions": [{"payment_date": p["date"]} for p in d["payments"]]},
                "detail": {"metadata": {
                    "recipient_view_url": f"{config.PUBLIC_BASE_URL}/mock-pay/{invoice_id}"}}}
    return _req("GET", f"/v2/invoicing/invoices/{invoice_id}")


def payer_url(invoice_id):
    """The page where the CLIENT opens the invoice and pays (log in with a sandbox Personal account)."""
    try:
        meta = get_invoice(invoice_id).get("detail", {}).get("metadata", {})
        return meta.get("recipient_view_url")
    except Exception:
        return None


def is_paid(status: str) -> bool:
    return status in ("PAID", "MARKED_AS_PAID")


def paid_date(inv: dict):
    try:
        return inv["payments"]["transactions"][0]["payment_date"][:10]
    except Exception:
        return date.today().isoformat()
