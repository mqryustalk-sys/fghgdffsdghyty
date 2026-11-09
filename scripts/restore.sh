#!/bin/sh
set -eu
: "${1:?Usage: restore.sh backup.dump (restores into EMPTY terminal database)}"
docker compose exec -T db pg_restore -U terminal -d terminal --exit-on-error < "$1"
