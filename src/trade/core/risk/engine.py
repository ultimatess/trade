"""
Deterministic Risk Engine & Kill Switch (docs/RISK_ARCHITECTURE.md).

Authoritative over every strategy and model. It evaluates SIZED orders (OrderIntent)
and returns ALLOW or DENY(reason_codes). Any error, missing or non-finite input results
in DENY. Strategy entry rules (OBI, volume, social scores) are NOT here; they belong
to the strategy. Flattening on a kill is performed by the caller.
"""

import hashlib
import logging
import math
import os
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

from trade.core.config import RiskLimits, config, state_dir
from trade.core.execution.intent import OrderIntent
from trade.core.market_state.models import MarketSnapshot
from trade.core.portfolio.view import PortfolioView

logger = logging.getLogger("RiskEngine")
KILL_LOCKFILE_NAME = "trading.lock"

# Reason codes (machine-readable; never free text)
KILL_SWITCH_ACTIVE = "KILL_SWITCH_ACTIVE"
DAILY_LOSS_LIMIT = "DAILY_LOSS_LIMIT"
MAX_DRAWDOWN = "MAX_DRAWDOWN"
OUTSIDE_TRADING_WINDOW = "OUTSIDE_TRADING_WINDOW"
MAX_POSITIONS = "MAX_POSITIONS"
DUPLICATE_POSITION = "DUPLICATE_POSITION"
NEAR_UPPER_CIRCUIT = "NEAR_UPPER_CIRCUIT"
NEAR_LOWER_CIRCUIT = "NEAR_LOWER_CIRCUIT"
SPREAD_TOO_WIDE = "SPREAD_TOO_WIDE"
INSUFFICIENT_ADV = "INSUFFICIENT_ADV"
ORDER_NOTIONAL_LIMIT = "ORDER_NOTIONAL_LIMIT"
MISSING_INPUT = "MISSING_INPUT"
RISK_ENGINE_ERROR = "RISK_ENGINE_ERROR"


@dataclass(frozen=True)
class RiskDecision:
    intent_id: str
    verdict: Literal["ALLOW", "DENY"]
    reason_codes: tuple[str, ...]
    limits_hash: str

    @property
    def allowed(self) -> bool:
        return self.verdict == "ALLOW" and not self.reason_codes


