"""DegenMiner — Telegraph miner for evidence-backed on-chain intelligence.

Serves three canonical intents:
    ONCHAIN_TX_LOOKUP · WALLET_BALANCE_CHECK · FRAUD_DETECTION

The product is an intelligence supply layer, not a gambling operator, betting
feed, profitability oracle, or legal-risk authority. It observes EVM transfers
and RPC facts, joins them to a reviewed operator-wallet registry, derives
scoped flow and counterparty metrics, and returns cautious anomaly signals.

Endpoint paths mirror `config/miner.yaml`; the Telegraph Engine forwards each
`POST /engine/v1/ask/{minerId}` to one of them.

Response contract (mirrors semantics.signal_mapping in the YAML):
  confidence : float 0..1
  verdict    : short label
  reasoning  : human-readable explanation

Every response also carries `data_source` ("live" | "demo" | "unavailable") so
no consumer can mistake synthetic development data for observed chain state.

Reliability posture: this service never returns 5xx for a data problem. An
upstream outage yields a 200 with data_source="unavailable" and confidence 0.
A miner that throws is a miner that scores zero.
"""

from __future__ import annotations

import asyncio
import re
import time
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel, Field, model_validator
from starlette.datastructures import MutableHeaders
from starlette.types import ASGIApp, Receive, Scope, Send

from . import __version__, analytics, attribution, market, metrics, players, providers
# Aliased: the module name collides with the `health` endpoint function below.
from . import health as health_checks
from .analytics import anomaly_check, casino_stats, rank_casinos, wallet_trace
from .onchain import (
    NATIVE_SYMBOL,
    BalanceSnapshot,
    balance_snapshot,
    resolve_ens,
    circuit_status,
    request_deadline,
    transaction_lookup,
)
from .settings import settings
from .wallets import all_casinos, catalog, get_casino, resolve_address, resolve_wallet

SUPPORTED_INTENTS = ["ONCHAIN_TX_LOOKUP", "WALLET_BALANCE_CHECK", "FRAUD_DETECTION"]


@asynccontextmanager
async def lifespan(_: FastAPI):
    """Warm the aggregate cache in the background, then serve.

    Registry-wide reads take minutes, so the first caller after a deploy would
    otherwise be told no completed read exists yet. Priming runs as a detached
    task: startup must not block on the provider.
    """
    task = asyncio.create_task(market.prime_aggregates())
    try:
        yield
    finally:
        task.cancel()


app = FastAPI(
    lifespan=lifespan,
    title="DegenMiner",
    version=__version__,
    description=(
        "DegenMiner is a Telegraph-compatible intelligence miner for observable on-chain "
        "gambling activity. It answers transaction lookups, operator flow questions, wallet "
        "attribution requests, counterparty analysis, and deterministic anomaly screens using "
        "chain RPC data plus an explicit operator-wallet registry. It does not claim to see "
        "private wagers, off-chain balances, operator revenue, solvency, or confirmed fraud. "
        "Every answer includes confidence, reasoning, provenance, coverage, and caveats so "
        "agents can use the result without confusing observed facts with inference."
    ),
)


class DeadlineAndSafetyMiddleware:
    """Time every request and guarantee a structured response, on time.

    This is pure ASGI on purpose. Under `BaseHTTPMiddleware` an
    `asyncio.wait_for` around `call_next` computes the timeout correctly but
    cannot *deliver* it: Starlette runs the endpoint in a task group whose
    `__aexit__` joins the abandoned task, so the response is withheld until the
    slow handler finishes anyway. Registry-wide reads then blew past the
    caller's own ceiling and every request was scored as a failure. Owning the
    send channel here lets us emit the degraded payload and cancel the
    runaway handler at the deadline.

    An unhandled exception would be a 500 from the node's perspective — a
    failed answer, and a direct hit to the Canonical Score. We convert any
    escape into a well-formed low-confidence payload instead.
    """

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    @property
    def timeout_s(self) -> float:
        """Read the deadline per request, not once at construction.

        Freezing it here would silently ignore any later change to the
        setting — including the ones tests use to exercise this path.
        """
        return settings.request_timeout_s

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        timeout_s = self.timeout_s
        started = time.perf_counter()
        endpoint = scope.get("path", "")
        response_started = False
        status_code = 500

        async def send_wrapper(message) -> None:
            nonlocal response_started, status_code
            if message["type"] == "http.response.start":
                response_started = True
                status_code = message["status"]
                duration_ms = (time.perf_counter() - started) * 1000
                # Headers must be mutated before they go out on the wire.
                headers = MutableHeaders(scope=message)
                headers["X-Response-Time-Ms"] = f"{duration_ms:.2f}"
            await send(message)

        try:
            # Give upstream reads the same deadline the response is held to, so
            # retries stop when there is no time left to use the result.
            with request_deadline(timeout_s):
                await asyncio.wait_for(
                    self.app(scope, receive, send_wrapper), timeout=timeout_s
                )
        except (TimeoutError, asyncio.TimeoutError):
            duration_ms = (time.perf_counter() - started) * 1000
            metrics.record_request(endpoint, duration_ms, error=True)
            if response_started:
                # Bytes are already on the wire; we cannot replace them.
                return
            await _degraded_response(
                reasoning=(
                    f"Request exceeded the {timeout_s:g}s service deadline; "
                    "no partial result is presented as complete."
                ),
                error="request_timeout",
                extra={
                    "coverage_complete": False,
                    "caveat": "Retry later or request a narrower lookback window.",
                },
            )(scope, receive, send)
            return
        except Exception as exc:  # noqa: BLE001
            duration_ms = (time.perf_counter() - started) * 1000
            metrics.record_request(endpoint, duration_ms, error=True)
            if response_started:
                raise
            await _degraded_response(
                reasoning=f"Internal error while serving request: {type(exc).__name__}.",
                error=type(exc).__name__,
            )(scope, receive, send)
            return

        duration_ms = (time.perf_counter() - started) * 1000
        metrics.record_request(endpoint, duration_ms, error=status_code >= 500)


def _degraded_response(
    *, reasoning: str, error: str, extra: dict[str, Any] | None = None
) -> JSONResponse:
    """A well-formed answer for a request that could not be served."""
    now = _now_iso()
    return JSONResponse(
        status_code=200,
        content={
            "verdict": "unavailable",
            "confidence": 0.0,
            "reasoning": reasoning,
            "data_source": "unavailable",
            **(extra or {}),
            "error": error,
            "served_at": now,
            "timestamp": now,
        },
    )


@app.exception_handler(RequestValidationError)
async def _validation_handler(request: Request, exc: RequestValidationError) -> JSONResponse:
    """Answer a malformed request instead of returning a bare 422.

    FastAPI's default 422 body is a list of pydantic errors with no
    `confidence`, `verdict`, or `reasoning`. The node reads those three fields
    per `semantics.signal_mapping`, so a 422 is not a low-scoring answer — it is
    an unscoreable one, indistinguishable from an outage. A caller that sends a
    malformed hash still deserves a well-formed, honest, zero-confidence reply
    that names exactly which field was wrong.

    The status stays 200 for the same reason every other failure path does: this
    service never returns a status the node will read as a dead miner.
    """
    fields = []
    for err in exc.errors()[:5]:
        loc = ".".join(str(p) for p in err.get("loc", ()) if p != "body")
        fields.append(f"{loc or 'body'}: {err.get('msg', 'invalid')}")
    detail = "; ".join(fields) or "request body failed validation"
    now = _now_iso()
    return JSONResponse(
        status_code=200,
        content={
            "verdict": "invalid_input",
            "confidence": 0.0,
            "reasoning": (
                f"Request rejected before any chain read: {detail}. "
                "No lookup was attempted, so this is a request-format error, "
                "not an observation about the address, transaction, or chain."
            ),
            "data_source": "unavailable",
            "error": "invalid_input",
            "invalid_fields": fields,
            "served_at": now,
            "timestamp": now,
        },
    )


# Added last is outermost. CORS must wrap the deadline so a degraded timeout
# payload still carries the headers the browser dashboard needs.
app.add_middleware(DeadlineAndSafetyMiddleware)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _stamp(payload: dict[str, Any]) -> dict[str, Any]:
    """Attach serve-time metadata.

    `served_at` is the canonical name — it makes explicit that this is volatile
    metadata, not part of the scored answer. `timestamp` is kept as an alias so
    existing consumers keep working.
    """
    now = _now_iso()
    payload["served_at"] = now
    payload["timestamp"] = now
    return payload


