/* Settle dashboard. Plain JavaScript, no build step. */
let chart = null, grid = null, STATE = null;

const $ = (id) => document.getElementById(id);
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const money = (n) => "$" + Number(n).toLocaleString("en-US", { minimumFractionDigits: 0, maximumFractionDigits: 2 });
const fmtDate = (iso) => new Date(iso + "T00:00:00").toLocaleDateString("en-US", { month: "short", day: "numeric" });
const addDays = (n) => { const d = new Date(); d.setDate(d.getDate() + n); return d.toISOString().slice(0, 10); };

function toast(msg, isErr) {
  const t = $("toast");
  t.textContent = msg; t.className = "show" + (isErr ? " err" : "");
  clearTimeout(toast._t); toast._t = setTimeout(() => (t.className = ""), isErr ? 7000 : 3200);
}

async function api(path, method = "GET") {
  const r = await fetch(path, { method });
  const data = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(data.detail || r.statusText);
  return data;
}

async function withBusy(btn, fn) {
  if (btn) btn.disabled = true;
  try { return await fn(); } catch (e) { toast(e.message, true); } finally { if (btn) btn.disabled = false; }
}

/* ---------------------------------------------------------------- render */
function renderModes(m) {
  const paypal = m.paypal === "mock"
    ? '<span class="badge warn">PayPal: mock mode (no keys set)</span>'
    : '<span class="badge">PayPal: sandbox</span>';
  const ai = m.ai === "rules"
    ? '<span class="badge warn">AI: rule-based fallback</span>'
    : `<span class="badge">AI: ${esc(m.ai)}</span>`;
  $("modes").innerHTML = paypal + ai;
}

function renderHero(s) {
  const k = s.kpis, h = $("headline");
  h.className = "";
  if (!s.invoices.length && !s.closed.length) {
    h.textContent = "No invoices yet.";
    $("subline").textContent = "Click \u201CCreate demo data\u201D to add five clients and ten real PayPal sandbox invoices.";
  } else if (k.shortfall_expected) {
    h.textContent = `You run out of cash on ${fmtDate(k.shortfall_expected)} if clients pay the way they usually do.`;
    h.className = "bad";
    $("subline").textContent = "Pick an invoice below. The assistant can write a reminder or negotiate a payment plan, and you approve before anything happens.";
  } else if (k.shortfall_worst) {
    h.textContent = `Safe if clients pay as usual, but you could run short on ${fmtDate(k.shortfall_worst)} if they pay late.`;
    $("subline").textContent = "A reminder to the riskiest clients now keeps that gap closed.";
  } else {
    h.textContent = "No cash gap in the next 90 days.";
    h.className = "good";
    $("subline").textContent = "Even if clients pay late, your balance stays above zero.";
  }
  $("strip").innerHTML =
    `<div><b>${money(k.balance)}</b>cash now</div>` +
    `<div><b>${money(k.outstanding)}</b>waiting on clients</div>` +
    `<div><b>${money(k.at_risk)}</b>with habitually late payers</div>`;
}

function renderChart(s) {
  const N = 60, rows = s.forecast.expected.rows.slice(0, N);
  const labels = rows.map((r) => fmtDate(r.date));
  const series = (sc) => s.forecast[sc].rows.slice(0, N).map((r) => r.balance);
  const data = {
    labels,
    datasets: [
      { label: "Pay early", data: series("best"), borderColor: "#4f8f87", borderDash: [6, 4], pointRadius: 0, borderWidth: 2, tension: 0 },
      { label: "Pay as usual", data: series("expected"), borderColor: "#17212b", pointRadius: 0, borderWidth: 3, tension: 0,
        fill: { target: "origin", above: "rgba(11,92,86,0.06)", below: "rgba(179,38,30,0.22)" } },
      { label: "Pay late", data: series("worst"), borderColor: "#b3261e", borderDash: [6, 4], pointRadius: 0, borderWidth: 2, tension: 0 },
    ],
  };
  const opts = {
    responsive: true, maintainAspectRatio: false, animation: false,
    interaction: { mode: "index", intersect: false },
    plugins: { legend: { display: false }, tooltip: { callbacks: { label: (c) => `${c.dataset.label}: ${money(c.parsed.y)}` } } },
    scales: {
      x: { ticks: { maxTicksLimit: 8, color: "#5d6a73" }, grid: { display: false } },
      y: { ticks: { callback: (v) => money(v), color: "#5d6a73" }, grid: { color: (c) => (c.tick.value === 0 ? "#17212b" : "#e4e8e4") } },
    },
  };
  if (chart) { chart.data = data; chart.update(); } else { chart = new Chart($("runway"), { type: "line", data, options: opts }); }
}

