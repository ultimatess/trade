"""
Indian paper broker (in-memory simulator).
Fills entries at price + 0.05%, evaluates take-profit / stop / timeout on each
tick, and applies statutory charges. No order book, queue, latency, partial
fills or gaps. Long-only, cash-funded (no leverage).
"""

import logging
import uuid
from datetime import date, datetime
from typing import Any
from zoneinfo import ZoneInfo

from trade.core.config import config
from trade.core.execution.charges import IndianTaxCalculator
from trade.core.execution.intent import OrderIntent
from trade.core.execution.models import Order
from trade.core.ledger.models import TradeResult
from trade.core.portfolio.models import Position
from trade.core.portfolio.view import PortfolioView

logger = logging.getLogger("PaperBroker")
IST = ZoneInfo("Asia/Kolkata")


class IndianPaperBroker:
    """Deterministic paper execution environment for Indian Equities."""

    def __init__(self, initial_capital: float = config.runtime.starting_capital_inr):
        self.cash: float = initial_capital
        self.positions: dict[str, Position] = {}
        self.open_orders: dict[str, Order] = {}
        self.trade_history: list[TradeResult] = []
        self.position_meta: dict[str, dict[str, Any]] = {}
        self.session_date: date | None = None
        self.day_start_equity: float = initial_capital
        self.high_water_mark: float = initial_capital
        self.daily_realized_loss: float = 0.0
        self.daily_realized_pnl: float = 0.0

    @property
    def total_equity(self) -> float:
        """NAV = cash + market value of open positions (charges are deducted when a position closes)."""
        market_value = sum(pos.current_price * pos.quantity for pos in self.positions.values() if pos.is_active)
        return self.cash + market_value

    def signal_id_for(self, symbol: str) -> str | None:
        """Signal that opened the current/last position in `symbol` (for forecast/outcome pairing)."""
        meta = self.position_meta.get(symbol)
        return str(meta["signal_id"]) if meta else None

    def roll_session(self, as_of: float) -> None:
        """Start a new IST trading day: reset daily counters and the day's starting equity (D-006)."""
        day = datetime.fromtimestamp(as_of, IST).date()
        if day != self.session_date:
            self.session_date = day
            self.day_start_equity = self.total_equity
            self.daily_realized_loss = 0.0
            self.daily_realized_pnl = 0.0

    def portfolio_view(self, as_of: float) -> PortfolioView:
        self.roll_session(as_of)
        equity = self.total_equity
        self.high_water_mark = max(self.high_water_mark, equity)
        active = [p for p in self.positions.values() if p.is_active]
        return PortfolioView(
            as_of=as_of,
            cash=self.cash,
            equity=equity,
            open_positions=len(active),
            open_symbols=frozenset(p.symbol for p in active),
            daily_realized_loss=self.daily_realized_loss,
            day_start_equity=self.day_start_equity,
            high_water_mark=self.high_water_mark,
        )

    def submit_intent(self, intent: OrderIntent, current_price: float, current_time: float) -> Order | None:
        """Fills a risk-approved intent immediately and registers its bracket (anchored to the fill price)."""
        if intent.symbol in self.positions and self.positions[intent.symbol].is_active:
            logger.warning(f"Position already active for {intent.symbol}, rejecting duplicate order.")
            return None
        if intent.side != "BUY" or intent.quantity <= 0 or current_price <= 0:
            logger.warning(f"Unsupported or invalid intent for {intent.symbol}: {intent.side} x{intent.quantity}")
            return None

        quantity = intent.quantity
        # Fixed entry slippage of 0.05%
        slippage = current_price * 0.0005
        fill_price = current_price + slippage
        if fill_price * quantity > self.cash:
            logger.warning(f"Insufficient cash for {intent.symbol}: need ₹{fill_price * quantity:,.2f}")
            return None
        self.cash -= fill_price * quantity  # D-001: entry notional is debited

        order = Order(
            client_order_id=str(uuid.uuid4()),
            symbol=intent.symbol,
            side="BUY",
            quantity=quantity,
            order_type="LIMIT",
            price=fill_price,
            created_at=current_time,
            status="FILLED",
        )

        target_price = round(fill_price * (1.0 + intent.take_profit_pct), 2)
        stop_price = round(fill_price * (1.0 - intent.stop_loss_pct), 2)

        self.positions[intent.symbol] = Position(
            symbol=intent.symbol,
            entry_price=fill_price,
            quantity=quantity,
            entry_time=current_time,
            target_price=target_price,
            stop_loss_price=stop_price,
            current_price=fill_price,
            is_active=True,
        )
        self.position_meta[intent.symbol] = {
            "strategy_id": intent.strategy_id,
            "strategy_version": intent.strategy_version,
            "signal_id": intent.signal_id,
            "max_holding_s": intent.max_holding_s,
        }

        logger.info(
            f"ENTRY FILLED: {intent.symbol} Qty={quantity} @ ₹{fill_price:.2f} | "
            f"Target=₹{target_price:.2f} | Stop=₹{stop_price:.2f} | {intent.strategy_id} v{intent.strategy_version}"
        )
        return order

    def update_price_tick(self, symbol: str, tick_price: float, tick_time: float) -> TradeResult | None:
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
        elif (tick_time - pos.entry_time) >= self.position_meta[symbol]["max_holding_s"]:
            exit_reason = "TIMEOUT"
            exit_price = tick_price

        if exit_reason:
            return self._close_position(pos, exit_price, tick_time, exit_reason)

        return None

    def _close_position(self, pos: Position, exit_price: float, exit_time: float, reason: str) -> TradeResult:
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
            exit_reason=reason,
        )
        self.trade_history.append(trade)

        logger.info(
            f"EXIT [{reason}]: {pos.symbol} Qty={pos.quantity} @ ₹{exit_price:.2f} | "
            f"Gross=₹{trade.gross_pnl:+.2f} Charges=₹{trade.total_statutory_charges:.2f} Net=₹{trade.net_pnl:+.2f}"
        )
        return trade

    def flatten_all(
        self, current_prices: dict[str, float], current_time: float, reason: str = "KILL_SWITCH"
    ) -> list[TradeResult]:
        """Emergency or EOD flattening of all open exposure."""
        closed_trades = []
        for symbol, pos in list(self.positions.items()):
            if pos.is_active:
                exit_price = current_prices.get(symbol, pos.current_price)
                trade = self._close_position(pos, exit_price, current_time, reason)
                closed_trades.append(trade)
        return closed_trades
