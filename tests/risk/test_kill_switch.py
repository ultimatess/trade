"""Kill switch must fail closed (docs/RISK_ARCHITECTURE.md section 6)."""

import os
import stat

import pytest

from tests import factories
from trade.core.market_state.models import MarketSnapshot
from trade.core.risk.engine import RiskEngine

pytestmark = pytest.mark.critical

needs_non_root = pytest.mark.skipif(hasattr(os, "geteuid") and os.geteuid() == 0, reason="root ignores permissions")

SNAP = MarketSnapshot(
    symbol="INFY",
    timestamp=1.0,
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


def gates(engine):
    d = engine.evaluate(factories.intent(), factories.portfolio(), SNAP, "11:00")
    return d.allowed, list(d.reason_codes)


def test_clean_state_allows_and_trigger_blocks(tmp_path):
    engine = RiskEngine(lockfile_path=str(tmp_path / "k.lock"))
    assert gates(engine)[0] is True
    engine.trigger_kill_switch("test")
    passed, reasons = gates(engine)
    assert passed is False and reasons == ["KILL_SWITCH_ACTIVE"]


def test_state_persists_across_restart(tmp_path):
    RiskEngine(lockfile_path=str(tmp_path / "k.lock")).trigger_kill_switch("before restart")
    assert RiskEngine(lockfile_path=str(tmp_path / "k.lock")).is_kill_switch_active()


@needs_non_root
def test_write_failure_still_halts(tmp_path):
    locked = tmp_path / "ro"
    locked.mkdir()
    locked.chmod(stat.S_IRUSR | stat.S_IXUSR)
    try:
        engine = RiskEngine(lockfile_path=str(locked / "k.lock"))
        engine.trigger_kill_switch("disk read-only")
        assert not (locked / "k.lock").exists()
        assert engine.is_kill_switch_active()
        assert gates(engine)[0] is False
    finally:
        locked.chmod(stat.S_IRWXU)


@needs_non_root
def test_unreadable_state_is_treated_as_active(tmp_path):
    hidden = tmp_path / "hidden"
    hidden.mkdir()
    engine = RiskEngine(lockfile_path=str(hidden / "k.lock"))
    hidden.chmod(0)
    try:
        assert engine.is_kill_switch_active()
        assert gates(engine)[0] is False
    finally:
        hidden.chmod(stat.S_IRWXU)


@needs_non_root
def test_failed_clear_stays_halted(tmp_path):
    d = tmp_path / "d"
    d.mkdir()
    engine = RiskEngine(lockfile_path=str(d / "k.lock"))
    engine.trigger_kill_switch("x")
    d.chmod(stat.S_IRUSR | stat.S_IXUSR)
    try:
        assert engine.clear_kill_switch() is False
        assert engine.is_kill_switch_active()
    finally:
        d.chmod(stat.S_IRWXU)


def test_explicit_clear_resets(tmp_path):
    engine = RiskEngine(lockfile_path=str(tmp_path / "k.lock"))
    engine.trigger_kill_switch("x")
    assert engine.clear_kill_switch() is True
    assert not engine.is_kill_switch_active()


def test_working_directory_change_does_not_bypass(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    engine = RiskEngine(lockfile_path="rel.lock")
    engine.trigger_kill_switch("x")
    other = tmp_path / "elsewhere"
    other.mkdir()
    monkeypatch.chdir(other)
    assert os.path.isabs(engine.lockfile_path)
    assert engine.is_kill_switch_active()
    assert RiskEngine(lockfile_path=str(tmp_path / "rel.lock")).is_kill_switch_active()


def test_default_location_is_absolute_state_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("TRADE_STATE_DIR", str(tmp_path / "state"))
    engine = RiskEngine()
    assert engine.lockfile_path == str((tmp_path / "state" / "trading.lock").resolve())
    engine.trigger_kill_switch("x")
    assert (tmp_path / "state" / "trading.lock").exists()


def test_daily_loss_breach_triggers_persistent_kill(tmp_path):
    engine = RiskEngine(lockfile_path=str(tmp_path / "k.lock"))
    d = engine.evaluate(factories.intent(), factories.portfolio(cash=97_900.0, daily_loss=2_000.0), SNAP, "11:00")
    assert not d.allowed and d.reason_codes == ("DAILY_LOSS_LIMIT",)
    assert RiskEngine(lockfile_path=str(tmp_path / "k.lock")).is_kill_switch_active()
