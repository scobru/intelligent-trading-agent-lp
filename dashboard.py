"""
Dashboard web per l'agente LP a Liquidita' Concentrata su Base.

Condivide il design system (Inter, JetBrains Mono, ITA helpers, Chart.js, badge e card)
con gli altri agenti della suite (DCA, Yield, Neutral, Degen).
"""

import datetime
import hmac
import json
import logging
import os
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Dict, Optional
from urllib.parse import urlparse

from dotenv import load_dotenv

load_dotenv()

import config
import db_utils
import pool_scanner
from base_client import BaseClient
from lp_manager import LpManager

logger = logging.getLogger(__name__)

STATIC_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static")
STATIC_ROUTES = {
    "/favicon.ico": ("favicon.ico", "image/x-icon"),
    "/static/icon.svg": ("icon.svg", "image/svg+xml"),
    "/static/icon-small.svg": ("icon-small.svg", "image/svg+xml"),
    "/static/icon-192.png": ("icon-192.png", "image/png"),
    "/static/icon-512.png": ("icon-512.png", "image/png"),
    "/static/apple-touch-icon.png": ("apple-touch-icon.png", "image/png"),
    "/static/site.webmanifest": ("site.webmanifest", "application/manifest+json"),
    "/static/dashboard.css": ("dashboard.css", "text/css; charset=utf-8"),
    "/static/dashboard.js": ("dashboard.js", "application/javascript; charset=utf-8"),
    "/icon.svg": ("icon.svg", "image/svg+xml"),
    "/icon-small.svg": ("icon-small.svg", "image/svg+xml"),
    "/icon-192.png": ("icon-192.png", "image/png"),
    "/icon-512.png": ("icon-512.png", "image/png"),
    "/apple-touch-icon.png": ("apple-touch-icon.png", "image/png"),
    "/site.webmanifest": ("site.webmanifest", "application/manifest+json"),
    "/dashboard.css": ("dashboard.css", "text/css; charset=utf-8"),
    "/dashboard.js": ("dashboard.js", "application/javascript; charset=utf-8"),
}

