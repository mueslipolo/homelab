# shellcheck shell=bash
# Are the devices that upload to the NAS (phones backing up photos, computers
# running Synology Drive Client) still doing it? Sourced by dashboard-status.sh
# (needs scripts/lib/json.sh).
#
# Which devices: $OPS_HOME/uploaders.conf (UPLOADERS_CONF overrides). One
# line per device, '|'-separated because device names have spaces:
#
#   # owner | kind     | device   | max days | photos folder (optional)
#   bob     | computer | laptop   | 21
#   anna    | phone    | Pixel 7  | 7
#
#   device     the name Synology Drive reports (Drive Admin Console, Client
#              List); for a phone also its folder Photos/MobileBackup/<device>
#   max days   silent this long: red; half of it: amber
#   photos     a phone's DCIM folder, if not the usual one (absolute path)
#
# Signals per device, the newer one counts:
#   seen       Synology Drive's client list (SYNO.SynologyDrive.Connection):
#              a client whose client_status is on_line counts as seen now,
#              any other as of its last_auth_time. The newest sighting per
#              device is kept in $JOBS_STATE_DIR/devices-seen.tsv (state, not
#              configuration), so a device stays known once it has left the
#              list
#   photos     the newest photo or video file in the phone's DCIM tree: when
#              its backup last added one. DSM's @eaDir thumbnails skipped.
# Devices uploading but not in the file are listed too, so a new or renamed
# phone gets noticed: `dashboard-status.sh --suggest-uploaders` prints lines
# to start from.

# Drive's connected clients: device \t owner \t kind \t version \t seen
_drive_clients() { # <now>
	command -v synowebapi >/dev/null || return 0
	synowebapi --exec api=SYNO.SynologyDrive.Connection method=list version=1 2>/dev/null | awk -v now="$1" '
		function val(s) { sub(/^[^:]*: */, "", s); sub(/,[[:space:]]*$/, "", s); gsub(/^"|"$/, "", s); return s }
		/^[[:space:]]*\{[[:space:]]*$/ { id = owner = type = version = status = ""; t = 0; next }
		/"client_id"/ { id = val($0) }
		/"client_name"/ { owner = val($0); sub(/@.*/, "", owner) }
		/"client_type"/ { type = val($0) }
		/"client_version"/ { version = val($0) }
		/"client_status"/ { status = val($0) }
		/"last_auth_time"/ { t = val($0) + 0 }
		/^[[:space:]]*\},?[[:space:]]*$/ {
			if (id != "") printf "%s\t%s\t%s\t%s\t%d\n", id, owner, (type ~ /mobile/ ? "phone" : "computer"),
				version, (status == "on_line" ? now : t)
			id = ""
		}'
}

# Newest photo or video (epoch) in a DCIM tree, or nothing. Files, not
# folders: a folder's date also changes when files are deleted or Synology
# Photos adds its @eaDir thumbnails, which made a retired phone look active.
_newest_dir() { # <dir>
	[ -d "$1" ] || return 0
	find "$1" -type d -name '@eaDir' -prune -o -type f ! -name '.*' -printf '%T@\n' 2>/dev/null | sort -n | tail -n 1 | cut -d. -f1
}

# Every phone backup folder: device \t owner \t newest change. Owners are home
# folder names without the uid suffix of LDAP homes (bob-1000101 -> bob).
_photo_folders() { # <volume>
	local dcim dev owner home
	for dcim in "$1"/homes/*/Photos/MobileBackup/*/DCIM "$1"/homes/@*/*/*/Photos/MobileBackup/*/DCIM; do
		[ -d "$dcim" ] || continue
		dev=${dcim%/DCIM}
		dev=${dev##*/}
		home=${dcim%%/Photos/*}
		owner=${home##*/}
		owner=${owner%-[0-9]*}
		printf '%s\t%s\t%s\n' "$dev" "$owner" "$(_newest_dir "$dcim")"
	done
}

# A phone's DCIM folder from the config, or the usual MobileBackup one.
_dcim_of() { # <volume> <owner> <device> <configured path>
	local d
	[ -n "$4" ] && { echo "$4"; return; }
	for d in "$1"/homes/"$2"/Photos/MobileBackup/"$3"/DCIM "$1"/homes/@*/*/"$2"-[0-9]*/Photos/MobileBackup/"$3"/DCIM; do
		[ -d "$d" ] && { echo "$d"; return; }
	done
}

