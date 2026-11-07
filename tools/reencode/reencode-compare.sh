#!/usr/bin/env bash
# Pick a preset for a series before converting it: encodes the same excerpt
# of one episode with each preset and prepares everything to compare them
# with the original. Runs on the desktop, like reencode.sh.
#
#   reencode-compare.sh [-s START] [-t SECONDS] EPISODE [PRESET...]
#
#   EPISODE     a video file (e.g. on the cifs mount of the media share)
#   PRESET...   which presets to try (default: all three)
#                 sitcom   sitcoms, old TV, animation, talk shows (smallest)
#                 series   modern drama series
#                 film     films, cinematic, dark or grainy series (best)
#               Experts: a number tries that GPU quantizer (qp) instead.
#   -s START    where the excerpt starts, in seconds (default 300)
#   -t SECONDS  excerpt length (default 120; starts at the keyframe before
#               START, so it may come out a few seconds longer)
#
# Output in ~/reencode/compare/<episode>/:
#   summary         sizes, estimated whole-episode size, VMAF (if available)
#   split-<preset>.mkv  left half original, right half re-encoded, in sync,
#                   with a line and labels between them
#   frames/         the same frames from each version, to flip between
#
# Then pick the smallest preset you can't tell from the original and run
#   reencode.sh <preset> "<series folder>"
set -euo pipefail

usage() { sed -n '2,/^set -/p' "$0" | grep '^#' | sed 's/^# \{0,1\}//' >&2; exit 2; }

# shellcheck source=SCRIPTDIR/reencode-presets.sh
. "$(dirname -- "$(realpath -- "$0")")/reencode-presets.sh"

START=300 LEN=120
while getopts "s:t:h" opt; do
	case $opt in
	s) START=$OPTARG ;;
	t) LEN=$OPTARG ;;
	*) usage ;;
	esac
