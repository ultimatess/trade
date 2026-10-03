"""
System configuration, split by authority:

- RiskLimits          config/risk_limits.yaml           (deterministic risk + sizing policy; frozen)
- CostModelConfig     config/cost_models/<id>.yaml      (statutory/broker charges)
- RuntimeSettings     config/runtime.yaml
Strategy parameters are NOT system config: each strategy version owns its strategy.yaml.

Every file is loaded once, validated strictly (missing or unknown keys fail),
and fingerprinted with SHA-256. Loading fails closed: a missing or malformed
file raises at import, so nothing can trade on default values.

"""

from __future__ import annotations

import dataclasses
import hashlib
import os
from dataclasses import dataclass, fields
from pathlib import Path
from typing import Any, TypeVar

import yaml

_PACKAGE_ROOT = Path(__file__).resolve().parents[1]  # src/trade
_REPO_ROOT = _PACKAGE_ROOT.parents[1]


class ConfigError(RuntimeError):
    """Configuration missing, malformed, or failing validation. Never caught to continue trading."""


def config_dir() -> Path:
    return Path(os.environ.get("TRADE_CONFIG_DIR", _REPO_ROOT / "config")).resolve()


def state_dir() -> Path:
    """Persistent runtime state (kill switch, operator token). Absolute; never cwd-relative."""
    return Path(os.environ.get("TRADE_STATE_DIR", _REPO_ROOT / "var" / "state")).resolve()


@dataclass(frozen=True)
class LoadedFile:
    path: str
    sha256: str


@dataclass(frozen=True)
class RiskLimits:
    version: int
    max_order_notional_inr: float
    max_open_positions: int
    max_daily_loss_inr: float
    max_drawdown_pct: float
    max_spread_pct: float
    circuit_buffer_pct: float
    min_adv_inr: float
    entry_window_start: str
    entry_window_end: str
    mandatory_squareoff: str
    min_order_notional_inr: float
    fixed_notional_inr: float
    uncalibrated_kelly_policy: str


@dataclass(frozen=True)
class CostModelConfig:
    id: str
    version: int
    currency: str
    brokerage_per_order_flat: float
    brokerage_per_order_pct: float
    stt_sell_pct: float
    exchange_txn_pct: float
    sebi_pct: float
    stamp_duty_buy_pct: float
    gst_pct: float


@dataclass(frozen=True)
class RuntimeSettings:
    version: int
    starting_capital_inr: float
    broker_type: str


T = TypeVar("T")


def load_yaml_dataclass(path: Path, cls: type[T]) -> tuple[T, LoadedFile]:
    """Strictly load a YAML mapping into a frozen dataclass (exact keys, exact types)."""
    try:
        raw = path.read_bytes()
    except OSError as e:
        raise ConfigError(f"cannot read config {path}: {e}") from e
    data = yaml.safe_load(raw)
    if not isinstance(data, dict):
        raise ConfigError(f"{path}: expected a mapping")
    expected = {f.name: f for f in fields(cls)}  # type: ignore[arg-type]
    missing = expected.keys() - data.keys()
    unknown = data.keys() - expected.keys()
    if missing or unknown:
        raise ConfigError(f"{path}: missing={sorted(missing)} unknown={sorted(unknown)}")
    values: dict[str, Any] = {}
    for name, f in expected.items():
        v = data[name]
        if str(f.type) == "tuple[str, ...]":
            if not isinstance(v, list) or not all(isinstance(x, str) and x for x in v):
                raise ConfigError(f"{path}: {name} must be a list of non-empty strings")
            values[name] = tuple(v)
            continue
        want = {"int": int, "float": float, "str": str}[str(f.type)]
        if want is float and isinstance(v, int) and not isinstance(v, bool):
            v = float(v)
        if type(v) is not want:
            raise ConfigError(f"{path}: {name} must be {want.__name__}, got {type(v).__name__}")
        values[name] = v
    return cls(**values), LoadedFile(str(path), hashlib.sha256(raw).hexdigest())


def load_risk_limits() -> tuple[RiskLimits, LoadedFile]:
    limits, meta = load_yaml_dataclass(config_dir() / "risk_limits.yaml", RiskLimits)
    if not 0.0 < limits.max_drawdown_pct < 1.0 or limits.max_open_positions < 0:
        raise ConfigError("risk_limits.yaml: values out of range")
    if limits.uncalibrated_kelly_policy not in ("legacy_strategy_fraction", "fixed_notional"):
        raise ConfigError("risk_limits.yaml: uncalibrated_kelly_policy must be legacy_strategy_fraction|fixed_notional")
    if not 0 < limits.min_order_notional_inr <= limits.fixed_notional_inr <= limits.max_order_notional_inr:
        raise ConfigError("risk_limits.yaml: require 0 < min_order_notional <= fixed_notional <= max_order_notional")
    return limits, meta


def load_cost_model(model_id: str = "nse_intraday") -> tuple[CostModelConfig, LoadedFile]:
    return load_yaml_dataclass(config_dir() / "cost_models" / f"{model_id}.yaml", CostModelConfig)


def load_runtime() -> tuple[RuntimeSettings, LoadedFile]:
    settings, meta = load_yaml_dataclass(config_dir() / "runtime.yaml", RuntimeSettings)
    broker = os.environ.get("BROKER_TYPE")
    if broker:
        settings = dataclasses.replace(settings, broker_type=broker)
    return settings, meta


@dataclass(frozen=True)
class TradingConfig:
    """System configuration in force. Strategy parameters live in each strategy's strategy.yaml."""

    risk: RiskLimits
    costs: CostModelConfig
    runtime: RuntimeSettings
    sources: tuple[LoadedFile, ...]


def load_config() -> TradingConfig:
    risk, r = load_risk_limits()
    costs, c = load_cost_model()
    runtime, rt = load_runtime()
    return TradingConfig(risk=risk, costs=costs, runtime=runtime, sources=(r, c, rt))


config = load_config()


def log_config_fingerprint(logger: Any) -> None:
    """Record which exact configuration files are in force (start-up audit trail)."""
    for src in config.sources:
        logger.info(f"CONFIG_LOADED {src.path} sha256={src.sha256}")
