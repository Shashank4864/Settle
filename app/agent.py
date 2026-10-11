"""
The AI agents.
  * Settle (merchant agent): writes reminders and proposes payment plans.
  * Payer agent (simulated client): accepts / counters / declines a plan.
Each agent asks the AI model first. If no model is set (or it fails) a rule-based fallback is used,
so the app never breaks during a demo. Everything the AI says is checked by policy.py before use.
"""
import json
from datetime import date

from . import db, llm, policy

MERCHANT_SYSTEM = (
    "You are Settle, a polite, professional accounts-receivable assistant for a freelancer. "
    "You never threaten, never change the invoice amount, and keep messages under 120 words. "
    "Reply with JSON only, no extra text."
)
PAYER_SYSTEM = (
    "You are the accounts-payable agent of a small business that owes money on an invoice. "
    "You negotiate a payment plan with the freelancer's assistant. Respect your cash limits strictly. "
    "Reply with JSON only, no extra text."
)


def _ask(system, user, fallback, actor):
    """Try the AI; on any problem use the fallback and write that into the audit log."""
    try:
        out = llm.complete_json(system, user)
        return out, "ai"
    except llm.LLMUnavailable:
        return fallback(), "rules"
    except Exception as e:  # network error, bad JSON, rate limit...
        db.audit(actor, "ai_failed_used_rules", {"error": str(e)[:300]})
        return fallback(), "rules"


# ---------------------------------------------------------------- reminders
def propose_reminder(ctx: dict) -> dict:
    inv, risk = ctx["invoice"], ctx["risk"]
    overdue = ctx["days_overdue"]

    def fallback():
        tone = "firm" if overdue > 0 else "friendly"
        if overdue > 0:
            msg = (f"Hi {inv['client']}, a quick follow-up: invoice for ${inv['amount']:,.2f} "
                   f"({inv['description']}) was due on {inv['due_date']} and is now {overdue} days overdue. "
                   f"Could you please arrange payment this week, or reply if you would like to agree on a short plan? Thank you!")
        else:
            msg = (f"Hi {inv['client']}, a friendly heads-up that invoice for ${inv['amount']:,.2f} "
                   f"({inv['description']}) is due on {inv['due_date']}. "
                   f"You can pay securely with the link in the invoice. Thanks so much!")
        return {"tone": tone, "subject": f"Reminder: invoice due {inv['due_date']}", "message": msg,
                "reasoning": risk["explanation"]}

    user = (
        "Write a payment reminder.\n"
        f"Invoice: {json.dumps(inv)}\nDays overdue (0 = not yet due): {overdue}\n"
        f"Client payment behaviour: {risk['explanation']} Risk: {risk['label']}.\n"
        'Return JSON: {"tone":"friendly|firm","subject":"...","message":"...","reasoning":"one sentence why this tone"}'
    )
    out, source = _ask(MERCHANT_SYSTEM, user, fallback, "settle-agent")
    out["source"] = source
    return out


# ---------------------------------------------------------------- payment plans
def _default_plan(amount: float) -> dict:
    first = round(amount * 0.5, 2)
    return {"installments": [{"amount": first, "due_in_days": 7},
                             {"amount": round(amount - first, 2), "due_in_days": 21}]}


def merchant_offer(ctx: dict, round_no: int, last_counter: dict | None) -> dict:
    inv = ctx["invoice"]

    def fallback():
        if last_counter:
            plan = policy.clamp_plan(last_counter, inv["amount"])
            return {"plan": plan, "message": "Thanks for the counter. This is the closest plan my rules allow."}
        return {"plan": _default_plan(inv["amount"]),
                "message": "To keep things easy, how about 50% now and the rest in two weeks?"}

    user = (
        f"Negotiation round {round_no}. Propose a payment plan for this invoice.\n"
        f"Invoice: {json.dumps(inv)}\n"
        f"Rules you must obey: at most {policy.MAX_INSTALLMENTS} installments, first installment at least "
        f"{int(policy.MIN_FIRST_PCT * 100)}% of the invoice, everything paid within {policy.MAX_PLAN_DAYS} days, "
        "installments must add up to exactly the invoice amount.\n"
        f"Client's last counter-offer: {json.dumps(last_counter) if last_counter else 'none yet'}\n"
        'Return JSON: {"plan":{"installments":[{"amount":0.0,"due_in_days":0}]},"message":"short friendly note"}'
    )
    out, source = _ask(MERCHANT_SYSTEM, user, fallback, "settle-agent")
    out["source"] = source
    return out


def payer_reply(ctx: dict, offer: dict) -> dict:
    inv = ctx["invoice"]
    cash_now = round(inv["amount"] * ctx["payer"]["cash_now_pct"], 2)
    max_days = ctx["payer"]["max_days"]
    plan = offer["plan"]

    def fits(p):
        ins = p["installments"]
        return ins[0]["amount"] <= cash_now + 0.01 and ins[-1]["due_in_days"] <= max_days

    def fallback():
        if fits(plan):
            return {"decision": "accept", "message": "That works for us. Thank you!"}
        first = min(cash_now, inv["amount"])
        rest = round(inv["amount"] - first, 2)
        counter = {"installments": [{"amount": round(first, 2), "due_in_days": 5}]}
        if rest > 0:
            counter["installments"].append({"amount": rest, "due_in_days": max_days})
        return {"decision": "counter", "counter_plan": counter,
                "message": f"We can pay ${first:,.2f} up front, and the rest by day {max_days}."}

    user = (
        f"You owe ${inv['amount']:,.2f} to a freelancer. Your hard limits: you can pay at most ${cash_now:,.2f} "
        f"right now (first installment) and everything must be settled within {max_days} days.\n"
        f"Their offer: {json.dumps(plan)}\nTheir note: {offer.get('message', '')}\n"
        'Return JSON: {"decision":"accept|counter|decline","counter_plan":{"installments":[{"amount":0.0,"due_in_days":0}]} '
        '(only when countering),"message":"short note"}'
    )
    out, source = _ask(PAYER_SYSTEM, user, fallback, "payer-agent")
    # safety: the simulated payer cannot accept something outside its own limits
    if out.get("decision") == "accept" and not fits(plan):
        out = fallback()
        source = "rules"
    out["source"] = source
    return out


def negotiate(ctx: dict, max_rounds: int = 3) -> dict:
    """Run the two agents against each other. Returns transcript + agreed plan (or None)."""
    amount = ctx["invoice"]["amount"]
    transcript, agreed, last_counter = [], None, None
    for r in range(1, max_rounds + 1):
        offer = merchant_offer(ctx, r, last_counter)
        offer["plan"] = policy.clamp_plan(offer.get("plan") or {}, amount)  # guardrail on our own side
        transcript.append({"round": r, "from": "Settle", "plan": offer["plan"], "message": offer.get("message", ""),
                           "source": offer.get("source")})
        reply = payer_reply(ctx, offer)
        decision = reply.get("decision", "decline")
        entry = {"round": r, "from": "Client agent", "decision": decision, "message": reply.get("message", ""),
                 "source": reply.get("source")}
        if decision == "counter":
            entry["plan"] = reply.get("counter_plan")
        transcript.append(entry)
        if decision == "accept":
            agreed = offer["plan"]
            break
        if decision == "decline":
            break
        counter = reply.get("counter_plan") or {}
        if not policy.validate_plan(counter, amount):
            transcript.append({"round": r, "from": "Settle", "decision": "accept",
                               "message": "Counter-offer is within my rules. Agreed.", "plan": counter})
            agreed = counter
            break
        last_counter = counter
    return {"agreed_plan": agreed, "transcript": transcript}
