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

# jq for reading API responses. DSM ships none: use the local one if any, else
# a pinned image.
JQ_IMAGE="ghcr.io/jqlang/jq:1.8.1"
_jq() {
	if command -v jq >/dev/null; then jq "$@"; else docker run --rm -i "$JQ_IMAGE" "$@"; fi
}
