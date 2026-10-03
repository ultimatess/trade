# Strategy Specification: Social Momentum 1% Scalp (NSE Intraday MIS)

## 1. Executive Summary
- **Asset Universe:** NSE Nifty 500 / Liquid Cash Equities with 20-day Average Daily Volume (ADV) > ₹10 Crore.
- **Capital Allocation:** ₹1,00,000 Total Account Balance.
- **Trade Sizing:** ₹20,000 per position (20% NAV cash / 1x MIS allocation, max 2 concurrent positions).
- **Core Strategy:** Capitalize on early structural momentum when social mention acceleration coincides with order book buying depth and relative volume spikes.

---

## 2. Quantitative Rules & Gate Definitions

### A. Pre-Trade Filters (Deterministic Gate 0)
1. **Trading Window Check:**
   - Active Entry Window: `09:30 AM IST – 02:30 PM IST`
   - No new entries permitted before 09:30 AM (opening volatility settling period) or after 02:30 PM.
   - Mandatory EOD Square-Off: `03:10 PM IST` (flattens all positions to avoid broker penalty fees).
2. **Circuit Limit Proximity Gate:**
   - Current Price must be at least **1.5% away** from both Upper Circuit (UC) and Lower Circuit (LC).
   - *Rationale:* Entering near Upper Circuit risks immediate rejection; entering near Lower Circuit risks zero-buyer bid evaporation.
3. **Spread & Liquidity Gate:**
   - Maximum Bid-Ask Spread $\le 0.15\%$ of current price.
   - 20-day Average Daily Volume $\ge ₹10,00,00,000$ (₹10 Crore).

### B. Signal Generation & Fast Reflex Scoring (Layer 2)
1. **Social Velocity Trigger:**
   - Cashtag mention velocity acceleration $\ge 3.5\sigma$ over 24h baseline.
   - Deduped tweet entropy check (filters out bot spam copy-paste).
2. **Volume Breakout Confirmation:**
   - Current 1-minute candle volume $\ge 3.0\times$ 20-period moving average volume ($V_{\text{rel}} \ge 3.0$).
3. **Order Book Depth Imbalance (OBI):**
   $$\text{OBI} = \frac{\text{Bid Depth} - \text{Ask Depth}}{\text{Bid Depth} + \text{Ask Depth}} \ge +0.35$$
4. **Fast Reflex Probabilistic Thresholds:**
   - $P(\text{Organic Momentum}) \ge 0.70$
   - $P(\text{Hit } +1.0\% \text{ before } -0.7\%) \ge 0.65$
   - Setup Quality Score $\ge 75 / 100$

### C. Entry & Execution Protocol (Layer 3)
- **Order Type:** Limit Order at National Best Bid (NBB) or Limit at Current Ask with maximum 0.05% slippage cap.
- **Atomic Bracket:** Every fill immediately creates an atomic OCO (One-Cancels-Other) bracket:
  - **Take Profit (TP):** Fill Price $+ 1.00\%$
  - **Hard Stop Loss (SL):** Fill Price $- 0.70\%$
- **Time Invalidation (Stale Exit):**
  - If neither TP nor SL is touched within **35 minutes**, the position is closed via market order to prevent holding stagnant pump decay.

---

## 3. Financial & Friction Math (NSE Intraday MIS)

| Parameter | Value | Notes |
| :--- | :--- | :--- |
| **Position Size** | ₹20,000 | 20% of ₹1 Lakh capital |
| **Gross TP (+1.00%)** | +₹200.00 | Targeted move |
| **Gross SL (-0.70%)** | -₹140.00 | Hard risk barrier |
| **Roundtrip Brokerage** | ₹40.00 | ₹20 buy + ₹20 sell (Dhan / Zerodha / Fyers) |
| **STT (Securities Tax)** | ₹5.00 | 0.025% on sell turnover |
| **Exchange & SEBI Fees**| ₹1.24 | NSE turnover + SEBI charges |
| **Stamp Duty & GST** | ₹8.02 | 0.003% buy + 18% GST on brokerage |
| **Total Roundtrip Friction** | **₹54.26** | **0.27% of trade capital** |
| **Net Profit on Win** | **+₹145.74 (+0.73%)** | Win payoff |
| **Net Loss on Stop** | **-₹194.26 (-0.97%)** | Loss payoff |
| **Required Breakeven Win Rate** | **57.13%** | Reflex model enforces $\ge 65.0\%$ gate |

---

## 4. Hard Risk Limits & Kill Switch
- **Max Concurrent Positions:** 2 positions (Max portfolio exposure ₹40,000).
- **Daily Loss Limit:** **-₹2,000 INR** (-2.0% of portfolio).
  - When cumulative daily realized + unrealized loss touches ₹2,000, the Kill Switch triggers:
    1. Cancels all pending orders.
    2. Flattens all open positions at market.
    3. Writes `trading.lock` and halts bot until the next calendar day.
- **Weekly Drawdown Gate:** If account drawdown exceeds -6.0% (₹94,000 NAV), trading halts for manual quant review.
