#!/bin/sh
# Hourly demo reset, run from the host's cron:
#   0 * * * *  /srv/finance-copilot/reset.sh
# Weekly full re-seed (keeps the date-anchored data looking current):
#   0 4 * * 1  FC_DEMO_RESEED=1 /srv/finance-copilot/reset.sh
#
# Restores the pristine snapshot over whatever visitors changed and restarts
# the container. Nothing on this box matters; it is disposable by design.
set -eu

CONTAINER="${CONTAINER:-fc-demo}"

if [ -n "${FC_DEMO_RESEED:-}" ]; then
    # Force the entrypoint to regenerate the snapshot with fresh dates.
    docker exec "$CONTAINER" sh -c 'rm -f "$FC_DATA_DIR/demo-snapshot.db"'
fi

docker restart "$CONTAINER"
