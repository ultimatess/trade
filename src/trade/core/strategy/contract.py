"""
Strategy contract (docs/STRATEGY_CONTRACT.md).

Every strategy, whether hand-written, migrated or AI-generated, implements `Strategy`.
A strategy receives an `Observation` and returns a `StrategyDecision`. Any entry is a
typed `Signal`. Signals are proposals: they carry no quantity. Sizing is decided by the
Sizer and approval by the RiskEngine. Execution never parses natural language.
"""

from __future__ import annotations

import hashlib
import json
import math
from abc import ABC, abstractmethod
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

import yaml

from trade.core.market_state.models import MarketSnapshot
from trade.core.signals.models import SocialSignal

Side = Literal["BUY", "SELL"]
FINGERPRINTED_FILES = ("strategy.yaml", "strategy.py")


class StrategySpecError(ValueError):
    """strategy.yaml missing, malformed, or inconsistent with system limits."""


class SignalValidationError(ValueError):
    """A strategy emitted an invalid signal. The signal is rejected, never repaired."""


class LookAheadError(ValueError):
    """An observation contains an input stamped after its as_of time."""


@dataclass(frozen=True)
class Observation:
    """Everything a strategy may see at time `as_of`. Inputs stamped after `as_of` are rejected."""

    symbol: str
    as_of: float  # epoch seconds
    session_time: str  # "HH:MM", exchange-local
    market: MarketSnapshot
    social: SocialSignal | None = None

    def __post_init__(self) -> None:
        if self.market.symbol != self.symbol or (self.social and self.social.symbol != self.symbol):
            raise ValueError("observation inputs must all belong to the observation symbol")
        if self.market.timestamp > self.as_of:
            raise LookAheadError(f"market snapshot at {self.market.timestamp} is after as_of {self.as_of}")
        if self.social and self.social.timestamp > self.as_of:
            raise LookAheadError(f"social signal at {self.social.timestamp} is after as_of {self.as_of}")


@dataclass(frozen=True)
class Signal:
    strategy_id: str
    strategy_version: int
    timestamp: float  # == observation.as_of
    symbol: str
    side: Side
    reference_price: float
    stop_price: float
    take_profit_price: float
    take_profit_pct: float  # bracket offsets, anchored to the fill price by execution
    stop_loss_pct: float
    expected_holding_period_s: int
    probability: float | None  # only when the strategy version is empirically calibrated
    confidence: float | None  # model score; never treated as a probability
    setup_quality: float | None
    requested_fraction: float | None  # strategy's sizing request; the Sizer may refuse it
    reason_codes: tuple[str, ...]
    features: tuple[tuple[str, float], ...]  # exact feature values used
    feature_snapshot_id: str = ""
    signal_id: str = ""

    def __post_init__(self) -> None:
        numbers = [
            self.reference_price,
            self.stop_price,
            self.take_profit_price,
            self.take_profit_pct,
            self.stop_loss_pct,
        ] + [v for _, v in self.features]
        if not all(isinstance(x, (int, float)) and math.isfinite(x) for x in numbers):
            raise SignalValidationError("signal contains a non-finite number")
        if self.side not in ("BUY", "SELL"):
            raise SignalValidationError(f"invalid side {self.side!r}")
        if self.reference_price <= 0 or self.take_profit_pct <= 0 or self.stop_loss_pct <= 0:
            raise SignalValidationError("prices and bracket offsets must be positive")
        ordered = (
            self.stop_price < self.reference_price < self.take_profit_price
            if self.side == "BUY"
            else self.take_profit_price < self.reference_price < self.stop_price
        )
        if not ordered:
            raise SignalValidationError("stop / reference / take-profit prices are not ordered for the side")
        if self.probability is not None and not 0.0 <= self.probability <= 1.0:
            raise SignalValidationError("probability must be within [0, 1]")
        if self.requested_fraction is not None and not 0.0 <= self.requested_fraction <= 1.0:
            raise SignalValidationError("requested_fraction must be within [0, 1]")
        if self.expected_holding_period_s <= 0:
            raise SignalValidationError("expected_holding_period_s must be positive")
        if not self.reason_codes:
            raise SignalValidationError("signal must carry at least one reason code")
        fsid = hashlib.sha256(json.dumps(sorted(self.features)).encode()).hexdigest()[:16]
        object.__setattr__(self, "feature_snapshot_id", fsid)
        ident = f"{self.strategy_id}|{self.strategy_version}|{self.symbol}|{self.timestamp!r}|{self.side}|{fsid}"
        object.__setattr__(self, "signal_id", hashlib.sha256(ident.encode()).hexdigest()[:32])


@dataclass(frozen=True)
class StrategyDecision:
    """Result of one observation: zero or more signals, plus why not when there are none."""

    signals: tuple[Signal, ...] = ()
    rejection_codes: tuple[str, ...] = ()
    diagnostics: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class StrategySpec:
    strategy_id: str
    version: int
    name: str
    description: str
    hypothesis_id: str | None
    provenance: str
    probability_calibration: Literal["uncalibrated", "calibrated"]
    universe: Mapping[str, Any]
    timeframe: str
    session: Mapping[str, str]
    required_data: tuple[Mapping[str, Any], ...]
    features: tuple[Mapping[str, Any], ...]
    parameters: Mapping[str, float]
    entry_conditions: tuple[str, ...]
    exit_conditions: Mapping[str, Any]
    invalidation_conditions: tuple[str, ...]
    position_sizing: Mapping[str, Any]
    risk_requirements: Mapping[str, float]
    reason_codes: tuple[str, ...]
    failure_modes: tuple[str, ...]
    cost_model: str
    fingerprint: str


