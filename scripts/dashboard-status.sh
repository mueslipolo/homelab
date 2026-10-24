#!/usr/bin/env bash
# Writes status.json for the dashboard: volume usage, the age of the newest
# app dump (Forgejo, Mealie), each Hyper Backup task's last result, what
# protects which shared folder (lib/protection.sh), jobs, deployments, and
# whether each Portainer stack runs the latest code (lib/stacks.sh). The
# dashboard is static nginx with no backend, so the NAS pushes the facts into
# the folder the dashboard already mounts (DASHBOARD_LOCAL_DIR, see
# stacks/dashboard/README.md) and the page only reads them.
#
# Run by scripts/run-jobs.sh at the end of every tick (as root: synobackup.log
# and the dump folders are not world-readable). Can also be run by hand.
# Every input is optional except the two paths below; a missing source shows
# on the page as "unknown", never as a false green.
set -euo pipefail

readonly OUT_DIR="${DASHBOARD_LOCAL_DIR:?set DASHBOARD_LOCAL_DIR to the dashboard local folder}"
readonly VOLUME="${STATUS_VOLUME:?set STATUS_VOLUME to the volume to report, e.g. /volume1}"
readonly FORGEJO_DUMPS="${FORGEJO_DUMP_DIR:-}"
readonly MEALIE_DUMPS="${MEALIE_DUMP_DIR:-}"
# Hyper Backup's log. Current packages write inside their own package dir; the
# DSM-wide /var/log/synolog/synobackup.log is a leftover that stopped updating
# (here: 2024-11), so reading it would show every task as stale. The first
# existing path wins; HYPERBACKUP_LOG overrides both.
backup_log_path() {
	local p
	for p in "${HYPERBACKUP_LOG:-}" \
		/var/packages/HyperBackup/var/log/synolog/synobackup.log \
		/var/log/synolog/synobackup.log; do
		[ -n "$p" ] && [ -r "$p" ] && { echo "$p"; return; }
	done
	return 0 # none readable: the page shows "no result"
}
BACKUP_LOG=$(backup_log_path)
readonly BACKUP_LOG

# shellcheck source=scripts/lib/json.sh
. "$(dirname -- "$0")/lib/json.sh"
# shellcheck source=scripts/lib/protection.sh
. "$(dirname -- "$0")/lib/protection.sh"
# shellcheck source=scripts/lib/code.sh
. "$(dirname -- "$0")/lib/code.sh"
# shellcheck source=scripts/lib/stacks.sh
. "$(dirname -- "$0")/lib/stacks.sh"
# shellcheck source=scripts/lib/devices.sh
. "$(dirname -- "$0")/lib/devices.sh"
# shellcheck source=scripts/lib/containers.sh
. "$(dirname -- "$0")/lib/containers.sh"

volume_json() {
	local size avail pct
	read -r size avail pct < <(df -Pk "$VOLUME" | awk 'NR == 2 { print $2, $4, $5 }')
	printf '{"path": %s, "size_kb": %s, "avail_kb": %s, "used_pct": %s}' \
		"$(json_str "$VOLUME")" "$size" "$avail" "${pct%\%}"
}

# Newest regular file in a folder, as {"name", "dir", "latest" (epoch or null)}.
dump_json() { # <label> <dir>
	local label=$1 dir=$2 newest=0 file="" f mtime
	if [ -d "$dir" ]; then
		for f in "$dir"/*; do
			[ -f "$f" ] || continue
			mtime=$(stat -c %Y -- "$f")
			if [ "$mtime" -gt "$newest" ]; then
				newest=$mtime
				file=${f##*/}
			fi
		done
	fi
	printf '{"name": %s, "dir": %s, "file": %s, "latest": %s}' \
		"$(json_str "$label")" "$(json_str "$dir")" "$(json_str "$file")" "$([ "$newest" -gt 0 ] && echo "$newest" || echo null)"
}

# Last finished/failed line per task. Expected shape (tab-separated):
#   info  2026/10/10 21:02:11  SYSTEM:  [Network][Task name] Backup task finished successfully. [...]
backups_json() {
	[ -n "$BACKUP_LOG" ] || { printf '[]'; return; }
	local line task when result epoch first=1
	local -A last_at=() last_result=() last_msg=()
	local re_time='([0-9]{4})/([0-9]{2})/([0-9]{2})[[:space:]]+([0-9]{2}:[0-9]{2}:[0-9]{2})'
	local re_task='\]\[([^]]+)\][[:space:]]*(.*)$'
	while IFS= read -r line; do
		case $line in
		*"Backup task finished successfully"*) result=success ;;
		*"partially completed"*) result=partial ;;
		*"Backup task"*fail* | *"Failed to run backup task"*) result=failed ;;
		*) continue ;;
		esac
		[[ $line =~ $re_time ]] || continue
		when="${BASH_REMATCH[1]}-${BASH_REMATCH[2]}-${BASH_REMATCH[3]} ${BASH_REMATCH[4]}"
		[[ $line =~ $re_task ]] || continue
		task=${BASH_REMATCH[1]}
		epoch=$(date -d "$when" +%s)
		last_at[$task]=$epoch
		last_result[$task]=$result
		last_msg[$task]=${BASH_REMATCH[2]}
	done < <(tail -n 5000 -- "$BACKUP_LOG")

	printf '['
	for task in "${!last_at[@]}"; do
		[ "$first" -eq 1 ] || printf ', '
		first=0
		printf '{"task": %s, "result": "%s", "at": %s, "message": %s}' \
			"$(json_str "$task")" "${last_result[$task]}" "${last_at[$task]}" \
			"$(json_str "${last_msg[$task]:0:200}")"
	done
	printf ']'
}

