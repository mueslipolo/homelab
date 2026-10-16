#!/usr/bin/env bash
# Asks Mealie for a backup through its own API, copies the zip out of the
# container into MEALIE_DUMP_DIR, and prunes old copies on both sides. Runs as
# root from scripts/run-jobs.sh (see scripts/jobs.conf) -- needs docker exec/cp.
#
# Mealie's backup export is portable across versions, unlike a raw Postgres
# data directory, which is why Hyper Backup takes these dumps and not
# MEALIE_PGDATA_PATH (docs/OPERATIONS.md).
set -euo pipefail

# shellcheck source=scripts/lib/dump.sh
. "$(dirname -- "$0")/lib/dump.sh"

readonly DUMP_DIR="${MEALIE_DUMP_DIR:?set MEALIE_DUMP_DIR to the NAS dumps directory}"
# Mealie → profile → API Tokens, as an admin user. Kept in jobs.env.
readonly TOKEN="${MEALIE_API_TOKEN:?set MEALIE_API_TOKEN to a Mealie API token of an admin user}"
readonly KEEP_DAYS="${MEALIE_DUMP_KEEP_DAYS:-14}"
readonly TIMEOUT="${MEALIE_DUMP_TIMEOUT:-15m}"
readonly BACKUPS="/app/data/backups" # inside the container

# The stack sets no container_name; find the container by its Compose service.
container=$(docker ps --filter label=com.docker.compose.service=mealie --format '{{.Names}}' | head -n 1)
[ -n "$container" ] || { echo "No running Mealie container (compose service 'mealie')" >&2; exit 1; }

dump_name="mealie-backup-$(date +%Y%m%d-%H%M%S).zip"
partial="${DUMP_DIR}/.${dump_name}.partial"
trap 'rm -f -- "$partial"' EXIT

# 1. The backup itself, requested from inside the container: no network path,
#    certificate or hostname involved. The token goes in on stdin, so it never
#    appears in a process list or in `docker inspect`.
# shellcheck disable=SC2016 # $ in the Python below is Python's, not ours
printf '%s' "$TOKEN" | timeout "$TIMEOUT" docker exec -i "$container" python3 -c '
import json, sys, urllib.error, urllib.request
token = sys.stdin.read().strip()
request = urllib.request.Request(
    "http://127.0.0.1:9000/api/admin/backups", method="POST",
    headers={"Authorization": "Bearer " + token},
)
try:
    with urllib.request.urlopen(request, timeout=840) as resp:
        body = json.load(resp)
except urllib.error.HTTPError as exc:
    sys.exit(f"Mealie refused the backup: HTTP {exc.code} {exc.read()[:200]!r}"
             " -- is MEALIE_API_TOKEN the token of an admin user?")
if body.get("error"):
    sys.exit("Mealie: " + str(body.get("message")))
'

# 2. Mealie names backups by time; the newest is the one just made.
newest=$(docker exec "$container" sh -c "ls -t $BACKUPS/*.zip 2>/dev/null | head -n 1")
[ -n "$newest" ] || { echo "Mealie reported success but $BACKUPS has no zip" >&2; exit 1; }
timeout "$TIMEOUT" docker cp "$container:$newest" "$partial"
publish_dump "$partial" "$DUMP_DIR" "$dump_name" 'mealie-backup-*.zip' "$KEEP_DAYS"

# 3. Mealie keeps every backup in its data folder; the copies here are the
#    history, so keep only the last couple of days there.
docker exec "$container" find "$BACKUPS" -maxdepth 1 -name '*.zip' -mtime +2 -delete
