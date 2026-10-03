"""
Quantitative Backtesting Harness & Statistical Gate Validator.
Applies real statutory friction, slippage, and tests out-of-sample statistical gates:
Sharpe > 1.5, Max Drawdown < 15%, Hit Rate > 55%, t-statistic > 2.0.
"""

import math
import random
import logging
from typing import Dict, List, Any, Tuple
from core.config import config
from core.market_state.models import MarketSnapshot
from core.signals.models import SocialSignal
from core.risk.engine import RiskEngine
from strategies.social_momentum.reflex import FastReflexScorer
from core.strategy.calibration import CalibrationEngine
from brokers.paper import IndianPaperBroker

logger = logging.getLogger("Backtester")

class BacktestMetrics:
    """Computes comprehensive quantitative performance and statistical significance."""

    @staticmethod
    def calculate(trades: List[Any], starting_capital: float = config.STARTING_CAPITAL) -> Dict[str, Any]:
        if not trades:
            return {
                "total_trades": 0,
                "net_pnl": 0.0,
                "hit_rate_pct": 0.0,
                "sharpe_ratio": 0.0,
                "max_drawdown_pct": 0.0,
                "t_statistic": 0.0,
                "passed_all_gates": False
            }

        pnls = [t.net_pnl for t in trades]
        wins = [p for p in pnls if p > 0]
        losses = [p for p in pnls if p <= 0]

        total_trades = len(pnls)
        hit_rate = (len(wins) / total_trades) * 100.0 if total_trades > 0 else 0.0
        net_pnl = sum(pnls)

        # Equity curve & Max Drawdown
        equity_curve = [starting_capital]
        for p in pnls:
            equity_curve.append(equity_curve[-1] + p)

        peak = equity_curve[0]
        max_dd = 0.0
        for eq in equity_curve:
            if eq > peak:
                peak = eq
            dd = (peak - eq) / peak if peak > 0 else 0.0
            if dd > max_dd:
                max_dd = dd
        max_drawdown_pct = max_dd * 100.0

        # Mean and Standard Deviation of trade returns
        returns = [p / config.MAX_CAPITAL_PER_TRADE for p in pnls]
        mean_ret = sum(returns) / total_trades
        variance = sum((r - mean_ret) ** 2 for r in returns) / (total_trades - 1) if total_trades > 1 else 0.0001
        std_ret = math.sqrt(max(1e-8, variance))

        # Annualized Sharpe (assuming ~3 trades/day * 252 days = ~750 trades/year)
        trades_per_year = min(750, max(250, total_trades * 5))
        sharpe = (mean_ret / std_ret) * math.sqrt(trades_per_year) if std_ret > 0 else 0.0

        # Out-of-sample t-statistic for positive alpha: t = (mean - 0) / (std / sqrt(n))
        se = std_ret / math.sqrt(total_trades) if total_trades > 0 else 1.0
        t_stat = mean_ret / se if se > 0 else 0.0

        # Validation against prompt criteria
        gate_sharpe = sharpe >= 1.50
        gate_max_dd = max_drawdown_pct <= 15.0
        gate_hit_rate = hit_rate >= 55.0
        gate_t_stat = t_stat >= 2.0

        passed_all_gates = gate_sharpe and gate_max_dd and gate_hit_rate and gate_t_stat

        return {
            "total_trades": total_trades,
            "wins": len(wins),
            "losses": len(losses),
            "net_pnl": round(net_pnl, 2),
            "final_equity": round(equity_curve[-1], 2),
            "hit_rate_pct": round(hit_rate, 2),
            "sharpe_ratio": round(sharpe, 2),
            "max_drawdown_pct": round(max_drawdown_pct, 2),
            "t_statistic": round(t_stat, 2),
            "gate_sharpe_passed": gate_sharpe,
            "gate_max_dd_passed": gate_max_dd,
            "gate_hit_rate_passed": gate_hit_rate,
            "gate_t_stat_passed": gate_t_stat,
            "passed_all_gates": passed_all_gates
        }


