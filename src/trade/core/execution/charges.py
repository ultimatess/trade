"""
Indian Equity Statutory Charges & Taxation Calculator (NSE Intraday MIS).
Computes exact brokerage, STT, Exchange transaction charges, SEBI fees, Stamp Duty, and GST.
"""

from typing import Dict
from trade.core.config import config

class IndianTaxCalculator:
    """Accurately calculates real-world transaction friction on Indian markets."""

    @staticmethod
    def calculate_charges(buy_price: float, sell_price: float, quantity: int) -> Dict[str, float]:
        turnover_buy = buy_price * quantity
        turnover_sell = sell_price * quantity
        total_turnover = turnover_buy + turnover_sell

        # 1. Brokerage: min(flat ₹20, 0.03% of leg turnover) per executed leg
        brokerage = min(config.BROKERAGE_PER_ORDER, turnover_buy * config.BROKERAGE_PER_ORDER_PCT) + \
                    min(config.BROKERAGE_PER_ORDER, turnover_sell * config.BROKERAGE_PER_ORDER_PCT)

        # 2. STT: 0.025% on sell turnover for equity intraday
        stt = turnover_sell * config.STT_SELL_PCT

        # 3. Exchange Turnover Charges: 0.00297% on NSE
        exchange_charges = total_turnover * config.EXCHANGE_TURNOVER_PCT

        # 4. SEBI Charges: ₹10 / crore (0.0001%)
        sebi_charges = total_turnover * config.SEBI_PCT

        # 5. Stamp Duty: 0.003% on buy turnover only
        stamp_duty = turnover_buy * config.STAMP_DUTY_BUY_PCT

        # 6. GST: 18% on (Brokerage + Exchange Charges + SEBI Charges)
        taxable_services = brokerage + exchange_charges + sebi_charges
        gst = taxable_services * config.GST_PCT

        total_charges = brokerage + stt + exchange_charges + sebi_charges + stamp_duty + gst
        gross_pnl = (sell_price - buy_price) * quantity
        net_pnl = gross_pnl - total_charges

        return {
            "turnover_buy": turnover_buy,
            "turnover_sell": turnover_sell,
            "brokerage": round(brokerage, 2),
            "stt": round(stt, 2),
            "exchange_charges": round(exchange_charges, 2),
            "sebi_charges": round(sebi_charges, 4),
            "stamp_duty": round(stamp_duty, 2),
            "gst": round(gst, 2),
            "total_charges": round(total_charges, 2),
            "gross_pnl": round(gross_pnl, 2),
            "net_pnl": round(net_pnl, 2),
            "net_roi_pct": round((net_pnl / turnover_buy) * 100, 3) if turnover_buy > 0 else 0.0
        }