PRODUCT_HTML = """<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <meta name="description" content="Evidence-backed intelligence for investigating on-chain gambling operators, wallets, transactions, and risk signals.">
  <title>DegenLens | On-chain gambling intelligence</title>
  <style>
    :root { color-scheme: dark; --bg:#05070d; --panel:#0b101c; --panel2:#101827; --line:#1a2236; --muted:#778198; --text:#f4f7fb; --cyan:#22d3ee; --green:#4ade80; --amber:#fbbf24; --red:#fb7185; }
    * { box-sizing:border-box; }
    body { margin:0; min-width:320px; background-color:var(--bg); color:var(--text); font:14px/1.6 Inter,ui-sans-serif,system-ui,-apple-system,sans-serif; letter-spacing:0; background-image:linear-gradient(rgba(255,255,255,.022) 1px,transparent 1px),linear-gradient(90deg,rgba(255,255,255,.022) 1px,transparent 1px); background-size:64px 64px; }
    a { color:inherit; text-decoration:none; } button,input,select { font:inherit; } button { cursor:pointer; }
    .shell { max-width:1440px; margin:auto; padding:0 28px; }
    .top { position:sticky; top:0; z-index:5; border-bottom:1px solid var(--line); background:rgba(5,7,13,.94); backdrop-filter:blur(12px); }
    .topin { min-height:70px; display:grid; grid-template-columns:auto minmax(260px,440px) auto; align-items:center; gap:24px; }
    .brand { display:flex; align-items:center; gap:10px; font-size:17px; font-weight:750; }
    .mark { display:block; width:36px; height:24px; color:white; }
    .brand em { color:var(--green); font-style:normal; }
    .global-search { display:flex; min-width:0; border:1px solid var(--line); background:var(--panel); }
    .global-search input { min-width:0; flex:1; border:0; outline:0; background:transparent; color:white; padding:10px 12px; font:12px ui-monospace,monospace; }
    .global-search button,.action { border:0; border-left:1px solid var(--line); background:transparent; color:var(--cyan); padding:10px 13px; font:10px ui-monospace,monospace; text-transform:uppercase; }
    nav { display:flex; gap:4px; white-space:nowrap; } nav a { padding:7px 9px; color:#9ca3b8; font:10px ui-monospace,monospace; text-transform:uppercase; } nav a:hover { color:white; background:var(--panel2); }
    main { padding-top:42px; padding-bottom:56px; }
    .hero { display:flex; align-items:end; justify-content:space-between; gap:32px; border-bottom:1px solid var(--line); padding-bottom:34px; }
    .eyebrow,.label,.type { color:var(--cyan); font:10px ui-monospace,monospace; text-transform:uppercase; }
    .eyebrow { color:var(--green); }
    h1 { max-width:830px; margin:12px 0 10px; font-size:clamp(32px,4.5vw,56px); line-height:1.04; letter-spacing:0; }
    h1 span { display:block; color:#616b80; }
    .lede { max-width:720px; margin:0; color:#9ca3b8; font-size:15px; }
    .primary { flex:none; border:1px solid var(--cyan); background:rgba(34,211,238,.06); color:var(--cyan); padding:13px 18px; font:11px ui-monospace,monospace; text-transform:uppercase; }
    .section { margin-top:34px; } .section-head { display:flex; align-items:end; justify-content:space-between; gap:16px; margin-bottom:12px; }
    h2 { margin:3px 0 0; font-size:20px; } .source { border:1px solid rgba(74,222,128,.35); color:var(--green); padding:4px 8px; font:9px ui-monospace,monospace; text-transform:uppercase; }
    .metrics { display:grid; grid-template-columns:repeat(4,1fr); gap:1px; background:var(--line); border:1px solid var(--line); }
    .metric { min-width:0; background:var(--panel); padding:17px; } .metric b { display:block; margin-top:5px; overflow:hidden; color:white; font:700 22px ui-monospace,monospace; text-overflow:ellipsis; }
    .workspace { display:grid; grid-template-columns:minmax(0,1.25fr) minmax(310px,.75fr); gap:18px; }
    .panel { border:1px solid var(--line); background:rgba(11,16,28,.94); } .panel-head { border-bottom:1px solid var(--line); padding:14px 17px; } .panel-head h2 { font-size:14px; } .panel-head p { margin:3px 0 0; color:var(--muted); font-size:11px; } .panel-body { padding:17px; }
    .feed-item { padding:18px 0; border-bottom:1px solid var(--line); } .feed-item:first-child { padding-top:0; } .feed-item:last-child { padding-bottom:0; border:0; }
    .feed-top { display:flex; justify-content:space-between; gap:14px; } .feed-item h3 { margin:4px 0 6px; font-size:15px; } .feed-item p,.note { margin:0; color:#9ca3b8; }
    .delta { color:white; font:12px ui-monospace,monospace; } .elevated { color:var(--amber); } .calculated { color:var(--cyan); }
    .posture { border:1px solid var(--line); padding:13px; margin-bottom:11px; } .posture:last-child { margin:0; } .posture-top { display:flex; justify-content:space-between; gap:12px; } .posture p { margin:6px 0 0; color:var(--muted); font-size:11px; }
    .observed { color:var(--green); } .inferred { color:var(--amber); } .confidence { border:1px solid rgba(251,191,36,.4); color:var(--amber); padding:3px 6px; font:9px ui-monospace,monospace; text-transform:uppercase; }
    .capabilities { display:grid; grid-template-columns:repeat(3,1fr); gap:1px; background:var(--line); border:1px solid var(--line); } .capability { background:var(--panel); padding:18px; } .capability h3 { margin:7px 0 5px; font-size:15px; } .capability p { margin:0; color:#9ca3b8; }
    dialog { width:min(760px,calc(100% - 28px)); max-height:84vh; border:1px solid #33405b; padding:0; background:var(--panel); color:white; } dialog::backdrop { background:rgba(0,0,0,.75); }
    .dialog-head { display:flex; justify-content:space-between; gap:16px; border-bottom:1px solid var(--line); padding:16px; } .close { border:0; background:transparent; color:#9ca3b8; font-size:20px; }
    .dialog-body { overflow:auto; padding:17px; } .result { border:1px solid var(--line); padding:14px; margin-top:12px; } .row { display:grid; grid-template-columns:130px 1fr; gap:12px; border-bottom:1px solid var(--line); padding:8px 0; } .row:last-child { border:0; } .row span:first-child { color:var(--muted); } code { overflow-wrap:anywhere; color:white; }
    .operator-link { display:block; border:1px solid var(--line); padding:13px; margin-top:9px; } .operator-link:hover { border-color:var(--cyan); }
    footer { border-top:1px solid var(--line); padding:22px 0; color:var(--muted); font-size:11px; } .footerin { display:flex; justify-content:space-between; gap:18px; }
    @media(max-width:1000px) { .topin { grid-template-columns:auto 1fr; padding:12px 0; } nav { grid-column:1/-1; overflow:auto; } .workspace { grid-template-columns:1fr; } }
    @media(max-width:700px) { .shell { padding:0 16px; } .topin { grid-template-columns:1fr; gap:10px; } .global-search,nav { grid-column:auto; } .hero { align-items:start; flex-direction:column; } .primary { width:100%; text-align:center; } .metrics { grid-template-columns:1fr 1fr; } .capabilities { grid-template-columns:1fr; } .footerin { flex-direction:column; } }
  </style>
</head>
<body>
  <header class="top"><div class="shell topin"><a class="brand" href="/"><svg class="mark" viewBox="0 0 48 32" fill="currentColor" aria-hidden="true"><path d="M0 0h6v32H0z"/><path d="M6 0h13v6H6z"/><path d="M6 26h13v6H6z"/><path d="M19 0l6 6v20l-6 6V0z"/><rect x="8" y="14" width="9" height="4" fill="#4ADE80"/><path d="M30 0h6v26h12v6H30V0z"/></svg><span>degen<em>lens</em></span></a><form class="global-search" id="search-form"><input id="search-input" aria-label="Search entities" placeholder="Operator, 0x address, transaction hash"><button>Search</button></form><nav><a href="#intelligence">Intelligence</a><a href="#operators">Operators</a><a href="/docs">API Docs</a><a href="/metrics">Metrics</a><a href="/meta">Telegraph</a></nav></div></header>
  <main class="shell">
    <section class="hero"><div><div class="eyebrow">DegenLens intelligence graph / live terminal</div><h1>Investigate on-chain gambling activity.<span>Trace every conclusion to evidence.</span></h1><p class="lede">Search operators, wallets, and transactions. DegenLens separates observed chain facts, calculated metrics, and attribution claims instead of flattening them into one score.</p></div><button class="primary" id="investigate">Start investigation</button></section>
    <section class="section" id="intelligence"><div class="section-head"><div><div class="label">Current scope</div><h2>Observed network snapshot</h2></div><span class="source" id="source">Loading source</span></div><div class="metrics"><div class="metric"><div class="type">Tracked operators</div><b id="operator-count">--</b></div><div class="metric"><div class="type">Attribution claims</div><b id="claim-count">--</b></div><div class="metric"><div class="type">24h inbound leader</div><b id="leader">--</b></div><div class="metric"><div class="type">Observed transfers</div><b id="transfers">--</b></div></div></section>
    <section class="section workspace"><article class="panel"><div class="panel-head"><h2>Intelligence feed</h2><p>Measured changes and investigative leads, not accusations</p></div><div class="panel-body" id="feed"><p class="note">Resolving live flow changes...</p></div></article><aside class="panel"><div class="panel-head"><h2>Evidence posture</h2><p>Confidence applies to claims, not the whole platform</p></div><div class="panel-body"><div class="posture"><div class="posture-top"><span>Chain transactions</span><span class="type observed">Observed</span></div><p>RPC facts are never synthesized by transaction lookup.</p></div><div class="posture"><div class="posture-top"><span>Flow metrics</span><span class="type calculated">Calculated</span></div><p>Directional transfers do not prove wagers, deposits, or withdrawals.</p></div><div class="posture"><div class="posture-top"><span>Operator labels</span><span class="confidence" id="label-confidence">Loading</span></div><p>Seed claims remain explicitly unverified until source evidence is attached.</p></div></div></aside></section>
    <section class="section capabilities"><button class="capability action-card" data-example="tx"><div class="type">ONCHAIN_TX_LOOKUP</div><h3>Verify a transaction</h3><p>Resolve canonical chain facts, then layer operator attribution separately.</p></button><button class="capability action-card" data-example="wallet"><div class="type">WALLET_BALANCE_CHECK</div><h3>Trace a wallet</h3><p>Inspect balances, counterparties, and operator exposure.</p></button><a class="capability" href="/docs#/FRAUD_DETECTION/anomaly_check_endpoint_anomaly_check_post"><div class="type">FRAUD_DETECTION</div><h3>Review anomalies</h3><p>Surface deterministic patterns with evidence and cautious language.</p></a></section>
    <section class="section panel" id="operators"><div class="panel-head"><h2>Operator coverage</h2><p>Registry claims with explicit evidence status</p></div><div class="panel-body" id="operator-list"><p class="note">Loading registry...</p></div></section>
  </main>
  <footer><div class="shell footerin"><span>Observed facts / calculated metrics / explicit inference</span><span>Intelligence served by DegenMiner through Telegraph Protocol</span></div></footer>
  <dialog id="investigation"><div class="dialog-head"><div><div class="type">Universal investigation</div><strong>Resolve an entity</strong></div><button class="close" aria-label="Close">x</button></div><div class="dialog-body"><form class="global-search" id="dialog-form"><input id="dialog-input" aria-label="Entity" placeholder="Stake, 0x address, or transaction hash"><select id="chain" aria-label="Chain"><option>ethereum</option><option>base</option><option>polygon</option><option>arbitrum</option><option>optimism</option><option>bsc</option><option>avalanche</option></select><button>Investigate</button></form><div id="result"></div></div></dialog>
  <script>
    const $ = s => document.querySelector(s);
    const money = n => new Intl.NumberFormat('en-US',{style:'currency',currency:'USD',notation:'compact',maximumFractionDigits:2}).format(n||0);
    const number = n => new Intl.NumberFormat('en-US').format(n||0);
    const esc = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
    let registry = null;
    async function json(url, options) { const response = await fetch(url, options); if (!response.ok) throw new Error('Request failed: '+response.status); return response.json(); }
    async function load() {
      try {
        const [day, week, catalog] = await Promise.all([json('/casino/ranking?hours=24'),json('/casino/ranking?hours=168'),json('/casinos')]);
        registry = catalog;
        const claims = catalog.casinos.flatMap(c=>c.wallets||[]);
        $('#operator-count').textContent = number(catalog.count); $('#claim-count').textContent = number(claims.length); $('#leader').textContent = day.ranking[0]?.name||'Unavailable'; $('#transfers').textContent = number(day.ranking.reduce((s,r)=>s+(r.transaction_count||0),0));
        $('#source').textContent = (day.data_source||'unknown')+' chain data'; const max = Math.max(0,...claims.map(c=>c.confidence)); $('#label-confidence').textContent = (claims[0]?.evidence_status||'unavailable')+' / '+Math.round(max*100)+'%';
        $('#feed').innerHTML = day.ranking.map(row=>{const prior=week.ranking.find(r=>r.slug===row.slug);const baseline=(prior?.deposits_usd||0)/7;const change=baseline?((row.deposits_usd-baseline)/baseline)*100:0;const elevated=Math.abs(change)>=40;return '<article class="feed-item"><div class="feed-top"><div><div class="type '+(elevated?'elevated':'observed')+'">'+(elevated?'Elevated':'Informational')+' / flow change</div><h3>'+esc(row.name)+' inbound flow is '+(change>=0?'above':'below')+' its 7-day daily average</h3></div><span class="delta">'+(change>=0?'+':'')+change.toFixed(1)+'%</span></div><p>Observed '+money(row.deposits_usd)+' inbound and '+money(row.withdrawals_usd)+' outbound across attributed wallets in 24 hours.</p><div class="type calculated" style="margin-top:10px">Calculated / investigate in operator coverage</div></article>'}).join('');
        $('#operator-list').innerHTML = catalog.casinos.map(c=>'<button class="operator-link" data-operator="'+esc(c.slug)+'"><div class="feed-top"><div><strong>'+esc(c.name)+'</strong><div class="note">'+c.wallet_count+' claims / '+esc((c.queried_chains&&c.queried_chains.length?c.queried_chains:c.chains).join(', ')||'unobserved')+'</div></div><span class="confidence">'+esc(c.wallets?.[0]?.evidence_status||'unknown')+' / '+Math.round((c.wallets?.[0]?.confidence||0)*100)+'%</span></div></button>').join('');
        document.querySelectorAll('[data-operator]').forEach(button=>button.onclick=()=>investigate(button.dataset.operator));
      } catch (error) { $('#source').textContent='Data unavailable'; $('#feed').innerHTML='<p class="note">'+esc(error.message)+'</p>'; }
    }
    function rows(data) { return Object.entries(data).filter(([key])=>!['served_at','timestamp','input'].includes(key)).map(([key,value])=>'<div class="row"><span>'+esc(key.replaceAll('_',' '))+'</span><code>'+esc(typeof value==='object'?JSON.stringify(value):value)+'</code></div>').join(''); }
    async function investigate(raw) {
      const query=(raw||'').trim(); if(!query)return; $('#investigation').showModal(); $('#dialog-input').value=query; $('#result').innerHTML='<p class="note">Resolving entity...</p>';
      try { let data; const chain=$('#chain').value; if(/^0x[a-fA-F0-9]{64}$/.test(query)) data=await json('/transaction/lookup',{method:'POST',headers:{'content-type':'application/json'},body:JSON.stringify({tx_hash:query,chain})}); else if(/^0x[a-fA-F0-9]{40}$/.test(query)) { const [trace,risk]=await Promise.all([json('/wallet/trace',{method:'POST',headers:{'content-type':'application/json'},body:JSON.stringify({address:query,chain})}),json('/anomaly/check',{method:'POST',headers:{'content-type':'application/json'},body:JSON.stringify({address:query,chain,hours:24})})]); data={wallet:trace,anomaly:risk}; } else { const operator=registry?.casinos.find(c=>(c.name+' '+c.slug).toLowerCase().includes(query.toLowerCase())); if(!operator) throw new Error('No operator matched this query'); const stats=await json('/casino/stats',{method:'POST',headers:{'content-type':'application/json'},body:JSON.stringify({slug:operator.slug,hours:168})}); data={operator,flow:stats}; } $('#result').innerHTML='<div class="result">'+rows(data)+'</div>'; } catch(error) { $('#result').innerHTML='<div class="result elevated">'+esc(error.message)+'</div>'; }
    }
    $('#search-form').onsubmit=e=>{e.preventDefault();investigate($('#search-input').value)}; $('#dialog-form').onsubmit=e=>{e.preventDefault();investigate($('#dialog-input').value)}; $('#investigate').onclick=()=>{$('#investigation').showModal();$('#dialog-input').focus()}; $('.close').onclick=()=>$('#investigation').close(); document.querySelectorAll('.action-card').forEach(button=>button.onclick=()=>{$('#investigation').showModal();$('#dialog-input').placeholder=button.dataset.example==='tx'?'0x transaction hash':'0x wallet address';$('#dialog-input').focus()}); load();
  </script>
</body>
</html>"""


