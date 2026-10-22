#!/usr/bin/env bash
# Deploys the ntfy scripts (ntfy.rsc) to the MikroTik router and Wi-Fi access
# point over SSH, with each one's own settings. Run from a workstation that can
# `ssh router` and `ssh ap` with a key (docs/guides/11-notifications.md).
#
#   tools/mikrotik/deploy.sh            both devices
#   tools/mikrotik/deploy.sh router     one of them
#   tools/mikrotik/deploy.sh -t ap      and send a test message from it
#
# Real values come from tools/mikrotik/mikrotik.env (gitignored, *.env):
#   NTFY_HOST=ntfy.example.com     ntfy's hostname, resolvable by the devices
#   NTFY_TOKEN=tk_...              the ntfy publish token
#   ROUTER=router  AP=ap           SSH hosts (default: router, ap)
#   DHCP_SERVER=defconf            DHCP server to attach the lease script to
#   GUEST_DHCP_SERVER=dhcp-guest   optional: the AP's guest DHCP server, whose
#                                  devices alert as "on the guest Wi-Fi"
#
# Per device:
#   router  scripts, internet + WireGuard checks, lease script ("New device on the LAN")
#   ap      scripts and lease script only ("on the Wi-Fi"); the router already
#           watches the internet and WireGuard
# Both: leases without a comment are named after their host name. Re-running
# is safe: the import replaces everything it owns.
set -euo pipefail

cd "$(dirname -- "$0")"
test=0
if [ "${1:-}" = -t ]; then test=1; shift; fi
[ -f mikrotik.env ] || { echo "Missing tools/mikrotik/mikrotik.env (NTFY_HOST, NTFY_TOKEN): see the top of this script." >&2; exit 2; }
# shellcheck disable=SC1091 # local, gitignored
. ./mikrotik.env
: "${NTFY_HOST:?set NTFY_HOST in mikrotik.env}" "${NTFY_TOKEN:?set NTFY_TOKEN in mikrotik.env}"
ROUTER=${ROUTER:-router} AP=${AP:-ap} DHCP_SERVER=${DHCP_SERVER:-defconf}

tmp=$(mktemp -d)
trap 'rm -rf "$tmp"' EXIT
sed -e "s/ntfy\.home\.example\.com/$NTFY_HOST/g" -e "s/tk_REPLACE_ME/$NTFY_TOKEN/g" ntfy.rsc >"$tmp/ntfy.rsc"
grep -q 'tk_REPLACE_ME\|ntfy\.home\.example\.com' "$tmp/ntfy.rsc" && { echo "Placeholders left in ntfy.rsc" >&2; exit 1; }

# The line attaching the lease script to a DHCP server. <where> is empty, or
# ' where=\"Wi-Fi\"' (RouterOS-escaped) for the alert's wording.
lease_script() { # <dhcp server> <where>
	echo "/ip dhcp-server set [find name=\"$1\"] lease-script=\":local f [:parse [/system script get ntfy-dhcp source]]; \\\$f bound=\\\$leaseBound mac=\\\$leaseActMAC ip=\\\$leaseActIP host=\\\$\\\"lease-hostname\\\"$2\""
}

# The commands specific to a device, as a second file to import: no quoting
# through ssh.
role_rsc() { # <role>
	local where=""
	[ "$1" = ap ] && where=' where=\"Wi-Fi\"'
	if [ "$1" = ap ]; then
		echo '/tool netwatch remove [find where comment~"^ntfy-inet"]'
		echo '/system scheduler remove [find where name="ntfy-wg"]'
	fi
	lease_script "$DHCP_SERVER" "$where"
	if [ "$1" = ap ] && [ -n "${GUEST_DHCP_SERVER:-}" ]; then
		lease_script "$GUEST_DHCP_SERVER" ' where=\"guest Wi-Fi\"'
	fi
	# shellcheck disable=SC2016 # RouterOS's $l, not the shell's
	echo '/ip dhcp-server lease { :foreach l in=[find where host-name!=""] do={ :if ([:len [get $l comment]] = 0) do={ set $l comment=[get $l host-name] } } }'
	if [ "$test" -eq 1 ]; then
		echo ":local s [:parse [/system script get ntfy-send source]]; \$s title=\"Deploy test from $1\" message=\"ntfy scripts deployed\" tags=\"white_check_mark\" priority=\"low\""
	fi
}

deploy() { # <role> <ssh host>
	local role=$1 host=$2 scripts out
	echo "== $role ($host)"
	role_rsc "$role" >"$tmp/ntfy-$role.rsc"
	scp -q "$tmp/ntfy.rsc" "$tmp/ntfy-$role.rsc" "$host:"
	# shellcheck disable=SC2029 # expanded here on purpose: the file name
	out=$(ssh "$host" "/import file-name=ntfy.rsc; /import file-name=ntfy-$role.rsc; /file remove [find where name~\"^ntfy\"]" 2>&1 | tr -d '\r')
	# RouterOS reports a failed line, and carries on: say so instead of hiding it.
	if grep -qiE 'failure|expected|syntax error|bad command|no such|invalid value' <<<"$out"; then
		echo "$role: the import reported errors:" >&2
		grep -iE -B1 'failure|expected|syntax error|bad command|no such|invalid value' <<<"$out" | grep -v "$NTFY_TOKEN" >&2
		return 1
	fi
	scripts=$(ssh "$host" ':put [:len [/system script find where name~"^ntfy-"]]' | tr -d '\r[:space:]')
	[ "$scripts" = 4 ] || { echo "$role: expected 4 ntfy scripts, found ${scripts:-none}" >&2; return 1; }
	# shellcheck disable=SC2029 # the server name is expanded here on purpose
	ssh "$host" "/ip dhcp-server print detail where name=\"$DHCP_SERVER\"" | grep -q 'ntfy-dhcp' ||
		{ echo "$role: lease script not attached to $DHCP_SERVER" >&2; return 1; }
	echo "   4 scripts, lease script on $DHCP_SERVER, leases named$([ "$test" -eq 1 ] && echo ', test message sent')"
}

case ${1:-both} in
both) deploy router "$ROUTER" && deploy ap "$AP" ;;
router) deploy router "$ROUTER" ;;
ap) deploy ap "$AP" ;;
*) echo "usage: deploy.sh [-t] [router|ap]" >&2; exit 2 ;;
esac
