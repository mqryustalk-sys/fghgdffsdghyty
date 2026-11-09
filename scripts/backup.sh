#!/bin/sh
set -eu
docker compose exec -T db pg_dump -U terminal -Fc terminal > "${1:-terminal-backup.dump}"
