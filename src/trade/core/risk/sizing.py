"""
Position sizing (docs/RISK_ARCHITECTURE.md section 5).

The Sizer turns a Signal into a quantity. It runs BEFORE the RiskEngine and can only
propose; the RiskEngine still approves or denies the sized order. Kelly sizing is
governed by system policy: a strategy without empirical calibration cannot obtain
Kelly sizing unless the operator explicitly sets the legacy policy.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from trade.core.config import RiskLimits
from trade.core.execution.intent import OrderIntent
from trade.core.portfolio.view import PortfolioView
from trade.core.strategy.contract import Signal, StrategySpec


@dataclass(frozen=True)
class SizingResult:
    intent: OrderIntent | None
    method: str
    reason_codes: tuple[str, ...] = ()


class Sizer:
    def __init__(self, limits: RiskLimits):
        self.limits = limits

    def _method(self, spec: StrategySpec) -> str:
        requested = str(spec.position_sizing.get("method"))
        if requested == "fractional_kelly" and spec.probability_calibration != "calibrated":
            if self.limits.uncalibrated_kelly_policy == "legacy_strategy_fraction":
                return "legacy_strategy_fraction"
            return "fixed_notional"
        return requested

    def size(self, signal: Signal, spec: StrategySpec, portfolio: PortfolioView) -> SizingResult:
        method = self._method(spec)
        price = signal.reference_price
        if method == "legacy_strategy_fraction":
            fraction = signal.requested_fraction or 0.0
            allocation = min(self.limits.max_order_notional_inr, portfolio.cash * fraction)
        elif method == "fixed_notional":
            allocation = min(self.limits.fixed_notional_inr, self.limits.max_order_notional_inr, portfolio.cash)
        else:
            return SizingResult(None, method, ("SIZING_METHOD_NOT_IMPLEMENTED",))

        if not math.isfinite(allocation) or allocation < self.limits.min_order_notional_inr or price <= 0:
            return SizingResult(None, method, ("ALLOCATION_BELOW_MINIMUM",))
        quantity = int(allocation / price)
        if quantity <= 0:
            return SizingResult(None, method, ("QUANTITY_ZERO",))
        intent = OrderIntent(
            signal_id=signal.signal_id,
            strategy_id=signal.strategy_id,
            strategy_version=signal.strategy_version,
            symbol=signal.symbol,
            side=signal.side,
            quantity=quantity,
            reference_price=price,
            take_profit_pct=signal.take_profit_pct,
            stop_loss_pct=signal.stop_loss_pct,
            max_holding_s=signal.expected_holding_period_s,
            sizing_method=method,
            created_at=signal.timestamp,
        )
        return SizingResult(intent, method)
