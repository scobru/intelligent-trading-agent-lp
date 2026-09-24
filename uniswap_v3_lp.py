"""
Modulo per la gestione della liquidita' concentrata su Uniswap V3 (e Aerodrome Slipstream) su Base.

Include:
  - Matematica dei tick Uniswap V3 (tick <-> sqrtPriceX96 <-> human price);
  - Calcolo della liquidita' L e quote token0 / token1;
  - Calcolo dell'Impermanent Loss teorico vs HODL;
  - Interazione con NonfungiblePositionManager (mint, increaseLiquidity, decreaseLiquidity, collect);
  - Interazione con i Pool Uniswap V3 (slot0, fee, ticks).
"""

import logging
import math
import time
from typing import Any, Dict, Optional, Tuple

import config
from base_client import BaseChainError, BaseClient

logger = logging.getLogger(__name__)

# ABI minimale Uniswap V3 Pool
POOL_ABI = [
    {
        "inputs": [],
        "name": "slot0",
        "outputs": [
            {"internalType": "uint160", "name": "sqrtPriceX96", "type": "uint160"},
            {"internalType": "int24", "name": "tick", "type": "int24"},
            {"internalType": "uint16", "name": "observationIndex", "type": "uint16"},
            {"internalType": "uint16", "name": "observationCardinality", "type": "uint16"},
            {"internalType": "uint16", "name": "observationCardinalityNext", "type": "uint16"},
            {"internalType": "uint8", "name": "feeProtocol", "type": "uint8"},
            {"internalType": "bool", "name": "unlocked", "type": "bool"},
        ],
        "stateMutability": "view",
        "type": "function",
    },
    {
        "inputs": [],
        "name": "liquidity",
        "outputs": [{"internalType": "uint128", "name": "", "type": "uint128"}],
        "stateMutability": "view",
        "type": "function",
    },
    {
        "inputs": [],
        "name": "fee",
        "outputs": [{"internalType": "uint24", "name": "", "type": "uint24"}],
        "stateMutability": "view",
        "type": "function",
    },
    {
        "inputs": [],
        "name": "token0",
        "outputs": [{"internalType": "address", "name": "", "type": "address"}],
        "stateMutability": "view",
        "type": "function",
    },
    {
        "inputs": [],
        "name": "token1",
        "outputs": [{"internalType": "address", "name": "", "type": "address"}],
        "stateMutability": "view",
        "type": "function",
    },
]