HTML = r"""<!DOCTYPE html>
<html lang="it">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Concentrated LP Agent</title>
<link rel="icon" href="/favicon.ico" sizes="48x48">
<link rel="icon" href="/static/icon.svg" type="image/svg+xml">
<link rel="apple-touch-icon" href="/static/apple-touch-icon.png">
<link rel="manifest" href="/static/site.webmanifest">
<meta name="theme-color" content="#8b5cf6">
<link rel="preconnect" href="https://fonts.googleapis.com">
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800&family=JetBrains+Mono:wght@400;500;600&display=swap" rel="stylesheet">
<link rel="stylesheet" href="/static/dashboard.css?v=4">
<style>
:root {
  --primary: #8b5cf6;
  --accent: #a855f7;
}
.btn-sm {
  padding: 5px 12px;
  font-size: 11px;
  border-radius: 6px;
}
.btn-sm:hover:not(:disabled) {
  opacity: 0.9;
  transform: translateY(-1px);
}
.pool-pair {
  display: flex;
  align-items: center;
  gap: 8px;
  font-weight: 700;
  font-family: 'JetBrains Mono', monospace;
}
.pool-active-badge {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  color: var(--success);
  background: rgba(16, 185, 129, 0.12);
  border: 1px solid rgba(16, 185, 129, 0.35);
  font-size: 11px;
  font-weight: 700;
  padding: 3px 8px;
  border-radius: 9999px;
}
.range-visualizer {
  background: var(--surface-2);
  border: 1px solid var(--border);
  border-radius: var(--radius);
  padding: 18px;
  margin-top: 6px;
}
.range-track {
  position: relative;
  height: 28px;
  background: rgba(255, 255, 255, 0.04);
  border-radius: 8px;
  margin: 18px 0 12px;
  overflow: visible;
}
.range-zone {
  position: absolute;
  top: 0; bottom: 0;
  background: rgba(139, 92, 246, 0.22);
  border-left: 2px solid var(--primary);
  border-right: 2px solid var(--primary);
  border-radius: 6px;
}
.price-cursor {
  position: absolute;
  top: -8px;
  bottom: -8px;
  width: 4px;
  background: #10b981;
  border-radius: 2px;
  box-shadow: 0 0 12px #10b981;
  transform: translateX(-50%);
  transition: left 0.3s ease;
  z-index: 2;
}
.price-cursor.out-of-range {
  background: #ef4444;
  box-shadow: 0 0 12px #ef4444;
}
.cursor-tooltip {
  position: absolute;
  top: -26px;
  left: 50%;
  transform: translateX(-50%);
  font-size: 11px;
  font-weight: 700;
  font-family: 'JetBrains Mono', monospace;
  padding: 2px 7px;
  border-radius: 4px;
  background: #10b981;
  color: #0b0f19;
  white-space: nowrap;
}
.cursor-tooltip.out-of-range {
  background: #ef4444;
  color: #fff;
}
.range-bounds {
  display: flex;
  justify-content: space-between;
  font-size: 12px;
  color: var(--muted);
}
.range-bounds b {
  color: var(--text);
  font-family: 'JetBrains Mono', monospace;
}
</style>
<script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.1/dist/chart.umd.min.js"></script>
<script src="/static/dashboard.js?v=4"></script>
</head>
<body>
<header class="header">
  <div class="brand">
    <img src="/static/icon.svg" alt="">
    <div>
      <h1>Concentrated LP Agent <span class="badge b-no" id="mode">…</span></h1>
      <p class="tagline">Liquidità concentrata su Base • Scanner Uniswap V3 Multi-Pool • OpenRouter AI • SQLite</p>
    </div>
  </div>
  <div class="header-actions">
    <span class="updated" id="updated"></span>
    <button class="btn" id="run">⚡ Esegui ciclo ora</button>
  </div>
</header>

<section class="card paper-panel" id="paper-panel" hidden>
  <div class="card-head"><h2>📝 Paper trading <small>portafoglio virtuale, liquidità reale</small></h2></div>
  <div class="paper-grid" id="paper-grid"></div>
  <p class="note" id="paper-note"></p>
</section>

<section class="card wallet-bar" id="wallet-panel" hidden>
  <div class="wallet-items" id="wallet-items"></div>
  <p class="note" id="wallet-note" hidden></p>
</section>

<section class="stats">
  <div class="card">
    <h3>Valore Posizione LP</h3>
    <div class="value" id="total">--</div>
    <div class="sub" id="total-sub">--</div>
  </div>
  <div class="card">
    <h3 id="curr-price-title">Prezzo &amp; Range</h3>
    <div class="value" id="curr-price">--</div>
    <div class="sub" id="range-badge-wrap"><span class="badge b-no" id="range-status">--</span></div>
  </div>
  <div class="card">
    <h3>Commissioni Totali</h3>
    <div class="value" style="color:var(--success)" id="fees-val">--</div>
    <div class="sub" id="fees-sub">0 Re-centers</div>
  </div>
  <div class="card">
    <h3>Impermanent Loss &amp; PnL</h3>
    <div class="value" id="il-val">0.00%</div>
    <div class="sub" id="pnl-sub">Net PnL: $0.00</div>
  </div>
</section>

<section class="card section">
  <div class="card-head">
    <div class="tabs" data-tabs="chart">
      <button class="tab active" data-tab="equity">💼 Andamento capitale</button>
      <button class="tab" data-tab="price">📈 Prezzo vs Equity</button>
    </div>
  </div>
  <div class="chart-box tall"><canvas id="equity"></canvas></div>
</section>

<section class="card section">
  <div class="card-head">
    <h2>🎯 Range di Liquidità Attivo <small>Uniswap V3</small></h2>
    <span class="badge b-info" id="pool-info">WETH/USDC 0.05%</span>
  </div>
  <div class="range-visualizer">
    <div class="range-bounds">
      <div>Bordo Inferiore: <b id="price-lower">$0.00</b> <small id="tick-lower-sub"></small></div>
      <div>Ampiezza Range: <b id="range-width">&plusmn;4.0%</b></div>
      <div>Bordo Superiore: <b id="price-upper">$0.00</b> <small id="tick-upper-sub"></small></div>
    </div>
    <div class="range-track">
      <div class="range-zone" id="range-zone" style="left:15%; width:70%;"></div>
      <div class="price-cursor" id="price-cursor" style="left:50%;">
        <div class="cursor-tooltip" id="cursor-tooltip">$0.00</div>
      </div>
    </div>
    <div style="display:flex; justify-content:space-between; align-items:center; font-size:12px; color:var(--muted); margin-top:10px; flex-wrap:wrap; gap:8px;">
      <span>Progresso nel range: <b id="range-progress" style="color:var(--text)">50%</b></span>
      <span id="range-distance">Distanza bordi: --% / --%</span>
      <span>Tick corrente: <b id="curr-tick" style="color:var(--text)">--</b></span>
    </div>
  </div>
</section>

<!-- SCANNER DELLE MIGLIORI OPPORTUNITA' POOL UNISWAP V3 -->
<section class="card section" id="pools-section">
  <div class="card-head">
    <div>
      <h2>🌊 Opportunità Liquidity Pool <small>Scanner live Uniswap V3 su Base Chain</small></h2>
    </div>
    <div class="tabs" data-tabs="pools">
      <button class="tab active" data-tab="top">🔥 Top Score</button>
      <button class="tab" data-tab="bluechips">💎 Bluechips &amp; Stabili</button>
      <button class="tab" data-tab="high_yield">🚀 Alto Rendimento</button>
      <button class="tab" data-tab="all">🌐 Tutte le Pool</button>
    </div>
  </div>
  <div class="table-wrap"><table>
    <thead>
      <tr>
        <th>Coppia</th>
        <th>Tier</th>
        <th>TVL</th>
        <th>Volume 24h</th>
        <th>Fee APY (Live)</th>
        <th>Media 30g</th>
        <th>Turnover 24h</th>
        <th>Rischio IL</th>
        <th style="text-align:right">Azione</th>
      </tr>
    </thead>
    <tbody id="pools-tbody"><tr><td colspan="9" class="empty">Caricamento opportunità in corso...</td></tr></tbody>
  </table></div>
</section>

<div class="grid-2">
  <section class="card">
    <div class="card-head"><h2>📊 Composizione Posizione LP</h2><small id="pos-note">Pool su Base</small></div>
    <div class="table-wrap"><table>
      <thead><tr><th>Asset</th><th>Quantità</th><th>Prezzo</th><th>Valore USD</th><th>Quota LP</th></tr></thead>
      <tbody id="assets"></tbody>
    </table></div>
  </section>
  <section class="card">
    <div class="card-head"><h2>🧠 Ultima decisione AI</h2></div>
    <div class="decision-box">
      <div class="title" id="ai-action">In attesa del primo ciclo...</div>
      <div class="desc" id="ai-reason">L'agente valuterà la posizione LP al prossimo intervallo o con "Esegui ciclo ora".</div>
    </div>
    <div class="kv">
      <div><b>Modello:</b> OpenRouter (Llama 3.3 70B)</div>
      <div><b>Strategia:</b> Liquidità concentrata dinamica + re-centering anti-whipsaw</div>
    </div>
  </section>
</div>

<section class="card section">
  <div class="card-head"><h2>📜 Storico operazioni LP</h2></div>
  <div class="table-wrap"><table>
    <thead><tr><th>Data (UTC)</th><th>Operazione</th><th>Dettaglio</th><th>Importo</th><th>Esito</th><th>Motivazione</th></tr></thead>
    <tbody id="ops"></tbody>
  </table></div>
</section>

<footer class="footer">Concentrated LP Agent • Liquidità Concentrata su Base (Uniswap V3) &amp; OpenRouter AI • CapRover &amp; Docker</footer>

<script>
const { $, esc, isNum, usd, signedUsd, pct, signedPct, price, big, cls, time, empty, sideBadge, statusBadge } = ITA;
let chart = null, data = null, chartTab = 'equity', poolsTab = 'top';

function renderStatus(s) {
  if (!s) return;
  const pos = s.position || {};
  const balances = s.balances || {};
  const currPrice = s.current_price || 0;
  const pool = s.pool || {};
  const t0 = pool.token0 || 'WETH';
  const t1 = pool.token1 || 'USDC';

  const lpVal = pos.current_lp_value_usd || 0;
  const totVal = s.total_equity_usd || (s.paper ? s.paper.total_equity_usd : lpVal + (balances.total_usd || 0));
  $('total').textContent = usd(totVal);

  const amt0 = pos.amount0 || 0;
  const amt1 = pos.amount1 || 0;
  $('total-sub').textContent = `${amt0.toFixed(4)} ${t0} / ${usd(amt1)}`;

  $('curr-price-title').textContent = `Prezzo ${t0} & Range`;
  $('curr-price').textContent = usd(currPrice);
  const inRange = pos.is_strictly_in_range;
  const hasPos = pos.has_position;
  if (!hasPos) {
    $('range-status').textContent = 'NESSUNA POSIZIONE';
    $('range-status').className = 'badge b-no';
  } else if (inRange) {
    $('range-status').textContent = '✅ IN-RANGE';
    $('range-status').className = 'badge b-ok';
  } else {
    $('range-status').textContent = '⚠️ OUT-OF-RANGE';
    $('range-status').className = 'badge b-bad';
  }

  const uncoll = pos.fees_collected_usd || 0;
  const totFees = s.total_fees_collected_usd || 0;
  $('fees-val').textContent = usd(totFees + uncoll);
  const recenters = s.total_recenters || 0;
  $('fees-sub').textContent = `${recenters} Re-centers • non riscosse: ${usd(uncoll)}`;

  const il = pos.impermanent_loss_pct || 0;
  const netPnl = pos.net_pnl_usd || 0;
  const netPnlPct = pos.net_pnl_pct || 0;
  $('il-val').innerHTML = `<span class="${cls(il)}">${il >= 0 ? '+' : ''}${il.toFixed(2)}%</span>`;
  $('pnl-sub').innerHTML = `Net PnL: <span class="${cls(netPnl)}">${signedUsd(netPnl)} (${signedPct(netPnlPct)})</span>`;

  $('pos-note').textContent = `${pool.pair || 'WETH/USDC'} su Base`;

  renderRange(s);
  renderAssets(s);
}

function renderRange(s) {
  const pos = s.position || {};
  const currPrice = s.current_price || 0;
  const pLower = pos.price_lower || 0;
  const pUpper = pos.price_upper || 0;

  $('pool-info').textContent = `${s.pool?.pair || 'WETH/USDC'} (${((s.pool?.fee_tier || 500) / 10000).toFixed(2)}%)`;
  $('price-lower').textContent = pLower ? usd(pLower) : '--';
  $('price-upper').textContent = pUpper ? usd(pUpper) : '--';
  $('tick-lower-sub').textContent = pos.tick_lower != null ? `(Tick ${pos.tick_lower})` : '';
  $('tick-upper-sub').textContent = pos.tick_upper != null ? `(Tick ${pos.tick_upper})` : '';
  $('range-width').textContent = `±${((s.range_width_pct || 8.0) / 2).toFixed(1)}%`;
  $('curr-tick').textContent = s.current_tick != null ? s.current_tick : '--';

  const cursor = $('price-cursor');
  const tooltip = $('cursor-tooltip');
  const zone = $('range-zone');

  if (!pLower || !pUpper || !currPrice) {
    cursor.style.left = '50%';
    tooltip.textContent = usd(currPrice);
    return;
  }

  const rangeSpan = pUpper - pLower;
  const pad = rangeSpan * 0.4;
  const displayMin = Math.max(0, pLower - pad);
  const displayMax = pUpper + pad;
  const totalSpan = displayMax - displayMin;

  const zoneLeft = Math.max(0, Math.min(100, ((pLower - displayMin) / totalSpan) * 100));
  const zoneWidth = Math.max(0, Math.min(100 - zoneLeft, ((pUpper - pLower) / totalSpan) * 100));
  zone.style.left = zoneLeft.toFixed(1) + '%';
  zone.style.width = zoneWidth.toFixed(1) + '%';

  const cursorPct = Math.max(2, Math.min(98, ((currPrice - displayMin) / totalSpan) * 100));
  cursor.style.left = cursorPct.toFixed(1) + '%';
  tooltip.textContent = usd(currPrice);

  const inRange = pos.is_strictly_in_range;
  cursor.classList.toggle('out-of-range', !inRange);
  tooltip.classList.toggle('out-of-range', !inRange);

  $('range-progress').textContent = `${(pos.range_progress_pct || 50).toFixed(1)}%`;

  const distLower = pLower > 0 ? ((currPrice - pLower) / pLower * 100).toFixed(1) : '--';
  const distUpper = pUpper > 0 ? ((pUpper - currPrice) / currPrice * 100).toFixed(1) : '--';
  $('range-distance').textContent = `Distanza bordi: inf +${distLower}% • sup -${distUpper}%`;
}

function renderAssets(s) {
  const pos = s.position || {};
  const currPrice = s.current_price || 0;
  const balances = s.balances || {};
  const pool = s.pool || {};
  const t0 = pool.token0 || 'WETH';
  const t1 = pool.token1 || 'USDC';

  const rows = [];
  const lpVal = pos.current_lp_value_usd || 0;

  if (pos.amount0 != null || pos.amount1 != null) {
    const t0Val = (pos.amount0 || 0) * currPrice;
    const t1Val = pos.amount1 || 0;
    const t0Pct = lpVal > 0 ? (t0Val / lpVal * 100) : 0;
    const t1Pct = lpVal > 0 ? (t1Val / lpVal * 100) : 0;

    rows.push({
      symbol: `${t0} (in LP)`,
      amount: (pos.amount0 || 0).toFixed(4),
      price: usd(currPrice),
      value: usd(t0Val),
      weight: `${t0Pct.toFixed(1)}%`,
    });
    rows.push({
      symbol: `${t1} (in LP)`,
      amount: (pos.amount1 || 0).toFixed(2),
      price: usd(1.0),
      value: usd(t1Val),
      weight: `${t1Pct.toFixed(1)}%`,
    });
  }

  if (balances.ETH != null) {
    rows.push({
      symbol: 'ETH (Gas Wallet)',
      amount: Number(balances.ETH).toFixed(5),
      price: usd(currPrice),
      value: usd(Number(balances.ETH) * currPrice),
      weight: 'Riserva gas',
    });
  }
  if (balances.USDC != null && balances.USDC > 0) {
    rows.push({
      symbol: 'USDC (Idle Wallet)',
      amount: Number(balances.USDC).toFixed(2),
      price: usd(1.0),
      value: usd(balances.USDC),
      weight: 'Disponibile',
    });
  }

  $('assets').innerHTML = rows.map(r => `<tr>
    <td><b>${esc(r.symbol)}</b></td>
    <td class="num">${r.amount}</td>
    <td class="num">${r.price}</td>
    <td class="num"><b>${r.value}</b></td>
    <td class="num">${r.weight}</td>
  </tr>`).join('') || empty(5, 'Nessun asset attivo.');
}

function renderPools(poolsObj, activePool) {
  if (!poolsObj) return;
  const tbody = $('pools-tbody');
  if (!tbody) return;

  let list = [];
  if (poolsTab === 'top') list = poolsObj.top || [];
  else if (poolsTab === 'bluechips') list = poolsObj.bluechips || [];
  else if (poolsTab === 'high_yield') list = poolsObj.high_yield || [];
  else if (poolsTab === 'all') {
    list = [...(poolsObj.top || []), ...(poolsObj.bluechips || []), ...(poolsObj.high_yield || [])];
    const seen = new Set();
    list = list.filter(p => {
      const k = `${p.symbol}-${p.fee_tier}`;
      if (seen.has(k)) return false;
      seen.add(k);
      return true;
    });
  }

  const activePair = (activePool?.pair || 'WETH/USDC').toUpperCase();
  const activeFee = Number(activePool?.fee_tier || 500);

  if (!list.length) {
    tbody.innerHTML = empty(9, 'Nessuna pool trovata per questa categoria.');
    return;
  }

  tbody.innerHTML = list.map(p => {
    const isCur = (p.symbol.toUpperCase() === activePair || `${p.token0}/${p.token1}`.toUpperCase() === activePair) && (p.fee_tier === activeFee);
    const rClass = p.risk_level === 'Basso' ? 'b-ok' : (p.risk_level === 'Medio' ? 'b-info' : 'b-bad');
    const actionHtml = isCur
      ? `<span class="pool-active-badge">● IN USO</span>`
      : `<button class="btn btn-sm" onclick="switchPool('${p.token0}', '${p.token1}', ${p.fee_tier})">Attiva</button>`;

    return `<tr>
      <td><span class="pool-pair">${esc(p.symbol)}</span></td>
      <td><span class="badge b-no">${esc(p.fee_tier_pct)}</span></td>
      <td class="num">${big(p.tvl_usd)}</td>
      <td class="num">${big(p.volume_24h_usd)}</td>
      <td class="num"><b style="color:var(--success)">${p.apy_base.toFixed(2)}%</b></td>
      <td class="num">${p.apy_mean_30d.toFixed(2)}%</td>
      <td class="num">${p.efficiency.toFixed(2)}x</td>
      <td><span class="badge ${rClass}">${esc(p.risk_level)}</span></td>
      <td style="text-align:right">${actionHtml}</td>
    </tr>`;
  }).join('');
}

async function switchPool(t0, t1, fee) {
  if (!confirm(`Vuoi impostare ${t0}/${t1} (${(fee/10000).toFixed(2)}%) come pool attiva dell'agente LP?`)) {
    return;
  }
  try {
    const res = await fetch('/api/switch-pool', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ token0: t0, token1: t1, fee: fee })
    });
    const result = await res.json();
    if (res.ok && result.status === 'success') {
      alert(`✅ Pool attiva impostata su ${result.pair}!\nPrezzo corrente: $${result.current_price}\nL'agente inizierà a gestire questa posizione.`);
      await load();
    } else {
      alert('Errore switch pool: ' + (result.reason || result.error || 'Operazione non riuscita'));
    }
  } catch (err) {
    alert('Errore di rete durante lo switch pool: ' + err.message);
  }
}

function renderChart(points) {
  if (!points || !points.length) return;
  const labels = points.map(p => time(p.created_at));

  if (chartTab === 'equity') {
    chart = ITA.lineChart(chart, $('equity'), labels, [
      { label: 'Capitale Totale ($)', data: points.map(p => p.total_equity_usd) },
      { label: 'Valore Posizione LP ($)', data: points.map(p => p.position_value_usd) },
    ]);
  } else {
    chart = ITA.lineChart(chart, $('equity'), labels, [
      { label: 'Prezzo ($)', data: points.map(p => p.current_price) },
    ]);
  }
}

function renderOps(ops) {
  const last = ops[0];
  if (last) {
    $('last-op') && ($('last-op').textContent = (last.operation || '--').toUpperCase());
    $('ai-action').textContent = (last.operation || 'HOLD').toUpperCase();
    $('ai-reason').textContent = last.reason || 'Nessuna motivazione salvata.';
  }
  $('ops').innerHTML = ops.map(o => {
    let detail = '--';
    try {
      const d = typeof o.details_json === 'string' ? JSON.parse(o.details_json) : (o.details_json || {});
      if (o.operation === 'mint') {
        detail = `Range ${d.range_width_pct ? '±' + (d.range_width_pct/2).toFixed(1) + '%' : ''}`;
      } else if (o.operation === 'recenter') {
        detail = d.old_range ? `Da ${d.old_range}` : 'Riposizionamento range';
      } else if (o.operation === 'switch_pool') {
        detail = `Cambio pool a ${d.pair || ''}`;
      } else if (o.operation === 'collect_fees') {
        detail = 'Riscossione commissioni pool';
      } else {
        detail = d.reason ? esc(d.reason) : '--';
      }
    } catch(e) {}

    return `<tr>
      <td class="num">${esc(time(o.created_at))}</td>
      <td>${sideBadge(o.operation)}</td>
      <td>${esc(detail)}</td>
      <td class="num">${o.amount_usd ? usd(o.amount_usd) : '--'}</td>
      <td>${statusBadge(o.status)}</td>
      <td class="reason">${esc(o.reason || '')}</td>
    </tr>`;
  }).join('') || empty(6, 'Nessuna operazione registrata.');
}

async function load() {
  try {
    const res = await fetch('/api/data');
    data = await res.json();
  } catch (e) {
    $('updated').textContent = 'dashboard non raggiungibile';
    return;
  }
  ITA.renderMeta(data.meta);
  renderStatus(data.status);
  renderChart(data.equity || []);
  renderOps(data.operations || []);
  renderPools(data.pools, data.status?.pool);
}

ITA.setupTabs('chart', (t) => {
  chartTab = t;
  renderChart(data?.equity || []);
});
ITA.setupTabs('pools', (t) => {
  poolsTab = t;
  renderPools(data?.pools, data?.status?.pool);
});
ITA.setupRun(load);
load();
setInterval(load, 15000);
</script>
</body>
</html>
"""


