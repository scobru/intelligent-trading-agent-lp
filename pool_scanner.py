"""
Scanner e analizzatore delle pool Uniswap V3 su Base Chain.

Interroga DefiLlama Yields API per scoprire in tempo reale le migliori opportunità
di rendimento da fee sulla liquidità concentrata, valutando:
  - Fee APY istantaneo e media mobile a 30 giorni (anti-spike);
  - Rapporto Volume 24h / TVL (efficienza del capitale);
  - Livello di rischio Impermanent Loss (Stable/Correlated, Bluechip, High Volatility);
  - Filtraggio di pool illiquide (< $50k TVL) o prive di volume.
"""

import logging
import math
import re
import time
from typing import Any, Dict, List, Optional

import config
import http_client

logger = logging.getLogger(__name__)

DEFILLAMA_POOLS_URL = "https://yields.llama.fi/pools"
CACHE_TTL_SECONDS = 300.0  # Cache 5 minuti per rispettare i rate limit

# Normalizzazione Fee Tier
FEE_MAP = {
    "0.01%": 100,
    "0.05%": 500,
    "0.3%": 3000,
    "0.30%": 3000,
    "1%": 10000,
    "1.0%": 10000,
    "1.00%": 10000,
}

# Coppie e asset con basso rischio IL (stabili o LST correlati a ETH)
CORRELATED_SYMBOLS = {
    "WSTETH", "CBETH", "RETH", "WEETH", "MSETH", "EZETH",
    "USDC", "USDBC", "EURC", "CUSD", "DAI", "LUSD"
}

BLUECHIP_SYMBOLS = {
    "WETH", "ETH", "USDC", "CBBTC", "TBTC", "WBTC", "CBETH", "WSTETH"
}


def parse_fee_tier(meta_str: Optional[str]) -> int:
    if not meta_str:
        return 500
    cleaned = meta_str.strip().replace(" ", "")
    return FEE_MAP.get(cleaned, 500)


def classify_risk(sym0: str, sym1: str) -> str:
    s0 = sym0.upper()
    s1 = sym1.upper()

    # Correlati / Stabili
    if (s0 in CORRELATED_SYMBOLS and s1 in CORRELATED_SYMBOLS) or \
       ("ETH" in s0 and "ETH" in s1) or \
       ("USD" in s0 and "USD" in s1):
        return "Basso"

    # Bluechip
    if s0 in BLUECHIP_SYMBOLS and s1 in BLUECHIP_SYMBOLS:
        return "Medio"

    # Altcoin / Volatile
    return "Alto"


def calculate_pool_score(pool: Dict[str, Any]) -> float:
    """
    Calcola un punteggio bilanciato tra APY da commissioni, media a 30 giorni e profondità di TVL.
    """
    apy = float(pool.get("apyBase") or pool.get("apy") or 0.0)
    mean30 = float(pool.get("apyMean30d") or apy)

    # Smorza picchi anomali di un singolo giorno
    if mean30 > 0:
        effective_apy = min(apy, mean30 * 2.0)
    else:
        effective_apy = apy

    tvl = max(float(pool.get("tvlUsd") or 1.0), 1.0)
    vol = max(float(pool.get("volumeUsd1d") or 0.0), 0.0)

    # Turnover / efficienza del capitale (Volume / TVL)
    efficiency = vol / tvl if tvl > 0 else 0.0

    # Ponderazione con profondità TVL (logaritmica)
    depth_multiplier = min(1.0, math.log10(tvl) / 7.0) if tvl >= 10 else 0.1

    return round(effective_apy * 0.7 + (efficiency * 100.0) * 0.3 * depth_multiplier, 2)


class PoolScanner:
    def __init__(self):
        self._cache_time = 0.0
        self._cached_result: Optional[Dict[str, Any]] = None

    def scan(self, force_refresh: bool = False, min_tvl: float = 50000.0) -> Dict[str, Any]:
        now = time.time()
        if not force_refresh and self._cached_result and (now - self._cache_time < CACHE_TTL_SECONDS):
            return self._cached_result

        try:
            raw_data = http_client.get_json(DEFILLAMA_POOLS_URL, cache_ttl=int(CACHE_TTL_SECONDS))
            pools = (raw_data or {}).get("data", [])
        except Exception as exc:
            logger.error("Errore recupero pool da DefiLlama: %s", exc)
            if self._cached_result:
                return self._cached_result
            return {"top": [], "bluechips": [], "high_yield": [], "total_scanned": 0, "scanned_at": now}

        # Filtra solo Uniswap V3 su Base
        base_uniswap = [
            p for p in pools
            if str(p.get("chain", "")).lower() == "base"
            and "uniswap" in str(p.get("project", "")).lower()
            and float(p.get("tvlUsd") or 0.0) >= min_tvl
        ]

        normalized: List[Dict[str, Any]] = []

        for p in base_uniswap:
            sym = str(p.get("symbol", "")).strip()
            parts = sym.split("-")
            if len(parts) < 2:
                continue

            token0_sym = parts[0].upper()
            token1_sym = parts[1].upper()
            fee_tier = parse_fee_tier(p.get("poolMeta"))

            tvl = float(p.get("tvlUsd") or 0.0)
            vol1d = float(p.get("volumeUsd1d") or 0.0)
            vol7d = float(p.get("volumeUsd7d") or 0.0)
            apy_base = float(p.get("apyBase") or 0.0)
            apy_mean = float(p.get("apyMean30d") or apy_base)
            efficiency = (vol1d / tvl) if tvl > 0 else 0.0

            risk = classify_risk(token0_sym, token1_sym)
            score = calculate_pool_score(p)

            tokens = p.get("underlyingTokens") or []
            t0_addr = tokens[0] if len(tokens) > 0 else ""
            t1_addr = tokens[1] if len(tokens) > 1 else ""

            normalized.append({
                "id": p.get("pool"),
                "symbol": f"{token0_sym}/{token1_sym}",
                "token0": token0_sym,
                "token1": token1_sym,
                "token0_address": t0_addr,
                "token1_address": t1_addr,
                "fee_tier": fee_tier,
                "fee_tier_pct": f"{fee_tier / 10000:.2f}%",
                "tvl_usd": tvl,
                "volume_24h_usd": vol1d,
                "volume_7d_usd": vol7d,
                "apy_base": round(apy_base, 2),
                "apy_mean_30d": round(apy_mean, 2),
                "efficiency": round(efficiency, 3),
                "risk_level": risk,
                "score": score,
            })

        # Ordinamenti e categorizzazioni
        sorted_by_score = sorted(normalized, key=lambda x: x["score"], reverse=True)

        bluechips = [
            p for p in sorted_by_score
            if p["risk_level"] in ("Basso", "Medio") and p["tvl_usd"] >= 100000.0
        ]

        high_yield = [
            p for p in sorted_by_score
            if p["apy_base"] >= 25.0 and p["volume_24h_usd"] >= 10000.0
        ]

        result = {
            "top": sorted_by_score[:25],
            "bluechips": bluechips[:20],
            "high_yield": high_yield[:20],
            "total_scanned": len(normalized),
            "scanned_at": now,
        }

        self._cached_result = result
        self._cache_time = now
        return result


# Singleton
scanner = PoolScanner()