# ABI minimale NonfungiblePositionManager
POSITION_MANAGER_ABI = [
    {
        "inputs": [
            {
                "components": [
                    {"internalType": "address", "name": "token0", "type": "address"},
                    {"internalType": "address", "name": "token1", "type": "address"},
                    {"internalType": "uint24", "name": "fee", "type": "uint24"},
                    {"internalType": "int24", "name": "tickLower", "type": "int24"},
                    {"internalType": "int24", "name": "tickUpper", "type": "int24"},
                    {"internalType": "uint256", "name": "amount0Desired", "type": "uint256"},
                    {"internalType": "uint256", "name": "amount1Desired", "type": "uint256"},
                    {"internalType": "uint256", "name": "amount0Min", "type": "uint256"},
                    {"internalType": "uint256", "name": "amount1Min", "type": "uint256"},
                    {"internalType": "address", "name": "recipient", "type": "address"},
                    {"internalType": "uint256", "name": "deadline", "type": "uint256"},
                ],
                "internalType": "struct INonfungiblePositionManager.MintParams",
                "name": "params",
                "type": "tuple",
            }
        ],
        "name": "mint",
        "outputs": [
            {"internalType": "uint256", "name": "tokenId", "type": "uint256"},
            {"internalType": "uint128", "name": "liquidity", "type": "uint128"},
            {"internalType": "uint256", "name": "amount0", "type": "uint256"},
            {"internalType": "uint256", "name": "amount1", "type": "uint256"},
        ],
        "stateMutability": "payable",
        "type": "function",
    },
    {
        "inputs": [
            {
                "components": [
                    {"internalType": "uint256", "name": "tokenId", "type": "uint256"},
                    {"internalType": "uint128", "name": "liquidity", "type": "uint128"},
                    {"internalType": "uint256", "name": "amount0Min", "type": "uint256"},
                    {"internalType": "uint256", "name": "amount1Min", "type": "uint256"},
                    {"internalType": "uint256", "name": "deadline", "type": "uint256"},
                ],
                "internalType": "struct INonfungiblePositionManager.DecreaseLiquidityParams",
                "name": "params",
                "type": "tuple",
            }
        ],
        "name": "decreaseLiquidity",
        "outputs": [
            {"internalType": "uint256", "name": "amount0", "type": "uint256"},
            {"internalType": "uint256", "name": "amount1", "type": "uint256"},
        ],
        "stateMutability": "payable",
        "type": "function",
    },
    {
        "inputs": [
            {
                "components": [
                    {"internalType": "uint256", "name": "tokenId", "type": "uint256"},
                    {"internalType": "address", "name": "recipient", "type": "address"},
                    {"internalType": "uint128", "name": "amount0Max", "type": "uint128"},
                    {"internalType": "uint128", "name": "amount1Max", "type": "uint128"},
                ],
                "internalType": "struct INonfungiblePositionManager.CollectParams",
                "name": "params",
                "type": "tuple",
            }
        ],
        "name": "collect",
        "outputs": [
            {"internalType": "uint256", "name": "amount0", "type": "uint256"},
            {"internalType": "uint256", "name": "amount1", "type": "uint256"},
        ],
        "stateMutability": "payable",
        "type": "function",
    },
    {
        "inputs": [{"internalType": "uint256", "name": "tokenId", "type": "uint256"}],
        "name": "positions",
        "outputs": [
            {"internalType": "uint96", "name": "nonce", "type": "uint96"},
            {"internalType": "address", "name": "operator", "type": "address"},
            {"internalType": "address", "name": "token0", "type": "address"},
            {"internalType": "address", "name": "token1", "type": "address"},
            {"internalType": "uint24", "name": "fee", "type": "uint24"},
            {"internalType": "int24", "name": "tickLower", "type": "int24"},
            {"internalType": "int24", "name": "tickUpper", "type": "int24"},
            {"internalType": "uint128", "name": "liquidity", "type": "uint128"},
            {"internalType": "uint256", "name": "feeGrowthInside0LastX128", "type": "uint256"},
            {"internalType": "uint256", "name": "feeGrowthInside1LastX128", "type": "uint256"},
            {"internalType": "uint128", "name": "tokensOwed0", "type": "uint128"},
            {"internalType": "uint128", "name": "tokensOwed1", "type": "uint128"},
        ],
        "stateMutability": "view",
        "type": "function",
    },
    {
        "inputs": [{"internalType": "address", "name": "owner", "type": "address"}],
        "name": "balanceOf",
        "outputs": [{"internalType": "uint256", "name": "", "type": "uint256"}],
        "stateMutability": "view",
        "type": "function",
    },
    {
        "inputs": [
            {"internalType": "address", "name": "owner", "type": "address"},
            {"internalType": "uint256", "name": "index", "type": "uint256"},
        ],
        "name": "tokenOfOwnerByIndex",
        "outputs": [{"internalType": "uint256", "name": "", "type": "uint256"}],
        "stateMutability": "view",
        "type": "function",
    },
]

# Factory ABI per trovare pool
FACTORY_ABI = [
    {
        "inputs": [
            {"internalType": "address", "name": "tokenA", "type": "address"},
            {"internalType": "address", "name": "tokenB", "type": "address"},
            {"internalType": "uint24", "name": "fee", "type": "uint24"},
        ],
        "name": "getPool",
        "outputs": [{"internalType": "address", "name": "pool", "type": "address"}],
        "stateMutability": "view",
        "type": "function",
    }
]


# ==============================================================================
# Matematica Uniswap V3 (Tick / SqrtPriceX96 / Prezzo / Liquidita')
# ==============================================================================

def tick_to_sqrt_price_x96(tick: int) -> int:
    """Converte un tick in sqrtPriceX96 (Q64.96 integer)."""
    return int((1.0001 ** (tick / 2.0)) * (2 ** 96))


def sqrt_price_x96_to_tick(sqrt_price_x96: int) -> int:
    """Converte un sqrtPriceX96 nel tick corrispondente."""
    ratio = sqrt_price_x96 / (2 ** 96)
    price_raw = ratio * ratio
    if price_raw <= 0:
        return 0
    return int(math.floor(math.log(price_raw) / math.log(1.0001)))


