#!/bin/sh
# Seed (or re-seed) the demo ledger, keep a pristine snapshot for the hourly
# reset, then serve. The generator is date-anchored, so every container start
# refreshes the data to look current.
set -eu

mkdir -p "$FC_DATA_DIR"

DB="$FC_DATA_DIR/finance.db"
SNAPSHOT="$FC_DATA_DIR/demo-snapshot.db"

if [ ! -f "$SNAPSHOT" ] || [ -n "${FC_DEMO_RESEED:-}" ]; then
    rm -f "$DB" "$DB-wal" "$DB-shm"
    python -m finance_copilot.demo_seed --db "$DB"
    cp "$DB" "$SNAPSHOT"
fi

if [ ! -f "$DB" ]; then
    cp "$SNAPSHOT" "$DB"
fi

exec python -m finance_copilot
