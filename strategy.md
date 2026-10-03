# Strategy Specification: Social Momentum 1% Scalp (NSE Intraday MIS)

> **Status:** Strategy #001 v1, a hand-specified design. It has **not been validated** on real data (no historical social or order-book dataset exists yet; forward recording started 2026-10-03). Where the implementation differs from this spec, the difference is marked **[Implementation]** and tracked in `docs/KNOWN_DEFECTS.md`.

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
- **Bracket:** Every fill registers take-profit and stop levels (OCO). **[Implementation]** The paper broker checks the levels on each price tick; there are no resting exchange orders.
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
| **Roundtrip Brokerage** | ₹12.06 | `min(₹20, 0.03%)` per leg as implemented. (A flat ₹20 + ₹20 would be ₹40.00) |
| **STT (Securities Tax)** | ₹5.05 | 0.025% on sell turnover |
| **Exchange & SEBI Fees**| ₹1.23 | NSE turnover + SEBI charges |
| **Stamp Duty & GST** | ₹2.99 | 0.003% buy + 18% GST on brokerage + exchange + SEBI |
| **Total Roundtrip Friction** | **₹21.34** | **0.11% of trade capital** (TP case, from the code's cost model) |
| **Net Profit on Win** | **+₹178.66 (+0.89%)** | Win payoff |
| **Net Loss on Stop** | **-₹161.12 (-0.81%)** | Loss payoff, before stop slippage (friction ₹21.12) |
| **Required Breakeven Win Rate** | **47.42%** | Before slippage. Reflex gate is $\ge 65.0\%$ on an **uncalibrated** probability |

**[Implementation]** The Kelly payoff ratio in code is `b = 0.75`, derived from the old ₹54.26 friction figure. It is preserved for v1 parity (D-004).

---

## 4. Hard Risk Limits & Kill Switch
- **Max Concurrent Positions:** 2 positions (Max portfolio exposure ₹40,000).
- **Daily Loss Limit:** **-₹2,000 INR** (-2.0% of portfolio).
  - When cumulative daily realized + unrealized loss touches ₹2,000, the Kill Switch triggers. **[Implementation]** Only the sum of losing realized trades is counted, and it never resets daily (D-006):
    1. Cancels all pending orders.
    2. Flattens all open positions at market.
    3. Writes the kill-switch lockfile and halts. **[Implementation]** A manual operator reset is required; there is no automatic next-day reset.
- **Drawdown Gate:** If account drawdown exceeds 6.0% from the high-water mark, trading halts for manual review. **[Implementation]** Configured in `config/risk_limits.yaml`, not yet enforced (D-006, Phase 2).
