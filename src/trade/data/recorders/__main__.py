"""
Social recorder CLI.

    python -m trade.data.recorders run [--once]      record Reddit posts/comments forward
    python -m trade.data.recorders refresh-universe  snapshot the Nifty 500 list from NSE
    python -m trade.data.recorders tag --date YYYY-MM-DD
    python -m trade.data.recorders status
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
import time
from datetime import datetime, timezone

from trade.core.env import install_redaction, load_dotenv
from trade.data.recorders import symbols
from trade.data.recorders.social import (
    JsonlSink,
    RecorderConfigError,
    Recorder,
    RedditSource,
    SourceError,
    load_author_salt,
    load_recorder_config,
    raw_social_root,
)

logger = logging.getLogger("SocialRecorder")


def cmd_run(args: argparse.Namespace) -> int:
    cfg = load_recorder_config()
    try:
        source = RedditSource.from_env(cfg, load_author_salt())
    except RecorderConfigError as e:
        print(f"REFUSING TO START: {e}. Copy .env.example to .env and fill in Reddit OAuth credentials.",
              file=sys.stderr)
        return 2
    sink = JsonlSink(raw_social_root(), source.name)
    recorder = Recorder(source, sink, cfg.max_pages_per_poll)
    sink.event("RECORDER_START", subreddits=list(cfg.subreddits), interval=cfg.poll_interval_seconds)
    logger.info(f"Recording {len(source.channels())} channels to {sink.dir}")
    try:
        while True:
            started = time.monotonic()
            results = recorder.run_once()
            logger.info(f"poll: {sum(v for v in results.values() if v > 0)} new, "
                        f"{sum(1 for v in results.values() if v < 0)} channel errors")
            if args.once:
                return 0 if all(v >= 0 for v in results.values()) else 1
            wait = cfg.poll_interval_seconds
            if source.rate_remaining is not None and source.rate_remaining < 5 and source.rate_reset:
                wait = max(wait, int(source.rate_reset) + 1)
            time.sleep(max(1.0, wait - (time.monotonic() - started)))
    except KeyboardInterrupt:
        return 0
    finally:
        sink.event("RECORDER_STOP")


def cmd_refresh_universe(_: argparse.Namespace) -> int:
    try:
        path = symbols.refresh_universe()
    except SourceError as e:
        print(f"universe refresh failed: {e}", file=sys.stderr)
        return 1
    print(f"wrote {path}")
    return 0


def cmd_tag(args: argparse.Namespace) -> int:
    out, total, tagged = symbols.tag_day(args.date)
    print(f"wrote {out}: {total} records, {tagged} with >=1 symbol (tagger v{symbols.TAGGER_VERSION})")
    return 0


def cmd_status(_: argparse.Namespace) -> int:
    root = raw_social_root()
    if not root.exists():
        print("no recordings yet")
        return 0
    for src in sorted(p for p in root.iterdir() if p.is_dir()):
        days = sorted(src.glob("2*.jsonl"))
        print(f"{src.name}: {len(days)} day(s)")
        for d in days[-7:]:
            with d.open() as f:
                print(f"  {d.stem}: {sum(1 for _ in f)} records")
        events = src / "_events.jsonl"
        if events.exists():
            lines = events.read_text().splitlines()
            gaps = sum('"GAP_POSSIBLE"' in line for line in lines)
            print(f"  events: {len(lines)} total, {gaps} possible gaps; last: {json.loads(lines[-1])}")
    return 0


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
    load_dotenv()
    install_redaction()
    parser = argparse.ArgumentParser(prog="python -m trade.data.recorders")
    sub = parser.add_subparsers(dest="cmd", required=True)
    run = sub.add_parser("run", help="record forward")
    run.add_argument("--once", action="store_true", help="single poll, then exit")
    run.set_defaults(fn=cmd_run)
    sub.add_parser("refresh-universe").set_defaults(fn=cmd_refresh_universe)
    tag = sub.add_parser("tag")
    tag.add_argument("--date", default=datetime.now(timezone.utc).date().isoformat())
    tag.set_defaults(fn=cmd_tag)
    sub.add_parser("status").set_defaults(fn=cmd_status)
    args = parser.parse_args(argv)
    return int(args.fn(args))


if __name__ == "__main__":
    sys.exit(main())
