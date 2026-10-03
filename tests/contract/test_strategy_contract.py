"""Strategy contract: immutability, determinism, purity, signal validation, look-ahead guard."""

import dataclasses
import socket
from pathlib import Path

import pytest

import trade.strategies as strategies_pkg
from trade.core.config import config
from trade.core.market_state.models import MarketSnapshot
from trade.core.signals.models import SocialSignal
from trade.core.strategy.contract import (
    LookAheadError,
    Observation,
    Signal,
    SignalValidationError,
    StrategyDecision,
    StrategySpecError,
    check_risk_requirements,
    fingerprint,
    load_strategy_spec,
)
from trade.strategies.social_momentum.v1.strategy import SocialMomentumV1

pytestmark = pytest.mark.critical

STRATEGIES_ROOT = Path(strategies_pkg.__file__).parent
PINNED = sorted(p.parent for p in STRATEGIES_ROOT.glob("*/v*/FINGERPRINT"))

SNAP = MarketSnapshot(
    symbol="INFY",
    timestamp=1000.0,
    last_price=1600.0,
    bid=1599.8,
    ask=1600.2,
    bid_depth=30000,
    ask_depth=8000,
    vwap=1595.0,
    relative_volume=4.5,
    upper_circuit=1760.0,
    lower_circuit=1440.0,
    adv_inr=3e8,
)
SIG = SocialSignal(
    symbol="INFY",
    timestamp=1000.0,
    mentions_count=60,
    velocity_zscore=4.2,
    unique_verified_ratio=0.9,
    spam_cluster_score=0.05,
)
OBS = Observation(symbol="INFY", as_of=1000.0, session_time="11:00", market=SNAP, social=SIG)


def test_every_strategy_version_is_pinned():
    versions = sorted(p for p in STRATEGIES_ROOT.glob("*/v*") if (p / "strategy.yaml").exists())
    assert versions and versions == PINNED, "every strategy version directory needs a FINGERPRINT"


@pytest.mark.parametrize("version_dir", PINNED, ids=lambda p: f"{p.parent.name}/{p.name}")
def test_pinned_versions_are_unchanged(version_dir):
    """Immutable versions: changing behaviour means creating a new version, not editing this one."""
    assert fingerprint(version_dir) == (version_dir / "FINGERPRINT").read_text().strip()


def test_v1_spec_loads_and_respects_system_limits():
    s = SocialMomentumV1().spec
    assert (s.strategy_id, s.version, s.probability_calibration) == ("social_momentum", 1, "uncalibrated")
    check_risk_requirements(s, config.risk.max_open_positions, config.risk.max_order_notional_inr)


def test_looser_risk_requirements_rejected():
    s = dataclasses.replace(SocialMomentumV1().spec, risk_requirements={"max_open_positions": 99})
    with pytest.raises(StrategySpecError):
        check_risk_requirements(s, 2, 20_000.0)


def test_malformed_spec_rejected(tmp_path):
    src = STRATEGIES_ROOT / "social_momentum" / "v1"
    (tmp_path / "strategy.py").write_bytes((src / "strategy.py").read_bytes())
    text = (src / "strategy.yaml").read_text()
    for bad in (
        text.replace("timeframe: 1m\n", ""),
        text + "\nextra_key: 1\n",
        text.replace("method: fractional_kelly", "method: martingale"),
    ):
        (tmp_path / "strategy.yaml").write_text(bad)
        with pytest.raises(StrategySpecError):
            load_strategy_spec(tmp_path)


def test_deterministic():
    a, b = SocialMomentumV1().on_observation(OBS), SocialMomentumV1().on_observation(OBS)
    assert a == b and a.signals and a.signals[0].signal_id == b.signals[0].signal_id


def test_on_observation_does_no_io(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("strategy attempted I/O")

    strategy = SocialMomentumV1()  # spec loading (file read) happens at construction, not per observation
    monkeypatch.setattr(socket, "socket", forbidden)
    monkeypatch.setattr("builtins.open", forbidden)
    monkeypatch.setattr(Path, "read_bytes", forbidden)
    monkeypatch.setattr(Path, "read_text", forbidden)
    assert strategy.on_observation(OBS).signals


def test_signal_carries_no_quantity_and_no_uncalibrated_probability():
    s = SocialMomentumV1().on_observation(OBS).signals[0]
    assert not hasattr(s, "quantity")
    assert s.probability is None and s.confidence is not None


def test_look_ahead_inputs_rejected():
    with pytest.raises(LookAheadError):
        Observation(symbol="INFY", as_of=999.0, session_time="11:00", market=SNAP, social=SIG)
    late_social = dataclasses.replace(SIG, timestamp=1001.0)
    with pytest.raises(LookAheadError):
        Observation(symbol="INFY", as_of=1000.0, session_time="11:00", market=SNAP, social=late_social)


@pytest.mark.parametrize(
    "change",
    [
        {"stop_price": 1700.0},  # stop above reference for a BUY
        {"take_profit_price": 1500.0},
        {"reference_price": float("nan")},
        {"side": "SHORT_EVERYTHING"},
        {"probability": 1.5},
        {"requested_fraction": 2.0},
        {"reason_codes": ()},
    ],
)
def test_invalid_signals_rejected(change):
    good = SocialMomentumV1().on_observation(OBS).signals[0]
    fields = {
        f.name: getattr(good, f.name)
        for f in dataclasses.fields(good)
        if f.name not in ("signal_id", "feature_snapshot_id")
    }
    with pytest.raises(SignalValidationError):
        Signal(**{**fields, **change})


def test_pipeline_validation_rejects_contract_violations():
    strat = SocialMomentumV1()
    good = strat.on_observation(OBS).signals[0]
    fields = {
        f.name: getattr(good, f.name)
        for f in dataclasses.fields(good)
        if f.name not in ("signal_id", "feature_snapshot_id")
    }
    for change in (
        {"timestamp": 999.0},
        {"reason_codes": ("BUY_BECAUSE_LLM_SAID_SO",)},
        {"strategy_version": 2},
        {"probability": 0.9},
    ):
        bad = Signal(**{**fields, **change})
        with pytest.raises(SignalValidationError):
            strat.validate(StrategyDecision(signals=(bad,)), OBS)
    with pytest.raises(SignalValidationError):
        strat.validate(StrategyDecision(rejection_codes=("MADE_UP",)), OBS)
