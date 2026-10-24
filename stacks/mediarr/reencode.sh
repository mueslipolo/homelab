#!/usr/bin/env bash
# Re-encode large H.264 (or older) videos to HEVC on an Intel GPU, in place.
# Meant for a desktop with the NAS's media share mounted (SMB); see
# docs/guides/media-reencoding.md. Not run on the NAS.
#
#   reencode.sh [-n] PRESET DIR...
#
#   PRESET    what kind of material, which sets the quality:
#               sitcom   sitcoms, old TV, animation, talk shows (smallest)
#               series   modern drama series
#               film     films, cinematic, dark or grainy series (best)
#             Each preset also leaves alone files that are already small
#             enough for it (reencode-presets.sh has the numbers).
#   -n        dry run: list what would be re-encoded, with totals per folder
#             (per series when DIR is tv/), largest first; change nothing
#
#   Expert overrides: -q QP (GPU quantizer, lower = better and bigger) and
#   -m MBIT (skip files below this bitrate), instead of or on top of PRESET.
#
#   Examples:
#     reencode.sh -n sitcom "/mnt/media/media/tv"            what would change
#     reencode.sh sitcom "/mnt/media/media/tv/30 Rock"       do it for a series
#
# Per file: skip HEVC/AV1 and files already small; encode to a local temp dir
# (GPU decode, GPU encode; software decode as fallback); keep the first video
# stream and every audio, subtitle and attachment stream; check the result
# (same duration, same stream counts, really smaller); only then replace the
# original on the share. An .mp4/.avi becomes .mkv; the old file is removed.
# Afterwards: Sonarr/Radarr -> Rescan, and turn off upgrades for the series.
set -euo pipefail

usage() { sed -n '2,/^set -/p' "$0" | grep '^#' | sed 's/^# \{0,1\}//' >&2; exit 2; }

# shellcheck source=SCRIPTDIR/reencode-presets.sh
. "$(dirname -- "$(realpath -- "$0")")/reencode-presets.sh"

