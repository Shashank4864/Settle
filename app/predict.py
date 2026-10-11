"""The prediction + forecast brain. Plain statistics on purpose: fast, explainable, testable."""
from datetime import date, timedelta
from statistics import median, pstdev


def client_profile(days_late: list) -> dict:
    """days_late: past payments, e.g. [9, 12, 7]. Negative = paid early."""
    if not days_late:  # unknown client -> cautious default
        return {"median": 7.0, "spread": 7.0, "p_late": 0.6, "n": 0}
    return {
        "median": float(median(days_late)),
        "spread": max(float(pstdev(days_late)), 2.0),
        "p_late": sum(d > 0 for d in days_late) / len(days_late),
        "n": len(days_late),
    }


def predict_payment(due: date, profile: dict, today: date | None = None) -> dict:
    today = today or date.today()
    m, s = profile["median"], profile["spread"]

    def clamp(d):  # an unpaid invoice can't be paid in the past
        return max(d, today + timedelta(days=1))

    return {
        "expected": clamp(due + timedelta(days=round(m))),
        "best": clamp(due + timedelta(days=round(m - s))),
        "worst": clamp(due + timedelta(days=round(m + s))),
        "p_late": profile["p_late"],
    }


def risk_label(profile: dict) -> str:
    if profile["p_late"] >= 0.7 or profile["median"] >= 8:
        return "High"
    if profile["p_late"] >= 0.4 or profile["median"] >= 3:
        return "Medium"
    return "Low"


def explain(name: str, profile: dict) -> str:
    if profile["n"] == 0:
        return f"{name} is a new client, so we assume a cautious 7 days late."
    late = round(profile["p_late"] * profile["n"])
    typ = round(profile["median"])
    if typ > 0:
        return f"{name} paid late on {late} of {profile['n']} invoices, typically {typ} days after the due date."
    if typ < 0:
        return f"{name} usually pays {abs(typ)} days early ({late} late of {profile['n']})."
    return f"{name} usually pays on the due date ({late} late of {profile['n']})."


def forecast_cash(opening: float, invoices: list, expenses: list, start: date,
                  days: int = 90, scenario: str = "expected") -> dict:
    """invoices: [{amount, pred}], expenses: [{amount, date(date)}]. Returns daily balance + first shortfall."""
    balance, rows, shortfall = opening, [], None
    for i in range(days + 1):
        d = start + timedelta(days=i)
        balance += sum(x["amount"] for x in invoices if x["pred"][scenario] == d)
        balance -= sum(e["amount"] for e in expenses if e["date"] == d)
        rows.append({"date": d.isoformat(), "balance": round(balance, 2)})
        if shortfall is None and balance < 0:
            shortfall = d.isoformat()
    return {"rows": rows, "shortfall": shortfall}
