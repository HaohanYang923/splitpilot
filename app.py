#!/usr/bin/env python3
"""
SplitPilot v4 - an AI agent that turns a messy group bill into fair
shares, settles who owes the person who paid, and collects through
PayPal. v3 added an AG Grid results grid, a personal payment-link page
per debtor, on-disk state persistence, and multi-currency support
(USD / CAD / EUR / GBP, amounts flow into the PayPal order currency).
v4 is a full visual redesign (see the design-system comment in PAGE):
flat receipt vernacular, IBM Plex Sans / Mono, colour only as money
state, rubber-stamp status badges.

PayPal AI Hackathon 2026 prototype. Python standard library only:
    python3 app.py   ->   http://localhost:8000

PayPal integration
------------------
Orders v2 against the Sandbox (https://api-m.sandbox.paypal.com):
create order -> buyer approval link -> capture. Set PAYPAL_CLIENT_ID
and PAYPAL_CLIENT_SECRET (free Sandbox app at developer.paypal.com)
for live Sandbox orders. Without credentials the app runs in a clearly
labelled Sandbox Demo Mode: it builds the identical order payload and
simulates approval and capture, so the full flow can be judged offline.
A simulated order is never presented as a real one.

AI layer
--------
1. Receipt parser: items, prices, quantities, price-first lines, and
   the people named on each line; unnamed lines are shared.
2. Fair-split engine: tax and tip allocated proportionally; cent-level
   rounding reconciled so shares sum exactly to the bill total.
3. Settlement: if one person paid the whole bill up front, everyone
   else's share becomes an amount owed to that payer; the payer's own
   share is settled automatically.
4. Anomaly check: duplicate lines and a declared total that does not
   match the parsed items are flagged in plain language before any
   money is requested.
5. Explanation generator: a per-person, auditable breakdown of how
   each number was produced.
6. Reminder agent: drafts the follow-up message in a chosen tone from
   the person's actual items.
"""

import base64
import csv
import io
import json
import os
import re
import secrets
import time
import urllib.request
import urllib.error
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

def _load_dotenv():
    """Load KEY=VALUE lines from a .env file next to this file.

    Real environment variables win; the file is only a convenience so
    `python3 app.py` picks up PayPal credentials without exports.
    """
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")
    if not os.path.exists(path):
        return
    for raw in open(path):
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        value = value.strip()
        if key and key not in os.environ:
            os.environ[key] = value


_load_dotenv()

PAYPAL_BASE = "https://api-m.sandbox.paypal.com"
PORT = int(os.environ.get("PORT", "8000"))
CURRENCIES = {"USD": "$", "CAD": "$", "EUR": "€", "GBP": "£"}

# ---------------------------------------------------------------- parsing

MONEY_END = re.compile(r"[$€£]?\s*(\d+(?:\.\d{1,2})?)\s*$")
MONEY_START = re.compile(r"^\s*[$€£]?\s*(\d+(?:\.\d{1,2})?)\s+(.+)$")
TAX_TIP = re.compile(r"^\s*(tax|tip|gratuity|service)\b", re.I)
TOTAL_LINE = re.compile(r"^\s*(grand\s+)?total\b", re.I)
SUBTOTAL_LINE = re.compile(r"^\s*sub\s*total\b", re.I)


def parse_receipt(text, participants):
    """Return (items, tax, tip, declared_total, warnings).

    Line formats:
        Item name (PersonA, PersonB) 12.50     named = those people
        Item name 12.50                        unnamed = shared by all
        12.50 Item name (PersonA)              price-first also works
        Coffee x2 8.00                         quantity stays in the name
        Tax 5.28 / Tip 11.88                   pulled out, split later
        Total 83.16                            checked against the items
    """
    items = []
    tax = 0.0
    tip = 0.0
    declared_total = None
    warnings = []
    names_lower = {}
    for p in participants:
        names_lower[p.lower()] = p

    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            continue
        amount = None
        label = None
        m = MONEY_END.search(line)
        if m:
            amount = float(m.group(1))
            label = line[: m.start()].strip(" -:")
        else:
            m2 = MONEY_START.match(line)
            if m2:
                amount = float(m2.group(1))
                label = m2.group(2).strip(" -:")
        if amount is None:
            warnings.append(f"Could not read a price in: '{line}'")
            continue
        if TOTAL_LINE.match(label):
            declared_total = amount
            continue
        if SUBTOTAL_LINE.match(label):
            continue
        if TAX_TIP.match(label):
            if label.lower().startswith("tax"):
                tax += amount
            else:
                tip += amount
            continue
        assigned = []
        paren = re.search(r"\(([^)]*)\)", label)
        if paren:
            for token in re.split(r"[,/&]|\band\b", paren.group(1)):
                token = token.strip().lower()
                if token in names_lower:
                    assigned.append(names_lower[token])
            label = (label[: paren.start()] + label[paren.end():]).strip(" -:")
        if not assigned:
            assigned = list(participants)
        items.append({"name": label or "Item", "amount": amount,
                      "people": assigned})

    seen = {}
    for it in items:
        key = (it["name"].lower(), it["amount"])
        if key in seen:
            seen[key] += 1
        else:
            seen[key] = 1
    for (name, amt), n in seen.items():
        if n > 1:
            warnings.append(
                f"'{name}' (${amt:.2f}) appears {n} times - check it is "
                "not a duplicate charge before requesting payment.")
    return items, round(tax, 2), round(tip, 2), declared_total, warnings