def _iso_or_none(ts: Any) -> Optional[str]:
    """Formatta timestamp in stringa ISO/UTC leggibile da ITA.time in dashboard.js."""
    if ts is None:
        return None
    try:
        if isinstance(ts, (int, float)):
            dt = datetime.datetime.fromtimestamp(float(ts), tz=datetime.timezone.utc)
            return dt.strftime("%Y-%m-%d %H:%M:%S")
        elif isinstance(ts, str) and ts.replace('.', '', 1).isdigit():
            dt = datetime.datetime.fromtimestamp(float(ts), tz=datetime.timezone.utc)
            return dt.strftime("%Y-%m-%d %H:%M:%S")
        return str(ts)
    except Exception:
        return str(ts)


def build_meta(data: Dict[str, Any]) -> Dict[str, Any]:
    status = data.get("status") or {}
    mode = status.get("mode")
    if not mode:
        mode = "paper" if config.PAPER_TRADING else ("dry_run" if config.DRY_RUN else "live")

    paper = None
    if mode == "paper":
        p = status.get("paper") or {}
        paper = {
            "initial_usd": p.get("initial_equity_usd", config.PAPER_START_USDC),
            "value_usd": p.get("total_equity_usd", status.get("total_equity_usd")),
            "pnl_usd": p.get("pnl_usd"),
            "operations": p.get("operations", 0),
            "costs_usd": p.get("gas_spent_usd", 0.0),
            "costs_label": "Gas simulato",
            "started_at": p.get("created_at"),
            "extra": [
                ["Fee riscosse (lifetime)", f"${float(status.get('total_fees_collected_usd') or 0.0):,.2f}"],
                ["Re-centers eseguiti", str(status.get("total_recenters") or 0)],
            ],
            "note": "Liquidità concentrata Uniswap V3 su Base. Fee accumulate in tempo reale all'interno del range.",
        }

    wallet = None
    balances = status.get("balances") or {}
    eth_bal = balances.get("ETH")
    if mode != "paper" and eth_bal is not None:
        eth = float(eth_bal)
        weth_px = status.get("current_price", 0.0)
        wallet = {
            "address": status.get("wallet"),
            "eth": eth,
            "eth_usd": eth * float(weth_px) if weth_px else None,
            "min_eth": config.MIN_ETH_RESERVE,
            "warn_eth": 0.005,
            "extra": [["USDC nel wallet", f"${float(balances.get('USDC') or 0):,.2f}"]],
        }

    return {
        "mode": mode,
        "updated_at": _iso_or_none(data.get("snapshot_at") or time.time()),
        "run_enabled": True,
        "paper": paper,
        "wallet": wallet,
    }


