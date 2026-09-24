"""
Simulatore Paper Trading per LP attivo a liquidita' concentrata su Uniswap V3.

Simula:
  - Creazione (mint) e chiusura (burn/collect) di posizioni NFT;
  - Maturazione realistica delle commissioni di swap in base al fee tier e al fattore di concentrazione;
  - Ribilanciamento dei token per il re-centering;
  - Costi gas e slippage realistici su Base L2;
  - Tracciamento equity curve, PnL e fee accumulate.
"""

import json
import logging
import math
import os
import time
from typing import Any, Dict, List, Optional, Tuple

import config
import uniswap_v3_lp as lp_math

logger = logging.getLogger(__name__)


class PaperLpBook:
    def __init__(self, path: str = None, start_usdc: float = None, start_eth: float = None):
        self.path = path or config.PAPER_STATE_PATH
        self.start_usdc = config.PAPER_START_USDC if start_usdc is None else start_usdc
        self.start_eth = config.PAPER_START_ETH if start_eth is None else start_eth
        self.state: Dict[str, Any] = {}
        self.load()

    def load(self):
        try:
            with open(self.path, encoding="utf-8") as fh:
                self.state = json.load(fh) or {}
        except (OSError, ValueError):
            self.state = {}

        if not self.state:
            self.state = {
                "initial_usdc": float(self.start_usdc),
                "initial_eth": float(self.start_eth),
                "balances": {
                    "USDC": float(self.start_usdc),
                    "WETH": 0.0,
                    "ETH": float(self.start_eth),
                },
                "position": None,
                "history": [],
                "gas_spent_usd": 0.0,
                "total_fees_earned_usd": 0.0,
                "created_at": time.time(),
                "last_fee_update_time": time.time(),
            }
            self.save()

    def save(self):
        try:
            os.makedirs(os.path.dirname(os.path.abspath(self.path)), exist_ok=True)
            with open(self.path, "w", encoding="utf-8") as fh:
                json.dump(self.state, fh, indent=2)
        except OSError as exc:
            logger.warning("Impossibile salvare %s: %s", self.path, exc)

    @property
    def balances(self) -> Dict[str, float]:
        return self.state.setdefault("balances", {})

    @property
    def active_position(self) -> Optional[Dict[str, Any]]:
        return self.state.get("position")

    def update_accrued_fees(self, current_price: float):
        """
        Accresce le fee simulate maturate dalla posizione attiva in base al tempo
        trascorso e al fattore di concentrazione del range.
        """
        pos = self.active_position
        if not pos:
            return

        now = time.time()
        last_t = float(self.state.get("last_fee_update_time", now))
        elapsed_hours = (now - last_t) / 3600.0
        self.state["last_fee_update_time"] = now

        if elapsed_hours <= 0:
            return

        # Verifica se il prezzo e' in-range
        p_lower = float(pos["price_lower"])
        p_upper = float(pos["price_upper"])
        if not (p_lower <= current_price <= p_upper):
            self.save()
            return  # Fuori range non matura fee!

        # Fattore di concentrazione: C = 1 / (1 - sqrt(P_l / P_u))
        try:
            c_factor = 1.0 / (1.0 - math.sqrt(p_lower / p_upper))
        except ZeroDivisionError:
            c_factor = 10.0

        # Base APR di swap per il pool WETH/USDC su Base (stimato 15% - 25% su TVL concentrato)
        # Rendimento orario
        pos_val = float(pos.get("entry_value_usd", 500.0))
        # Stima conservativa APR: 20% * (c_factor / 15.0) limitato a 60% APR max
        effective_apr = min(0.60, 0.15 * (c_factor / 10.0))
        hourly_rate = effective_apr / 8760.0

        fee_earned = pos_val * hourly_rate * elapsed_hours
        pos["uncollected_fees_usd"] = float(pos.get("uncollected_fees_usd", 0.0)) + fee_earned
        self.state["total_fees_earned_usd"] = float(self.state.get("total_fees_earned_usd", 0.0)) + fee_earned
        self.save()

    def mint_position(self, current_price: float, range_width_pct: float,
                      target_capital_usd: float = None,
                      token0_sym: str = None, token1_sym: str = None,
                      fee_tier: int = None) -> Dict[str, Any]:
        """
        Apre una nuova posizione LP simulata centrata sul prezzo corrente.
        Usa i saldi disponibili.
        """
        active_cfg = config.get_active_pool_config()
        t0 = (token0_sym or active_cfg.get("token0") or config.POOL_TOKEN0_SYMBOL).upper()
        t1 = (token1_sym or active_cfg.get("token1") or config.POOL_TOKEN1_SYMBOL).upper()
        fee = int(fee_tier or active_cfg.get("fee") or config.POOL_FEE)
        dec0 = config.get_token_decimals(t0, 18)
        dec1 = config.get_token_decimals(t1, 6)

        tick_l, tick_u, p_lower, p_upper = lp_math.calculate_range_ticks(
            current_price, range_width_pct, fee, decimals0=dec0, decimals1=dec1
        )

        usdc_avail = float(self.balances.get("USDC", 0.0))
        weth_avail = float(self.balances.get("WETH", 0.0))
        t0_avail = float(self.balances.get(t0, 0.0))

        tot_usd = usdc_avail + (weth_avail * (current_price if t0 == "WETH" or t1 == "WETH" else 2500.0))
        if t1 == "USDC":
            tot_usd = max(tot_usd, usdc_avail + (t0_avail * current_price))
        cap_usd = min(tot_usd * 0.85, target_capital_usd or tot_usd * 0.85)

        if cap_usd < 20.0:
            return {"status": "rejected", "reason": f"Capitale insufficiente (${cap_usd:.2f})"}

        half_cap = cap_usd / 2.0
        needed_amt0 = (half_cap / current_price) if current_price > 0 else 0.0
        needed_amt1 = half_cap

        # Aggiusta saldi
        if t1 in self.balances and self.balances[t1] >= needed_amt1:
            self.balances[t1] = max(0.0, self.balances[t1] - needed_amt1)
        else:
            self.balances["USDC"] = max(0.0, self.balances.get("USDC", 0.0) - needed_amt1)

        if t0 in self.balances and self.balances[t0] >= needed_amt0:
            self.balances[t0] = max(0.0, self.balances[t0] - needed_amt0)
        else:
            self.balances["USDC"] = max(0.0, self.balances.get("USDC", 0.0) - half_cap)

        gas = config.PAPER_GAS_USD * 2.0
        self.state["gas_spent_usd"] = float(self.state.get("gas_spent_usd", 0.0)) + gas

        # Calcolo liquidità L
        sqrt_curr = lp_math.human_price_to_sqrt_price_x96(current_price, decimals0=dec0, decimals1=dec1)
        sqrt_a = lp_math.tick_to_sqrt_price_x96(tick_l)
        sqrt_b = lp_math.tick_to_sqrt_price_x96(tick_u)

        raw0 = int(needed_amt0 * (10 ** dec0))
        raw1 = int(needed_amt1 * (10 ** dec1))
        liq = lp_math.get_liquidity_for_amounts(sqrt_curr, sqrt_a, sqrt_b, raw0, raw1)

        pos_id = int(time.time() * 1000) % 1_000_000

        pos = {
            "token_id": pos_id,
            "token0": t0,
            "token1": t1,
            "decimals0": dec0,
            "decimals1": dec1,
            "fee": fee,
            "tick_lower": tick_l,
            "tick_upper": tick_u,
            "price_lower": p_lower,
            "price_upper": p_upper,
            "liquidity": liq,
            "entry_price": current_price,
            "entry_time": time.time(),
            "entry_amount0": needed_amt0,
            "entry_amount1": needed_amt1,
            "entry_value_usd": cap_usd,
            "uncollected_fees_usd": 0.0,
            "fees_collected_usd": 0.0,
        }

        self.state["position"] = pos
        self.state["last_fee_update_time"] = time.time()
        self.save()

        return {"status": "success", "operation": "mint", "position": pos}

    def close_and_recenter(self, current_price: float, new_range_width_pct: float) -> Dict[str, Any]:
        """
        Raccoglie fee, ritira la liquidita' della posizione esistente e apre
        un nuovo range centrato sul prezzo corrente.
        """
        pos = self.active_position
        if not pos:
            return self.mint_position(current_price, new_range_width_pct)

        # 1. Aggiorna e raccoglie le fee
        self.update_accrued_fees(current_price)
        fees_usd = float(pos.get("uncollected_fees_usd", 0.0))
        fees_collected = float(pos.get("fees_collected_usd", 0.0)) + fees_usd

        # 2. Calcola i token restituiti dalla chiusura
        t0 = pos.get("token0", "WETH")
        t1 = pos.get("token1", "USDC")
        dec0 = int(pos.get("decimals0") or config.get_token_decimals(t0, 18))
        dec1 = int(pos.get("decimals1") or config.get_token_decimals(t1, 6))

        tick_l = int(pos["tick_lower"])
        tick_u = int(pos["tick_upper"])
        liq = int(pos.get("liquidity", 0))

        sqrt_curr = lp_math.human_price_to_sqrt_price_x96(current_price, decimals0=dec0, decimals1=dec1)
        sqrt_a = lp_math.tick_to_sqrt_price_x96(tick_l)
        sqrt_b = lp_math.tick_to_sqrt_price_x96(tick_u)

        raw0, raw1 = lp_math.get_amounts_for_liquidity(sqrt_curr, sqrt_a, sqrt_b, liq)
        ret0 = raw0 / (10 ** dec0)
        ret1 = raw1 / (10 ** dec1)

        # Aggiungi le fee riscosse
        ret1 += fees_usd

        # Riaccredita sul saldo virtuale
        self.balances[t0] = float(self.balances.get(t0, 0.0)) + ret0
        self.balances[t1] = float(self.balances.get(t1, 0.0)) + ret1

        # Costo gas per decrease + collect + swap + mint
        gas = config.PAPER_GAS_USD * 4.0
        self.state["gas_spent_usd"] = float(self.state.get("gas_spent_usd", 0.0)) + gas

        # Archivia vecchia posizione
        pos["closed_at"] = time.time()
        pos["close_price"] = current_price
        pos["returned_token0"] = ret0
        pos["returned_token1"] = ret1
        pos["fees_collected_usd"] = fees_collected
        pos["uncollected_fees_usd"] = 0.0
        self.state.setdefault("history", []).append(pos)
        self.state["position"] = None
        self.save()

        # 3. Apre la nuova posizione centrata
        new_mint = self.mint_position(current_price, new_range_width_pct, token0_sym=t0, token1_sym=t1, fee_tier=pos.get("fee"))
        return {
            "status": "success",
            "operation": "recenter",
            "closed_position": pos,
            "new_position": new_mint.get("position"),
            "fees_collected_usd": fees_usd,
        }

    def collect_fees_only(self, current_price: float) -> Dict[str, Any]:
        """Raccoglie solo le commissioni accumulate senza chiudere il range."""
        self.update_accrued_fees(current_price)
        pos = self.active_position
        if not pos:
            return {"status": "noop", "reason": "Nessuna posizione aperta"}

        uncollected = float(pos.get("uncollected_fees_usd", 0.0))
        if uncollected < config.MIN_FEE_COLLECT_USD:
            return {"status": "noop", "reason": f"Fee accumulate (${uncollected:.2f}) sotto la soglia minima (${config.MIN_FEE_COLLECT_USD})"}

        pos["fees_collected_usd"] = float(pos.get("fees_collected_usd", 0.0)) + uncollected
        pos["uncollected_fees_usd"] = 0.0
        self.balances["USDC"] = float(self.balances.get("USDC", 0.0)) + uncollected

        gas = config.PAPER_GAS_USD
        self.state["gas_spent_usd"] = float(self.state.get("gas_spent_usd", 0.0)) + gas
        self.save()

        return {
            "status": "success",
            "operation": "collect_fees",
            "amount_usd": uncollected,
        }

    def summary(self, current_price: float) -> Dict[str, Any]:
        """Sintesi dello stato del Paper book con calcolo dell'equity complessiva."""
        self.update_accrued_fees(current_price)
        weth_bal = float(self.balances.get("WETH", 0.0))
        usdc_bal = float(self.balances.get("USDC", 0.0))
        eth_bal = float(self.balances.get("ETH", 0.0))

        wallet_usd = usdc_bal + (weth_bal * current_price) + (eth_bal * current_price)

        pos_val = 0.0
        fees_uncoll = 0.0
        pos = self.active_position
        if pos:
            t0 = pos.get("token0", "WETH")
            t1 = pos.get("token1", "USDC")
            dec0 = int(pos.get("decimals0") or config.get_token_decimals(t0, 18))
            dec1 = int(pos.get("decimals1") or config.get_token_decimals(t1, 6))

            tick_l = int(pos["tick_lower"])
            tick_u = int(pos["tick_upper"])
            liq = int(pos.get("liquidity", 0))
            sqrt_curr = lp_math.human_price_to_sqrt_price_x96(current_price, decimals0=dec0, decimals1=dec1)
            sqrt_a = lp_math.tick_to_sqrt_price_x96(tick_l)
            sqrt_b = lp_math.tick_to_sqrt_price_x96(tick_u)
            raw0, raw1 = lp_math.get_amounts_for_liquidity(sqrt_curr, sqrt_a, sqrt_b, liq)
            pos_val = ((raw0 / (10 ** dec0)) * current_price) + (raw1 / (10 ** dec1))
            fees_uncoll = float(pos.get("uncollected_fees_usd", 0.0))

        total_equity = wallet_usd + pos_val + fees_uncoll
        init_equity = float(self.state.get("initial_usdc", 1000.0)) + (float(self.state.get("initial_eth", 0.5)) * current_price)
        total_pnl_usd = total_equity - init_equity
        total_pnl_pct = (total_pnl_usd / init_equity * 100.0) if init_equity > 0 else 0.0

        return {
            "total_equity_usd": round(total_equity, 2),
            "wallet_value_usd": round(wallet_usd, 2),
            "position_value_usd": round(pos_val, 2),
            "balances": dict(self.balances),
            "total_fees_earned_usd": round(float(self.state.get("total_fees_earned_usd", 0.0)), 2),
            "gas_spent_usd": round(float(self.state.get("gas_spent_usd", 0.0)), 4),
            "total_pnl_usd": round(total_pnl_usd, 2),
            "total_pnl_pct": round(total_pnl_pct, 2),
        }
