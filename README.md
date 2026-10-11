# Settle

**Late invoices are what sink small businesses. Settle sees the cash gap coming, then chases the money through PayPal, and asks you before it does anything.**

Built for the *PayPal AI Hackathon* (Oct-Nov 2026). Everything runs on the free **PayPal sandbox**.

## The problem
Freelancers and micro-agencies rarely fail for lack of work. They fail because invoices get paid late and nobody sees the cash gap until rent is due. Chasing clients is awkward, so it gets put off. Today's tools either send identical robotic reminders or just show a dashboard.

## What Settle does
1. **Reads** your open PayPal invoices.
2. **Predicts** when each client will really pay, from their own payment history, and explains why in plain language.
3. **Forecasts** your cash for 90 days (pay early / as usual / late) and tells you the day you run out.
4. **Acts, with your approval.** The AI drafts a reminder, or two agents (Settle and a simulated *client agent*) **negotiate a payment plan**. A human approves; only then does Settle call PayPal.
5. **Verifies.** When a client pays, Settle detects it, updates cash, and learns that client's behaviour.

## How it uses PayPal (sandbox)
PayPal **Invoicing v2 REST API**: create invoice, send, remind, cancel, record payment, read status, plus the client-facing payer page (`recipient_view_url`). A payment plan = cancel the original invoice + create and send N installment invoices.

## How it uses AI
- A **merchant agent** writes reminders (tone chosen from the client's risk) and proposes payment plans.
- A **client agent** (simulated payer with private cash limits) accepts, counters or declines. They negotiate up to 3 rounds.
- Works with **Claude** or any **OpenAI-compatible** API. With no key it falls back to deterministic rules, so a demo never breaks. The fallback is shown in the UI and in the audit log.

## Guardrails (`app/policy.py`) - plain code, so the AI cannot talk its way around them
- Every AI proposal waits in an **approval queue**. Nothing reaches PayPal without a human click.
- A plan may never change the invoice total, needs a first payment of at least 34%, at most 3 installments, finishing within 45 days.
- Reminders are blocked if they contain threats.
- Rules are checked again right before execution. Everything is written to an **audit log**.

## Architecture
```
Browser dashboard (AG Grid + Chart.js)
        |  JSON
FastAPI  app/main.py ──► service.py ──► agent.py ──► llm.py (Claude / OpenAI-compatible / rules)
                              |              └──► policy.py (guardrails)
                              ├──► predict.py (risk + 90-day forecast)
                              ├──► paypal_client.py ──► PayPal sandbox Invoicing API  (or built-in mock)
                              └──► db.py (SQLite: clients, invoices, actions, audit)
```

## Run it (5 minutes, no keys needed)
```bash
python -m venv .venv && source .venv/bin/activate     # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env                                  # Windows: copy .env.example .env
uvicorn app.main:app --port 8000
```
Open http://localhost:8000 and click **Create demo data**. With no PayPal keys the app uses a built-in mock PayPal (badge says *mock mode*).

### Use the real PayPal sandbox
1. In the [PayPal Developer Dashboard](https://developer.paypal.com/dashboard/) (Sandbox toggle on) open *Apps & Credentials*, create an app, copy **Client ID** and **Secret**.
2. Copy the email of your sandbox **Business** account (*Testing Tools -> Sandbox Accounts*).
3. Put them in `.env` as `PAYPAL_CLIENT_ID`, `PAYPAL_SECRET`, `MERCHANT_EMAIL`. Restart. The badge now says *PayPal: sandbox*.
4. Optional: `python scripts/smoke_test.py` checks the PayPal connection step by step.
5. To pay an invoice as the client: click **Client page**, log in at sandbox.paypal.com with a sandbox *Personal* account, pay, then click **Check PayPal for payments**.
6. Optional: to use your own sandbox Personal accounts as the clients, create `data/clients.json`, e.g. `{"Acme Studio": "sb-xxxx@personal.example.com"}`.

### Turn on the AI
In `.env`: `LLM_PROVIDER=anthropic` + `ANTHROPIC_API_KEY=...`, or `LLM_PROVIDER=openai` + `LLM_BASE_URL`, `LLM_API_KEY`, `LLM_MODEL` for any OpenAI-compatible service. The header badge shows which is active.

### Tests
```bash
pytest -q
```

## Honest notes and limitations
- **Payment history is simulated** (generated per client in `app/seed.py`), because the sandbox cannot produce months of real history. Live invoices are real sandbox invoices, and every real payment is added to the client's history.
- The **client agent is simulated**, with its own private limits. It stands in for a customer's own AI assistant.
- Forecast assumption: installments of an agreed plan are predicted to be paid 70% closer to their due date than the client's usual lateness. This is a stated assumption, not a measured fact.
- **Sandbox only.** "Mark paid" records a payment in the sandbox for demos. Payment detection uses polling (a button and a 20-second refresh), not webhooks.
- Sandbox invoice emails are not delivered to real inboxes. Clients pay through the payer page link.

## License
MIT. Bundled third-party libraries in `static/vendor/` keep their own MIT licenses (AG Grid Community, Chart.js).
