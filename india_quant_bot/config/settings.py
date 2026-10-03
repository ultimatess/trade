"""
Global System Configuration for Indian Equities (NSE) Scalping System.
Strictly hardcodes quantitative risk limits and statutory friction parameters.
"""

import os
from dataclasses import dataclass
from typing import Dict, Any

@dataclass(frozen=True)
class TradingConfig:
    # Capital & Sizing (INR)
    STARTING_CAPITAL: float = 100_000.0        # ₹1 Lakh
    MAX_CAPITAL_PER_TRADE: float = 20_000.0    # ₹20,000 (20% of NAV)
    MAX_CONCURRENT_POSITIONS: int = 2          # Max ₹40,000 total exposure
    
    # Target and Risk Ratios
    TARGET_PROFIT_PCT: float = 0.010           # +1.00% Take Profit
    STOP_LOSS_PCT: float = 0.007               # -0.70% Hard Stop Loss
    MAX_HOLDING_MINUTES: int = 35              # Invalidation timeout
    
    # Portfolio-Level Hard Stops
    MAX_DAILY_LOSS: float = 2_000.0            # ₹2,000 (2.0% daily kill limit)
    MAX_ACCOUNT_DRAWDOWN_PCT: float = 0.08     # 8% max drawdown before freeze
    
    # Market Microstructure Gates
    MAX_SPREAD_PCT: float = 0.0015             # 0.15% maximum bid-ask spread
    CIRCUIT_BUFFER_PCT: float = 0.015          # Must be 1.5% away from Upper/Lower circuits
    MIN_ADV_INR: float = 100_000_000.0         # ₹10 Crore 20-day ADV
    MIN_ORDER_BOOK_IMBALANCE: float = 0.35     # OBI >= +0.35 (buyer dominated)
    MIN_RELATIVE_VOLUME: float = 3.0           # 3x volume surge on 1m bar
    
    # Decision Reflex Gates
    MIN_CALIBRATED_PROBABILITY: float = 0.65   # Fast reflex win probability threshold
    MIN_QUALITY_SCORE: float = 75.0            # Setup quality threshold (0-100)
    
    # Timing Constraints (IST)
    MARKET_START_ENTRY: str = "09:30"          # Settling period passed
    MARKET_STOP_ENTRY: str = "14:30"           # No new entries late afternoon
    MANDATORY_SQUAREOFF: str = "15:10"         # EOD square-off before broker MIS penalty
    
    # Indian NSE Intraday Statutory Charges
    BROKERAGE_PER_ORDER: float = 20.0          # Flat ₹20 discount brokerage
    STT_SELL_PCT: float = 0.00025              # 0.025% on sell turnover
    EXCHANGE_TURNOVER_PCT: float = 0.0000297   # 0.00297% NSE
    SEBI_PCT: float = 0.000001                 # ₹10 per crore
    STAMP_DUTY_BUY_PCT: float = 0.00003        # 0.003% on buy turnover
    GST_PCT: float = 0.18                      # 18% on (Brokerage + Txn + SEBI)

    # Broker Backend
    BROKER_TYPE: str = os.getenv("BROKER_TYPE", "PAPER")  # "PAPER" or "DHAN"
    LOCKFILE_PATH: str = "trading.lock"

config = TradingConfig()
