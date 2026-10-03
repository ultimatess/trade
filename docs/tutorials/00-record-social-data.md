# 00 — Record Real Social Data

Strategy #001 needs historical social-mention data for NSE stocks, and none exists yet. History can only accumulate in calendar time, so recording starts now (operator decision 2026-10-03).

## 1. Create Reddit OAuth credentials
1. Sign in to Reddit and open https://www.reddit.com/prefs/apps.
2. Click **create another app**, choose **script**, and set any redirect URI (e.g. `http://localhost:8080`).
3. Note the client id (under the app name) and the secret.

Use is subject to Reddit's Data API terms. The free tier allows about 100 requests per minute; the recorder uses about 8 per minute with the default config.

## 2. Configure
```bash
cp .env.example .env
# edit .env: REDDIT_CLIENT_ID, REDDIT_CLIENT_SECRET,
# REDDIT_USER_AGENT="macos:trade-os-recorder:0.1 (by /u/<your reddit username>)"
make refresh-universe    # dated Nifty 500 snapshot from NSE (for symbol tagging)
make health              # credentials and universe should show OK
```

Subreddits and the poll interval are set in `config/social_recorder.yaml`.

## 3. Record
```bash
make record-social-once  # one poll; verify it works
make record-social       # continuous (Ctrl-C to stop)
make social-status       # records per day, gaps, last event
```

The recorder must keep running to build continuous history. On macOS, run it in a terminal session that stays open, or under `launchd`. Any period it is not running is a gap in the dataset.

## 4. What gets stored
- `datasets/raw/social/reddit/<YYYY-MM-DD>.jsonl`: one row per post or comment, with `event_time` (created) and `available_at` (received). The author is stored only as a salted hash.
- `datasets/raw/social/reddit/_events.jsonl`: start/stop, `GAP_POSSIBLE`, rate-limit and auth errors. Gaps are recorded, never hidden.

## 5. Tag symbols
```bash
make tag-social DATE=2026-10-04
```
This writes `datasets/derived/social_mentions/v1/<date>.jsonl`. Tagger v1 matches cashtags and ALL-CAPS tickers only. It does **not** match company names ("Tata Steel"), and its precision and recall have not been measured. Raw data is never modified, so improved taggers can be re-run over the whole history.

## Limits to know
- Reddit is one community and is not representative of all Indian retail chatter. StockTwits and X are not available (DATA_ARCHITECTURE §7).
- Order-book depth, the other input Strategy #001 needs, is **not** recorded yet. It needs a broker feed.