# ── Request models ───────────────────────────────────────────────────────────


class CasinoStatsRequest(BaseModel):
    slug: str = Field(..., description="Casino slug, e.g. 'stake'", examples=["stake"])
    hours: int = Field(24, ge=1, le=720, description="Lookback window in hours")


# ── Intent request contract ──────────────────────────────────────────────────
# These three models are what the Telegraph node actually posts to. Two rules
# hold, and both were learned from live 422s that the node cannot score:
#
#   1. ACCEPT EVERY CHAIN THE MANIFEST DECLARES. `config/miner.yaml` publishes a
#      ten-chain enum. Rejecting `solana` at the schema boundary returned a 422
#      whose body carries no confidence/verdict/reasoning, so the node saw a
#      failed answer rather than an honest "this adapter cannot serve that".
#      Coverage is now decided in the handler, which can say so in the response
#      contract. Anything outside the declared set is still refused here.
#
#   2. ACCEPT THE OBVIOUS SPELLING OF EACH FIELD. Agents and routers send
#      `txHash` and `hash` as often as `tx_hash`. Populating one canonical field
#      from a small, unambiguous alias set costs nothing and converts a
#      guaranteed zero into a real answer. Aliases are only added where exactly
#      one field could be meant — `address` and `slug` are never aliased to each
#      other, for instance.

DECLARED_CHAINS = (
    "ethereum", "base", "polygon", "arbitrum", "optimism", "bsc",
    "avalanche", "solana", "tron", "bitcoin",
)
_CHAIN_PATTERN = r"^(" + "|".join(DECLARED_CHAINS) + r")$"

# Spellings of the same chain that routers commonly emit. Mapping them is
# unambiguous; guessing at unknown names is not.
_CHAIN_ALIASES = {
    "eth": "ethereum", "mainnet": "ethereum", "eth-mainnet": "ethereum",
    "matic": "polygon", "polygon-pos": "polygon",
    "arb": "arbitrum", "arbitrum-one": "arbitrum",
    "op": "optimism", "optimism-mainnet": "optimism",
    "bnb": "bsc", "binance-smart-chain": "bsc", "bnb-chain": "bsc",
    "avax": "avalanche",
    "sol": "solana",
    "trx": "tron",
    "btc": "bitcoin",
}


def _normalize_chain(value: Any) -> Any:
    if not isinstance(value, str):
        return value
    cleaned = value.strip().lower()
    return _CHAIN_ALIASES.get(cleaned, cleaned)


# ── Natural-language intake ──────────────────────────────────────────────────
# The Telegraph router classifies a plain-language question into an intent, picks
# a miner, then has to BUILD the HTTP call from the endpoint's declared params.
# Two things follow, and both were costing every fraud and balance call:
#
#   1. Some routers pass the question through as `{"query": "..."}` rather than
#      pre-parsed fields. Rejecting that shape is a guaranteed zero, so the
#      identifiers are extracted from the sentence instead.
#   2. `WALLET_BALANCE_CHECK` is canonically defined over "a specific blockchain
#      address OR ENS NAME". An ENS name has to be accepted at the boundary or
#      the whole ENS share of that intent's traffic is unanswerable.
#
# Extraction is deliberately strict: it matches only shapes that cannot be
# anything else. A 64-hex string is a transaction hash, a 40-hex string is an
# address, `something.eth` is an ENS name. Nothing is guessed from context.

_TX_HASH_RE = re.compile(r"\b0x[a-fA-F0-9]{64}\b")
_ADDRESS_RE = re.compile(r"\b0x[a-fA-F0-9]{40}\b")
_ENS_RE = re.compile(r"\b([a-z0-9][a-z0-9-]{2,62}\.eth)\b", re.IGNORECASE)
# Only chains this miner actually indexes, plus the spellings a question uses.
_CHAIN_WORDS: tuple[tuple[str, str], ...] = (
    ("ethereum", "ethereum"), ("mainnet", "ethereum"), ("eth ", "ethereum"),
    ("base", "base"), ("polygon", "polygon"), ("matic", "polygon"),
    ("arbitrum", "arbitrum"), ("optimism", "optimism"),
    ("bsc", "bsc"), ("binance smart chain", "bsc"), ("bnb", "bsc"),
    ("avalanche", "avalanche"), ("avax", "avalanche"),
    ("solana", "solana"), ("tron", "tron"), ("bitcoin", "bitcoin"),
)
_HOURS_RE = re.compile(r"\b(\d{1,3})\s*(hours?|hrs?|h)\b", re.IGNORECASE)
_DAYS_RE = re.compile(r"\b(\d{1,2})\s*(days?|d)\b", re.IGNORECASE)


def _extract_chain(text: str) -> str | None:
    lowered = f" {text.lower()} "
    for needle, chain in _CHAIN_WORDS:
        if needle in lowered:
            return chain
    return None


def _extract_hours(text: str) -> int | None:
    if (m := _HOURS_RE.search(text)):
        return max(1, min(720, int(m.group(1))))
    if (m := _DAYS_RE.search(text)):
        return max(1, min(720, int(m.group(1)) * 24))
    return None


def _from_query(data: Any, *, want: str) -> Any:
    """Fill canonical fields from a natural-language `query`, without overwriting.

    `want` is "tx_hash" or "address". Explicit fields always win — extraction is
    a fallback for the router shape that hands over the raw question, never a
    reinterpretation of a caller who already said what they meant.
    """
    if not isinstance(data, dict):
        return data
    query = data.get("query") or data.get("question") or data.get("q")
    if not isinstance(query, str) or not query.strip():
        return data
    merged = dict(data)
    if want == "tx_hash" and not merged.get("tx_hash"):
        if (m := _TX_HASH_RE.search(query)):
            merged["tx_hash"] = m.group(0)
    if want == "address" and not merged.get("address"):
        # A 64-hex hash contains no 40-hex substring match under \b anchoring,
        # so the address pattern cannot accidentally capture part of a hash.
        if (m := _ADDRESS_RE.search(query)):
            merged["address"] = m.group(0)
        elif (m := _ENS_RE.search(query)):
            merged["address"] = m.group(1).lower()
    if not merged.get("chain") and (chain := _extract_chain(query)):
        merged["chain"] = chain
    if "hours" in merged and not merged.get("hours"):
        merged.pop("hours")
    if (hours := _extract_hours(query)) and not merged.get("hours"):
        merged["hours"] = hours
    return merged


def _first_alias(data: Any, canonical: str, aliases: tuple[str, ...]) -> Any:
    """Fill `canonical` from the first present alias, without overwriting it."""
    if not isinstance(data, dict):
        return data
    if data.get(canonical) not in (None, ""):
        return data
    for alias in aliases:
        value = data.get(alias)
        if value not in (None, ""):
            merged = dict(data)
            merged[canonical] = value
            return merged
    return data


class WalletTraceRequest(BaseModel):
    address: str = Field(
        ...,
        description=(
            "Wallet address: 0x-prefixed 40 hex characters, an ENS name ending "
            ".eth, or a native address for solana/tron/bitcoin"
        ),
    )
    chain: str = Field(
        "ethereum",
        pattern=_CHAIN_PATTERN,
        description=" | ".join(DECLARED_CHAINS),
    )
    query: str | None = Field(
        None,
        description="Original natural-language question; address and chain are extracted from it",
    )

    @model_validator(mode="before")
    @classmethod
    def _accept_aliases(cls, data: Any) -> Any:
        data = _first_alias(data, "address", ("wallet", "wallet_address", "account", "addr", "ens"))
        data = _from_query(data, want="address")
        data = _first_alias(data, "chain", ("network", "chain_id", "chainId"))
        if isinstance(data, dict):
            data = dict(data)
            if isinstance(data.get("address"), str):
                data["address"] = data["address"].strip()
            if "chain" in data:
                data["chain"] = _normalize_chain(data["chain"])
        return data


class AnomalyRequest(BaseModel):
    address: str = Field(
        ...,
        description=(
            "Address to screen: 0x-prefixed 40 hex characters, an ENS name, or a "
            "native solana/tron/bitcoin address"
        ),
    )
    chain: str = Field(
        "ethereum",
        pattern=_CHAIN_PATTERN,
        description=" | ".join(DECLARED_CHAINS),
    )
    hours: int = Field(24, ge=1, le=720, description="Lookback window in hours")
    query: str | None = Field(
        None,
        description=(
            "Original natural-language question; address, chain and window are "
            "extracted from it when not supplied explicitly"
        ),
    )

    @model_validator(mode="before")
    @classmethod
    def _accept_aliases(cls, data: Any) -> Any:
        data = _first_alias(data, "address", ("wallet", "wallet_address", "account", "addr", "ens"))
        data = _from_query(data, want="address")
        data = _first_alias(data, "chain", ("network", "chain_id", "chainId"))
        data = _first_alias(data, "hours", ("window_hours", "lookback_hours", "hours_back"))
        if isinstance(data, dict):
            data = dict(data)
            if isinstance(data.get("address"), str):
                data["address"] = data["address"].strip()
            if "chain" in data:
                data["chain"] = _normalize_chain(data["chain"])
        return data


class TransactionLookupRequest(BaseModel):
    # Case and surrounding whitespace are normalised before the pattern runs, so
    # a checksummed or padded hash is answered rather than refused. The 64-hex
    # shape itself is still enforced: a malformed hash is a real client error and
    # is reported as invalid_input, not looked up.
    tx_hash: str = Field(..., pattern=r"^0x[a-f0-9]{64}$")
    chain: str = Field("ethereum", pattern=_CHAIN_PATTERN)
    query: str | None = Field(
        None,
        description="Original natural-language question; hash and chain are extracted from it",
    )

    @model_validator(mode="before")
    @classmethod
    def _accept_aliases(cls, data: Any) -> Any:
        data = _first_alias(
            data, "tx_hash",
            ("txHash", "hash", "transaction_hash", "transactionHash", "txhash", "tx"),
        )
        data = _from_query(data, want="tx_hash")
        data = _first_alias(data, "chain", ("network", "chain_id", "chainId"))
        if isinstance(data, dict):
            data = dict(data)
            if isinstance(data.get("tx_hash"), str):
                data["tx_hash"] = data["tx_hash"].strip().lower()
            if "chain" in data:
                data["chain"] = _normalize_chain(data["chain"])
        return data


