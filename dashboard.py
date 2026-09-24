"""
Dashboard web per l'agente LP a Liquidita' Concentrata su Base.

Standard library HTTP server (senza dipendenze esterne come Flask/FastAPI):
  - Barra visiva interattiva del Range di Prezzo (In-Range / Out-of-Range);
  - Metriche chiave: Valore LP, Valore HODL, Impermanent Loss, Fee accumulate;
  - Equity curve e composizione asset (WETH / USDC);
  - Pulsanti manuali di Re-center, Fee Collection ed Esecuzione Ciclo;
  - Storico operazioni ed eventi registrati su SQLite.
"""

import hmac
import json
import logging
import os
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Dict, Optional
from urllib.parse import urlparse

from dotenv import load_dotenv

load_dotenv()

import config
import db_utils
from base_client import BaseClient
from lp_manager import LpManager

logger = logging.getLogger(__name__)

STATIC_ROUTES = {
    "/apple-touch-icon.png": ("static/apple-touch-icon.png", "image/png"),
    "/favicon.ico": ("static/favicon.ico", "image/x-icon"),
    "/icon-192.png": ("static/icon-192.png", "image/png"),
    "/icon-512.png": ("static/icon-512.png", "image/png"),
    "/icon.svg": ("static/icon.svg", "image/svg+xml"),
    "/icon-small.svg": ("static/icon-small.svg", "image/svg+xml"),
    "/site.webmanifest": ("static/site.webmanifest", "application/manifest+json"),
    "/dashboard.css": ("static/dashboard.css", "text/css"),
    "/dashboard.js": ("static/dashboard.js", "application/javascript"),
}

