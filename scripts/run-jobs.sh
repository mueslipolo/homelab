#!/usr/bin/env bash
# The NAS's one scheduled task. DSM Task Scheduler runs this as root every 15
# minutes; it decides which jobs in scripts/jobs.conf are due and runs them.
#
#   run-jobs.sh /path/to/jobs.env
#
# Each tick:
#   1. updates this repo clone from Forgejo (best effort: if Forgejo is down,
#      the last good checkout keeps running — backups must not depend on it),
#   2. runs every due job under a timeout, logging to $OPS_HOME/logs/<job>/,
#      recording $OPS_HOME/state/<job>.json, and pinging healthchecks.io,
#   3. refreshes the dashboard's status.json,
#   4. pushes what turned amber or red, or recovered, to ntfy (alerts.sh).
#
# jobs.env is local to the NAS (root-only, not in git): paths, and the
# healthchecks.io ping key. See docs/guides/10-backups-and-jobs.md for its variables.
set -uo pipefail # no -e: one failing job must not stop the others

readonly ENV_FILE="${1:?usage: run-jobs.sh /path/to/jobs.env}"
# shellcheck disable=SC1090
set -a && . "$ENV_FILE" && set +a

# Task Scheduler's PATH lacks docker (/usr/local/bin).
export PATH="$PATH:/usr/local/bin:/usr/bin:/bin"

REPO=$(cd "$(dirname -- "$0")/.." && pwd)
readonly REPO
readonly SCRIPTS="${JOBS_DIR:-$REPO/scripts}"
readonly CONF="${JOBS_CONF:-$SCRIPTS/jobs.conf}"
readonly OPS_HOME="${OPS_HOME:?set OPS_HOME in jobs.env (state and logs live there)}"
readonly STATE="$OPS_HOME/state"
readonly LOGS="$OPS_HOME/logs"
readonly RETRY_AFTER="${JOBS_RETRY_AFTER:-3600}" # seconds between attempts after a failure
readonly LOG_KEEP_DAYS="${JOBS_LOG_KEEP_DAYS:-30}"
readonly HC_BASE="${HC_PING_BASE:-https://hc-ping.com}"
readonly HC_PREFIX="${HC_SLUG_PREFIX:-homelab-}"

# shellcheck source=scripts/lib/json.sh
. "$REPO/scripts/lib/json.sh"

for tool in flock timeout curl; do
	command -v "$tool" >/dev/null || { echo "run-jobs: $tool not found" >&2; exit 1; }
done
mkdir -p "$STATE" "$LOGS"
chmod 700 "$OPS_HOME"

# One tick at a time: a long job may still be running when the next starts.
exec 9>"$OPS_HOME/run-jobs.lock"
flock -n 9 || exit 0

now() { echo "${RUN_JOBS_NOW:-$(date +%s)}"; }

# healthchecks.io: one check per job, addressed by slug and created on first
# ping, so the only secret is the project's ping key. No key, no pings.
hc() { # <slug> <signal: start | 0..255> [body file]
	[ -n "${HC_PING_KEY:-}" ] || return 0
	local url="$HC_BASE/$HC_PING_KEY/$HC_PREFIX$1" body=/dev/null reply
	[ "$2" = start ] && url+="/start" || url+="/$2"
	url+="?create=1"
	[ -n "${3:-}" ] && body=$3
	# Never fatal, but never silent either: say which ping failed and what
	# healthchecks.io answered, without printing the key.
	if ! reply=$(tail -c 10000 -- "$body" |
		curl -sS -m 10 --retry 3 -w ' (HTTP %{http_code})' --data-binary @- "$url" 2>&1) ||
		[[ $reply != *"(HTTP 2"* ]]; then
		echo "run-jobs: healthchecks.io ping $HC_PREFIX$1/$2 failed: ${reply//$HC_PING_KEY/<key>}" >&2
	fi
}

# The key goes into a URL path: catch a pasted URL, API key or quotes early.
if [ -n "${HC_PING_KEY:-}" ]; then
	if [[ $HC_PING_KEY =~ ^[0-9a-f]{8}-([0-9a-f]{4}-){3}[0-9a-f]{12}$ ]]; then
		echo "run-jobs: HC_PING_KEY is a check's UUID, not the project's ping key (Project Settings -> Ping key -> Create ping key)" >&2
	elif ! [[ $HC_PING_KEY =~ ^[A-Za-z0-9_-]+$ ]]; then
		echo "run-jobs: HC_PING_KEY must be the project's ping key (letters, digits, - and _ only)" >&2
	fi
fi

# ------------------------------------------------------------- 1. update
sync_repo() {
	local before after err="" ok=1 ok_at
	before=$(git -C "$REPO" rev-parse --short HEAD 2>/dev/null || echo unknown)
	if [ -n "${RUN_JOBS_NO_PULL:-}" ]; then
		err="pull disabled"
	elif ! command -v git >/dev/null; then
		ok=0 err="git not installed"
	elif ! err=$(timeout 60 git -C "$REPO" pull -q --ff-only 2>&1); then
		ok=0
	else
		err=""
	fi
	after=$(git -C "$REPO" rev-parse --short HEAD 2>/dev/null || echo unknown)

	ok_at=$(sed -n 's/.*"pull_ok_at": \([0-9]*\).*/\1/p' "$STATE/_scheduler.json" 2>/dev/null)
	[ "$ok" = 1 ] && ok_at=$(now)
	printf '{"tick": %s, "sha": %s, "pull_ok": %s, "pull_ok_at": %s, "pull_error": %s}\n' \
		"$(now)" "$(json_str "$after")" "$([ "$ok" = 1 ] && echo true || echo false)" \
		"${ok_at:-null}" "$(json_str "${err:0:300}")" | write_atomic "$STATE/_scheduler.json"

	# New code arrived: restart once on it, so this tick already uses it.
	if [ "$before" != "$after" ] && [ -z "${RUN_JOBS_REEXEC:-}" ]; then
		exec env RUN_JOBS_REEXEC=1 "$REPO/scripts/run-jobs.sh" "$ENV_FILE"
	fi
}

