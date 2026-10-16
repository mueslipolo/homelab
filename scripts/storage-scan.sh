#!/usr/bin/env bash
# Nightly picture of the disk for the dashboard's Storage tab: where the
# space goes, how it changes, and which series and films are worth
# re-encoding. Writes $DASHBOARD_LOCAL_DIR/storage.json; keeps its history
# and probe cache in $OPS_HOME/storage/. Runs as root from run-jobs.sh.
#
# Sources:
#   df                      volume total, used, free
#   btrfs qgroups           size per shared folder, Drive history, snapshots
#   btrfs filesystem du     Drive history's own space, when qgroups lack it
#   docker system df        Docker's share and what a prune would free
#   find + ffprobe          the media library: size, length and codec per
#                           file (ffprobe runs in the Jellyfin container; a
#                           file is probed once, then remembered by its
#                           path, size and date)
# Trends come from history.tsv, one block of lines per day: the past can't
# be rebuilt, so they fill in as the days go by.
set -euo pipefail

# shellcheck source=scripts/lib/json.sh
. "$(dirname -- "$0")/lib/json.sh"
# shellcheck source=scripts/lib/storage.sh
. "$(dirname -- "$0")/lib/storage.sh"

readonly VOLUME="${STATUS_VOLUME:?set STATUS_VOLUME to the volume to report, e.g. /volume1}"
readonly OUT_DIR="${DASHBOARD_LOCAL_DIR:?set DASHBOARD_LOCAL_DIR to the dashboard local folder}"
readonly STATE="${OPS_HOME:?set OPS_HOME}/storage"
readonly MEDIA="${STORAGE_MEDIA_DIR:-}"                     # host path holding tv/ and movies/
readonly IN_CONTAINER="${JELLYFIN_MEDIA_PATH:-/data/media}" # the same folder inside Jellyfin
readonly TODAY="${STORAGE_TODAY:-$(date +%F)}"              # tests pin the date
PATH="$PATH:/usr/syno/sbin:/usr/syno/bin:/sbin:/usr/sbin"

mkdir -p "$STATE"
chmod 700 "$STATE"
TMP=$(mktemp -d)
trap 'rm -rf -- "$TMP"' EXIT

# ---------------------------------------------------------------- collect
read -r total used free < <(df -B1 -P "$VOLUME" | awk 'NR == 2 { print $2, $3, $4 }')
subvolumes >"$TMP/subvols.tsv"      # name \t kind \t rfer \t excl
drive_own "$TMP/subvols.tsv"
docker_usage >"$TMP/docker.tsv"     # total \t reclaimable
recycle_bins "$VOLUME" >"$TMP/recycle.tsv"
if [ -n "$MEDIA" ] && [ -d "$MEDIA" ]; then
	media_files "$MEDIA" >"$TMP/files.tsv"                            # rel \t size \t mtime
	probe_new "$TMP/files.tsv" "$STATE/probe.tsv" "$IN_CONTAINER"     # updates the cache
	media_items "$TMP/files.tsv" "$STATE/probe.tsv" >"$TMP/items.tsv" # kind \t name \t bytes \t files \t episodes \t secs \t hevc \t saving
else
	: >"$TMP/items.tsv"
fi

# ---------------------------------------------------------------- history
# Today's block replaces any earlier run of the same day; 400 days are kept.
{
	printf '%s\tused\t-\t%s\t0\n' "$TODAY" "$used"
	# Drive's history as the page shows it: its own space when btrfs tracks it.
	awk -F'\t' -v d="$TODAY" '
		$2 == "share" { printf "%s\tshare\t%s\t%s\t0\n", d, $1, $3 }
		$2 == "drive" { printf "%s\tdrive\t-\t%s\t0\n", d, ($4 > 0 ? $4 : $3) }' "$TMP/subvols.tsv"
	awk -F'\t' -v d="$TODAY" '{ printf "%s\t%s\t%s\t%s\t%s\n", d, $1, $2, $3, $5 }' "$TMP/items.tsv"
} >"$TMP/today.tsv"
touch "$STATE/history.tsv"
cutoff=$(date -d "$TODAY -400 days" +%F)
awk -F'\t' -v t="$TODAY" -v c="$cutoff" '$1 != t && $1 >= c' "$STATE/history.tsv" | cat - "$TMP/today.tsv" | sort -s -k1,1 >"$TMP/history.tsv"
mv -f "$TMP/history.tsv" "$STATE/history.tsv"

# ------------------------------------------------------------------ output
storage_json "$TODAY" "$total" "$used" "$free" "$TMP" "$STATE/history.tsv" | write_atomic "$OUT_DIR/storage.json"
echo "storage.json written: $(awk -F'\t' '$1 == "series"' "$TMP/items.tsv" | wc -l) series, $(awk -F'\t' '$1 == "film"' "$TMP/items.tsv" | wc -l) films"
