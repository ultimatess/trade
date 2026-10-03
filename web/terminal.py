"""
Real-time Terminal Monitoring Dashboard.
Displays Live NAV, Open Positions, Brier Calibration, and Kill Switch Status.
"""

from datetime import datetime
from core.config import config
from core.risk.engine import RiskEngine
from brokers.paper import IndianPaperBroker

class TerminalDashboard:
    """Renders high-clarity status reports for the trading system."""

    @staticmethod
    def render(broker: IndianPaperBroker, risk_engine: RiskEngine, brier_score: float) -> str:
        now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        kill_active = risk_engine.is_kill_switch_active()
        status_banner = "🛑 HALTED (KILL SWITCH ACTIVE)" if kill_active else "🟢 ACTIVE & ARMED"

        output = []
        output.append("=" * 72)
        output.append(f"  QUANT TRADING SYSTEM DASHBOARD (NSE INTRADAY)  [{now_str}]")
        output.append("=" * 72)
        output.append(f"  SYSTEM STATUS      : {status_banner}")
        output.append(f"  PORTFOLIO NAV      : ₹{broker.total_equity:,.2f} (Starting: ₹{config.STARTING_CAPITAL:,.2f})")
        output.append(f"  AVAILABLE CASH     : ₹{broker.cash:,.2f}")
        output.append(f"  DAILY REALIZED P&L : ₹{broker.daily_realized_pnl:+,.2f}")
        output.append(f"  DAILY LOSS INCURRED: ₹{broker.daily_realized_loss:,.2f} / Max ₹{config.MAX_DAILY_LOSS:,.2f}")
        output.append(f"  CALIBRATION (BRIER): {brier_score:.4f} (Lower = More Accurate)")
        output.append("-" * 72)

        output.append("  ACTIVE POSITIONS:")
        active_positions = [p for p in broker.positions.values() if p.is_active]
        if not active_positions:
            output.append("    (No open exposure. Cash protected.)")
        else:
            for p in active_positions:
                output.append(
                    f"    • {p.symbol}: Qty {p.quantity} @ Entry ₹{p.entry_price:.2f} | "
                    f"Target(+1%) ₹{p.target_price:.2f} | Stop(-0.7%) ₹{p.stop_loss_price:.2f} | "
                    f"Unrealized: ₹{p.gross_unrealized_pnl:+,.2f} ({p.pnl_pct:+.2%})"
                )

        output.append("-" * 72)
        output.append(f"  RECENT EXECUTIONS ({len(broker.trade_history)} total):")
        for t in broker.trade_history[-5:]:
            output.append(
                f"    • [{t.exit_reason}] {t.symbol} | Net P&L: ₹{t.net_pnl:+,.2f} | "
                f"Statutory Charges: ₹{t.total_statutory_charges:.2f}"
            )
        output.append("=" * 72)
        return "\n".join(output)

if __name__ == "__main__":
    broker = IndianPaperBroker()
    risk = RiskEngine()
    print(TerminalDashboard.render(broker, risk, 0.124))
