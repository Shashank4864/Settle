"""Business logic: build the dashboard state, create AI proposals, execute approved actions, sync with PayPal."""
import json
from datetime import date, timedelta

from . import agent, db, paypal_client as pp, policy
from .predict import client_profile, explain, forecast_cash, predict_payment, risk_label


# ------------------------------------------------------------------ helpers
def _d(s):
    return date.fromisoformat(s)


def _client(client_id):
    c = db.one("SELECT * FROM clients WHERE id=?", (client_id,))
    c["history"] = json.loads(c["history"])
    return c


def _risk(client):
    prof = client_profile(client["history"])
    return prof, {"label": risk_label(prof), "explanation": explain(client["name"], prof),
                  "p_late": round(prof["p_late"], 2), "median_days_late": prof["median"]}


def invoice_context(inv_id: int) -> dict:
    inv = db.one("SELECT * FROM invoices WHERE id=?", (inv_id,))
    if not inv:
        raise KeyError("invoice not found")
    client = _client(inv["client_id"])
    prof, risk = _risk(client)
    overdue = max(0, (date.today() - _d(inv["due_date"])).days)
    return {
        "invoice": {"id": inv["id"], "client": client["name"], "amount": inv["amount"],
                    "description": inv["description"], "due_date": inv["due_date"]},
        "risk": risk, "days_overdue": overdue,
        "payer": {"cash_now_pct": client["cash_now_pct"], "max_days": client["max_days"]},
        "client_email": client["email"],
    }


# ------------------------------------------------------------------ dashboard state
def build_state() -> dict:
    today = date.today()
    balance = db.get_setting("balance", 0.0)
    expenses = [{"name": e["name"], "amount": e["amount"], "date": _d(e["date"])}
                for e in db.query("SELECT * FROM expenses")]
    open_invs = db.query("SELECT * FROM invoices WHERE status='SENT' ORDER BY due_date")
    pending = {a["invoice_id"]: a for a in db.query("SELECT * FROM actions WHERE status='pending'")}
    rows, forecast_inputs = [], []
    for inv in open_invs:
        client = _client(inv["client_id"])
        prof, risk = _risk(client)
        if inv["parent_id"] is not None:
            # Assumption (stated in the README): installments of an agreed plan are paid more punctually.
            prof = {**prof, "median": prof["median"] * 0.3, "spread": max(prof["spread"] * 0.5, 2.0)}
        pred = predict_payment(_d(inv["due_date"]), prof, today)
        forecast_inputs.append({"amount": inv["amount"], "pred": pred})
        rows.append({
            "id": inv["id"], "client": client["name"], "description": inv["description"],
            "amount": inv["amount"], "due_date": inv["due_date"], "status": inv["status"],
            "risk": risk["label"], "p_late": risk["p_late"], "why": risk["explanation"],
            "expected_pay_date": pred["expected"].isoformat(), "worst_pay_date": pred["worst"].isoformat(),
            "pending_action": pending.get(inv["id"], {}).get("type"),
            "is_plan_child": inv["parent_id"] is not None,
        })
    fc = {sc: forecast_cash(balance, forecast_inputs, expenses, today, 90, sc)
          for sc in ("best", "expected", "worst")}
    done = db.query("SELECT * FROM invoices WHERE status!='SENT' ORDER BY id DESC")
    history_rows = [{"id": i["id"], "client": _client(i["client_id"])["name"], "description": i["description"],
                     "amount": i["amount"], "due_date": i["due_date"], "status": i["status"]} for i in done]
    at_risk = sum(r["amount"] for r in rows if r["risk"] == "High")
    kpis = {
        "balance": round(balance, 2),
        "outstanding": round(sum(r["amount"] for r in rows), 2),
        "at_risk": round(at_risk, 2),
        "shortfall_expected": fc["expected"]["shortfall"],
        "shortfall_worst": fc["worst"]["shortfall"],
        "shortfall_best": fc["best"]["shortfall"],
    }
    return {"kpis": kpis, "invoices": rows, "closed": history_rows, "forecast": fc,
            "expenses": [{**e, "date": e["date"].isoformat()} for e in expenses],
            "modes": {"paypal": pp.mode(), "ai": _ai_mode()}}


