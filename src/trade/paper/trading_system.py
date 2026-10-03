"""
Paper-trading loop: ingestion, Strategy #001 through the shared decision pipeline,
deterministic risk approval, and paper execution with brackets.
"""

import logging
import time

from trade.brokers.paper import IndianPaperBroker
from trade.core.config import config
from trade.core.market_state.models import MarketSnapshot
from trade.core.pipeline import DecisionPipeline
from trade.core.risk.engine import RiskEngine
from trade.core.strategy.calibration import CalibrationEngine
from trade.core.strategy.contract import Observation
from trade.data.providers.social import SocialMomentumScanner
from trade.strategies.social_momentum.v1.strategy import SocialMomentumV1

# Setup clean structured logging
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s", datefmt="%H:%M:%S")
logger = logging.getLogger("QuantLoop")


class QuantTradingSystem:
    """Master orchestrator implementing the 3-layer quant execution loop."""

    def __init__(self) -> None:
        self.risk_engine = RiskEngine()
        self.calibration_engine = CalibrationEngine()
        self.strategy = SocialMomentumV1()
        self.social_scanner = SocialMomentumScanner()
        self.broker = IndianPaperBroker(initial_capital=config.runtime.starting_capital_inr)
        self.is_running = True

    def run_single_cycle(
        self,
        symbol: str,
        current_price: float,
        bid_depth: float,
        ask_depth: float,
        relative_volume: float,
        adv_inr: float,
        sample_tweets: list[str],
        simulated_time_str: str = "11:00",
        timestamp: float | None = None,
    ) -> None:
        """
        Executes one full iteration of the deterministic trading loop.
        """
        ts = timestamp or time.time()
        logger.info(f"--- Cycle Iteration: {symbol} @ ₹{current_price:.2f} ({simulated_time_str} IST) ---")

        # Step 1: EOD Mandatory Square-Off Check
        if simulated_time_str >= config.risk.mandatory_squareoff:
            logger.warning("15:10 IST MANDATORY EOD SQUARE-OFF REACHED. Flattening open exposure.")
            signal_ids = {s: self.broker.signal_id_for(s) for s in self.broker.positions}
            for trade in self.broker.flatten_all(
                current_prices={symbol: current_price}, current_time=ts, reason="EOD_SQUAREOFF"
            ):
                self.calibration_engine.resolve(signal_ids.get(trade.symbol), trade.net_pnl)
            return

        # Step 2: Check Active Positions & Update Trailing Brackets
        signal_id = self.broker.signal_id_for(symbol)
        exit_result = self.broker.update_price_tick(symbol, current_price, ts)
        if exit_result:
            # Pair the exit with the forecast made at entry (D-002)
            self.calibration_engine.resolve(signal_id, exit_result.net_pnl)
            logger.info(f"Position Closed. Brier Score updated: {self.calibration_engine.compute_brier_score():.4f}")

        # Step 3: Ingest Social Sentiment (Extract pure numeric signal)
        signal = self.social_scanner.sanitize_and_extract_signal(symbol, sample_tweets, ts)

        # Step 4: Build Compact Numeric Snapshot (Strict timestamp prior)
        spread = current_price * 0.0008  # 0.08% spread (well within 0.15% gate)
        snapshot = MarketSnapshot(
            symbol=symbol,
            timestamp=ts,
            last_price=current_price,
            bid=round(current_price - (spread / 2), 2),
            ask=round(current_price + (spread / 2), 2),
            bid_depth=bid_depth,
            ask_depth=ask_depth,
            vwap=round(current_price * 0.999, 2),
            relative_volume=relative_volume,
            upper_circuit=round(current_price * 1.10, 2),
            lower_circuit=round(current_price * 0.90, 2),
            adv_inr=adv_inr,
        )

        # Step 5: Strategy -> Signal -> Sizer -> RiskEngine (authoritative) -> Broker
        obs = Observation(symbol=symbol, as_of=ts, session_time=simulated_time_str, market=snapshot, social=signal)
        pipeline = DecisionPipeline(self.strategy, self.risk_engine)
        result = pipeline.process(obs, self.broker.portfolio_view(ts), self.broker)
        d = result.decision.diagnostics if result.decision else {}
        if d:
            logger.info(
                f"STRATEGY {self.strategy.strategy_id} v{self.strategy.version}: P(Organic)={d['p_organic']:.2f} | "
                f"P(Win) score (uncalibrated)={d['p_win_calibrated']:.2f} | Quality={d['setup_quality']:.1f}"
            )
        if result.step == "ORDER_FILLED" and result.decision:
            filled = {i.signal_id for i in result.intents}
            for sig in result.decision.signals:
                if sig.signal_id in filled and sig.confidence is not None:
                    self.calibration_engine.register_forecast(sig.signal_id, sig.confidence)
            for order in result.orders:
                logger.info(f"ORDER DISPATCHED: ClientOrderID={order.client_order_id} Symbol={symbol}")
        else:
            logger.info(f"NO ORDER [{result.step}]: {list(result.reason_codes)}")
