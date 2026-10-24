#!/usr/bin/env bash
# Runs Paperless's own document_exporter into its export folder
# (PAPERLESS_DATA_ROOT/export, mounted at /usr/src/paperless/export), which
# Hyper Backup takes along with the share. Runs as root from
# scripts/run-jobs.sh (see scripts/jobs.conf) -- needs docker exec.
#
# Incremental: unchanged documents are not rewritten, and -d deletes files of
# documents that are gone, so the folder mirrors the archive and the backups
# only carry what changed. document_importer restores the export into the
# same Paperless version (pin that image tag, then upgrade); the raw media
# folder alone would also need the database.
set -euo pipefail

readonly EXPORT="/usr/src/paperless/export" # inside the container
readonly TIMEOUT="${PAPERLESS_EXPORT_TIMEOUT:-25m}" # done before the 20:40 S3 backup

# Found by image, whatever the container is called: only Paperless's own
# webserver runs paperless-ngx.
container=$(docker ps --format '{{.Names}} {{.Image}}' | awk '$2 ~ /paperless-ngx/ { print $1; exit }')
[ -n "$container" ] || { echo "No running Paperless container (image paperless-ngx)" >&2; exit 1; }

# The command the former DSM task ran, from manage.py's directory. Its exit
# code is the verdict: files are only rewritten when they changed, so their
# age says nothing about whether the export ran.
timeout "$TIMEOUT" docker exec "$container" /bin/sh -c "./manage.py document_exporter -d $EXPORT"

# Without a manifest, document_importer cannot restore the folder.
docker exec "$container" test -s "$EXPORT/manifest.json" ||
	{ echo "Export finished but $EXPORT has no manifest.json" >&2; exit 1; }
echo "Export written to $EXPORT ($(docker exec "$container" sh -c "ls '$EXPORT' | wc -l") entries)"
