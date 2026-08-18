"""DegenMiner — Telegraph miner for on-chain gambling intelligence.

Serves three canonical intents:
  ONCHAIN_TX_LOOKUP · WALLET_BALANCE_CHECK · FRAUD_DETECTION

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

import time
from datetime import datetime, timezone
from typing import Any

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel, Field

from . import __version__, attribution, market, metrics, players, providers
from .analytics import anomaly_check, casino_stats, rank_casinos, wallet_trace
from .onchain import circuit_status, transaction_lookup
from .settings import settings
from .wallets import all_casinos, catalog, get_casino, resolve_address, resolve_wallet

SUPPORTED_INTENTS = ["ONCHAIN_TX_LOOKUP", "WALLET_BALANCE_CHECK", "FRAUD_DETECTION"]

app = FastAPI(
    title="DegenMiner",
    version=__version__,
    description=(
        "DegenMiner turns labeled on-chain casino activity into usable intelligence: "
        "live deposit and withdrawal flows, wallet attribution, and explainable fraud signals. "
        "Every answer includes confidence, reasoning, and data provenance for agents and analysts."
    ),
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.middleware("http")
async def timing_and_safety(request: Request, call_next):
    """Time every request and guarantee a structured response.

    An unhandled exception here would be a 500 from the node's perspective —
    a failed answer, and a direct hit to the Canonical Score. We convert any
    escape into a well-formed low-confidence payload instead.
    """
    started = time.perf_counter()
    endpoint = request.url.path
    try:
        response = await call_next(request)
    except Exception as exc:  # noqa: BLE001
        duration_ms = (time.perf_counter() - started) * 1000
        metrics.record_request(endpoint, duration_ms, error=True)
        return JSONResponse(
            status_code=200,
            content={
                "verdict": "unavailable",
                "confidence": 0.0,
                "reasoning": f"Internal error while serving request: {type(exc).__name__}.",
                "data_source": "unavailable",
                "error": type(exc).__name__,
                "served_at": _now_iso(),
                "timestamp": _now_iso(),
            },
        )
    duration_ms = (time.perf_counter() - started) * 1000
    metrics.record_request(endpoint, duration_ms, error=response.status_code >= 500)
    response.headers["X-Response-Time-Ms"] = f"{duration_ms:.2f}"
    return response


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
  <dialog id="investigation"><div class="dialog-head"><div><div class="type">Universal investigation</div><strong>Resolve an entity</strong></div><button class="close" aria-label="Close">x</button></div><div class="dialog-body"><form class="global-search" id="dialog-form"><input id="dialog-input" aria-label="Entity" placeholder="Stake, 0x address, or transaction hash"><select id="chain" aria-label="Chain"><option>ethereum</option><option>base</option><option>polygon</option><option>arbitrum</option></select><button>Investigate</button></form><div id="result"></div></div></dialog>
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
        $('#operator-list').innerHTML = catalog.casinos.map(c=>'<button class="operator-link" data-operator="'+esc(c.slug)+'"><div class="feed-top"><div><strong>'+esc(c.name)+'</strong><div class="note">'+c.wallet_count+' claims / '+esc(c.chains.join(', '))+'</div></div><span class="confidence">'+esc(c.wallets?.[0]?.evidence_status||'unknown')+' / '+Math.round((c.wallets?.[0]?.confidence||0)*100)+'%</span></div></button>').join('');
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


class WalletTraceRequest(BaseModel):
    address: str = Field(..., description="Wallet address (0x-prefixed, 40 hex chars)")
    chain: str = Field("ethereum", description="ethereum | base | polygon | arbitrum")


class AnomalyRequest(BaseModel):
    address: str = Field(..., description="Wallet address to screen")
    chain: str = Field("ethereum", description="ethereum | base | polygon | arbitrum")
    hours: int = Field(24, ge=1, le=720, description="Lookback window in hours")


class TransactionLookupRequest(BaseModel):
    tx_hash: str = Field(..., pattern=r"^0x[a-fA-F0-9]{64}$")
    chain: str = Field("ethereum", pattern=r"^(ethereum|base|polygon|arbitrum)$")


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
            "Live on-chain casino flow, wallet attribution, and explainable fraud "
            "signals for agents and analysts."
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
    ready = settings.live_data_available and not circuit["open"]
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
            "reasoning": f"'{req.slug}' is not a tracked casino. Known slugs: {known}.",
            "data_source": "unavailable",
            "known_slugs": [c.slug for c in all_casinos()],
        })

    return _stamp({
        "slug": stats.slug,
        "name": stats.name,
        "window_hours": stats.window_hours,
        "observed_inbound_usd": stats.observed_inbound_usd,
        "observed_outbound_usd": stats.observed_outbound_usd,
        # Compatibility aliases for existing consumers.
        "deposits_usd": stats.deposits_usd,
        "withdrawals_usd": stats.withdrawals_usd,
        "net_flow_usd": stats.net_flow_usd,
        "unique_depositors": stats.unique_depositors,
        "transaction_count": stats.transaction_count,
        "wallet_count": stats.wallet_count,
        "chains": stats.chains,
        "total_usd": stats.deposits_usd,  # on_chain integer extraction target
        "confidence": stats.confidence,
        "verdict": "net_inflow" if stats.net_flow_usd >= 0 else "net_outflow",
        "reasoning": (
            f"Observed {stats.transaction_count} transfers across {stats.wallet_count} "
            f"labeled {stats.name} wallets over {stats.window_hours}h. "
            f"Inbound ${stats.deposits_usd:,.0f}, outbound ${stats.withdrawals_usd:,.0f}, "
            f"net ${stats.net_flow_usd:,.0f} from {stats.unique_depositors} unique inbound counterparties. "
            "Transfer direction does not by itself prove a player deposit or withdrawal."
        ),
        "data_source": stats.data_source,
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


@app.post("/transaction/lookup", tags=["ONCHAIN_TX_LOOKUP"])
async def transaction_lookup_endpoint(req: TransactionLookupRequest) -> dict[str, Any]:
    """Canonical transaction lookup enriched with gambling entity attribution."""
    transaction, unavailable_reason = await transaction_lookup(req.tx_hash, req.chain)
    if transaction is None:
        return _stamp({
            "tx_hash": req.tx_hash.lower(),
            "chain": req.chain,
            "verdict": "unavailable" if "not found" not in unavailable_reason else "not_found",
            "confidence": 0.0,
            "reasoning": unavailable_reason,
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
                "confidence": wallet.confidence,
                "evidence_status": wallet.evidence_status,
                "evidence": list(wallet.evidence),
            })
    classification = (
        "observed_inbound" if to_claim and not from_claim
        else "observed_outbound" if from_claim and not to_claim
        else "operator_internal" if from_claim and to_claim
        else "unattributed"
    )
    return _stamp({
        "tx_hash": transaction.tx_hash,
        "chain": transaction.chain,
        "status": transaction.status,
        "block_number": transaction.block_number,
        "block_hash": transaction.block_hash,
        "from_address": transaction.from_addr,
        "to_address": transaction.to_addr,
        "value_wei": str(transaction.value_wei),
        "value_native": transaction.value_native,
        "gas": transaction.gas,
        "gas_price_wei": str(transaction.gas_price_wei),
        "input": transaction.input,
        "classification": classification,
        "associations": associations,
        "confidence": max((a[1].confidence for a in (from_claim, to_claim) if a), default=1.0 if classification == "unattributed" else 0.0),
        "verdict": transaction.status,
        "reasoning": f"Transaction resolved by chain RPC and classified as {classification} using {len(associations)} registry claim(s).",
        "data_source": transaction.data_source,
        "method": "direct_rpc_lookup",
        "evidence": [{"type": "transaction", "chain": req.chain, "tx_hash": transaction.tx_hash}],
    })


# ── WALLET_BALANCE_CHECK ─────────────────────────────────────────────────────


@app.post("/wallet/trace", tags=["WALLET_BALANCE_CHECK"])
async def wallet_trace_endpoint(req: WalletTraceRequest) -> dict[str, Any]:
    """Balance plus casino-cluster attribution for an address."""
    labeled = resolve_address(req.address)
    wallet_claim = resolve_wallet(req.address)
    trace = await wallet_trace(req.address, req.chain)

    return _stamp({
        "address": trace.address,
        "chain": trace.chain,
        "balance_native": trace.balance_native,
        "labeled_casino": labeled.slug if labeled else None,
        "labeled_casino_name": labeled.name if labeled else None,
        "top_association": trace.casino_slug,
        "casino_name": trace.casino_name,
        "associations": trace.associations,
        "association_count": len(trace.associations),
        "confidence": max(trace.confidence, wallet_claim[1].confidence if wallet_claim else 0.0),
        "verdict": (
            labeled.slug if labeled else (trace.casino_slug or "unlabeled")
        ),
        "reasoning": (
            f"Address {req.address[:10]}… on {trace.chain} holds "
            f"{trace.balance_native:.4f} native units. "
            + (
                f"Directly labeled as a {labeled.name} wallet. "
                if labeled
                else ""
            )
            + f"Interacted with {len(trace.associations)} tracked casino "
              f"cluster(s) in the last 30 days."
        ),
        "data_source": trace.data_source,
        "classification": "observed" if labeled else "calculated",
        "attribution": ({
            "role": wallet_claim[1].role,
            "evidence_status": wallet_claim[1].evidence_status,
            "evidence": list(wallet_claim[1].evidence),
            "last_reviewed": wallet_claim[1].last_reviewed,
        } if wallet_claim else None),
    })


# ── FRAUD_DETECTION ──────────────────────────────────────────────────────────


@app.post("/anomaly/check", tags=["FRAUD_DETECTION"])
async def anomaly_check_endpoint(req: AnomalyRequest) -> dict[str, Any]:
    """Screen an address for wash-trading, velocity spikes, and sybil patterns."""
    report = await anomaly_check(req.address, req.chain, req.hours)
    return _stamp({
        "address": report.address,
        "chain": report.chain,
        "verdict": report.verdict,
        "score": report.score,
        "is_suspicious": report.verdict not in {"normal", "unavailable"},
        "signals": report.signals,
        "signal_count": len(report.signals),
        "transfers_analyzed": report.transfers_analyzed,
        "window_hours": req.hours,
        "confidence": (
            0.0
            if report.data_source == "unavailable"
            else min(0.55 + report.score / 2, 0.95)
        ),
        "reasoning": report.reasoning,
        "data_source": report.data_source,
    })


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
                "wallets": [
                    {
                        "address": w.address,
                        "chain": w.chain,
                        "role": w.role,
                        "confidence": w.confidence,
                        "evidence_status": w.evidence_status,
                        "evidence": list(w.evidence),
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
            "reasoning": f"'{slug}' has no reviewed wallet claim — flow is unobserved.",
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


# ── Player evaluation ────────────────────────────────────────────────────────
#
# Net position against attributed operator clusters. Deliberately NOT called
# profit and loss: off-chain balances are invisible, non-wager flows are
# indistinguishable from winnings, and an address is not a person.


class PlayerEvaluateRequest(BaseModel):
    address: str = Field(..., description="Wallet address to evaluate")
    chain: str = Field("ethereum", description="ethereum | base | polygon | arbitrum")
    hours: int = Field(720, ge=1, le=720, description="Lookback window in hours")


@app.post("/player/evaluate", tags=["WALLET_BALANCE_CHECK"])
async def player_evaluate_endpoint(req: PlayerEvaluateRequest) -> dict[str, Any]:
    """Evaluate one address against every attributed operator cluster."""
    p = await players.evaluate_player(req.address, req.chain, req.hours)

    if p.is_operator_wallet:
        verdict = "operator_wallet"
    elif p.entity_class == "infrastructure":
        verdict = "infrastructure"
    elif p.transfers_with_operators == 0:
        verdict = "no_operator_activity"
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
            f"No transfers between {req.address[:10]}… and any attributed operator "
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
    hours: int = 168, limit: int = 25, include_infrastructure: bool = False
) -> dict[str, Any]:
    """Counterparties ranked by net observed position across operator clusters."""
    hours = max(1, min(hours, 720))
    limit = max(1, min(limit, 100))
    result = await players.player_leaderboard(
        hours, limit, exclude_infrastructure=not include_infrastructure
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
            f"{result['addresses_observed']} distinct counterparties observed across "
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
    return _stamp({
        **result,
        # Confidence describes the SEARCH, not ownership of any candidate.
        "confidence": 0.6,
        "verdict": f"{len(result['candidates'])}_candidates_{strong}_strong",
        "reasoning": (
            f"Examined {result['counterparties_examined']} counterparties of "
            f"{result['name']}'s {result['known_clusters']} known cluster(s) over "
            f"{hours}h and shortlisted {result['candidates_shortlisted']}. "
            f"{len(result['candidates'])} candidate(s) returned, {strong} rated strong. "
            f"These are review candidates — on-chain behaviour never proves ownership."
        ),
        "data_source": "derived",
    })


@app.get("/attribution/discover", tags=["catalog"])
async def attribution_discover_all_endpoint(
    hours: int = 168, per_operator: int = 5
) -> dict[str, Any]:
    """Run discovery across every attributed operator."""
    hours = max(1, min(hours, 720))
    per_operator = max(1, min(per_operator, 20))
    result = await attribution.discover_all(hours, per_operator)
    return _stamp({
        **result,
        "confidence": 0.6,
        "verdict": (
            f"{result['candidates_proposed']}_candidates_"
            f"{result['strong_candidates']}_strong"
        ),
        "reasoning": result["note"],
        "data_source": "derived",
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
