# Data Architecture

## 1. Provider Interfaces

All data access goes through provider interfaces. Strategies never call providers. They receive a `MarketSnapshot`.

```python
class HistoricalDataProvider(Protocol):
    def bars(self, symbols, timeframe, start, end, *, as_of: datetime) -> Table: ...
class RealtimeDataProvider(Protocol):
    def subscribe(self, symbols, kinds) -> AsyncIterator[MarketEvent]: ...
class QuoteProvider(Protocol):      def quote(self, symbol, *, as_of) -> Quote: ...
class OrderBookProvider(Protocol):  def book(self, symbol, depth, *, as_of) -> OrderBook: ...
class InstrumentProvider(Protocol): def instruments(self, *, as_of) -> list[Instrument]: ...
class TradingCalendarProvider(Protocol):
    def sessions(self, market, start, end) -> list[Session]: ...
    def is_open(self, market, at: datetime) -> bool: ...
class SocialDataProvider(Protocol):
    def mentions(self, symbols, start, end, *, as_of) -> Table: ...  # counts/metadata, never raw text to strategies
```

Every historical method takes `as_of`. A provider **must not return any record whose availability time is later than `as_of`**. This is enforced in a shared base class, not left to each adapter.

Initial adapters:

| Adapter | Use |
|---|---|
| `CsvProvider`, `ParquetProvider` | User-supplied history |
| `SyntheticProvider` | Deterministic demo scenarios (§6) |
| `NseCalendarProvider` | Static holiday table + session times (versioned) |
| `StockTwitsProvider` | Existing code, moved; **coverage of NSE symbols is unverified** |
| Broker/data vendor adapters | Phase 13 |

## 2. Event Time vs Availability Time

Every record has two timestamps:
- `event_time`: when the thing happened (bar close, trade print, tweet posted).
- `available_at`: when this system could first have known it. Examples: bar close plus feed latency; the vendor publish time of a corporate action; the ingest time of a scraped post.

**Snapshots filter on `available_at ≤ T`.** This is the main defence against look-ahead bias. A 1-minute bar stamped 10:15 that covers 10:15–10:16 is available at 10:16 plus latency.

## 3. MarketSnapshot

```python
class MarketSnapshot(BaseModel, frozen=True):
    snapshot_id: str            # sha256 over (as_of, symbol, feature values, feature versions)
    as_of: datetime
    symbol: str
    session_state: SessionState # PRE_OPEN / OPEN / CLOSING / CLOSED / HALTED
    bar: OHLCV | None
    quote: Quote | None         # bid, ask, sizes, quote_time
    book: OrderBookSummary | None
    features: Mapping[str, FeatureValue]
    regime: RegimeLabel | None
    data_age: timedelta         # as_of − oldest required input's available_at

class FeatureValue(BaseModel, frozen=True):
    name: str
    version: int
    value: float | None
    source_time: datetime       # max available_at of inputs used
    lookback: str               # e.g. "20 bars"
    params_hash: str
```

`SnapshotBuilder` computes features through a **feature registry**. Each feature is a pure function of a window of inputs whose `available_at ≤ as_of`. It is registered with a name, version, lookback and code hash. Changing a feature's code requires a version bump, which a test enforces via the code-hash manifest.

## 4. Datasets & Versioning

```
datasets/<dataset_id>/
  manifest.json        # id, version, source, symbols, range, schema, row counts,
                       # per-file sha256, created_at, created_by, parent_version
  data/*.parquet
```

- `dataset_id@version` is immutable. Corrections create a new version with `parent_version`.
- **Partitions**: every research dataset version defines `TRAIN`, `VALIDATION` and `OUT_OF_SAMPLE` time ranges, with an **embargo gap** between them (default ≥ the longest feature lookback plus the maximum holding period). This prevents overlapping-window leakage.
- **OOS vault**: OOS partition files can be read only through `OOSVault.evaluate(experiment_id, strategy_version)`. This call:
  - checks the hypothesis's remaining OOS budget (default 1);
  - records an `OOS_EVALUATION` event with the strategy hash *before* running;
  - returns metrics only, never row-level data, to the agent runtime.
  The `ParquetProvider` refuses OOS ranges unless it is called from the vault context.

## 5. Data Quality (`trade.data.quality`)

Checks run on ingest and on dataset creation. Results are stored in the manifest. Failures block the dataset version from being used for VALIDATION or OOS.
- monotonic, unique timestamps per symbol; no future-dated rows;
- gaps against the trading calendar;
- OHLC consistency (`low ≤ open,close ≤ high`), non-negative volume;
- outlier returns against a rolling MAD;
- bid ≤ ask; crossed or locked book detection;
- corporate actions: split/bonus adjustment flags; **survivorship**: the universe is built from point-in-time constituent lists, not today's list.

## 6. Demo Scenario Datasets

Deterministic, seeded, committed as small Parquet files plus a generator script:
`TREND, MEAN_REVERSION, HIGH_VOLATILITY, LOW_VOLATILITY, CRASH, GAP, LIQUIDITY_SHOCK, REGIME_TRANSITION`.

**Rule:** generators emit only raw observables: prices, volumes, quotes, depth, mention counts. They never emit features or labels that are derived from future returns. The current generator violates this (CURRENT_STATE §6). Each scenario has a separate `truth.json` (true regime per bar) that only the evaluation code reads, to score the regime classifier.

Demo datasets are labelled **SYNTHETIC** everywhere in the UI and reports. Results on them demonstrate mechanics, not edge.

## 7. Social Data (Strategy #001)

- Raw text is stored only in a quarantined table. Strategies receive numeric features only (counts, velocity z, duplication rate, account-age stats).
- Text reaches an agent only as quoted, delimited data in the untrusted-data channel (AI_AGENT_ARCHITECTURE §5).
- **Decision (operator, 2026-10-03): record real social data forward from now.** Strategy #001 will be validated on recorded data, not run as paper-only research. Until enough history accumulates, it can be parity-tested and demo-run but **not validated**. The history needed is set by the walk-forward plan: at minimum, enough sessions to fill TRAIN + VALIDATION + OOS plus embargo.
- **Recorder** (`trade.data.recorders.social`): polls sources and appends raw posts to date-partitioned JSONL under `datasets/raw/social/<source>/<YYYY-MM-DD>.jsonl`. Each record stores `source`, `post_id`, `event_time` (post created), `available_at` (fetch time), `channel`, `author_hash` (salted SHA-256, never the raw handle), `text`, `url`. Records are deduplicated by `(source, post_id)`. Symbol tagging is a **separate, versioned, re-runnable** step against a dated universe snapshot (Nifty 500 list from NSE archives), so extraction can improve without re-recording.
- **Source status (probed 2026-10-03 from the operator machine):**
  | Source | Status |
  |---|---|
  | Reddit (r/IndianStreetBets, r/IndianStockMarket, r/StockMarketIndia, r/DalalStreetTalks) | Public JSON returns 403; **requires an OAuth app** (free tier). Credentials go in `.env` |
  | StockTwits | 403 Cloudflare challenge; no usable public API. Not implemented |
  | X / Twitter | Paid API. Not implemented |
  | NSE instrument lists (`EQUITY_L.csv`, `ind_nifty500list.csv`) | Reachable (HTTP 200) |
- Order-book depth (OBI) recording still requires a broker feed (DATA_ARCHITECTURE §8). It is not covered by this decision.

## 8. Order Book Data

OBI requires L2 depth. Historical NSE depth is not freely available. The path is to record broker websocket depth to Parquet (once a broker adapter exists) and/or use a vendor. Until then, OBI-dependent backtests use synthetic scenarios only.