done
shift $((OPTIND - 1))
[ $# -ge 1 ] || usage
if [ ! -f "$1" ]; then
	echo "Not a file: $1" >&2
	echo "(A path with spaces needs quotes: \"/mnt/media/media/tv/30 Rock/...mkv\")" >&2
	exit 2
fi
SRC=$(realpath -- "$1") # the script works from its output folder
shift
# shellcheck disable=SC2086 # the list is split on purpose
[ $# -gt 0 ] || set -- $PRESETS

# Each version: a label (the preset's name, or qpNN) and its quantizer.
LABELS=() QPS=()
for v in "$@"; do
	if is_preset "$v"; then
		LABELS+=("$v") QPS+=("$(preset_qp "$v")")
	elif [[ $v =~ ^[0-9]+$ ]]; then
		LABELS+=("qp$v") QPS+=("$v")
	else
		echo "Unknown preset: $v (use $PRESETS, or a number)" >&2
		exit 2
	fi
done

export LIBVA_DRIVER_NAME="${LIBVA_DRIVER_NAME:-iHD}"
readonly DEVICE=/dev/dri/renderD128
name=$(basename -- "${SRC%.*}")
OUT="$HOME/reencode/compare/$name"
mkdir -p "$OUT/frames"
cd "$OUT"
ff() { ffmpeg -nostdin -hide_banner -loglevel error -y "$@"; }
dur_of() { ffprobe -v error -show_entries format=duration -of csv=p=0 -- "$1"; }
mb() { awk -v b="$1" 'BEGIN { printf "%.0f MB", b / 1e6 }'; }

# 1. The excerpt, copied as is: every version is made from this exact file.
echo "Cutting ${LEN}s from ${START}s of $(basename -- "$SRC")..."
ff -ss "$START" -t "$LEN" -i "$SRC" -map 0:v:0 -c copy -an orig.mkv
seg=$(dur_of orig.mkv)

# 2. One encode per version, as reencode.sh does it.
for i in "${!LABELS[@]}"; do
	echo "Encoding ${LABELS[$i]}..."
	ff -hwaccel vaapi -hwaccel_output_format vaapi -vaapi_device "$DEVICE" -i orig.mkv \
		-c:v hevc_vaapi -rc_mode CQP -qp "${QPS[$i]}" "${LABELS[$i]}.mkv"
done

# 3. Split screens (left original, right re-encode), near-lossless so the
#    comparison itself adds no artefacts. The halves join seamlessly when the
#    quality matches, so a line and labels show where each side is.
marks() { # <label>
	local label="fontcolor=white:fontsize=h/24:box=1:boxcolor=black@0.6:boxborderw=8:y=20"
	printf '%s' "drawbox=x=iw/2-3:y=0:w=6:h=ih:color=black:t=fill,drawbox=x=iw/2-1:y=0:w=2:h=ih:color=white:t=fill,"
	printf '%s' "drawtext=text=original:x=20:$label,drawtext=text=$1:x=w-tw-20:$label"
}
for l in "${LABELS[@]}"; do
	echo "Building split-$l.mkv..."
	# Square pixels first: an anamorphic source (DVD, half-SBS) would
	# otherwise come out stretched.
	ff -i orig.mkv -i "$l.mkv" -filter_complex \
		"[0:v]scale=iw*sar:ih,setsar=1,crop=iw/2:ih:0:0[l];[1:v]scale=iw*sar:ih,setsar=1,crop=iw/2:ih:iw/2:0[r];[l][r]hstack,$(marks "$l")" \
		-c:v libx264 -crf 10 -preset ultrafast "split-$l.mkv"
done

# 4. Four frames spread over the excerpt, same numbers in every version.
echo "Extracting frames..."
for v in orig "${LABELS[@]}"; do
	for i in 1 2 3 4; do
		ff -ss "$(awk -v d="$seg" -v i="$i" 'BEGIN { print d * i / 5 }')" -i "$v.mkv" \
			-frames:v 1 "frames/$i-$v.png"
	done
done

# 5. Summary: what a whole episode would weigh with each version.
codec=$(ffprobe -v error -select_streams v:0 -show_entries stream=codec_name -of csv=p=0 -- "$SRC" | sed -n 1p)
dims=$(ffprobe -v error -select_streams v:0 -show_entries stream=width,height -of csv=s=x:p=0 -- "$SRC" | sed -n 1p)
full_size=$(stat -c %s -- "$SRC")
full_dur=$(dur_of "$SRC")
orig_seg=$(stat -c %s orig.mkv)
mbit=$(awk -v s="$full_size" -v d="$full_dur" 'BEGIN { printf "%.1f", s * 8 / d / 1e6 }')
vmaf=0
ffmpeg -hide_banner -filters 2>/dev/null | grep -q libvmaf && vmaf=1
{
	printf 'Episode: %s\n' "$SRC"
	printf 'Excerpt: %ss from %ss · source %s %s, %s Mbit/s\n' "${seg%.*}" "$START" "$codec" "$dims" "$mbit"
	case $codec in hevc | av1) printf 'Note: reencode.sh skips this file (already %s).\n' "$codec" ;; esac
	case $(basename -- "$SRC") in *3D* | *SBS* | *[Hh]alf-*) printf 'Note: the name says 3D side-by-side: two images per frame on a 2D screen.\n' ;; esac
	echo
	printf '%-10s %10s %16s %7s %s\n' version excerpt "whole episode" saved "$([ "$vmaf" = 1 ] && echo VMAF)"
	printf '%-10s %10s %16s %7s\n' original "$(mb "$orig_seg")" "$(mb "$full_size")" -
	for l in "${LABELS[@]}"; do
		s=$(stat -c %s "$l.mkv")
		# Audio and subtitles are kept as they are: only the video part shrinks.
		est=$(awk -v F="$full_size" -v o="$orig_seg" -v n="$s" -v fd="$full_dur" -v sd="$seg" \
			'BEGIN { v = o * fd / sd; if (v > F) v = F; print F - v + n * fd / sd }')
		score=""
		if [ "$vmaf" = 1 ]; then
			score=$(ffmpeg -nostdin -hide_banner -i "$l.mkv" -i orig.mkv \
				-lavfi "libvmaf=n_threads=$(nproc)" -f null - 2>&1 | grep -o 'VMAF score: [0-9.]*' | grep -o '[0-9.]*$' || true)
		fi
		note=""
		if is_preset "$l" && awk -v m="$mbit" -v min="$(preset_min "$l")" 'BEGIN { exit !(m < min) }'; then
			note=" (reencode.sh $l would skip it: already below $(preset_min "$l") Mbit/s)"
		fi
		printf '%-10s %10s %16s %6s%% %s%s\n' "$l" "$(mb "$s")" "$(mb "$est")" \
			"$(awk -v a="$full_size" -v b="$est" 'BEGIN { printf "%.0f", (1 - b / a) * 100 }')" "$score" "$note"
	done
	if [ "$vmaf" = 1 ]; then
		printf '\nRough rule of thumb: VMAF 95+ = indistinguishable from the original, below 90 starts to show.\n'
	else
		printf '\n(No VMAF score: this ffmpeg is built without libvmaf. Judge by eye.)\n'
	fi
} | tee summary

cat <<EOF

Compare in: $OUT
  mpv "split-${LABELS[0]}.mkv"   left original, right re-encoded; space = pause, . and , = frame by frame
  frames/   open 1-orig.png and 1-${LABELS[0]}.png in an image viewer and flip between them
Pick the smallest preset you can't tell from the original, then:
  reencode.sh <preset> "<series folder>"     and delete $OUT when done.
EOF
