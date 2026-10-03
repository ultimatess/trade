"""
Market state models: compact numeric order-book and price snapshots.
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class MarketSnapshot:
    """Compact numeric snapshot consumed by Fast Reflex layer. Strict prior timestamp."""
    symbol: str
    timestamp: float
    last_price: float
    bid: float
    ask: float
    bid_depth: float
    ask_depth: float
    vwap: float
    relative_volume: float
    upper_circuit: float
    lower_circuit: float
    adv_inr: float

    @property
    def spread_pct(self) -> float:
        if self.last_price <= 0:
            return 1.0
        return (self.ask - self.bid) / self.last_price

    @property
    def order_book_imbalance(self) -> float:
        total = self.bid_depth + self.ask_depth
        if total <= 0:
            return 0.0
        return (self.bid_depth - self.ask_depth) / total

    @property
    def dist_to_upper_circuit_pct(self) -> float:
        if self.last_price <= 0:
            return 0.0
        return (self.upper_circuit - self.last_price) / self.last_price

    @property
    def dist_to_lower_circuit_pct(self) -> float:
        if self.last_price <= 0:
            return 0.0
        return (self.last_price - self.lower_circuit) / self.last_price
