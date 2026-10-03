"""
Main Deterministic Execution Loop for Indian Quant Bot.
Coordinates Ingestion, Deterministic Risk Vetoes, Fast Reflex Scoring,
and Idempotent OCO Bracket Order Routing.
"""

import logging
import time

from trade.brokers.paper import IndianPaperBroker
from trade.core.config import config
from trade.core.market_state.models import MarketSnapshot
from trade.core.risk.engine import RiskEngine
from trade.core.strategy.calibration import CalibrationEngine
from trade.data.providers.social import SocialMomentumScanner
from trade.strategies.social_momentum.reflex import FastReflexScorer

# Setup clean structured logging
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s", datefmt="%H:%M:%S")
logger = logging.getLogger("QuantLoop")


class QuantTradingSystem:
    """Master orchestrator implementing the 3-layer quant execution loop."""

    def __init__(self) -> None:
        self.risk_engine = RiskEngine()
        self.calibration_engine = CalibrationEngine()
        self.reflex_scorer = FastReflexScorer(self.calibration_engine)
        self.social_scanner = SocialMomentumScanner()
        self.broker = IndianPaperBroker(initial_capital=config.STARTING_CAPITAL)
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
        if simulated_time_str >= config.MANDATORY_SQUAREOFF:
            logger.warning("15:10 IST MANDATORY EOD SQUARE-OFF REACHED. Flattening open exposure.")
            self.broker.flatten_all(current_prices={symbol: current_price}, current_time=ts, reason="EOD_SQUAREOFF")
            return

        # Step 2: Check Active Positions & Update Trailing Brackets
        exit_result = self.broker.update_price_tick(symbol, current_price, ts)
        if exit_result:
            # Feed back result to calibration engine
            outcome = 1 if exit_result.net_pnl > 0 else 0
            self.calibration_engine.record_outcome(config.MIN_CALIBRATED_PROBABILITY, outcome)
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

        # Step 5: Deterministic Pre-Trade Risk Gate Validation (Layer 3 Veto)
        active_pos_count = len([p for p in self.broker.positions.values() if p.is_active])
        passed_risk, vetoes = self.risk_engine.validate_pre_trade_gates(
            snapshot=snapshot,
            signal=signal,
            current_equity=self.broker.total_equity,
            current_positions_count=active_pos_count,
            daily_loss_incurred=self.broker.daily_realized_loss,
            time_str=simulated_time_str,
        )

        if not passed_risk:
            logger.info(f"RISK VETO: Trade blocked by deterministic rules. Reasons: {vetoes}")
            return

        # Step 6: Fast Reflex Scoring (Layer 2)
        decision = self.reflex_scorer.evaluate(snapshot, signal)
        logger.info(
            f"REFLEX SCORING: P(Organic)={decision.p_organic:.2f} | "
            f"P(Win Calibrated)={decision.p_win_calibrated:.2f} | "
            f"Quality={decision.setup_quality:.1f} | Recommended Kelly Sizing={decision.recommended_fraction:.2%}"
        )

        if not decision.passed_all_gates:
            logger.info(f"REFLEX VETO: Setup did not meet quality thresholds. Reasons: {decision.veto_reasons}")
            return

        # Step 7: Order Execution (Layer 3 - Idempotent OCO Bracket)
        order = self.broker.submit_bracket_entry(
            symbol=symbol, capital_fraction=decision.recommended_fraction, current_price=current_price, current_time=ts
        )

        if order:
            logger.info(f"ORDER DISPATCHED: ClientOrderID={order.client_order_id} Symbol={symbol}")
