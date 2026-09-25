"""
Entrypoint principale per l'agente LP a Liquidita' Concentrata su Base (Chain ID 8453).

Pipeline di esecuzione ciclica:
  1. Verifica connettività RPC Base e saldi;
  2. Auto-refuel USDC da riserve native ETH se configurato;
  3. Recupero prezzo slot0/quoter e stato della posizione LP concentrata;
  4. Valutazione e decisione strategica (LLM OpenRouter + fallback deterministico);
  5. Esecuzione on-chain o simulata (mint, recenter, collect, hold);
  6. Persistenza snapshot ed eventi su SQLite;
  7. Notifiche via Telegram;
  8. Dashboard web integrata in background.
"""

import argparse
import logging
import sys
import threading
import time

import config
import db_utils
from base_client import BaseClient
from dashboard import run_dashboard
from lp_agent import LpAgent
from lp_manager import LpManager
from telegram_bot import TelegramNotifier

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger("main")


def run_cycle(manager: LpManager, agent: LpAgent, notifier: TelegramNotifier) -> None:
    if db_utils.is_bot_paused():
        pinfo = db_utils.get_pause_info()
        logger.info("⏸️ Bot LP in stato di PAUSA (%s). Ciclo ignorato.", pinfo.get("reason", "Pausa attiva"))
        return

    logger.info("--- Inizio ciclo LP (Modalità: %s) ---", "PAPER" if config.PAPER_TRADING else ("DRY_RUN" if config.DRY_RUN else "LIVE"))

    # 1. Auto-refuel USDC se necessario
    if config.AUTO_SWAP_ETH_TO_USDC and not config.PAPER_TRADING and manager.client and manager.client.address:
        try:
            refuel = manager.uniswap.auto_refuel_usdc()
            if refuel:
                logger.info("Auto-refuel completato: %s", refuel)
        except Exception as exc:
            logger.warning("Auto-refuel non riuscito: %s", exc)

    # 2. Stato corrente
    status = manager.get_status()
    pos = status.get("position", {})
    price = status.get("current_price", 0.0)

    logger.info(
        "Prezzo WETH: $%.2f | In-Range: %s | Valore LP: $%.2f | IL: %.2f%% | Fee riscosse: $%.2f",
        price,
        "SI" if pos.get("is_strictly_in_range") else "NO",
        pos.get("current_lp_value_usd", 0.0),
        pos.get("impermanent_loss_pct", 0.0),
        status.get("total_fees_collected_usd", 0.0),
    )

    # 3. Decisione (LLM o logica deterministica)
    decision = agent.decide(status)
    op = decision.get("operation", "hold")
    logger.info("Decisione strategica: %s -> %s", op.upper(), decision.get("reason", ""))

    # 4. Esecuzione
    result = manager.execute_action(decision, status)
    logger.info("Risultato operazione: %s", result.get("status"))

    # 5. Notifiche & Database
    db_utils.log_snapshot(status)
    if op != "hold":
        db_utils.log_operation(decision, result)
        if op == "recenter":
            notifier.notify_recenter(result)
        elif op == "collect_fees":
            notifier.send_message(f"💰 *Fee LP Riscosse*: `${result.get('amount_usd', 0):.2f}`")
    elif not pos.get("is_strictly_in_range") and pos.get("has_position"):
        notifier.notify_out_of_range(status)

    logger.info("--- Ciclo LP completato con successo ---")


def main():
    parser = argparse.ArgumentParser(description="Base Concentrated Liquidity LP Agent")
    parser.add_argument("--once", action="store_true", help="Esegui un singolo ciclo ed esci")
    parser.add_argument("--no-dashboard", action="store_true", help="Disabilita la dashboard web")
    args = parser.parse_args()

    db_utils.init_db()

    client = BaseClient(rpc_url=config.BASE_RPC_URL)
    manager = LpManager(client)
    agent = LpAgent(manager)
    notifier = TelegramNotifier(manager)

    if args.once:
        run_cycle(manager, agent, notifier)
        return

    # Avvio Dashboard Web in background
    if not args.no_dashboard:
        dash_thread = threading.Thread(target=run_dashboard, args=(client, manager), daemon=True)
        dash_thread.start()

    # Avvio polling comandi Telegram
    notifier.start_polling()

    logger.info("Agente LP attivo. Controllo ogni %s secondi...", config.LOOP_SLEEP_SECONDS)
    try:
        while True:
            try:
                run_cycle(manager, agent, notifier)
            except Exception as exc:
                logger.error("Eccezione durante il ciclo LP: %s", exc, exc_info=True)
            time.sleep(config.LOOP_SLEEP_SECONDS)
    except KeyboardInterrupt:
        logger.info("Arresto agente in corso...")
    finally:
        notifier.stop()


if __name__ == "__main__":
    main()
