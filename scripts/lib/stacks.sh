# shellcheck shell=bash
# The stacks, as Dockhand deploys them (docs/guides/02-dockhand.md): it records
# the commit it last deployed for each git stack; git says the last commit that
# changed the stack's code (lib/code.sh: anything but Markdown). Sourced by
# dashboard-status.sh (needs scripts/lib/json.sh and scripts/lib/code.sh) and
# by redeploy-images.sh (needs scripts/lib/json.sh).
#
# Dockhand is asked only when its token is in jobs.env; without it, the
# dashboard shows no stack list:
#   DOCKHAND_TOKEN   Dockhand -> Profile -> API tokens (dh_...)
#
# DOCKHAND_URL defaults to Dockhand's port on the NAS's loopback
# (stacks/dockhand/compose.yaml), plain HTTP that never leaves the host.

DOCKHAND_URL="${DOCKHAND_URL:-http://127.0.0.1:3010}"

# Dockhand's API: prints the response body; fails on an HTTP error. With
# `Accept: application/json` a deploy answers once it is over, with
# {"success", "error"}, instead of a job id to poll (createJobResponse in
# Dockhand's src/lib/server/sse.ts).
dockhand_api() { # <method> <path> [max seconds]
	# The token goes in on stdin, never on the command line.
	printf 'header = "Authorization: Bearer %s"\n' "$DOCKHAND_TOKEN" |
		curl -sS -m "${3:-30}" --fail -K - -H "Accept: application/json" -X "$1" "$DOCKHAND_URL$2"
}

# Whether Dockhand can be asked (its token is set).
dockhand_ready() { [ -n "${DOCKHAND_TOKEN:-}" ]; }

# One line per git stack, \x1f-separated (a tab would collapse empty fields):
#   id, status, name, compose path, deployed commit, deploy time (epoch)
# Status: 1 active, 4 its last deploy failed (its sync error is a failed
# deploy). Fails if Dockhand does not answer. Only these fields leave the
# response: it also holds each stack's webhook secret.
stacks_list() {
	local body
	body=$(dockhand_api GET /api/git/stacks) || return 1
	# lastCommit is the commit of its last sync: a sync deploys whenever the
	# stack's folder changed, so its folder is that commit's, unless the
	# deploy failed (status 4). lastSync is ISO 8601 with milliseconds.
	_jq -r '.[] | [(.id | tostring),
		(if .syncStatus == "error" and ((.syncError // "") | startswith("Deploy failed: ")) then "4" else "1" end),
		.stackName, (.composePath // ""), (.lastCommit // ""),
		((.lastSync // "") | try (sub("\\.[0-9]+"; "") | fromdateiso8601 | tostring) catch "")]
		| join("\u001f")' <<<"$body"
}

# [{"name", "failed", "git", "deployed": {"commit", "at"},
#   "code": {"commit", "at"}, "current": true|false|null}], or null when
#   Dockhand cannot be asked or does not answer.
#   current = the stack folder's last commit is part of what was deployed.
stacks_json() { # <repo>
	local repo=$1 lines status name path hash at dir code first=1 current
	dockhand_ready || { printf 'null'; return; }
	lines=$(stacks_list 2>/dev/null) || { printf 'null'; return; }
	printf '['
	while IFS=$'\x1f' read -r _ status name path hash at; do
		[ -n "$name" ] || continue
		code=null current=null
		if [ -n "$path" ]; then
			dir=$(dirname -- "$path")
			code=$(code_commit "$repo" "$dir") # lib/code.sh: Markdown doesn't count
			if [ -n "$code" ] && [ -n "$hash" ] && git -C "$repo" cat-file -e "$hash^{commit}" 2>/dev/null; then
				git -C "$repo" merge-base --is-ancestor "${code% *}" "$hash" && current=true || current=false
			fi
			[ -n "$code" ] && code=$(printf '{"commit": %s, "at": %s}' "$(json_str "${code:0:7}")" "${code#* }") || code=null
		fi
		[ "$first" -eq 1 ] || printf ', '
		first=0
		printf '{"name": %s, "failed": %s, "git": %s, "deployed": %s, "code": %s, "current": %s}' \
			"$(json_str "$name")" \
			"$([ "$status" = 4 ] && echo true || echo false)" \
			"$([ -n "$path" ] && echo true || echo false)" \
			"$([ -n "$hash" ] && printf '{"commit": %s, "at": %s}' "$(json_str "${hash:0:7}")" "$(_num "$at")" || echo null)" \
			"$code" "$current"
	done <<<"$lines"
	printf ']'
}