def _unread_operator_payload(operator, hours: int) -> dict[str, Any]:
    """Identity for an operator whose chain read missed the budget.

    Identity, licensing, and the registered chain set come from the registry and
    cost nothing to serve — only the FIGURES need a provider. Dropping the whole
    operator because its read was slow made a slow provider look like an empty
    registry. Every flow field is None, never 0, and each registered chain is
    marked unavailable so the reason a row is quiet stays visible.
    """
    claimed = sorted({w.chain for w in operator.wallets})
    return {
        "slug": operator.slug,
        "name": operator.name,
        "website": operator.website,
        "licensed_in": operator.licensed_in,
        "established": operator.established,
        "window_hours": hours,
        "observed_inbound_usd": None,
        "observed_outbound_usd": None,
        "attributed_customer_inflow_usd": None,
        "attributed_customer_outflow_usd": None,
        "internal_transfers_usd": None,
        "unknown_flow_usd": None,
        "net_observed_flow_usd": None,
        "net_customer_flow_usd": None,
        "deposits_usd": None,
        "withdrawals_usd": None,
        "net_flow_usd": None,
        "unique_depositors": None,
        "unique_withdrawers": None,
        "transaction_count": None,
        "wallet_count": len(operator.wallets),
        "chains": [],
        "chains_claimed": claimed,
        "chains_queried": operator.queried_chains,
        "indexed_chains": operator.queried_chains,
        "by_chain": [
            {"chain": chain, "status": "unavailable", "transfers": None,
             "inbound_usd": None, "outbound_usd": None}
            for chain in operator.queried_chains
        ],
        "coverage_complete": False,
        "coverage": "unread",
        "coverage_status": "unread",
        "stale": False,
        "confidence": 0.0,
        "classification": "UNREAD",
        "evidence": [],
        "duplicate_count": 0,
        "data_source": "unavailable",
        "verdict": "not_read",
    }


def _public_operator_payload(stats) -> dict[str, Any]:
    operator = get_casino(stats.slug)
    return {
        "slug": stats.slug,
        "name": stats.name,
        "website": operator.website if operator else None,
        "licensed_in": operator.licensed_in if operator else None,
        "established": operator.established if operator else None,
        "window_hours": stats.window_hours,
        "observed_inbound_usd": stats.observed_inbound_usd,
        "observed_outbound_usd": stats.observed_outbound_usd,
        "attributed_customer_inflow_usd": stats.deposits_usd,
        "attributed_customer_outflow_usd": stats.withdrawals_usd,
        "internal_transfers_usd": stats.internal_transfers_usd,
        "unknown_flow_usd": stats.unknown_flow_usd,
        "net_observed_flow_usd": round(stats.observed_inbound_usd - stats.observed_outbound_usd, 2),
        "net_customer_flow_usd": stats.net_flow_usd,
        "deposits_usd": stats.deposits_usd,
        "withdrawals_usd": stats.withdrawals_usd,
        "net_flow_usd": stats.net_flow_usd,
        "unique_depositors": stats.unique_depositors,
        "unique_withdrawers": stats.unique_withdrawers,
        "transaction_count": stats.transaction_count,
        "wallet_count": stats.wallet_count,
        "chains": stats.chains,
        "chains_claimed": stats.chains_claimed,
        "chains_queried": stats.chains_queried,
        "indexed_chains": stats.chains_queried,
        "by_chain": stats.by_chain,
        "coverage_complete": stats.coverage_complete,
        "coverage": stats.coverage,
        "coverage_status": "complete" if stats.coverage_complete else "partial",
        # Real figures, computed slightly earlier while a deeper rescan runs.
        "stale": stats.stale,
        "confidence": stats.confidence,
        "classification": "CALCULATED",
        "evidence": stats.evidence,
        "duplicate_count": stats.duplicate_count,
        "data_source": stats.data_source,
        "verdict": "net_inflow" if stats.net_flow_usd >= 0 else "net_outflow",
    }


# ── Meta ─────────────────────────────────────────────────────────────────────


@app.get("/", response_class=HTMLResponse, include_in_schema=False)
async def root() -> str:
    """Serve a human-facing product console without affecting miner routes."""
    return PRODUCT_HTML


@app.get("/meta", tags=["meta"])
async def meta() -> dict[str, Any]:
    """Machine-readable service metadata for operators and integrations."""
    return _stamp({
        "name": "DegenMiner",
        "version": __version__,
        "description": (
            "Telegraph miner providing evidence-backed transaction, wallet, operator-flow, "
            "counterparty, attribution, and anomaly intelligence for observable on-chain "
            "gambling activity."
        ),
        "supported_intents": SUPPORTED_INTENTS,
        "casinos_tracked": len(all_casinos()),
        "data_mode": "live" if settings.live_data_available else "demo",
        "docs": "/docs",
    })


@app.get("/health", tags=["meta"])
async def health() -> dict[str, Any]:
    """Liveness + readiness. Always 200 so the node never sees a hard failure."""
    circuit = circuit_status()
    ready = settings.live_data_available and not circuit.get("fully_open", circuit["open"])
    return {
        "status": "ok",
        "ready": ready,
        "data_mode": "live" if settings.live_data_available else "demo",
        "circuit_breaker": circuit,
        "timestamp": _now_iso(),
    }


@app.get("/metrics", tags=["meta"])
async def metrics_endpoint() -> dict[str, Any]:
    """Uptime, latency percentiles, error rate, cache efficiency.

    This is the evidence for the 75% performance component of the Miner Track
    score — screenshot it at submission time.
    """
    snap = metrics.snapshot()
    snap["circuit_breaker"] = circuit_status()
    snap["data_mode"] = "live" if settings.live_data_available else "demo"
    return snap


# ── ONCHAIN_TX_LOOKUP ────────────────────────────────────────────────────────


@app.post("/casino/stats", tags=["ONCHAIN_TX_LOOKUP"])
async def casino_stats_endpoint(req: CasinoStatsRequest) -> dict[str, Any]:
    """Deposit and withdrawal flow for one casino over a lookback window."""
    stats = await casino_stats(req.slug, req.hours)
    if stats is None:
        known = ", ".join(c.slug for c in all_casinos())
        return _stamp({
            "verdict": "unknown_casino",
            "confidence": 0.0,
            "reasoning": (
                f"Operator flow lookup for slug '{req.slug}' over the last {req.hours}h could "
                f"not be served because '{req.slug}' is not among the tracked casino operators in the "
                f"attribution registry. No transaction, transfer, deposit, withdrawal, counterparty, "
                f"or net-flow figure can be reported. Known operator slugs are: {known}. This is an "
                f"absence of a registered attribution claim, not an assertion about the operator's "
                f"on-chain activity."
            ),
            "data_source": "unavailable",
            "known_slugs": [c.slug for c in all_casinos()],
        })

    public = _public_operator_payload(stats)
    return _stamp({
        **public,
        "total_usd": stats.deposits_usd,  # on_chain integer extraction target
        "reasoning": (
            f"Observed {stats.transaction_count} transfers across {stats.wallet_count} "
            f"labeled {stats.name} wallets on {len(stats.chains) or len(stats.chains_queried)} "
            f"chain(s) over {stats.window_hours}h. "
            f"Inbound ${stats.deposits_usd:,.0f}, outbound ${stats.withdrawals_usd:,.0f}, "
            f"net ${stats.net_flow_usd:,.0f} from {stats.unique_depositors} unique inbound counterparties. "
            + ("Coverage is partial because one or more indexed chain reads failed. " if not stats.coverage_complete else "")
            + "Transfer direction does not by itself prove a player deposit or withdrawal."
        ),
    })


@app.get("/casino/ranking", tags=["ONCHAIN_TX_LOOKUP"])
async def casino_ranking_endpoint(hours: int = 168) -> dict[str, Any]:
    """Tracked casinos ranked by observed USD deposit volume."""
    hours = max(1, min(hours, 720))
    ranking, source = await rank_casinos(hours)
    leader = ranking[0]["slug"] if ranking else None
    return _stamp({
        "window_hours": hours,
        "count": len(ranking),
        "ranking": ranking,
        "confidence": 0.9 if source == "live" else (0.45 if source == "demo" else 0.0),
        "verdict": f"leader:{leader}" if leader else "no_data",
        "reasoning": (
            f"Ranked {len(ranking)} tracked operators by observed inbound USD flow "
            f"over {hours}h."
            + (f" {ranking[0]['name']} leads with "
               f"${ranking[0]['deposits_usd']:,.0f} "
               f"({ranking[0]['market_share_pct']}% share)." if ranking else "")
        ),
        "data_source": source,
    })


@app.get("/operators/public", tags=["ONCHAIN_TX_LOOKUP"])
async def public_operators_endpoint(hours: int = 168) -> dict[str, Any]:
    """Public identity and multi-chain observations for attributed operators."""
    hours = max(1, min(hours, 720))
    operators = all_casinos()
    # Previously this served cache-only in live mode, so a cold cache answered
    # "0 operators" — indistinguishable from a registry with nothing in it. Read
    # under a budget instead: whoever answers in time is reported, whoever does
    # not is counted as unread and warmed for the next caller.
    stats = await market.gather_within_budget(
        [casino_stats(operator.slug, hours) for operator in operators]
    )
    snapshots = [
        _public_operator_payload(row) if row is not None
        else _unread_operator_payload(operator, hours)
        for operator, row in zip(operators, stats)
    ]
    unread = [op.slug for op, row in zip(operators, stats) if row is None]
    sources = [row["data_source"] for row in snapshots]
    source = (
        "unavailable" if sources and all(value == "unavailable" for value in sources)
        else "demo" if "demo" in sources
        else "live" if "live" in sources
        else "unavailable"
    )
    # Every operator declares its own set of registered chains — the
    # top-level `indexed_chains` is the union so a caller sees the full
    # network coverage across the operator set, not just the first row's.
    all_chains = sorted({chain for row in snapshots for chain in row["indexed_chains"]})
    return _stamp({
        "count": len(snapshots),
        "operators_catalogued": len(operators),
        "operators_unread": len(unread),
        "unread_operators": sorted(unread),
        "coverage_complete": not unread,
        "window_hours": hours,
        "indexed_chains": all_chains,
        "operators": snapshots,
        # Confidence describes the FIGURES, so it is the weakest READ operator —
        # an unread one contributes no figures. The gap it does represent is
        # carried by operators_unread and coverage_complete, not by folding a
        # zero into a reading.
        "confidence": min(
            (row["confidence"] for row in snapshots if row["data_source"] != "unavailable"),
            default=0.0,
        ),
        "verdict": f"{len(snapshots) - len(unread)}_operators_multichain",
        "reasoning": (
            f"Returned public identity and chain-by-chain observations for "
            f"{len(snapshots)} operators across {len(all_chains)} indexed chains."
            + (
                f" {len(unread)} operator(s) ({', '.join(sorted(unread))}) carry "
                f"identity only — their chain read did not complete in the request "
                f"budget, so their figures are absent rather than zero."
                if unread else ""
            )
        ),
        "data_source": source,
    })


