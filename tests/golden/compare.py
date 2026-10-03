"""Summarise golden-fixture changes for tests/golden/CHANGELOG.md: python -m tests.golden.compare <before_dir>"""

import json
import sys
from pathlib import Path

from tests.golden.characterize import FIXTURE_DIR

KEYS = ("total_trades", "net_pnl", "final_equity", "hit_rate_pct", "sharpe_ratio", "max_drawdown_pct", "t_statistic")


def main(before_dir: str) -> None:
    before, after = Path(before_dir), FIXTURE_DIR
    for f in sorted(after.glob("*.json")):
        old, new = json.loads((before / f.name).read_text()), json.loads(f.read_text())
        if old == new:
            continue
        print(f"### {f.stem}")
        if f.stem == "backtest":
            print("| run | " + " | ".join(KEYS) + " |\n|---|" + "---|" * len(KEYS))
            for run in old:
                cells = [f"{old[run].get(k)} → {new[run].get(k)}" for k in KEYS]
                print(f"| {run} | " + " | ".join(cells) + " |")
        elif f.stem == "broker":
            for name in old:
                o, n = old[name], new[name]
                print(
                    f"- {name}: cash {o['cash']:.2f} → {n['cash']:.2f}; equity {o['total_equity']:.2f} → "
                    f"{n['total_equity']:.2f}; trades {len(o['trades'])} → {len(n['trades'])}"
                )
        elif f.stem == "paper_cycle":
            o, n = old["broker"], new["broker"]
            print(
                f"- cash {o['cash']:.2f} → {n['cash']:.2f}; equity {o['total_equity']:.2f} → {n['total_equity']:.2f}; "
                f"trades {len(o['trades'])} → {len(n['trades'])}; brier {old['brier']} → {new['brier']}"
            )
        else:
            print("- changed (see diff)")
        print()


if __name__ == "__main__":
    main(sys.argv[1])