def sqrt_price_x96_to_human_price(sqrt_price_x96: int, decimals0: int = 18, decimals1: int = 6) -> float:
    """
    Calcola il prezzo 'human readable' del token0 espresso in token1.
    Es. WETH (18) e USDC (6) -> ritorna prezzo di 1 WETH in USDC.
    """
    ratio = sqrt_price_x96 / (2 ** 96)
    price_raw = ratio * ratio
    return price_raw * (10 ** (decimals0 - decimals1))


def human_price_to_sqrt_price_x96(price: float, decimals0: int = 18, decimals1: int = 6) -> int:
    """Converte un prezzo human readable (es. 2500 USDC per 1 WETH) in sqrtPriceX96."""
    price_raw = price / (10 ** (decimals0 - decimals1))
    sqrt_p = math.sqrt(max(1e-18, price_raw))
    return int(sqrt_p * (2 ** 96))


def human_price_to_tick(price: float, decimals0: int = 18, decimals1: int = 6) -> int:
    """Converte prezzo human readable in tick."""
    price_raw = price / (10 ** (decimals0 - decimals1))
    if price_raw <= 0:
        return 0
    return int(math.floor(math.log(price_raw) / math.log(1.0001)))


def tick_to_human_price(tick: int, decimals0: int = 18, decimals1: int = 6) -> float:
    """Converte un tick nel prezzo human readable del token0 in token1."""
    price_raw = 1.0001 ** tick
    return price_raw * (10 ** (decimals0 - decimals1))


def align_tick_to_spacing(tick: int, spacing: int) -> int:
    """Allinea un tick al multiplo consentito per il fee tier specificato."""
    return int(math.floor(tick / spacing)) * spacing


def calculate_range_ticks(current_price: float, range_width_pct: float, fee: int,
                          decimals0: int = 18, decimals1: int = 6) -> Tuple[int, int, float, float]:
    """
    Calcola (tick_lower, tick_upper, price_lower, price_upper) centrati sul prezzo corrente.
    range_width_pct: es. 8.0 significa +/- 4.0% attorno al prezzo.
    """
    spacing = config.TICK_SPACINGS.get(fee, 10)
    half_w = (range_width_pct / 2.0) / 100.0

    p_lower = current_price * (1.0 - half_w)
    p_upper = current_price * (1.0 + half_w)

    raw_tick_l = human_price_to_tick(p_lower, decimals0, decimals1)
    raw_tick_u = human_price_to_tick(p_upper, decimals0, decimals1)

    tick_lower = align_tick_to_spacing(raw_tick_l, spacing)
    tick_upper = align_tick_to_spacing(raw_tick_u, spacing)

    # Assicurati che tick_lower < tick_upper di almeno uno spacing
    if tick_upper <= tick_lower:
        tick_upper = tick_lower + spacing

    actual_p_lower = tick_to_human_price(tick_lower, decimals0, decimals1)
    actual_p_upper = tick_to_human_price(tick_upper, decimals0, decimals1)

    return tick_lower, tick_upper, actual_p_lower, actual_p_upper


