"""Order intents: a sized, not-yet-approved order derived from a Signal."""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Literal


@dataclass(frozen=True)
class OrderIntent:
    signal_id: str
    strategy_id: str
    strategy_version: int
    symbol: str
    side: Literal["BUY", "SELL"]
    quantity: int
    reference_price: float
    take_profit_pct: float  # bracket anchored to the fill price
    stop_loss_pct: float
    max_holding_s: int
    sizing_method: str
    created_at: float

    @property
    def intent_id(self) -> str:
        return f"{self.signal_id}:{self.quantity}"

    @property
    def notional(self) -> float:
        return self.quantity * self.reference_price

    def is_well_formed(self) -> bool:
        values = (self.reference_price, self.take_profit_pct, self.stop_loss_pct)
        return (
            self.quantity > 0
            and all(math.isfinite(v) and v > 0 for v in values)
            and self.max_holding_s > 0
            and self.side in ("BUY", "SELL")
        )