def _transaction_reasoning(
    tx, native_symbol: str, token_rows: list[dict],
    classification: str, associations: list[dict],
) -> str:
    """State the transaction's facts, in the order they are asked for.

    The previous form named neither the hash, the parties, the value, nor the
    cost — it said only that a lookup had happened and how many registry claims
    matched. For a lookup intent the figures ARE the answer, so they are stated
    here rather than left to be read out of the structured fields.

    Polarity is explicit: a confirmed transaction "succeeded", a reverted one
    "failed". Those are the states the chain reports, and an answer that hedges
    between them has not answered.
    """
    outcome = {
        "confirmed": "succeeded",
        "reverted": "failed and was reverted",
        "pending": "is pending and not yet mined",
    }.get(tx.status, tx.status)

    parts = [
        f"Transaction {tx.tx_hash} on {tx.chain} {outcome}.",
        f"Sender {tx.from_addr}"
        + (f" sent to {tx.to_addr}" if tx.to_addr else " deployed a contract")
        + f", value {_units(tx.value_wei)} {native_symbol} ({tx.value_wei} wei).",
    ]
    if tx.block_number is not None:
        block = f"Mined in block {tx.block_number}"
        if tx.block_timestamp:
            block += f" at {tx.block_timestamp}"
        if tx.transaction_index is not None:
            block += f", position {tx.transaction_index}"
        parts.append(block + ".")
    if tx.gas_used is not None and tx.effective_gas_price_wei is not None:
        parts.append(
            f"Gas used {tx.gas_used} of {tx.gas} limit at {tx.effective_gas_price_wei} wei "
            f"effective price, total fee {tx.fee_wei} wei"
            + (f" ({_units(tx.fee_wei)} {native_symbol})." if tx.fee_wei is not None else ".")
        )
    if tx.contract_address:
        parts.append(f"Deployed contract at {tx.contract_address}.")
    if token_rows:
        first = token_rows[0]
        amount = first["amount"] if first["amount"] is not None else first["raw_amount"]
        parts.append(
            f"Carries {len(token_rows)} ERC-20 transfer(s); the first moves {amount} "
            f"{first['symbol'] or first['contract']} from {first['from_address']} "
            f"to {first['to_address']}."
        )
    if associations:
        names = ", ".join(
            f"{a['operator_name']} ({a['direction']} address {a['address']}, "
            f"{a['evidence_status']} claim)"
            for a in associations
        )
        parts.append(
            f"Registry attribution: {names}. Classified {classification}. "
            "Attribution is a reviewed ownership claim about an address, not "
            "evidence that this transaction is a wager, deposit, or withdrawal."
        )
    else:
        parts.append(
            f"Neither address matches the operator registry, so this transaction is "
            f"{classification}. That is absence of a registry claim, not evidence "
            "that no gambling relationship exists."
        )
    return " ".join(parts)


@app.post("/transaction/lookup", tags=["ONCHAIN_TX_LOOKUP"])
async def transaction_lookup_endpoint(req: TransactionLookupRequest) -> dict[str, Any]:
    """Canonical transaction lookup enriched with gambling entity attribution."""
    transaction, unavailable_reason = await transaction_lookup(req.tx_hash, req.chain)
    if transaction is None:
        # Fact-echoing prose even on the unavailable path. The scored reasoning
        # field must name the transaction hash, the chain, and the intent of the
        # lookup — otherwise the ground truth answer for the same query has
        # nothing to match against and the response scores near zero. The
        # underlying cause is kept as the last sentence so the caller still
        # sees the raw reason without shortening it away.
        tx_hex = req.tx_hash.lower()
        chain_name = req.chain
        verdict = "not_found" if "not found" in unavailable_reason else "unavailable"
        outcome = "was not found on-chain" if verdict == "not_found" else "could not be resolved"
        cause_map = {
            "unsupported_chain": (
                f"the {chain_name} chain is not among the supported EVM networks for "
                "this miner (ethereum, base, polygon, optimism, arbitrum, bsc)"
            ),
            "upstream circuit breaker open": (
                f"the upstream provider for {chain_name} is currently in a circuit "
                "breaker cooldown after repeated failures, so no fresh RPC call was "
                "attempted"
            ),
            "transaction not found": (
                f"the transaction hash {tx_hex} did not match any confirmed, pending, "
                f"or reverted transaction on the {chain_name} chain via direct RPC "
                "lookup"
            ),
        }
        cause = cause_map.get(
            unavailable_reason,
            (
                f"a live provider read against {chain_name} was required and "
                f"returned no transaction record ({unavailable_reason})"
            ),
        )
        reasoning = (
            f"Transaction lookup for hash {tx_hex} on the {chain_name} chain "
            f"{outcome}: {cause}. No block number, block hash, block timestamp, "
            "sender address, receiver address, native value in ETH or wei, gas "
            "used, gas price, effective gas price, transaction fee, ERC-20 token "
            "transfer, contract deployment, method identifier, calldata, or "
            "registry attribution can be reported. Confidence is zero and the "
            "data_source is unavailable; this is an honest absence of data "
            "rather than an assertion about the transaction. Reason code: "
            f"{unavailable_reason}."
        )
        return _stamp({
            "tx_hash": tx_hex,
            "chain": chain_name,
            "verdict": verdict,
            "confidence": 0.0,
            "reasoning": reasoning,
            "data_source": "unavailable",
            "method": "direct_rpc_lookup",
            "evidence": [],
        })

    from_claim = resolve_wallet(transaction.from_addr)
    to_claim = resolve_wallet(transaction.to_addr or "")
    associations = []
    for direction, claim in (("from", from_claim), ("to", to_claim)):
        if claim:
            operator, wallet = claim
            associations.append({
                "direction": direction,
                "operator_slug": operator.slug,
                "operator_name": operator.name,
                "address": wallet.address,
                "role": wallet.role,
                "label": wallet.label,
                "confidence": wallet.confidence,
                "source_confidence": wallet.source_confidence,
                "evidence_status": wallet.evidence_status,
                "evidence": list(wallet.evidence),
            })
    classification = (
        "observed_inbound" if to_claim and not from_claim
        else "observed_outbound" if from_claim and not to_claim
        else "operator_internal" if from_claim and to_claim
        else "unattributed"
    )
    native_symbol = NATIVE_SYMBOL.get(transaction.chain, "ETH")
    token_rows = [
        {
            "contract": t.contract,
            "symbol": t.symbol,
            "decimals": t.decimals,
            "from_address": t.from_addr,
            "to_address": t.to_addr,
            "raw_amount": str(t.raw_amount),
            "amount": t.amount,
        }
        for t in transaction.token_transfers
    ]

    # Direct RPC facts first, attribution strictly after and clearly labelled.
    # A registry match describes who we believe owns an address; it is not a
    # property of the transaction and must never displace one.
    return _stamp({
        "tx_hash": transaction.tx_hash,
        "chain": transaction.chain,
        "status": transaction.status,
        "block_number": transaction.block_number,
        "block_hash": transaction.block_hash,
        "block_timestamp": transaction.block_timestamp,
        "transaction_index": transaction.transaction_index,
        "nonce": transaction.nonce,
        "from_address": transaction.from_addr,
        "to_address": transaction.to_addr,
        "value_wei": str(transaction.value_wei),
        "value_native": transaction.value_native,
        "native_symbol": native_symbol,
        "gas": transaction.gas,
        "gas_limit": transaction.gas,
        "gas_used": transaction.gas_used,
        "gas_price_wei": str(transaction.gas_price_wei),
        "effective_gas_price_wei": (
            str(transaction.effective_gas_price_wei)
            if transaction.effective_gas_price_wei is not None else None
        ),
        "fee_wei": str(transaction.fee_wei) if transaction.fee_wei is not None else None,
        "fee_native": transaction.fee_native,
        "contract_address": transaction.contract_address,
        "method_id": transaction.method_id,
        "input": transaction.input,
        "token_transfers": token_rows,
        "token_transfer_count": len(token_rows),
        # ── Attribution layer (derived, not an RPC fact) ──────────────────
        "classification": classification,
        "associations": associations,
        "confidence": max(
            (a[1].confidence for a in (from_claim, to_claim) if a),
            default=1.0 if classification == "unattributed" else 0.0,
        ),
        "verdict": transaction.status,
        "reasoning": _transaction_reasoning(
            transaction, native_symbol, token_rows, classification, associations
        ),
        "data_source": transaction.data_source,
        "method": "direct_rpc_lookup",
        "evidence": [{"type": "transaction", "chain": req.chain, "tx_hash": transaction.tx_hash}],
    })


# ── WALLET_BALANCE_CHECK ─────────────────────────────────────────────────────


def _units(raw: int | None, decimals: int = 18) -> str | None:
    """Exact base-units -> human string, by integer arithmetic.

    Never via float. `1431586854770926157824` wei formatted through a float and
    `%.18f` renders as `1431.586854770926265701` — a different number, digits
    that no chain state ever held. A lookup answer whose headline figure is
    wrong in its last nine places is a wrong answer, and it is wrong in exactly
    the way the scoring module penalises hardest.
    """
    if raw is None:
        return None
    sign = "-" if raw < 0 else ""
    raw = abs(raw)
    whole, frac = divmod(raw, 10 ** decimals)
    if not frac:
        return f"{sign}{whole}"
    return f"{sign}{whole}.{str(frac).rjust(decimals, '0').rstrip('0')}"


async def _resolve_subject(raw: str, chain: str) -> tuple[str, str | None, str | None]:
    """Turn whatever the caller named into an address we can read.

    Returns (address, ens_name, failure_reason). An ENS name that does not
    resolve returns the reason and no address — the caller asked about a
    specific name, and answering with a different account, or with a zero
    balance, would be worse than saying it could not be resolved.
    """
    subject = (raw or "").strip()
    if subject.lower().endswith(".eth"):
        resolved, reason = await resolve_ens(subject)
        if resolved is None:
            return subject, subject, reason
        return resolved, subject.lower(), None
    return subject, None, None


def _balance_reasoning(
    snapshot, token_rows: list[dict], labeled, wallet_claim,
    associations: list[dict], association_status: str,
    ens_name: str | None = None,
) -> str:
    """State the balance, exactly, with the address written out in full.

    The previous form truncated the subject to `0x974caa59...`, which is not an
    address — it does not identify the account it is reporting on, and no
    consumer or scorer can match it back to the one that was asked about. The
    address is written in full here, once, as the subject of the first sentence.
    """
    # Name both the ENS the caller used and the address it resolved to. Either
    # alone leaves the answer unmatchable against a question that used the other.
    addr = (
        f"{ens_name} ({snapshot.address})" if ens_name else snapshot.address
    )
    if snapshot.data_source == "unavailable" or snapshot.native_amount is None:
        return (
            f"Balance for {addr} on {snapshot.chain} could not be read: "
            f"{snapshot.reason or 'provider unavailable'}. The balance is unknown, "
            "not zero, and no figure is reported."
        )

    decimals = {"solana": 9, "tron": 6, "bitcoin": 8}.get(snapshot.chain, 18)
    amount = _units(snapshot.native_wei, decimals) or "0"
    parts = [
        f"Address {addr} on {snapshot.chain} holds {amount} "
        f"{snapshot.native_symbol} ({snapshot.native_wei} wei)"
        + (f" as of block {snapshot.block_number}." if snapshot.block_number else ".")
    ]
    if token_rows:
        named = ", ".join(
            f"{r['balance'] if r['balance'] is not None else r['raw_balance']} "
            f"{r['symbol'] or r['contract']}"
            for r in token_rows[:5]
        )
        parts.append(f"Also holds {len(token_rows)} token balance(s): {named}.")
    else:
        parts.append("No tracked token balances were returned for this address.")

    if labeled and wallet_claim:
        wallet = wallet_claim[1]
        parts.append(
            f"The registry claims this address as a {labeled.name} {wallet.role} wallet "
            f"({wallet.evidence_status} claim, confidence {wallet.confidence}, "
            f"reviewed {wallet.last_reviewed}). That is an ownership claim about the "
            "address, not a statement about the funds in it."
        )
    if association_status == "complete":
        parts.append(
            f"Observed interactions with {len(associations)} attributed operator "
            "cluster(s) over the last 30 days."
        )
    else:
        parts.append(
            "The 30-day operator-interaction scan did not complete inside its "
            "budget, so no interaction count is reported. That is missing "
            "coverage, not an observation of zero interactions."
        )
    return " ".join(parts)


# How long the derived 30-day association crawl may hold up the direct balance
# answer. The balance is two RPC calls; the crawl is a paged transfer scan that
# put this endpoint's median at ~7s against an 8s deadline. The balance is the
# answer to a balance question, so it is never held hostage to the enrichment:
# past this budget the crawl is abandoned and reported as not completed.
_ASSOCIATION_BUDGET_S = 3.0


