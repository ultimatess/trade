"""D-002: Brier score pairs each outcome with the forecast made at entry."""

import pytest

from trade.core.risk.engine import RiskEngine
from trade.core.strategy.calibration import CalibrationEngine
from trade.paper.trading_system import QuantTradingSystem

pytestmark = pytest.mark.critical


def test_resolve_uses_registered_forecast_once():
    c = CalibrationEngine()
    c.register_forecast("s1", 0.8)
    c.resolve("s1", net_pnl=-10.0)
    c.resolve("s1", net_pnl=50.0)  # already resolved: ignored
    c.resolve("unknown", net_pnl=50.0)
    c.resolve(None, net_pnl=50.0)
    assert c.history == [(0.8, 0)]
    assert c.compute_brier_score() == pytest.approx(0.64)


def test_paper_loop_records_forecast_for_bracket_exit(tmp_path):
    system = QuantTradingSystem()
    system.risk_engine = RiskEngine(lockfile_path=str(tmp_path / "k.lock"))
    tweets = [f"$INFY breakout {i}" for i in range(8)]  # 8 distinct mentions -> high velocity, no spam
    system.run_single_cycle("INFY", 1600.0, 45000, 12000, 4.5, 3e8, tweets, "11:00", timestamp=1_700_000_000.0)
    assert system.calibration_engine.pending, "entry should register a forecast"
    forecast = next(iter(system.calibration_engine.pending.values()))
    target = system.broker.positions["INFY"].target_price
    system.run_single_cycle("INFY", target + 1, 45000, 12000, 4.5, 3e8, [], "11:05", timestamp=1_700_000_300.0)
    assert system.calibration_engine.history == [(forecast, 1)]
    assert 0.0 < forecast < 1.0
