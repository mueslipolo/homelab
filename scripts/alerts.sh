#!/usr/bin/env bash
# Pushes the dashboard's warnings to ntfy as they happen, so nobody has to look
# at the dashboard: a scheduled job failed; a container crashed, restart-loops
# or is unhealthy; a phone or computer stopped backing up; the volume is filling
# up or grew unusually in a day; the scheduler cannot update its clone. Run by scripts/run-jobs.sh after every status.json refresh
# (every 15 minutes) when NTFY_TOKEN is set.
#
# Pushes on changes only: an item turning amber or red, or getting worse, and
# its return to normal. One late backup is one notification, not one per tick.
# The levels are the dashboard's own (stacks/dashboard/html/index.html).
#
# jobs.env (beside what run-jobs.sh already needs):
#   NTFY_TOKEN          ntfy publish token
#   NTFY_ALERT_TOPIC    default homelab
#   DASHBOARD_URL       optional, e.g. https://dashboard.<domain>: tapping opens its tab
#   ALERT_GROWTH_GB     default 100: a day's growth above this is reported
set -euo pipefail

# shellcheck source=scripts/lib/json.sh
. "$(dirname -- "$0")/lib/json.sh"
# shellcheck source=scripts/lib/ntfy.sh
. "$(dirname -- "$0")/lib/ntfy.sh"

readonly DIR="${DASHBOARD_LOCAL_DIR:?set DASHBOARD_LOCAL_DIR to the dashboard local folder}"
readonly STATE_FILE="${OPS_HOME:?set OPS_HOME}/state/alerts.tsv"
readonly TOPIC="${NTFY_ALERT_TOPIC:-homelab}"
readonly NOW="${ALERTS_NOW:-$(date +%s)}"
: "${NTFY_TOKEN:?set NTFY_TOKEN to an ntfy token that can publish}"

[ -f "$DIR/status.json" ] || { echo "No status.json yet"; exit 0; }
storage="$DIR/storage.json"
[ -f "$storage" ] || storage=/dev/null

# One line per watched item: key, level (ok/warn/bad), dashboard tab, title,
# detail; tab-separated. Recovery from a growth spike is not worth a push, so
# growth only ever reports warn.
# shellcheck disable=SC2016 # the $names in the jq program are jq's
current=$(
	{
		cat "$DIR/status.json"
		cat "$storage"
	} | _jq -r -s --argjson now "$NOW" --argjson growth_gb "${ALERT_GROWTH_GB:-100}" '
	def days(s): (s / 86400 | floor);
	def clean: tostring | gsub("[\t\n\r]"; " ");
	(.[0]) as $st | (.[1] // {}) as $sg |
	[
	  (($st.jobs // [])[] |
	    [ "job:" + .name, (if .exit != 0 then "bad" else "ok" end), "system",
	      (if .exit != 0 then "Job failed: " + .name else "Job ok again: " + .name end),
	      (if .exit != 0 then "exit \(.exit): \(.message // "")" else "" end) ]),

	  (($st.devices.devices // [])[] |
	    ([.last_seen // 0, .newest_photo // 0] | max) as $last |
	    (if $last == 0 then 1e12 else $now - $last end) as $age |
	    (if $age >= .max_days * 86400 then "bad"
	     elif $age >= ((.max_days / 2) | ceil) * 86400 then "warn" else "ok" end) as $lvl |
	    ((.owner[:1] | ascii_upcase) + .owner[1:] + " · " + .name) as $who |
	    [ "device:" + .owner + "/" + .name, $lvl, "backups",
	      (if $lvl == "ok" then $who + " is uploading again"
	       elif $last == 0 then $who + ": no upload seen yet"
	       else $who + ": nothing uploaded for \(days($age)) days" end),
	      (if $lvl == "ok" then "" else "limit \(.max_days) days" end) ]),

	  (($st.containers // [])[] |
	    (if .state == "restarting" then ["bad", "restart-looping (exit \(.exit_code))"]
	     elif (.state == "exited" or .state == "dead") and
	          (.oom or (.exit_code as $e | [0, 137, 143] | index($e)) == null)
	       then ["bad", (if .oom then "killed: out of memory" else "crashed (exit \(.exit_code))" end)]
	     elif .health == "unhealthy" then ["warn", "unhealthy"]
	     elif .state == "running" and .restarts > 0 and ($now - (.started // 0)) < 1200
	       then ["warn", "restarted by docker after a crash (\(.restarts) so far)"]
	     else ["ok", ""] end) as $c |
	    [ "container:" + .name, $c[0], "system",
	      (if $c[0] == "ok" then "Container " + .name + " is running again"
	       else "Container " + .name + ": " + $c[1] end),
	      (if .project != "" then "stack " + .project else "" end) ]),

	  (($st.volume // empty) |
	    (if .used_pct >= 90 then "bad" elif .used_pct >= 80 then "warn" else "ok" end) as $lvl |
	    [ "volume", $lvl, "storage",
	      (if $lvl == "ok" then "Volume below 80 % again" else "Volume \(.used_pct) % full" end),
	      "\(.avail_kb / 1048576 | floor) GB free on \(.path)" ]),

	  (($st.scheduler // empty) |
	    [ "scheduler", (if .pull_ok then "ok" else "warn" end), "system",
	      (if .pull_ok then "Scheduler updates its clone again" else "Scheduler cannot update its git clone" end),
	      (if .pull_ok then "" else .pull_error // "" end) ]),

	  (($sg.daily // []) | select(length >= 2) |
	    (.[-1][1] - .[-2][1]) as $d | .[-1][0] as $day |
	    [ "growth:" + $day, (if $d > $growth_gb * 1073741824 then "warn" else "ok" end), "storage",
	      "Used space grew \($d / 1073741824 | floor) GB on \($day)",
	      "more than the \($growth_gb) GB alert threshold" ])
	] | .[] | map(clean) | join("\t")'
)

rank() { case $1 in bad) echo 2 ;; warn) echo 1 ;; *) echo 0 ;; esac; }
touch -- "$STATE_FILE"
declare -A before=()
while IFS=$'\t' read -r key level; do
	[ -n "$key" ] && before[$key]=$level
done <"$STATE_FILE"

sent=0
new_state=""
while IFS=$'\t' read -r key level tab title detail; do
	[ -n "$key" ] || continue
	new_state+="$key"$'\t'"$level"$'\n'
	old=${before[$key]:-ok}
	[ "$level" = "$old" ] && continue
	if [ "$(rank "$level")" -gt "$(rank "$old")" ]; then
		priority=$([ "$level" = bad ] && echo high || echo default)
		tags=$([ "$level" = bad ] && echo rotating_light || echo warning)
	else
		# Better than before: worth a quiet word only when it is fine again.
		[ "$level" = ok ] || continue
		[[ $key == growth:* ]] && continue
		priority=low tags=white_check_mark
	fi
	click=""
	[ -n "${DASHBOARD_URL:-}" ] && click="${DASHBOARD_URL%/}/#$tab"
	if ntfy_send "$TOPIC" "$title" "$priority" "$tags" "$click" <<<"${detail:-see the dashboard}"; then
		echo "Sent: $title"
		sent=$((sent + 1))
	else
		# Keep the old level, so the next run tries again.
		echo "alerts: could not send '$title'" >&2
		new_state=${new_state%"$key"$'\t'"$level"$'\n'}
		new_state+="$key"$'\t'"$old"$'\n'
	fi
done <<<"$current"

printf '%s' "$new_state" | write_atomic "$STATE_FILE"
echo "$sent notification(s) sent"