HTML_TEMPLATE = """<!DOCTYPE html>
<html lang="it">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Base Concentrated LP Agent</title>
  <link rel="icon" href="/favicon.ico" sizes="any">
  <link rel="icon" href="/icon.svg" type="image/svg+xml">
  <link rel="apple-touch-icon" href="/apple-touch-icon.png">
  <link rel="manifest" href="/site.webmanifest">
  <link rel="stylesheet" href="/dashboard.css">
  <style>
    :root {
      --bg: #090d16;
      --card-bg: #111827;
      --card-border: #1f293d;
      --text: #f3f4f6;
      --text-muted: #9ca3af;
      --primary: #3b82f6;
      --success: #10b981;
      --warning: #f59e0b;
      --danger: #ef4444;
      --accent: #8b5cf6;
    }
    body {
      background: var(--bg);
      color: var(--text);
      font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
      margin: 0;
      padding: 20px;
    }
    .container { max-width: 1200px; margin: 0 auto; }
    .header {
      display: flex;
      justify-content: space-between;
      align-items: center;
      padding-bottom: 20px;
      border-bottom: 1px solid var(--card-border);
      margin-bottom: 24px;
    }
    .badge {
      padding: 6px 12px;
      border-radius: 9999px;
      font-size: 0.85rem;
      font-weight: 600;
      text-transform: uppercase;
    }
    .badge-in { background: rgba(16,185,129,0.2); color: var(--success); border: 1px solid var(--success); }
    .badge-out { background: rgba(239,68,68,0.2); color: var(--danger); border: 1px solid var(--danger); }
    .badge-paper { background: rgba(139,92,246,0.2); color: var(--accent); border: 1px solid var(--accent); }
    .grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(240px, 1fr)); gap: 16px; margin-bottom: 24px; }
    .card {
      background: var(--card-bg);
      border: 1px solid var(--card-border);
      border-radius: 12px;
      padding: 20px;
    }
    .card-title { font-size: 0.85rem; color: var(--text-muted); margin-bottom: 8px; text-transform: uppercase; }
    .card-value { font-size: 1.8rem; font-weight: 700; }
    
    /* Range Visualizer */
    .range-box {
      background: var(--card-bg);
      border: 1px solid var(--card-border);
      border-radius: 12px;
      padding: 24px;
      margin-bottom: 24px;
    }
    .range-bar-container {
      position: relative;
      height: 36px;
      background: #1f2937;
      border-radius: 8px;
      margin: 24px 0 12px 0;
      overflow: hidden;
      border: 1px solid var(--card-border);
    }
    .range-active-zone {
      position: absolute;
      left: 15%;
      right: 15%;
      height: 100%;
      background: rgba(16, 185, 129, 0.25);
      border-left: 2px dashed var(--success);
      border-right: 2px dashed var(--success);
    }
    .range-price-pin {
      position: absolute;
      top: -4px;
      width: 4px;
      height: 44px;
      background: #ffffff;
      box-shadow: 0 0 10px rgba(255,255,255,0.8);
      transform: translateX(-50%);
      transition: left 0.5s ease;
    }
    .range-labels {
      display: flex;
      justify-content: space-between;
      color: var(--text-muted);
      font-size: 0.9rem;
    }
    
    table { width: 100%; border-collapse: collapse; margin-top: 12px; }
    th, td { padding: 12px; text-align: left; border-bottom: 1px solid var(--card-border); font-size: 0.9rem; }
    th { color: var(--text-muted); }
    .btn {
      background: var(--primary);
      color: white;
      border: none;
      padding: 10px 20px;
      border-radius: 8px;
      font-weight: 600;
      cursor: pointer;
      transition: opacity 0.2s;
    }
    .btn:hover { opacity: 0.9; }
    .btn-secondary { background: #374151; }
  </style>
</head>
<body>
<div class="container">
  <div class="header">
    <div>
      <h1 style="margin:0 0 4px 0;">⚡ Uniswap V3 Concentrated LP Agent</h1>
      <span style="color:var(--text-muted); font-size:0.9rem;">Base L2 (8453) &bull; Pair: <strong id="poolPair">WETH/USDC</strong></span>
    </div>
    <div style="display:flex; gap:10px; align-items:center;">
      <span id="badgeMode" class="badge badge-paper">PAPER</span>
      <span id="badgeStatus" class="badge badge-in">IN RANGE</span>
      <button id="btnRun" class="btn">Esegui Ciclo Ora</button>
    </div>
  </div>

  <!-- Key Metrics -->
  <div class="grid">
    <div class="card">
      <div class="card-title">Prezzo Corrente WETH</div>
      <div id="currPrice" class="card-value">$0.00</div>
      <div id="tickVal" style="color:var(--text-muted); font-size:0.85rem; margin-top:4px;">Tick: -</div>
    </div>
    <div class="card">
      <div class="card-title">Valore Posizione LP</div>
      <div id="lpVal" class="card-value">$0.00</div>
      <div id="lpBreakdown" style="color:var(--text-muted); font-size:0.85rem; margin-top:4px;">- WETH / - USDC</div>
    </div>
    <div class="card">
      <div class="card-title">Commissioni Riscosse</div>
      <div id="feesVal" class="card-value" style="color:var(--success);">$0.00</div>
      <div id="recentersCount" style="color:var(--text-muted); font-size:0.85rem; margin-top:4px;">0 Re-centers</div>
    </div>
    <div class="card">
      <div class="card-title">Impermanent Loss / PnL</div>
      <div id="ilVal" class="card-value">0.00%</div>
      <div id="pnlNet" style="color:var(--text-muted); font-size:0.85rem; margin-top:4px;">Net PnL: $0.00</div>
    </div>
  </div>

  <!-- Active Range Gauge -->
  <div class="range-box">
    <div style="display:flex; justify-content:space-between; align-items:center;">
      <h3 style="margin:0;">🎯 Posizionamento nel Range di Liquidità</h3>
      <span id="rangeWidth" style="color:var(--accent); font-weight:600;">Range: +/- 4.0%</span>
    </div>

    <div class="range-bar-container">
      <div class="range-active-zone"></div>
      <div id="pricePin" class="range-price-pin" style="left: 50%;"></div>
    </div>

    <div class="range-labels">
      <div>Bordo Inferiore: <strong id="priceLower">$0.00</strong></div>
      <div>Prezzo: <strong id="centerPrice">$0.00</strong> (<span id="rangeProgress">50%</span>)</div>
      <div>Bordo Superiore: <strong id="priceUpper">$0.00</strong></div>
    </div>
  </div>

  <!-- Operations Log -->
  <div class="card">
    <div style="display:flex; justify-content:space-between; align-items:center; margin-bottom:12px;">
      <h3 style="margin:0;">📜 Storico Operazioni LP</h3>
      <span style="color:var(--text-muted); font-size:0.85rem;">Aggiornamento live ogni 15s</span>
    </div>
    <table>
      <thead>
        <tr>
          <th>Orario</th>
          <th>Operazione</th>
          <th>Importo USD</th>
          <th>Status</th>
          <th>Dettagli / Motivo</th>
        </tr>
      </thead>
      <tbody id="opsBody">
        <tr><td colspan="5" style="text-align:center; color:var(--text-muted);">Caricamento dati...</td></tr>
      </tbody>
    </table>
  </div>
</div>

<script>
async function refresh() {
  try {
    const res = await fetch('/api/status');
    const d = await res.json();

    document.getElementById('poolPair').innerText = d.pool.pair + ' (' + (d.pool.fee_tier/10000).toFixed(2) + '%)';
    document.getElementById('badgeMode').innerText = (d.mode || 'paper').toUpperCase();
    document.getElementById('currPrice').innerText = '$' + d.current_price.toLocaleString('en-US', {minimumFractionDigits: 2, maximumFractionDigits: 2});
    document.getElementById('tickVal').innerText = 'Tick: ' + d.current_tick;

    const pos = d.position || {};
    const inRange = pos.is_strictly_in_range;
    const bStatus = document.getElementById('badgeStatus');
    if (inRange) {
      bStatus.className = 'badge badge-in';
      bStatus.innerText = 'IN RANGE';
    } else {
      bStatus.className = 'badge badge-out';
      bStatus.innerText = 'OUT OF RANGE';
    }

    document.getElementById('lpVal').innerText = '$' + (pos.current_lp_value_usd || 0).toFixed(2);
    document.getElementById('lpBreakdown').innerText = (pos.amount0 || 0).toFixed(4) + ' WETH / $' + (pos.amount1 || 0).toFixed(2) + ' USDC';
    document.getElementById('feesVal').innerText = '$' + (d.total_fees_collected_usd || 0).toFixed(2);
    document.getElementById('recentersCount').innerText = (d.total_recenters || 0) + ' Re-centers eseguiti';

    const ilPct = pos.impermanent_loss_pct || 0;
    const ilEl = document.getElementById('ilVal');
    ilEl.innerText = (ilPct >= 0 ? '+' : '') + ilPct.toFixed(2) + '%';
    ilEl.style.color = ilPct < -2.0 ? 'var(--danger)' : 'var(--text)';

    document.getElementById('pnlNet').innerText = 'Net PnL: $' + (pos.net_pnl_usd || 0).toFixed(2) + ' (' + (pos.net_pnl_pct || 0).toFixed(2) + '%)';

    // Range bar
    const pL = pos.price_lower || (d.current_price * 0.96);
    const pU = pos.price_upper || (d.current_price * 1.04);
    document.getElementById('priceLower').innerText = '$' + pL.toFixed(2);
    document.getElementById('priceUpper').innerText = '$' + pU.toFixed(2);
    document.getElementById('centerPrice').innerText = '$' + d.current_price.toFixed(2);
    document.getElementById('rangeWidth').innerText = 'Range: +/- ' + ((d.range_width_pct || 8.0)/2).toFixed(1) + '%';

    const prog = pos.range_progress_pct !== undefined ? pos.range_progress_pct : 50;
    document.getElementById('rangeProgress').innerText = prog.toFixed(1) + '%';
    // Mappa la posizione percentuale (15% to 85% zone)
    const pinPos = 15 + (prog * 0.70);
    document.getElementById('pricePin').style.left = Math.max(0, Math.min(100, pinPos)) + '%';

    // Ops history
    const opsRes = await fetch('/api/operations');
    const ops = await opsRes.json();
    const tbody = document.getElementById('opsBody');
    if (ops.length > 0) {
      tbody.innerHTML = ops.map(o => `
        <tr>
          <td>${new Date(o.created_at * 1000).toLocaleTimeString()}</td>
          <td><strong style="text-transform:uppercase;">${o.operation}</strong></td>
          <td>$${(o.amount_usd || 0).toFixed(2)}</td>
          <td><span style="color:${o.status === 'success' ? 'var(--success)' : 'var(--text-muted)'}">${o.status}</span></td>
          <td>${o.reason || '-'}</td>
        </tr>
      `).join('');
    } else {
      tbody.innerHTML = '<tr><td colspan="5" style="text-align:center; color:var(--text-muted);">Nessuna operazione registrata</td></tr>';
    }
  } catch(e) {
    console.error('Refresh error:', e);
  }
}

document.getElementById('btnRun').addEventListener('click', async () => {
  const token = prompt('Inserisci DASHBOARD_ADMIN_TOKEN (lascia vuoto se disabilitato):', '');
  try {
    const res = await fetch('/api/run', {
      method: 'POST',
      headers: {'Content-Type': 'application/json', 'X-Admin-Token': token || ''}
    });
    if (res.ok) {
      alert('Ciclo LP avviato con successo!');
      refresh();
    } else {
      alert('Avvio rifiutato.');
    }
  } catch(e) { alert('Errore: ' + e); }
});

refresh();
setInterval(refresh, 15000);
</script>
</body>
</html>
"""


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
            fpath = os.path.join(os.path.dirname(__file__), rel)
            if os.path.isfile(fpath):
                self.send_response(200)
                self.send_header("Content-Type", ctype)
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
            self.wfile.write(HTML_TEMPLATE.encode("utf-8"))
            return

        if path == "/api/status":
            try:
                st = self.manager.get_status() if self.manager else {}
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

    def do_POST(self):
        parsed = urlparse(self.path)
        path = parsed.path

        if path == "/api/run":
            if config.DASHBOARD_ADMIN_TOKEN:
                provided = self.headers.get("X-Admin-Token", "")
                if not hmac.compare_digest(provided, config.DASHBOARD_ADMIN_TOKEN):
                    self._send_json(401, {"error": "Unauthorized"})
                    return

            def _trigger():
                try:
                    subprocess.run([sys.executable, "main.py", "--once"], check=False)
                except Exception as exc:
                    logger.warning("Errore esecuzione main.py --once: %s", exc)

            threading.Thread(target=_trigger, daemon=True).start()
            self._send_json(200, {"status": "triggered"})
            return

        self.send_error(404)

    def _send_json(self, status: int, data: Any):
        body = json.dumps(data, default=str).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def run_dashboard(client: Optional[BaseClient] = None, manager: Optional[LpManager] = None):
    DashboardHandler.client = client
    DashboardHandler.manager = manager
    server = ThreadingHTTPServer((config.DASHBOARD_HOST, config.DASHBOARD_PORT), DashboardHandler)
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
