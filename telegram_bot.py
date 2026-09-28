"""
Interfaccia Telegram per il monitoraggio e il controllo dell'agente LP a Liquidita' Concentrata.

Fornisce:
  - Invio di alert proattivi quando il prezzo esce dal range o quando viene eseguito un re-center;
  - Comandi interattivi: /status, /position, /recenter, /collect, /help.
"""

import json
import logging
import threading
import time
from typing import Any, Dict, Optional

import config
import http_client
from lp_manager import LpManager

logger = logging.getLogger(__name__)


class TelegramNotifier:
    def __init__(self, manager: Optional[LpManager] = None):
        self.manager = manager
        self.token = config.TELEGRAM_BOT_TOKEN
        self.chat_id = config.TELEGRAM_CHAT_ID
        self.enabled = bool(self.token and self.chat_id)
        self._last_update_id = 0
        self._thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()

    def send_message(self, text: str) -> bool:
        if not self.enabled:
            return False
        url = f"https://api.telegram.org/bot{self.token}/sendMessage"
        payload = {
            "chat_id": self.chat_id,
            "text": text,
            "parse_mode": "Markdown",
            "disable_web_page_preview": True,
        }
        try:
            resp = http_client.post(url, json=payload, timeout=10)
            return resp.status_code == 200
        except Exception as exc:
            logger.warning("Errore invio messaggio Telegram: %s", exc)
            return False

    def notify_recenter(self, result: Dict[str, Any]):
        text = (
            f"🔄 *Concentrated LP Recenter Eseguito*\n\n"
            f"Operazione: `{result.get('operation')}`\n"
            f"Status: `{result.get('status')}`\n"
            f"Fee riscosse: `${result.get('fees_collected_usd', 0.0):.2f}`\n"
        )
        if result.get("new_position"):
            p = result["new_position"]
            text += f"Nuovo range: `${p.get('price_lower', 0):.2f}` - `${p.get('price_upper', 0):.2f}`\n"
        self.send_message(text)

    def notify_out_of_range(self, status: Dict[str, Any]):
        pos = status.get("position", {})
        text = (
            f"⚠️ *Allerta LP: Prezzo Fuori Range!*\n\n"
            f"Prezzo attuale: `${status.get('current_price', 0):.2f}`\n"
            f"Range attivo: `${pos.get('price_lower', 0):.2f}` - `${pos.get('price_upper', 0):.2f}`\n"
            f"IL Stimato: `{pos.get('impermanent_loss_pct', 0):.2f}%`\n"
            f"Azione: In attesa di re-centering..."
        )
        self.send_message(text)

    def start_polling(self):
        if not self.enabled or not self.manager:
            return
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._poll_loop, daemon=True)
        self._thread.start()

    def stop(self):
        self._stop_event.set()
        if self._thread:
            self._thread.join(timeout=3)

    def _poll_loop(self):
        while not self._stop_event.is_set():
            try:
                url = f"https://api.telegram.org/bot{self.token}/getUpdates?offset={self._last_update_id + 1}&timeout=10"
                resp = http_client.get(url, timeout=15)
                if resp.status_code == 200:
                    data = resp.json()
                    for update in data.get("result", []):
                        self._last_update_id = update["update_id"]
                        msg = update.get("message") or {}
                        text = msg.get("text", "").strip()
                        chat = msg.get("chat", {})
                        if str(chat.get("id")) == str(self.chat_id) and text.startswith("/"):
                            self._handle_command(text)
            except Exception as exc:
                logger.debug("Polling telegram exception: %s", exc)
            time.sleep(2)

    def _handle_command(self, cmd: str):
        parts = cmd.split()
        base_cmd = parts[0].lower().split("@")[0]

        if base_cmd in ("/start", "/help"):
            self.send_message(
                "🤖 *Comandi LP Agent:*\n"
                "/status - Stato del pool e dell'agente\n"
                "/position - Dettagli della posizione concentrata attiva\n"
                "/pools - Scanner migliori pool Uniswap V3 su Base per APY e Volume\n"
                "/switch <T0> <T1> [fee] - Cambia pool attiva (es. /switch CBBTC USDC 500)\n"
                "/recenter - Forza il riposizionamento del range sul prezzo corrente\n"
                "/collect - Raccoglie le commissioni accumulate\n"
                "/help - Questa guida"
            )
        elif base_cmd == "/status":
            st = self.manager.get_status(max_age=config.STATUS_CACHE_TTL_SECONDS)
            pos = st.get("position", {})
            pool_info = st.get("pool", {})
            t0 = pool_info.get("token0", "WETH")
            in_range = "✅ In-Range" if pos.get("is_strictly_in_range") else "❌ Out-of-Range"
            self.send_message(
                f"📊 *Stato LP Agent ({st.get('mode', '').upper()})*\n\n"
                f"Pool: `{pool_info.get('pair')}` ({pool_info.get('fee_tier', 500) / 10000:.2f}%)\n"
                f"Prezzo {t0}: `${st.get('current_price', 0):.4f}`\n"
                f"Stato: {in_range}\n"
                f"Valore LP: `${pos.get('current_lp_value_usd', 0):.2f}`\n"
                f"Fee Totali: `${st.get('total_fees_collected_usd', 0):.2f}`\n"
                f"Re-centers: `{st.get('total_recenters', 0)}`"
            )
        elif base_cmd == "/position":
            st = self.manager.get_status(max_age=config.STATUS_CACHE_TTL_SECONDS)
            pos = st.get("position", {})
            if not pos.get("has_position"):
                self.send_message("Nessuna posizione LP attiva.")
                return
            t0 = pos.get("token0", "WETH")
            t1 = pos.get("token1", "USDC")
            self.send_message(
                f"🎯 *Posizione LP Attiva ({t0}/{t1})*\n\n"
                f"Range: `${pos.get('price_lower', 0):.4f}` - `${pos.get('price_upper', 0):.4f}`\n"
                f"Progresso nel range: `{pos.get('range_progress_pct', 0)}%`\n"
                f"{t0}: `{pos.get('amount0', 0):.4f}` | {t1}: `${pos.get('amount1', 0):.2f}`\n"
                f"Impermanent Loss: `{pos.get('impermanent_loss_pct', 0):.2f}%` (${pos.get('impermanent_loss_usd', 0):.2f})\n"
                f"PnL Netto: `{pos.get('net_pnl_pct', 0):.2f}%` (${pos.get('net_pnl_usd', 0):.2f})\n"
                f"Tempo in posizione: `{pos.get('position_age_hours', 0)}` ore"
            )
        elif base_cmd == "/pools":
            try:
                from pool_scanner import scanner
                res = scanner.scan()
                top = res.get("top", [])[:5]
                if not top:
                    self.send_message("Nessuna pool trovata o scanner in aggiornamento.")
                    return
                lines = ["🌊 *Top Opportunità Uniswap V3 su Base:*"]
                for i, p in enumerate(top, 1):
                    lines.append(
                        f"{i}. *{p['symbol']}* ({p['fee_tier_pct']})\n"
                        f"   Fee APY: `{p['apy_base']}%` (30g: `{p['apy_mean_30d']}%`)\n"
                        f"   TVL: `${p['tvl_usd']:,.0f}` | Vol 24h: `${p['volume_24h_usd']:,.0f}`\n"
                        f"   Rischio: `{p['risk_level']}`"
                    )
                lines.append("\nUsa `/switch <T0> <T1> [fee]` per attivare una pool.")
                self.send_message("\n".join(lines))
            except Exception as exc:
                self.send_message(f"Errore scanner pool: {exc}")
        elif base_cmd == "/switch":
            if len(parts) < 3:
                self.send_message("Uso: `/switch <TOKEN0> <TOKEN1> [fee_tier]`\nEsempio: `/switch CBBTC USDC 500`")
                return
            t0 = parts[1].upper()
            t1 = parts[2].upper()
            fee = int(parts[3]) if len(parts) > 3 and parts[3].isdigit() else 500
            res = self.manager.switch_pool(t0, t1, fee)
            if res.get("status") == "success":
                self.send_message(
                    f"✅ *Pool Attiva Cambiata!*\n\n"
                    f"Coppia: `{res['pair']}` ({res['fee_tier'] / 10000:.2f}%)\n"
                    f"Prezzo attuale: `${res['current_price']:.4f}`\n"
                    f"Pool Address: `{res['pool_address']}`"
                )
            else:
                self.send_message(f"❌ Errore cambio pool: {res.get('reason')}")
        elif base_cmd == "/recenter":
            st = self.manager.get_status(max_age=config.STATUS_CACHE_TTL_SECONDS)
            act = {"operation": "recenter", "reason": "Re-center manuale richiesto da Telegram"}
            res = self.manager.execute_action(act, st)
            self.notify_recenter(res)
        elif base_cmd == "/collect":
            st = self.manager.get_status(max_age=config.STATUS_CACHE_TTL_SECONDS)
            act = {"operation": "collect_fees", "reason": "Raccolta manuale da Telegram"}
            res = self.manager.execute_action(act, st)
            self.send_message(f"💰 Risultato Collect: `{res.get('status')}` - Importo: `${res.get('amount_usd', 0):.2f}`")


if __name__ == "__main__":
    from base_client import BaseClient

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
    cl = BaseClient(rpc_url=config.BASE_RPC_URL)
    mgr = LpManager(cl)
    bot = TelegramNotifier(mgr)
    if bot.enabled:
        logger.info("📱 Telegram listener avviato in polling...")
        bot.start_polling()
        try:
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            logger.info("Arresto Telegram Bot...")
        finally:
            bot.stop()
    else:
        logger.info("Telegram Bot disabilitato (TELEGRAM_BOT_TOKEN o TELEGRAM_CHAT_ID non configurati).")

