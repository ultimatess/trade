"""
Historical Backtest Runner & Gate Verification Script.
Executes multi-regime market simulation and verifies statistical criteria.
"""

from backtesting.engine import BacktestRunner

def main():
    print("=" * 75)
    print("RUNNING 2-YEAR MULTI-REGIME HISTORICAL BACKTEST GATE")
    print("Gates: Sharpe > 1.5 | Max Drawdown < 15% | Hit Rate > 55% | t-stat > 2.0")
    print("=" * 75)

    runner = BacktestRunner(seed=42)
    metrics = runner.run_multi_regime_simulation(total_candles=500)

    print(f"Total Completed Trades : {metrics['total_trades']}")
    print(f"Wins / Losses          : {metrics['wins']} W / {metrics['losses']} L")
    print(f"Hit Rate               : {metrics['hit_rate_pct']:.2f}%  (Gate: >= 55.0%) -> {'PASS' if metrics['gate_hit_rate_passed'] else 'FAIL'}")
    print(f"Net Realized P&L       : ₹{metrics['net_pnl']:+,.2f} (After all Indian statutory charges)")
    print(f"Ending Equity          : ₹{metrics['final_equity']:,.2f}")
    print(f"Sharpe Ratio           : {metrics['sharpe_ratio']:.2f}  (Gate: >= 1.50)  -> {'PASS' if metrics['gate_sharpe_passed'] else 'FAIL'}")
    print(f"Max Drawdown           : {metrics['max_drawdown_pct']:.2f}%  (Gate: <= 15.0%) -> {'PASS' if metrics['gate_max_dd_passed'] else 'FAIL'}")
    print(f"Out-of-Sample t-stat   : {metrics['t_statistic']:.2f}  (Gate: >= 2.00)  -> {'PASS' if metrics['gate_t_stat_passed'] else 'FAIL'}")
    print("-" * 75)
    print(f"OVERALL BACKTEST GATE STATUS: {'PASSED (READY FOR PAPER TRADING)' if metrics['passed_all_gates'] else 'REJECTED'}")
    print("=" * 75)

if __name__ == "__main__":
    main()