function renderGrid(s) {
  if (!grid) {
    grid = agGrid.createGrid($("grid"), {
      rowHeight: 56,
      defaultColDef: { sortable: true, resizable: true, cellStyle: { display: "flex", alignItems: "center" } },
      columnDefs: [
        { field: "client", headerName: "Client", width: 130 },
        { field: "description", headerName: "Work", flex: 1, minWidth: 160, tooltipField: "description" },
        { field: "amount", headerName: "Amount", width: 105, valueFormatter: (p) => money(p.value) },
        { field: "due_date", headerName: "Due", width: 90, valueFormatter: (p) => fmtDate(p.value) },
        { field: "expected_pay_date", headerName: "Likely paid", width: 110, valueFormatter: (p) => fmtDate(p.value) },
        { field: "risk", headerName: "Late risk", width: 100, cellRenderer: (p) => `<span class="risk ${esc(p.value)}">${esc(p.value)}</span>`,
          tooltipValueGetter: (p) => p.data.why },
        { headerName: "", colId: "actions", sortable: false, width: 430, cellRenderer: actionButtons },
      ],
      tooltipShowDelay: 300,
      getRowId: (p) => String(p.data.id),
      overlayNoRowsTemplate: "<span>No open invoices.</span>",
    });
  }
  grid.setGridOption("rowData", s.invoices);
}

function actionButtons(p) {
  const d = p.data, wrap = document.createElement("div");
  wrap.className = "cellbtns";
  const pending = !!d.pending_action;
  const mk = (txt, cls, fn, title) => { const b = document.createElement("button"); b.textContent = txt; b.className = cls; b.title = title || ""; b.onclick = (e) => { e.stopPropagation(); withBusy(b, () => fn(b)); }; wrap.appendChild(b); return b; };
  mk("Draft reminder", "", () => draftReminder(d.id), "AI writes a reminder for you to approve").disabled = pending;
  mk("Negotiate plan", "ghost", () => negotiate(d.id), "Two agents negotiate a payment plan").disabled = pending;
  mk("Client page", "quiet", () => openPayer(d.id), "Open the page where the client pays");
  mk("Mark paid", "quiet", () => simulatePaid(d.id), "Demo helper: pretend the client paid");
  return wrap;
}

function planTable(plan) {
  return '<table class="parts">' + plan.installments.map((p, i) =>
    `<tr><td>Payment ${i + 1} &middot; due ${fmtDate(addDays(p.due_in_days))}</td><td>${money(p.amount)}</td></tr>`).join("") + "</table>";
}

function renderQueue(actions) {
  $("queueCount").textContent = actions.length ? String(actions.length) : "";
  if (!actions.length) {
    $("queue").innerHTML = '<div class="empty">Nothing waiting. Draft a reminder or negotiate a plan from the invoice list.</div>';
    return;
  }
  $("queue").innerHTML = actions.map((a) => {
    const inv = a.invoice, head = `<h3>${esc(inv.client)} &middot; ${money(inv.amount)}</h3><div class="meta">${esc(inv.description)} &middot; due ${fmtDate(inv.due_date)}</div>`;
    if (a.type === "reminder") {
      const p = a.payload;
      return `<div class="card">${head}<div><b>${esc(p.subject)}</b></div><div class="msg">${esc(p.message)}</div>
        <div class="why">Tone: ${esc(p.tone)}. ${esc(p.reasoning)} <i>(written by ${p.source === "ai" ? "AI" : "rules"})</i></div>
        <div class="actions"><button data-a="approve" data-id="${a.id}">Approve and send via PayPal</button><button class="danger" data-a="reject" data-id="${a.id}">Reject</button></div></div>`;
    }
    const chat = a.payload.transcript.map((t) => {
      const client = t.from !== "Settle";
      const extra = t.plan ? planTable(t.plan) : "";
      const dec = t.decision && t.decision !== "counter" ? ` <i>(${esc(t.decision)})</i>` : "";
      return `<div class="bubble ${client ? "client" : ""}"><span class="who">${esc(t.from)}${dec}</span>${esc(t.message)}${extra}</div>`;
    }).join("");
    return `<div class="card plan">${head}<div class="why">The two assistants agreed on this plan:</div>${planTable(a.payload.plan)}
      <details><summary class="why">See the negotiation</summary><div class="chat">${chat}</div></details>
      <div class="why">Approving cancels the original invoice and sends ${a.payload.plan.installments.length} new PayPal invoices.</div>
      <div class="actions"><button data-a="approve" data-id="${a.id}">Approve plan</button><button class="danger" data-a="reject" data-id="${a.id}">Reject</button></div></div>`;
  }).join("");
}