def get_amounts_for_liquidity(sqrt_price_x96: int, sqrt_ratio_a: int, sqrt_ratio_b: int,
                              liquidity: int) -> Tuple[int, int]:
    """
    Data la liquidita' L e i bordi di prezzo [sqrt_ratio_a, sqrt_ratio_b],
    calcola l'ammontare esatto di token0 (raw) e token1 (raw) in posizione.
    """
    if sqrt_ratio_a > sqrt_ratio_b:
        sqrt_ratio_a, sqrt_ratio_b = sqrt_ratio_b, sqrt_ratio_a

    if sqrt_price_x96 <= sqrt_ratio_a:
        # Prezzo al di sotto del range: 100% token0
        amount0 = int((liquidity * (2 ** 96) * (sqrt_ratio_b - sqrt_ratio_a)) // (sqrt_ratio_b * sqrt_ratio_a))
        amount1 = 0
    elif sqrt_price_x96 < sqrt_ratio_b:
        # Prezzo dentro il range: contiene entrambi i token
        amount0 = int((liquidity * (2 ** 96) * (sqrt_ratio_b - sqrt_price_x96)) // (sqrt_ratio_b * sqrt_price_x96))
        amount1 = int((liquidity * (sqrt_price_x96 - sqrt_ratio_a)) // (2 ** 96))
    else:
        # Prezzo al di sopra del range: 100% token1
        amount0 = 0
        amount1 = int((liquidity * (sqrt_ratio_b - sqrt_ratio_a)) // (2 ** 96))

    return amount0, amount1


def get_liquidity_for_amounts(sqrt_price_x96: int, sqrt_ratio_a: int, sqrt_ratio_b: int,
                              amount0: int, amount1: int) -> int:
    """
    Dati gli ammontari raw di token0 e token1, calcola la massima liquidita' L ottenibile.
    """
    if sqrt_ratio_a > sqrt_ratio_b:
        sqrt_ratio_a, sqrt_ratio_b = sqrt_ratio_b, sqrt_ratio_a

    if sqrt_price_x96 <= sqrt_ratio_a:
        # Prezzo al di sotto
        if sqrt_ratio_b == sqrt_ratio_a:
            return 0
        return int((amount0 * (sqrt_ratio_a * sqrt_ratio_b) // (2 ** 96)) // (sqrt_ratio_b - sqrt_ratio_a))
    elif sqrt_price_x96 < sqrt_ratio_b:
        # Prezzo dentro il range
        l0 = int((amount0 * (sqrt_price_x96 * sqrt_ratio_b) // (2 ** 96)) // (sqrt_ratio_b - sqrt_price_x96)) if (sqrt_ratio_b - sqrt_price_x96) > 0 else 0
        l1 = int((amount1 * (2 ** 96)) // (sqrt_price_x96 - sqrt_ratio_a)) if (sqrt_price_x96 - sqrt_ratio_a) > 0 else 0
        if amount0 == 0:
            return l1
        if amount1 == 0:
            return l0
        return min(l0, l1)
    else:
        # Prezzo al di sopra
        if sqrt_ratio_b == sqrt_ratio_a:
            return 0
        return int((amount1 * (2 ** 96)) // (sqrt_ratio_b - sqrt_ratio_a))


def calculate_impermanent_loss(entry_price: float, current_price: float,
                               price_lower: float, price_upper: float) -> float:
    """
    Calcola l'Impermanent Loss in percentuale rispetto a detenere gli asset (HODL).
    Ritorna un valore float in percentuale (es. -1.25%).
    """
    if entry_price <= 0 or current_price <= 0 or price_lower >= price_upper:
        return 0.0

    k = current_price / entry_price
    # Formula standard Uniswap V3 per IL nel range [P_l, P_u]
    r = math.sqrt(k)
    sqrt_l = math.sqrt(price_lower / entry_price)
    sqrt_u = math.sqrt(price_upper / entry_price)

    # Clamping se fuori range
    r_clamped = max(sqrt_l, min(sqrt_u, r))

    # Valore LP relativo
    # Se entry era dentro il range (sqrt_l < 1 < sqrt_u)
    denom = 2 * r - sqrt_l - (1.0 / sqrt_u) if (sqrt_l < 1.0 < sqrt_u) else 1.0
    if denom <= 0:
        return 0.0

    # Approssimazione geometrica precisa
    # V_lp = L * (2 * sqrt(P) - sqrt(P_l) - P / sqrt(P_u))
    # V_hold = x_0 * P + y_0
    v_lp = 2 * r_clamped - sqrt_l - (r_clamped ** 2) / sqrt_u
    v_hold = (1 - sqrt_l) * k + (1 - (1 / sqrt_u))

    if v_hold <= 0:
        return 0.0

    il = (v_lp - v_hold) / v_hold
    return round(il * 100.0, 2)


# ==============================================================================
# Classe UniswapV3Lp: interazione contrattuale on-chain
# ==============================================================================

class UniswapV3Lp:
    def __init__(self, client: BaseClient):
        self.client = client
        self.w3 = client.w3
        self.pm_address = self.w3.to_checksum_address(config.UNISWAP_V3_POSITION_MANAGER)
        self.factory_address = self.w3.to_checksum_address(config.UNISWAP_V3_FACTORY)

        self.pm_contract = self.w3.eth.contract(address=self.pm_address, abi=POSITION_MANAGER_ABI)
        self.factory_contract = self.w3.eth.contract(address=self.factory_address, abi=FACTORY_ABI)

    def get_pool_address(self, tokenA: str, tokenB: str, fee: int) -> str:
        """Interroga la Factory per l'indirizzo del pool per la coppia e fee tier."""
        addr_a = self.w3.to_checksum_address(tokenA)
        addr_b = self.w3.to_checksum_address(tokenB)
        return self.factory_contract.functions.getPool(addr_a, addr_b, int(fee)).call()

    def get_pool_state(self, pool_address: str) -> Dict[str, Any]:
        """Interroga lo stato corrente del pool (slot0, tick, liquidity, fee)."""
        pool = self.w3.eth.contract(address=self.w3.to_checksum_address(pool_address), abi=POOL_ABI)
        slot0 = pool.functions.slot0().call()
        liq = pool.functions.liquidity().call()
        t0 = pool.functions.token0().call()
        t1 = pool.functions.token1().call()
        f = pool.functions.fee().call()

        return {
            "address": pool_address,
            "token0": t0,
            "token1": t1,
            "fee": f,
            "sqrtPriceX96": slot0[0],
            "tick": slot0[1],
            "unlocked": slot0[6],
            "liquidity": liq,
        }

    def get_position_details(self, token_id: int) -> Dict[str, Any]:
        """Recupera tutti i parametri di una posizione NFT aperta dal PositionManager."""
        res = self.pm_contract.functions.positions(int(token_id)).call()
        return {
            "tokenId": token_id,
            "nonce": res[0],
            "operator": res[1],
            "token0": res[2],
            "token1": res[3],
            "fee": res[4],
            "tickLower": res[5],
            "tickUpper": res[6],
            "liquidity": res[7],
            "feeGrowthInside0LastX128": res[8],
            "feeGrowthInside1LastX128": res[9],
            "tokensOwed0": res[10],
            "tokensOwed1": res[11],
        }

    def get_user_positions(self, owner_address: str = None) -> list:
        """Restituisce la lista di tokenId posseduti dall'indirizzo."""
        owner = self.w3.to_checksum_address(owner_address or self.client.address)
        num_pos = self.pm_contract.functions.balanceOf(owner).call()
        token_ids = []
        for i in range(num_pos):
            tid = self.pm_contract.functions.tokenOfOwnerByIndex(owner, i).call()
            token_ids.append(tid)
        return token_ids

    def mint_position(self, token0: str, token1: str, fee: int,
                      tick_lower: int, tick_upper: int,
                      amount0_desired: int, amount1_desired: int,
                      slippage_bps: int = None) -> str:
        """Invia la transazione mint() per aprire una nuova posizione di liquidita' concentrata."""
        slippage_bps = config.DEFAULT_SLIPPAGE_BPS if slippage_bps is None else slippage_bps
        slip_mult = 1.0 - (slippage_bps / 10_000.0)

        amt0_min = int(amount0_desired * slip_mult)
        amt1_min = int(amount1_desired * slip_mult)
        deadline = int(time.time()) + 300

        # Assicura approvazione ERC20 al PositionManager
        self.client.approve_if_needed(token0, self.pm_address, amount0_desired)
        self.client.approve_if_needed(token1, self.pm_address, amount1_desired)

        params = (
            self.w3.to_checksum_address(token0),
            self.w3.to_checksum_address(token1),
            int(fee),
            int(tick_lower),
            int(tick_upper),
            int(amount0_desired),
            int(amount1_desired),
            amt0_min,
            amt1_min,
            self.client.address,
            deadline,
        )

        fn = self.pm_contract.functions.mint(params)
        return self.client.build_and_send(fn, value=0)

    def decrease_liquidity(self, token_id: int, liquidity: int, slippage_bps: int = None) -> str:
        """Riduce/rimuove la liquidita' da una posizione NFT."""
        deadline = int(time.time()) + 300
        params = (
            int(token_id),
            int(liquidity),
            0,  # amount0Min
            0,  # amount1Min
            deadline,
        )
        fn = self.pm_contract.functions.decreaseLiquidity(params)
        return self.client.build_and_send(fn, value=0)

    def collect_fees(self, token_id: int, recipient: str = None) -> str:
        """Raccoglie le fee maturate (e l'ammontare ritirato con decreaseLiquidity)."""
        rec = self.w3.to_checksum_address(recipient or self.client.address)
        # 2**128 - 1 per riscuotere tutto
        max_uint128 = (2 ** 128) - 1
        params = (
            int(token_id),
            rec,
            max_uint128,
            max_uint128,
        )
        fn = self.pm_contract.functions.collect(params)
        return self.client.build_and_send(fn, value=0)
