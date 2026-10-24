# shellcheck shell=bash
# What protects which shared folder, read from DSM itself so the dashboard's
# Backups tab needs no hand-written plan: the shared folders, each one's
# snapshot schedule and newest snapshot, and every Hyper Backup task with its
# destination, schedule, encryption and folders. Sourced by
# dashboard-status.sh (needs scripts/lib/json.sh); runs as root.
#
# Every source is optional: off DSM, or where a layout differs, the part is
# just missing and the page shows it as "no data", never as protected.

# DSM keeps its tools out of root's usual PATH when run from Task Scheduler.
PATH="$PATH:/usr/syno/sbin:/usr/syno/bin"
HYPERBACKUP_CONF="${HYPERBACKUP_CONF:-/var/packages/HyperBackup/etc/synobackup.conf}"

# Scheduled tasks as "id|state|type|days|HH:MM|app|last argument" lines, from
# synoschedtask's blocks of "Key: [value]" lines.
_schedules() {
	command -v synoschedtask >/dev/null || return 0
	synoschedtask --get 2>/dev/null | awk '
		function val(s) { sub(/^[^[]*\[/, "", s); sub(/\][^]]*$/, "", s); return s }
		function flush() { if (id != "") print id "|" state "|" type "|" days "|" t "|" app "|" arg; id = state = type = days = t = app = arg = "" }
		/^[[:space:]]*ID: \[/           { flush(); id = val($0) }
		/^[[:space:]]*State: \[/        { state = val($0) }
		/^[[:space:]]*Type: \[/         { type = val($0) }
		/^[[:space:]]*Days of week: \[/ { days = val($0) }
		/^[[:space:]]*App: \[/          { app = val($0) }
		/^[[:space:]]*Run time: \[/     { split($0, p, /[][]/); t = sprintf("%02d:%02d", p[2], p[4]) }
		/^[[:space:]]*CmdArgv: \[/      { n = split($0, q, "'\''"); arg = (n >= 3) ? q[n - 1] : "" }
		END { flush() }'
}

# "daily 03:00", "Mon,Wed 02:00", "monthly 01:00" for one schedule line.
_when() { # <schedule line>
	local type days t
	IFS='|' read -r _ _ type days t _ _ <<<"$1"
	case $type in
	daily) printf 'daily %s' "$t" ;;
	weekly) [ "$(tr ',' '\n' <<<"$days" | grep -c .)" -eq 7 ] && printf 'daily %s' "$t" || printf '%s %s' "$days" "$t" ;;
	*) printf '%s %s' "$type" "$t" ;;
	esac
}

# A share snapshot's name -> epoch. Two forms, depending on the snapshot task's
# time setting: local time with its offset ("GMT+02-2026.10.12-02.00.03") or
# UTC without one ("GMT-2026.10.14-01.05.04").
_snap_epoch() {
	local re='^GMT([+-][0-9]{2})?-([0-9]{4})\.([0-9]{2})\.([0-9]{2})-([0-9]{2})\.([0-9]{2})\.([0-9]{2})$'
	[[ $1 =~ $re ]] || return 1
	date -d "${BASH_REMATCH[2]}-${BASH_REMATCH[3]}-${BASH_REMATCH[4]} ${BASH_REMATCH[5]}:${BASH_REMATCH[6]}:${BASH_REMATCH[7]} ${BASH_REMATCH[1]:-+00}00" +%s 2>/dev/null
}

# {"enabled", "schedule", "latest", "count"} for a share, or null when it has
# no snapshot schedule. Snapshots live in <volume>/@sharesnap/<share>/.
_snapshot_json() { # <share> <share path> <schedules>
	local name=$1 path=$2 line latest=0 count=0 s e
	line=$(awk -F'|' -v n="$name" '$6 == "SYNO.SDS.Share.Snapshot" && $7 == n' <<<"$3" | head -n 1)
	[ -n "$line" ] || { printf 'null'; return; }
	if [ -n "$path" ] && [ -d "${path%/*}/@sharesnap/$name" ]; then
		for s in "${path%/*}/@sharesnap/$name"/*; do
			e=$(_snap_epoch "${s##*/}") || continue
			count=$((count + 1))
			[ "$e" -gt "$latest" ] && latest=$e
		done
	fi
	printf '{"enabled": %s, "schedule": %s, "latest": %s, "count": %s}' \
		"$([ "$(cut -d'|' -f2 <<<"$line")" = enabled ] && echo true || echo false)" \
		"$(json_str "$(_when "$line")")" "$([ "$latest" -gt 0 ] && echo "$latest" || echo null)" \
		"$([ -d "${path%/*}/@sharesnap/$name" ] && echo "$count" || echo null)"
}

shares_json() { # <schedules>
	local name path first=1
	printf '['
	command -v synoshare >/dev/null || { printf ']'; return; }
	while IFS= read -r name; do
		# usbshareN are USB disks: here, only backup destinations.
		case $name in '' | usbshare*) continue ;; esac
		path=$(synoshare --get "$name" 2>/dev/null | sed -n 's/^[[:space:]]*Path[ .]*\[\(.*\)\][[:space:]]*$/\1/p' | head -n 1)
		[ "$first" -eq 1 ] || printf ', '
		first=0
		printf '{"name": %s, "path": %s, "snapshot": %s}' \
			"$(json_str "$name")" "$(json_str "$path")" "$(_snapshot_json "$name" "$path" "$1")"
	done < <(synoshare --enum ALL 2>/dev/null | sed -n '/Listed:/,$p' | tail -n +2)
	printf ']'
}

# Hyper Backup tasks from its own config: [repo_N] says where, [task_N] what
# and when (sched_id = the Task Scheduler entry). Values are key=value, strings
# quoted; backup_folders is a JSON array of share-relative paths, passed on as
# is. Fields go through as \x1f-separated: a tab would collapse empty ones.
tasks_json() { # <schedules>
	local first=1 name sched enc folders kind line
	[ -r "$HYPERBACKUP_CONF" ] || { printf '[]'; return; }
	printf '['
	while IFS=$'\x1f' read -r name kind enc sched folders; do
		[[ $folders =~ ^\[.*\]$ ]] || folders='[]'
		line=$(awk -F'|' -v id="$sched" '$1 == id' <<<"$1" | head -n 1)
		[ "$first" -eq 1 ] || printf ', '
		first=0
		printf '{"name": %s, "dest": %s, "encrypted": %s, "enabled": %s, "schedule": %s, "folders": %s}' \
			"$(json_str "$name")" "$(json_str "$kind")" \
			"$([ "$enc" = true ] && echo true || echo false)" \
			"$([ -n "$line" ] && [ "$(cut -d'|' -f2 <<<"$line")" = enabled ] && echo true || echo false)" \
			"$([ -n "$line" ] && json_str "$(_when "$line")" || echo null)" "$folders"
	done < <(awk '
		function unq(s) { if (s ~ /^".*"$/) s = substr(s, 2, length(s) - 2); return s }
		/^\[/ { sec = substr($0, 2, length($0) - 2); next }
		{ i = index($0, "="); if (!i) next; k = substr($0, 1, i - 1); v = unq(substr($0, i + 1)); conf[sec, k] = v }
		sec ~ /^task_/ && !(sec in seen) { seen[sec]; order[++n] = sec }
		END {
			for (j = 1; j <= n; j++) {
				t = order[j]; r = "repo_" conf[t, "repo_id"]
				trans = conf[r, "trans_type"]; share = conf[r, "remote_share"]
				if (trans == "local") kind = (share ~ /^usbshare/) ? "usb" : "local"
				else if (trans == "aws_s3") kind = "s3"
				else kind = trans
				printf "%s\037%s\037%s\037%s\037%s\n", conf[t, "name"], kind,
					conf[t, "enable_data_encrypt"], conf[t, "sched_id"], conf[t, "backup_folders"]
			}
		}' "$HYPERBACKUP_CONF")
	printf ']'
}

protection_json() {
	local schedules
	schedules=$(_schedules)
	printf '{"shares": %s, "tasks": %s}' "$(shares_json "$schedules")" "$(tasks_json "$schedules")"
}
