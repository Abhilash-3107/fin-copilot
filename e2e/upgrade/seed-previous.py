"""Seeds the previous release's demo ledger on a chosen day, with that release's own seeder.

Run with the previous release's interpreter (the upgrade journey does it with
`uvx --from finance-copilot==<from> python -I seed-previous.py <data dir> <YYYY-MM-DD>`).
The release has no FC_TODAY: its demo route calls seed(conn, date.today()), and that is the only
place it reads the date. Calling seed() with the pinned day gives the ledger the route would give
on that day, so the journey does not depend on the day it is run.
"""
import sys
from datetime import date
from pathlib import Path

from finance_copilot.db.connection import get_migrated_db
from finance_copilot.demo_seed import seed

data_dir, day = Path(sys.argv[1]), date.fromisoformat(sys.argv[2])
conn = get_migrated_db(str(data_dir / "finance.db"))
summary = seed(conn, day)
conn.close()
print(f"seeded the previous release's demo on {day}: {summary['transactions']} payments")