class RiskEngine:
    """Deterministic risk guardian. The kill switch fails closed."""

    def __init__(self, lockfile_path: str | None = None, limits: RiskLimits | None = None):
        self.limits = limits or config.risk
        self.limits_hash = hashlib.sha256(repr(self.limits).encode()).hexdigest()[:16]
        # Absolute path: the kill switch must not depend on the working directory.
        path = Path(lockfile_path) if lockfile_path else state_dir() / KILL_LOCKFILE_NAME
        self.lockfile_path = str(path.resolve())
        # In-memory halt survives a failed lockfile write; only an explicit clear resets it.
        self._halted_in_memory = False

    def is_kill_switch_active(self) -> bool:
        """Active if halted in memory, the lockfile exists, or the lockfile state cannot be read."""
        if self._halted_in_memory:
            return True
        try:
            Path(self.lockfile_path).stat()
            return True
        except FileNotFoundError:
            return False
        except OSError as e:
            logger.critical(f"Kill switch state unreadable ({e}); treating as ACTIVE")
            return True

    def trigger_kill_switch(self, reason: str) -> None:
        """Engages the kill switch: halt in memory first, then persist the lockfile."""
        self._halted_in_memory = True
        logger.critical(f"EMERGENCY KILL SWITCH ENGAGED: {reason}")
        try:
            Path(self.lockfile_path).parent.mkdir(parents=True, exist_ok=True)
            with open(self.lockfile_path, "w") as f:
                f.write(f"REASON: {reason}\nTRIGGERED_AT: {datetime.now(UTC).isoformat()}\n")
        except Exception as e:
            logger.critical(f"Failed to persist kill switch lockfile ({e}); remaining HALTED in memory")

    def clear_kill_switch(self) -> bool:
        """Manual operator unlock. Stays halted if the lockfile cannot be removed."""
        try:
            os.remove(self.lockfile_path)
        except FileNotFoundError:
            pass
        except OSError as e:
            logger.critical(f"Failed to remove kill switch lockfile ({e}); remaining HALTED")
            return False
        was_active = self._halted_in_memory
        self._halted_in_memory = False
        if was_active or not self.is_kill_switch_active():
            logger.info("Kill switch reset by operator.")
        return True

    # ------------------------------------------------------------------ portfolio monitor
    def check_portfolio(self, portfolio: PortfolioView) -> tuple[str, ...]:
        """Runs every cycle, signal or not. A breached portfolio limit engages the kill switch."""
        try:
            if self.is_kill_switch_active():
                return (KILL_SWITCH_ACTIVE,)
            values = (portfolio.equity, portfolio.day_start_equity, portfolio.high_water_mark)
            if not all(math.isfinite(v) for v in values) or portfolio.high_water_mark <= 0:
                self.trigger_kill_switch("Portfolio state invalid (non-finite equity, day start or high-water mark)")
                return (MISSING_INPUT,)
            # D-006: net daily loss including unrealized P&L, and drawdown from the high-water mark
            if portfolio.daily_loss >= self.limits.max_daily_loss_inr:
                self.trigger_kill_switch(
                    f"Daily loss ₹{portfolio.daily_loss:,.2f} (realized + unrealized) breached "
                    f"limit ₹{self.limits.max_daily_loss_inr:,.2f}"
                )
                return (DAILY_LOSS_LIMIT,)
            if portfolio.drawdown_pct >= self.limits.max_drawdown_pct:
                self.trigger_kill_switch(
                    f"Drawdown {portfolio.drawdown_pct:.2%} from high-water mark ₹{portfolio.high_water_mark:,.2f} "
                    f"breached limit {self.limits.max_drawdown_pct:.0%}"
                )
                return (MAX_DRAWDOWN,)
            return ()
        except Exception as e:  # noqa: BLE001 - fail closed on any error
            logger.critical(f"Portfolio check failed ({e}); engaging kill switch")
            self.trigger_kill_switch(f"portfolio check error: {e}")
            return (RISK_ENGINE_ERROR,)

    # ------------------------------------------------------------------ order-level decision
    def evaluate(
        self, intent: OrderIntent, portfolio: PortfolioView, market: MarketSnapshot, session_time: str
    ) -> RiskDecision:
        """ALLOW or DENY(reason_codes) for a sized order. Any exception results in DENY."""
        try:
            reasons = self._evaluate(intent, portfolio, market, session_time)
        except Exception as e:  # noqa: BLE001 - fail closed on any error
            logger.critical(f"Risk evaluation error ({e}); denying order")
            reasons = (RISK_ENGINE_ERROR,)
        return RiskDecision(
            intent_id=intent.intent_id,
            verdict="DENY" if reasons else "ALLOW",
            reason_codes=reasons,
            limits_hash=self.limits_hash,
        )

    def _evaluate(
        self, intent: OrderIntent, portfolio: PortfolioView, market: MarketSnapshot, session_time: str
    ) -> tuple[str, ...]:
        lim = self.limits
        halt = self.check_portfolio(portfolio)
        if halt:
            return halt
        numbers = (
            market.last_price,
            market.bid,
            market.ask,
            market.upper_circuit,
            market.lower_circuit,
            market.adv_inr,
            portfolio.cash,
            portfolio.equity,
        )
        if not all(math.isfinite(x) for x in numbers) or not intent.is_well_formed():
            return (MISSING_INPUT,)
        if intent.symbol != market.symbol:
            return (MISSING_INPUT,)

        r: list[str] = []
        if session_time < lim.entry_window_start or session_time > lim.entry_window_end:
            r.append(OUTSIDE_TRADING_WINDOW)
        if portfolio.open_positions >= lim.max_open_positions:
            r.append(MAX_POSITIONS)
        if intent.symbol in portfolio.open_symbols:
            r.append(DUPLICATE_POSITION)
        if market.dist_to_upper_circuit_pct < lim.circuit_buffer_pct:
            r.append(NEAR_UPPER_CIRCUIT)
        if market.dist_to_lower_circuit_pct < lim.circuit_buffer_pct:
            r.append(NEAR_LOWER_CIRCUIT)
        if market.spread_pct > lim.max_spread_pct:
            r.append(SPREAD_TOO_WIDE)
        if market.adv_inr < lim.min_adv_inr:
            r.append(INSUFFICIENT_ADV)
        if intent.notional > lim.max_order_notional_inr:
            r.append(ORDER_NOTIONAL_LIMIT)
        return tuple(r)
