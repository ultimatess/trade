# datasets/

Local data store (contents are gitignored).

| Path | Contents | Mutability |
|---|---|---|
| `raw/social/<source>/<YYYY-MM-DD>.jsonl` | Recorded posts (`event_time`, `available_at`, hashed author, raw text) | append-only |
| `raw/social/<source>/_events.jsonl` | Recorder events: start/stop, gaps, HTTP/auth errors | append-only |
| `reference/nse/nifty500_<date>.csv` | Dated universe snapshots + manifest (sha256) | immutable |
| `derived/social_mentions/v<N>/<date>.jsonl` | Symbol-tagged mentions (tagger version + universe hash per row) | regenerable |

See docs/DATA_ARCHITECTURE.md.
