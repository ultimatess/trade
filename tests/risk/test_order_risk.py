"""Order-level RiskEngine.evaluate, Sizer and DecisionPipeline (docs/RISK_ARCHITECTURE.md)."""

import dataclasses
import math

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from tests import factories
from trade.core.config import config
from trade.core.pipeline import DecisionPipeline
from trade.core.risk.engine import RiskEngine
from trade.core.risk.sizing import Sizer
from trade.core.strategy.contract import Observation
from trade.strategies.social_momentum.v1.strategy import SocialMomentumV1

pytestmark = pytest.mark.critical
LIM = config.risk


@pytest.fixture
def engine(tmp_path):
    return RiskEngine(lockfile_path=str(tmp_path / "k.lock"))


def evaluate(engine, intent=None, portfolio=None, snap=None, t="11:00"):
    return engine.evaluate(
        intent or factories.intent(), portfolio or factories.portfolio(), snap or factories.snapshot(), t
    )


def test_clean_order_allowed(engine):
    d = evaluate(engine)
    assert d.allowed and d.verdict == "ALLOW" and d.reason_codes == () and d.limits_hash


@pytest.mark.parametrize(
    "kwargs,code",
    [
        ({"t": "09:29"}, "OUTSIDE_TRADING_WINDOW"),
        ({"t": "14:31"}, "OUTSIDE_TRADING_WINDOW"),
        ({"portfolio": factories.portfolio(open_symbols=("A", "B"))}, "MAX_POSITIONS"),
        ({"portfolio": factories.portfolio(open_symbols=("INFY",))}, "DUPLICATE_POSITION"),
        ({"snap": factories.snapshot(upper_circuit=1610.0)}, "NEAR_UPPER_CIRCUIT"),
        ({"snap": factories.snapshot(lower_circuit=1590.0)}, "NEAR_LOWER_CIRCUIT"),
        ({"snap": factories.snapshot(bid=1597.0, ask=1603.0)}, "SPREAD_TOO_WIDE"),
        ({"snap": factories.snapshot(adv_inr=9.9e7)}, "INSUFFICIENT_ADV"),
        ({"intent": factories.intent(quantity=13)}, "ORDER_NOTIONAL_LIMIT"),  # 13 x 1600 = 20,800
        ({"portfolio": factories.portfolio(daily_loss=2000.0)}, "DAILY_LOSS_LIMIT"),
    ],
)
def test_each_limit_denies(engine, kwargs, code):
    d = evaluate(engine, **kwargs)
    assert not d.allowed and code in d.reason_codes


@pytest.mark.parametrize(
    "kwargs",
    [
        {"t": "09:30"},
        {"t": "14:30"},
        {"portfolio": factories.portfolio(open_symbols=("A",))},
        {"intent": factories.intent(quantity=12)},  # 19,200 < 20,000
        {"portfolio": factories.portfolio(daily_loss=1999.99)},
    ],
)
def test_just_inside_limits_allows(engine, kwargs):
    assert evaluate(engine, **kwargs).allowed


@pytest.mark.parametrize(
    "bad",
    [
        {"snap": factories.snapshot(adv_inr=float("nan"))},
        {"snap": factories.snapshot(bid=float("inf"))},
        {"portfolio": factories.portfolio(cash=float("nan"))},
        {"intent": factories.intent(quantity=0)},
        {"intent": factories.intent(reference_price=float("nan"))},
        {"intent": factories.intent(symbol="OTHER")},  # intent/market mismatch
    ],
)
def test_missing_or_invalid_input_denies(engine, bad):
    d = evaluate(engine, **bad)
    assert not d.allowed and d.reason_codes == ("MISSING_INPUT",)


def test_non_finite_daily_loss_halts(engine):
    d = evaluate(engine, portfolio=factories.portfolio(daily_loss=float("nan")))
    assert not d.allowed and engine.is_kill_switch_active()


