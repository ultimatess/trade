"""D-006: drawdown from high-water mark, net daily loss incl. unrealized, IST session reset, flatten on breach."""

from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from tests import factories
from trade.brokers.paper import IndianPaperBroker
from trade.core.pipeline import DecisionPipeline
from trade.core.risk.engine import RiskEngine
from trade.core.strategy.contract import Observation
from trade.strategies.social_momentum.v1.strategy import SocialMomentumV1

pytestmark = pytest.mark.critical
IST = ZoneInfo("Asia/Kolkata")
DAY1_1100 = datetime(2026, 10, 5, 11, 0, tzinfo=IST).timestamp()
DAY2_0935 = datetime(2026, 10, 6, 9, 35, tzinfo=IST).timestamp()


@pytest.fixture
def engine(tmp_path):
    return RiskEngine(lockfile_path=str(tmp_path / "k.lock"))


def test_drawdown_limit_is_6pct_from_high_water_mark(engine):
    ok = factories.portfolio(cash=94_001.0, high_water_mark=100_000.0)  # 5.999% below HWM, flat day
    assert engine.check_portfolio(ok) == ()
    breach = factories.portfolio(cash=94_000.0, high_water_mark=100_000.0)  # exactly 6%
    assert engine.check_portfolio(breach) == ("MAX_DRAWDOWN",)
    assert engine.is_kill_switch_active()


def test_unrealized_losses_count_toward_daily_loss(engine):
    b = IndianPaperBroker(100_000.0)
    b.roll_session(DAY1_1100)
    for sym in ("A", "B"):
        b.submit_intent(factories.intent(sym, 199, 100.0, signal_id=sym), 100.0, DAY1_1100)
    for sym in ("A", "B"):
        b.positions[sym].current_price = 94.9  # mark down ~5.1%, still open
    view = b.portfolio_view(DAY1_1100 + 60)
    assert view.daily_realized_loss == 0.0 and view.daily_loss >= 2000.0
    assert engine.check_portfolio(view) == ("DAILY_LOSS_LIMIT",)


def test_winners_offset_losers_net_daily_loss(engine):
    # Documented semantic change: the legacy gate summed losing trades only (gross); the limit is now net.
    view = factories.portfolio(cash=98_600.0, daily_loss=1_400.0)
    assert engine.check_portfolio(view) == ()


def test_new_ist_session_resets_daily_counters():
    b = IndianPaperBroker(100_000.0)
    b.roll_session(DAY1_1100)
    b.submit_intent(factories.intent("A", 150, 100.0), 100.0, DAY1_1100)
    b.update_price_tick("A", 99.0, DAY1_1100 + 60)  # stop loss: realized loss
    assert b.portfolio_view(DAY1_1100 + 120).daily_loss > 0
    day2 = b.portfolio_view(DAY2_0935)
    assert day2.daily_loss == 0.0 and day2.daily_realized_loss == 0.0
    assert day2.day_start_equity == pytest.approx(b.total_equity)
    assert day2.high_water_mark == pytest.approx(100_000.0)  # HWM is NOT reset daily


def test_breach_flattens_open_positions_and_halts(engine):
    b = IndianPaperBroker(100_000.0)
    b.roll_session(DAY1_1100)
    b.submit_intent(factories.intent("INFY", 12, 1600.0), 1600.0, DAY1_1100)
    b.positions["INFY"].current_price = 1400.0
    b.day_start_equity = b.total_equity + 2_500.0  # day already down Rs 2,500 net
    obs = Observation(
        symbol="INFY",
        as_of=DAY1_1100 + 60,
        session_time="11:01",
        market=factories.snapshot(price=1400.0, ts=DAY1_1100 + 60),
        social=factories.social(ts=DAY1_1100 + 60),
    )
    result = DecisionPipeline(SocialMomentumV1(), engine).process(obs, b.portfolio_view(DAY1_1100 + 60), b)
    assert result.step == "RISK_HALTED" and result.reason_codes == ("DAILY_LOSS_LIMIT",)
    assert not any(p.is_active for p in b.positions.values())
    assert b.trade_history[-1].exit_reason == "RISK_HALT"
    assert engine.is_kill_switch_active()


@pytest.mark.parametrize("field", ["equity", "day_start_equity", "high_water_mark"])
def test_non_finite_portfolio_state_halts(engine, field):
    import dataclasses

    bad = dataclasses.replace(factories.portfolio(), **{field: float("nan")})
    assert engine.check_portfolio(bad) == ("MISSING_INPUT",)
    assert engine.is_kill_switch_active()