_SPEC_KEYS: dict[str, type | tuple[type, ...]] = {
    "strategy_id": str,
    "version": int,
    "name": str,
    "description": str,
    "hypothesis_id": (str, type(None)),
    "provenance": str,
    "probability_calibration": str,
    "universe": dict,
    "timeframe": str,
    "session": dict,
    "required_data": list,
    "features": list,
    "parameters": dict,
    "entry_conditions": list,
    "exit_conditions": dict,
    "invalidation_conditions": list,
    "position_sizing": dict,
    "risk_requirements": dict,
    "reason_codes": list,
    "failure_modes": list,
    "cost_model": str,
}
_SIZING_METHODS = {"fixed_notional", "pct_equity", "fractional_kelly"}


def fingerprint(version_dir: Path) -> str:
    """Content hash of a strategy version. Any edit to a pinned version changes it."""
    h = hashlib.sha256()
    for name in FINGERPRINTED_FILES:
        h.update(name.encode() + b"\0" + (version_dir / name).read_bytes() + b"\0")
    return h.hexdigest()


def load_strategy_spec(version_dir: Path) -> StrategySpec:
    path = version_dir / "strategy.yaml"
    try:
        data = yaml.safe_load(path.read_bytes())
    except OSError as e:
        raise StrategySpecError(f"cannot read {path}: {e}") from e
    if not isinstance(data, dict):
        raise StrategySpecError(f"{path}: expected a mapping")
    missing, unknown = _SPEC_KEYS.keys() - data.keys(), data.keys() - _SPEC_KEYS.keys()
    if missing or unknown:
        raise StrategySpecError(f"{path}: missing={sorted(missing)} unknown={sorted(unknown)}")
    for key, typ in _SPEC_KEYS.items():
        if not isinstance(data[key], typ) or isinstance(data[key], bool):
            raise StrategySpecError(f"{path}: {key} has wrong type {type(data[key]).__name__}")
    if data["probability_calibration"] not in ("uncalibrated", "calibrated"):
        raise StrategySpecError(f"{path}: probability_calibration must be uncalibrated|calibrated")
    if not all(isinstance(v, (int, float)) and not isinstance(v, bool) for v in data["parameters"].values()):
        raise StrategySpecError(f"{path}: parameters must be numeric")
    exits = data["exit_conditions"]
    for k in ("take_profit_pct", "stop_loss_pct", "time_stop_minutes"):
        if not isinstance(exits.get(k), (int, float)) or exits[k] <= 0:
            raise StrategySpecError(f"{path}: exit_conditions.{k} must be a positive number")
    if data["position_sizing"].get("method") not in _SIZING_METHODS:
        raise StrategySpecError(f"{path}: position_sizing.method must be one of {sorted(_SIZING_METHODS)}")
    fields: dict[str, Any] = {k: tuple(v) if isinstance(v, list) else v for k, v in data.items()}
    return StrategySpec(**fields, fingerprint=fingerprint(version_dir))


def check_risk_requirements(spec: StrategySpec, max_open_positions: int, max_order_notional: float) -> None:
    """A strategy may only tighten system risk limits, never loosen them."""
    req = spec.risk_requirements
    if (
        req.get("max_open_positions", 0) > max_open_positions
        or req.get("max_order_notional_inr", 0) > max_order_notional
    ):
        raise StrategySpecError(f"{spec.strategy_id} v{spec.version} requests limits looser than RiskLimits")


class Strategy(ABC):
    """Base class. `on_observation` must be a pure function: no I/O, no clock, no hidden randomness."""

    spec: StrategySpec

    @property
    def strategy_id(self) -> str:
        return self.spec.strategy_id

    @property
    def version(self) -> int:
        return self.spec.version

    @abstractmethod
    def on_observation(self, obs: Observation) -> StrategyDecision: ...

    def validate(self, decision: StrategyDecision, obs: Observation) -> StrategyDecision:
        """Contract checks applied by the pipeline to every decision."""
        allowed = set(self.spec.reason_codes)
        for code in decision.rejection_codes:
            if code not in allowed:
                raise SignalValidationError(f"undeclared rejection code {code!r}")
        for s in decision.signals:
            if (s.strategy_id, s.strategy_version) != (self.strategy_id, self.version):
                raise SignalValidationError("signal strategy identity does not match the strategy")
            if s.timestamp != obs.as_of or s.symbol != obs.symbol:
                raise SignalValidationError("signal timestamp/symbol must equal the observation's")
            if not set(s.reason_codes) <= allowed:
                raise SignalValidationError(f"undeclared reason codes {set(s.reason_codes) - allowed}")
            if s.probability is not None and self.spec.probability_calibration != "calibrated":
                raise SignalValidationError("uncalibrated strategy emitted a probability")
        return decision
