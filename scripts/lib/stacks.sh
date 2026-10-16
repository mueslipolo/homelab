# shellcheck shell=bash
# Is each Portainer stack running the latest code? Portainer records the commit
# it last deployed for a Git stack; git says the last commit that changed the
# stack's code (lib/code.sh: anything but Markdown). Sourced by
# dashboard-status.sh (needs scripts/lib/json.sh and scripts/lib/code.sh).
#
# Needs PORTAINER_TOKEN (Portainer -> My account -> Access tokens) in jobs.env;
# without it the dashboard shows no stack list. PORTAINER_URL defaults to the
# NAS's own Portainer (self-signed certificate, hence -k on localhost only).

PORTAINER_URL="${PORTAINER_URL:-https://127.0.0.1:9443}"
# DSM ships no jq: use the local one if any, else a pinned image.
JQ_IMAGE="ghcr.io/jqlang/jq:1.8.1"

_jq() {
	if command -v jq >/dev/null; then jq "$@"; else docker run --rm -i "$JQ_IMAGE" "$@"; fi
}

# One line per stack: name, id, status (1 active, 2 stopped), compose path,
# deployed commit, update time; \x1f-separated (a tab would collapse empty
# fields). Only these fields leave the API response: it also holds every
# stack's environment, passwords included.
_portainer_stacks() {
	local body insecure=()
	case $PORTAINER_URL in https://127.0.0.1:* | https://localhost:*) insecure=(-k) ;; esac
	# The token goes in on stdin, never on the command line.
	body=$(printf 'header = "X-API-Key: %s"\n' "$PORTAINER_TOKEN" |
		curl -sS -m 20 --fail "${insecure[@]}" -K - "$PORTAINER_URL/api/stacks") || return 1
	_jq -r '.[] | [.Name, (.Id | tostring), (.Status | tostring),
		(.GitConfig.ConfigFilePath // ""), (.GitConfig.ConfigHash // ""),
		((.UpdateDate // .CreationDate // 0) | tostring)] | join("\u001f")' <<<"$body"
}

# [{"name", "status", "git", "deployed": {"commit", "at"}, "code": {"commit", "at"},
#   "current": true|false|null}], or null when Portainer cannot be asked.
#   current = the stack folder's last commit is part of what was deployed.
stacks_json() { # <repo>
	local repo=$1 lines name id status path hash at dir code first=1 current
	[ -n "${PORTAINER_TOKEN:-}" ] || { printf 'null'; return; }
	lines=$(_portainer_stacks 2>/dev/null) || { printf 'null'; return; }
	printf '['
	while IFS=$'\x1f' read -r name id status path hash at; do
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
		printf '{"name": %s, "id": %s, "active": %s, "git": %s, "deployed": %s, "code": %s, "current": %s}' \
			"$(json_str "$name")" "$(_num "$id")" "$([ "$status" = 1 ] && echo true || echo false)" \
			"$([ -n "$path" ] && echo true || echo false)" \
			"$([ -n "$hash" ] && printf '{"commit": %s, "at": %s}' "$(json_str "${hash:0:7}")" "$(_num "$at")" || echo null)" \
			"$code" "$current"
	done <<<"$lines"
	printf ']'
}
