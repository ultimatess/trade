"""
Original system test suite (ported to the Phase 2 interfaces).

Validates Indian tax calculations, risk vetoes, kill-switch invariance, Strategy #001
scoring and sizing, bracket execution, and the legacy simulation harness. Each test
keeps the intent of the original Phase 0 test.
"""

import os
import unittest

from tests import factories
from trade.backtesting.engine import BacktestRunner
from trade.brokers.paper import IndianPaperBroker
from trade.core.config import config
from trade.core.execution.charges import IndianTaxCalculator
from trade.core.risk.engine import RiskEngine
from trade.core.strategy.contract import Observation
from trade.strategies.social_momentum.v1.strategy import SocialMomentumV1


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
        charges = IndianTaxCalculator.calculate_charges(100.0, 101.0, 200)
        self.assertEqual(charges["brokerage"], 12.06)  # min(₹20, 0.03%) per leg
        self.assertGreater(charges["stt"], 4.0)  # STT on sell
        self.assertGreater(charges["gst"], 2.0)  # GST
        self.assertEqual(charges["total_charges"], 21.34)
        self.assertEqual(charges["gross_pnl"], 200.0)
        self.assertEqual(charges["net_pnl"], 178.66)

    def test_risk_engine_trading_hours_veto(self):
        """Pre-market or post-market orders must be strictly vetoed."""
        snap = factories.snapshot("TATASTEEL", price=150.0)
        d = self.risk_engine.evaluate(factories.intent("TATASTEEL", 10, 150.0), factories.portfolio(), snap, "09:15")
        self.assertFalse(d.allowed)
        self.assertIn("OUTSIDE_TRADING_WINDOW", d.reason_codes)

    def test_risk_engine_circuit_proximity_veto(self):
        """Orders near Upper or Lower Circuit must be vetoed to avoid liquidity lock."""
        # Price 164.50 with Upper Circuit 165.0 (0.3% away < 1.5% buffer)
        snap = factories.snapshot("ZOMATO", price=164.50, upper_circuit=165.0, lower_circuit=135.0)
        d = self.risk_engine.evaluate(factories.intent("ZOMATO", 10, 164.5), factories.portfolio(), snap, "11:00")
        self.assertFalse(d.allowed)
        self.assertIn("NEAR_UPPER_CIRCUIT", d.reason_codes)

    def test_kill_switch_triggers_on_daily_loss(self):
        """Reaching ₹2,000 daily loss immediately trips the kill switch."""
        snap = factories.snapshot("RELIANCE", price=2500.0)
        d = self.risk_engine.evaluate(
            factories.intent("RELIANCE", 5, 2500.0),
            factories.portfolio(cash=97_900.0, daily_loss=2100.0),
            snap,
            "11:00",
        )
        self.assertFalse(d.allowed)
        self.assertTrue(self.risk_engine.is_kill_switch_active())
        self.assertIn("DAILY_LOSS_LIMIT", d.reason_codes)

    def test_strategy_scoring_and_sizing(self):
        """Strategy #001 scores the setup, emits a BUY signal and caps its sizing request at 20%."""
        obs = Observation(
            symbol="INFY",
            as_of=1000.0,
            session_time="11:00",
            market=factories.snapshot(vwap=1595.0),
            social=factories.social(),
        )
        decision = SocialMomentumV1().on_observation(obs)
        self.assertEqual(len(decision.signals), 1)
        s = decision.signals[0]
        self.assertGreaterEqual(s.confidence, config.MIN_CALIBRATED_PROBABILITY)
        self.assertLessEqual(s.requested_fraction, 0.20)
        self.assertIsNone(s.probability)  # uncalibrated: no probability claimed

    def test_paper_broker_bracket_execution(self):
        """Verifies the Take Profit (+1.00%) bracket trigger."""
        broker = IndianPaperBroker(initial_capital=100_000.0)
        order = broker.submit_intent(factories.intent("TEST_STOCK", 200, 100.0), 100.0, 1000.0)
        self.assertIsNotNone(order)
        pos = broker.positions["TEST_STOCK"]
        trade = broker.update_price_tick("TEST_STOCK", tick_price=pos.target_price + 0.10, tick_time=1060.0)
        self.assertIsNotNone(trade)
        self.assertEqual(trade.exit_reason, "TAKE_PROFIT")
        self.assertGreater(trade.net_pnl, 0.0)

    def test_backtest_simulation_execution(self):
        """Runs the legacy multi-regime simulator and verifies the metric set."""
        metrics = BacktestRunner(seed=123).run_multi_regime_simulation(total_candles=150)
        for key in ("total_trades", "sharpe_ratio", "max_drawdown_pct", "hit_rate_pct", "t_statistic"):
            self.assertIn(key, metrics)


if __name__ == "__main__":
    unittest.main()
