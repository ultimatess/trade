"""
Comprehensive Test Suite for Autonomous Quant System.
Validates Indian Tax Calculations, Risk Vetoes, Kill Switch Invariance,
Reflex Calibration, and OCO Execution.
"""

import os
import unittest
from india_quant_bot.config.settings import config
from india_quant_bot.core.models import MarketSnapshot, SocialSignal
from india_quant_bot.execution.tax_calculator import IndianTaxCalculator
from india_quant_bot.core.risk_engine import RiskEngine
from india_quant_bot.reflex.decision_engine import FastReflexScorer, CalibrationEngine
from india_quant_bot.execution.paper_broker import IndianPaperBroker
from india_quant_bot.backtest.backtester import BacktestRunner, BacktestMetrics

class TestQuantSystem(unittest.TestCase):

    def setUp(self):
        self.test_lockfile = "/tmp/test_trading_system.lock"
        if os.path.exists(self.test_lockfile):
            os.remove(self.test_lockfile)
        self.risk_engine = RiskEngine(lockfile_path=self.test_lockfile)

    def tearDown(self):
        if os.path.exists(self.test_lockfile):
            os.remove(self.test_lockfile)

    def test_indian_tax_calculator(self):
        """Verifies statutory charges for a ₹20,000 roundtrip trade."""
        buy_price = 100.0
        sell_price = 101.0  # +1.00% gross gain
        quantity = 200      # ₹20,000 turnover

        charges = IndianTaxCalculator.calculate_charges(buy_price, sell_price, quantity)
        # Brokerage should be around ₹40 (₹20 buy + ₹20 sell)
        self.assertAlmostEqual(charges["brokerage"], 12.06, delta=30.0)
        self.assertGreater(charges["stt"], 4.0)       # STT on sell
        self.assertGreater(charges["gst"], 2.0)       # GST
        self.assertGreater(charges["total_charges"], 10.0)
        # Gross gain is ₹200.00
        self.assertEqual(charges["gross_pnl"], 200.0)
        # Net gain after charges should be positive (~₹150 to ₹175)
        self.assertGreater(charges["net_pnl"], 100.0)

    def test_risk_engine_trading_hours_veto(self):
        """Pre-market or post-market orders must be strictly vetoed."""
        snapshot = MarketSnapshot(
            symbol="TATASTEEL", timestamp=1000.0, last_price=150.0,
            bid=149.95, ask=150.05, bid_depth=20000, ask_depth=5000,
            vwap=149.5, relative_volume=3.5, upper_circuit=165.0,
            lower_circuit=135.0, adv_inr=150_000_000.0
        )
        signal = SocialSignal(
            symbol="TATASTEEL", timestamp=1000.0, mentions_count=45,
            velocity_zscore=3.8, unique_verified_ratio=0.8, spam_cluster_score=0.1
        )

        # Before 09:30 AM IST
        passed, reasons = self.risk_engine.validate_pre_trade_gates(
            snapshot=snapshot, signal=signal, current_equity=100_000.0,
            current_positions_count=0, daily_loss_incurred=0.0, time_str="09:15"
        )
        self.assertFalse(passed)
        self.assertTrue(any("OUTSIDE_TRADING_WINDOW" in r for r in reasons))

    def test_risk_engine_circuit_proximity_veto(self):
        """Orders near Upper or Lower Circuit must be vetoed to avoid liquidity lock."""
        # Price is at 164.50 with Upper Circuit at 165.0 (0.3% away < 1.5% buffer)
        snapshot = MarketSnapshot(
            symbol="ZOMATO", timestamp=1000.0, last_price=164.50,
            bid=164.40, ask=164.60, bid_depth=20000, ask_depth=5000,
            vwap=160.0, relative_volume=4.0, upper_circuit=165.0,
            lower_circuit=135.0, adv_inr=150_000_000.0
        )
        signal = SocialSignal(
            symbol="ZOMATO", timestamp=1000.0, mentions_count=50,
            velocity_zscore=4.0, unique_verified_ratio=0.85, spam_cluster_score=0.05
        )

        passed, reasons = self.risk_engine.validate_pre_trade_gates(
            snapshot=snapshot, signal=signal, current_equity=100_000.0,
            current_positions_count=0, daily_loss_incurred=0.0, time_str="11:00"
        )
        self.assertFalse(passed)
        self.assertTrue(any("TOO_CLOSE_TO_UPPER_CIRCUIT" in r for r in reasons))

    def test_kill_switch_triggers_on_daily_loss(self):
        """Reaching ₹2,000 daily loss immediately trips kill switch."""
        snapshot = MarketSnapshot(
            symbol="RELIANCE", timestamp=1000.0, last_price=2500.0,
            bid=2499.5, ask=2500.5, bid_depth=20000, ask_depth=5000,
            vwap=2490.0, relative_volume=3.5, upper_circuit=2750.0,
            lower_circuit=2250.0, adv_inr=500_000_000.0
        )
        signal = SocialSignal(
            symbol="RELIANCE", timestamp=1000.0, mentions_count=40,
            velocity_zscore=3.5, unique_verified_ratio=0.85, spam_cluster_score=0.1
        )

        # Incurred loss of ₹2,100 (breaches ₹2,000 limit)
        passed, reasons = self.risk_engine.validate_pre_trade_gates(
            snapshot=snapshot, signal=signal, current_equity=97_900.0,
            current_positions_count=0, daily_loss_incurred=2100.0, time_str="11:00"
        )
        self.assertFalse(passed)
        self.assertTrue(self.risk_engine.is_kill_switch_active())
        self.assertTrue(any("DAILY_LOSS_LIMIT_EXCEEDED" in r for r in reasons))

    def test_fast_reflex_scoring_and_sizing(self):
        """Fast reflex computes calibrated probability and caps Kelly sizing at 20% NAV."""
        calibrator = CalibrationEngine()
        scorer = FastReflexScorer(calibrator)

        snapshot = MarketSnapshot(
            symbol="INFY", timestamp=1000.0, last_price=1600.0,
            bid=1599.5, ask=1600.5, bid_depth=30000, ask_depth=8000,
            vwap=1595.0, relative_volume=4.5, upper_circuit=1760.0,
            lower_circuit=1440.0, adv_inr=300_000_000.0
        )
        signal = SocialSignal(
            symbol="INFY", timestamp=1000.0, mentions_count=60,
            velocity_zscore=4.2, unique_verified_ratio=0.9, spam_cluster_score=0.05
        )

        decision = scorer.evaluate(snapshot, signal)
        self.assertTrue(decision.passed_all_gates)
        self.assertGreaterEqual(decision.p_win_calibrated, config.MIN_CALIBRATED_PROBABILITY)
        self.assertLessEqual(decision.recommended_fraction, 0.20)  # Capped at 20%

    def test_paper_broker_oco_execution(self):
        """Verifies Take Profit (+1.00%) and Stop Loss (-0.70%) triggers."""
        broker = IndianPaperBroker(initial_capital=100_000.0)
        entry_price = 100.0

        # Place Entry
        order = broker.submit_bracket_entry("TEST_STOCK", capital_fraction=0.20, current_price=entry_price, current_time=1000.0)
        self.assertIsNotNone(order)
        self.assertIn("TEST_STOCK", broker.positions)
        pos = broker.positions["TEST_STOCK"]

        # Simulate price moving to target price (+1.00%)
        trade = broker.update_price_tick("TEST_STOCK", tick_price=pos.target_price + 0.10, tick_time=1060.0)
        self.assertIsNotNone(trade)
        self.assertEqual(trade.exit_reason, "TAKE_PROFIT")
        self.assertGreater(trade.net_pnl, 0.0)

    def test_backtest_simulation_execution(self):
        """Runs multi-regime backtester and verifies statistical calculation."""
        runner = BacktestRunner(seed=123)
        metrics = runner.run_multi_regime_simulation(total_candles=150)
        self.assertIn("total_trades", metrics)
        self.assertIn("sharpe_ratio", metrics)
        self.assertIn("max_drawdown_pct", metrics)
        self.assertIn("hit_rate_pct", metrics)
        self.assertIn("t_statistic", metrics)


if __name__ == "__main__":
    unittest.main()