class DashboardHandler(BaseHTTPRequestHandler):
    client: Optional[BaseClient] = None
    manager: Optional[LpManager] = None

    def log_message(self, format, *args):
        pass

    def do_GET(self):
        parsed = urlparse(self.path)
        path = parsed.path

        if path in STATIC_ROUTES:
            rel, ctype = STATIC_ROUTES[path]
            fpath = os.path.join(STATIC_DIR, rel)
            if os.path.isfile(fpath):
                self.send_response(200)
                self.send_header("Content-Type", ctype)
                self.send_header("Cache-Control", "public, max-age=86400")
                self.end_headers()
                with open(fpath, "rb") as fh:
                    self.wfile.write(fh.read())
                return
            self.send_error(404)
            return

        if path in ("/", "/index.html"):
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write(HTML.encode("utf-8"))
            return

        if path == "/health":
            self._send_json(200, {"status": "healthy", "service": "lp-dashboard"})
            return

        if path == "/api/pools":
            try:
                pools_data = pool_scanner.scanner.scan()
                self._send_json(200, pools_data)
            except Exception as exc:
                logger.error("Errore /api/pools: %s", exc)
                self._send_json(500, {"error": str(exc)})
            return

        if path == "/api/data":
            try:
                st = self.manager.get_status() if self.manager else {}
                db_data = db_utils.fetch_dashboard_data()
                meta = build_meta({"status": st, "snapshot_at": db_data.get("snapshot_at")})
                pools_data = pool_scanner.scanner.scan()
                payload = {
                    "status": st,
                    "snapshot_at": _iso_or_none(db_data.get("snapshot_at") or time.time()),
                    "equity": db_data.get("equity", []),
                    "operations": db_data.get("operations", []),
                    "pools": pools_data,
                    "meta": meta,
                    "run_enabled": True,
                }
                payload["is_paused"] = db_utils.is_bot_paused()
                payload["pause_info"] = db_utils.get_pause_info()
                self._send_json(200, payload)
            except Exception as exc:
                logger.error("Errore /api/data: %s", exc)
                self._send_json(500, {"error": str(exc)})
            return

        if path == "/api/status":
            try:
                st = self.manager.get_status() if self.manager else {}
                st["is_paused"] = db_utils.is_bot_paused()
                st["pause_info"] = db_utils.get_pause_info()
                self._send_json(200, st)
            except Exception as exc:
                self._send_json(500, {"error": str(exc)})
            return

        if path == "/api/operations":
            try:
                ops = db_utils.get_recent_operations(limit=30)
                self._send_json(200, ops)
            except Exception as exc:
                self._send_json(500, {"error": str(exc)})
            return

        if path == "/api/snapshots":
            try:
                snaps = db_utils.get_recent_snapshots(limit=50)
                self._send_json(200, snaps)
            except Exception as exc:
                self._send_json(500, {"error": str(exc)})
            return

        self.send_error(404)

    def _is_auth_valid(self) -> bool:
        run_token = os.getenv("DASHBOARD_RUN_TOKEN", config.DASHBOARD_ADMIN_TOKEN)
        if not run_token:
            return False
        provided = self.headers.get("X-Run-Token", "") or self.headers.get("X-Admin-Token", "")
        if not provided and "Authorization" in self.headers:
            auth = self.headers.get("Authorization", "")
            if auth.startswith("Bearer "):
                provided = auth[7:].strip()
            else:
                provided = auth.strip()
        return bool(provided and hmac.compare_digest(provided, run_token))

    def do_POST(self):
        parsed = urlparse(self.path)
        path = parsed.path

        if path not in ("/api/switch-pool", "/api/run", "/api/pause", "/api/resume", "/api/release_funds"):
            self.send_error(404)
            return

        if path in ("/api/run", "/api/pause", "/api/resume"):
            if not self._is_auth_valid():
                self._send_json(403, {"error": "Token non valido o mancante", "message": "Token non valido o mancante"})
                return

        if path == "/api/pause":
            reason = "Pausa richiesta da API"
            try:
                clen = int(self.headers.get("Content-Length", 0))
                if clen > 0:
                    body = json.loads(self.rfile.read(clen).decode("utf-8"))
                    reason = body.get("reason", reason)
            except Exception:
                pass
            db_utils.set_bot_paused(True, reason=reason)
            self._send_json(200, {"status": "success", "is_paused": True, "message": f"Bot in pausa: {reason}"})
            return

        if path == "/api/resume":
            db_utils.set_bot_paused(False)
            self._send_json(200, {"status": "success", "is_paused": False, "message": "Bot riattivato con successo."})
            return

        if path == "/api/switch-pool":
            try:
                clen = int(self.headers.get("Content-Length", 0))
                raw_body = self.rfile.read(clen) if clen > 0 else b"{}"
                payload = json.loads(raw_body.decode("utf-8")) if raw_body else {}
                t0 = str(payload.get("token0", "")).strip().upper()
                t1 = str(payload.get("token1", "")).strip().upper()
                fee = int(payload.get("fee", 500))

                if not t0 or not t1 or t0 == t1:
                    self._send_json(400, {"status": "error", "reason": "Coppia token non valida."})
                    return

                if not self.manager:
                    self._send_json(500, {"status": "error", "reason": "Manager non inizializzato."})
                    return

                res = self.manager.switch_pool(t0, t1, fee)
                if res.get("status") == "success":
                    st = self.manager.get_status()
                    db_utils.log_snapshot(st)
                    db_utils.log_operation(
                        {"operation": "switch_pool", "amount_usd": 0.0, "reason": f"Cambio manuale pool a {t0}/{t1} ({fee})"},
                        {"status": "success", "new_pair": f"{t0}/{t1}"}
                    )
                self._send_json(200, res)
            except Exception as exc:
                logger.error("Errore /api/switch-pool: %s", exc)
                self._send_json(500, {"status": "error", "reason": str(exc)})
            return

        if path == "/api/run":
            if db_utils.is_bot_paused():
                pinfo = db_utils.get_pause_info()
                self._send_json(200, {
                    "status": "paused",
                    "is_paused": True,
                    "message": f"Bot LP attualmente in PAUSA ({pinfo.get('reason', 'Pausa attiva')}). Ciclo ignorato."
                })
                return

            def _trigger():
                try:
                    subprocess.run([sys.executable, "main.py", "--once", "--no-dashboard"], check=False)
                except Exception as exc:
                    logger.warning("Errore esecuzione main.py --once: %s", exc)

            threading.Thread(target=_trigger, daemon=True).start()
            self._send_json(200, {"status": "triggered", "message": "Ciclo LP avviato in background."})
            return

        if path == "/api/release_funds":
            target_amount = 0.0
            try:
                clen = int(self.headers.get("Content-Length", 0))
                if clen > 0:
                    body = json.loads(self.rfile.read(clen).decode("utf-8"))
                    target_amount = float(body.get("amount_usd", 0.0) or 0.0)
            except Exception:
                pass
            try:
                from base_client import BaseClient
                from lp_manager import LPManager
                client = BaseClient()
                manager = LPManager(client)
                res = manager.release_funds(target_usdc=target_amount)
                self._send_json(200, res)
            except Exception as exc:
                self._send_json(500, {"status": "error", "message": str(exc)})
            return

        self.send_error(404)

    def _send_json(self, status: int, data: Any):
        body = json.dumps(data, default=str).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


class ReuseAddrHTTPServer(ThreadingHTTPServer):
    allow_reuse_address = True


def run_dashboard(client: Optional[BaseClient] = None, manager: Optional[LpManager] = None):
    DashboardHandler.client = client
    DashboardHandler.manager = manager
    try:
        server = ReuseAddrHTTPServer((config.DASHBOARD_HOST, config.DASHBOARD_PORT), DashboardHandler)
    except OSError as exc:
        logger.error("Impossibile avviare dashboard su %s:%s - %s", config.DASHBOARD_HOST, config.DASHBOARD_PORT, exc)
        return
    logger.info("LP Dashboard avviata su http://%s:%s", config.DASHBOARD_HOST, config.DASHBOARD_PORT)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        server.server_close()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    cl = BaseClient(rpc_url=config.BASE_RPC_URL)
    mgr = LpManager(cl)
    run_dashboard(cl, mgr)
