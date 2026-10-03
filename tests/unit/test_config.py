"""Configuration loading must be strict and fail closed."""

import dataclasses
import shutil
from pathlib import Path

import pytest

from trade.core import config as cfg

REPO_CONFIG = Path(cfg.__file__).resolve().parents[3] / "config"


@pytest.fixture
def config_copy(tmp_path, monkeypatch):
    dst = tmp_path / "config"
    shutil.copytree(REPO_CONFIG, dst)
    monkeypatch.setenv("TRADE_CONFIG_DIR", str(dst))
    return dst


def test_repo_config_loads_and_matches_legacy_values():
    c = cfg.load_config()
    assert c.risk.max_order_notional_inr == 20_000.0
    assert c.risk.max_open_positions == 2
    assert c.risk.max_daily_loss_inr == 2_000.0
    assert c.risk.max_drawdown_pct == 0.06  # operator decision 2026-10-03
    assert c.runtime.starting_capital_inr == 100_000.0
    assert c.risk.uncalibrated_kelly_policy == "fixed_notional"  # D-003/D-004
    assert c.risk.fixed_notional_inr == 20_000.0
    assert all(len(s.sha256) == 64 for s in c.sources)


def test_risk_limits_are_immutable():
    with pytest.raises(dataclasses.FrozenInstanceError):
        cfg.config.risk.max_daily_loss_inr = 1e9  # type: ignore[misc]


@pytest.mark.critical
def test_missing_file_fails_closed(config_copy):
    (config_copy / "risk_limits.yaml").unlink()
    with pytest.raises(cfg.ConfigError):
        cfg.load_config()


@pytest.mark.critical
@pytest.mark.parametrize(
    "mutation",
    [
        ("max_daily_loss_inr: 2000.0", ""),  # missing key
        ("version: 1", "version: 1\nmax_leverage_override: 99"),  # unknown key
        ("max_open_positions: 2", "max_open_positions: two"),  # wrong type
        ("max_drawdown_pct: 0.06", "max_drawdown_pct: 5.0"),  # out of range
    ],
)
def test_malformed_risk_limits_fail_closed(config_copy, mutation):
    path = config_copy / "risk_limits.yaml"
    text = path.read_text()
    assert mutation[0] in text
    path.write_text(text.replace(mutation[0], mutation[1]))
    with pytest.raises(cfg.ConfigError):
        cfg.load_config()


def test_fingerprint_changes_with_content(config_copy):
    before = {s.path: s.sha256 for s in cfg.load_config().sources}
    path = config_copy / "runtime.yaml"
    path.write_text(path.read_text() + "\n# edited\n")
    after = {s.path: s.sha256 for s in cfg.load_config().sources}
    assert before[str(path)] != after[str(path)]
