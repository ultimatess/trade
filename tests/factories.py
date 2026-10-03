"""Small builders for test inputs (snapshots, signals, intents, portfolio views)."""

from trade.core.execution.intent import OrderIntent
from trade.core.market_state.models import MarketSnapshot
from trade.core.portfolio.view import PortfolioView
from trade.core.signals.models import SocialSignal


def snapshot(symbol="INFY", ts=1000.0, price=1600.0, **kw):
    base = dict(
        symbol=symbol,
        timestamp=ts,
        last_price=price,
        bid=price - 0.2,
        ask=price + 0.2,
        bid_depth=30000,
        ask_depth=8000,
        vwap=price * 0.997,
        relative_volume=4.5,
        upper_circuit=price * 1.1,
        lower_circuit=price * 0.9,
        adv_inr=3e8,
    )
    return MarketSnapshot(**{**base, **kw})


def social(symbol="INFY", ts=1000.0, **kw):
    base = dict(
        symbol=symbol,
        timestamp=ts,
        mentions_count=60,
        velocity_zscore=4.2,
        unique_verified_ratio=0.9,
        spam_cluster_score=0.05,
    )
    return SocialSignal(**{**base, **kw})


def intent(symbol="INFY", quantity=10, price=1600.0, **kw):
    base = dict(
        signal_id="sig",
        strategy_id="social_momentum",
        strategy_version=1,
        symbol=symbol,
        side="BUY",
        quantity=quantity,
        reference_price=price,
        take_profit_pct=0.01,
        stop_loss_pct=0.007,
        max_holding_s=2100,
        sizing_method="test",
        created_at=1000.0,
    )
    return OrderIntent(**{**base, **kw})


def portfolio(cash=100_000.0, equity=None, open_symbols=(), daily_loss=0.0, as_of=1000.0):
    return PortfolioView(
        as_of=as_of,
        cash=cash,
        equity=cash if equity is None else equity,
        open_positions=len(open_symbols),
        open_symbols=frozenset(open_symbols),
        daily_realized_loss=daily_loss,
    )
