"""Creates the demo world: 5 clients, simulated payment history, expenses, and REAL PayPal sandbox invoices."""
import json
import os
import random
from datetime import date, timedelta

from . import db, paypal_client as pp

# name -> (typical days late, spread, simulated cash-now share, simulated max days to settle, default email)
CLIENTS = {
    "Acme Studio":   (9, 3, 0.40, 30, "acme@example.com"),
    "Brightwave":    (0, 1, 0.80, 20, "brightwave@example.com"),
    "Northfield Co": (4, 6, 0.36, 35, "northfield@example.com"),
    "Lumen Labs":    (14, 4, 0.40, 40, "lumen@example.com"),
    "Orbit Foods":   (1, 2, 0.60, 25, "orbit@example.com"),
}
# (client, description, amount, due in N days)
INVOICES = [
    ("Acme Studio", "Brand identity - phase 2", 2400, 5),
    ("Acme Studio", "Landing page build", 900, 21),
    ("Lumen Labs", "Mobile app prototype", 3100, 9),
    ("Lumen Labs", "Monthly retainer", 1200, 30),
    ("Brightwave", "Website maintenance", 1800, 12),
    ("Brightwave", "SEO audit", 650, 28),
    ("Northfield Co", "Product photography", 2200, 7),
    ("Northfield Co", "Social media pack", 1500, 25),
    ("Orbit Foods", "Menu redesign", 800, 14),
    ("Orbit Foods", "Packaging mockups", 1100, 33),
]
# (name, amount, due in N days)
EXPENSES = [("Software subscriptions", 220, 3), ("Rent", 3900, 11),
            ("Contractor payment", 2000, 13), ("Quarterly tax", 1800, 34)]
OPENING_BALANCE = 1500.0


def _client_emails() -> dict:
    """Optional: put your sandbox Personal emails in data/clients.json -> {"Acme Studio": "sb-...@personal.example.com"}"""
    path = os.path.join("data", "clients.json")
    if os.path.exists(path):
        with open(path) as f:
            return json.load(f)
    return {}


def run():
    random.seed(7)
    emails = _client_emails()
    db.wipe()
    ids = {}
    for name, (mu, sd, cash, maxd, email) in CLIENTS.items():
        history = [max(-3, round(random.gauss(mu, sd))) for _ in range(8)]
        ids[name] = db.execute(
            "INSERT INTO clients(name,email,history,cash_now_pct,max_days) VALUES (?,?,?,?,?)",
            (name, emails.get(name, email), json.dumps(history), cash, maxd))
    today = date.today()
    for name, amt, days in EXPENSES:
        db.execute("INSERT INTO expenses(name,amount,date) VALUES (?,?,?)",
                   (name, amt, (today + timedelta(days=days)).isoformat()))
    db.set_setting("balance", OPENING_BALANCE)
    created = 0
    for client, desc, amount, due_in in INVOICES:
        due = (today + timedelta(days=due_in)).isoformat()
        email = emails.get(client, CLIENTS[client][4])
        pid = pp.create_invoice(client, email, [(desc, amount)], due, note="Thank you for your business!")
        pp.send_invoice(pid)
        db.execute(
            "INSERT INTO invoices(paypal_id,client_id,description,amount,due_date,status,parent_id,created) "
            "VALUES (?,?,?,?,?,?,?,?)", (pid, ids[client], desc, amount, due, "SENT", None, db.now()))
        created += 1
    db.audit("system", "seeded_demo_data", {"clients": len(CLIENTS), "invoices": created, "paypal_mode": pp.mode()})
    return {"clients": len(CLIENTS), "invoices": created}