def test_exception_inside_evaluation_denies(engine, monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("bug")

    monkeypatch.setattr(engine, "_evaluate", boom)
    d = evaluate(engine)
    assert d.verdict == "DENY" and d.reason_codes == ("RISK_ENGINE_ERROR",)


def test_kill_switch_short_circuits(engine):
    engine.trigger_kill_switch("test")
    assert evaluate(engine).reason_codes == ("KILL_SWITCH_ACTIVE",)


@settings(max_examples=400, deadline=None)
@given(
    qty=st.integers(min_value=-5, max_value=100),
    price=st.one_of(st.floats(min_value=-10, max_value=5000), st.sampled_from([float("nan"), float("inf")])),
    positions=st.integers(min_value=0, max_value=4),
    loss=st.one_of(st.floats(min_value=0, max_value=5000), st.just(float("nan"))),
    spread=st.floats(min_value=0, max_value=0.01),
    adv=st.floats(min_value=0, max_value=1e9),
    hhmm=st.sampled_from(["08:00", "09:29", "09:30", "12:00", "14:30", "14:31", "15:30"]),
)
def test_property_allow_never_violates_a_limit(tmp_path_factory, qty, price, positions, loss, spread, adv, hhmm):
    engine = RiskEngine(lockfile_path=str(tmp_path_factory.mktemp("k") / "k.lock"))
    p = 1600.0
    snap = factories.snapshot(bid=p * (1 - spread / 2), ask=p * (1 + spread / 2), adv_inr=adv)
    intent = factories.intent(quantity=qty, price=price)
    pf = factories.portfolio(open_symbols=tuple(f"S{i}" for i in range(positions)), daily_loss=loss)
    d = engine.evaluate(intent, pf, snap, hhmm)
    if d.allowed:
        assert qty > 0 and math.isfinite(price) and price > 0
        assert qty * price <= LIM.max_order_notional_inr
        assert positions < LIM.max_open_positions
        assert loss < LIM.max_daily_loss_inr
        assert snap.spread_pct <= LIM.max_spread_pct and adv >= LIM.min_adv_inr
        assert LIM.entry_window_start <= hhmm <= LIM.entry_window_end
        assert not engine.is_kill_switch_active()


# --- Sizer ---------------------------------------------------------------------


def v1_signal():
    obs = Observation(
        symbol="INFY", as_of=1000.0, session_time="11:00", market=factories.snapshot(), social=factories.social()
    )
    return SocialMomentumV1().on_observation(obs).signals[0]


def test_sizer_legacy_policy_matches_legacy_rule():
    sig, spec = v1_signal(), SocialMomentumV1().spec
    r = Sizer(dataclasses.replace(LIM, uncalibrated_kelly_policy="legacy_strategy_fraction")).size(
        sig, spec, factories.portfolio(cash=100_000.0)
    )
    assert r.method == "legacy_strategy_fraction"
    assert r.intent.quantity == int(min(20_000.0, 100_000.0 * sig.requested_fraction) / sig.reference_price)


def test_sizer_refuses_kelly_for_uncalibrated_strategy_under_fixed_policy():
    sig, spec = v1_signal(), SocialMomentumV1().spec
    r = Sizer(dataclasses.replace(LIM, uncalibrated_kelly_policy="fixed_notional")).size(
        sig, spec, factories.portfolio(cash=100_000.0)
    )
    assert r.method == "fixed_notional"
    assert r.intent.quantity == int(LIM.fixed_notional_inr / sig.reference_price)


def test_sizer_never_exceeds_cash_or_limit_and_rejects_dust():
    sig, spec = v1_signal(), SocialMomentumV1().spec
    fixed = Sizer(dataclasses.replace(LIM, uncalibrated_kelly_policy="fixed_notional"))
    assert fixed.size(sig, spec, factories.portfolio(cash=5_000.0)).intent.notional <= 5_000.0
    r = fixed.size(sig, spec, factories.portfolio(cash=500.0))
    assert r.intent is None and r.reason_codes == ("ALLOCATION_BELOW_MINIMUM",)


# --- Pipeline -------------------------------------------------------------------


class RecordingBroker:
    def __init__(self):
        self.submitted = []

    def submit_intent(self, intent, price, t):
        self.submitted.append(intent)
        return object()


def test_pipeline_risk_denial_never_reaches_broker(engine):
    broker = RecordingBroker()
    obs = Observation(
        symbol="INFY", as_of=1000.0, session_time="15:00", market=factories.snapshot(), social=factories.social()
    )
    result = DecisionPipeline(SocialMomentumV1(), engine).process(obs, factories.portfolio(), broker)
    assert result.step == "RISK_DENIED" and "OUTSIDE_TRADING_WINDOW" in result.reason_codes
    assert broker.submitted == []


def test_pipeline_halted_portfolio_skips_strategy(engine):
    broker = RecordingBroker()
    obs = Observation(
        symbol="INFY", as_of=1000.0, session_time="11:00", market=factories.snapshot(), social=factories.social()
    )
    result = DecisionPipeline(SocialMomentumV1(), engine).process(obs, factories.portfolio(daily_loss=5000.0), broker)
    assert result.step == "RISK_HALTED" and result.decision is None and broker.submitted == []
    assert engine.is_kill_switch_active()


def test_pipeline_allowed_order_reaches_broker(engine):
    broker = RecordingBroker()
    obs = Observation(
        symbol="INFY", as_of=1000.0, session_time="11:00", market=factories.snapshot(), social=factories.social()
    )
    result = DecisionPipeline(SocialMomentumV1(), engine).process(obs, factories.portfolio(), broker)
    assert result.step == "ORDER_FILLED" and len(broker.submitted) == 1
    assert result.risk_decisions[0].allowed
