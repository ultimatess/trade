"""
Versioned symbol tagging of recorded social posts against a dated NSE universe.

Raw recordings are never modified. Tagging writes a derived dataset, so it
can be re-run with an improved tagger (bump TAGGER_VERSION) without
re-recording:

    datasets/reference/nse/nifty500_<YYYY-MM-DD>.csv (+ .manifest.json)
    datasets/derived/social_mentions/v<TAGGER_VERSION>/<YYYY-MM-DD>.jsonl

Tagger v1 rules (precision/recall NOT yet measured):
- cashtags ($RELIANCE, $reliance) that are universe symbols
- bare ALL-CAPS tokens of length >= 3 that are universe symbols, excluding
  AMBIGUOUS_BARE (ordinary words/abbreviations); those count only as cashtags
- company-name matching is not implemented
"""

from __future__ import annotations

import csv
import hashlib
import io
import json
import os
import re
from dataclasses import dataclass
from datetime import UTC, date, datetime
from pathlib import Path

from trade.core.config import _REPO_ROOT
from trade.data.recorders.social import HttpFn, SourceError, default_http

TAGGER_VERSION = 1
NIFTY500_URL = "https://archives.nseindia.com/content/indices/ind_nifty500list.csv"
AMBIGUOUS_BARE = frozenset({"BSE", "OIL", "ACE", "FACT", "IDEA", "ITI", "CUB", "SCI"})
_CASHTAG = re.compile(r"\$([A-Za-z0-9&-]{2,20})\b")
_BARE = re.compile(r"(?<![A-Za-z0-9$&-])([A-Z][A-Z0-9&-]{2,19})(?![A-Za-z0-9&-])")


def data_root() -> Path:
    return Path(os.environ.get("TRADE_DATA_DIR", _REPO_ROOT / "datasets")).resolve()


@dataclass(frozen=True)
class Universe:
    symbols: frozenset[str]
    as_of: str
    sha256: str
    path: str


def refresh_universe(http: HttpFn = default_http, today: date | None = None) -> Path:
    """Download the current Nifty 500 list as a dated, hashed snapshot."""
    status, _, body = http("GET", NIFTY500_URL, {"User-Agent": "Mozilla/5.0 (trade-os universe snapshot)"}, None)
    if status != 200:
        raise SourceError("HTTP", f"NSE universe download returned HTTP {status}")
    rows = list(csv.DictReader(io.StringIO(body.decode("utf-8-sig"))))
    symbols = [r.get("Symbol", "").strip() for r in rows]
    if len(symbols) < 400 or not all(symbols):
        raise SourceError("PARSE", f"unexpected universe file ({len(symbols)} rows)")
    day = (today or datetime.now(UTC).date()).isoformat()
    out_dir = data_root() / "reference" / "nse"
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"nifty500_{day}.csv"
    path.write_bytes(body)
    (out_dir / f"nifty500_{day}.manifest.json").write_text(
        json.dumps(
            {
                "source_url": NIFTY500_URL,
                "fetched_at": datetime.now(UTC).isoformat(),
                "rows": len(symbols),
                "sha256": hashlib.sha256(body).hexdigest(),
            },
            indent=1,
        )
        + "\n"
    )
    return path


def latest_universe_path() -> Path:
    snaps = sorted((data_root() / "reference" / "nse").glob("nifty500_*.csv"))
    if not snaps:
        raise FileNotFoundError("no universe snapshot; run: make refresh-universe")
    return snaps[-1]


def load_universe(path: Path | None = None) -> Universe:
    path = path or latest_universe_path()
    body = path.read_bytes()
    symbols = frozenset(r["Symbol"].strip() for r in csv.DictReader(io.StringIO(body.decode("utf-8-sig"))))
    as_of = path.stem.split("_")[-1]
    return Universe(symbols, as_of, hashlib.sha256(body).hexdigest(), str(path))


def tag_symbols(text: str, universe: Universe) -> list[str]:
    found = {m.upper() for m in _CASHTAG.findall(text) if m.upper() in universe.symbols}
    found |= {m for m in _BARE.findall(text) if m in universe.symbols and m not in AMBIGUOUS_BARE}
    return sorted(found)


def tag_day(day: str, source: str = "reddit", universe: Universe | None = None) -> tuple[Path, int, int]:
    """Tag one day of raw recordings. Returns (output path, records, records with >= 1 symbol)."""
    universe = universe or load_universe()
    raw = data_root() / "raw" / "social" / source / f"{day}.jsonl"
    out_dir = data_root() / "derived" / "social_mentions" / f"v{TAGGER_VERSION}"
    out_dir.mkdir(parents=True, exist_ok=True)
    out = out_dir / f"{day}.jsonl"
    total = tagged = 0
    with raw.open() as src, out.open("w") as dst:
        for line in src:
            try:
                r = json.loads(line)
            except ValueError:
                continue
            syms = tag_symbols(f"{r.get('title') or ''}\n{r.get('text') or ''}", universe)
            total += 1
            tagged += bool(syms)
            dst.write(
                json.dumps(
                    {
                        "post_id": r["post_id"],
                        "source": r["source"],
                        "channel": r["channel"],
                        "kind": r["kind"],
                        "event_time": r["event_time"],
                        "available_at": r["available_at"],
                        "author_hash": r["author_hash"],
                        "symbols": syms,
                        "tagger_version": TAGGER_VERSION,
                        "universe_as_of": universe.as_of,
                        "universe_sha256": universe.sha256,
                    },
                    sort_keys=True,
                )
                + "\n"
            )
    return out, total, tagged
