"""The web server. Run with:  uvicorn app.main:app --port 8000"""
import os

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles

from . import config, db, paypal_client as pp, seed, service

app = FastAPI(title="Settle")
db.init()
STATIC = os.path.join(os.path.dirname(__file__), "..", "static")
app.mount("/static", StaticFiles(directory=STATIC), name="static")


def _run(fn, *a):
    try:
        return fn(*a)
    except KeyError as e:
        raise HTTPException(404, str(e))
    except ValueError as e:
        raise HTTPException(400, str(e))
    except Exception as e:
        raise HTTPException(502, f"{type(e).__name__}: {e}")


@app.get("/")
def home():
    return FileResponse(os.path.join(STATIC, "index.html"))


@app.get("/api/state")
def state():
    return service.build_state()


@app.post("/api/seed")
def seed_demo():
    return _run(seed.run)


@app.post("/api/sync")
def sync():
    return _run(service.sync_with_paypal)


@app.post("/api/invoices/{inv_id}/reminder")
def draft_reminder(inv_id: int):
    return _run(service.propose_reminder, inv_id)


@app.post("/api/invoices/{inv_id}/negotiate")
def negotiate(inv_id: int):
    return _run(service.propose_plan, inv_id)


@app.get("/api/invoices/{inv_id}/payer-link")
def payer_link(inv_id: int):
    inv = db.one("SELECT * FROM invoices WHERE id=?", (inv_id,))
    if not inv:
        raise HTTPException(404, "invoice not found")
    return {"url": pp.payer_url(inv["paypal_id"])}


@app.post("/api/invoices/{inv_id}/simulate-paid")
def simulate_paid(inv_id: int):
    """DEMO HELPER: pretend the client paid (sandbox 'record payment'), then sync."""
    inv = db.one("SELECT * FROM invoices WHERE id=?", (inv_id,))
    if not inv:
        raise HTTPException(404, "invoice not found")
    _run(pp.record_payment, inv["paypal_id"], inv["amount"])
    db.audit("demo", "simulated_client_payment", {"invoice": inv_id})
    return _run(service.sync_with_paypal)


@app.get("/api/actions")
def actions():
    return service.list_actions("pending")


@app.post("/api/actions/{aid}/approve")
def approve(aid: int):
    return _run(service.approve, aid)


@app.post("/api/actions/{aid}/reject")
def reject(aid: int):
    _run(service.reject, aid)
    return {"ok": True}


@app.get("/api/audit")
def audit_log():
    return db.query("SELECT * FROM audit ORDER BY id DESC LIMIT 60")


@app.get("/mock-pay/{pid}", response_class=HTMLResponse)
def mock_pay(pid: str):
    """Only used in MOCK mode: a tiny stand-in for PayPal's payer page."""
    if not config.PAYPAL_MOCK:
        raise HTTPException(404)
    return f"""<body style="font-family:sans-serif;max-width:420px;margin:60px auto">
    <h2>Mock invoice {pid}</h2><p>This simulates the client's payment page (mock mode).</p>
    <button onclick="fetch('/mock-pay/{pid}/pay',{{method:'POST'}}).then(()=>document.body.innerHTML='<h2>Paid. You can close this tab.</h2>')"
     style="padding:12px 20px;font-size:16px">Pay now</button></body>"""


@app.post("/mock-pay/{pid}/pay")
def mock_pay_do(pid: str):
    if not config.PAYPAL_MOCK:
        raise HTTPException(404)
    pp.record_payment(pid, 0.0)
    return {"ok": True}
