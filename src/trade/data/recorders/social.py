"""
Real social-data recorder (forward recording for Strategy #001 validation).

Polls Reddit through its OAuth API and appends every new post and comment to
date-partitioned, append-only JSONL files:

    datasets/raw/social/<source>/<YYYY-MM-DD>.jsonl      (UTC date of available_at)
    datasets/raw/social/<source>/_events.jsonl           (gaps, HTTP/auth errors, start/stop)

Point-in-time discipline (docs/DATA_ARCHITECTURE.md section 2):
- event_time   = when the post was created (from the source)
- available_at = when this recorder received it (our clock)
Backtests may only use a record at times >= available_at.

Raw text is quarantined data. It is stored verbatim and never interpreted.
Author handles are replaced by a salted HMAC so authors can be counted without
storing their identity. Symbol tagging is a separate, versioned step
(trade.data.recorders.symbols).

Credentials come from the environment (.env): REDDIT_CLIENT_ID,
REDDIT_CLIENT_SECRET, REDDIT_USER_AGENT. Without them the recorder refuses
to start.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
import os
import secrets
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable, Iterable
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Protocol

from trade.core.config import _REPO_ROOT, config_dir, load_yaml_dataclass, state_dir

logger = logging.getLogger("SocialRecorder")

RECORD_SCHEMA_VERSION = 1
CLOCK_SKEW_TOLERANCE = timedelta(minutes=5)

# (method, url, headers, body) -> (status, headers, body)
HttpFn = Callable[[str, str, dict[str, str], bytes | None], tuple[int, dict[str, str], bytes]]
ClockFn = Callable[[], datetime]


class RecorderConfigError(RuntimeError):
    """Missing credentials or configuration. The recorder refuses to start."""


class SourceError(RuntimeError):
    def __init__(self, kind: str, detail: str, retry_after: float | None = None):
        super().__init__(f"{kind}: {detail}")
        self.kind, self.detail, self.retry_after = kind, detail, retry_after


@dataclass(frozen=True)
class SocialRecorderConfig:
    version: int
    subreddits: tuple[str, ...]
    poll_interval_seconds: int
    max_pages_per_poll: int
    page_size: int


@dataclass(frozen=True)
class RawPost:
    schema_version: int
    source: str
    kind: str                 # "post" | "comment"
    post_id: str              # source-native fullname, e.g. t3_abc / t1_xyz
    channel: str              # e.g. r/IndianStreetBets
    event_time: str           # ISO-8601 UTC, from the source
    available_at: str         # ISO-8601 UTC, our receive time
    author_hash: str | None   # HMAC-SHA256(salt, author); None for deleted authors
    title: str | None
    text: str
    url: str
    parent_id: str | None
    observed_score: int | None
    quality_flags: tuple[str, ...] = field(default_factory=tuple)


def default_http(method: str, url: str, headers: dict[str, str], body: bytes | None) -> tuple[int, dict[str, str], bytes]:
    if not url.startswith("https://"):
        raise SourceError("CONFIG", "only https URLs are allowed")
    req = urllib.request.Request(url, data=body, headers=headers, method=method)  # noqa: S310 (https enforced)
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:  # noqa: S310
            return resp.status, {k.lower(): v for k, v in resp.headers.items()}, resp.read()
    except urllib.error.HTTPError as e:
        return e.code, {k.lower(): v for k, v in (e.headers or {}).items()}, e.read() or b""
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        raise SourceError("NETWORK", str(e)) from e


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def load_author_salt() -> bytes:
    """Stable per-installation salt (0600 in the state dir) so author hashes are consistent across runs."""
    path = state_dir() / "social_author_salt"
    try:
        return path.read_bytes()
    except FileNotFoundError:
        path.parent.mkdir(parents=True, exist_ok=True)
        salt = secrets.token_bytes(32)
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "wb") as f:
            f.write(salt)
        return salt


class SocialSource(Protocol):
    name: str

    def channels(self) -> list[str]: ...

    def fetch_page(self, channel: str, after: str | None) -> tuple[list[dict[str, Any]], str | None]: ...

    def to_record(self, item: dict[str, Any], available_at: datetime) -> RawPost: ...


class RedditSource:
    """Reddit OAuth (application-only, client_credentials) reader for public subreddits."""

    name = "reddit"
    TOKEN_URL = "https://www.reddit.com/api/v1/access_token"
    API = "https://oauth.reddit.com"

    def __init__(self, client_id: str, client_secret: str, user_agent: str, subreddits: Iterable[str],
                 author_salt: bytes, page_size: int = 100, http: HttpFn = default_http,
                 clock: Callable[[], float] = time.time):
        if not (client_id and client_secret and user_agent):
            raise RecorderConfigError("REDDIT_CLIENT_ID, REDDIT_CLIENT_SECRET and REDDIT_USER_AGENT are required")
        self._client_id, self._client_secret, self._ua = client_id, client_secret, user_agent
        self._subs = list(subreddits)
        self._salt = author_salt
        self._page_size = page_size
        self._http = http
        self._clock = clock
        self._token: str | None = None
        self._token_expiry = 0.0
        self.rate_remaining: float | None = None
        self.rate_reset: float | None = None

    @classmethod
    def from_env(cls, cfg: SocialRecorderConfig, author_salt: bytes, http: HttpFn = default_http) -> RedditSource:
        return cls(os.environ.get("REDDIT_CLIENT_ID", ""), os.environ.get("REDDIT_CLIENT_SECRET", ""),
                   os.environ.get("REDDIT_USER_AGENT", ""), cfg.subreddits, author_salt, cfg.page_size, http)

    def channels(self) -> list[str]:
        return [f"r/{s}/{kind}" for s in self._subs for kind in ("new", "comments")]

    def _authenticate(self) -> None:
        basic = base64.b64encode(f"{self._client_id}:{self._client_secret}".encode()).decode()
        status, _, body = self._http("POST", self.TOKEN_URL,
                                     {"Authorization": f"Basic {basic}", "User-Agent": self._ua,
                                      "Content-Type": "application/x-www-form-urlencoded"},
                                     b"grant_type=client_credentials")
        if status != 200:
            raise SourceError("AUTH", f"token endpoint returned HTTP {status}")
        try:
            payload = json.loads(body)
            self._token = str(payload["access_token"])
            self._token_expiry = self._clock() + float(payload.get("expires_in", 3600)) - 60
        except (ValueError, KeyError, TypeError) as e:
            raise SourceError("AUTH", f"malformed token response: {e}") from e

    def _get(self, path: str, params: dict[str, str], retried: bool = False) -> dict[str, Any]:
        if not self._token or self._clock() >= self._token_expiry:
            self._authenticate()
        url = f"{self.API}{path}?{urllib.parse.urlencode(params)}"
        status, headers, body = self._http("GET", url, {"Authorization": f"bearer {self._token}",
                                                        "User-Agent": self._ua}, None)
        self._track_rate_limit(headers)
        if status == 401 and not retried:
            self._token = None
            return self._get(path, params, retried=True)
        if status == 429:
            raise SourceError("RATE_LIMITED", "HTTP 429", retry_after=self.rate_reset or 60.0)
        if status != 200:
            raise SourceError("HTTP", f"{path} returned HTTP {status}")
        try:
            data = json.loads(body)
        except ValueError as e:
            raise SourceError("PARSE", f"{path}: invalid JSON") from e
        if not isinstance(data, dict) or data.get("kind") != "Listing":
            raise SourceError("PARSE", f"{path}: not a Listing")
        return data

    def _track_rate_limit(self, headers: dict[str, str]) -> None:
        try:
            if "x-ratelimit-remaining" in headers:
                self.rate_remaining = float(headers["x-ratelimit-remaining"])
            if "x-ratelimit-reset" in headers:
                self.rate_reset = float(headers["x-ratelimit-reset"])
        except ValueError:
            pass

    def fetch_page(self, channel: str, after: str | None) -> tuple[list[dict[str, Any]], str | None]:
        _, sub, kind = channel.split("/")
        params = {"limit": str(self._page_size), "raw_json": "1"}
        if after:
            params["after"] = after
        data = self._get(f"/r/{sub}/{kind}", params)["data"]
        items = [{**c["data"], "_kind": c["kind"]} for c in data.get("children", []) if c.get("kind") in ("t1", "t3")]
        return items, data.get("after")

    def to_record(self, item: dict[str, Any], available_at: datetime) -> RawPost:
        is_comment = item.get("_kind") == "t1" or "body" in item
        created = datetime.fromtimestamp(float(item["created_utc"]), tz=timezone.utc)
        flags: list[str] = []
        if created > available_at + CLOCK_SKEW_TOLERANCE:
            flags.append("EVENT_AFTER_RECEIPT")
        author = item.get("author")
        author_hash = None
        if author and author not in ("[deleted]", "[removed]"):
            author_hash = hmac.new(self._salt, author.encode(), hashlib.sha256).hexdigest()
        return RawPost(
            schema_version=RECORD_SCHEMA_VERSION,
            source=self.name,
            kind="comment" if is_comment else "post",
            post_id=str(item.get("name") or f"{'t1' if is_comment else 't3'}_{item['id']}"),
            channel=f"r/{item.get('subreddit', '')}",
            event_time=_iso(created),
            available_at=_iso(available_at),
            author_hash=author_hash,
            title=None if is_comment else str(item.get("title", "")),
            text=str(item.get("body" if is_comment else "selftext", "")),
            url="https://www.reddit.com" + str(item.get("permalink", "")),
            parent_id=str(item["link_id"]) if is_comment and item.get("link_id") else None,
            observed_score=int(item["score"]) if isinstance(item.get("score"), int) else None,
            quality_flags=tuple(flags),
        )


class JsonlSink:
    """Append-only, fsync'd JSONL store with cross-restart de-duplication."""

    def __init__(self, root: Path, source: str, clock: ClockFn = utc_now):
        self.dir = root / source
        self.dir.mkdir(parents=True, exist_ok=True)
        self._clock = clock
        self.seen: set[str] = set()
        self.channels_with_history: set[str] = set()
        today = clock().date()
        for day in (today - timedelta(days=1), today):
            path = self.dir / f"{day.isoformat()}.jsonl"
            if path.exists():
                with path.open() as f:
                    for line in f:
                        try:
                            row = json.loads(line)
                            self.seen.add(row["post_id"])
                            self.channels_with_history.add(channel_key(row["channel"], row["kind"]))
                        except (ValueError, KeyError):
                            continue  # torn final line after a crash; record stays unread, not rewritten

    def write(self, records: list[RawPost]) -> int:
        new = [r for r in records if r.post_id not in self.seen]
        by_day: dict[str, list[RawPost]] = {}
        for r in new:
            by_day.setdefault(r.available_at[:10], []).append(r)
        for day, rows in by_day.items():
            self._append(self.dir / f"{day}.jsonl", (asdict(r) for r in rows))
        self.seen.update(r.post_id for r in new)
        self.channels_with_history.update(channel_key(r.channel, r.kind) for r in new)
        return len(new)

    def event(self, kind: str, **details: Any) -> None:
        self._append(self.dir / "_events.jsonl", [{"at": _iso(self._clock()), "event": kind, **details}])

    @staticmethod
    def _append(path: Path, rows: Iterable[dict[str, Any]]) -> None:
        data = "".join(json.dumps(r, ensure_ascii=False, sort_keys=True) + "\n" for r in rows)
        if not data:
            return
        fd = os.open(path, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o644)
        try:
            os.write(fd, data.encode("utf-8"))
            os.fsync(fd)
        finally:
            os.close(fd)


