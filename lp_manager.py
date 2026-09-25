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

        active_cfg = config.get_active_pool_config()
        self.token0_symbol = active_cfg.get("token0", config.POOL_TOKEN0_SYMBOL).upper()
        self.token1_symbol = active_cfg.get("token1", config.POOL_TOKEN1_SYMBOL).upper()
        self.fee_tier = int(active_cfg.get("fee", config.POOL_FEE))
        self.token0_decimals = config.get_token_decimals(self.token0_symbol, 18)
        self.token1_decimals = config.get_token_decimals(self.token1_symbol, 6)

        self._pool_address: Optional[str] = None
        self._cached_price: Optional[float] = None
        self.last_tick: int = 0
        self._last_price_time: float = 0.0

    # ------------------------------------------------------------ pool & prezzo
    def get_pool_address(self) -> str:
        if self._pool_address is None:
            token0 = config.get_token_address(self.token0_symbol)
            token1 = config.get_token_address(self.token1_symbol)
            try:
                self._pool_address = self.v3_lp.get_pool_address(token0, token1, self.fee_tier)
            except Exception as exc:
                logger.warning("Impossibile recuperare pool address da Factory: %s", exc)
                if self.token0_symbol == "WETH" and self.token1_symbol == "USDC" and self.fee_tier == 500:
                    self._pool_address = "0xd4405F0704621DBe9d4dEA60E128E0C3b26bddbD"
        return self._pool_address or "0x0000000000000000000000000000000000000000"

    def get_current_price_and_tick(self) -> Tuple[float, int]:
        """
        Recupera il prezzo human readable (token1 per token0) e il tick corrente.
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
            price = lp_math.sqrt_price_x96_to_human_price(sqrt_p, decimals0=self.token0_decimals, decimals1=self.token1_decimals)
            self._cached_price = price
            self.last_tick = tick
            self._last_price_time = now
            return price, tick
        except Exception as exc:
            logger.warning("Errore lettura slot0 dal pool: %s. Tento tramite Uniswap Quoter...", exc)
            try:
                addr0 = config.get_token_address(self.token0_symbol)
                addr1 = config.get_token_address(self.token1_symbol)
                probe = 0.01 if self.token0_decimals == 18 else 1.0
                price = self.uniswap.price_in_quote(addr0, addr1, probe_units=probe)
                if price:
                    tick = lp_math.human_price_to_tick(price, self.token0_decimals, self.token1_decimals)
                    self._cached_price = price
                    self.last_tick = tick
                    self._last_price_time = now
                    return price, tick
            except Exception as exc2:
                logger.warning("Anche quoter fallito: %s", exc2)

        # Fallback se offline/mock
        fallback_price = self._cached_price or 2500.0
        fallback_tick = lp_math.human_price_to_tick(fallback_price, self.token0_decimals, self.token1_decimals)
        return fallback_price, fallback_tick

    def switch_pool(self, token0_sym: str, token1_sym: str, fee_tier: Optional[int] = None) -> Dict[str, Any]:
        """
        Cambia dinamicamente la pool attiva su cui l'agente opera.
        """
        t0 = token0_sym.strip().upper()
        t1 = token1_sym.strip().upper()
        if not t0 or not t1 or t0 == t1:
            return {"status": "error", "reason": f"Coppia token non valida: {t0}/{t1}"}

        fee = int(fee_tier or self.fee_tier or 500)
        logger.info("Switching LP pool to %s/%s (fee: %d)...", t0, t1, fee)

        config.save_active_pool_config(t0, t1, fee)
        self.token0_symbol = t0
        self.token1_symbol = t1
        self.fee_tier = fee
        self.token0_decimals = config.get_token_decimals(t0, 18)
        self.token1_decimals = config.get_token_decimals(t1, 6)

        # Invalida cache
        self._pool_address = None
        self._cached_price = None
        self._last_price_time = 0.0

        # Se in paper mode e c'era una posizione aperta su altra coppia, chiudila
        if self.paper and self.paper.active_position:
            old_p = self.paper.active_position
            old_t0 = old_p.get("token0")
            old_t1 = old_p.get("token1")
            if old_t0 != t0 or old_t1 != t1:
                logger.info("Chiusura automatica posizione precedente su %s/%s per switch a %s/%s", old_t0, old_t1, t0, t1)
                curr_price, _ = self.get_current_price_and_tick()
                self.paper.close_and_recenter(curr_price, config.RANGE_WIDTH_PCT)

        price, tick = self.get_current_price_and_tick()
        pool_addr = self.get_pool_address()

        return {
            "status": "success",
            "pair": f"{t0}/{t1}",
            "token0": t0,
            "token1": t1,
            "fee_tier": fee,
            "pool_address": pool_addr,
            "current_price": price,
            "current_tick": tick,
        }

    # ------------------------------------------------------------ stato complessivo
    def get_status(self) -> Dict[str, Any]:
        price, tick = self.get_current_price_and_tick()
        prices = {
            self.token1_symbol: 1.0,
            self.token0_symbol: price,
            "USDC": 1.0,
            "WETH": price if self.token0_symbol == "WETH" else 2500.0,
            "ETH": price if self.token0_symbol == "WETH" else 2500.0,
        }

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
            mode = "dry_run" if config.DRY_RUN else "live"
            # In modalità LIVE, verifica che la posizione salvata nel tracker appartenga realmente on-chain al wallet
            if mode == "live" and self.client and self.client.address:
                try:
                    onchain_tids = self.v3_lp.get_user_positions(self.client.address)
                    active_pos = self.tracker.active_position
                    if active_pos:
                        tid = active_pos.get("token_id")
                        if not onchain_tids or tid not in onchain_tids:
                            logger.warning(
                                "Posizione tracker locale (token_id: %s) non esiste on-chain per il wallet %s. Reset residuo paper/dry-run.",
                                tid, self.client.address
                            )
                            self.tracker.set_active_position(None)
                except Exception as exc:
                    logger.debug("Verifica on-chain NFT posizioni fallita: %s", exc)

            pos_eval = self.tracker.evaluate(price, tick, prices)
            t0_addr = config.get_token_address(self.token0_symbol)
            t1_addr = config.get_token_address(self.token1_symbol)
            balances = {
                self.token1_symbol: self.client.balance_of_float(t1_addr) if self.client.address else 0.0,
                self.token0_symbol: self.client.balance_of_float(t0_addr) if self.client.address else 0.0,
                "ETH": self.client.eth_balance() if self.client.address else 0.0,
            }
            paper_summary = None

        # Condizioni operative
        needs_mint = not pos_eval.get("has_position")
        needs_recenter = pos_eval.get("needs_recenter", False)
        uncoll_fees = float(pos_eval.get("fees_collected_usd", 0.0))
        needs_collect = config.AUTO_COLLECT_FEES and (uncoll_fees >= config.MIN_FEE_COLLECT_USD)

        # Calcolo Equity Complessiva (wallet + LP attiva reale)
        pos_val = float(pos_eval.get("current_lp_value_usd", 0.0) if pos_eval.get("has_position") else 0.0)
        u_bal = float(balances.get("USDC", 0.0) or 0.0)
        w_bal = float(balances.get("WETH", 0.0) or 0.0) * price
        total_eq = float(paper_summary.get("total_equity_usd", 0.0)) if paper_summary else (u_bal + w_bal + pos_val)

        status = {
            "mode": mode,
            "wallet": self.client.address if self.client else "",
            "total_equity_usd": round(total_eq, 2),
            "total_value_usd": round(total_eq, 2),
            "pool": {
                "address": self.get_pool_address(),
                "pair": f"{self.token0_symbol}/{self.token1_symbol}",
                "fee_tier": self.fee_tier,
                "token0": self.token0_symbol,
                "token1": self.token1_symbol,
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
            res = self.paper.mint_position(
                price, w,
                token0_sym=self.token0_symbol,
                token1_sym=self.token1_symbol,
                fee_tier=self.fee_tier,
            )
            if res.get("status") == "success":
                self.tracker.set_active_position(res["position"])
            return res

        if config.DRY_RUN:
            tick_l, tick_u, p_l, p_u = lp_math.calculate_range_ticks(
                price, w, self.fee_tier,
                decimals0=self.token0_decimals, decimals1=self.token1_decimals
            )
            plan = f"mint Uniswap V3 LP NFT: range [${p_l:.2f} - ${p_u:.2f}], tick [{tick_l} - {tick_u}]"
            sim_pos = {
                "token_id": 999999,
                "token0": self.token0_symbol,
                "token1": self.token1_symbol,
                "decimals0": self.token0_decimals,
                "decimals1": self.token1_decimals,
                "fee": self.fee_tier,
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
        token0 = config.get_token_address(self.token0_symbol)
        token1 = config.get_token_address(self.token1_symbol)
        tick_l, tick_u, p_l, p_u = lp_math.calculate_range_ticks(
            price, w, self.fee_tier,
            decimals0=self.token0_decimals, decimals1=self.token1_decimals
        )

        # Calcola ammontari desiderati
        t1_bal = self.client.balance_of_float(token1)
        amt1_desired = int(min(t1_bal * 0.8, 1000.0) * (10 ** self.token1_decimals))
        amt0_desired = int(((amt1_desired / (10 ** self.token1_decimals)) / price) * (10 ** self.token0_decimals))

        tx_hash = self.v3_lp.mint_position(
            token0=token0,
            token1=token1,
            fee=self.fee_tier,
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
            tick_l, tick_u, p_l, p_u = lp_math.calculate_range_ticks(
                price, w, self.fee_tier,
                decimals0=self.token0_decimals, decimals1=self.token1_decimals
            )
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
