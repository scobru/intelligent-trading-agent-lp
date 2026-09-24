"""
Unit tests per Intelligent Trading Agent - Concentrated Liquidity LP (Base).
Testa:
  - Matematica Uniswap V3 (tick <-> sqrtPriceX96 <-> human price, quote liquidita', IL)
  - PositionTracker (valutazione in-range, out-of-range, PnL, trigger recenter)
  - PaperLpBook (simulazione mint, accrual fee orario, recenter, equity)
  - LpManager (pianificazione ed esecuzione delle operazioni)
  - db_utils (persistenza ed estrazione snapshot e log operazioni)
"""

import os
import shutil
import tempfile
import time
import unittest
from unittest.mock import MagicMock, patch

import config
import db_utils
import uniswap_v3_lp as lp_math
from lp_manager import LpManager
from paper import PaperLpBook
from positions import PositionTracker


class TestUniswapV3Math(unittest.TestCase):
    def test_sqrt_price_tick_conversions(self):
        tick = -198000
        sqrt_p = lp_math.tick_to_sqrt_price_x96(tick)
        calc_tick = lp_math.sqrt_price_x96_to_tick(sqrt_p)
        self.assertAlmostEqual(tick, calc_tick, delta=1)

    def test_human_price_conversions(self):
        # WETH (18) e USDC (6) -> Prezzo 2500.0 USDC per WETH
        price = 2500.0
        sqrt_p = lp_math.human_price_to_sqrt_price_x96(price, 18, 6)
        calc_price = lp_math.sqrt_price_x96_to_human_price(sqrt_p, 18, 6)
        self.assertAlmostEqual(price, calc_price, places=2)

    def test_calculate_range_ticks(self):
        current_price = 2500.0
        range_w = 8.0  # +/- 4.0%
        fee = 500      # spacing 10
        tick_l, tick_u, p_lower, p_upper = lp_math.calculate_range_ticks(current_price, range_w, fee, 18, 6)

        self.assertLess(tick_l, tick_u)
        self.assertEqual(tick_l % 10, 0)
        self.assertEqual(tick_u % 10, 0)
        self.assertLess(p_lower, current_price)
        self.assertGreater(p_upper, current_price)
        self.assertAlmostEqual(p_lower, 2500.0 * 0.96, delta=15.0)
        self.assertAlmostEqual(p_upper, 2500.0 * 1.04, delta=15.0)

    def test_liquidity_amounts_allocation(self):
        current_p = 2500.0
        tick_l, tick_u, _, _ = lp_math.calculate_range_ticks(current_p, 8.0, 500, 18, 6)
        sqrt_curr = lp_math.human_price_to_sqrt_price_x96(current_p, 18, 6)
        sqrt_l = lp_math.tick_to_sqrt_price_x96(tick_l)
        sqrt_u = lp_math.tick_to_sqrt_price_x96(tick_u)

        # 1. Dentro il range: contiene sia token0 che token1
        liq = 10_000_000_000
        amt0, amt1 = lp_math.get_amounts_for_liquidity(sqrt_curr, sqrt_l, sqrt_u, liq)
        self.assertGreater(amt0, 0)
        self.assertGreater(amt1, 0)

        # 2. Sotto il range (prezzo crolla a 2000): 100% token0 (WETH)
        sqrt_low = lp_math.human_price_to_sqrt_price_x96(2000.0, 18, 6)
        amt0_low, amt1_low = lp_math.get_amounts_for_liquidity(sqrt_low, sqrt_l, sqrt_u, liq)
        self.assertGreater(amt0_low, 0)
        self.assertEqual(amt1_low, 0)

        # 3. Sopra il range (prezzo sale a 3000): 100% token1 (USDC)
        sqrt_high = lp_math.human_price_to_sqrt_price_x96(3000.0, 18, 6)
        amt0_high, amt1_high = lp_math.get_amounts_for_liquidity(sqrt_high, sqrt_l, sqrt_u, liq)
        self.assertEqual(amt0_high, 0)
        self.assertGreater(amt1_high, 0)

    def test_impermanent_loss(self):
        # Se prezzo invariato -> IL = 0
        il_0 = lp_math.calculate_impermanent_loss(2500.0, 2500.0, 2400.0, 2600.0)
        self.assertEqual(il_0, 0.0)

        # Se prezzo si muove verso il bordo -> IL negativo
        il_move = lp_math.calculate_impermanent_loss(2500.0, 2580.0, 2400.0, 2600.0)
        self.assertLess(il_move, 0.0)