def _ai_mode():
    from . import config
    return config.LLM_PROVIDER if config.llm_enabled() else "rules"


# ------------------------------------------------------------------ proposals (AI -> pending approval)
def _guard_pending(inv_id, kind):
    if db.one("SELECT id FROM actions WHERE invoice_id=? AND status='pending'", (inv_id,)):
        raise ValueError("This invoice already has a proposal waiting for your approval.")
    inv = db.one("SELECT * FROM invoices WHERE id=?", (inv_id,))
    if not inv or inv["status"] != "SENT":
        raise ValueError("Only open (sent) invoices can be actioned.")


def propose_reminder(inv_id: int) -> dict:
    _guard_pending(inv_id, "reminder")
    ctx = invoice_context(inv_id)
    out = agent.propose_reminder(ctx)
    problems = policy.validate_reminder(out)
    if problems:
        db.audit("guardrail", "reminder_blocked", {"invoice": inv_id, "problems": problems})
        raise ValueError("Guardrail blocked the draft: " + "; ".join(problems))
    payload = {"subject": out.get("subject", "Payment reminder"), "message": out["message"],
               "tone": out.get("tone", "friendly"), "reasoning": out.get("reasoning", ""),
               "source": out.get("source")}
    aid = db.execute("INSERT INTO actions(invoice_id,type,payload,status,created) VALUES (?,?,?,?,?)",
                     (inv_id, "reminder", json.dumps(payload), "pending", db.now()))
    db.audit(f"agent:{out.get('source')}", "proposed_reminder", {"invoice": inv_id, "action": aid})
    return {"action_id": aid, **payload}


def propose_plan(inv_id: int) -> dict:
    _guard_pending(inv_id, "payment_plan")
    ctx = invoice_context(inv_id)
    result = agent.negotiate(ctx)
    db.audit("agents", "negotiation_finished", {"invoice": inv_id, "agreed": bool(result["agreed_plan"])})
    if not result["agreed_plan"]:
        return {"action_id": None, "agreed": False, "transcript": result["transcript"]}
    problems = policy.validate_plan(result["agreed_plan"], ctx["invoice"]["amount"])
    if problems:
        db.audit("guardrail", "plan_blocked", {"invoice": inv_id, "problems": problems})
        raise ValueError("Guardrail blocked the plan: " + "; ".join(problems))
    payload = {"plan": result["agreed_plan"], "transcript": result["transcript"]}
    aid = db.execute("INSERT INTO actions(invoice_id,type,payload,status,created) VALUES (?,?,?,?,?)",
                     (inv_id, "payment_plan", json.dumps(payload), "pending", db.now()))
    db.audit("agents", "proposed_plan", {"invoice": inv_id, "action": aid, "plan": result["agreed_plan"]})
    return {"action_id": aid, "agreed": True, **payload}


# ------------------------------------------------------------------ approval queue
def list_actions(status="pending"):
    out = []
    for a in db.query("SELECT * FROM actions WHERE status=? ORDER BY id DESC", (status,)):
        inv = db.one("SELECT * FROM invoices WHERE id=?", (a["invoice_id"],))
        client = _client(inv["client_id"])
        out.append({"id": a["id"], "type": a["type"], "payload": json.loads(a["payload"]),
                    "invoice": {"id": inv["id"], "client": client["name"], "amount": inv["amount"],
                                "due_date": inv["due_date"], "description": inv["description"]},
                    "created": a["created"]})
    return out


def reject(action_id: int):
    a = db.one("SELECT * FROM actions WHERE id=? AND status='pending'", (action_id,))
    if not a:
        raise ValueError("No such pending action.")
    db.execute("UPDATE actions SET status='rejected', decided=? WHERE id=?", (db.now(), action_id))
    db.audit("human", "rejected", {"action": action_id})