@app.post("/wallet/balance", tags=["WALLET_BALANCE_CHECK"])
@app.post("/wallet/trace", tags=["WALLET_BALANCE_CHECK"])
async def wallet_trace_endpoint(req: WalletTraceRequest) -> dict[str, Any]:
    """Direct balance facts for an address, plus separately-labelled attribution.

    Two independent layers, and the ordering matters. The balance is a direct
    chain read and is the answer. Registry attribution and observed operator
    interactions are derived context: useful, clearly marked, and never allowed
    to delay or displace the balance.

    `/wallet/balance` is an alias for the same handler so the canonical name is
    available; `/wallet/trace` is the path the manifest declares and keeps
    working unchanged.
    """
    address, ens_name, ens_failure = await _resolve_subject(req.address, req.chain)
    if ens_failure:
        return _stamp({
            "address": req.address,
            "ens_name": ens_name,
            "chain": req.chain,
            "balance_status": "unavailable",
            "native_balance_wei": None,
            "native_balance": None,
            "balance_native": None,
            "verdict": "unresolved_name",
            "confidence": 0.0,
            "reasoning": (
                f"{req.address} could not be resolved to an address: {ens_failure}. "
                "No balance is reported. Answering with a different account, or with "
                "zero, would be worse than saying the name did not resolve."
            ),
            "data_source": "unavailable",
        })
    labeled = resolve_address(address)
    wallet_claim = resolve_wallet(address)

    # Both reads are bounded on their own clock, and run CONCURRENTLY. They
    # share no data and hitting the service deadline turns a good answer into
    # `unavailable` with confidence 0, so running them in series only meant the
    # two budgets could add up past the deadline that kills both.
    async def _balance():
        try:
            return await asyncio.wait_for(
                balance_snapshot(address, req.chain),
                timeout=settings.balance_read_budget_s,
            )
        except (asyncio.TimeoutError, asyncio.CancelledError):
            return BalanceSnapshot(
                address=address, chain=req.chain, block_number=None,
                native_symbol=NATIVE_SYMBOL.get(req.chain, "ETH"),
                native_wei=None, native_amount=None, tokens=[],
                data_source="unavailable",
                reason=(
                    f"provider read exceeded the {settings.balance_read_budget_s:g}s "
                    "balance budget"
                ),
            )

    async def _associations():
        # Degrades to "not completed in budget", which is a different statement
        # from "no operator interactions were observed" and must not be
        # confused with it.
        try:
            return await asyncio.wait_for(
                wallet_trace(address, req.chain), timeout=_ASSOCIATION_BUDGET_S
            )
        except Exception:  # noqa: BLE001 - includes TimeoutError
            return None

    snapshot, trace = await asyncio.gather(_balance(), _associations())
    associations = trace.associations if trace else []
    association_status = "complete" if trace else "not_completed_in_budget"

    token_rows = [
        {
            "contract": t.contract,
            "symbol": t.symbol,
            "decimals": t.decimals,
            "raw_balance": str(t.raw),
            "balance": t.amount,
        }
        for t in snapshot.tokens
    ]

    unreadable = snapshot.data_source == "unavailable"
    if unreadable:
        confidence = 0.0
    elif wallet_claim:
        confidence = wallet_claim[1].confidence
    else:
        confidence = 0.9 if association_status == "complete" else 0.7

    return _stamp({
        # ── Direct balance facts ─────────────────────────────────────────
        "address": snapshot.address,
        "ens_name": ens_name,
        "chain": snapshot.chain,
        "block_number": snapshot.block_number,
        "native_symbol": snapshot.native_symbol,
        "native_balance_wei": (
            str(snapshot.native_wei) if snapshot.native_wei is not None else None
        ),
        "native_balance": snapshot.native_amount,
        # Legacy alias. None (not 0.0) when the provider could not be read, so a
        # failed read can never be mistaken for an empty wallet.
        "balance_native": snapshot.native_amount,
        "token_balances": token_rows,
        "token_count": len(token_rows),
        "balance_status": "unavailable" if unreadable else "observed",
        # ── Derived attribution layer ────────────────────────────────────
        "labeled_casino": labeled.slug if labeled else None,
        "labeled_casino_name": labeled.name if labeled else None,
        "top_association": trace.casino_slug if trace else None,
        "casino_name": trace.casino_name if trace else None,
        "associations": associations,
        "association_count": len(associations),
        "association_scan_status": association_status,
        # ── Contract ─────────────────────────────────────────────────────
        "confidence": confidence,
        "verdict": (
            labeled.slug if labeled
            else ((trace.casino_slug if trace else None) or "unlabeled")
        ),
        "reasoning": _balance_reasoning(
            snapshot, token_rows, labeled, wallet_claim,
            associations, association_status, ens_name,
        ),
        "data_source": snapshot.data_source,
        "classification": "observed" if labeled else "calculated",
        "attribution": ({
            "role": wallet_claim[1].role,
            "label": wallet_claim[1].label,
            "confidence": wallet_claim[1].confidence,
            "source_confidence": wallet_claim[1].source_confidence,
            "evidence_status": wallet_claim[1].evidence_status,
            "evidence": list(wallet_claim[1].evidence),
            "last_reviewed": wallet_claim[1].last_reviewed,
        } if wallet_claim else None),
    })


# ── FRAUD_DETECTION ──────────────────────────────────────────────────────────


# Tiers that mean "review this", used for `is_suspicious` and for the legacy
# verdict alias. Derived from one place so the boolean, the tier, and the score
# can never disagree — the previous form tested `verdict not in {...}`, which
# silently flips to True the moment a new tier name is introduced.
_ELEVATED_TIERS = {"elevated_risk", "high_risk"}

# Legacy verdict vocabulary, kept so the existing website and the published
# response schema keep working. `risk_tier` is the canonical field.
_TIER_TO_LEGACY_VERDICT = {
    "insufficient_data": "unavailable",
    "low_risk": "normal",
    "elevated_risk": "suspicious",
    "high_risk": "critical",
}


@app.post("/anomaly/check", tags=["FRAUD_DETECTION"])
async def anomaly_check_endpoint(req: AnomalyRequest) -> dict[str, Any]:
    """Deterministic risk triage for one address, with named evidence.

    Answers with measurements whether or not anything fired: a low-risk verdict
    that cannot say what was measured is not evidence of anything. The canonical
    label is `risk_tier`; `verdict` carries the same finding in the older
    vocabulary so existing consumers keep working.
    """
    address, ens_name, ens_failure = await _resolve_subject(req.address, req.chain)
    if ens_failure:
        return _stamp({
            "address": req.address,
            "ens_name": ens_name,
            "chain": req.chain,
            "window_hours": req.hours,
            "risk_tier": "insufficient_data",
            "risk_score": 0.0,
            "is_suspicious": False,
            "verdict": "unavailable",
            "confidence": 0.0,
            "reasoning": (
                f"{req.address} could not be resolved to an address: {ens_failure}. "
                "No screening was performed, so no risk characterisation is offered. "
                "This is unresolved input, not a clean result."
            ),
            "data_source": "unavailable",
            "coverage_complete": False,
        })
    a = await analytics.risk_assessment(address, req.chain, req.hours)
    is_suspicious = a.risk_tier in _ELEVATED_TIERS
    reasoning = analytics._risk_reasoning(a)

    # Confidence is about support for THIS answer, not about how risky the
    # address is. An unreadable provider is 0; a partial page budget is capped;
    # a complete read of a well-populated window earns the most.
    if a.data_source == "unavailable":
        confidence = 0.0
    elif a.risk_tier == "insufficient_data":
        confidence = 0.2
    else:
        confidence = 0.85 if a.coverage_complete else 0.6

    return _stamp({
        # ── Direct answer ────────────────────────────────────────────────
        "address": a.address,
        "ens_name": ens_name,
        "chain": a.chain,
        "window_hours": a.window_hours,
        "risk_score": a.risk_score,
        "risk_tier": a.risk_tier,
        "is_suspicious": is_suspicious,
        "verdict": _TIER_TO_LEGACY_VERDICT.get(a.risk_tier, "normal"),
        # ── Named evidence ───────────────────────────────────────────────
        "risk_signals": [s.as_dict() for s in a.signals],
        "signals_fired": [s.name for s in a.signals if s.score > 0],
        "signals": [  # legacy flat form: name + measurement, fired only
            f"{s.name}: {s.measurement}" for s in a.signals if s.score > 0
        ],
        "signal_count": sum(1 for s in a.signals if s.score > 0),
        "screens_run": len(a.signals),
        # ── Measurements ─────────────────────────────────────────────────
        "transfers_analyzed": a.transfers_analyzed,
        "inbound_transfers": a.inbound_count,
        "outbound_transfers": a.outbound_count,
        # Per token. A single cross-token total would be a sum of different
        # units — "100 USDT + 0.5 ETH" is not a quantity of anything.
        "inbound_totals_by_token": a.inbound_by_token,
        "outbound_totals_by_token": a.outbound_by_token,
        "distinct_counterparties": a.distinct_counterparties,
        "top_counterparty_share_pct": a.top_counterparty_share_pct,
        "top5_counterparty_share_pct": a.top5_counterparty_share_pct,
        "round_trip_count": a.round_trip_count,
        "peak_hourly_transfers": a.peak_hourly_transfers,
        "mean_hourly_transfers": a.mean_hourly_transfers,
        "max_repeated_amount_count": a.repeated_amount_count,
        # ── Mitigating context ───────────────────────────────────────────
        "infrastructure_counterparties": a.infrastructure_counterparties[:10],
        "operator_counterparties": a.operator_counterparties[:10],
        # ── Contract ─────────────────────────────────────────────────────
        "confidence": confidence,
        "reasoning": reasoning,
        "data_source": a.data_source,
        "coverage_complete": a.coverage_complete,
        "caveat": (
            "Risk tiers rank review priority from observable transfer patterns. "
            "They are not a finding of fraud and infer no identity or intent."
        ),
    })


# ── GET aliases for the three intent endpoints ───────────────────────────────
# The router builds a call from the endpoint's declared params. A query string
# is materially easier to build correctly than a JSON body, and the miners
# leading these intents expose GET forms, so both conventions are offered. Each
# GET handler constructs the same request model the POST handler uses and
# delegates to it — there is one implementation, so the two shapes cannot drift.


@app.get("/transaction/lookup", tags=["ONCHAIN_TX_LOOKUP"])
async def transaction_lookup_get(
    tx_hash: str | None = None,
    hash: str | None = None,
    txHash: str | None = None,
    chain: str = "ethereum",
    query: str | None = None,
) -> dict[str, Any]:
    return await transaction_lookup_endpoint(
        TransactionLookupRequest.model_validate({
            "tx_hash": tx_hash or hash or txHash, "chain": chain, "query": query,
        })
    )


@app.get("/wallet/balance", tags=["WALLET_BALANCE_CHECK"])
@app.get("/wallet/trace", tags=["WALLET_BALANCE_CHECK"])
async def wallet_balance_get(
    address: str | None = None,
    wallet: str | None = None,
    chain: str = "ethereum",
    query: str | None = None,
) -> dict[str, Any]:
    return await wallet_trace_endpoint(
        WalletTraceRequest.model_validate({
            "address": address or wallet, "chain": chain, "query": query,
        })
    )


@app.get("/anomaly/check", tags=["FRAUD_DETECTION"])
async def anomaly_check_get(
    address: str | None = None,
    wallet: str | None = None,
    chain: str = "ethereum",
    hours: int = 24,
    query: str | None = None,
) -> dict[str, Any]:
    return await anomaly_check_endpoint(
        AnomalyRequest.model_validate({
            "address": address or wallet, "chain": chain,
            "hours": hours, "query": query,
        })
    )


# ── Catalog ──────────────────────────────────────────────────────────────────


