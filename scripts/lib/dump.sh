# shellcheck shell=bash
# Shared by the app-dump jobs (forgejo-dump.sh, mealie-backup.sh). Source it.
#
# A dump only gets its final name once it is complete: copied under a hidden
# '.partial' name, checked, then renamed. A hang, a full disk or a killed copy
# therefore never leaves a file the dashboard or Hyper Backup would take for a
# good dump.

# A zip ends with an end-of-central-directory record ("PK\5\6") in its last
# 64 KiB; a truncated copy does not. No unzip/python needed on DSM.
is_complete_zip() { # <file>
	tail -c 65558 -- "$1" | grep -q -a $'PK\x05\x06'
}

# Check <partial>, give it its final name in <dir>, prune that dir's dumps
# matching <glob> older than <keep_days>. Fails (non-zero) on a bad dump.
publish_dump() { # <partial> <dir> <final name> <glob> <keep_days>
	local partial=$1 dir=$2 name=$3 glob=$4 keep=$5
	if [ ! -s "$partial" ] || ! is_complete_zip "$partial"; then
		echo "Dump is empty or truncated: $(stat -c %s -- "$partial" 2>/dev/null || echo 0) bytes" >&2
		return 1
	fi
	chmod 600 -- "$partial"
	mv -f -- "$partial" "$dir/$name"
	# Hyper Backup keeps the long history; locally a couple of weeks is enough.
	find "$dir" -maxdepth 1 -name "$glob" -mtime "+$keep" -delete
	echo "Dump written to $dir/$name ($(stat -c %s -- "$dir/$name") bytes)"
}
