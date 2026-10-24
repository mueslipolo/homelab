#!/usr/bin/env bash
# Why are forced writes slow on a volume? One read-only pass over what
# explains it, from the investigation in docs/slow-disk-writes.md: drives and
# their write cache, RAID and btrfs state, what is writing right now (disks,
# processes, containers), how long a 4 KB forced write takes, and which
# containers still use Container Manager's `db` log driver.
#
#   sudo bash tools/disk-diag.sh <volume> [test folder]
#   sudo bash tools/disk-diag.sh /volume1 /volume1/docker/authelia/data
#
# Writes nothing but a 4 KB test file in the test folder (default: the
# volume), deleted after each timing. Takes about 40 seconds.
# Indented with spaces, not tabs: pasted into an interactive shell, tabs
# trigger completion and mangle the script.
set -u
export LC_ALL=C

vol=${1:?usage: disk-diag.sh <volume> [test folder]}
dir=${2:-$vol}
hr() { printf '\n== %s\n' "$1"; }

hr "drives: model, rotation, write cache"
for d in /dev/sata[0-9] /dev/sd[a-z] /dev/nvme[0-9]n1; do
  [ -b "$d" ] || continue
  if command -v smartctl >/dev/null; then
    printf '%s: ' "$d"
    smartctl -i -g wcache -d sat "$d" 2>/dev/null |
      grep -E 'Device Model|Model Number|Rotation Rate|Write cache' | sed 's/  */ /g' | tr '\n' ';'
    echo
  else
    echo "$d: $(cat "/sys/block/${d##*/}/device/model" 2>/dev/null)"
  fi
done

hr "RAID: a resync, recovery or check makes every write slow"
cat /proc/mdstat

hr "btrfs on $vol"
grep " $vol " /proc/mounts
btrfs scrub status "$vol" 2>&1 | head -4
btrfs balance status "$vol" 2>&1 | head -2
echo "qgroups: $(btrfs qgroup show "$vol" 2>/dev/null | tail -n +3 | wc -l)"
echo "subvolumes by top folder (Docker's image layers are under @docker or btrfs):"
btrfs subvolume list "$vol" 2>/dev/null | awk '{ print $NF }' | awk -F/ '{ print "  " $1 "/" $2 }' |
  sort | uniq -c | sort -rn | head -6

hr "memory"
free -m

hr "disk activity over 10 s"
snap() { awk '$3 ~ /^(sata[0-9]|sd[a-z]|nvme[0-9]n1|md[0-9]+)$/ { print $3, $4, $8, $13 }' /proc/diskstats; }
a=$(snap); sleep 10; b=$(snap)
join <(echo "$a" | sort) <(echo "$b" | sort) |
  awk '{ printf "%-8s %7.1f reads/s %7.1f writes/s %4.0f %% busy\n", $1, ($5-$2)/10, ($6-$3)/10, ($7-$4)/100 }'

hr "who wrote the most in 10 s (MB written, MB read)"
io() {
  for p in /proc/[0-9]*; do
    [ -r "$p/io" ] && awk -v pid="${p#/proc/}" '/^read_bytes/ { r = $2 } /^write_bytes/ { w = $2 }
      END { print pid, r + 0, w + 0 }' "$p/io" 2>/dev/null
  done
}
a=$(io | sort); sleep 10; b=$(io | sort)
join <(echo "$a") <(echo "$b") | awk '{ printf "%s %.1f %.1f\n", $1, ($5-$3)/1048576, ($4-$2)/1048576 }' |
  sort -k2,2nr | head -8 | while read -r pid w r; do
    cid=$(grep -oE '[0-9a-f]{64}' "/proc/$pid/cgroup" 2>/dev/null | head -1)
    name=""
    [ -n "$cid" ] && name=$(docker inspect -f '{{.Name}}' "$cid" 2>/dev/null)
    printf '%-7s %6s %6s  %s %s\n' "$pid" "$w" "$r" "$(cat "/proc/$pid/comm" 2>/dev/null)" "$name"
  done

hr "4 KB forced write (seconds): $dir, then memory as the floor"
for t in "$dir" /dev/shm; do
  line=""
  for _ in 1 2 3 4 5 6 7 8 9 10; do
    start=$(date +%s%N)
    dd if=/dev/zero of="$t/disk-diag.test" bs=4k count=1 conv=fsync 2>/dev/null
    line="$line $(awk -v ns=$(($(date +%s%N) - start)) 'BEGIN { printf "%.2f", ns / 1e9 }')"
  done
  rm -f "$t/disk-diag.test"
  echo "$t:$line"
done

if command -v docker >/dev/null; then
  hr "containers by log driver ('db' writes each log line to a SQLite file)"
  docker ps --format '{{.Names}}' | sort | while read -r c; do
    printf '%-28s %s\n' "$c" "$(docker inspect -f '{{.HostConfig.LogConfig.Type}}' "$c")"
  done
  root=$(docker info -f '{{.DockerRootDir}}' 2>/dev/null)
  hr "files Docker changed in the last minute under $root/containers"
  find "$root/containers" -type f -mmin -1 -printf '%f\n' 2>/dev/null | sort | uniq -c | sort -rn | head -8
fi