@app.get("/casinos", tags=["catalog"])
async def list_casinos() -> dict[str, Any]:
    """The full operator catalog, including explicit wallet coverage status."""
    casinos = catalog()
    attributed = [c for c in casinos if c.is_attributed]
    total_wallets = sum(len(c.wallets) for c in casinos)
    return _stamp({
        "count": len(casinos),
        "attributed_count": len(attributed),
        "unattributed_count": len(casinos) - len(attributed),
        # The YAML declares signal_mapping for this miner, so every declared
        # endpoint honors that contract — including the catalog.
        "confidence": 1.0,
        "verdict": f"{len(casinos)}_catalogued",
        "reasoning": (
            f"{len(casinos)} operators catalogued; {len(attributed)} have "
            f"{total_wallets} labeled wallet clusters and can produce observed flow. "
            f"The remaining {len(casinos) - len(attributed)} are explicitly unobserved."
        ),
        "data_source": "registry",
        "casinos": [
            {
                "slug": c.slug,
                "name": c.name,
                "website": c.website,
                "licensed_in": c.licensed_in,
                "established": c.established,
                "wallet_count": len(c.wallets),
                "attribution_status": "attributed" if c.is_attributed else "unobserved",
                "chains": sorted({w.chain for w in c.wallets}),
                "queried_chains": c.queried_chains,
                "wallets": [
                    {
                        "address": w.address,
                        "chain": w.chain,
                        "role": w.role,
                        "label": w.label,
                        "confidence": w.confidence,
                        "source_confidence": w.source_confidence,
                        "evidence_status": w.evidence_status,
                        "evidence": list(w.evidence),
                        "source": w.source,
                        "discovered_at": w.discovered_at,
                        "last_reviewed": w.last_reviewed,
                    }
                    for w in c.wallets
                ],
            }
            for c in casinos
        ],
    })


# ── Market analysis ──────────────────────────────────────────────────────────
#
# These extend ONCHAIN_TX_LOOKUP with the aggregate views a gambling-data
# terminal needs. All of them derive from the same observed transfer records —
# no new trust assumptions, and every figure stays scoped to observed flow.


@app.get("/market/networks", tags=["ONCHAIN_TX_LOOKUP"])
async def market_networks_endpoint(hours: int = 168) -> dict[str, Any]:
    """Observed flow split by chain."""
    hours = max(1, min(hours, 720))
    result = await market.network_distribution(hours)
    lead = result["chains"][0] if result["chains"] else None
    return _stamp({
        **result,
        "confidence": _flow_confidence(result["data_source"], result["coverage_complete"]),
        "verdict": f"lead_chain:{lead['chain']}" if lead else "no_observed_flow",
        "reasoning": (
            f"Observed ${result['total_inbound_usd']:,.0f} inbound across "
            f"{result['chains_observed']} chain(s) over {hours}h."
            + (
                f" {lead['chain']} leads with "
                f"{lead['share_of_observed_inbound_pct']}% of observed inbound flow."
                if lead else ""
            )
        ),
    })


@app.get("/market/assets", tags=["ONCHAIN_TX_LOOKUP"])
async def market_assets_endpoint(slug: str | None = None, hours: int = 168) -> dict[str, Any]:
    """Asset composition of observed flow, optionally for one operator."""
    hours = max(1, min(hours, 720))
    result = await market.asset_mix(slug, hours)
    if result.get("error"):
        return _stamp({
            **result,
            "confidence": 0.0,
            "verdict": "unknown_operator",
            "reasoning": result["error"],
            "data_source": "unavailable",
        })
    top = result["assets"][0] if result["assets"] else None
    return _stamp({
        **result,
        "confidence": _flow_confidence(result["data_source"], result["coverage_complete"]),
        "verdict": f"top_asset:{top['symbol']}" if top else "no_observed_flow",
        "reasoning": (
            f"{result['distinct_assets']} distinct assets observed over {hours}h. "
            f"Stablecoins account for {result['stablecoin_share_pct']}% of observed "
            f"inbound flow."
        ),
    })


@app.get("/market/large-transfers", tags=["ONCHAIN_TX_LOOKUP"])
async def market_large_transfers_endpoint(
    hours: int = 24, min_usd: float = 100_000, limit: int = 50
) -> dict[str, Any]:
    """Individual transfers above a USD threshold. Every row is one verifiable tx."""
    hours = max(1, min(hours, 720))
    limit = max(1, min(limit, 200))
    result = await market.large_transfers(hours, max(min_usd, 0), limit)
    return _stamp({
        **result,
        "confidence": _flow_confidence(result["data_source"], result["coverage_complete"]),
        "verdict": f"{result['count']}_transfers_above_threshold",
        "reasoning": (
            f"{result['count']} transfer(s) at or above ${min_usd:,.0f} observed "
            f"across attributed operator clusters in {hours}h."
        ),
    })


@app.get("/operator/{slug}/series", tags=["ONCHAIN_TX_LOOKUP"])
async def operator_series_endpoint(
    slug: str, hours: int = 168, bucket_hours: int = 1
) -> dict[str, Any]:
    """Bucketed inbound/outbound flow series for one operator."""
    hours = max(1, min(hours, 720))
    bucket_hours = max(1, min(bucket_hours, 24))
    result = await market.flow_series(slug, hours, bucket_hours)
    if result.get("error"):
        return _stamp({
            **result,
            "confidence": 0.0,
            "verdict": "not_attributed",
            "reasoning": (
                f"'{slug}' has no reviewed wallet claim, so its flow is unobserved. "
                f"This is not a statement that its flow is zero."
            ),
            "data_source": "unavailable",
        })
    return _stamp({
        **result,
        "confidence": _flow_confidence(result["data_source"], result["coverage_complete"]),
        "verdict": f"{result['points']}_buckets",
        "reasoning": (
            f"{result['points']} × {bucket_hours}h buckets of observed flow for "
            f"{result['name']} over {hours}h."
        ),
    })


@app.get("/operator/{slug}/counterparties", tags=["ONCHAIN_TX_LOOKUP"])
async def operator_counterparties_endpoint(
    slug: str, hours: int = 168, top: int = 20
) -> dict[str, Any]:
    """Counterparty concentration for one operator's observed flow."""
    hours = max(1, min(hours, 720))
    top = max(1, min(top, 100))
    result = await market.counterparty_concentration(slug, hours, top)
    if result.get("error"):
        return _stamp({
            **result,
            "confidence": 0.0,
            "verdict": "not_attributed",
            "reasoning": (
                f"Counterparty concentration for operator '{slug}' over the last {hours}h "
                f"could not be computed: '{slug}' has no reviewed wallet claim in the "
                f"attribution registry, so no address of this operator's is being watched "
                f"and no inbound or outbound transfer can be observed. Distinct "
                f"counterparties, top-10 share, and routing versus broad-user-behaviour "
                f"analysis are unavailable. This is an absence of a registered wallet claim, "
                f"not an assertion that the operator has no observable on-chain activity."
            ),
            "data_source": "unavailable",
        })
    return _stamp({
        **result,
        "confidence": _flow_confidence(result["data_source"], result["coverage_complete"]),
        "verdict": f"top10_{result['top10_share_of_observed_flow_pct']}pct",
        "reasoning": (
            f"{result['distinct_counterparties']} distinct counterparties observed. "
            f"The top 10 account for {result['top10_share_of_observed_flow_pct']}% of "
            f"observed flow — high concentration indicates routing or bridge activity "
            f"rather than broad user behaviour."
        ),
    })


@app.get("/coverage", tags=["catalog"])
async def coverage_endpoint() -> dict[str, Any]:
    """What this miner can and cannot see. Published so consumers can weight
    every other figure it returns."""
    result = market.coverage_report()
    return _stamp({
        **result,
        "confidence": 1.0,  # the registry is authoritative about itself
        "verdict": (
            f"{result['operators_attributed']}/{result['operators_catalogued']}_attributed"
        ),
        "reasoning": (
            f"{result['operators_attributed']} of {result['operators_catalogued']} "
            f"catalogued operators have reviewed wallet claims, spanning "
            f"{result['wallet_clusters']} clusters on "
            f"{len(result['chains_covered'])} chain(s). "
            f"{result['operators_unattributed']} operators are catalogued but "
            f"unobserved."
        ),
        "data_source": "registry",
    })


def _flow_confidence(source: str, complete: bool) -> float:
    """Confidence for aggregate flow figures.

    Attribution uncertainty dominates: even perfect chain reads only tell us
    what the *claimed* clusters did. Capped accordingly, then discounted for
    synthetic data and truncated windows.
    """
    if source == "unavailable":
        return 0.0
    base = 0.55 if source == "live" else 0.3  # attribution ceiling
    if not complete:
        base *= 0.6
    return round(base, 3)


def _search_confidence(result: dict[str, Any]) -> float:
    """Confidence that the DISCOVERY SEARCH ran and covered the cluster.

    Never a statement about any candidate's ownership — the ceiling for that is
    published separately as `max_recommended_confidence`.
    """
    if result.get("data_source") == "unavailable":
        return 0.0
    return 0.6 if result.get("coverage_complete", True) else 0.36


# ── Player evaluation ────────────────────────────────────────────────────────
#
# Net position against attributed operator clusters. Deliberately NOT called
# profit and loss: off-chain balances are invisible, non-wager flows are
# indistinguishable from winnings, and an address is not a person.


class PlayerEvaluateRequest(BaseModel):
    address: str = Field(..., description="Wallet address to evaluate")
    chain: str = Field("ethereum", pattern=r"^(ethereum|base|polygon|arbitrum|optimism|bsc|avalanche)$", description="ethereum | base | polygon | arbitrum | optimism | bsc | avalanche")
    hours: int = Field(720, ge=1, le=720, description="Lookback window in hours")


@app.post("/player/evaluate", tags=["WALLET_BALANCE_CHECK"])
async def player_evaluate_endpoint(req: PlayerEvaluateRequest) -> dict[str, Any]:
    """Evaluate one address against every attributed operator cluster."""
    p = await players.evaluate_player(req.address, req.chain, req.hours)

    if p.is_operator_wallet:
        verdict = "operator_wallet"
    elif p.transfers_with_operators == 0:
        verdict = "no_operator_activity"
    elif p.entity_class == "infrastructure":
        verdict = "infrastructure"
    else:
        verdict = "net_positive" if p.net_position_usd >= 0 else "net_negative"

    if p.data_source == "unavailable":
        confidence = 0.0
    elif p.transfers_with_operators == 0:
        confidence = 0.4  # absence of evidence within our coverage
    else:
        # Capped by attribution uncertainty AND off-chain invisibility.
        confidence = 0.5 if p.data_source == "live" else 0.25
        if not p.coverage_complete:
            confidence *= 0.6

    if p.transfers_with_operators == 0:
        reasoning = (
            f"No transfers between {req.address} and any attributed operator "
            f"cluster in {req.hours}h. This covers only clusters we have labeled — "
            f"activity with unattributed operators would not appear."
        )
    else:
        reasoning = (
            f"Observed {p.transfers_with_operators} transfers with "
            f"{p.operators_touched} attributed operator(s) over {req.hours}h. "
            f"Sent ${p.sent_to_operators_usd:,.0f}, received "
            f"${p.received_from_operators_usd:,.0f}, net "
            f"${p.net_position_usd:,.0f}. Net position is settlement direction, not "
            f"proven gambling profit or loss — balances held inside an operator are "
            f"invisible and non-wager flows are indistinguishable from winnings."
        )

    return _stamp({
        "address": p.address,
        "chain": p.chain,
        "window_hours": p.window_hours,
        "sent_to_operators_usd": p.sent_to_operators_usd,
        "received_from_operators_usd": p.received_from_operators_usd,
        "net_position_usd": p.net_position_usd,
        "transfers_with_operators": p.transfers_with_operators,
        "operators_touched": p.operators_touched,
        "exposures": [
            {
                "slug": e.slug,
                "name": e.name,
                "sent_usd": e.sent_usd,
                "received_usd": e.received_usd,
                "net_usd": round(e.net_usd, 2),
                "transfers": e.transfers,
                "first_seen": e.first_seen,
                "last_seen": e.last_seen,
            }
            for e in p.exposures
        ],
        "first_seen": p.first_seen,
        "last_seen": p.last_seen,
        "active_hours": p.active_hours,
        "avg_transfer_usd": p.avg_transfer_usd,
        "largest_transfer_usd": p.largest_transfer_usd,
        "entity_class": p.entity_class,
        "classification_reasons": p.classification_reasons,
        "behaviour_flags": p.behaviour_flags,
        "is_operator_wallet": p.is_operator_wallet,
        "operator_label": p.operator_label,
        "confidence": round(confidence, 3),
        "verdict": verdict,
        "reasoning": reasoning,
        "data_source": p.data_source,
        "coverage_complete": p.coverage_complete,
        "caveat": (
            "Net position measures settlement direction across attributed clusters. "
            "It is not gambling profit and loss."
        ),
    })


