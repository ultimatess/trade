"""Read-only portfolio snapshot handed to the risk engine and sizer."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class PortfolioView:
    as_of: float
    cash: float
    equity: float
    open_positions: int
    open_symbols: frozenset[str]
    daily_realized_loss: float
