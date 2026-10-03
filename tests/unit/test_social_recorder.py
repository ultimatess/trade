"""Social recorder: point-in-time stamping, dedupe, gaps, auth, privacy, fail-closed start."""

import json
import logging
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from trade.data.recorders import social, symbols
from trade.data.recorders.__main__ import main as cli_main

T0 = datetime(2026, 10, 3, 5, 0, tzinfo=UTC)
SECRET = "s3cr3t-client-value"


def listing(items, after=None):
    return {"kind": "Listing", "data": {"after": after, "children": items}}


def post(n, created=T0 - timedelta(minutes=1), sub="IndianStreetBets", author="trader1"):
    return {
        "kind": "t3",
        "data": {
            "id": f"p{n}",
            "name": f"t3_p{n}",
            "created_utc": created.timestamp(),
            "author": author,
            "title": f"$RELIANCE post {n}",
            "selftext": "body",
            "permalink": f"/r/{sub}/comments/p{n}/",
            "subreddit": sub,
            "score": 3,
        },
    }


def comment(n, sub="IndianStreetBets"):
    return {
        "kind": "t1",
        "data": {
            "id": f"c{n}",
            "name": f"t1_c{n}",
            "created_utc": (T0 - timedelta(seconds=30)).timestamp(),
            "author": "[deleted]",
            "body": f"INFY and TCS c{n}",
            "link_id": "t3_p1",
            "permalink": f"/r/{sub}/comments/p1/x/c{n}/",
            "subreddit": sub,
            "score": 1,
        },
    }


class FakeReddit:
    """Scripted Reddit API. pages[channel] = list of (items, after) returned per successive call."""

    def __init__(self, pages=None, token_status=200, listing_status=None):
        self.pages = pages or {}
        self.token_status = token_status
        self.listing_status = listing_status or []
        self.calls = []
        self.token_requests = 0

    def __call__(self, method, url, headers, body):
        self.calls.append((method, url, dict(headers)))
        if url.startswith(social.RedditSource.TOKEN_URL):
            self.token_requests += 1
            if self.token_status != 200:
                return self.token_status, {}, b"{}"
            return 200, {}, json.dumps({"access_token": f"tok{self.token_requests}", "expires_in": 3600}).encode()
        if self.listing_status:
            status = self.listing_status.pop(0)
            if status != 200:
                return status, {"x-ratelimit-remaining": "0", "x-ratelimit-reset": "42"}, b"{}"
        path = url.split("oauth.reddit.com")[1].split("?")[0]
        queue = self.pages.get(path, [])
        items, after = queue.pop(0) if queue else ([], None)
        return 200, {"x-ratelimit-remaining": "90"}, json.dumps(listing(items, after)).encode()


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("TRADE_STATE_DIR", str(tmp_path / "state"))
    monkeypatch.setenv("TRADE_DATA_DIR", str(tmp_path / "datasets"))
    return tmp_path


def make(http, subs=("IndianStreetBets",), clock=lambda: T0, max_pages=5):
    src = social.RedditSource(
        "cid-123456",
        SECRET,
        "test:trade-os:0.1 (by /u/test)",
        subs,
        b"salt" * 8,
        http=http,
        clock=lambda: clock().timestamp(),
    )
    sink = social.JsonlSink(social.raw_social_root(), "reddit", clock=clock)
    return social.Recorder(src, sink, max_pages=max_pages, clock=clock), sink


