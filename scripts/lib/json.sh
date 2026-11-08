# shellcheck shell=bash
# Shared by scripts that hand-write JSON (DSM ships neither jq nor a
# guaranteed python). Source it; do not run it.

# Quote a string as a JSON string literal. Control characters are flattened to
# spaces: these values are log lines and names, never structured text.
json_str() {
	local s=$1
	s=${s//\\/\\\\}
	s=${s//\"/\\\"}
	s=${s//$'\t'/ }
	s=${s//$'\r'/}
	s=${s//$'\n'/ }
	printf '"%s"' "$s"
}

# A number as is, anything else (empty, "?") as null.
_num() { [[ $1 =~ ^[0-9]+$ ]] && printf '%s' "$1" || printf 'null'; }

# Write stdin to a file atomically (same directory, then rename), world-
# readable: the dashboard's nginx runs as an unprivileged user.
write_atomic() { # <path>
	local tmp
	tmp=$(mktemp "$(dirname -- "$1")/.$(basename -- "$1").XXXXXX")
	cat >"$tmp"
	chmod 644 -- "$tmp"
	mv -f -- "$tmp" "$1"
}

# jq for reading API responses: the local one if it is 1.7 or newer, else a
# pinned image. An older jq (one DSM can come with) lacks functions these
# scripts use, e.g. ceil: alerts.sh failed to compile with it.
JQ_IMAGE="ghcr.io/jqlang/jq:1.8.1"
_jq_local=""
_jq() {
	if [ -z "$_jq_local" ]; then
		_jq_local=no
		case $(jq --version 2>/dev/null) in
		jq-1.[7-9]* | jq-1.[1-9][0-9]* | jq-[2-9]*) _jq_local=yes ;;
		esac
	fi
	if [ "$_jq_local" = yes ]; then jq "$@"; else docker run --rm -i "$JQ_IMAGE" "$@"; fi
}
