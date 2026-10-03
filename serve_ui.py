#!/usr/bin/env python3
"""
Launcher script for the Indian Quant Trading Bot Web UI & Server.
Run:
    python3 serve_ui.py
    python3 serve_ui.py --port 8080
"""

import argparse

from trade.web.server import run_server


def main():
    parser = argparse.ArgumentParser(description="Start the Indian Quant Trading Web Dashboard.")
    parser.add_argument("--port", type=int, default=8080, help="Port to bind the web server (default: 8080)")
    args = parser.parse_args()

    print("=" * 72)
    print("  QUANTUM REFLEX // NSE INTRADAY TRADING ENGINE")
    print(f"  Starting Local Web Server on http://localhost:{args.port}")
    print("=" * 72)
    run_server(args.port)


if __name__ == "__main__":
    main()