def compute_split(items, tax, tip, symbol="$"):
    """Return ({person: detail}, bill_total). Shares sum exactly to the
    bill total: rounding drift is given to the largest share."""
    people = {}
    for it in items:
        share = it["amount"] / len(it["people"])
        for p in it["people"]:
            if p not in people:
                people[p] = {"items": [], "subtotal": 0.0}
            people[p]["items"].append(f"{it['name']} ({symbol}{share:.2f})")
            people[p]["subtotal"] += share
    subtotal_all = sum(d["subtotal"] for d in people.values())
    extra = tax + tip
    for d in people.values():
        if subtotal_all:
            d["tax_tip"] = extra * d["subtotal"] / subtotal_all
        else:
            d["tax_tip"] = 0.0
        d["total"] = d["subtotal"] + d["tax_tip"]
    bill_total = round(subtotal_all + extra, 2)
    drift = round(bill_total - sum(round(d["total"], 2)
                                   for d in people.values()), 2)
    if people and drift:
        richest = max(people.values(), key=lambda d: d["total"])
        richest["total"] = round(richest["total"] + drift, 2)
    for d in people.values():
        d["subtotal"] = round(d["subtotal"], 2)
        d["tax_tip"] = round(d["tax_tip"], 2)
        d["total"] = round(d["total"], 2)
    return people, bill_total


def settle(shares, payer):
    """Net settlement. If `payer` fronted the whole bill, each other
    person owes the payer their full share, and the payer owes nothing.
    Returns {person: amount_owed}."""
    owed = {}
    for name, d in shares.items():
        if payer and name == payer:
            owed[name] = 0.0
        else:
            owed[name] = d["total"]
    return owed


def explain(shares, owed, payer, bill_total, symbol="$"):
    """Plain-language, per-person audit trail of the numbers."""
    lines = []
    for name, d in shares.items():
        items_txt = "; ".join(d["items"])
        line = (f"{name}: {items_txt} = {symbol}{d['subtotal']:.2f} of items, "
                f"+ {symbol}{d['tax_tip']:.2f} proportional tax & tip "
                f"= {symbol}{d['total']:.2f}.")
        if payer:
            if name == payer:
                line += (f" {name} paid the {symbol}{bill_total:.2f} bill up "
                         f"front, so this share is already settled.")
            else:
                line += f" Owes {symbol}{owed[name]:.2f} to {payer}."
        lines.append(line)
    if payer and payer in shares:
        recover = round(bill_total - shares[payer]["total"], 2)
        lines.append(f"Total for {payer} to collect back: "
                     f"{symbol}{recover:.2f} of the {symbol}{bill_total:.2f} bill.")
    return lines


def reminder(name, detail, payer, tone, symbol="$"):
    items_txt = ", ".join(i.rsplit(" (", 1)[0] for i in detail["items"])
    amt = f"{symbol}{detail['total']:.2f}"
    if payer:
        to_txt = f" to {payer}"
    else:
        to_txt = ""
    if tone == "firm":
        return (f"Hi {name}, following up on the group bill: your share is "
                f"{amt} ({items_txt}, incl. your part of tax & tip). "
                f"Please send it via the PayPal link{to_txt} today if you "
                "can. Thanks.")
    if tone == "funny":
        return (f"{name}! The bill gremlins have done the maths: {amt} for "
                f"{items_txt} (+ tax & tip). One tap on the PayPal link "
                "and we never speak of this again.")
    return (f"Hi {name}, hope you enjoyed it! Your share comes to {amt} "
            f"({items_txt}, including tax & tip). The PayPal link makes "
            "it a one-tap thing whenever you're ready. Thank you!")


# ---------------------------------------------------------------- PayPal

_TOKEN_CACHE = {"token": None, "expires_at": 0.0}


def paypal_token():
    cid = os.environ.get("PAYPAL_CLIENT_ID")
    secret = os.environ.get("PAYPAL_CLIENT_SECRET")
    if not cid or not secret:
        return None
    # Reuse the cached token until it is about to expire. Live
    # verification (2026-11-07) showed the Sandbox token endpoint can
    # take ~30s to answer, so fetching a fresh token on every call
    # made payments slow and flaky; tokens live for hours.
    if _TOKEN_CACHE["token"] and time.time() < _TOKEN_CACHE["expires_at"]:
        return _TOKEN_CACHE["token"]
    req = urllib.request.Request(
        PAYPAL_BASE + "/v1/oauth2/token",
        data=b"grant_type=client_credentials", method="POST")
    basic = base64.b64encode(f"{cid}:{secret}".encode()).decode()
    req.add_header("Authorization", "Basic " + basic)
    req.add_header("Content-Type", "application/x-www-form-urlencoded")
    last_err = None
    for attempt in range(2):
        try:
            with urllib.request.urlopen(req, timeout=60) as r:
                data = json.load(r)
            _TOKEN_CACHE["token"] = data["access_token"]
            _TOKEN_CACHE["expires_at"] = (
                time.time() + max(60, int(data.get("expires_in", 3600)) - 120))
            return _TOKEN_CACHE["token"]
        except Exception as e:
            last_err = e
    raise last_err


