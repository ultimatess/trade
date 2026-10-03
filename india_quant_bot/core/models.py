"""
Core Data Models for Market State, Social Signals, Decisions, Orders, and Trades.
Enforces type safety and strict numeric schemas across all layers.
"""

from dataclasses import dataclass, field
from typing import List, Optional
import time
import uuid

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


@dataclass(frozen=True)
class SocialSignal:
    """Sanitized social velocity signal. Contains NO raw prompt text."""
    symbol: str
    timestamp: float
    mentions_count: int
    velocity_zscore: float
    unique_verified_ratio: float
    spam_cluster_score: float  # 0.0 (clean) to 1.0 (pure bot spam)


@dataclass
class ReflexDecision:
    """Output from the Fast Reflex layer. Advisory only."""
    symbol: str
    timestamp: float
    p_organic: float
    p_win_raw: float
    p_win_calibrated: float
    setup_quality: float
    recommended_fraction: float
    passed_all_gates: bool
    veto_reasons: List[str] = field(default_factory=list)


@dataclass
class Order:
    """Idempotent order definition."""
    client_order_id: str
    symbol: str
    side: str           # "BUY" or "SELL"
    quantity: int
    order_type: str     # "LIMIT", "MARKET", "STOP"
    price: float
    created_at: float = field(default_factory=time.time)
    status: str = "PENDING"  # "PENDING", "FILLED", "CANCELLED", "REJECTED"


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


@dataclass
class TradeResult:
    """Final executed trade telemetry."""
    symbol: str
    entry_price: float
    exit_price: float
    quantity: int
    entry_time: float
    exit_time: float
    gross_pnl: float
    total_statutory_charges: float
    net_pnl: float
    exit_reason: str  # "TAKE_PROFIT", "STOP_LOSS", "TIMEOUT", "KILL_SWITCH", "EOD_SQUAREOFF"