# Options may come before or after the preset: -n sitcom DIR or sitcom -n DIR.
QP="" MIN_MBIT="" DRY=0 PRESET="" DIRS=()
while [ $# -gt 0 ]; do
	case $1 in
	-n) DRY=1 ;;
	-q) QP=${2:?-q needs a value}; shift ;;
	-m) MIN_MBIT=${2:?-m needs a value}; shift ;;
	-h | --help) usage ;;
	-*) echo "Unknown option: $1" >&2; usage ;;
	*)
		if [ -z "$PRESET" ] && [ ${#DIRS[@]} -eq 0 ] && is_preset "$1"; then
			PRESET=$1
		else
			DIRS+=("$1")
		fi
		;;
	esac
	shift
done
if [ -z "$PRESET" ] && [ -z "$QP" ]; then
	echo "Which preset? $PRESETS (e.g. reencode.sh sitcom \"<folder>\")" >&2
	exit 2
fi
[ -n "$QP" ] || QP=$(preset_qp "$PRESET")
[ -n "$MIN_MBIT" ] || MIN_MBIT=$( [ -n "$PRESET" ] && preset_min "$PRESET" || echo 3)
[ ${#DIRS[@]} -gt 0 ] || { echo "Which folder? e.g. \"/mnt/media/media/tv/30 Rock\"" >&2; exit 2; }
for d in "${DIRS[@]}"; do
	[ -e "$d" ] || { echo "Not found: $d (a path with spaces needs quotes)" >&2; exit 2; }
done
set -- "${DIRS[@]}"
echo "Preset: ${PRESET:-custom} (qp $QP, files above $MIN_MBIT Mbit/s)"

export LIBVA_DRIVER_NAME="${LIBVA_DRIVER_NAME:-iHD}"
readonly DEVICE=/dev/dri/renderD128
WORK=$(mktemp -d "${TMPDIR:-/tmp}/reencode.XXXXXX")
trap 'rm -rf -- "$WORK"' EXIT

# An unreadable file gives an empty answer (and is skipped), never an abort.
probe() { # <file> <entries> [<stream selector>]
	local sel=()
	[ -z "${3:-}" ] || sel=(-select_streams "$3")
	{ ffprobe -v fatal "${sel[@]}" -show_entries "$2" -of csv=p=0 -- "$1" </dev/null || true; } | sed -n 1p
}
count() { { ffprobe -v fatal -select_streams "$2" -show_entries stream=index -of csv=p=0 -- "$1" </dev/null || true; } | grep -c . || true; }
human() { numfmt --to=iec --suffix=B "$1"; }

encode() { # <in> <out> <subtitle codec> <hw decode: 1|0>
	local dec=()
	[ "$4" = 1 ] && dec=(-hwaccel vaapi -hwaccel_output_format vaapi)
	local vf=()
	[ "$4" = 1 ] || vf=(-vf 'format=nv12,hwupload')
	ffmpeg -nostdin -hide_banner -loglevel error -stats -y "${dec[@]}" -vaapi_device "$DEVICE" -i "$1" \
		-map 0:v:0 -map '0:a?' -map '0:s?' -map '0:t?' -c copy -c:s "$3" \
		"${vf[@]}" -c:v hevc_vaapi -rc_mode CQP -qp "$QP" "$2"
}

saved=0 done_n=0 skipped=0 failed=0
# Dry run: totals per folder directly under each DIR (a series under tv/, a
# season under a series), so a scan of the whole library ranks what to do first.
declare -A g_size=() g_n=() g_secs=()
group_of() { # <file>
	local root rel
	for root in "$@"; do
		root=${root%/}
		case $1 in "$root"/*) ;; *) continue ;; esac
		rel=${1#"$root"/}
		[ "${rel%%/*}" = "$rel" ] && { echo "${root##*/}"; return; }
		echo "${rel%%/*}"
		return
	done
}
ROOTS=("$@")
while IFS= read -r -d '' f; do
	name=${f##*/}
	codec=$(probe "$f" stream=codec_name v:0)
	case $codec in hevc | av1 | '') skipped=$((skipped + 1)); continue ;; esac
	size=$(stat -c %s -- "$f")
	dur=$(probe "$f" format=duration)
	mbit=$(awk -v s="$size" -v d="$dur" 'BEGIN { printf "%.1f", (d > 0 ? s * 8 / d / 1e6 : 0) }')
	if awk -v m="$mbit" -v min="$MIN_MBIT" 'BEGIN { exit !(m < min) }'; then
		skipped=$((skipped + 1))
		continue
	fi
	if [ "$DRY" = 1 ]; then
		printf '%-8s %5s Mbit/s  %s\n' "$(human "$size")" "$mbit" "$f"
		done_n=$((done_n + 1))
		g=$(group_of "$f" "${ROOTS[@]}")
		g_size[$g]=$(( ${g_size[$g]:-0} + size ))
		g_n[$g]=$(( ${g_n[$g]:-0} + 1 ))
		g_secs[$g]=$(awk -v a="${g_secs[$g]:-0}" -v d="$dur" 'BEGIN { printf "%d", a + d }')
		continue
	fi

	printf '\n%s (%s, %s, %s Mbit/s)\n' "$name" "$codec" "$(human "$size")" "$mbit"
	out="$WORK/${name%.*}.mkv"
	sub=copy
	[ "${f##*.}" = mp4 ] && sub=srt # mov_text cannot go into Matroska
	if ! encode "$f" "$out" "$sub" 1 && ! { echo "  GPU decode failed, retrying with CPU decode"; encode "$f" "$out" "$sub" 0; }; then
		echo "  ! encode failed, original kept"
		failed=$((failed + 1)); rm -f -- "$out"; continue
	fi

	# The checks that decide whether the original may go.
	new_size=$(stat -c %s -- "$out")
	new_dur=$(probe "$out" format=duration)
	problem=""
	awk -v a="$dur" -v b="$new_dur" 'BEGIN { exit !((a - b > 2) || (b - a > 2)) }' && problem="duration $dur -> $new_dur"
	for s in a s; do
		[ "$(count "$f" "$s")" = "$(count "$out" "$s")" ] || problem="$problem ${s}-stream count differs"
	done
	[ "$new_size" -lt $((size * 9 / 10)) ] || problem="$problem not smaller ($(human "$new_size"))"
	if [ -n "$problem" ]; then
		echo "  ! $problem -- original kept"
		failed=$((failed + 1)); rm -f -- "$out"; continue
	fi

	# Replace: copy next to the original under a hidden name, then rename over
	# it, so the share never holds a half-copied file under the real name.
	dir=${f%/*}
	tmp="$dir/.${name%.*}.reencode.part"
	cp -- "$out" "$tmp"
	mv -f -- "$tmp" "$dir/${name%.*}.mkv"
	[ "$f" = "$dir/${name%.*}.mkv" ] || rm -f -- "$f"
	rm -f -- "$out"
	saved=$((saved + size - new_size))
	done_n=$((done_n + 1))
	printf '  %s -> %s\n' "$(human "$size")" "$(human "$new_size")"
done < <(find "$@" -type f \( -iname '*.mkv' -o -iname '*.mp4' -o -iname '*.avi' -o -iname '*.m4v' \) \
	! -name '.*' -print0 | sort -z)

if [ "$DRY" = 1 ]; then
	if [ "$done_n" -gt 0 ]; then
		printf '\n%10s %6s %8s  %s\n' size files Mbit/s folder
		for g in "${!g_size[@]}"; do
			printf '%s\t%s\t%s\t%s\n' "${g_size[$g]}" "${g_n[$g]}" "${g_secs[$g]}" "$g"
		done | sort -rn | while IFS=$'\t' read -r b n secs g; do
			printf '%10s %6s %8s  %s\n' "$(human "$b")" "$n" \
				"$(awk -v b="$b" -v t="$secs" 'BEGIN { printf "%.1f", (t > 0 ? b * 8 / t / 1e6 : 0) }')" "$g"
		done
		total=0
		for g in "${!g_size[@]}"; do total=$((total + g_size[$g])); done
		printf '%10s in total (one measured sitcom episode shrank 84%%). Not sure which\n' "$(human "$total")"
		printf 'preset fits a series? Try one episode: reencode-compare.sh "<episode>"\n'
	fi
	printf '\n%d file(s) would be re-encoded with preset %s, %d skipped (HEVC/AV1 or below %s Mbit/s)\n' \
		"$done_n" "${PRESET:-custom (qp $QP)}" "$skipped" "$MIN_MBIT"
else
	printf '\n%d re-encoded, %s saved; %d skipped, %d failed (originals kept)\n' \
		"$done_n" "$(human "$saved")" "$skipped" "$failed"
	echo "Next: Sonarr/Radarr -> Rescan; turn off upgrades for these series."
fi
