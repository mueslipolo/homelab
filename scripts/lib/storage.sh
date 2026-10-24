# shellcheck shell=bash
# Collectors for storage-scan.sh. Sourced; each prints tab-separated lines.
# Every source is optional: off DSM, without Docker or without Jellyfin the
# matching part is simply empty, and the dashboard leaves it out.

# One line per subvolume of interest: name \t kind \t referenced \t exclusive
# (bytes). kind: share, drive (Synology Drive's history), hbcache (Hyper
# Backup's cache), snapshot (name = the share it belongs to), system (DSM's
# own subvolumes: @syno, @SynoDrive...). "exclusive" is 0 when btrfs does
# not track it; storage.awk then says so.
subvolumes() {
	command -v btrfs >/dev/null || return 0
	awk -F'\t' '
		FILENAME == ARGV[1] { path[$1] = $2; next }
		!($1 in path) { next }
		{
			p = path[$1]
			if (p == "@synologydrive") kind = "drive"
			else if (p == "@img_bkp_cache") kind = "hbcache"
			else if (p ~ /^@sharesnap\/[^\/]+\/./) { kind = "snapshot"; split(p, s, "/"); p = s[2] }
			else if (p !~ /^[@#]/ && p !~ /\//) kind = "share"
			else if (p ~ /^@/ && p !~ /\// && p != "@docker") kind = "system"   # Docker: from docker system df
			else next
			printf "%s\t%s\t%s\t%s\n", p, kind, $2, $3
		}' \
		<(btrfs subvolume list "$VOLUME" 2>/dev/null | awk '{ id = $2; sub(/^.* path /, ""); print id "\t" $0 }') \
		<(btrfs qgroup show --raw "$VOLUME" 2>/dev/null | awk '$1 ~ /^0\/[0-9]+$/ { sub(/^0\//, "", $1); print $1 "\t" $2 "\t" $3 }')
}

# Drive's history is mostly clones of the files in the shares (shared
# blocks): its referenced size said 193 GB where it held 4.6 GB of its own.
# When the qgroup counters don't report exclusive space, ask btrfs directly;
# it walks the folder's metadata (minutes), so only for this one.
drive_own() { # <subvols.tsv>  rewrites the drive line's exclusive column
	local own
	awk -F'\t' '$2 == "drive" && $4 == 0 { found = 1 } END { exit !found }' "$1" || return 0
	own=$(btrfs filesystem du -s --raw "$VOLUME/@synologydrive" 2>/dev/null | awk 'NR == 2 { print $2 }')
	[ -n "$own" ] || return 0
	awk -F'\t' -v OFS='\t' -v own="$own" '$2 == "drive" { $4 = own } { print }' "$1" >"$1.new" && mv -f "$1.new" "$1"
}

# btrfs metadata on disk, in bytes (one line): what `btrfs filesystem df`
# reports as used for Metadata and System, twice when DUP (two copies).
fs_metadata() {
	command -v btrfs >/dev/null || return 0
	btrfs filesystem df --raw "$VOLUME" 2>/dev/null | awk '
		/^(Metadata|System),/ && match($0, /used=[0-9]+/) {
			n = substr($0, RSTART + 5, RLENGTH - 5); t += ($2 ~ /^DUP/ ? 2 : 1) * n; found = 1
		}
		END { if (found) printf "%.0f\n", t }'
}

# Docker's total, in bytes (one line).
docker_usage() {
	command -v docker >/dev/null || return 0
	docker system df --format '{{.Type}}\t{{.Size}}' 2>/dev/null | awk -F'\t' '
		function bytes(s,   n, u) {
			if (!match(s, /^[0-9.]+/)) return 0
			n = substr(s, 1, RLENGTH); u = substr(s, RLENGTH + 1); sub(/ .*/, "", u)
			if (u ~ /^[kK]B$/) return n * 1e3
			if (u == "MB") return n * 1e6
			if (u == "GB") return n * 1e9
			if (u == "TB") return n * 1e12
			return n
		}
		{ total += bytes($2) }
		END { if (NR) printf "%.0f\n", total }'
}

# Video files under the media folder: path relative to it \t size \t mtime.
media_files() { # <media dir>
	find "$1" -type f \( -iname '*.mkv' -o -iname '*.mp4' -o -iname '*.avi' -o -iname '*.m4v' -o -iname '*.ts' \) \
		! -name '.*' -printf '%P\t%s\t%T@\n' 2>/dev/null | awk -F'\t' 'NF == 3'
}

# Probe the files the cache doesn't know yet (new, or changed size or date)
# with ffprobe inside the Jellyfin container, appending each result to the
# cache as it comes: a run cut short by its timeout resumes where it stopped.
# Cache lines: path \t size \t mtime \t codec \t seconds. Entries for files
# that are gone are dropped.
probe_new() { # <files.tsv> <cache> <media path inside the container>
	local files=$1 cache=$2 root=$3 todo container
	touch "$cache"
	# FILENAME, not NR == FNR: with an empty first file, that test is true
	# for the second file too.
	todo=$(awk -F'\t' 'FILENAME == ARGV[1] { k[$1 FS $2 FS $3]; next } !(($1 FS $2 FS $3) in k)' "$cache" "$files")
	if [ -n "$todo" ]; then
		container=$(docker ps --format '{{.Names}} {{.Image}}' 2>/dev/null | awk '$2 ~ /(^|\/)jellyfin(:|$)/ { print $1; exit }')
		if [ -z "$container" ]; then
			echo "No running Jellyfin container: $(printf '%s\n' "$todo" | wc -l) file(s) left unprobed" >&2
		else
			echo "Probing $(printf '%s\n' "$todo" | wc -l) new or changed file(s) in $container"
			# shellcheck disable=SC2016 # expanded inside the container
			printf '%s\n' "$todo" | docker exec -i -e R="$root" "$container" sh -c '
				P=/usr/lib/jellyfin-ffmpeg/ffprobe; [ -x "$P" ] || P=ffprobe
				T=$(printf "\t")
				while IFS="$T" read -r f s m; do
					printf "%s\t%s\t%s\t" "$f" "$s" "$m"
					timeout 60 "$P" -v error -select_streams v:0 -show_entries stream=codec_name:format=duration \
						-of csv=p=0 "$R/$f" </dev/null 2>/dev/null | head -n 2 | tr "\n" "\t"
					echo
				done' | awk -F'\t' -v OFS='\t' '{
					codec = $4; secs = $5
					if (codec ~ /^[0-9.]+$/) { secs = codec; codec = "?" }   # no video stream
					print $1, $2, $3, (codec == "" ? "?" : codec), (secs == "" ? 0 : secs + 0); fflush()
				}' >>"$cache"
		fi
	fi
	# Keep one entry per current file: the newest matching line.
	awk -F'\t' 'FILENAME == ARGV[1] { k[$1 FS $2 FS $3]; next } (($1 FS $2 FS $3) in k) { last[$1 FS $2 FS $3] = $0 }
		END { for (x in last) print last[x] }' "$files" "$cache" | sort >"$cache.new"
	mv -f "$cache.new" "$cache"
}

# One line per series (tv/<name>/...) and per film (movies/<name>/...):
# kind \t name \t bytes \t files \t episodes \t seconds.
media_items() { # <files.tsv> <cache>
	awk -F'\t' '
		FILENAME == ARGV[1] { secs[$1] = $5; next }
		{
			n = split($1, part, "/")
			if (n < 3) next
			top = tolower(part[1])
			if (top ~ /^(tv|series|shows)$/) kind = "series"
			else if (top ~ /^(movies|films)$/) kind = "film"
			else next
			key = kind FS part[2]
			bytes[key] += $2; files[key]++; seconds[key] += ($1 in secs) ? secs[$1] : 0
			ep = 1
			if (kind == "series" && match(part[n], /[Ss][0-9]+[Ee][0-9]+([- ]?[Ee][0-9]+)*/)) {
				m = substr(part[n], RSTART, RLENGTH); ep = gsub(/[Ee][0-9]+/, "", m)
			}
			episodes[key] += ep
		}
		END {
			for (key in bytes) {
				split(key, k, FS)
				printf "%s\t%s\t%.0f\t%d\t%d\t%.0f\n", k[1], k[2], bytes[key], files[key], episodes[key], seconds[key]
			}
		}' "$2" "$1" | sort
}

# One line per season folder (tv/<series>/<season>/...): series \t season \t
# bytes \t episodes. Episodes directly in the series folder go under an
# empty season name. Episodes are counted as in media_items.
media_seasons() { # <files.tsv>
	awk -F'\t' '
		{
			n = split($1, part, "/")
			if (n < 3 || tolower(part[1]) !~ /^(tv|series|shows)$/) next
			key = part[2] FS (n >= 4 ? part[3] : "")
			bytes[key] += $2; ep = 1
			if (match(part[n], /[Ss][0-9]+[Ee][0-9]+([- ]?[Ee][0-9]+)*/)) { m = substr(part[n], RSTART, RLENGTH); ep = gsub(/[Ee][0-9]+/, "", m) }
			episodes[key] += ep
		}
		END { for (key in bytes) { split(key, k, FS); printf "%s\t%s\t%.0f\t%d\n", k[1], k[2], bytes[key], episodes[key] } }' "$1" | sort
}

# The largest folders of each shared folder, for the volume treemap:
# share \t total \t <path> \t bytes   what du measured for the whole share
# share \t folder \t <path> \t bytes  one of its 12 largest folders
# The folders are the share's top-level ones, unless one folder holds 90 %
# of it: then that folder's own (homes/@LH-<domain>/61/<user> for LDAP
# homes, media/<tv, movies> for a library one level down), up to 3 levels.
# du counts the blocks on disk (sparse and compressed files count less) and
# skips the snapshots. One pass per share over its metadata: minutes for
# large photo folders.
share_folders() { # <volume> <subvols.tsv>
	local s
	awk -F'\t' '$2 == "share" { print $1 }' "$2" | while IFS= read -r s; do
		[ -d "$1/$s" ] || continue
		du -xk -d4 --exclude='#snapshot' -- "$1/$s" 2>/dev/null | awk -F'\t' -v root="$1/$s" -v share="$s" '
			NF != 2 { next }
			{
				rel = $2 == root ? "" : substr($2, length(root) + 2)
				size[rel] = $1 * 1024
				if (rel == "") next
				parent = rel; if (!sub(/\/[^\/]*$/, "", parent)) parent = ""
				kids[parent] = kids[parent] "\n" rel; haskids[parent] = 1
			}
			END {
				if (!("" in size)) exit
				cur = ""
				for (lvl = 0; lvl < 3; lvl++) {
					best = ""; n = split(substr(kids[cur], 2), k, "\n")
					for (i = 1; i <= n; i++) if (best == "" || size[k[i]] > size[best]) best = k[i]
					if (best == "" || !(best in haskids) || size[best] < 0.9 * size[cur]) break
					cur = best
				}
				printf "%s\ttotal\t\t%.0f\n", share, size[""]
				n = split(substr(kids[cur], 2), k, "\n")
				for (r = 1; r <= 12; r++) {
					best = 0
					for (i = 1; i <= n; i++) if (!(i in taken) && size[k[i]] > 0 && (best == 0 || size[k[i]] > size[k[best]])) best = i
					if (!best) break
					taken[best]; printf "%s\tfolder\t%s\t%.0f\n", share, k[best], size[k[best]]
				}
			}'
	done
}

# What the snapshots still hold that the shared folders no longer have:
# files in any of a share's snapshots whose inode is gone from the live
# folder (a snapshot keeps inode numbers, so a moved or renamed file still
# matches). Per share:
# share \t total \t \t bytes
# share \t folder \t <path> \t bytes   its 12 largest folders, up to 5 levels deep
# File sizes, not blocks: an estimate. A file changed in place keeps its
# inode, so the old blocks a snapshot keeps of it are not counted.
snapshot_held() { # <volume>
	local dir share s
	for dir in "$1"/@sharesnap/*/; do
		share=$(basename "$dir")
		case $share in @*) continue ;; esac # @<share>.meta: DSM's bookkeeping
		[ -d "$1/$share" ] || continue
		ls -d "$dir"GMT* >/dev/null 2>&1 || continue
		{
			find "$1/$share" -xdev -type f -printf '%i\n' 2>/dev/null
			echo '--'
			for s in "$dir"GMT*; do find "$s" -type f -printf '%i\t%s\t%P\n' 2>/dev/null; done
		} | awk -F'\t' -v share="$share" '
			!snap { if ($0 == "--") snap = 1; else live[$1]; next }
			NF != 3 || ($1 in live) || ($1 in seen) { next }
			{
				seen[$1]; n = split($3, p, "/"); d = (n == 1 ? "." : p[1])
				for (i = 2; i < n && i <= 5; i++) d = d "/" p[i]
				sum[d] += $2; tot += $2
			}
			END {
				if (!tot) exit
				printf "%s\ttotal\t\t%.0f\n", share, tot
				for (r = 1; r <= 12; r++) {
					best = ""
					for (d in sum) if (!(d in taken) && (best == "" || sum[d] > sum[best])) best = d
					if (best == "") break
					taken[best]; printf "%s\tfolder\t%s\t%.0f\n", share, best, sum[best]
				}
			}'
	done
}

# The JSON for the dashboard, from the collected files and the history.
storage_json() { # <today> <total> <used> <free> <tmp dir> <history.tsv>
	local t=$1 targets="" k
	for k in 7 30 90; do targets="$targets $(date -d "$t -$k days" +%F)"; done
	local weeks=""
	for k in 11 10 9 8 7 6 5 4 3 2 1 0; do weeks="$weeks $(date -d "$t -$((k * 7)) days" +%F)"; done
	awk -F'\t' -v today="$t" -v total="$2" -v used="$3" -v free="$4" -v targets="$targets" -v weeks="$weeks" \
		-v generated="$(date +%s)" -v media_share="$(media_share)" \
		-f "$(dirname -- "${BASH_SOURCE[0]}")/storage.awk" \
		"$5/subvols.tsv" "$5/docker.tsv" "$5/items.tsv" "$5/seasons.tsv" "$5/folders.tsv" "$5/held.tsv" "$5/metadata.tsv" "$6"
}

# The shared folder holding the media library (its size is already shown
# per series and film, so the change list leaves it out).
media_share() {
	[ -n "${STORAGE_MEDIA_DIR:-}" ] || return 0
	local rel=${STORAGE_MEDIA_DIR#"${STATUS_VOLUME%/}"/}
	echo "${rel%%/*}"
}
