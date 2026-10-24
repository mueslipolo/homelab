#!/usr/bin/env bash
# Dumps Forgejo's data via its own `forgejo dump` export, copies the result
# out of the container, and prunes old dumps. Runs as root from
# scripts/run-jobs.sh (see scripts/jobs.conf) — needs docker exec/cp access.
#
# A dump only gets its final name once it is complete: its end-of-zip record
# is present (a truncation check). Anything else — a hang, a full disk, a killed copy — leaves
# no file the dashboard or Hyper Backup could mistake for a good dump.
set -euo pipefail

# shellcheck source=scripts/lib/dump.sh
. "$(dirname -- "$0")/lib/dump.sh"

readonly CONTAINER="forgejo"
readonly APP_INI="/var/lib/gitea/custom/conf/app.ini"
readonly CONTAINER_TMP="/tmp/gitea"
# Host path the dump is copied to. No default: a placeholder here would
# surface as a confusing `docker cp` failure, and a real path would put
# deployment-specific state in a tracked file.
readonly DUMP_DIR="${FORGEJO_DUMP_DIR:?set FORGEJO_DUMP_DIR to the NAS dumps directory}"
readonly KEEP_DAYS="${FORGEJO_DUMP_KEEP_DAYS:-14}"
# `forgejo dump` of a small instance takes seconds; this only stops a hang
# from running into the Hyper Backup window (20:40).
readonly DUMP_TIMEOUT="${FORGEJO_DUMP_TIMEOUT:-20m}"

dump_name="forgejo-dump-$(date +%Y%m%d-%H%M%S).zip"
partial="${DUMP_DIR}/.${dump_name}.partial"

cleanup() {
	rm -f -- "$partial"
	docker exec "${CONTAINER}" rm -f "${CONTAINER_TMP}/${dump_name}" 2>/dev/null || true
}

main() {
	trap cleanup EXIT

	timeout "$DUMP_TIMEOUT" \
		docker exec "${CONTAINER}" forgejo dump -c "${APP_INI}" --file "${CONTAINER_TMP}/${dump_name}"
	timeout "$DUMP_TIMEOUT" \
		docker cp "${CONTAINER}:${CONTAINER_TMP}/${dump_name}" "$partial"

	publish_dump "$partial" "$DUMP_DIR" "$dump_name" 'forgejo-dump-*.zip' "$KEEP_DAYS"
}

main "$@"
