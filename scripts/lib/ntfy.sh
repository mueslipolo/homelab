# shellcheck shell=bash
# Push a notification to ntfy (docs/guides/11-notifications.md). Source it;
# do not run it.
#
#   NTFY_TOKEN   publish token (required)
#   NTFY_URL     default http://127.0.0.1:2586: ntfy's loopback port, since the
#                NAS cannot reach Caddy's macvlan IP

# ntfy_send <topic> <title> <priority> <tags> <click URL or ""> <<<"message"
ntfy_send() {
	local topic=$1 title=$2 priority=$3 tags=$4 click=$5
	local headers=(-H "Title: $title" -H "Priority: $priority" -H "Tags: $tags")
	[ -n "$click" ] && headers+=(-H "Click: $click")
	# The token goes in on stdin (curl -K -), never on the command line; the
	# message is read from this function's stdin into a variable first.
	local body
	body=$(cat)
	printf 'header = "Authorization: Bearer %s"\n' "${NTFY_TOKEN:?set NTFY_TOKEN to an ntfy token that can publish}" |
		curl -sS -m 30 --fail -o /dev/null -K - "${headers[@]}" \
			--data-binary "$body" "${NTFY_URL:-http://127.0.0.1:2586}/$topic"
}
