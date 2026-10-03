"""
Portfolio models: live open exposure.
"""

from dataclasses import dataclass


@dataclass
class Position:
    """Live open exposure tracking."""

    symbol: str
    entry_price: float
    quantity: int
    entry_time: float
    target_price: float
    stop_loss_price: float
    current_price: float
    is_active: bool = True

    @property
    def gross_unrealized_pnl(self) -> float:
        return (self.current_price - self.entry_price) * self.quantity

    @property
    def pnl_pct(self) -> float:
        if self.entry_price <= 0:
            return 0.0
        return (self.current_price - self.entry_price) / self.entry_price