# --------------------------------------------------------------- 2. jobs
is_due() { # <name> <HH:MM | tick>
	local t today now_hm okday last_try
	t=$(now)
	last_try=$(cat "$STATE/$1.lasttry" 2>/dev/null || echo 0)
	# 'tick' jobs run every tick; after a failure, only once the retry delay
	# has passed (a failing build must not rerun every 15 minutes).
	if [ "$2" = tick ]; then
		[ ! -f "$STATE/$1.failed" ] || [ "$((t - last_try))" -ge "$RETRY_AFTER" ]
		return
	fi
	today=$(date -d "@$t" +%F)
	now_hm=$(date -d "@$t" +%H%M)
	okday=$(cat "$STATE/$1.okday" 2>/dev/null || true)
	[ "$okday" != "$today" ] &&
		[ "$((10#$now_hm))" -ge "$((10#${2/:/}))" ] &&
		[ "$((t - last_try))" -ge "$RETRY_AFTER" ]
}

run_job() { # <name> <HH:MM> <timeout> <script> [args...]
	local name=$1 at=$2 limit=$3 script=$4 started ended rc log sha
	shift 4
	started=$(now)
	mkdir -p "$LOGS/$name"
	log="$LOGS/$name/$(date -d "@$started" +%Y%m%d-%H%M%S).log"
	echo "$started" >"$STATE/$name.lasttry"
	hc "$name" start

	# stdin from /dev/null: the caller's stdin is jobs.conf itself.
	timeout "$limit" bash "$SCRIPTS/$script" "$@" </dev/null >"$log" 2>&1
	rc=$?
	[ "$rc" = 124 ] && echo "run-jobs: killed after $limit" >>"$log"
	ended=$(now)
	if [ "$rc" = 0 ]; then
		date -d "@$started" +%F >"$STATE/$name.okday"
		rm -f "$STATE/$name.failed"
	else
		: >"$STATE/$name.failed"
	fi

	sha=$(git -C "$REPO" rev-parse --short HEAD 2>/dev/null || echo unknown)
	printf '{"name": %s, "at": %s, "started": %s, "ended": %s, "exit": %s, "last_ok_day": %s, "sha": %s, "message": %s}\n' \
		"$(json_str "$name")" "$(json_str "$at")" "$started" "$ended" "$rc" \
		"$(json_str "$(cat "$STATE/$name.okday" 2>/dev/null)")" "$(json_str "$sha")" \
		"$(json_str "$(tail -n 1 -- "$log" | cut -c1-200)")" | write_atomic "$STATE/$name.json"
	hc "$name" "$rc" "$log"
}

run_due_jobs() {
	local name at limit script
	while read -r name at limit script rest; do
		case $name in '' | '#'*) continue ;; esac
		if ! [[ $at =~ ^([0-2][0-9]:[0-5][0-9]|tick)$ ]] || [ -z "$script" ]; then
			echo "run-jobs: bad line in $CONF: $name $at $limit $script" >&2
			continue
		fi
		# Plain word splitting is intended: args are simple tokens.
		# shellcheck disable=SC2086
		is_due "$name" "$at" && run_job "$name" "$at" "$limit" "$script" $rest
	done <"$CONF"
}

# ------------------------------------------------------------ 3. report
refresh_status() {
	[ -n "${DASHBOARD_LOCAL_DIR:-}" ] || return 0
	JOBS_STATE_DIR="$STATE" bash "$SCRIPTS/dashboard-status.sh" ||
		echo "run-jobs: dashboard-status.sh failed" >&2
}

# ------------------------------------------------------------ 4. alerts
# Push what turned amber or red (or recovered) to ntfy; never fatal.
send_alerts() {
	[ -n "${DASHBOARD_LOCAL_DIR:-}" ] && [ -n "${NTFY_TOKEN:-}" ] || return 0
	bash "$SCRIPTS/alerts.sh" >"$LOGS/alerts.log" 2>&1 ||
		echo "run-jobs: alerts.sh failed, see $LOGS/alerts.log" >&2
}

main() {
	sync_repo
	run_due_jobs
	refresh_status
	send_alerts
	find "$LOGS" -type f -name '*.log' -mtime "+$LOG_KEEP_DAYS" -delete 2>/dev/null

	# Heartbeat: healthchecks.io alerts when this stops arriving (NAS down,
	# task disabled). It also fails once the clone has not updated for a day,
	# so silently stale scripts get noticed.
	local ok_at
	ok_at=$(sed -n 's/.*"pull_ok_at": \([0-9]*\).*/\1/p' "$STATE/_scheduler.json")
	if [ -z "${RUN_JOBS_NO_PULL:-}" ] && [ "$(($(now) - ${ok_at:-0}))" -gt 86400 ]; then
		hc run-jobs 1 "$STATE/_scheduler.json"
	else
		hc run-jobs 0
	fi
}

main
