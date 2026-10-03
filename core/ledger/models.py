"""
Ledger models: final executed trade records.
"""

from dataclasses import dataclass


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
