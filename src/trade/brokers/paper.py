"""
High-Fidelity Indian Paper Broker & Execution Simulator.
Accurately models NSE order matching, OCO brackets, slippage, and statutory taxes.
"""

import uuid
import logging
from typing import Dict, List, Optional
from trade.core.config import config
from trade.core.execution.models import Order
from trade.core.portfolio.models import Position
from trade.core.ledger.models import TradeResult
from trade.core.execution.charges import IndianTaxCalculator

logger = logging.getLogger("PaperBroker")

class IndianPaperBroker:
    """Deterministic paper execution environment for Indian Equities."""

    def __init__(self, initial_capital: float = config.STARTING_CAPITAL):
        self.cash: float = initial_capital
        self.positions: Dict[str, Position] = {}
        self.open_orders: Dict[str, Order] = {}
        self.trade_history: List[TradeResult] = []
        self.daily_realized_loss: float = 0.0
        self.daily_realized_pnl: float = 0.0

    @property
    def total_equity(self) -> float:
        """Returns total portfolio NAV including cash and unrealized P&L."""
        unrealized = sum(pos.gross_unrealized_pnl for pos in self.positions.values() if pos.is_active)
        return self.cash + unrealized

    def submit_bracket_entry(
        self,
        symbol: str,
        capital_fraction: float,
        current_price: float,
        current_time: float
    ) -> Optional[Order]:
        """
        Submits atomic entry order and registers OCO take-profit / stop-loss bracket.
        """
        if symbol in self.positions and self.positions[symbol].is_active:
            logger.warning(f"Position already active for {symbol}, rejecting duplicate order.")
            return None

        # Sizing: Cap to MAX_CAPITAL_PER_TRADE (₹20,000)
        allocated_capital = min(config.MAX_CAPITAL_PER_TRADE, self.cash * capital_fraction)
        if allocated_capital < 1000.0 or current_price <= 0:
            logger.warning(f"Insufficient allocation: ₹{allocated_capital:.2f} or invalid price {current_price}")
            return None

        quantity = int(allocated_capital / current_price)
        if quantity <= 0:
            logger.warning(f"Computed quantity is 0 for price ₹{current_price}")
            return None

        # Realistic Slippage on entry (0.05% typical on liquid NSE stocks)
        slippage = current_price * 0.0005
        fill_price = current_price + slippage

        order_id = str(uuid.uuid4())
        order = Order(
            client_order_id=order_id,
            symbol=symbol,
            side="BUY",
            quantity=quantity,
            order_type="LIMIT",
            price=fill_price,
            created_at=current_time,
            status="FILLED"
        )

        target_price = round(fill_price * (1.0 + config.TARGET_PROFIT_PCT), 2)
        stop_price = round(fill_price * (1.0 - config.STOP_LOSS_PCT), 2)

        self.positions[symbol] = Position(
            symbol=symbol,
            entry_price=fill_price,
            quantity=quantity,
            entry_time=current_time,
            target_price=target_price,
            stop_loss_price=stop_price,
            current_price=fill_price,
            is_active=True
        )

        logger.info(
            f"ENTRY FILLED: {symbol} Qty={quantity} @ ₹{fill_price:.2f} | "
            f"Target(+1%)=₹{target_price:.2f} | Stop(-0.7%)=₹{stop_price:.2f}"
        )
        return order

    def update_price_tick(
        self,
        symbol: str,
        tick_price: float,
        tick_time: float
    ) -> Optional[TradeResult]:
        """
        Evaluates active OCO brackets and timeouts on each price update.
        """
        if symbol not in self.positions or not self.positions[symbol].is_active:
            return None

        pos = self.positions[symbol]
        pos.current_price = tick_price

        exit_reason = None
        exit_price = tick_price

        # 1. Take Profit hit (+1.00%)
        if tick_price >= pos.target_price:
            exit_reason = "TAKE_PROFIT"
            exit_price = pos.target_price

        # 2. Hard Stop Loss hit (-0.70%)
        elif tick_price <= pos.stop_loss_price:
            exit_reason = "STOP_LOSS"
            # Simulate adverse execution slippage on stop-loss
            exit_price = pos.stop_loss_price * 0.9995

        # 3. Time Invalidation (35 mins elapsed without hitting TP/SL)
        elif (tick_time - pos.entry_time) >= (config.MAX_HOLDING_MINUTES * 60):
            exit_reason = "TIMEOUT"
            exit_price = tick_price

        if exit_reason:
            return self._close_position(pos, exit_price, tick_time, exit_reason)

        return None

    def _close_position(
        self,
        pos: Position,
        exit_price: float,
        exit_time: float,
        reason: str
    ) -> TradeResult:
        """Executes position exit and applies Indian statutory charges."""
        charges = IndianTaxCalculator.calculate_charges(pos.entry_price, exit_price, pos.quantity)
        
        pos.is_active = False
        net_pnl = charges["net_pnl"]
        self.cash += (pos.entry_price * pos.quantity) + net_pnl
        self.daily_realized_pnl += net_pnl

        if net_pnl < 0:
            self.daily_realized_loss += abs(net_pnl)

        trade = TradeResult(
            symbol=pos.symbol,
            entry_price=pos.entry_price,
            exit_price=exit_price,
            quantity=pos.quantity,
            entry_time=pos.entry_time,
            exit_time=exit_time,
            gross_pnl=charges["gross_pnl"],
            total_statutory_charges=charges["total_charges"],
            net_pnl=net_pnl,
            exit_reason=reason
        )
        self.trade_history.append(trade)

        logger.info(
            f"EXIT [{reason}]: {pos.symbol} Qty={pos.quantity} @ ₹{exit_price:.2f} | "
            f"Gross=₹{trade.gross_pnl:+.2f} Charges=₹{trade.total_statutory_charges:.2f} Net=₹{trade.net_pnl:+.2f}"
        )
        return trade

    def flatten_all(self, current_prices: Dict[str, float], current_time: float, reason: str = "KILL_SWITCH") -> List[TradeResult]:
        """Emergency or EOD flattening of all open exposure."""
        closed_trades = []
        for symbol, pos in list(self.positions.items()):
            if pos.is_active:
                exit_price = current_prices.get(symbol, pos.current_price)
                trade = self._close_position(pos, exit_price, current_time, reason)
                closed_trades.append(trade)
        return closed_trades
