"""
Local health check (`make health`). Read-only: it reports state, it never changes it.

Exit code 0 = healthy, 1 = at least one FAIL. WARN does not fail the check.
"""

from __future__ import annotations

import json
import os
import sys

from trade.core.env import load_dotenv


def main() -> int:
    load_dotenv()
    results: list[tuple[str, str, str]] = []

    def check(name: str, status: str, detail: str) -> None:
        results.append((status, name, detail))

    try:
        from trade.core.config import config, state_dir

        for src in config.sources:
            check("config", "OK", f"{os.path.relpath(src.path)} sha256={src.sha256[:12]}")
    except Exception as e:  # noqa: BLE001 - report any config failure
        check("config", "FAIL", f"configuration did not load: {e}")
        return _report(results)

    sd = state_dir()
    try:
        sd.mkdir(parents=True, exist_ok=True)
        probe = sd / ".health_probe"
        probe.write_text("ok")
        probe.unlink()
        check("state dir", "OK", f"{sd} writable")
    except OSError as e:
        check("state dir", "FAIL", f"{sd} not writable: {e}")

    from trade.core.risk.engine import RiskEngine

    engine = RiskEngine()
    if engine.is_kill_switch_active():
        check("kill switch", "WARN", f"ACTIVE ({engine.lockfile_path}); trading is halted until operator reset")
    else:
        check("kill switch", "OK", "not active")

    from trade.data.recorders import symbols

    try:
        u = symbols.load_universe()
        check("universe", "OK", f"{len(u.symbols)} symbols as of {u.as_of}")
    except FileNotFoundError:
        check("universe", "WARN", "no NSE universe snapshot; run `make refresh-universe`")

    missing = [k for k in ("REDDIT_CLIENT_ID", "REDDIT_CLIENT_SECRET", "REDDIT_USER_AGENT") if not os.environ.get(k)]
    if missing:
        check(
            "social recorder",
            "WARN",
            f"credentials not configured ({', '.join(missing)}); recorder will refuse to start",
        )
    else:
        check("social recorder", "OK", "credentials present")

    from trade.data.recorders.social import raw_social_root

    events = raw_social_root() / "reddit" / "_events.jsonl"
    if events.exists():
        last = json.loads(events.read_text().splitlines()[-1])
        check(
            "social data",
            "OK" if last["event"] in ("RECORDER_START", "RECORDER_STOP") else "WARN",
            f"last recorder event: {last['event']} at {last['at']}",
        )
    else:
        check("social data", "WARN", "nothing recorded yet")

    check("live trading", "OK", "NOT IMPLEMENTED (locked by design)")
    return _report(results)


def _report(results: list[tuple[str, str, str]]) -> int:
    for status, name, detail in results:
        print(f"[{status:4}] {name:16} {detail}")
    return 1 if any(s == "FAIL" for s, _, _ in results) else 0


if __name__ == "__main__":
    sys.exit(main())