def paypal_post(path, token, payload=None, request_id=None):
    data = None
    if payload is not None:
        data = json.dumps(payload).encode()
    req = urllib.request.Request(PAYPAL_BASE + path, data=data, method="POST")
    req.add_header("Authorization", f"Bearer {token}")
    req.add_header("Content-Type", "application/json")
    if request_id:
        # PayPal deduplicates retries that reuse the same request id,
        # so retrying a dropped connection cannot create a duplicate.
        req.add_header("PayPal-Request-Id", request_id)
    # The Sandbox occasionally drops a connection mid-request; retry
    # once on transport errors (HTTP errors are real answers - raise).
    last_err = None
    for attempt in range(2):
        try:
            with urllib.request.urlopen(req, timeout=60) as r:
                return json.load(r)
        except urllib.error.HTTPError:
            raise
        except Exception as e:
            last_err = e
    raise last_err


def create_order(person, amount, description, currency="USD"):
    """Create a PayPal Sandbox order (Orders v2, intent CAPTURE)."""
    payload = {
        "intent": "CAPTURE",
        "purchase_units": [{
            "description": description[:127],
            "amount": {"currency_code": currency, "value": f"{amount:.2f}"},
        }],
        "application_context": {
            "brand_name": "SplitPilot",
            "user_action": "PAY_NOW",
        },
    }
    token = paypal_token()
    if token is None:
        oid = "SIM-" + base64.urlsafe_b64encode(
            os.urandom(6)).decode().rstrip("=")
        return {"mode": "sandbox-demo (no credentials configured)",
                "id": oid, "status": "CREATED", "payload": payload,
                "approve_link": None}
    request_id = "splitpilot-" + base64.urlsafe_b64encode(
        os.urandom(9)).decode().rstrip("=")
    order = paypal_post("/v2/checkout/orders", token, payload, request_id)
    link = None
    for l in order.get("links", []):
        if l["rel"] == "approve":
            link = l["href"]
    return {"mode": "paypal-sandbox", "id": order["id"],
            "status": order.get("status", "CREATED"),
            "payload": payload, "approve_link": link}


def capture_order(order_id):
    """Capture an approved order. Simulated for SIM- ids."""
    if order_id.startswith("SIM-"):
        return {"mode": "sandbox-demo", "id": order_id,
                "status": "COMPLETED"}
    token = paypal_token()
    if token is None:
        return {"mode": "sandbox-demo", "id": order_id,
                "status": "COMPLETED"}
    result = paypal_post(f"/v2/checkout/orders/{order_id}/capture",
                         token, {}, f"capture-{order_id}")
    return {"mode": "paypal-sandbox", "id": order_id,
            "status": result.get("status", "COMPLETED")}


# ---------------------------------------------------------------- web app

PAGE = r"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>SplitPilot - AI bill splitting with PayPal</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=IBM+Plex+Mono:wght@400;500;600&family=IBM+Plex+Sans:wght@400;500;600;700&display=swap" rel="stylesheet">
<link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/ag-grid-community@32.3.3/styles/ag-grid.css">
<link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/ag-grid-community@32.3.3/styles/ag-theme-quartz.css">
<script src="https://cdn.jsdelivr.net/npm/ag-grid-community@32.3.3/dist/ag-grid-community.min.js"></script>
<style>
/* SplitPilot design system (v4)
   Direction: the printed receipt, treated seriously. Flat, no shadows,
   no gradients. Colour only ever means money state: PayPal blue = act,
   green = settled, red = owed. All numbers in IBM Plex Mono, tabular,
   like a till printout. Signature element: the rubber-stamp status. */
