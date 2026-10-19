#!/usr/bin/env bash
# Pushes a notification to ntfy when Renovate has something new waiting for
# approval: majors, and Forgejo/Portainer updates, which no merge deploys
# (renovate.json). Minor updates merge themselves and are not reported.
#
# Reads the "Pending Approval" section of Renovate's Dependency Dashboard issue
# in Forgejo, and notifies only about entries not seen on the previous run, so
# one update is one notification, not one a day. Runs from scripts/run-jobs.sh
# after Renovate's daily run (scripts/jobs.conf).
#
# jobs.env:
#   FORGEJO_TOKEN    Forgejo token with read access to the repo's issues
#   FORGEJO_REPO     owner/repo, e.g. admin/homelab
#   NTFY_TOKEN       ntfy token allowed to publish to NTFY_TOPIC
#   FORGEJO_URL      default http://127.0.0.1:3000 (the host's published port)
#   NTFY_URL         default http://127.0.0.1:2586 (ntfy's loopback port)
#   NTFY_TOPIC       default homelab
set -euo pipefail

# shellcheck source=scripts/lib/json.sh
. "$(dirname -- "$0")/lib/json.sh"
# shellcheck source=scripts/lib/ntfy.sh
. "$(dirname -- "$0")/lib/ntfy.sh"

readonly FORGEJO_URL="${FORGEJO_URL:-http://127.0.0.1:3000}"
readonly FORGEJO_TOKEN="${FORGEJO_TOKEN:?set FORGEJO_TOKEN to a Forgejo token that can read the repo}"
readonly FORGEJO_REPO="${FORGEJO_REPO:?set FORGEJO_REPO to owner/repo}"
: "${NTFY_TOKEN:?set NTFY_TOKEN to an ntfy token that can publish}"
readonly NTFY_TOPIC="${NTFY_TOPIC:-homelab}"
readonly SEEN="${OPS_HOME:?set OPS_HOME}/state/renovate-notified.txt"

# Tokens go in on stdin (curl -K -), never on the command line.
issues=$(printf 'header = "Authorization: token %s"\n' "$FORGEJO_TOKEN" |
	curl -sS -m 30 --fail -K - \
		"$FORGEJO_URL/api/v1/repos/$FORGEJO_REPO/issues?state=open&type=issues&q=Dependency%20Dashboard&limit=10")

# Number, link, then the body, of the issue Renovate titles "Dependency Dashboard".
dashboard=$(_jq -r '[.[] | select(.title == "Dependency Dashboard")][0] // empty |
	(.number | tostring), .html_url, .body' <<<"$issues")
if [ -z "$dashboard" ]; then
	echo "No open Dependency Dashboard issue in $FORGEJO_REPO: has Renovate run?"
	exit 0
fi
url=$(sed -n 2p <<<"$dashboard")

# One line per update waiting: the text after the approve-branch marker, e.g.
#  - [ ] <!-- approve-branch=renovate/x-13.x -->chore(deps): update x to v13
pending=$(sed -n '3,$p' <<<"$dashboard" | tr -d '\r' | awk '
	/^## / { inside = ($0 ~ /^## Pending Approval/) ; next }
	inside && /<!-- approve-branch=/ { sub(/.*-->[[:space:]]*/, ""); print }' | sort -u)

touch -- "$SEEN"
new=$(comm -23 <(printf '%s\n' "$pending" | sed '/^$/d') <(sort -u -- "$SEEN"))
total=$(printf '%s\n' "$pending" | sed '/^$/d' | wc -l | tr -d ' ')

if [ -n "$new" ]; then
	count=$(printf '%s\n' "$new" | wc -l | tr -d ' ')
	if [ "$count" -eq 1 ]; then title="1 update waiting for your approval"; else title="$count updates waiting for your approval"; fi
	body="$new"
	if [ "$total" -gt "$count" ]; then body+=$'\n'"($total waiting in all)"; fi
	ntfy_send "$NTFY_TOPIC" "$title" default package "$url" <<<"$body"
	echo "Notified: $title"
else
	echo "Nothing new waiting ($total in all)"
fi

# Remember what is pending now: an update approved and gone, then back, is new.
printf '%s\n' "$pending" | sed '/^$/d' | write_atomic "$SEEN"
