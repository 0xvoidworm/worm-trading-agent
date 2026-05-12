#!/usr/bin/env python3
"""Launcher untuk TUI dashboard."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dashboard.tui import run_dashboard

if __name__ == "__main__":
    import argparse
    p = argparse.ArgumentParser()
    p.add_argument("--refresh", type=int, default=60, help="refresh interval seconds")
    args = p.parse_args()
    run_dashboard(refresh_sec=args.refresh)
