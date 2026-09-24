"""
Configurazione dell'agente LP Attivo (Liquidità Concentrata) su Base (Chain ID 8453).

Fornisce liquidità concentrata in un range ottimizzato su Uniswap V3 (es. WETH/USDC 0.05%),
raccoglie le commissioni maturate e riposiziona (re-center) il range quando il prezzo
esce dai bordi definiti, minimizzando l'impermanent loss e massimizzando il rendimento da fee.
"""

import os
from typing import Dict
from dotenv import load_dotenv

load_dotenv()


def _f(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, default))
    except (TypeError, ValueError):
        return float(default)


def _i(name: str, default: int) -> int:
    try:
        return int(float(os.getenv(name, default)))
    except (TypeError, ValueError):
        return int(default)


def _b(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in ("1", "true", "yes", "y", "on", "si")


# ---------------------------------------------------------------- rete Base
CHAIN_ID = 8453
CHAIN_NAME = "Base"
BASE_RPC_URL = os.getenv("BASE_RPC_URL", "https://mainnet.base.org")
WALLET_ADDRESS = os.getenv("WALLET_ADDRESS", "")
PRIVATE_KEY = os.getenv("PRIVATE_KEY", "")

# ---------------------------------------------------------------- token principali Base
WETH = "0x4200000000000000000000000000000000000006"
USDC = "0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913"
CBBTC = "0xcbB7C0000aB88B473b1f5aFd9ef808440eed33Bf"
WSTETH = "0xc1CBa3fCea344f92D9239c08C0568f6F2F0ee452"
CBETH = "0x2Ae3F1Ec7F1F5012CFEab0185bfc7aa3cf0DEc22"

KNOWN_ASSETS: Dict[str, Dict[str, any]] = {
    "USDC": {"address": USDC, "decimals": 6, "symbol": "USDC"},
    "WETH": {"address": WETH, "decimals": 18, "symbol": "WETH"},
    "CBBTC": {"address": CBBTC, "decimals": 8, "symbol": "CBBTC"},
    "wstETH": {"address": WSTETH, "decimals": 18, "symbol": "wstETH"},
    "cbETH": {"address": CBETH, "decimals": 18, "symbol": "cbETH"},
}

# ---------------------------------------------------------------- contratti Uniswap V3 su Base
UNISWAP_V3_FACTORY = "0x33128a8fC17869897dcE68Ed026d694621f6FDfD"
UNISWAP_V3_POSITION_MANAGER = "0x03a520b32C04BF3bEEf7BEb72E919cf822Ed34f1"
UNISWAP_V3_SWAP_ROUTER_02 = "0x2626664c2603336E57B271c5C0b26F421741e481"
UNISWAP_V3_QUOTER_V2 = "0x3d4e44Eb1374240CE5F1B871ab261CD16335B76a"

# Spacing dei tick per fee tier Uniswap V3
TICK_SPACINGS = {
    100: 1,      # 0.01%
    500: 10,     # 0.05%
    3000: 60,    # 0.30%
    10000: 200,  # 1.00%
}

# ---------------------------------------------------------------- configurazione Pool LP
POOL_TOKEN0_SYMBOL = os.getenv("POOL_TOKEN0", "WETH").upper()
POOL_TOKEN1_SYMBOL = os.getenv("POOL_TOKEN1", "USDC").upper()
POOL_FEE = _i("POOL_FEE", 500)  # default 0.05% (500 bps)

# Ampiezza del range di prezzo attorno al prezzo corrente (es. 8.0 -> +/- 4.0%)
RANGE_WIDTH_PCT = _f("RANGE_WIDTH_PCT", 8.0)

# Buffer prima del bordo per considerare la posizione out-of-range (in %)
OUT_OF_RANGE_BUFFER_PCT = _f("OUT_OF_RANGE_BUFFER_PCT", 0.5)

# Tempo minimo di permanenza in ore prima di poter riposizionare (anti-whipsaw)
MIN_HOLD_HOURS_BEFORE_RECENTER = _f("MIN_HOLD_HOURS_BEFORE_RECENTER", 2.0)

# Raccolta automatica delle commissioni
AUTO_COLLECT_FEES = _b("AUTO_COLLECT_FEES", True)
MIN_FEE_COLLECT_USD = _f("MIN_FEE_COLLECT_USD", 5.0)

# Reinvesti le fee raccolte nel nuovo mint del range
COMPOUND_FEES = _b("COMPOUND_FEES", True)

# Max slippage ammesso nelle operazioni (in basis points)
DEFAULT_SLIPPAGE_BPS = _i("DEFAULT_SLIPPAGE_BPS", 50)
MAX_SLIPPAGE_BPS = _i("MAX_SLIPPAGE_BPS", 100)
MAX_GAS_PRICE_GWEI = _f("MAX_GAS_PRICE_GWEI", 0.5)
MIN_ETH_RESERVE = _f("MIN_ETH_RESERVE", 0.002)

# ---------------------------------------------------------------- sicurezza & simulazione
DRY_RUN = _b("DRY_RUN", True)
PAPER_TRADING = _b("PAPER_TRADING", False)
PAPER_START_USDC = _f("PAPER_START_USDC", 1000.0)
PAPER_START_ETH = _f("PAPER_START_ETH", 0.5)
PAPER_GAS_USD = _f("PAPER_GAS_USD", 0.03)

if PAPER_TRADING:
    DRY_RUN = True

# ---------------------------------------------------------------- auto-refuel USDC da ETH
AUTO_SWAP_ETH_TO_USDC = _b("AUTO_SWAP_ETH_TO_USDC", True)
ETH_GAS_RESERVE = _f("ETH_GAS_RESERVE", 0.003)
MIN_ETH_SWAP_AMOUNT = _f("MIN_ETH_SWAP_AMOUNT", 0.002)
USDC_AUTO_SWAP_THRESHOLD = _f("USDC_AUTO_SWAP_THRESHOLD", 5.0)

# ---------------------------------------------------------------- AI Reasoning (OpenRouter)
OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY", "")
OPENROUTER_MODEL = os.getenv("OPENROUTER_MODEL", "meta-llama/llama-3.3-70b-instruct:free")
LLM_FALLBACK_RULES = _b("LLM_FALLBACK_RULES", True)

# ---------------------------------------------------------------- Telegram
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")

# ---------------------------------------------------------------- HTTP / transazioni
HTTP_TIMEOUT = int(os.getenv("HTTP_TIMEOUT", 30))  # timeout connessione RPC in secondi
TX_TIMEOUT_SECONDS = int(os.getenv("TX_TIMEOUT_SECONDS", 300))
TX_DEADLINE_SECONDS = int(os.getenv("TX_DEADLINE_SECONDS", 600))

# ---------------------------------------------------------------- Dashboard
DASHBOARD_HOST = os.getenv("DASHBOARD_HOST", "0.0.0.0")
DASHBOARD_PORT = _i("DASHBOARD_PORT", 8080)
DASHBOARD_ADMIN_TOKEN = os.getenv("DASHBOARD_ADMIN_TOKEN", "")

# ---------------------------------------------------------------- Loop
LOOP_SLEEP_SECONDS = _i("LOOP_SLEEP_SECONDS", 900)  # controllo ogni 15 minuti


# ---------------------------------------------------------------- percorsi persistenti
def persistent_path(filename: str) -> str:
    project_dir = os.path.dirname(os.path.abspath(__file__))
    data_dir = os.path.join(project_dir, "data")
    if os.path.isdir(data_dir) and os.access(data_dir, os.W_OK):
        return os.path.join(data_dir, filename)
    return os.path.join(project_dir, filename)


SQLITE_DB_PATH = os.getenv("SQLITE_DB_PATH") or persistent_path("lp_agent.db")
POSITION_STATE_PATH = os.getenv("POSITION_STATE_PATH") or persistent_path("lp_position.json")
PAPER_STATE_PATH = os.getenv("PAPER_STATE_PATH") or persistent_path("paper_lp.json")
