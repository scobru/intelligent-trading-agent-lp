"""
Persistenza SQLite per l'agente LP a Liquidita' Concentrata su Base.

Registra:
  - Snapshot periodici dell'equity, del valore della posizione, dell'in-range e dell'IL;
  - Storico delle operazioni di re-center, apertura (mint) e riscossione fee.
"""

import json
import logging
import os
import sqlite3
import time
from typing import Any, Dict, List, Optional

import config

logger = logging.getLogger(__name__)


def _connect():
    os.makedirs(os.path.dirname(os.path.abspath(config.SQLITE_DB_PATH)), exist_ok=True)
    conn = sqlite3.connect(config.SQLITE_DB_PATH, timeout=10.0)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    with _connect() as conn:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS lp_snapshots (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                created_at REAL NOT NULL,
                current_price REAL NOT NULL,
                current_tick INTEGER,
                total_equity_usd REAL NOT NULL,
                position_value_usd REAL,
                fees_collected_usd REAL,
                is_in_range INTEGER,
                impermanent_loss_pct REAL,
                net_pnl_usd REAL,
                details_json TEXT
            );

            CREATE TABLE IF NOT EXISTS lp_operations (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                created_at REAL NOT NULL,
                operation TEXT NOT NULL,
                amount_usd REAL,
                details_json TEXT,
                result_json TEXT,
                status TEXT,
                reason TEXT
            );

            CREATE INDEX IF NOT EXISTS idx_lp_snapshots_created ON lp_snapshots(created_at);
            CREATE INDEX IF NOT EXISTS idx_lp_operations_created ON lp_operations(created_at);

            CREATE TABLE IF NOT EXISTS bot_control (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
        """)


def log_snapshot(status: Dict[str, Any]):
    try:
        init_db()
        now = time.time()
        pos = status.get("position") or {}
        paper = status.get("paper") or {}
        tot_eq = paper.get("total_equity_usd", status.get("total_equity_usd", 0.0))

        with _connect() as conn:
            conn.execute("""
                INSERT INTO lp_snapshots (
                    created_at, current_price, current_tick, total_equity_usd,
                    position_value_usd, fees_collected_usd, is_in_range,
                    impermanent_loss_pct, net_pnl_usd, details_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                now,
                float(status.get("current_price", 0.0)),
                int(status.get("current_tick", 0)),
                float(tot_eq),
                float(pos.get("current_lp_value_usd", 0.0)),
                float(pos.get("fees_collected_usd", 0.0)),
                1 if pos.get("is_strictly_in_range") else 0,
                float(pos.get("impermanent_loss_pct", 0.0)),
                float(pos.get("net_pnl_usd", 0.0)),
                json.dumps(pos, default=str),
            ))
    except Exception as exc:
        logger.warning("Errore salvataggio snapshot LP a DB: %s", exc)


def log_operation(action: Dict[str, Any], result: Dict[str, Any]):
    try:
        init_db()
        now = time.time()
        op = action.get("operation", "unknown")
        amt = float(action.get("amount_usd", result.get("amount_usd", 0.0)))
        status = result.get("status", "unknown")
        reason = action.get("reason", "")

        with _connect() as conn:
            conn.execute("""
                INSERT INTO lp_operations (
                    created_at, operation, amount_usd, details_json, result_json, status, reason
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """, (
                now, op, amt,
                json.dumps(action, default=str),
                json.dumps(result, default=str),
                status, reason
            ))
    except Exception as exc:
        logger.warning("Errore salvataggio operazione LP a DB: %s", exc)


def get_recent_snapshots(limit: int = 50) -> List[Dict[str, Any]]:
    init_db()
    with _connect() as conn:
        cur = conn.execute("""
            SELECT * FROM lp_snapshots ORDER BY created_at DESC LIMIT ?
        """, (limit,))
        return [dict(r) for r in cur.fetchall()]


def get_recent_operations(limit: int = 50) -> List[Dict[str, Any]]:
    init_db()
    with _connect() as conn:
        cur = conn.execute("""
            SELECT * FROM lp_operations ORDER BY created_at DESC LIMIT ?
        """, (limit,))
        return [dict(r) for r in cur.fetchall()]


def fetch_dashboard_data(limit: int = 50) -> Dict[str, Any]:
    """Recupera tutti i dati storici (equity snapshots ed operazioni) per la dashboard."""
    init_db()
    with _connect() as conn:
        latest = conn.execute("SELECT * FROM lp_snapshots ORDER BY id DESC LIMIT 1").fetchone()
        equity = conn.execute("""
            SELECT created_at, total_equity_usd, position_value_usd, current_price, is_in_range
            FROM (SELECT * FROM lp_snapshots ORDER BY id DESC LIMIT 500)
            ORDER BY id ASC
        """).fetchall()
        ops = conn.execute("""
            SELECT * FROM lp_operations ORDER BY id DESC LIMIT ?
        """, (limit,)).fetchall()

    return {
        "snapshot_at": latest["created_at"] if latest else None,
        "equity": [dict(r) for r in equity],
        "operations": [dict(r) for r in ops],
    }


def is_bot_paused() -> bool:
    """Verifica se il bot LP e' in stato di pausa."""
    try:
        init_db()
        with _connect() as conn:
            row = conn.execute("SELECT value FROM bot_control WHERE key = 'is_paused'").fetchone()
            if row:
                return str(row["value"]).lower() in ("1", "true", "yes")
    except Exception:
        pass
    return False


def get_pause_info() -> Dict[str, Any]:
    """Recupera dettagli sullo stato di pausa del bot."""
    info = {"is_paused": False, "reason": "", "updated_at": ""}
    try:
        init_db()
        with _connect() as conn:
            rows = conn.execute("SELECT key, value, updated_at FROM bot_control WHERE key IN ('is_paused', 'pause_reason')").fetchall()
            for row in rows:
                if row["key"] == "is_paused":
                    info["is_paused"] = str(row["value"]).lower() in ("1", "true", "yes")
                    info["updated_at"] = row["updated_at"]
                elif row["key"] == "pause_reason":
                    info["reason"] = row["value"]
    except Exception:
        pass
    return info


def set_bot_paused(paused: bool, reason: str = "") -> None:
    """Imposta o rimuove lo stato di pausa del bot LP."""
    init_db()
    now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    val = "1" if paused else "0"
    with _connect() as conn:
        conn.execute("""
            INSERT INTO bot_control (key, value, updated_at) VALUES ('is_paused', ?, ?)
            ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at;
        """, (val, now))
        conn.execute("""
            INSERT INTO bot_control (key, value, updated_at) VALUES ('pause_reason', ?, ?)
            ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at;
        """, (reason or ("Pausa da Coordinatore/Operatore" if paused else "Operativo"), now))