:root{--paper:#F8FAFC;--card:#FFFFFF;--ink:#0F172A;--muted:#475569;
 --faint:#64748B;--line:#DCE5E1;--line-soft:#E1F2ED;--paypal:#0070BA;
 --paypal-dark:#005EA6;--settled:#047857;--settled-fill:#059669;
 --owed:#DC2626;--warn-bg:#FEF2F2;--warn-ink:#7F1D1D}
*{box-sizing:border-box}
body{font-family:'IBM Plex Sans',system-ui,-apple-system,sans-serif;
 margin:0;background:var(--paper);color:var(--ink);font-size:16px;line-height:1.5}
main{max-width:880px;margin:0 auto;padding:28px 16px 64px}
h1{font-size:34px;font-weight:700;letter-spacing:-.02em;margin:6px 0 6px;
 display:flex;align-items:center;gap:10px}
h1 svg{flex:none}
.sub{color:var(--muted);margin:0;max-width:62ch}
.steps{display:flex;background:var(--card);border:1px solid var(--line);
 border-radius:10px;margin:18px 0;overflow:hidden}
.step{flex:1;padding:10px 14px;font-size:13px;color:var(--muted)}
.step + .step{border-left:1px dashed #CBD5E1}
.step b{color:var(--ink);display:block;font-size:14px}
.card{background:var(--card);border:1px solid var(--line);border-radius:10px;
 padding:20px;margin:16px 0}
label{display:block;font-size:13px;font-weight:500;color:var(--muted);margin:14px 0 5px}
textarea,input,select{width:100%;background:#fff;color:var(--ink);
 border:1px solid #CBD5E1;border-radius:8px;padding:11px;font-size:15px;
 font-family:inherit}
textarea{font-family:'IBM Plex Mono',ui-monospace,monospace;font-size:13.5px;line-height:1.55}
:focus-visible{outline:2px solid var(--paypal);outline-offset:2px}
button{background:var(--paypal);color:#fff;border:1px solid var(--paypal);
 border-radius:8px;padding:11px 20px;min-height:44px;font-size:15px;
 font-weight:600;cursor:pointer;margin-top:14px;transition:background-color .15s ease}
button:hover{background:var(--paypal-dark)}
button.secondary{background:#fff;color:var(--ink);border:1px solid #94A3B8;
 font-weight:500;margin-top:8px}
button.secondary:hover{border-color:var(--ink);background:#fff}
button.preset{background:#fff;color:var(--ink);border:1px solid #CBD5E1;
 font-weight:500;margin:2px 6px 2px 0;min-height:40px;padding:8px 14px}
button.preset:hover{border-color:var(--paypal);color:var(--paypal)}
table{width:100%;border-collapse:collapse;font-size:14px}
td,th{border-bottom:1px solid #E2E8F0;padding:8px;text-align:left;vertical-align:top}
th{color:var(--muted);font-weight:500}
.warn{background:var(--warn-bg);border:1px solid #FCA5A5;color:var(--warn-ink);
 border-radius:8px;padding:11px 13px;margin:10px 0;font-size:14px}
.badge{font-family:'IBM Plex Mono',monospace;font-size:11px;background:#F0F8F6;
 border:1px solid var(--line-soft);color:var(--muted);border-radius:4px;padding:2px 7px}
.paid,.due{font-family:'IBM Plex Mono',monospace;font-size:12px;font-weight:700;
 letter-spacing:.05em;display:inline-block;padding:1px 8px;border-radius:4px;
 transform:rotate(-2deg)}
.paid{color:var(--settled);border:2px solid var(--settled)}
.due{color:var(--owed);border:2px solid var(--owed)}
.small{font-size:12.5px;color:var(--faint);line-height:1.55}
.bar{height:8px;background:#E8EFEC;border-radius:99px;overflow:hidden;margin:10px 0 6px}
.barfill{height:100%;background:var(--settled-fill);width:0%;transition:width .3s ease}
h3.total{font-family:'IBM Plex Mono',monospace;font-size:21px;font-weight:600;margin:2px 0 4px}
.explain{font-family:'IBM Plex Mono',monospace;font-size:12.5px;color:#334155;
 line-height:1.65;border-top:1px dashed #CBD5E1;padding-top:12px}
.agwrap{height:208px;width:100%;margin:12px 0;
 --ag-font-family:'IBM Plex Sans',sans-serif;--ag-header-background-color:#F0F8F6;
 --ag-row-hover-color:#F0F8F6;--ag-border-color:#E1F2ED;
 --ag-header-foreground-color:#0F172A;--ag-data-color:#0F172A}
.actionrow{border-top:1px dashed #CBD5E1;padding:13px 0;font-size:15px}
.actionrow .small{word-break:break-all}
a{color:var(--paypal)}
@media (prefers-reduced-motion: reduce){
 *{transition:none !important;animation:none !important}}
@media (max-width:640px){.steps{flex-direction:column}
 .step + .step{border-left:0;border-top:1px dashed #CBD5E1}}
</style></head><body><main>
<h1><svg width="30" height="30" viewBox="0 0 24 24" aria-hidden="true"><path d="M6 2h12v20l-2-1.4L14 22l-2-1.4L10 22l-2-1.4L6 22V2z" fill="#0070BA"/><rect x="9" y="7" width="6" height="1.8" fill="#fff"/><rect x="9" y="11" width="6" height="1.8" fill="#fff"/><rect x="9" y="15" width="4" height="1.8" fill="#fff"/></svg>SplitPilot</h1>
<p class="sub">Paste a messy group bill. AI splits it fairly to the cent,
checks it for mistakes, works out who owes the person who paid, and
collects through PayPal.</p>
<div class="steps">
<div class="step"><b>1. Paste</b>the receipt and the people</div>
<div class="step"><b>2. Split</b>AI checks it and does the maths</div>
<div class="step"><b>3. Collect</b>PayPal requests, tracked live</div>
</div>

<div class="card">
<div>
<button class="preset" onclick="preset('dinner')">Dinner</button>
<button class="preset" onclick="preset('trip')">Road trip</button>
<button class="preset" onclick="preset('flat')">Flat utilities</button>
</div>
<label>Who was there? (comma separated)</label>
<input id="people" value="Alice, Bob, Carol">
<label>Who paid the whole bill up front? (leave blank if everyone pays separately)</label>
<input id="payer" value="Alice">
<label>Receipt - one line per item. Names in (brackets) for personal items;
no brackets means shared. A Total line is checked against the items.</label>
<textarea id="receipt" rows="8"></textarea>
<label>Reminder tone</label>
<select id="tone"><option value="polite">Polite</option>
<option value="firm">Firm</option><option value="funny">Funny</option></select>
<label>Currency</label>
<select id="currency"><option value="USD">USD ($)</option>
<option value="CAD">CAD ($)</option><option value="EUR">EUR (€)</option>
<option value="GBP">GBP (£)</option></select>
<button onclick="split()">Split it with AI</button>
</div>

<div id="out"></div>

<script>
const PRESETS = {
 dinner: {people: "Alice, Bob, Carol", payer: "Alice",
  receipt: "Margherita Pizza 24.00\nCaesar Salad (Alice) 14.00\nTruffle Pasta (Bob) 22.00\nSparkling Water (Carol) 6.00\nTax 5.28\nTip 11.88\nTotal 83.16"},
 trip: {people: "Alice, Bob, Carol, Dan", payer: "Bob",
  receipt: "Gas 62.40\nHotel Room Night 1 148.00\nHotel Room Night 2 148.00\nTolls 17.25\nCoffee x4 (Alice, Bob, Carol, Dan) 21.00\nTax 9.60\nTotal 406.25"},
 flat: {people: "Alice, Bob, Carol", payer: "Carol",
  receipt: "Electricity 96.30\nInternet 59.99\nWater 41.20\nCleaning Supplies 18.45\nTotal 215.94"}
};
let shares = {}, owed = {}, payer = "", links = {}, sym = "$";
function preset(k){
  const p = PRESETS[k];
  document.getElementById('people').value = p.people;
  document.getElementById('payer').value = p.payer;
  document.getElementById('receipt').value = p.receipt;
}
preset('dinner');

async function split(){
  const body = {people: document.getElementById('people').value,
    payer: document.getElementById('payer').value,
    receipt: document.getElementById('receipt').value,
    currency: document.getElementById('currency').value,
    tone: document.getElementById('tone').value};
  const r = await fetch('/api/split', {method:'POST',
    headers:{'Content-Type':'application/json'}, body: JSON.stringify(body)});
  const d = await r.json();
  if (d.error){ alert(d.error); return; }
  shares = d.shares; owed = d.owed; payer = d.payer || "";
  links = d.links || {}; sym = d.symbol || "$";
  let h = '';
  d.warnings.forEach(w => h += '<div class="warn">AI check: ' + w + '</div>');
  h += '<div class="card"><h3 class="total">Bill total: ' + sym + d.bill_total.toFixed(2) +
    ' (' + d.currency + ')</h3>';
  h += '<div class="bar"><div class="barfill" id="barfill"></div></div>' +
    '<div class="small" id="progress">Nothing collected yet</div>';
  h += '<div id="grid" class="ag-theme-quartz agwrap"></div>';
  h += '<div id="actions"></div>';
  h += '<h4>How AI got these numbers</h4><div class="explain">' +
    d.explanation.map(e => '<div>' + e + '</div>').join('') + '</div>';
  h += '<button class="secondary" onclick="exportSummary()">Copy shareable summary</button> ' +
    '<button class="secondary" onclick="exportCsv()">Download CSV</button>' +
    '<div class="small" id="exported"></div></div>';
  document.getElementById('out').innerHTML = h;
  renderGrid(d);
  renderActions();
}
function statusOf(name){
  if (owed[name] === 0) return 'Settled';
  return 'Unpaid';
}
function renderGrid(d){
  const rows = Object.entries(d.shares).map(([name, s]) => ({
    person: name, items: s.items.join(', '), subtotal: s.subtotal,
    taxTip: s.tax_tip, share: s.total, owes: owed[name],
    status: statusOf(name)}));
  const el = document.getElementById('grid');
  if (window.agGrid && agGrid.createGrid){
    agGrid.createGrid(el, {
      columnDefs: [
        {field: 'person', headerName: 'Person', width: 105, sortable: true, filter: true},
        {field: 'items', headerName: 'Items', flex: 1, minWidth: 190, wrapText: true},
        {field: 'subtotal', headerName: 'Subtotal', width: 112, sortable: true,
         valueFormatter: p => sym + p.value.toFixed(2)},
        {field: 'taxTip', headerName: 'Tax & tip', width: 108, sortable: true,
         valueFormatter: p => sym + p.value.toFixed(2)},
        {field: 'share', headerName: 'Share', width: 100, sortable: true,
         valueFormatter: p => sym + p.value.toFixed(2)},
        {field: 'owes', headerName: 'Owes', width: 100, sortable: true,
         valueFormatter: p => sym + p.value.toFixed(2)},
        {field: 'status', headerName: 'Status', width: 98, sortable: true, filter: true}],
      rowData: rows, domLayout: 'normal'});
  } else {
    let t = '<table><tr><th>Person</th><th>Items</th><th>Share</th><th>Owes</th><th>Status</th></tr>';
    rows.forEach(r2 => t += '<tr><td>' + r2.person + '</td><td class="small">' +
      r2.items + '</td><td>' + sym + r2.share.toFixed(2) + '</td><td>' + sym +
      r2.owes.toFixed(2) + '</td><td>' + r2.status + '</td></tr>');
    el.innerHTML = t + '</table><div class="small">AG Grid CDN unreachable - plain table fallback shown.</div>';
  }
}
function renderActions(){
  let h = '';
  for (const name of Object.keys(shares)){
    h += '<div class="actionrow"><b>' + name + '</b> - <span id="st-' + name + '">' +
      (owed[name] === 0 ? '<span class="paid">Settled (paid up front)</span>'
        : '<span class="due">Unpaid</span>') + '</span>';
    if (owed[name] > 0){
      h += '<br><button class="secondary" onclick="pay(\'' + name + '\')">Request payment</button> ' +
        '<button class="secondary" onclick="remind(\'' + name + '\')">Reminder text</button> ' +
        '<button class="secondary" onclick="copyLink(\'' + name + '\')">Copy personal payment link</button>' +
        '<div class="small">' + location.origin + links[name] + '</div>';
    }
    h += '<div class="small" id="pay-' + name + '"></div>' +
      '<div class="small" id="rem-' + name + '"></div></div>';
  }
  document.getElementById('actions').innerHTML = h;
}
async function copyLink(name){
  await navigator.clipboard.writeText(location.origin + links[name]);
  document.getElementById('pay-' + name).textContent = 'Personal payment link copied - send it to ' + name + '.';
}
async function pay(name){
  const r = await fetch('/api/pay', {method:'POST',
    headers:{'Content-Type':'application/json'},
    body: JSON.stringify({name: name, amount: owed[name]})});
  const d = await r.json();
  const el = document.getElementById('pay-' + name);
  let h = 'Order ' + d.id + '<br><span class="badge">' + d.mode + '</span>';
  if (d.approve_link){
    h += '<br><a style="color:#6db3f2" target="_blank" href="' + d.approve_link + '">Open PayPal approval</a>';
  }
  h += '<br><button class="secondary" onclick="capture(\'' + name + '\',\'' + d.id + '\')">Capture payment</button>';
  el.innerHTML = h;
}
async function capture(name, orderId){
  const r = await fetch('/api/capture', {method:'POST',
    headers:{'Content-Type':'application/json'},
    body: JSON.stringify({name: name, order_id: orderId})});
  const d = await r.json();
  if (d.status === 'COMPLETED'){
    document.getElementById('st-' + name).innerHTML = '<span class="paid">Paid</span>';
    document.getElementById('pay-' + name).innerHTML =
      '<span class="paid">Captured: ' + d.id + '</span>';
    refreshProgress();
  }
}
async function refreshProgress(){
  const r = await fetch('/api/status');
  const d = await r.json();
  const pct = d.to_collect > 0 ? Math.round(100 * d.collected / d.to_collect) : 100;
  document.getElementById('barfill').style.width = pct + '%';
  document.getElementById('progress').textContent =
    'Collected ' + sym + d.collected.toFixed(2) + ' of ' + sym + d.to_collect.toFixed(2) + ' (' + pct + '%)';
}
async function remind(name){
  const r = await fetch('/api/reminder', {method:'POST',
    headers:{'Content-Type':'application/json'},
    body: JSON.stringify({name: name,
      tone: document.getElementById('tone').value})});
  const d = await r.json();
  document.getElementById('rem-' + name).textContent = d.text;
}
async function exportSummary(){
  const r = await fetch('/api/export');
  const d = await r.json();
  await navigator.clipboard.writeText(d.text);
  document.getElementById('exported').textContent = 'Summary copied - paste it into the group chat.';
}
async function exportCsv(){
  const r = await fetch('/api/export');
  const d = await r.json();
  const a = document.createElement('a');
  a.href = URL.createObjectURL(new Blob([d.csv], {type: 'text/csv'}));
  a.download = 'splitpilot.csv'; a.click();
}
</script></main></body></html>"""

STATE = {"shares": {}, "owed": {}, "payer": "", "paid": {}, "orders": {},
         "tokens": {}, "currency": "USD", "symbol": "$"}

DATA_FILE = os.environ.get(
    "SPLITPILOT_DATA",
    os.path.join(os.path.dirname(os.path.abspath(__file__)),
                 "data", "state.json"))


def save_state():
    """Persist the current split so a server restart (or a judge
    revisiting the hosted demo tomorrow) does not lose it."""
    try:
        os.makedirs(os.path.dirname(DATA_FILE), exist_ok=True)
        with open(DATA_FILE, "w") as f:
            json.dump(STATE, f)
    except OSError:
        pass  # persistence is best-effort; the app still works in memory


def load_state():
    try:
        with open(DATA_FILE) as f:
            saved = json.load(f)
        for key in STATE:
            if key in saved:
                STATE[key] = saved[key]
    except (OSError, ValueError):
        pass


def new_tokens(owed):
    """One unguessable payment-link token per person who owes money."""
    tokens = {}
    for name, amount in owed.items():
        if amount > 0:
            tokens[secrets.token_urlsafe(9)] = name
    return tokens


PAY_PAGE = r"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Your share - SplitPilot</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=IBM+Plex+Mono:wght@400;500;600&family=IBM+Plex+Sans:wght@400;500;600;700&display=swap" rel="stylesheet">
<style>
*{box-sizing:border-box}
body{font-family:'IBM Plex Sans',system-ui,-apple-system,sans-serif;margin:0;
 background:#F8FAFC;color:#0F172A;font-size:16px;line-height:1.5}
main{max-width:560px;margin:0 auto;padding:28px 16px 64px}
h1{font-size:26px;font-weight:700;letter-spacing:-.02em}
.card{background:#fff;border:1px solid #DCE5E1;border-radius:10px;
 padding:20px;margin:16px 0}
button{background:#0070BA;color:#fff;border:1px solid #0070BA;border-radius:8px;
 padding:12px 20px;min-height:44px;font-size:15px;font-weight:600;
 cursor:pointer;margin-top:12px;transition:background-color .15s ease}
button:hover{background:#005EA6}
button.secondary{background:#fff;color:#0F172A;border:1px solid #94A3B8;font-weight:500}
button.secondary:hover{border-color:#0F172A}
:focus-visible{outline:2px solid #0070BA;outline-offset:2px}
.small{font-size:13px;color:#64748B;line-height:1.6}
.items{font-family:'IBM Plex Mono',monospace;font-size:13.5px;line-height:1.65;
 color:#334155;border-bottom:1px dashed #CBD5E1;padding-bottom:12px}
.total{font-family:'IBM Plex Mono',monospace;font-size:24px;font-weight:600}
.paid{font-family:'IBM Plex Mono',monospace;font-size:13px;font-weight:700;
 letter-spacing:.05em;display:inline-block;padding:2px 10px;border-radius:4px;
 transform:rotate(-2deg);color:#047857;border:2px solid #047857}
.badge{font-family:'IBM Plex Mono',monospace;font-size:11px;background:#F0F8F6;
 border:1px solid #E1F2ED;color:#475569;border-radius:4px;padding:2px 7px}
a{color:#0070BA}
@media (prefers-reduced-motion: reduce){*{transition:none !important}}
</style></head><body><main>
<h1>Hi __NAME__, here is your share</h1>
<div class="card">
<div class="items">__ITEMS__</div>
<p>Items: __SYMBOL____SUBTOTAL__<br>Tax &amp; tip (your share): __SYMBOL____TAXTIP__<br>
<span class="total">Total: __SYMBOL____TOTAL__</span></p>
<p>__PAYER_LINE__</p>
<div id="zone">__ZONE__</div>
</div>
<p class="small">Split by SplitPilot with AI. Payment runs through the
PayPal Sandbox - no real money moves.</p>
<script>
const TOKEN = "__TOKEN__";
async function pay(){
  const r = await fetch('/api/pay-for/' + TOKEN, {method:'POST',
    headers:{'Content-Type':'application/json'}, body:'{}'});
  const d = await r.json();
  const el = document.getElementById('zone');
  if (d.error){ el.textContent = d.error; return; }
  let h = 'Order ' + d.id + ' <span class="badge">' + d.mode + '</span>';
  if (d.approve_link){
    h += '<br><a target="_blank" href="' + d.approve_link + '">Open PayPal approval</a>';
  }
  h += '<br><button class="secondary" onclick="capture()">I approved - capture it</button>';
  el.innerHTML = h;
}
async function capture(){
  const r = await fetch('/api/capture-for/' + TOKEN, {method:'POST',
    headers:{'Content-Type':'application/json'}, body:'{}'});
  const d = await r.json();
  if (d.status === 'COMPLETED'){
    document.getElementById('zone').innerHTML =
      '<span class="paid">Paid - thank you!</span>';
  }
}
</script></main></body></html>"""


def render_pay_page(token):
    name = STATE["tokens"].get(token)
    if not name:
        return None
    d = STATE["shares"][name]
    symbol = STATE["symbol"]
    items = "<br>".join(d["items"])
    if STATE["payer"]:
        payer_line = (f"{STATE['payer']} paid the full bill up front, so "
                      f"this share goes to {STATE['payer']}.")
    else:
        payer_line = "This is your share of a group bill."
    if name in STATE["paid"]:
        zone = '<span class="paid">Already paid - thank you!</span>'
    else:
        zone = ('<button onclick="pay()">Pay '
                f'{symbol}{STATE["owed"][name]:.2f} with PayPal</button>')
    page = PAY_PAGE
    page = page.replace("__TOKEN__", token)
    page = page.replace("__NAME__", name)
    page = page.replace("__ITEMS__", items)
    page = page.replace("__SYMBOL__", symbol)
    page = page.replace("__SUBTOTAL__", f"{d['subtotal']:.2f}")
    page = page.replace("__TAXTIP__", f"{d['tax_tip']:.2f}")
    page = page.replace("__TOTAL__", f"{d['total']:.2f}")
    page = page.replace("__PAYER_LINE__", payer_line)
    page = page.replace("__ZONE__", zone)
    return page


def export_data():
    out = io.StringIO()
    w = csv.writer(out)
    w.writerow(["person", "items_subtotal", "tax_tip", "share_total",
                "owes", "status"])
    for name, d in STATE["shares"].items():
        if name in STATE["paid"]:
            status = "paid"
        elif STATE["owed"].get(name, 0) == 0:
            status = "settled"
        else:
            status = "unpaid"
        w.writerow([name, f"{d['subtotal']:.2f}", f"{d['tax_tip']:.2f}",
                    f"{d['total']:.2f}",
                    f"{STATE['owed'].get(name, 0):.2f}", status])
    lines = ["SplitPilot summary"]
    for name, d in STATE["shares"].items():
        owe = STATE["owed"].get(name, 0)
        if owe:
            lines.append(f"{name} owes {STATE['symbol']}{owe:.2f} "
                         f"(share {STATE['symbol']}{d['total']:.2f})")
        else:
            lines.append(f"{name}: share {STATE['symbol']}{d['total']:.2f} - settled")
    return "\n".join(lines), out.getvalue()


class Handler(BaseHTTPRequestHandler):
    def _json(self, obj, code=200):
        body = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _body(self):
        n = int(self.headers.get("Content-Length", 0))
        return json.loads(self.rfile.read(n) or b"{}")

    def do_GET(self):
        if self.path in ("/", "/index.html"):
            body = PAGE.encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        elif self.path == "/health":
            self._json({"ok": True, "version": 4})
        elif self.path.startswith("/pay/"):
            page = render_pay_page(self.path[len("/pay/"):])
            if page is None:
                self._json({"error": "link not found or expired"}, 404)
            else:
                body = page.encode()
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
        elif self.path == "/api/status":
            to_collect = round(sum(STATE["owed"].values()), 2)
            collected = 0.0
            for name in STATE["paid"]:
                collected += STATE["owed"].get(name, 0)
            self._json({"to_collect": to_collect,
                        "collected": round(collected, 2),
                        "paid": list(STATE["paid"].keys())})
        elif self.path == "/api/export":
            text, csv_text = export_data()
            self._json({"text": text, "csv": csv_text})
        else:
            self._json({"error": "not found"}, 404)

    def do_POST(self):
        try:
            data = self._body()
            if self.path == "/api/split":
                participants = []
                for p in data.get("people", "").split(","):
                    p = p.strip()
                    if p:
                        participants.append(p)
                if not participants:
                    return self._json({"error": "add at least one person"}, 400)
                payer = data.get("payer", "").strip()
                if payer and payer not in participants:
                    return self._json(
                        {"error": f"payer '{payer}' is not in the people list"},
                        400)
                items, tax, tip, declared, warnings = parse_receipt(
                    data.get("receipt", ""), participants)
                if not items:
                    return self._json({"error": "no items parsed"}, 400)
                currency = data.get("currency", "USD")
                if currency not in CURRENCIES:
                    currency = "USD"
                symbol = CURRENCIES[currency]
                shares, bill_total = compute_split(items, tax, tip, symbol)
                if declared is not None and abs(declared - bill_total) > 0.02:
                    warnings.append(
                        f"The receipt declares a total of {symbol}{declared:.2f} "
                        f"but the items, tax and tip add up to "
                        f"{symbol}{bill_total:.2f} - a "
                        f"{symbol}{abs(declared - bill_total):.2f} "
                        "difference. Check the bill before collecting.")
                owed = settle(shares, payer)
                tokens = new_tokens(owed)
                STATE.update({"shares": shares, "owed": owed,
                              "payer": payer, "paid": {}, "orders": {},
                              "tokens": tokens, "currency": currency,
                              "symbol": symbol})
                save_state()
                links = {}
                for token, person in tokens.items():
                    links[person] = "/pay/" + token
                return self._json({
                    "shares": shares, "owed": owed, "payer": payer,
                    "bill_total": bill_total, "warnings": warnings,
                    "currency": currency, "symbol": symbol, "links": links,
                    "explanation": explain(shares, owed, payer, bill_total,
                                           symbol)})
            if self.path == "/api/pay":
                name = data.get("name", "")
                amount = float(data.get("amount", 0))
                if amount <= 0:
                    return self._json({"error": "nothing to collect"}, 400)
                order = create_order(
                    name, amount,
                    f"SplitPilot: {name}'s share of a group bill",
                    STATE["currency"])
                STATE["orders"][name] = order["id"]
                save_state()
                return self._json(order)
            if self.path.startswith("/api/pay-for/"):
                token = self.path[len("/api/pay-for/"):]
                name = STATE["tokens"].get(token)
                if not name:
                    return self._json({"error": "link not found"}, 404)
                if name in STATE["paid"]:
                    return self._json({"error": "already paid"}, 400)
                order = create_order(
                    name, STATE["owed"][name],
                    f"SplitPilot: {name}'s share of a group bill",
                    STATE["currency"])
                STATE["orders"][name] = order["id"]
                save_state()
                return self._json(order)
            if self.path == "/api/capture":
                name = data.get("name", "")
                order_id = data.get("order_id", "")
                result = capture_order(order_id)
                if result["status"] == "COMPLETED":
                    STATE["paid"][name] = order_id
                    save_state()
                return self._json(result)
            if self.path.startswith("/api/capture-for/"):
                token = self.path[len("/api/capture-for/"):]
                name = STATE["tokens"].get(token)
                if not name:
                    return self._json({"error": "link not found"}, 404)
                order_id = STATE["orders"].get(name, "")
                if not order_id:
                    return self._json({"error": "create the order first"}, 400)
                result = capture_order(order_id)
                if result["status"] == "COMPLETED":
                    STATE["paid"][name] = order_id
                    save_state()
                return self._json(result)
            if self.path == "/api/reminder":
                name = data.get("name", "")
                detail = STATE["shares"].get(name)
                if not detail:
                    return self._json({"error": "split first"}, 400)
                detail = dict(detail)
                detail["total"] = STATE["owed"].get(name, detail["total"])
                return self._json({"text": reminder(
                    name, detail, STATE["payer"],
                    data.get("tone", "polite"), STATE["symbol"])})
            return self._json({"error": "not found"}, 404)
        except urllib.error.URLError as e:
            return self._json({"error": f"PayPal sandbox unreachable: {e}"}, 502)
        except Exception as e:
            return self._json({"error": str(e)}, 500)

    def log_message(self, *args):
        pass


if __name__ == "__main__":
    load_state()
    if os.environ.get("PAYPAL_CLIENT_ID"):
        mode = "PayPal Sandbox (credentials found)"
    else:
        mode = ("Sandbox Demo Mode (set PAYPAL_CLIENT_ID / "
                "PAYPAL_CLIENT_SECRET for live Sandbox orders)")
    print(f"SplitPilot v4 on http://localhost:{PORT} - {mode}")
    ThreadingHTTPServer(("0.0.0.0", PORT), Handler).serve_forever()
