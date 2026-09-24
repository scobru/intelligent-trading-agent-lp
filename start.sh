#!/bin/bash
set -u

echo "========================================================"
echo "Starting Concentrated LP Agent (Uniswap V3 on Base)"
echo "========================================================"

INTERVAL="${LOOP_SLEEP_SECONDS:-${TRADING_INTERVAL:-900}}"
PORT="${DASHBOARD_PORT:-${PORT:-3000}}"

echo "[1/2] Starting Web Dashboard on port ${PORT}..."
python dashboard.py &

if [ -n "${TELEGRAM_BOT_TOKEN:-}" ]; then
    echo "📱 Starting Telegram Bot listener..."
    python telegram_bot.py &
fi

echo "[2/2] Starting LP yield loop (interval: ${INTERVAL}s)..."
if [ "${PAPER_TRADING:-false}" = "true" ]; then
    echo "📝 PAPER attivo: portafoglio virtuale."
elif [ "${DRY_RUN:-true}" = "true" ]; then
    echo "🧪 DRY-RUN attivo: nessuna transazione verrà firmata."
fi
echo ""

while true; do
    echo "⏰ [$(date -u +%Y-%m-%dT%H:%M:%SZ)] Running LP cycle..."
    python main.py --once --no-dashboard
    echo "💤 Sleeping for ${INTERVAL} seconds until next cycle..."
    sleep "${INTERVAL}"
done
