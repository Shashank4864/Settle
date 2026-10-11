import json
import os
import sys
from datetime import date, timedelta

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("DB_PATH", str(tmp_path / "t.db"))
    monkeypatch.setenv("PAYPAL_CLIENT_ID", "")
    monkeypatch.setenv("LLM_PROVIDER", "mock")
    for m in [m for m in sys.modules if m == "app" or m.startswith("app.")]:
        del sys.modules[m]
    from fastapi.testclient import TestClient
    from app.main import app
    c = TestClient(app)
    c.post("/api/seed")
    return c


def test_predict_and_forecast():
    from app.predict import client_profile, forecast_cash, predict_payment, risk_label
    prof = client_profile([9, 12, 7, 10])
    assert risk_label(prof) == "High"
    today = date(2026, 10, 3)
    pred = predict_payment(today + timedelta(days=5), prof, today)
    assert pred["worst"] >= pred["expected"] >= pred["best"]
    fc = forecast_cash(100, [{"amount": 50, "pred": pred}], [{"amount": 500, "date": today + timedelta(days=2)}], today, 30)
    assert fc["shortfall"] == (today + timedelta(days=2)).isoformat()


def test_policy_blocks_bad_plans():
    from app import policy
    assert policy.validate_plan({"installments": [{"amount": 500, "due_in_days": 7}, {"amount": 500, "due_in_days": 21}]}, 1000) == []
    assert policy.validate_plan({"installments": [{"amount": 100, "due_in_days": 7}, {"amount": 900, "due_in_days": 60}]}, 1000)
    assert policy.validate_plan({"installments": [{"amount": 500, "due_in_days": 7}, {"amount": 400, "due_in_days": 21}]}, 1000)  # wrong total
    fixed = policy.clamp_plan({"installments": [{"amount": 100, "due_in_days": 5}, {"amount": 900, "due_in_days": 90}]}, 1000)
    assert policy.validate_plan(fixed, 1000) == []
    assert policy.validate_reminder({"message": "pay now or we start legal action"})


def test_reminder_needs_approval_then_sends(client):
    inv = client.get("/api/state").json()["invoices"][0]
    r = client.post(f"/api/invoices/{inv['id']}/reminder")
    assert r.status_code == 200
    assert client.post(f"/api/invoices/{inv['id']}/reminder").status_code == 400  # already pending
    from app import paypal_client as pp, db
    pid = db.one("SELECT paypal_id FROM invoices WHERE id=?", (inv["id"],))["paypal_id"]
    assert "reminders" not in json.load(open(pp.MOCK_FILE))[pid]  # nothing sent yet
    aid = client.get("/api/actions").json()[0]["id"]
    assert client.post(f"/api/actions/{aid}/approve").status_code == 200
    assert "reminders" in json.load(open(pp.MOCK_FILE))[pid]


def test_reject_does_nothing(client):
    inv = client.get("/api/state").json()["invoices"][0]
    client.post(f"/api/invoices/{inv['id']}/reminder")
    aid = client.get("/api/actions").json()[0]["id"]
    assert client.post(f"/api/actions/{aid}/reject").status_code == 200
    assert client.get("/api/actions").json() == []


def test_negotiation_creates_plan_and_replaces_invoice(client):
    s = client.get("/api/state").json()
    inv = next(i for i in s["invoices"] if i["client"] == "Lumen Labs")
    n = client.post(f"/api/invoices/{inv['id']}/negotiate").json()
    assert n["agreed"] and len(n["transcript"]) >= 2
    aid = client.get("/api/actions").json()[0]["id"]
    assert client.post(f"/api/actions/{aid}/approve").status_code == 200
    s2 = client.get("/api/state").json()
    assert all(i["id"] != inv["id"] for i in s2["invoices"])
    kids = [i for i in s2["invoices"] if i["is_plan_child"]]
    assert abs(sum(k["amount"] for k in kids) - inv["amount"]) < 0.05


def test_payment_updates_cash_and_learns(client):
    s = client.get("/api/state").json()
    inv = s["invoices"][0]
    before = s["kpis"]["balance"]
    assert client.post(f"/api/invoices/{inv['id']}/simulate-paid").json()["updated"] == [inv["id"]]
    s2 = client.get("/api/state").json()
    assert s2["kpis"]["balance"] == before + inv["amount"]


def test_ai_failure_falls_back_to_rules(client, monkeypatch):
    from app import config
    monkeypatch.setattr(config, "LLM_PROVIDER", "openai")
    monkeypatch.setattr(config, "LLM_API_KEY", "fake")
    monkeypatch.setattr(config, "LLM_BASE_URL", "http://127.0.0.1:9")  # nothing listens here
    inv = client.get("/api/state").json()["invoices"][0]
    r = client.post(f"/api/invoices/{inv['id']}/reminder")
    assert r.status_code == 200 and r.json()["source"] == "rules"