class Recorder:
    def __init__(self, source: SocialSource, sink: JsonlSink, max_pages: int, clock: ClockFn = utc_now):
        self.source, self.sink, self.max_pages, self.clock = source, sink, max_pages, clock

    def poll_channel(self, channel: str) -> int:
        """Fetch newest-first pages until reaching already-recorded items (or max_pages). Returns new count.

        With no recorded history for the channel, one page establishes the baseline.
        If history exists but no overlap is found within max_pages, a GAP_POSSIBLE
        event is recorded: items between the pages fetched and the history may be missing.
        """
        written, after, pages = 0, None, 0
        caught_up = False
        has_history = channel in self.sink.channels_with_history
        while pages < self.max_pages:
            items, after = self.source.fetch_page(channel, after)
            pages += 1
            received = self.clock()
            records = [self.source.to_record(i, received) for i in items]
            overlap = any(r.post_id in self.sink.seen for r in records)
            written += self.sink.write(records)
            if overlap or not after or not items or not has_history:
                caught_up = True
                break
        if not caught_up:
            self.sink.event("GAP_POSSIBLE", channel=channel, pages=pages,
                            detail="no overlap with recorded items within max_pages; items may be missing")
        return written

    def run_once(self) -> dict[str, int]:
        results: dict[str, int] = {}
        for channel in self.source.channels():
            try:
                results[channel] = self.poll_channel(channel)
            except SourceError as e:
                self.sink.event(e.kind, channel=channel, detail=e.detail)
                logger.warning(f"{channel}: {e.kind} {e.detail}")
                results[channel] = -1
                if e.kind in ("RATE_LIMITED", "AUTH"):
                    break
        return results


def channel_key(channel: str, kind: str) -> str:
    """Maps a record to the polling channel it came from, e.g. r/IndianStreetBets/comments."""
    return f"{channel}/{'comments' if kind == 'comment' else 'new'}"


def load_recorder_config() -> SocialRecorderConfig:
    cfg, _ = load_yaml_dataclass(config_dir() / "social_recorder.yaml", SocialRecorderConfig)
    if not 1 <= cfg.page_size <= 100 or cfg.poll_interval_seconds < 10 or cfg.max_pages_per_poll < 1:
        raise RecorderConfigError("social_recorder.yaml: values out of range")
    return cfg


def raw_social_root() -> Path:
    return Path(os.environ.get("TRADE_DATA_DIR", _REPO_ROOT / "datasets")).resolve() / "raw" / "social"
