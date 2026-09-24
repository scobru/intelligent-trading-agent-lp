"""
Tracciamento e valutazione analitica delle posizioni LP attive.

Gestisce:
  - Stato della posizione aperta (token_id, tickLower, tickUpper, entry price/value);
  - Controllo in-range / out-of-range con buffer di prossimita';
  - Calcolo dell'Impermanent Loss (IL) vs HODL;
  - Calcolo del valore corrente, delle commissioni maturate e del PnL netto;
  - Verifica dei criteri di riposizionamento (re-center).
"""

import json
import logging
import os
import time
from typing import Any, Dict, Optional

import config
import uniswap_v3_lp as lp_math

logger = logging.getLogger(__name__)


class PositionTracker:
    def __init__(self, state_path: str = None):
        self.state_path = state_path or config.POSITION_STATE_PATH
        self.state: Dict[str, Any] = {}
        self.load()

    def load(self):
        try:
            with open(self.state_path, encoding="utf-8") as fh:
                self.state = json.load(fh) or {}
        except (OSError, ValueError):
            self.state = {}

        if not self.state:
            self.state = {
                "active_position": None,
                "history": [],
                "created_at": time.time(),
                "total_fees_collected_usd": 0.0,
                "total_recenters": 0,
                "last_recenter_time": 0.0,
            }
            self.save()

    def save(self):
        try:
            os.makedirs(os.path.dirname(os.path.abspath(self.state_path)), exist_ok=True)
            with open(self.state_path, "w", encoding="utf-8") as fh:
                json.dump(self.state, fh, indent=2)
        except OSError as exc:
            logger.warning("Impossibile salvare %s: %s", self.state_path, exc)

    @property
    def active_position(self) -> Optional[Dict[str, Any]]:
        return self.state.get("active_position")

    def set_active_position(self, pos_data: Dict[str, Any]):
        self.state["active_position"] = pos_data
        self.save()

    def record_recenter(self, old_pos: Dict[str, Any], new_pos: Dict[str, Any]):
        self.state["total_recenters"] = int(self.state.get("total_recenters", 0)) + 1
        self.state["last_recenter_time"] = time.time()
        self.state.setdefault("history", []).append({
            "closed_at": time.time(),
            "position": old_pos,
        })
        self.state["active_position"] = new_pos
        self.save()

    def record_fee_collected(self, fee_usd: float):
        self.state["total_fees_collected_usd"] = round(
            float(self.state.get("total_fees_collected_usd", 0.0)) + float(fee_usd), 4
        )
        if self.state.get("active_position"):
            pos = self.state["active_position"]
            pos["fees_collected_usd"] = round(
                float(pos.get("fees_collected_usd", 0.0)) + float(fee_usd), 4
            )
        self.save()

    def evaluate(self, current_price: float, current_tick: int, prices: Dict[str, float]) -> Dict[str, Any]:
        """
        Valuta lo stato della posizione attiva rispetto al prezzo corrente e alla volatilità.
        """
        pos = self.active_position
        if not pos:
            return {
                "has_position": False,
                "is_in_range": False,
                "needs_recenter": True,  # Se non c'è posizione, deve aprirne una
                "reason": "Nessuna posizione LP attiva",
                "total_recenters": self.state.get("total_recenters", 0),
                "total_fees_collected_usd": self.state.get("total_fees_collected_usd", 0.0),
            }

        price_l = float(pos["price_lower"])
        price_u = float(pos["price_upper"])
        tick_l = int(pos["tick_lower"])
        tick_u = int(pos["tick_upper"])
        entry_price = float(pos.get("entry_price", current_price))
        entry_val = float(pos.get("entry_value_usd", 0.0))

        # Verifica In-Range
        buffer_p = (config.OUT_OF_RANGE_BUFFER_PCT / 100.0)
        p_l_buffered = price_l * (1.0 + buffer_p)
        p_u_buffered = price_u * (1.0 - buffer_p)

        is_strictly_in_range = (price_l <= current_price <= price_u)
        is_safe_in_range = (p_l_buffered <= current_price <= p_u_buffered)

        # Distanze percentuali dai bordi
        dist_to_lower_pct = ((current_price - price_l) / current_price) * 100.0 if current_price > 0 else 0.0
        dist_to_upper_pct = ((price_u - current_price) / current_price) * 100.0 if current_price > 0 else 0.0

        # Posizionamento relativo all'interno del range (0% = bordo inferiore, 100% = bordo superiore)
        range_span = price_u - price_l
        if range_span > 0:
            range_progress_pct = max(0.0, min(100.0, ((current_price - price_l) / range_span) * 100.0))
        else:
            range_progress_pct = 50.0

        # Calcolo quote attuali dei due token
        liquidity = int(pos.get("liquidity", 0))
        sqrt_curr = lp_math.human_price_to_sqrt_price_x96(current_price)
        sqrt_a = lp_math.tick_to_sqrt_price_x96(tick_l)
        sqrt_b = lp_math.tick_to_sqrt_price_x96(tick_u)

        amt0_raw, amt1_raw = lp_math.get_amounts_for_liquidity(sqrt_curr, sqrt_a, sqrt_b, liquidity)
        amt0 = amt0_raw / 1e18  # WETH
        amt1 = amt1_raw / 1e6   # USDC

        curr_lp_value = (amt0 * current_price) + amt1

        # Valore HODL se avessimo mantenuto i token iniziali
        init_amt0 = float(pos.get("entry_amount0", amt0))
        init_amt1 = float(pos.get("entry_amount1", amt1))
        hodl_value = (init_amt0 * current_price) + init_amt1

        # Impermanent Loss
        il_usd = curr_lp_value - hodl_value
        il_pct = (il_usd / hodl_value * 100.0) if hodl_value > 0 else 0.0

        # Commissioni e PnL netto
        fees_usd = float(pos.get("fees_collected_usd", 0.0)) + float(pos.get("uncollected_fees_usd", 0.0))
        net_pnl_usd = (curr_lp_value + fees_usd) - entry_val if entry_val > 0 else (curr_lp_value + fees_usd - hodl_value)
        net_pnl_pct = (net_pnl_usd / entry_val * 100.0) if entry_val > 0 else 0.0

        # Verifica tempo minimo di hold
        now = time.time()
        pos_age_hours = (now - float(pos.get("entry_time", now))) / 3600.0
        hold_time_met = pos_age_hours >= config.MIN_HOLD_HOURS_BEFORE_RECENTER

        # Criterio di riposizionamento: fuori range (o sul bordo) e tempo minimo trascorso
        needs_recenter = (not is_safe_in_range) and hold_time_met

        return {
            "has_position": True,
            "token_id": pos.get("token_id"),
            "token0": pos.get("token0", "WETH"),
            "token1": pos.get("token1", "USDC"),
            "fee_tier": pos.get("fee", config.POOL_FEE),
            "tick_lower": tick_l,
            "tick_upper": tick_u,
            "price_lower": round(price_l, 2),
            "price_upper": round(price_u, 2),
            "current_price": round(current_price, 2),
            "entry_price": round(entry_price, 2),
            "entry_value_usd": round(entry_val, 2),
            "current_lp_value_usd": round(curr_lp_value, 2),
            "hodl_value_usd": round(hodl_value, 2),
            "impermanent_loss_usd": round(il_usd, 2),
            "impermanent_loss_pct": round(il_pct, 2),
            "amount0": round(amt0, 6),
            "amount1": round(amt1, 2),
            "fees_collected_usd": round(fees_usd, 2),
            "net_pnl_usd": round(net_pnl_usd, 2),
            "net_pnl_pct": round(net_pnl_pct, 2),
            "is_strictly_in_range": is_strictly_in_range,
            "is_safe_in_range": is_safe_in_range,
            "range_progress_pct": round(range_progress_pct, 1),
            "dist_to_lower_pct": round(dist_to_lower_pct, 2),
            "dist_to_upper_pct": round(dist_to_upper_pct, 2),
            "position_age_hours": round(pos_age_hours, 1),
            "needs_recenter": needs_recenter,
            "hold_time_met": hold_time_met,
            "total_recenters": self.state.get("total_recenters", 0),
            "total_fees_collected_usd": self.state.get("total_fees_collected_usd", 0.0),
        }