# Config lines as owner \t kind \t device \t max days \t photos folder.
_uploaders() { # <file>
	[ -f "$1" ] || return 0
	awk -F'|' -v OFS='\t' '
		function trim(s) { gsub(/^[[:space:]]+|[[:space:]]+$/, "", s); return s }
		/^[[:space:]]*(#|$)/ { next }
		{ max = trim($4) + 0; print trim($1), trim($2), trim($3), (max > 0 ? max : 7), trim($5) }' "$1"
}

devices_json() { # <volume>
	local vol=$1 now=${DEVICES_NOW:-$(date +%s)} conf=${UPLOADERS_CONF:-${OPS_HOME:+$OPS_HOME/uploaders.conf}}
	local mem="" t owner kind dev max photos dcim newest
	t=$(mktemp -d)
	[ -n "${JOBS_STATE_DIR:-}" ] && mem="$JOBS_STATE_DIR/devices-seen.tsv" && touch "$mem"
	_drive_clients "$now" >"$t/drive"
	# Last sighting per device (owner FS device): today's connections win.
	awk -F'\t' -v OFS='\t' 'FILENAME == ARGV[1] { k = $2 FS $1; s[k] = $0; next }
		{ k = $2 FS $1; if (!(k in s) || $5 > (split(s[k], f, FS) ? f[5] : 0)) s[k] = $0 }
		END { for (k in s) print s[k] }' "$t/drive" ${mem:+"$mem"} | sort >"$t/seen"
	[ -n "$mem" ] && cp "$t/seen" "$mem"
	_photo_folders "$vol" >"$t/photos"

	# Configured devices, with both signals.
	: >"$t/listed"
	if [ -n "$conf" ]; then
		while IFS=$'\t' read -r owner kind dev max photos; do
			newest=""
			if [ "$kind" = phone ]; then
				dcim=$(_dcim_of "$vol" "$owner" "$dev" "$photos")
				[ -z "$dcim" ] || newest=$(_newest_dir "$dcim")
			fi
			printf '%s\t%s\t%s\t%s\t%s\n' "$owner" "$kind" "$dev" "$max" "$newest" >>"$t/listed"
		done < <(_uploaders "$conf")
	fi

	awk -F'\t' -v now="$now" -v configured="$([ -n "$conf" ] && [ -f "$conf" ] && echo 1 || echo 0)" '
		function js(s) { gsub(/\\/, "\\\\", s); gsub(/"/, "\\\"", s); return "\"" s "\"" }
		function nn(x) { return (x == "" ? "null" : x) }
		FILENAME ~ /\/drive$/ { online[$2 FS $1] = ($5 == now); next }   # connected right now
		FILENAME ~ /\/seen$/ { k = $2 FS $1; seen[k] = $5; ver[k] = $4; skind[k] = $3; next }
		FILENAME ~ /\/photos$/ { k = $2 FS $1; if ($3 != "" && $3 > photo[k]) photo[k] = $3; next }
		FILENAME ~ /\/listed$/ {
			k = $1 FS $3; listed[k] = 1
			L[++n] = sprintf("{\"owner\": %s, \"kind\": %s, \"name\": %s, \"max_days\": %d, \"version\": %s, \"last_seen\": %s, \"online\": %s, \"newest_photo\": %s}", \
				js($1), js($2), js($3), $4, (k in ver ? js(ver[k]) : "null"), nn(seen[k]), (online[k] ? "true" : "false"), nn($5))
		}
		END {
			printf "{\"configured\": %s, \"devices\": [", (configured ? "true" : "false")
			for (i = 1; i <= n; i++) printf "%s%s", (i > 1 ? ", " : ""), L[i]
			# Uploading in the last 30 days, but not in the file.
			printf "], \"unlisted\": ["; sep = ""
			for (k in seen) if (!(k in listed) && now - seen[k] < 30 * 86400) { u[k] = 1 }
			for (k in photo) if (!(k in listed) && now - photo[k] < 30 * 86400) { u[k] = 1 }
			for (k in u) {
				split(k, p, FS)
				printf "%s{\"owner\": %s, \"kind\": %s, \"name\": %s, \"last_seen\": %s, \"newest_photo\": %s}", sep, js(p[1]), \
					js(k in skind ? skind[k] : "phone"), js(p[2]), nn(seen[k]), nn(photo[k])
				sep = ", "
			}
			printf "]}"
		}' "$t/drive" "$t/seen" "$t/photos" "$t/listed"
	rm -rf -- "$t"
}

# Lines for uploaders.conf from what is uploading now (last 30 days).
devices_suggest() { # <volume>
	local json
	json=$(UPLOADERS_CONF=/dev/null devices_json "$1")
	echo "# owner | kind | device | max days | photos folder (optional)"
	printf '%s\n' "$json" | grep -o '{"owner": [^}]*}' | sed -n 's/.*"owner": "\([^"]*\)", "kind": "\([^"]*\)", "name": "\([^"]*\)".*/\1 | \2 | \3 | 7/p' | sort -u
}
