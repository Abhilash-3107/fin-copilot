"""Thin wrapper: the demo generator now lives in finance_copilot.demo_seed
(shipped in the wheel so the app's "Load demo data" button can use it).
This script keeps the documented dev entry point working:

    uv run python scripts/seed_demo_data.py [--db path.db] [--wipe]
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from finance_copilot.demo_seed import main

if __name__ == "__main__":
    main()