def rows(sink, day="2026-10-03"):
    path = sink.dir / f"{day}.jsonl"
    return [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []


def events(sink):
    path = sink.dir / "_events.jsonl"
    return [json.loads(line)["event"] for line in path.read_text().splitlines()] if path.exists() else []


def test_records_posts_and_comments_with_point_in_time_stamps(env):
    http = FakeReddit(
        {
            "/r/IndianStreetBets/new": [([post(1), post(2)], None)],
            "/r/IndianStreetBets/comments": [([comment(1)], None)],
        }
    )
    rec, sink = make(http)
    assert rec.run_once() == {"r/IndianStreetBets/new": 2, "r/IndianStreetBets/comments": 1}
    out = rows(sink)
    assert {r["kind"] for r in out} == {"post", "comment"}
    for r in out:
        assert r["available_at"] == "2026-10-03T05:00:00.000Z"
        assert r["event_time"] <= r["available_at"]
        assert r["schema_version"] == 1 and r["source"] == "reddit"
    c = next(r for r in out if r["kind"] == "comment")
    assert c["author_hash"] is None and c["parent_id"] == "t3_p1" and c["title"] is None


def test_authors_are_hashed_never_stored(env):
    rec, sink = make(FakeReddit({"/r/IndianStreetBets/new": [([post(1, author="RealHandle")], None)]}))
    rec.run_once()
    text = (sink.dir / "2026-10-03.jsonl").read_text()
    assert "RealHandle" not in text
    assert len(rows(sink)[0]["author_hash"]) == 64


def test_dedupe_within_and_across_restarts(env):
    pages = {"/r/IndianStreetBets/new": [([post(2), post(1)], None), ([post(3), post(2)], None)]}
    http = FakeReddit(pages)
    rec, sink = make(http)
    rec.run_once()
    rec2, sink2 = make(http)  # restart: history reloaded from disk
    assert rec2.run_once()["r/IndianStreetBets/new"] == 1
    assert [r["post_id"] for r in rows(sink2)] == ["t3_p2", "t3_p1", "t3_p3"]


def test_first_poll_takes_one_page_baseline_without_gap(env):
    http = FakeReddit({"/r/IndianStreetBets/new": [([post(9), post(8)], "t3_p8"), ([post(7)], None)]})
    rec, sink = make(http)
    rec.run_once()
    assert len(rows(sink)) == 2 and "GAP_POSSIBLE" not in events(sink)


def test_paginates_until_overlap_and_flags_gap_when_none(env):
    http = FakeReddit({"/r/IndianStreetBets/new": [([post(1)], None)]})
    rec, sink = make(http, max_pages=2)
    rec.run_once()  # baseline: p1
    http.pages["/r/IndianStreetBets/new"] = [([post(5), post(4)], "t3_p4"), ([post(3), post(2)], "t3_p2")]
    rec.run_once()  # 2 pages, never reaches p1
    assert "GAP_POSSIBLE" in events(sink)
    http.pages["/r/IndianStreetBets/new"] = [([post(7), post(6)], "t3_p6"), ([post(5)], "t3_p5")]
    before = events(sink).count("GAP_POSSIBLE")
    assert rec.run_once()["r/IndianStreetBets/new"] == 2  # stops at overlap with p5
    assert events(sink).count("GAP_POSSIBLE") == before


def test_expired_token_is_refreshed_once(env):
    http = FakeReddit({"/r/IndianStreetBets/new": [([post(1)], None)]}, listing_status=[401, 200, 200])
    rec, _ = make(http)
    assert rec.run_once()["r/IndianStreetBets/new"] == 1
    assert http.token_requests == 2


def test_rate_limit_and_auth_failures_are_recorded_not_raised(env):
    rec, sink = make(FakeReddit(listing_status=[429]))
    assert set(rec.run_once().values()) == {-1}
    assert "RATE_LIMITED" in events(sink)
    rec, sink = make(FakeReddit(token_status=401))
    assert set(rec.run_once().values()) == {-1}
    assert "AUTH" in events(sink)


def test_event_after_receipt_is_flagged(env):
    future = post(1, created=T0 + timedelta(hours=1))
    rec, sink = make(FakeReddit({"/r/IndianStreetBets/new": [([future], None)]}))
    rec.run_once()
    assert rows(sink)[0]["quality_flags"] == ["EVENT_AFTER_RECEIPT"]


def test_records_partition_by_receipt_date(env):
    clock = {"now": T0.replace(hour=23, minute=59, second=59)}
    http = FakeReddit({"/r/IndianStreetBets/new": [([post(1)], None), ([post(2), post(1)], None)]})
    rec, sink = make(http, clock=lambda: clock["now"])
    rec.run_once()
    clock["now"] += timedelta(seconds=2)
    rec.run_once()
    assert [r["post_id"] for r in rows(sink, "2026-10-03")] == ["t3_p1"]
    assert [r["post_id"] for r in rows(sink, "2026-10-04")] == ["t3_p2"]


def test_torn_last_line_does_not_break_restart(env):
    rec, sink = make(FakeReddit({"/r/IndianStreetBets/new": [([post(1)], None)]}))
    rec.run_once()
    with (sink.dir / "2026-10-03.jsonl").open("a") as f:
        f.write('{"post_id": "t3_tor')  # crash mid-write
    _, sink2 = make(FakeReddit())
    assert "t3_p1" in sink2.seen


@pytest.mark.critical
def test_refuses_to_start_without_credentials(env, monkeypatch, capsys):
    for k in ("REDDIT_CLIENT_ID", "REDDIT_CLIENT_SECRET", "REDDIT_USER_AGENT"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.chdir(env)  # no .env here
    assert cli_main(["run", "--once"]) == 2
    assert "REFUSING TO START" in capsys.readouterr().err


@pytest.mark.critical
def test_client_secret_never_written_or_logged(env, caplog, monkeypatch):
    monkeypatch.setenv("REDDIT_CLIENT_SECRET", SECRET)
    from trade.core.env import install_redaction

    install_redaction()
    caplog.handler.addFilter(__import__("trade.core.env", fromlist=["RedactingFilter"]).RedactingFilter())
    rec, sink = make(FakeReddit(listing_status=[500]))
    with caplog.at_level(logging.INFO):
        rec.run_once()
        logging.getLogger("SocialRecorder").warning(f"debug dump {SECRET}")
    for path in Path(env).rglob("*"):
        if path.is_file():
            assert SECRET.encode() not in path.read_bytes(), path
    assert SECRET not in caplog.text and "***REDACTED***" in caplog.text


def test_only_https_is_allowed():
    with pytest.raises(social.SourceError):
        social.default_http("GET", "http://example.com", {}, None)


# --- symbol tagging ---------------------------------------------------------

UNIVERSE = symbols.Universe(
    frozenset({"RELIANCE", "INFY", "TCS", "M&M", "IDEA", "LT", "TATASTEEL"}), "2026-10-03", "0" * 64, "test"
)


@pytest.mark.parametrize(
    "text,expected",
    [
        ("$RELIANCE breakout", ["RELIANCE"]),
        ("$reliance and $infy", ["INFY", "RELIANCE"]),
        ("INFY and TCS results today", ["INFY", "TCS"]),
        ("infy results", []),  # bare lowercase is not a ticker
        ("Great IDEA to buy", []),  # ambiguous bare word
        ("$IDEA is cheap", ["IDEA"]),  # ...but explicit cashtag counts
        ("LT looks strong", []),  # 2-char bare tokens ignored
        ("M&M up 3%", ["M&M"]),
        ("NSE:TATASTEEL / TATASTEEL.NS", ["TATASTEEL"]),
        ("TCSX is not TCS-like", []),
    ],
)
def test_tagger_v1(text, expected):
    assert symbols.tag_symbols(text, UNIVERSE) == expected


def test_tag_day_writes_versioned_derived_dataset(env):
    rec, sink = make(
        FakeReddit(
            {"/r/IndianStreetBets/new": [([post(1)], None)], "/r/IndianStreetBets/comments": [([comment(1)], None)]}
        )
    )
    rec.run_once()
    out, total, tagged = symbols.tag_day("2026-10-03", universe=UNIVERSE)
    assert (total, tagged) == (2, 2)
    assert out.parent.name == f"v{symbols.TAGGER_VERSION}"
    derived = [json.loads(line) for line in out.read_text().splitlines()]
    assert {tuple(d["symbols"]) for d in derived} == {("RELIANCE",), ("INFY", "TCS")}
    assert all("text" not in d and d["universe_sha256"] == "0" * 64 for d in derived)
    assert rows(sink)[0]["title"] == "$RELIANCE post 1"  # raw untouched


def test_refresh_universe_validates_and_snapshots(env):
    csv_body = "Company Name,Industry,Symbol,Series,ISIN Code\n" + "".join(
        f"Co {i},X,SYM{i},EQ,INE{i:09d}\n" for i in range(450)
    )
    path = symbols.refresh_universe(http=lambda *a: (200, {}, csv_body.encode()), today=datetime(2026, 10, 3).date())
    u = symbols.load_universe(path)
    assert len(u.symbols) == 450 and u.as_of == "2026-10-03"
    with pytest.raises(social.SourceError):
        symbols.refresh_universe(http=lambda *a: (200, {}, b"Symbol\nA\n"))
