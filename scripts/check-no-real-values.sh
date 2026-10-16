#!/usr/bin/env bash
# Blocks a commit that would put deployment-specific real values into tracked
# files. This is not redundant with gitleaks, the repo's other pre-commit hook:
# gitleaks hunts credentials, and none of these are credentials, which is why
# the internal domain and NAS volume paths survived three separate cleanup
# passes (see README's placeholder table for what should appear instead).
#
# Generic patterns live in this file because they are safe to publish. Values
# specific to this deployment must not be committed at all, so they are read
# from an untracked file in the repo root:
#
#     .private-patterns     one extended regex per line, # starts a comment
#
# See .private-patterns.example. Without that file the site-specific checks are
# skipped (with a warning), and only the generic ones below run.
set -uo pipefail

readonly PRIVATE_PATTERNS=".private-patterns"
status=0

report() { # <file> <line-and-text> <why>
	printf '  %s:%s\n      %s\n' "$1" "$2" "$3" >&2
	status=1
}

scan() { # <file> <extended-regex> <why>
	local file=$1 regex=$2 why=$3 hit
	while IFS= read -r hit; do
		[ -n "$hit" ] && report "$file" "$hit" "$why"
	done < <(grep -nE "$regex" -- "$file" 2>/dev/null | cut -c1-160)
}

# Site-specific regexes, if the untracked file is present.
private=()
if [ -f "$PRIVATE_PATTERNS" ]; then
	while IFS= read -r line; do
		line=${line%%#*}
		# Trim the ends only — a pattern may legitimately contain a space
		# (e.g. a device name), so stripping all whitespace would mangle it.
		line="${line#"${line%%[![:space:]]*}"}"
		line="${line%"${line##*[![:space:]]}"}"
		[ -n "$line" ] && private+=("$line")
	done < "$PRIVATE_PATTERNS"
else
	echo "warning: $PRIVATE_PATTERNS not found — site-specific checks skipped (see .private-patterns.example)" >&2
fi

for file in "$@"; do
	# Deleted or renamed away: nothing to check.
	[ -f "$file" ] || continue
	# Never scan the pattern file itself, and skip binaries.
	[ "$file" = "$PRIVATE_PATTERNS" ] && continue
	grep -qI . -- "$file" 2>/dev/null || continue

	scan "$file" '/volume[0-9]+/' \
		'Synology volume path — the placeholder table says <nas-volume-path>, and .env.example files use /path/to/...'
	# Any *UID= / *GID= with a number (PUID, PAPERLESS_UID, USER_GID...), except
	# the generic 0 and 1000 that images document as their fixed defaults.
	while IFS= read -r hit; do
		printf '%s' "$hit" | grep -qE '[UG]ID=(0|1000)\b' && continue
		report "$file" "$hit" 'real uid/gid — the placeholder table says <uid> / <gid>'
	done < <(grep -nE '\b[A-Z_]*[UG]ID=[0-9]+' -- "$file" 2>/dev/null | cut -c1-160)
	# Private host addresses. CIDR ranges (a trailing /NN) are skipped: Docker
	# subnets in allowlists are not a LAN map. Placeholder IPs use RFC 5737
	# (192.0.2.x, 198.51.100.x, 203.0.113.x), which this does not match.
	# Octets without leading zeros: tariff codes such as TARMED '10.02.01.01'
	# are not addresses, and a real address is never written '10.02.01.01'.
	scan "$file" '(^|[^0-9.])(10\.(25[0-5]|2[0-4][0-9]|1[0-9][0-9]|[1-9]?[0-9])|192\.168|172\.(1[6-9]|2[0-9]|3[01]))\.(25[0-5]|2[0-4][0-9]|1[0-9][0-9]|[1-9]?[0-9])\.(25[0-5]|2[0-4][0-9]|1[0-9][0-9]|[1-9]?[0-9])([^0-9./]|$)' \
		'private LAN address — the placeholder table says <nas-ip>; use RFC 5737 (192.0.2.x) in examples'
	# LAN hostnames. `networks.lan` is the compose key path for the edge
	# stack's macvlan network, not a host.
	while IFS= read -r hit; do
		printf '%s' "$hit" | sed -E 's/(example|networks)\.lan//g' |
			grep -qE '[A-Za-z0-9-]\.lan\b' || continue
		report "$file" "$hit" '.lan hostname — the placeholder table says <nas-host>'
	done < <(grep -nE '[A-Za-z0-9-]\.lan\b' -- "$file" 2>/dev/null | cut -c1-160)

	# Email addresses, excluding the ones the repo deliberately uses.
	while IFS= read -r hit; do
		printf '%s' "$hit" | grep -qE '(example\.(com|lan)|noreply@anthropic\.com)' && continue
		[ -n "$hit" ] && report "$file" "$hit" \
			'email address — the placeholder table says admin@example.com'
	done < <(grep -nE '[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}' -- "$file" 2>/dev/null | cut -c1-160)

	for regex in ${private+"${private[@]}"}; do
		scan "$file" "$regex" "matches a pattern in $PRIVATE_PATTERNS (deployment-specific)"
	done
done

if [ "$status" -ne 0 ]; then
	cat >&2 <<'MSG'

Commit blocked: deployment-specific real values found in staged files.

Use the placeholders from README's substitution table, or move the content
out of the repository if it cannot be usefully scrubbed in place.
To commit anyway (rarely right): git commit --no-verify
MSG
fi

exit "$status"
