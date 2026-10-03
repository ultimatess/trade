"""
Execution models: idempotent order definitions.
"""

from dataclasses import dataclass, field
import time


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
