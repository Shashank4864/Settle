"""
GUARDRAILS. The AI may *propose* anything, but only proposals that pass these rules can ever be executed.
These rules are plain code, so the AI cannot talk its way around them.
"""
from datetime import date, timedelta

MAX_INSTALLMENTS = 3
MIN_FIRST_PCT = 0.34        # first installment must be at least 34% of the invoice
MAX_PLAN_DAYS = 45          # everything must be settled within 45 days from today
MAX_MESSAGE_CHARS = 900
BANNED_WORDS = ("lawsuit", "legal action", "sue ", "collections agency", "police", "arrest", "ruin your")


def validate_plan(plan: dict, amount: float, today: date | None = None) -> list:
    """Returns a list of rule violations. Empty list = plan is allowed."""
    today = today or date.today()
    errors = []
    inst = plan.get("installments") or []
    if not inst:
        return ["plan has no installments"]
    if len(inst) > MAX_INSTALLMENTS:
        errors.append(f"more than {MAX_INSTALLMENTS} installments")
    try:
        total = sum(float(i["amount"]) for i in inst)
        days = [int(i["due_in_days"]) for i in inst]
        first = float(inst[0]["amount"])
    except Exception:
        return ["installments are malformed"]
    if abs(total - amount) > 0.05:
        errors.append(f"installments add up to {total:.2f}, not {amount:.2f} (agent may not change the amount)")
    if any(float(i["amount"]) <= 0 for i in inst):
        errors.append("an installment is zero or negative")
    if first + 0.05 < MIN_FIRST_PCT * amount:
        errors.append(f"first installment is below {int(MIN_FIRST_PCT * 100)}% of the invoice")
    if max(days) > MAX_PLAN_DAYS:
        errors.append(f"plan runs longer than {MAX_PLAN_DAYS} days")
    if min(days) < 0 or days != sorted(days):
        errors.append("installment dates must be in order and not in the past")
    return errors


def validate_reminder(msg: dict) -> list:
    errors = []
    text = (msg.get("message") or "")
    if not text.strip():
        errors.append("empty message")
    if len(text) > MAX_MESSAGE_CHARS:
        errors.append("message too long")
    low = text.lower()
    for w in BANNED_WORDS:
        if w in low:
            errors.append(f"message contains a threatening phrase ({w.strip()!r})")
    return errors


def clamp_plan(plan: dict, amount: float) -> dict:
    """Nearest allowed plan to whatever the other side asked for (used during negotiation)."""
    inst = plan.get("installments") or [{"amount": amount, "due_in_days": 14}]
    n = max(1, min(MAX_INSTALLMENTS, len(inst)))
    days = [max(0, min(MAX_PLAN_DAYS, int(i.get("due_in_days", 14)))) for i in inst[:n]]
    days = sorted(days)
    first = max(float(inst[0]["amount"]), round(MIN_FIRST_PCT * amount, 2))
    first = min(first, amount)
    rest = round(amount - first, 2)
    amounts = [round(first, 2)]
    if n > 1 and rest > 0:
        share = round(rest / (n - 1), 2)
        amounts += [share] * (n - 2)
        amounts.append(round(amount - sum(amounts), 2))
    else:
        days = days[:1]
        amounts = [round(amount, 2)]
    return {"installments": [{"amount": a, "due_in_days": d} for a, d in zip(amounts, days)]}
