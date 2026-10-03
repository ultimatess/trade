"""Read-only portfolio snapshot handed to the risk engine and sizer."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class PortfolioView:
    as_of: float
    cash: float
    equity: float  # cash + market value of open positions
    open_positions: int
    open_symbols: frozenset[str]
    daily_realized_loss: float  # sum of losing closed trades today (display only)
    day_start_equity: float  # equity at the start of the current IST session
    high_water_mark: float  # highest equity observed

    @property
    def daily_loss(self) -> float:
        """Net loss today, realized + unrealized (D-006). Zero when the day is flat or up."""
        return max(0.0, self.day_start_equity - self.equity)

    @property
    def drawdown_pct(self) -> float:
        return (
            0.0 if self.high_water_mark <= 0 else max(0.0, (self.high_water_mark - self.equity) / self.high_water_mark)
        )
