#!/usr/bin/env bash
# Find identical files inside ONE folder and delete the extra copies, keeping
# one of each. Runs on the laptop, over the cifs mount of the NAS share (see
# docs/guides/media-reencoding.md for the mount).
#
#   duplicates.sh [-a] [-k PATTERN]... [-m SIZE] DIR
#
#   DIR         one folder, e.g. one person's Photos. Files outside it are
#               never compared, so nobody loses a copy because someone else
#               has one.
#   -a          really delete. Without it: only shows what would be deleted.
#   -k PATTERN  never delete files whose path matches PATTERN, e.g.
#               -k '**/MobileBackup/**' (repeatable)
#   -m SIZE     ignore files smaller than SIZE (default: none; empty files
#               are always ignored)
#
# Which copy stays: of identical files, the most deeply nested one, e.g.
# MobileBackup/Pixel 7/DCIM/Camera/2021/10/x.mp4 rather than
# Photos from 2021/x.mp4 (a Google Takeout copy), unless -k says otherwise.
# Report and plan are kept in ~/duplicates/.
#
# Freed space shows up on the NAS only once the share's recycle bin is
# emptied (if enabled) and its snapshots holding the files have expired.
set -euo pipefail

usage() { sed -n '2,/^set -/p' "$0" | grep '^#' | sed 's/^# \{0,1\}//' >&2; exit 2; }

# fclones (github.com/pkolaczk/fclones): finds duplicates by size, then the
# first and last bytes, then a full hash, so most files are never read whole.
# Pinned and checked: the release publishes no checksums, this one was
# computed when it was pinned.
readonly FCLONES_VERSION=0.35.0
readonly FCLONES_SHA256=9eae0466e5b78871cf25822e503ee9efbfa28dc36cc167060c4a4920306389ac
readonly FCLONES_DIR="$HOME/.local/share/homelab/fclones-$FCLONES_VERSION"
readonly FCLONES="$FCLONES_DIR/fclones"

APPLY=0 MIN=1 KEEP=()
while getopts "ak:m:h" opt; do
	case $opt in
	a) APPLY=1 ;;
	k) KEEP+=(--keep-path "$OPTARG") ;;
	m) MIN=$OPTARG ;;
	*) usage ;;
	esac
done
shift $((OPTIND - 1))
[ $# -eq 1 ] || usage
[ -d "$1" ] || { echo "Not a folder: $1 (a path with spaces needs quotes)" >&2; exit 2; }
DIR=$(realpath -- "$1")

if [ ! -x "$FCLONES" ]; then
	echo "Installing fclones $FCLONES_VERSION into $FCLONES_DIR"
	tmp=$(mktemp -d)
	trap 'rm -rf -- "$tmp"' EXIT
	curl -fsSL -o "$tmp/f.tgz" \
		"https://github.com/pkolaczk/fclones/releases/download/v$FCLONES_VERSION/fclones-$FCLONES_VERSION-linux-musl-x86_64.tar.gz"
	echo "$FCLONES_SHA256  $tmp/f.tgz" | sha256sum -c --quiet || { echo "Checksum mismatch: not installed" >&2; exit 1; }
	tar xzf "$tmp/f.tgz" -C "$tmp"
	mkdir -p "$FCLONES_DIR"
	install -m 755 "$(find "$tmp" -type f -name fclones | head -n 1)" "$FCLONES"
fi

OUT="$HOME/duplicates"
mkdir -p "$OUT"
name=$(basename -- "$DIR")
stamp="$(printf '%s' "$name" | tr -c 'A-Za-z0-9._-' '_')-$(date +%Y%m%d-%H%M)"
report="$OUT/$stamp-report.txt"
plan="$OUT/$stamp-plan.txt"

# DSM's own folders: snapshots, recycle bins, thumbnails. Hidden files are
# skipped by fclones already. Never .json: Google Takeout writes one next to
# each photo (date, place, description), and two identical ones can still
# belong to different photos.
log="$OUT/$stamp-log.txt"
echo "Looking for duplicates in $DIR..."
echo "(the last stage reads every candidate file in full)"
# Identical = same size, same first and last bytes, and the same BLAKE3
# (cryptographic) hash of the whole content; fclones' default hash is faster
# but not cryptographic, and these are photos about to be deleted.
"$FCLONES" group --min "$MIN" --hash-fn blake3 \
	--exclude '**/#snapshot/**' --exclude '**/#recycle/**' --exclude '**/@eaDir/**' --exclude '**/*.json' \
	-o "$report" "$DIR" 2> >(tee -a "$log" | sed -u -n 's/^.*fclones: *info: /  /p' >&2)
wait # for the progress lines to drain
grep -E '^# (Total|Redundant):' "$report" | sed 's/^# /  /'

"$FCLONES" remove --dry-run --priority least-nested ${KEEP[@]+"${KEEP[@]}"} <"$report" \
	2>>"$log" | sed "s|$DIR/||g" >"$plan"
n=$(grep -c '^rm ' "$plan" || true)
if [ "$n" -eq 0 ]; then
	echo "No duplicates to delete."
	exit 0
fi
echo
echo "Would delete $n file(s), keeping one copy of each. First ones:"
head -n 15 "$plan" | sed 's/^rm /  delete  /'
echo "  … full list: $plan"

if [ "$APPLY" = 1 ]; then
	echo
	echo "Deleting..."
	"$FCLONES" remove --priority least-nested ${KEEP[@]+"${KEEP[@]}"} <"$report" 2>>"$log"
	grep -E 'Processed [0-9]+ files' "$log" | tail -n 1 | sed 's/^.*fclones: *info: /  /'
	echo "Done. Space returns on the NAS once the recycle bin is emptied and old snapshots expire."
else
	echo
	echo "Nothing deleted. Check the list, then run again with -a to delete."
fi
