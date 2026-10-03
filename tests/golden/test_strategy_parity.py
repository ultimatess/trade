"""Strategy #001 v1 (new contract) == legacy FastReflexScorer + strategy gates (frozen oracle)."""

import pytest

from tests.golden.characterize import _case_grid
from tests.golden.legacy import oracle
from trade.core.market_state.models import MarketSnapshot
from trade.core.signals.models import SocialSignal
from trade.core.strategy.contract import Observation
from trade.strategies.social_momentum.v1.strategy import SocialMomentumV1

pytestmark = pytest.mark.critical

LEGACY_TO_V1 = {
    "INSUFFICIENT_BUY_DEPTH_OBI": "OBI_BELOW_MIN",
    "VOLUME_SURGE_ABSENT": "RVOL_BELOW_MIN",
    "BOT_SPAM_CLUSTER_DETECTED": "SPAM_CLUSTER_DETECTED",
    "REFLEX_ORGANIC_PROB_LOW": "P_ORGANIC_BELOW_MIN",
    "REFLEX_WIN_PROB_LOW": "P_WIN_BELOW_MIN",
    "REFLEX_QUALITY_SCORE_LOW": "QUALITY_BELOW_MIN",
}
SCORES = ("p_organic", "p_win_raw", "p_win_calibrated", "setup_quality", "recommended_fraction")
CASES = _case_grid()
STRATEGY = SocialMomentumV1()


def legacy_strategy_vetoes(case):
    # Evaluate every legacy gate (no kill-switch / daily-loss short-circuit), keep the strategy-type ones.
    ctx = {**case["context"], "daily_loss_incurred": 0.0}
    _, risk_reasons, _ = oracle.pre_trade_gates(case["snapshot"], case["signal"], ctx, False)
    reflex = oracle.reflex_evaluate(case["snapshot"], case["signal"])
    codes = {
        LEGACY_TO_V1[r.split(":")[0]] for r in risk_reasons + reflex["veto_reasons"] if r.split(":")[0] in LEGACY_TO_V1
    }
    return codes, reflex


def observe(case):
    snap, sig = MarketSnapshot(**case["snapshot"]), SocialSignal(**case["signal"])
    return Observation(
        symbol=snap.symbol, as_of=snap.timestamp, session_time=case["context"]["time_str"], market=snap, social=sig
    )


@pytest.mark.parametrize("i", range(len(CASES)))
def test_v1_matches_legacy(i):
    case = CASES[i]
    obs = observe(case)
    decision = STRATEGY.validate(STRATEGY.on_observation(obs), obs)
    expected_codes, reflex = legacy_strategy_vetoes(case)

    assert {k: decision.diagnostics[k] for k in SCORES} == {k: reflex[k] for k in SCORES}
    assert set(decision.rejection_codes) == expected_codes
    legacy_would_enter = not expected_codes and reflex["recommended_fraction"] > 0
    assert bool(decision.signals) == legacy_would_enter
    if decision.signals:
        s = decision.signals[0]
        assert s.requested_fraction == reflex["recommended_fraction"]
        assert s.side == "BUY" and s.reference_price == case["snapshot"]["last_price"]


def test_grid_exercises_both_outcomes():
    entries = sum(bool(STRATEGY.on_observation(observe(c)).signals) for c in CASES)
    assert 20 <= entries <= len(CASES) - 20