class TestPositionTracker(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.state_file = os.path.join(self.temp_dir, "test_pos.json")
        self.tracker = PositionTracker(state_path=self.state_file)

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_no_position_evaluation(self):
        eval_res = self.tracker.evaluate(current_price=2500.0, current_tick=-198000, prices={"WETH": 2500.0, "USDC": 1.0})
        self.assertFalse(eval_res["has_position"])
        self.assertTrue(eval_res["needs_recenter"])

    def test_in_range_evaluation(self):
        pos = {
            "token_id": 1234,
            "token0": "WETH",
            "token1": "USDC",
            "tick_lower": -200000,
            "tick_upper": -196000,
            "price_lower": 2400.0,
            "price_upper": 2600.0,
            "entry_price": 2500.0,
            "entry_time": time.time() - 3600,
            "entry_value_usd": 1000.0,
            "entry_amount0": 0.2,
            "entry_amount1": 500.0,
            "liquidity": 10_000_000_000,
        }
        self.tracker.set_active_position(pos)
        eval_res = self.tracker.evaluate(current_price=2500.0, current_tick=-198000, prices={"WETH": 2500.0, "USDC": 1.0})

        self.assertTrue(eval_res["has_position"])
        self.assertTrue(eval_res["is_strictly_in_range"])
        self.assertFalse(eval_res["needs_recenter"])
        self.assertAlmostEqual(eval_res["range_progress_pct"], 50.0, delta=2.0)

    def test_out_of_range_recenter_trigger(self):
        pos = {
            "token_id": 1234,
            "price_lower": 2400.0,
            "price_upper": 2600.0,
            "tick_lower": -200000,
            "tick_upper": -196000,
            "entry_price": 2500.0,
            "entry_time": time.time() - 10000,  # > MIN_HOLD_HOURS_BEFORE_RECENTER
            "liquidity": 10_000_000_000,
        }
        self.tracker.set_active_position(pos)
        # Prezzo supera il bordo superiore
        eval_res = self.tracker.evaluate(current_price=2650.0, current_tick=-195000, prices={"WETH": 2650.0, "USDC": 1.0})

        self.assertFalse(eval_res["is_strictly_in_range"])
        self.assertTrue(eval_res["needs_recenter"])


class TestPaperLpBook(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.state_file = os.path.join(self.temp_dir, "test_paper_lp.json")
        self.paper = PaperLpBook(path=self.state_file, start_usdc=1000.0, start_eth=0.5)

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_initial_balances(self):
        self.assertEqual(self.paper.balances.get("USDC"), 1000.0)
        self.assertEqual(self.paper.balances.get("ETH"), 0.5)

    def test_mint_position(self):
        res = self.paper.mint_position(current_price=2500.0, range_width_pct=8.0)
        self.assertEqual(res["status"], "success")
        pos = self.paper.active_position
        self.assertIsNotNone(pos)
        self.assertLess(pos["price_lower"], 2500.0)
        self.assertGreater(pos["price_upper"], 2500.0)
        self.assertGreater(pos["liquidity"], 0)

    def test_accrue_and_collect_fees(self):
        self.paper.mint_position(current_price=2500.0, range_width_pct=8.0)
        # Simula 24 ore trascorse
        self.paper.state["last_fee_update_time"] = time.time() - (24 * 3600)
        self.paper.update_accrued_fees(current_price=2500.0)

        uncoll = self.paper.active_position.get("uncollected_fees_usd", 0.0)
        self.assertGreater(uncoll, 0.0)

        # Assicura fee sopra la soglia minima di raccolta ($5.0)
        self.paper.active_position["uncollected_fees_usd"] = 15.0

        # Raccogli fee
        col_res = self.paper.collect_fees_only(current_price=2500.0)
        self.assertEqual(col_res["status"], "success")
        self.assertEqual(self.paper.active_position["uncollected_fees_usd"], 0.0)

    def test_close_and_recenter(self):
        self.paper.mint_position(current_price=2500.0, range_width_pct=8.0)
        # Prezzo si sposta a 2700
        rec_res = self.paper.close_and_recenter(current_price=2700.0, new_range_width_pct=8.0)
        self.assertEqual(rec_res["status"], "success")
        new_pos = self.paper.active_position
        self.assertAlmostEqual(new_pos["entry_price"], 2700.0, places=1)
        self.assertEqual(len(self.paper.state["history"]), 1)


class TestLpManager(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.pos_file = os.path.join(self.temp_dir, "test_pos.json")
        self.paper_file = os.path.join(self.temp_dir, "test_paper_lp.json")

        self.client_mock = MagicMock()
        self.client_mock.address = "0x1111111111111111111111111111111111111111"

        self.p_pos = patch("config.POSITION_STATE_PATH", self.pos_file)
        self.p_paper = patch("config.PAPER_STATE_PATH", self.paper_file)
        self.p_pt = patch("config.PAPER_TRADING", True)
        self.p_pos.start()
        self.p_paper.start()
        self.p_pt.start()

        self.manager = LpManager(self.client_mock)
        # Mock price to avoid external network dependencies during tests
        self.manager._cached_price = 2500.0
        self.manager.last_tick = -198000
        self.manager._last_price_time = time.time() + 1000

    def tearDown(self):
        self.p_pos.stop()
        self.p_paper.stop()
        self.p_pt.stop()
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_plan_and_execute_initial_mint(self):
        status = self.manager.get_status()
        self.assertTrue(status["needs_mint"])

        action = self.manager.plan_action(status)
        self.assertEqual(action["operation"], "mint")

        res = self.manager.execute_action(action, status)
        self.assertEqual(res["status"], "success")

        # Ora la posizione deve essere aperta e in range
        new_status = self.manager.get_status()
        self.assertFalse(new_status["needs_mint"])
        self.assertTrue(new_status["position"]["is_strictly_in_range"])

    def test_plan_hold_when_in_range(self):
        # Prima mint
        self.manager.execute_action({"operation": "mint", "price": 2500.0}, self.manager.get_status())
        status = self.manager.get_status()

        action = self.manager.plan_action(status)
        self.assertEqual(action["operation"], "hold")


class TestDatabaseUtils(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.db_file = os.path.join(self.temp_dir, "test_lp.db")
        self.p_db = patch("config.SQLITE_DB_PATH", self.db_file)
        self.p_db.start()
        db_utils.init_db()

    def tearDown(self):
        self.p_db.stop()
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_log_and_get_snapshots(self):
        status = {
            "current_price": 2500.0,
            "current_tick": -198000,
            "total_equity_usd": 2250.0,
            "position": {
                "current_lp_value_usd": 1000.0,
                "fees_collected_usd": 25.0,
                "is_strictly_in_range": True,
                "impermanent_loss_pct": -0.15,
                "net_pnl_usd": 25.0,
            },
        }
        db_utils.log_snapshot(status)
        recent = db_utils.get_recent_snapshots(limit=5)
        self.assertEqual(len(recent), 1)
        self.assertEqual(recent[0]["current_price"], 2500.0)
        self.assertEqual(recent[0]["is_in_range"], 1)

    def test_log_and_get_operations(self):
        act = {"operation": "recenter", "reason": "Test recenter"}
        res = {"status": "success", "amount_usd": 1000.0}
        db_utils.log_operation(act, res)
        ops = db_utils.get_recent_operations(limit=5)
        self.assertEqual(len(ops), 1)
        self.assertEqual(ops[0]["operation"], "recenter")
        self.assertEqual(ops[0]["amount_usd"], 1000.0)

    def test_fetch_dashboard_data(self):
        status = {
            "current_price": 2600.0,
            "current_tick": -197500,
            "total_equity_usd": 3000.0,
            "position": {
                "current_lp_value_usd": 1500.0,
                "fees_collected_usd": 50.0,
                "is_strictly_in_range": True,
            },
        }
        db_utils.log_snapshot(status)
        db_utils.log_operation({"operation": "mint"}, {"status": "success", "amount_usd": 1500.0})

        dash_data = db_utils.fetch_dashboard_data()
        self.assertIsNotNone(dash_data.get("snapshot_at"))
        self.assertGreaterEqual(len(dash_data.get("equity", [])), 1)
        self.assertGreaterEqual(len(dash_data.get("operations", [])), 1)


class TestDashboardAndAgentRobustness(unittest.TestCase):
    def test_dashboard_handles_address_already_in_use(self):
        import socket
        import dashboard

        # Occupy a free port
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.bind(("127.0.0.1", 0))
        sock.listen(1)
        busy_port = sock.getsockname()[1]

        try:
            with patch("config.DASHBOARD_HOST", "127.0.0.1"), patch("config.DASHBOARD_PORT", busy_port):
                # run_dashboard should log error and return cleanly without raising OSError
                dashboard.run_dashboard(None, None)
        finally:
            sock.close()

    def test_build_meta(self):
        import dashboard

        data = {
            "snapshot_at": 1700000000.0,
            "status": {
                "mode": "paper",
                "total_equity_usd": 1200.0,
                "total_fees_collected_usd": 15.0,
                "total_recenters": 2,
                "paper": {
                    "initial_equity_usd": 1000.0,
                    "total_equity_usd": 1200.0,
                    "pnl_usd": 200.0,
                    "operations": 3,
                    "gas_spent_usd": 0.09,
                    "created_at": 1699900000.0,
                }
            }
        }
        meta = dashboard.build_meta(data)
        self.assertEqual(meta["mode"], "paper")
        self.assertTrue(meta["run_enabled"])
        self.assertIsNotNone(meta["paper"])
        self.assertEqual(meta["paper"]["initial_usd"], 1000.0)
        self.assertEqual(meta["paper"]["value_usd"], 1200.0)

    def test_agent_robust_to_non_json_llm_response(self):
        from lp_agent import LpAgent

        mgr_mock = MagicMock()
        mgr_mock.plan_action.return_value = {"operation": "hold", "reason": "Deterministic fallback"}

        agent = LpAgent(mgr_mock)
        with patch.object(agent, "_query_llm", return_value=None):
            agent.api_key = "dummy_key"
            res = agent.decide({})
            self.assertEqual(res["operation"], "hold")


if __name__ == "__main__":
    unittest.main()