# Scheduler and per-job state written by run-jobs.sh, passed through as-is
# (it is our own JSON). Absent when run outside run-jobs.sh.
jobs_json() {
	local dir=${JOBS_STATE_DIR:-} f first=1
	printf '['
	if [ -n "$dir" ]; then
		for f in "$dir"/*.json; do
			[ -f "$f" ] || continue
			[ "${f##*/}" != _scheduler.json ] || continue
			[ "$first" -eq 1 ] || printf ', '
			first=0
			tr -d '\n' <"$f"
		done
	fi
	printf ']'
}

# For each image build-images.sh maintains (its arguments in jobs.conf), the
# three stages of a deploy, so the dashboard can say which one is behind:
#   code    -- the last commit changing stacks/<stack>'s code in this clone
#              (lib/code.sh: anything but Markdown)
#   built   -- the image under the tag: its commit, build time, and whether it
#              was built from the current code (code hash label)
#   running -- the container: its build's commit, when it started,
#              and whether it runs the image now under the tag
# Labels come from build-images.sh; a container keeps its image's labels even
# once that image has been replaced under the tag.
images_json() {
	local repo conf specs=() spec stack image first=1
	repo="$(cd "$(dirname -- "$0")/.." && pwd)"
	conf="$(dirname -- "$0")/jobs.conf"
	if [ -f "$conf" ] && command -v docker >/dev/null; then
		# shellcheck disable=SC2207 # specs are simple tokens
		specs=($(awk '$4 == "build-images.sh" { for (i = 5; i <= NF; i++) print $i }' "$conf"))
	fi
	printf '['
	for spec in "${specs[@]}"; do
		stack=${spec%%=*}
		image=${spec#*=}
		# A path (.forgejo/ci) is a tool image, not a stack: nothing runs it.
		case $stack in */*) continue ;; esac
		[ "$first" -eq 1 ] || printf ', '
		first=0
		printf '{"stack": %s, "code": %s, "built": %s, "running": %s}' \
			"$(json_str "$stack")" \
			"$(code_json "$repo" "$stack")" "$(built_json "$repo" "$stack" "$image")" \
			"$(running_json "$stack" "$image")"
	done
	printf ']'
}

code_json() { # <repo> <stack>
	local info
	info=$(code_commit "$1" "stacks/$2" '%h') || info=
	[ -n "$info" ] || { printf 'null'; return; }
	printf '{"commit": %s}' "$(json_str "$info")"
}

# Labels of an image or a container: commit|built|tree
_labels() {
	docker inspect --format '{{ index .Config.Labels "homelab.commit" }}|{{ index .Config.Labels "homelab.built" }}|{{ index .Config.Labels "homelab.tree" }}' "$1" 2>/dev/null
}

built_json() { # <repo> <stack> <image>
	local info commit at tree current
	info=$(_labels "$3") || { printf 'null'; return; }
	IFS='|' read -r commit at tree <<<"$info"
	current=$(code_hash "$1" "stacks/$2")
	printf '{"commit": %s, "at": %s, "from_current_code": %s}' \
		"$(json_str "${commit:-?}")" "$(_num "$at")" \
		"$([ -n "$tree" ] && [ "$tree" = "$current" ] && echo true || echo false)"
}

running_json() { # <container> <image>
	local info commit started id latest
	info=$(_labels "$1") || { printf 'null'; return; }
	commit=${info%%|*}
	started=$(docker inspect --format '{{ .State.StartedAt }}' "$1" 2>/dev/null)
	started=$(date -d "$started" +%s 2>/dev/null || echo null)
	id=$(docker inspect --format '{{ .Image }}' "$1" 2>/dev/null)
	latest=$(docker image inspect --format '{{ .Id }}' "$2" 2>/dev/null || true)
	printf '{"commit": %s, "started": %s, "state": %s, "same_image": %s}' \
		"$(json_str "${commit:-?}")" "$started" \
		"$(json_str "$(docker inspect --format '{{ .State.Status }}' "$1" 2>/dev/null)")" \
		"$([ -n "$id" ] && [ "$id" = "$latest" ] && echo true || echo false)"
}

scheduler_json() {
	local f=${JOBS_STATE_DIR:-}/_scheduler.json
	if [ -n "${JOBS_STATE_DIR:-}" ] && [ -f "$f" ]; then tr -d '\n' <"$f"; else printf 'null'; fi
}

main() {
	local dumps=()
	[ -n "$FORGEJO_DUMPS" ] && dumps+=("$(dump_json "Forgejo dump" "$FORGEJO_DUMPS")")
	[ -n "$MEALIE_DUMPS" ] && dumps+=("$(dump_json "Mealie dump" "$MEALIE_DUMPS")")

	# Written atomically, so the page never reads a half-written file.
	{
		printf '{\n  "generated": %s,\n' "$(date +%s)"
		printf '  "volume": %s,\n' "$(volume_json)"
		printf '  "dumps": [%s],\n' "$(IFS=,; echo "${dumps[*]-}")"
		printf '  "backups": %s,\n' "$(backups_json)"
		printf '  "protection": %s,\n' "$(protection_json)"
		printf '  "devices": %s,\n' "$(devices_json "$VOLUME")"
		printf '  "scheduler": %s,\n' "$(scheduler_json)"
		printf '  "images": %s,\n' "$(images_json)"
		printf '  "containers": %s,\n' "$(containers_json)"
		printf '  "stacks": %s,\n' "$(stacks_json "$(cd "$(dirname -- "$0")/.." && pwd)")"
		printf '  "jobs": %s\n}\n' "$(jobs_json)"
	} | write_atomic "$OUT_DIR/status.json"
}

if [ "${1:-}" = --suggest-uploaders ]; then
	devices_suggest "$VOLUME"   # lib/devices.sh: lines to start uploaders.conf from
	exit 0
fi
main "$@"
