"""
Gestore e coordinatore strategico delle operazioni LP a Liquidita' Concentrata.

Esegue:
  - Recupero del prezzo e del tick corrente dal pool Uniswap V3 (WETH/USDC);
  - Monitoraggio costante della posizione (in-range, out-of-range, impermanent loss);
  - Pianificazione ed esecuzione delle operazioni (mint iniziale, re-centering, fee collection);
  - Supporto trasparente per modalita' Paper, Dry-run e Live on-chain.
"""

import logging
import time
from typing import Any, Dict, Optional, Tuple

import config
import uniswap_v3_lp as lp_math
from base_client import BaseChainError, BaseClient
from paper import PaperLpBook
from positions import PositionTracker
from uniswap import UniswapV3
from uniswap_v3_lp import UniswapV3Lp

logger = logging.getLogger(__name__)


class LpManager:
    def __init__(self, client: BaseClient):
        self.client = client
        self.uniswap = UniswapV3(client)
        self.v3_lp = UniswapV3Lp(client)
        self.tracker = PositionTracker()
        self.paper = PaperLpBook() if config.PAPER_TRADING else None
        self._pool_address: Optional[str] = None
        self._cached_price: Optional[float] = None
        self._last_price_time: float = 0.0

    # ------------------------------------------------------------ pool & prezzo
    def get_pool_address(self) -> str:
        if self._pool_address is None:
            token0 = config.KNOWN_ASSETS[config.POOL_TOKEN0_SYMBOL]["address"]
            token1 = config.KNOWN_ASSETS[config.POOL_TOKEN1_SYMBOL]["address"]
            try:
                self._pool_address = self.v3_lp.get_pool_address(token0, token1, config.POOL_FEE)
            except Exception as exc:
                logger.warning("Impossibile recuperare pool address da Factory: %s", exc)
                self._pool_address = "0xd4405F0704621DBe9d4dEA60E128E0C3b26bddbD"  # Fallback noto pool WETH/USDC 0.05%
        return self._pool_address

    def get_current_price_and_tick(self) -> Tuple[float, int]:
        """
        Recupera il prezzo human readable (USDC per WETH) e il tick corrente.
        Usa cache di 15 secondi per ottimizzare le chiamate RPC.
        """
        now = time.time()
        if self._cached_price and (now - self._last_price_time < 15.0):
            return self._cached_price, self.last_tick

        try:
            pool_addr = self.get_pool_address()
            state = self.v3_lp.get_pool_state(pool_addr)
            sqrt_p = state["sqrtPriceX96"]
            tick = state["tick"]
            price = lp_math.sqrt_price_x96_to_human_price(sqrt_p, decimals0=18, decimals1=6)
            self._cached_price = price
            self.last_tick = tick
            self._last_price_time = now
            return price, tick
        except Exception as exc:
            logger.warning("Errore lettura slot0 dal pool: %s. Tento tramite Uniswap Quoter...", exc)
            try:
                price = self.uniswap.price_in_quote(config.WETH, config.USDC, probe_units=0.01)
                if price:
                    tick = lp_math.human_price_to_tick(price, 18, 6)
                    self._cached_price = price
                    self.last_tick = tick
                    self._last_price_time = now
                    return price, tick
            except Exception as exc2:
                logger.warning("Anche quoter fallito: %s", exc2)

        # Fallback se offline/mock
        fallback_price = self._cached_price or 2500.0
        fallback_tick = lp_math.human_price_to_tick(fallback_price, 18, 6)
        return fallback_price, fallback_tick

    # ------------------------------------------------------------ stato complessivo
    def get_status(self) -> Dict[str, Any]:
        price, tick = self.get_current_price_and_tick()
        prices = {"USDC": 1.0, "WETH": price, "ETH": price}

        if self.paper:
            # Sincronizza lo stato della posizione attiva nel tracker
            paper_pos = self.paper.active_position
            if paper_pos:
                self.tracker.set_active_position(paper_pos)
            pos_eval = self.tracker.evaluate(price, tick, prices)
            paper_summary = self.paper.summary(price)
            mode = "paper"
            balances = dict(self.paper.balances)
        else:
            pos_eval = self.tracker.evaluate(price, tick, prices)
            mode = "dry_run" if config.DRY_RUN else "live"
            balances = {
                "USDC": self.client.balance_of_float(config.USDC) if self.client.address else 0.0,
                "WETH": self.client.balance_of_float(config.WETH) if self.client.address else 0.0,
                "ETH": self.client.eth_balance() if self.client.address else 0.0,
            }
            paper_summary = None

        # Condizioni operative
        needs_mint = not pos_eval.get("has_position")
        needs_recenter = pos_eval.get("needs_recenter", False)
        uncoll_fees = float(pos_eval.get("fees_collected_usd", 0.0))
        needs_collect = config.AUTO_COLLECT_FEES and (uncoll_fees >= config.MIN_FEE_COLLECT_USD)

        status = {
            "mode": mode,
            "wallet": self.client.address if self.client else "",
            "pool": {
                "address": self.get_pool_address(),
                "pair": f"{config.POOL_TOKEN0_SYMBOL}/{config.POOL_TOKEN1_SYMBOL}",
                "fee_tier": config.POOL_FEE,
            },
            "current_price": price,
            "current_tick": tick,
            "position": pos_eval,
            "balances": balances,
            "needs_mint": needs_mint,
            "needs_recenter": needs_recenter,
            "needs_collect": needs_collect,
            "range_width_pct": config.RANGE_WIDTH_PCT,
            "total_recenters": self.tracker.state.get("total_recenters", 0),
            "total_fees_collected_usd": self.tracker.state.get("total_fees_collected_usd", 0.0),
        }

        if paper_summary:
            status["paper"] = paper_summary

        return status

    # ------------------------------------------------------------ pianificazione
    def plan_action(self, status: Dict[str, Any]) -> Dict[str, Any]:
        price = status["current_price"]
        pos = status.get("position", {})

        if status.get("needs_mint"):
            return {
                "operation": "mint",
                "price": price,
                "range_width_pct": config.RANGE_WIDTH_PCT,
                "reason": f"Apertura prima posizione LP: range +/- {config.RANGE_WIDTH_PCT / 2:.1f}% centrato su ${price:.2f}",
            }

        if status.get("needs_recenter"):
            p_l = pos.get("price_lower")
            p_u = pos.get("price_upper")
            return {
                "operation": "recenter",
                "price": price,
                "old_range": f"${p_l:.2f} - ${p_u:.2f}",
                "new_range_width_pct": config.RANGE_WIDTH_PCT,
                "reason": (
                    f"Prezzo (${price:.2f}) uscito dal range [${p_l:.2f} - ${p_u:.2f}]. "
                    f"Ribilanciamento e riposizionamento LP centrato su ${price:.2f}."
                ),
            }

        if status.get("needs_collect"):
            return {
                "operation": "collect_fees",
                "price": price,
                "amount_usd": pos.get("fees_collected_usd", 0.0),
                "reason": f"Riscossione commissioni accumulate (${pos.get('fees_collected_usd', 0.0):.2f})",
            }

        return {
            "operation": "hold",
            "price": price,
            "reason": (
                f"Posizione in-range (${pos.get('price_lower'):.2f} <= ${price:.2f} <= ${pos.get('price_upper'):.2f}) - "
                f"Progresso {pos.get('range_progress_pct', 50.0):.1f}%, accumulo commissioni attivo."
            ),
        }

    # ------------------------------------------------------------ esecuzione
    def execute_action(self, action: Dict[str, Any], status: Dict[str, Any]) -> Dict[str, Any]:
        op = action.get("operation", "hold")
        if op == "hold":
            return {"status": "hold", "operation": "hold", "reason": action.get("reason", "")}

        if op == "mint":
            return self._execute_mint(action, status)
        elif op == "recenter":
            return self._execute_recenter(action, status)
        elif op == "collect_fees":
            return self._execute_collect(action, status)

        return {"status": "rejected", "operation": op, "reason": f"Operazione '{op}' non supportata"}

    def _execute_mint(self, action: Dict[str, Any], status: Dict[str, Any]) -> Dict[str, Any]:
        price = status["current_price"]
        w = action.get("range_width_pct", config.RANGE_WIDTH_PCT)

        if self.paper:
            res = self.paper.mint_position(price, w)
            if res.get("status") == "success":
                self.tracker.set_active_position(res["position"])
            return res

        if config.DRY_RUN:
            tick_l, tick_u, p_l, p_u = lp_math.calculate_range_ticks(price, w, config.POOL_FEE)
            plan = f"mint Uniswap V3 LP NFT: range [${p_l:.2f} - ${p_u:.2f}], tick [{tick_l} - {tick_u}]"
            sim_pos = {
                "token_id": 999999,
                "token0": config.POOL_TOKEN0_SYMBOL,
                "token1": config.POOL_TOKEN1_SYMBOL,
                "fee": config.POOL_FEE,
                "tick_lower": tick_l,
                "tick_upper": tick_u,
                "price_lower": p_l,
                "price_upper": p_u,
                "liquidity": 1000000,
                "entry_price": price,
                "entry_time": time.time(),
                "entry_value_usd": 500.0,
                "fees_collected_usd": 0.0,
            }
            self.tracker.set_active_position(sim_pos)
            return {"status": "dry_run", "operation": "mint", "plan": plan, "position": sim_pos}

        # Live mint on-chain
        token0 = config.KNOWN_ASSETS[config.POOL_TOKEN0_SYMBOL]["address"]
        token1 = config.KNOWN_ASSETS[config.POOL_TOKEN1_SYMBOL]["address"]
        tick_l, tick_u, p_l, p_u = lp_math.calculate_range_ticks(price, w, config.POOL_FEE)

        # Calcola ammontari desiderati (es. 50% del saldo USDC disponibile)
        usdc_bal = self.client.balance_of_float(config.USDC)
        amt1_desired = int(min(usdc_bal * 0.8, 1000.0) * 1e6)
        amt0_desired = int(((amt1_desired / 1e6) / price) * 1e18)

        tx_hash = self.v3_lp.mint_position(
            token0=token0,
            token1=token1,
            fee=config.POOL_FEE,
            tick_lower=tick_l,
            tick_upper=tick_u,
            amount0_desired=amt0_desired,
            amount1_desired=amt1_desired,
        )
        return {
            "status": "success",
            "operation": "mint",
            "tx_hash": tx_hash,
            "range": [p_l, p_u],
        }

    def _execute_recenter(self, action: Dict[str, Any], status: Dict[str, Any]) -> Dict[str, Any]:
        price = status["current_price"]
        w = action.get("new_range_width_pct", config.RANGE_WIDTH_PCT)
        old_pos = self.tracker.active_position or {}

        if self.paper:
            res = self.paper.close_and_recenter(price, w)
            if res.get("status") == "success":
                self.tracker.record_recenter(old_pos, res["new_position"])
            return res

        if config.DRY_RUN:
            tick_l, tick_u, p_l, p_u = lp_math.calculate_range_ticks(price, w, config.POOL_FEE)
            plan = f"decreaseLiquidity + collect old NFT -> swap balances -> mint new NFT [${p_l:.2f} - ${p_u:.2f}]"
            new_pos = dict(old_pos, price_lower=p_l, price_upper=p_u, tick_lower=tick_l, tick_upper=tick_u, entry_price=price)
            self.tracker.record_recenter(old_pos, new_pos)
            return {"status": "dry_run", "operation": "recenter", "plan": plan, "new_position": new_pos}

        # Live on-chain recenter
        tid = old_pos.get("token_id")
        if not tid:
            return {"status": "error", "reason": "Nessun tokenId registrato per la posizione attiva"}

        # 1. Decrease liquidity a 0
        pos_details = self.v3_lp.get_position_details(tid)
        liq = pos_details["liquidity"]
        tx_dec = self.v3_lp.decrease_liquidity(tid, liq)
        # 2. Collect tutto
        tx_col = self.v3_lp.collect_fees(tid)

        # 3. Mint nuova posizione
        mint_res = self._execute_mint(action, status)

        return {
            "status": "success",
            "operation": "recenter",
            "decrease_tx": tx_dec,
            "collect_tx": tx_col,
            "mint_result": mint_res,
        }

    def _execute_collect(self, action: Dict[str, Any], status: Dict[str, Any]) -> Dict[str, Any]:
        price = status["current_price"]
        if self.paper:
            res = self.paper.collect_fees_only(price)
            if res.get("status") == "success":
                self.tracker.record_fee_collected(res.get("amount_usd", 0.0))
            return res

        if config.DRY_RUN:
            amt = action.get("amount_usd", 0.0)
            self.tracker.record_fee_collected(amt)
            return {"status": "dry_run", "operation": "collect_fees", "amount_usd": amt}

        pos = self.tracker.active_position or {}
        tid = pos.get("token_id")
        if not tid:
            return {"status": "error", "reason": "Nessun tokenId attivo"}

        tx_col = self.v3_lp.collect_fees(tid)
        self.tracker.record_fee_collected(action.get("amount_usd", 0.0))
        return {"status": "success", "operation": "collect_fees", "tx": tx_col}