function renderAudit(rows) {
  if (!rows.length) { $("audit").innerHTML = '<div class="empty">No activity yet.</div>'; return; }
  $("audit").innerHTML = rows.slice(0, 14).map((r) => {
    const t = new Date(r.ts).toLocaleTimeString("en-US", { hour: "2-digit", minute: "2-digit", second: "2-digit" });
    return `<div class="arow"><span class="t">${esc(t)}</span><span>${esc(r.actor)}</span><span>${esc(r.event.replaceAll("_", " "))}</span><span class="d">${esc(r.detail || "")}</span></div>`;
  }).join("");
}

/* ---------------------------------------------------------------- actions */
async function refresh() {
  const [s, actions, audit] = await Promise.all([api("/api/state"), api("/api/actions"), api("/api/audit")]);
  STATE = s;
  renderModes(s.modes); renderHero(s); renderChart(s); renderGrid(s); renderQueue(actions); renderAudit(audit);
}

async function draftReminder(id) { await api(`/api/invoices/${id}/reminder`, "POST"); toast("Reminder drafted. Review it in \u201CWaiting for your OK\u201D."); await refresh(); }
async function negotiate(id) {
  const r = await api(`/api/invoices/${id}/negotiate`, "POST");
  toast(r.agreed ? "The agents reached a deal. Review it before it goes out." : "The agents could not agree. Nothing was changed.", !r.agreed);
  await refresh();
}
async function simulatePaid(id) { await api(`/api/invoices/${id}/simulate-paid`, "POST"); toast("Payment recorded. Forecast updated."); await refresh(); }
async function openPayer(id) {
  const r = await api(`/api/invoices/${id}/payer-link`);
  if (!r.url) return toast("PayPal did not return a payer link for this invoice.", true);
  window.open(r.url, "_blank", "noopener");
}

$("queue").addEventListener("click", (e) => {
  const b = e.target.closest("button[data-a]"); if (!b) return;
  withBusy(b, async () => {
    if (b.dataset.a === "approve") { await api(`/api/actions/${b.dataset.id}/approve`, "POST"); toast("Approved and sent through PayPal."); }
    else { await api(`/api/actions/${b.dataset.id}/reject`, "POST"); toast("Rejected. Nothing was sent."); }
    await refresh();
  });
});
$("btnSeed").onclick = (e) => withBusy(e.target, async () => {
  if (STATE && STATE.invoices.length && !confirm("This replaces the current demo data and creates new invoices in PayPal. Continue?")) return;
  toast("Creating demo data in PayPal... this takes a few seconds.");
  const r = await api("/api/seed", "POST"); toast(`Created ${r.invoices} invoices for ${r.clients} clients.`); await refresh();
});
$("btnSync").onclick = (e) => withBusy(e.target, async () => {
  const r = await api("/api/sync", "POST");
  toast(r.updated.length ? `Found ${r.updated.length} payment(s). Forecast updated.` : "No new payments found."); await refresh();
});

refresh().catch((e) => toast(e.message, true));
setInterval(() => { if (!document.hidden) refresh().catch(() => {}); }, 20000);
