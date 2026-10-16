#!/usr/bin/env bash
# Fire the extraction webhook at an existing document, by id.
#
# Useful because the real trigger ("Document Added") only fires at consumption,
# and Paperless rejects a re-upload of a document it already has. This posts to
# /hook exactly as Paperless would.
#
# The secret is read from the container's own environment and never appears on
# the host command line, in your shell history, or in the process list.
#
#   ./fire-hook.sh 1234              one document
#   ./fire-hook.sh 1234 1235 1236    several
#
# THIS WRITES. There is no dry-run on the webhook path — the handler answers
# 200 immediately and works in the background, so the response says only that
# the request was accepted, never that extraction succeeded. Use
# `paperless-tarmed apply --id <id>` instead for a dry run.
set -uo pipefail

readonly CONTAINER="${PAPERLESS_TARMED_CONTAINER:-paperless-tarmed}"

if [ $# -eq 0 ]; then
	awk 'NR>1 && /^#/ {sub(/^# ?/, ""); print; next} NR>1 {exit}' "$0"
	exit 2
fi

if ! docker inspect "$CONTAINER" >/dev/null 2>&1; then
	printf 'No container named %s. Set PAPERLESS_TARMED_CONTAINER to override.\n' \
		"$CONTAINER" >&2
	exit 1
fi

status=0
for doc_id in "$@"; do
	case $doc_id in
	'' | *[!0-9]*)
		printf '%-8s skipped — not a numeric document id\n' "$doc_id" >&2
		status=1
		continue
		;;
	esac

	# Runs inside the container: reads the secret from its own env, posts to
	# its own listener. Exits non-zero on any status other than 200 so the
	# loop below can report it.
	if ! docker exec -i "$CONTAINER" python - "$doc_id" <<-'PY'
		import json, os, sys, urllib.error, urllib.request

		doc_id = sys.argv[1]
		secret = os.environ.get("PAPERLESS_TARMED_WEBHOOK_SECRET", "")
		if not secret:
		    sys.exit("PAPERLESS_TARMED_WEBHOOK_SECRET is not set in the container")

		req = urllib.request.Request(
		    "http://127.0.0.1:8000/hook",
		    data=json.dumps({"doc_id": int(doc_id)}).encode(),
		    headers={
		        "Content-Type": "application/json",
		        "X-Paperless-Tarmed-Secret": secret,
		    },
		)
		try:
		    with urllib.request.urlopen(req, timeout=30) as resp:
		        print(f"{doc_id:>8}  {resp.status}  {resp.read().decode().strip()}")
		except urllib.error.HTTPError as exc:
		    hint = {
		        400: "no document id in the request — check the app, not this script",
		        401: "secret rejected — the container's own env does not match itself?",
		    }.get(exc.code, exc.reason)
		    sys.exit(f"{doc_id:>8}  {exc.code}  {hint}")
		except urllib.error.URLError as exc:
		    sys.exit(f"{doc_id:>8}  ----  not listening: {exc.reason}")
	PY
	then
		status=1
	fi
done

printf '\nAccepted ≠ extracted. The outcome is only in the log:\n'
printf '  docker logs -f %s\n' "$CONTAINER"
exit "$status"
