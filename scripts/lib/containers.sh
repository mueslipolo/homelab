# shellcheck shell=bash
# The state of every long-running container, for the dashboard and for
# alerts.sh (crashes, restart loops, failed healthchecks). Sourced by
# dashboard-status.sh (needs scripts/lib/json.sh).
#
# Long-running = has a restart policy: every stack's services and Portainer,
# not one-off `docker run --rm` helpers (the jq image, CI jobs).

# [{"name", "project", "state", "health", "exit_code", "oom", "restarts",
#   "started"}], or null when docker cannot be asked.
containers_json() {
	local ids lines name policy state health code oom restarts started project first=1
	ids=$(docker ps -aq 2>/dev/null) || { printf 'null'; return; }
	[ -n "$ids" ] || { printf '[]'; return; }
	# shellcheck disable=SC2086 # one argument per container id
	lines=$(docker inspect --format \
		'{{.Name}}{{"\x1f"}}{{.HostConfig.RestartPolicy.Name}}{{"\x1f"}}{{.State.Status}}{{"\x1f"}}{{if .State.Health}}{{.State.Health.Status}}{{end}}{{"\x1f"}}{{.State.ExitCode}}{{"\x1f"}}{{.State.OOMKilled}}{{"\x1f"}}{{.RestartCount}}{{"\x1f"}}{{.State.StartedAt}}{{"\x1f"}}{{index .Config.Labels "com.docker.compose.project"}}' \
		$ids 2>/dev/null) || { printf 'null'; return; }
	printf '['
	while IFS=$'\x1f' read -r name policy state health code oom restarts started project; do
		case $policy in "" | no) continue ;; esac
		started=$(date -d "$started" +%s 2>/dev/null || echo null)
		[ "$first" -eq 1 ] || printf ', '
		first=0
		printf '{"name": %s, "project": %s, "state": %s, "health": %s, "exit_code": %s, "oom": %s, "restarts": %s, "started": %s}' \
			"$(json_str "${name#/}")" "$(json_str "$project")" "$(json_str "$state")" \
			"$([ -n "$health" ] && json_str "$health" || echo null)" "$(_num "$code")" \
			"$([ "$oom" = true ] && echo true || echo false)" "$(_num "$restarts")" "$started"
	done <<<"$lines"
	printf ']'
}