def approve(action_id: int) -> dict:
    """The ONLY place money-related PayPal calls are made for AI proposals. Requires a human click."""
    a = db.one("SELECT * FROM actions WHERE id=? AND status='pending'", (action_id,))
    if not a:
        raise ValueError("No such pending action.")
    payload = json.loads(a["payload"])
    inv = db.one("SELECT * FROM invoices WHERE id=?", (a["invoice_id"],))
    db.execute("UPDATE actions SET status='approved', decided=? WHERE id=?", (db.now(), action_id))
    db.audit("human", "approved", {"action": action_id, "type": a["type"]})
    try:
        if a["type"] == "reminder":
            problems = policy.validate_reminder(payload)
            if problems:
                raise ValueError("; ".join(problems))
            pp.send_reminder(inv["paypal_id"], payload["subject"], payload["message"])
            result = {"sent": True}
        elif a["type"] == "payment_plan":
            result = _execute_plan(inv, payload["plan"])
        else:
            raise ValueError("unknown action type")
        db.execute("UPDATE actions SET status='executed', result=? WHERE id=?", (json.dumps(result), action_id))
        db.audit("system", "executed", {"action": action_id, **result})
        return {"ok": True, **result}
    except Exception as e:
        db.execute("UPDATE actions SET status='failed', result=? WHERE id=?",
                   (json.dumps({"error": str(e)[:500]}), action_id))
        db.audit("system", "execution_failed", {"action": action_id, "error": str(e)[:500]})
        raise


def _execute_plan(inv: dict, plan: dict) -> dict:
    # defence in depth: re-check the rules right before touching money
    problems = policy.validate_plan(plan, inv["amount"])
    if problems:
        raise ValueError("Guardrail: " + "; ".join(problems))
    client = _client(inv["client_id"])
    today = date.today()
    new_ids = []
    n = len(plan["installments"])
    for i, part in enumerate(plan["installments"], start=1):
        due = (today + timedelta(days=int(part["due_in_days"]))).isoformat()
        desc = f"{inv['description']} - installment {i}/{n}"
        pid = pp.create_invoice(client["name"], client["email"], [(desc, float(part["amount"]))], due,
                                note="Payment plan agreed between both assistants.")
        pp.send_invoice(pid)
        new_ids.append(db.execute(
            "INSERT INTO invoices(paypal_id,client_id,description,amount,due_date,status,parent_id,created) "
            "VALUES (?,?,?,?,?,?,?,?)",
            (pid, client["id"], desc, float(part["amount"]), due, "SENT", inv["id"], db.now())))
    pp.cancel_invoice(inv["paypal_id"])
    db.execute("UPDATE invoices SET status='REPLACED' WHERE id=?", (inv["id"],))
    return {"replaced_invoice": inv["id"], "new_invoices": new_ids}


# ------------------------------------------------------------------ PayPal sync
def sync_with_paypal() -> dict:
    """Ask PayPal which open invoices are now paid; update cash, forecast and each client's history."""
    updated = []
    for inv in db.query("SELECT * FROM invoices WHERE status='SENT'"):
        try:
            remote = pp.get_invoice(inv["paypal_id"])
        except Exception as e:
            db.audit("system", "sync_error", {"invoice": inv["id"], "error": str(e)[:300]})
            continue
        status = remote.get("status", "")
        if pp.is_paid(status):
            pdate = pp.paid_date(remote)
            late = (_d(pdate) - _d(inv["due_date"])).days
            client = _client(inv["client_id"])
            hist = client["history"] + [late]
            db.execute("UPDATE clients SET history=? WHERE id=?", (json.dumps(hist[-12:]), client["id"]))
            db.execute("UPDATE invoices SET status='PAID' WHERE id=?", (inv["id"],))
            db.set_setting("balance", db.get_setting("balance", 0.0) + inv["amount"])
            db.audit("paypal", "payment_detected", {"invoice": inv["id"], "client": client["name"],
                                                   "days_late": late, "amount": inv["amount"]})
            updated.append(inv["id"])
        elif status == "CANCELLED":
            db.execute("UPDATE invoices SET status='CANCELLED' WHERE id=?", (inv["id"],))
            updated.append(inv["id"])
    return {"updated": updated}