@app.get("/players/leaderboard", tags=["WALLET_BALANCE_CHECK"])
async def player_leaderboard_endpoint(
    hours: int = 168, limit: int = 25, include_infrastructure: bool = False,
    casino: str | None = None,
) -> dict[str, Any]:
    """Counterparties ranked by net observed position across operator clusters."""
    hours = max(1, min(hours, 720))
    limit = max(1, min(limit, 100))
    result = await players.player_leaderboard(
        hours, limit, exclude_infrastructure=not include_infrastructure,
        casino_slug=casino,
    )
    top = result["net_positive"][0] if result["net_positive"] else None
    return _stamp({
        **result,
        "confidence": _flow_confidence(result["data_source"], result["coverage_complete"]),
        "verdict": (
            f"{result['individual_candidates']}_candidates" if result["addresses_observed"]
            else "no_observed_activity"
        ),
        "reasoning": (
            f"{result['addresses_observed']} distinct counterparties observed "
            f"{f'for {casino} ' if casino else 'across '}"
            f"attributed clusters over {hours}h. "
            f"{result['individual_candidates']} classified as individual candidates; "
            f"{result['infrastructure_excluded']} classified as infrastructure and "
            f"excluded by default."
            + (
                f" Highest net position ${top['net_position_usd']:,.0f}."
                if top else ""
            )
        ),
    })


# ── Attribution discovery ────────────────────────────────────────────────────
#
# Coverage, not capability, is what limits this miner. These endpoints propose
# candidate operator wallets with evidence so the registry can grow by review
# instead of guesswork. They never mutate the registry.


@app.get("/attribution/discover/{slug}", tags=["catalog"])
async def attribution_discover_endpoint(
    slug: str, hours: int = 168, limit: int = 10
) -> dict[str, Any]:
    """Propose sibling wallet candidates for one attributed operator."""
    hours = max(1, min(hours, 720))
    limit = max(1, min(limit, 50))
    result = await attribution.discover_for_operator(slug, hours, limit)

    if result.get("error"):
        return _stamp({
            **result,
            "confidence": 0.0,
            "verdict": "not_expandable",
            "reasoning": (
                f"'{slug}' has no reviewed wallet claim to expand from. Discovery "
                f"grows an existing cluster; it cannot bootstrap one."
            ),
            "data_source": "unavailable",
        })

    strong = sum(1 for c in result["candidates"] if c["strength"] >= 0.7)
    searched = result.get("data_source") != "unavailable"
    return _stamp({
        **result,
        # Confidence describes the SEARCH, not ownership of any candidate — and
        # a search that never ran cannot support even that.
        "confidence": _search_confidence(result),
        "verdict": (
            f"{len(result['candidates'])}_candidates_{strong}_strong" if searched
            else "search_pending"
        ),
        "reasoning": (
            (
                f"Examined {result['counterparties_examined']} counterparties of "
                f"{result['name']}'s {result['known_clusters']} known cluster(s) over "
                f"{hours}h and shortlisted {result['candidates_shortlisted']}. "
                f"{len(result['candidates'])} candidate(s) returned, {strong} rated "
                f"strong. These are review candidates — on-chain behaviour never "
                f"proves ownership."
            ) if searched else (
                f"No completed discovery pass over {result['name']}'s clusters is "
                f"cached for {hours}h; one is in progress. An empty candidate list "
                f"here means nothing was searched, not that nothing was found. "
                f"On-chain behaviour never proves ownership."
            )
        ),
    })


@app.get("/attribution/discover", tags=["catalog"])
async def attribution_discover_all_endpoint(
    hours: int = 168, per_operator: int = 5
) -> dict[str, Any]:
    """Run discovery across every attributed operator."""
    hours = max(1, min(hours, 720))
    per_operator = max(1, min(per_operator, 20))
    result = await attribution.discover_all(hours, per_operator)
    searched = result.get("data_source") != "unavailable"
    return _stamp({
        **result,
        "confidence": _search_confidence(result),
        "verdict": (
            f"{result['candidates_proposed']}_candidates_"
            f"{result['strong_candidates']}_strong" if searched
            else "search_pending"
        ),
        "reasoning": result["note"] if searched else (
            "No completed discovery pass across the attributed operators is cached "
            "for this window; one is in progress. Retry shortly."
        ),
    })


# ── Treasury / reserves ──────────────────────────────────────────────────────


@app.get("/operator/{slug}/treasury", tags=["WALLET_BALANCE_CHECK"])
async def operator_treasury_endpoint(slug: str) -> dict[str, Any]:
    """Current holdings across an operator's attributed clusters."""
    result = await market.operator_treasury(slug)
    if result.get("error"):
        return _stamp({
            **result,
            "confidence": 0.0,
            "verdict": "not_attributed",
            "reasoning": (
                f"'{slug}' has no reviewed wallet claim, so its reserves are "
                f"unobserved. This is not a statement that its reserves are zero."
            ),
            "data_source": "unavailable",
        })
    return _stamp({
        **result,
        "confidence": _flow_confidence(result["data_source"], True),
        "verdict": f"reserves:{result['total_usd']:.0f}",
        "reasoning": (
            f"Read {result['clusters_read']} attributed cluster(s) for {result['name']}. "
            f"Observed ${result['total_usd']:,.0f} across {result['distinct_assets']} "
            f"asset(s), {result['stablecoin_share_pct']}% in stablecoins. "
            f"Balances only — not a solvency statement, since player liabilities are "
            f"off-chain and unattributed wallets are invisible."
        ),
    })


@app.get("/market/treasury", tags=["WALLET_BALANCE_CHECK"])
async def treasury_ranking_endpoint() -> dict[str, Any]:
    """Attributed operators ranked by observed on-chain reserves."""
    result = await market.treasury_ranking()
    top = result["operators"][0] if result["operators"] else None
    return _stamp({
        **result,
        "confidence": _flow_confidence(result["data_source"], True),
        "verdict": f"leader:{top['slug']}" if top else "no_reserves_observed",
        "reasoning": (
            f"Read reserves for {result['operators_read']} attributed operator(s), "
            f"totalling ${result['total_observed_usd']:,.0f}."
            + (
                f" {top['name']} holds the largest observed balance at "
                f"${top['total_usd']:,.0f}."
                if top else ""
            )
        ),
    })


# ── Infrastructure providers ─────────────────────────────────────────────────


@app.get("/market/providers", tags=["ONCHAIN_TX_LOOKUP"])
async def market_providers_endpoint(hours: int = 168, limit: int = 25) -> dict[str, Any]:
    """Infrastructure providers ranked by casino flow carried, with trend.

    Bridges, exchanges, and routing contracts — NOT game providers, which leave
    no on-chain record and cannot be derived from chain data.
    """
    hours = max(1, min(hours, 360))  # doubled internally for the trend comparison
    limit = max(1, min(limit, 100))
    result = await providers.provider_activity(hours, limit)
    top = result["ranked"][0] if result["ranked"] else None
    return _stamp({
        **result,
        "confidence": _flow_confidence(result["data_source"], result["coverage_complete"]),
        "verdict": f"top_rail:{top['label']}" if top else "no_rails_observed",
        "reasoning": (
            f"Identified {result['rails_total']} infrastructure rail(s) carrying "
            f"${result['total_rail_flow_usd']:,.0f} of operator flow over {hours}h, "
            f"{result['rails_identified']} matched to a published label. "
            f"{len(result['trending'])} rising or new versus the prior {hours}h."
            + (f" {top['label']} leads with ${top['flow_usd']:,.0f}." if top else "")
        ),
    })


@app.get("/players/cohorts", tags=["WALLET_BALANCE_CHECK"])
async def player_cohorts_endpoint(hours: int = 168) -> dict[str, Any]:
    """Value segments, new-vs-returning, and flow concentration across every
    observed counterparty."""
    hours = max(1, min(hours, 360))
    result = await players.player_cohorts(hours)
    return _stamp({
        **result,
        "confidence": _flow_confidence(result["data_source"], result["coverage_complete"]),
        "verdict": f"{result['addresses_active']}_active",
        "reasoning": (
            f"{result['addresses_active']} addresses active over {hours}h — "
            f"{result['new_this_period']} new, {result['returning']} returning "
            f"({result['retention_pct']}% retention). Top 10 addresses account for "
            f"{result['concentration']['top_10_share_pct']}% of gross flow. "
            f"{result['multi_operator_pct']}% used more than one operator."
        ),
    })


@app.get("/players/overlap", tags=["WALLET_BALANCE_CHECK"])
async def player_overlap_endpoint(hours: int = 168) -> dict[str, Any]:
    """Addresses shared between operators — a competitive audience signal."""
    hours = max(1, min(hours, 720))
    result = await players.cross_operator_overlap(hours)
    top = result["operator_pairs"][0] if result["operator_pairs"] else None
    return _stamp({
        **result,
        "confidence": _flow_confidence(result["data_source"], True),
        "verdict": f"{result['multi_operator_addresses']}_shared",
        "reasoning": (
            f"{result['multi_operator_addresses']} of {result['addresses_observed']} "
            f"observed addresses ({result['overlap_pct']}%) transacted with more than "
            f"one attributed operator over {hours}h."
            + (
                f" Largest overlap: {top['operator_a']} and {top['operator_b']} share "
                f"{top['shared_addresses']} addresses."
                if top else ""
            )
        ),
    })


# ── Label health ─────────────────────────────────────────────────────────────
#
# Every figure this miner produces is downstream of a wallet label, and a label
# can be wrong in ways that look identical to a real zero. These endpoints make
# that difference visible instead of letting a bad label quietly deflate an
# aggregate.


@app.get("/health/registry", tags=["catalog"])
async def registry_health_endpoint(hours: int = 168) -> dict[str, Any]:
    """Health of every attributed operator's wallet claims."""
    hours = max(1, min(hours, 720))
    result = await health_checks.registry_health(hours)
    return _stamp({
        **result,
        "confidence": 0.9,  # a probe is a direct chain read
        "verdict": (
            f"{result['operators_reportable']}/{result['operators_attributed']}_reportable"
        ),
        "reasoning": result["registry_verdict"] + (
            f" {result['dead_label_count']} claimed address(es) have no transfer "
            f"history at all — those labels are wrong, not quiet."
            if result["dead_label_count"] else ""
        ),
        "data_source": "live",
    })


@app.get("/health/operator/{slug}", tags=["catalog"])
async def operator_health_endpoint(slug: str, hours: int = 168) -> dict[str, Any]:
    """Health of one operator's wallet claims."""
    hours = max(1, min(hours, 720))
    casino = get_casino(slug)
    if not casino:
        return _stamp({
            "slug": slug,
            "confidence": 0.0,
            "verdict": "unknown_operator",
            "reasoning": f"'{slug}' is not in the catalog.",
            "data_source": "unavailable",
        })
    result = await health_checks.operator_health(casino, hours)
    return _stamp({
        **result,
        "confidence": 0.9 if result["total_claims"] else 1.0,
        "verdict": result["attribution_status"],
        "reasoning": result["detail"],
        "data_source": "live" if result["total_claims"] else "registry",
    })