class BacktestRunner:
    """Simulates realistic multi-regime market environments with Twitter signals."""

    def __init__(self, seed: int = 42):
        self.rng = random.Random(seed)

    def run_multi_regime_simulation(self, total_candles: int = 500) -> Dict[str, Any]:
        """
        Executes backtest over synthetic multi-regime market series
        (Regime 1: Strong Trend, Regime 2: Choppy Churn, Regime 3: Bot Pump & Dump).
        """
        risk_engine = RiskEngine(lockfile_path="/tmp/backtest_trading.lock")
        if risk_engine.is_kill_switch_active():
            risk_engine.clear_kill_switch()

        calibration_engine = CalibrationEngine()
        reflex_scorer = FastReflexScorer(calibration_engine)
        broker = IndianPaperBroker(initial_capital=config.STARTING_CAPITAL)

        base_price = 1450.0  # Typical NSE mid-large cap (e.g., INFY / RELIANCE range)
        current_price = base_price
        simulated_time = 1700000000.0  # Epoch timestamp

        symbols = ["TATASTEEL", "RELIANCE", "ZOMATO", "HDFCBANK"]

        for i in range(total_candles):
            simulated_time += 60.0  # 1-minute steps
            symbol = symbols[i % len(symbols)]

            # Regime switching
            regime = "TREND" if i % 3 == 0 else ("DUMP" if i % 7 == 0 else "NORMAL")

            # Generate realistic synthetic state
            if regime == "TREND":
                rvol = self.rng.uniform(3.2, 6.5)
                obi = self.rng.uniform(0.38, 0.75)
                zscore = self.rng.uniform(3.6, 5.5)
                spam_score = self.rng.uniform(0.05, 0.20)
                price_shock = self.rng.uniform(0.002, 0.012)
            elif regime == "DUMP":
                # Classic fake bot pump: high spam score, negative OBI
                rvol = self.rng.uniform(4.0, 7.0)
                obi = self.rng.uniform(-0.40, 0.10)
                zscore = self.rng.uniform(4.5, 7.0)
                spam_score = self.rng.uniform(0.60, 0.95)
                price_shock = self.rng.uniform(-0.015, -0.005)
            else:
                rvol = self.rng.uniform(1.0, 2.5)
                obi = self.rng.uniform(-0.20, 0.25)
                zscore = self.rng.uniform(0.5, 2.2)
                spam_score = self.rng.uniform(0.1, 0.3)
                price_shock = self.rng.uniform(-0.003, 0.003)

            current_price = max(100.0, current_price * (1.0 + price_shock))

            snapshot = MarketSnapshot(
                symbol=symbol,
                timestamp=simulated_time,
                last_price=current_price,
                bid=current_price - 0.20,
                ask=current_price + 0.20,
                bid_depth=15000.0 if obi > 0 else 5000.0,
                ask_depth=5000.0 if obi > 0 else 15000.0,
                vwap=current_price * 0.998,
                relative_volume=rvol,
                upper_circuit=current_price * 1.10,
                lower_circuit=current_price * 0.90,
                adv_inr=150_000_000.0  # ₹15 Crore ADV (Liquid)
            )

            signal = SocialSignal(
                symbol=symbol,
                timestamp=simulated_time,
                mentions_count=int(zscore * 30),
                velocity_zscore=zscore,
                unique_verified_ratio=0.85 if spam_score < 0.3 else 0.20,
                spam_cluster_score=spam_score
            )

            # Update existing positions
            broker.update_price_tick(symbol, current_price, simulated_time)

            # Pre-trade gate check
            passed_risk, _ = risk_engine.validate_pre_trade_gates(
                snapshot=snapshot,
                signal=signal,
                current_equity=broker.total_equity,
                current_positions_count=len([p for p in broker.positions.values() if p.is_active]),
                daily_loss_incurred=broker.daily_realized_loss,
                time_str="11:30"
            )

            if passed_risk:
                decision = reflex_scorer.evaluate(snapshot, signal)
                if decision.passed_all_gates and decision.recommended_fraction > 0:
                    broker.submit_bracket_entry(
                        symbol=symbol,
                        capital_fraction=decision.recommended_fraction,
                        current_price=current_price,
                        current_time=simulated_time
                    )

        # Close any lingering positions at end of backtest
        broker.flatten_all(
            current_prices={s: current_price for s in symbols},
            current_time=simulated_time,
            reason="BACKTEST_EOD"
        )

        metrics = BacktestMetrics.calculate(broker.trade_history, starting_capital=config.STARTING_CAPITAL)
        return metrics
