#!/usr/bin/env bash
# Tests for scripts/run-jobs.sh: when jobs run, what they record, what they
# ping. A fake clock (RUN_JOBS_NOW) and a fake curl make it deterministic;
# nothing touches the network or the real repo clone.
#
#   bash scripts/tests/run-jobs.test.sh
set -uo pipefail

ROOT=$(cd "$(dirname -- "$0")/../.." && pwd)
RUN="$ROOT/scripts/run-jobs.sh"
export TZ=Europe/Zurich

fails=0
pass() { printf '  ok    %s\n' "$1"; }
fail() { printf '  FAIL  %s\n' "$1"; fails=$((fails + 1)); }
check() { # <description> <command...>
	local d=$1
	shift
	if "$@"; then pass "$d"; else fail "$d"; fi
}

setup() {
	T=$(mktemp -d)
	mkdir -p "$T/jobs" "$T/bin" "$T/ops"
	# Fake curl: records each URL it was asked to ping.
	cat >"$T/bin/curl" <<-'EOF'
		#!/usr/bin/env bash
		for a in "$@"; do case $a in http*) echo "$a" >>"$CURL_LOG" ;; esac; done
		printf "OK (HTTP %s)" "${FAKE_HTTP:-200}"
		[ -t 0 ] || cat >/dev/null
	EOF
	chmod +x "$T/bin/curl"
	printf 'echo ran >>"%s/ran.ok"\n' "$T" >"$T/jobs/ok.sh"
	printf 'echo boom; exit 3\n' >"$T/jobs/fail.sh"
	printf 'sleep 5\n' >"$T/jobs/slow.sh"
	# shellcheck disable=SC2016 # $line belongs to the generated script
	printf 'read -r line && echo "stole: $line" >>"%s/stolen"\n' "$T" >"$T/jobs/reader.sh"
	cat >"$T/jobs.env" <<-EOF
		OPS_HOME=$T/ops
		HC_PING_KEY=testkey
		HC_PING_BASE=https://hc.test
	EOF
	export CURL_LOG="$T/curl.log" JOBS_DIR="$T/jobs" JOBS_CONF="$T/jobs.conf" RUN_JOBS_NO_PULL=1
	: >"$CURL_LOG"
}

at() { date -d "$1" +%s; } # local wall-clock time -> epoch
tick() { PATH="$T/bin:$PATH" RUN_JOBS_NOW=$(at "$1") bash "$RUN" "$T/jobs.env"; }
runs() { [ -f "$T/ran.ok" ] && wc -l <"$T/ran.ok" | tr -d ' ' || echo 0; }
pinged() { grep -q -- "$1" "$CURL_LOG"; }
not_pinged() { ! pinged "$1"; }
not_in_file() { ! grep -q -- "$1" "$2"; }
valid_json() { python3 -m json.tool "$1" >/dev/null 2>&1; }

echo "due-based scheduling"
setup
echo 'okjob 20:00 1m ok.sh' >"$T/jobs.conf"
tick "2026-10-11 19:45"
check "not run before its time" [ "$(runs)" = 0 ]
tick "2026-10-11 20:00"
check "runs at its time" [ "$(runs)" = 1 ]
tick "2026-10-11 20:15"
check "runs once a day" [ "$(runs)" = 1 ]
tick "2026-10-12 08:00"
check "next day: not before its time" [ "$(runs)" = 1 ]
tick "2026-10-12 23:30"
check "missed window catches up on the next tick" [ "$(runs)" = 2 ]
check "state records exit 0" grep -q '"exit": 0' "$T/ops/state/okjob.json"
check "state records the success day" grep -q '"last_ok_day": "2026-10-12"' "$T/ops/state/okjob.json"
check "pings start" pinged "https://hc.test/testkey/homelab-okjob/start?create=1"
check "pings success" pinged "https://hc.test/testkey/homelab-okjob/0?create=1"
check "pings heartbeat" pinged "https://hc.test/testkey/homelab-run-jobs/0?create=1"
check "writes a log per run" [ "$(find "$T/ops/logs/okjob" -name '*.log' | wc -l)" = 2 ]
rm -rf "$T"

echo "failures and retries"
setup
echo 'bad 06:00 1m fail.sh' >"$T/jobs.conf"
tick "2026-10-11 06:00"
check "failure recorded with its exit code" grep -q '"exit": 3' "$T/ops/state/bad.json"
check "failure message is the log's last line" grep -q '"message": "boom"' "$T/ops/state/bad.json"
check "pings the exit code" pinged "homelab-bad/3?create=1"
: >"$CURL_LOG"
tick "2026-10-11 06:30"
check "no retry within the hour" not_pinged "homelab-bad/start"
tick "2026-10-11 07:00"
check "retried after an hour" pinged "homelab-bad/start"
rm -rf "$T"

echo "tick jobs"
setup
echo 'every tick 1m ok.sh' >"$T/jobs.conf"
tick "2026-10-11 01:00"
tick "2026-10-11 01:15"
check "a tick job runs on every tick" [ "$(runs)" = 2 ]
echo 'bad tick 1m fail.sh' >"$T/jobs.conf"
: >"$CURL_LOG"
tick "2026-10-11 02:00"
tick "2026-10-11 02:15"
check "a failing tick job waits before retrying" [ "$(grep -c 'homelab-bad/start' "$CURL_LOG")" = 1 ]
tick "2026-10-11 03:00"
check "and retries after an hour" [ "$(grep -c 'homelab-bad/start' "$CURL_LOG")" = 2 ]
rm -rf "$T"

echo "build-images.sh"
B=$(mktemp -d)
mkdir -p "$B/bin" "$B/repo/scripts/lib" "$B/repo/stacks/app"
cp "$ROOT/scripts/build-images.sh" "$B/repo/scripts/"
cp "$ROOT/scripts/lib/code.sh" "$B/repo/scripts/lib/"
echo 'FROM scratch' >"$B/repo/stacks/app/Dockerfile"
git_q() { git -C "$B/repo" -c user.email=t@example.com -c user.name=t "$@" >/dev/null; }
git_q init -q && git_q add -A && git_q commit -qm one
# Fake docker: the label of the last build becomes the image's label.
# shellcheck disable=SC2016 # the $s belong to the generated script
printf '%s\n' '#!/usr/bin/env bash' \
	'case $1 in' \
	'image) [ -f "$FAKE/label" ] && cat "$FAKE/label" ;;' \
	'build) echo build >>"$FAKE/builds"' \
	'	for a in "$@"; do case $a in homelab.tree=*) printf %s "${a#homelab.tree=}" >"$FAKE/label" ;; esac; done ;;' \
	'esac' >"$B/bin/docker"
chmod +x "$B/bin/docker"
builds() { if [ -f "$B/builds" ]; then wc -l <"$B/builds" | tr -d ' '; else echo 0; fi; }
bi() { FAKE=$B PATH="$B/bin:$PATH" bash "$B/repo/scripts/build-images.sh" "$@" >/dev/null 2>&1; }
bi_fails() { ! bi "$@"; }
bi app=app:1
check "builds an image that has none" [ "$(builds)" = 1 ]
bi app=app:1
check "skips an image already built from this code" [ "$(builds)" = 1 ]
echo 'LABEL x=y' >>"$B/repo/stacks/app/Dockerfile" && git_q commit -qam two
bi app=app:1
check "rebuilds when the stack's code changed" [ "$(builds)" = 2 ]
echo '# app' >"$B/repo/stacks/app/README.md" && git_q add -A && git_q commit -qm docs
bi app=app:1
check "a README change alone does not rebuild" [ "$(builds)" = 2 ]
check "rejects a stack without a Dockerfile" bi_fails nope=nope:1
rm -rf "$B"

echo "lib/dump.sh"
P=$(mktemp -d)
# shellcheck source=scripts/lib/dump.sh
. "$ROOT/scripts/lib/dump.sh"
python3 -c "import zipfile,sys;z=zipfile.ZipFile(sys.argv[1],'w');z.writestr('a','x'*5000);z.close()" "$P/.good.partial"
head -c 300 "$P/.good.partial" >"$P/.cut.partial"
touch -d '20 days ago' "$P/app-dump-old.zip"
check "a complete zip is published under its final name" publish_dump "$P/.good.partial" "$P" app-dump-new.zip 'app-dump-*.zip' 14
check "it is readable by its owner only" [ "$(stat -c %a "$P/app-dump-new.zip")" = 600 ]
check "dumps past the retention are pruned" [ ! -e "$P/app-dump-old.zip" ]
publish_dump_fails() { ! publish_dump "$@" 2>/dev/null; }
check "a truncated zip is refused" publish_dump_fails "$P/.cut.partial" "$P" app-dump-cut.zip 'app-dump-*.zip' 14
check "and never gets a final name" [ ! -e "$P/app-dump-cut.zip" ]
rm -rf "$P"

echo "timeouts and isolation"
setup
printf 'slow 01:00 1s slow.sh\nokjob 01:00 1m ok.sh\n' >"$T/jobs.conf"
tick "2026-10-11 01:00"
check "a hung job is killed (exit 124)" grep -q '"exit": 124' "$T/ops/state/slow.json"
check "a hung job does not block the next one" [ "$(runs)" = 1 ]
rm -rf "$T"

setup
printf 'reader 01:00 1m reader.sh\nokjob 01:00 1m ok.sh\n' >"$T/jobs.conf"
tick "2026-10-11 01:00"
check "a job cannot read jobs.conf from stdin" [ ! -s "$T/stolen" ]
check "the line after it still runs" [ "$(runs)" = 1 ]
rm -rf "$T"

setup
printf '# comment\n\nbroken 25h 1m ok.sh\nokjob 01:00 1m ok.sh\n' >"$T/jobs.conf"
tick "2026-10-11 01:00" 2>"$T/err"
check "a malformed line is reported and skipped" grep -q "bad line" "$T/err"
check "the valid line still runs" [ "$(runs)" = 1 ]
rm -rf "$T"

echo "locking and no key"
setup
echo 'okjob 01:00 1m ok.sh' >"$T/jobs.conf"
mkdir -p "$T/ops"
exec 8>"$T/ops/run-jobs.lock"
flock 8
tick "2026-10-11 01:00"
check "a second instance exits while the first runs" [ "$(runs)" = 0 ]
exec 8>&-
sed -i '/HC_PING_KEY/d' "$T/jobs.env"
: >"$CURL_LOG"
tick "2026-10-11 01:00"
check "runs without a ping key" [ "$(runs)" = 1 ]
check "and pings nothing" [ ! -s "$CURL_LOG" ]
rm -rf "$T"

echo "ping errors"
setup
echo 'okjob 01:00 1m ok.sh' >"$T/jobs.conf"
FAKE_HTTP=400 tick "2026-10-11 01:00" 2>"$T/err"
check "a rejected ping names the check" grep -q "homelab-okjob/0 failed" "$T/err"
check "and never prints the key" not_in_file testkey "$T/err"
check "the job still ran" [ "$(runs)" = 1 ]
sed -i 's|HC_PING_KEY=testkey|HC_PING_KEY=https://hc-ping.com/abc|' "$T/jobs.env"
tick "2026-10-11 02:00" 2>"$T/err"
check "a URL pasted as the key is flagged" grep -q "must be the project's ping key" "$T/err"
sed -i 's|HC_PING_KEY=.*|HC_PING_KEY=0f8fad5b-d9cb-469f-a165-70867728950e|' "$T/jobs.env"
tick "2026-10-11 03:00" 2>"$T/err"
check "a check UUID pasted as the key is recognised" grep -q "is a check's UUID" "$T/err"
rm -rf "$T"

echo "status.json integration"
setup
echo 'okjob 01:00 1m ok.sh' >"$T/jobs.conf"
mkdir -p "$T/dash"
cp "$ROOT/scripts/dashboard-status.sh" "$T/jobs/"
mkdir -p "$T/jobs/lib" && cp "$ROOT/scripts/lib/"*.sh "$T/jobs/lib/"
printf 'DASHBOARD_LOCAL_DIR=%s\nSTATUS_VOLUME=/\nHYPERBACKUP_LOG=/nonexistent\n' "$T/dash" >>"$T/jobs.env"
tick "2026-10-11 01:00"
check "status.json is valid JSON" valid_json "$T/dash/status.json"
check "status.json lists the job" grep -q '"name": "okjob"' "$T/dash/status.json"
check "status.json carries the scheduler state" grep -q '"pull_error": "pull disabled"' "$T/dash/status.json"
rm -rf "$T"

echo "paperless-export"
P=$(mktemp -d)
mkdir -p "$P/bin"
# Fake docker: two containers, only one of them Paperless. The
# exporter writes a manifest unless NO_MANIFEST is set.
cat >"$P/bin/docker" <<'EOF'
#!/usr/bin/env bash
case "$1 $3" in
"ps "*) printf 'webserver nginx:1\npaperless ghcr.io/paperless-ngx/paperless-ngx:3.2.1\n' ;;
"exec /bin/sh") echo "$*" >>"$P_DIR/calls"; [ -n "${NO_MANIFEST:-}" ] || echo "[]" >"$P_DIR/manifest" ;;
"exec test") [ -s "$P_DIR/manifest" ] ;;
"exec sh") echo 3 ;;
esac
EOF
chmod +x "$P/bin/docker"
pe() { P_DIR=$P PATH="$P/bin:$PATH" bash "$ROOT/scripts/paperless-export.sh" >"$P/out" 2>&1; }
pe_fails() { ! pe; }
check "exports, deleting what is gone" pe
check "in the Paperless container, found by its image" grep -qx 'exec paperless /bin/sh -c ./manage.py document_exporter -d /usr/src/paperless/export' "$P/calls"
rm "$P/manifest"
export NO_MANIFEST=1
check "an export without a manifest fails" pe_fails
unset NO_MANIFEST
check "and says why" grep -q "has no manifest.json" "$P/out"
rm -rf "$P"

echo "Portainer stacks (lib/stacks.sh)"
K=$(mktemp -d)
mkdir -p "$K/bin" "$K/repo/stacks/app" "$K/repo/stacks/web"
gk() { git -C "$K/repo" -c user.email=t@example.com -c user.name=t "$@" >/dev/null; }
gk init -q
echo 1 >"$K/repo/stacks/app/compose.yaml"
echo 1 >"$K/repo/stacks/web/compose.yaml"
gk add -A && gk commit -qm one
first=$(git -C "$K/repo" rev-parse HEAD)
echo 2 >"$K/repo/stacks/web/compose.yaml"
gk commit -qam two
two=$(git -C "$K/repo" rev-parse --short=7 HEAD)
echo '# docs' >"$K/repo/stacks/app/README.md"
gk add -A && gk commit -qm docs
# Fake Portainer: app and web deployed at the first commit, a Web editor
# stack, and one deployed from a commit this clone does not have.
cat >"$K/stacks.json" <<EOF
[{"Id": 3, "Name": "app", "Status": 1, "UpdateDate": 1791700000,
  "Env": [{"name": "DB_PASSWORD", "value": "hunter2"}],
  "GitConfig": {"ConfigFilePath": "stacks/app/compose.yaml", "ConfigHash": "$first"}},
 {"Id": 4, "Name": "web", "Status": 1, "CreationDate": 1791600000,
  "GitConfig": {"ConfigFilePath": "stacks/web/compose.yaml", "ConfigHash": "$first"}},
 {"Id": 5, "Name": "forgejo", "Status": 2, "GitConfig": null},
 {"Id": 6, "Name": "lost", "Status": 1,
  "GitConfig": {"ConfigFilePath": "stacks/app/compose.yaml", "ConfigHash": "0123456789abcdef0123456789abcdef01234567"}}]
EOF
cat >"$K/bin/curl" <<'EOF'
#!/usr/bin/env bash
echo "$*" >>"$K_DIR/curl.args"
cat >>"$K_DIR/curl.stdin"
[ -z "${CURL_FAIL:-}" ] || exit 22
cat "$K_DIR/stacks.json"
EOF
chmod +x "$K/bin/curl"
stacks() {
	K_DIR=$K PATH="$K/bin:$PATH" PORTAINER_TOKEN=${1-ptr_secret} bash -c \
		". '$ROOT/scripts/lib/json.sh'; . '$ROOT/scripts/lib/code.sh'; . '$ROOT/scripts/lib/stacks.sh'; stacks_json '$K/repo'"
}
stacks >"$K/out.json"
sj() { python3 -c "import json,sys; d={s['name']: s for s in json.load(open('$K/out.json'))}; sys.exit(0 if ($1) else 1)"; }
check "stacks are valid JSON" valid_json "$K/out.json"
check "a stack deployed at its folder's last commit is current" sj "d['app']['current'] is True and d['app']['deployed']['at'] == 1791700000"
check "a stack whose folder changed since needs a redeploy" sj "d['web']['current'] is False and d['web']['code']['commit'] == '$two'"
check "a README change alone does not need a redeploy" sj "d['app']['current'] is True"
check "the creation date stands in for a never-updated stack" sj "d['web']['deployed']['at'] == 1791600000"
check "a Web editor stack is not from git" sj "d['forgejo']['git'] is False and d['forgejo']['current'] is None and not d['forgejo']['active']"
check "a commit the clone lacks is unknown, not current" sj "d['lost']['current'] is None"
check "stack environments are not copied" not_in_file hunter2 "$K/out.json"
check "the token goes in on stdin" grep -q 'X-API-Key: ptr_secret' "$K/curl.stdin"
check "never on the command line" not_in_file ptr_secret "$K/curl.args"
check "only the local Portainer skips certificate checks" grep -q -- '-k ' "$K/curl.args"
check "no token: no stack list" [ "$(stacks '')" = null ]
check "Portainer unreachable: no stack list" [ "$(CURL_FAIL=1 stacks)" = null ]
rm -rf "$K"

echo "docker-prune"
D=$(mktemp -d)
mkdir -p "$D/bin"
printf '#!/usr/bin/env bash\necho "$*" >>"%s/calls"\necho "Deleted Images:"; echo "Total reclaimed space: 1.2GB"\n' "$D" >"$D/bin/docker"
chmod +x "$D/bin/docker"
PATH="$D/bin:$PATH" bash "$ROOT/scripts/docker-prune.sh" >"$D/out"
check "removes only unused images older than a week" grep -qx 'image prune -af --filter until=168h' "$D/calls"
check "and old build cache" grep -qx 'builder prune -af --filter until=168h' "$D/calls"
check "reports what was freed" grep -q 'Total reclaimed space' "$D/out"
rm -rf "$D"

echo "storage-scan"
Q=$(mktemp -d)
mkdir -p "$Q/bin" "$Q/vol/media/media/tv/Big Show/Season 01" "$Q/vol/media/media/tv/Small Show" \
	"$Q/vol/media/media/movies/Old Film (1999)" "$Q/vol/homes/#recycle" "$Q/ops" "$Q/dash"
# Sparse files: real sizes for find, no disk used.
truncate -s 3G "$Q/vol/media/media/tv/Big Show/Season 01/Big.Show.S01E01.1080p.x264.mkv"
truncate -s 6G "$Q/vol/media/media/tv/Big Show/Season 01/Big.Show.S01E02E03.1080p.x264.mkv"
truncate -s 1G "$Q/vol/media/media/tv/Small Show/Small.Show.S01E01.x265.mkv"
truncate -s 30G "$Q/vol/media/media/movies/Old Film (1999)/Old.Film.1999.1080p.REMUX.mkv"
truncate -s 5M "$Q/vol/homes/#recycle/old.txt"
# Fake btrfs: shares, Drive history, the backup cache, one snapshot.
# EXCL=1 makes btrfs report exclusive sizes.
cat >"$Q/bin/btrfs" <<'EOF'
#!/usr/bin/env bash
case "$1 $2" in
"filesystem du") printf '     Total   Exclusive  Set shared  Filename\n%s %s %s %s\n' 207000000000 "${DRIVE_OWN:-4980000000}" 202000000000 "$4" ;;
"subvolume list") printf 'ID 257 gen 9 top level 5 path media\nID 258 gen 9 top level 5 path homes\nID 259 gen 9 top level 5 path @synologydrive\nID 260 gen 9 top level 5 path @img_bkp_cache\nID 261 gen 9 top level 5 path @sharesnap/homes/GMT+02-2026.10.01-01.05.03\nID 262 gen 9 top level 5 path @syno\n' ;;
"qgroup show")
	x=0; [ -n "${EXCL:-}" ] && x=1
	printf 'qgroupid rfer excl\n-------- ---- ----\n'
	printf '0/257 %s %s\n0/258 %s %s\n0/259 %s %s\n0/260 61000000000 %s\n0/261 300000000000 %s\n0/262 8000000000 0\n' \
		"${MEDIA_B:-2000000000000}" $((x * 1900000000000)) "${HOMES_B:-400000000000}" $((x * 390000000000)) \
		"${DRIVE_B:-500000000000}" $((x * 240000000000)) $((x * 60000000000)) $((x * 12000000000)) ;;
esac
EOF
# Fake docker: a Jellyfin container whose ffprobe says x265 = hevc, else h264;
# episodes last 45 minutes, films 2 hours. Every probed file is logged.
cat >"$Q/bin/docker" <<'EOF'
#!/usr/bin/env bash
case $1 in
ps) [ -n "${NO_JELLYFIN:-}" ] || echo "mediarr-jellyfin-1 lscr.io/linuxserver/jellyfin:10.10.7"; echo "mediarr-jellyseerr-1 fallenbagel/jellyseerr:2.7.3" ;;
system) printf 'Images\t30.01GB\t19.34GB (64%%)\nContainers\t431MB\t0B (0%%)\nLocal Volumes\t2.008MB\t1.988MB (99%%)\nBuild Cache\t5.056GB\t1.648GB\n' ;;
exec)
	while IFS=$'\t' read -r f s m; do
		echo "$f" >>"$Q_DIR/probed"
		c=h264; case $f in *x265*) c=hevc ;; esac
		d=2700; case $f in movies/*) d=7200 ;; esac
		printf '%s\t%s\t%s\t%s\t%s\t\n' "$f" "$s" "$m" "$c" "$d"
	done ;;
esac
EOF
chmod +x "$Q/bin/btrfs" "$Q/bin/docker"
scan() { # <date> [VAR=value...]
	local d=$1; shift
	env "$@" Q_DIR="$Q" PATH="$Q/bin:$PATH" STATUS_VOLUME="$Q/vol" DASHBOARD_LOCAL_DIR="$Q/dash" OPS_HOME="$Q/ops" \
		STORAGE_MEDIA_DIR="$Q/vol/media/media" STORAGE_TODAY="$d" bash "$ROOT/scripts/storage-scan.sh" >"$Q/out" 2>&1
}
qj() { python3 -c "import json,sys; d=json.load(open('$Q/dash/storage.json')); sys.exit(0 if ($1) else 1)"; }

scan 2026-09-01
check "first run writes valid JSON" valid_json "$Q/dash/storage.json"
check "every file is probed once" [ "$(wc -l <"$Q/probed")" = 4 ]
check "shared folders come from btrfs, without @syno" qj "sorted(p['name'] for p in d['parts'] if p['kind'] == 'share') == ['homes', 'media']"
check "Drive history and the backup cache are parts" qj "{'drive', 'hbcache', 'docker'} <= {p['kind'] for p in d['parts']}"
check "Drive's own space is measured when the counters lack it" qj "[p['bytes'] for p in d['parts'] if p['kind'] == 'drive'] == [4980000000] and d['reclaim']['drive'] == 4980000000"
check "without exclusive sizes, overlap is reported, not hidden" qj "not d['exclusive_known'] and d['counted_twice'] >= 0 and d['reclaim']['snapshots'] is None"
check "docker total and reclaimable from docker system df" qj "[p['bytes'] for p in d['parts'] if p['kind'] == 'docker'] == [35499008000] and d['reclaim']['docker'] == 20988000000"
check "recycle bins are measured" qj "d['reclaim']['recycle'] > 0"
check "a double episode counts twice" qj "[s['episodes'] for s in d['media']['series']['top'] if s['name'] == 'Big Show'] == [3]"
check "series sorted by size" qj "[s['name'] for s in d['media']['series']['top']] == ['Big Show', 'Small Show']"
check "an HEVC series has nothing to gain" qj "[(s['hevc'], s['saving']) for s in d['media']['series']['top'] if s['name'] == 'Small Show'] == [(True, 0)]"
check "an H.264 series above 6 Mbit/s would shrink" qj "[s['saving'] > 4e9 for s in d['media']['series']['top'] if s['name'] == 'Big Show'] == [True]"
check "GB per hour from size and length" qj "[s['gb_per_hour'] for s in d['media']['series']['top'] if s['name'] == 'Big Show'] == [round(9 * 2**30 / 1e9 / 1.5, 2)]"
check "a REMUX film is flagged" qj "d['media']['film']['top'][0]['remux'] is True"
check "no growth on the first day" qj "d['growth'] is None and d['changes'] == []"

truncate -s 3G "$Q/vol/media/media/tv/Big Show/Season 01/Big.Show.S01E04.1080p.x264.mkv"
mkdir -p "$Q/vol/media/media/movies/New Film (2026)"
truncate -s 8G "$Q/vol/media/media/movies/New Film (2026)/New.Film.2026.1080p.x265.mkv"
: >"$Q/probed"
scan 2026-10-01 MEDIA_B=2011000000000 HOMES_B=380000000000 DRIVE_B=230000000000 DRIVE_OWN=3000000000
scan 2026-10-01 MEDIA_B=2011000000000 HOMES_B=380000000000 DRIVE_B=230000000000 DRIVE_OWN=3000000000
check "only new files are probed" [ "$(sort -u "$Q/probed" | wc -l)" = 2 ]
check "a rerun the same day replaces that day" [ "$(grep -c $'^2026-10-01\tused' "$Q/ops/storage/history.tsv")" = 1 ]
check "growth over the period" qj "d['growth']['since'] == '2026-09-01' and d['growth']['per_day'] is not None"
check "new episodes are counted in the changes" qj "[c['new'] for c in d['changes'] if c['name'] == 'Big Show'] == [1]"
check "new films are counted together" qj "[c['new'] for c in d['changes'] if c['kind'] == 'films'] == [1]"
check "Drive history shrinking is a decrease" qj "[c['delta'] < 0 for c in d['changes'] if c['kind'] == 'drive'] == [True]"
check "the media share itself is not in the changes" qj "'media' not in [c['name'] for c in d['changes']]"
check "shared folders get 7 and 30 day deltas" qj "[s['d30'] for s in d['shares'] if s['name'] == 'homes'] == [-20000000000]"
check "twelve weekly points, the last one today" qj "all(len(s['weekly']) == 12 and s['weekly'][-1] == s['bytes'] for s in d['shares'])"

scan 2026-10-02 EXCL=1
check "with exclusive sizes, Drive and snapshots count only their own space" qj "d['exclusive_known'] and [p['bytes'] for p in d['parts'] if p['kind'] == 'drive'] == [240000000000] and d['reclaim']['snapshots'] == 12000000000"
truncate -s 2G "$Q/vol/media/media/tv/Small Show/Small.Show.S01E02.x265.mkv"
scan 2026-10-03 NO_JELLYFIN=1
check "without Jellyfin the page still gets its data" valid_json "$Q/dash/storage.json"
check "and the unprobed files are reported" grep -q "left unprobed" "$Q/out"
rm -rf "$Q"

echo "uploading devices (lib/devices.sh)"
U=$(mktemp -d)
NOW=$(date -d '2026-10-14 12:00' +%s)
H="$U/vol/homes/@LH-EXAMPLE.LAN/61"
A="$H/anna-1000102/Photos/MobileBackup"
B="$H/bob-1000101/Photos/MobileBackup"
mkdir -p "$U/bin" "$U/state" "$A/Pixel 7/DCIM/Camera/2026/10/@eaDir" "$A/Old Phone/DCIM/Camera/2021/01" "$B/Pixel 9a/DCIM/Camera/2026/10"
at_file() { : >"$2"; touch -d "@$(date -d "$1" +%s)" "$2"; }
at_file '2026-10-13 18:00' "$A/Pixel 7/DCIM/Camera/2026/10/PXL_1.jpg"
at_file '2026-10-14 11:00' "$A/Pixel 7/DCIM/Camera/2026/10/@eaDir/PXL_1.jpg" # thumbnail: ignored
at_file '2026-10-14 11:30' "$B/Pixel 9a/DCIM/Camera/2026/10/PXL_2.mp4"
at_file '2026-06-01 10:00' "$A/Old Phone/DCIM/Camera/2021/01/IMG_3.jpg"
: >"$A/Old Phone/DCIM/Camera/2021/01/gone.jpg"
rm "$A/Old Phone/DCIM/Camera/2021/01/gone.jpg" # a deletion today must not make it look active
# Fake synowebapi, shaped like DSM's: Bob's laptop and phone connected.
cat >"$U/bin/synowebapi" <<'EOF'
#!/usr/bin/env bash
[ -n "${NOBODY:-}" ] && { printf '{\n   "data" : {\n      "items" : []\n   },\n   "success" : true\n}\n'; exit 0; }
cat <<'OUT'
{
   "data" : {
      "items" : [
         {
            "client_id" : "laptop",
            "client_name" : "bob@example.lan",
            "client_status" : "on_line",
            "client_type" : "drive",
            "client_version" : "4.0.3-17892",
            "last_auth_time" : 1791954554
         },
         {
            "client_id" : "Pixel 9a",
            "client_name" : "bob@example.lan",
            "client_status" : "on_line",
            "client_type" : "drive_mobile",
            "client_version" : "3.8.1-1066",
            "last_auth_time" : 1791954802
         }
      ]
   },
   "success" : true
}
OUT
EOF
chmod +x "$U/bin/synowebapi"
dev() { # [VAR=value...]
	env "$@" PATH="$U/bin:$PATH" DEVICES_NOW="$NOW" JOBS_STATE_DIR="$U/state" bash -c \
		". '$ROOT/scripts/lib/json.sh'; . '$ROOT/scripts/lib/devices.sh'; devices_json '$U/vol'" >"$U/out.json"
}
dj() { python3 -c "import json,sys; d=json.load(open('$U/out.json')); L={x['name']: x for x in d['devices']}; N={x['name']: x for x in d['unlisted']}; sys.exit(0 if ($1) else 1)"; }

dev UPLOADERS_CONF="$U/none.conf"
check "devices: valid JSON without a config" valid_json "$U/out.json"
check "without a config, active devices are suggestions" dj "not d['configured'] and set(N) == {'laptop', 'Pixel 9a', 'Pixel 7'}"
check "a phone silent for months is not suggested" dj "'Old Phone' not in N"
check "owners come from the Drive login and the home folder" dj "N['laptop']['owner'] == 'bob' and N['Pixel 7']['owner'] == 'anna'"
check "thumbnails and deletions don't count as new photos" dj "N['Pixel 7']['newest_photo'] == $(date -d '2026-10-13 18:00' +%s)"

printf '%s\n' '# owner | kind | device | max days' 'bob | computer | laptop | 21' 'bob | phone | Pixel 9a | 7' \
	'anna | phone | Pixel 7' "anna | phone | Old Phone | 7 | $H/anna-1000102/Photos/MobileBackup/Old Phone/DCIM" >"$U/uploaders.conf"
dev UPLOADERS_CONF="$U/uploaders.conf"
check "configured devices are listed, in file order" dj "d['configured'] and [x['name'] for x in d['devices']] == ['laptop', 'Pixel 9a', 'Pixel 7', 'Old Phone']"
check "max days per device, 7 by default" dj "[x['max_days'] for x in d['devices']] == [21, 7, 7, 7]"
check "a connected device is online now" dj "L['laptop']['online'] and L['laptop']['last_seen'] == $NOW"
check "a phone gets its newest photo" dj "L['Pixel 9a']['newest_photo'] == $(date -d '2026-10-14 11:30' +%s)"
check "a configured photo folder is used" dj "L['Old Phone']['newest_photo'] == $(date -d '2026-06-01 10:00' +%s)"
check "nothing is unlisted when all are configured" dj "d['unlisted'] == []"

LATER=$((NOW + 3600))
env NOBODY=1 PATH="$U/bin:$PATH" DEVICES_NOW="$LATER" JOBS_STATE_DIR="$U/state" UPLOADERS_CONF="$U/uploaders.conf" bash -c \
	". '$ROOT/scripts/lib/json.sh'; . '$ROOT/scripts/lib/devices.sh'; devices_json '$U/vol'" >"$U/out.json"
check "a device not connected keeps its last sighting" dj "L['laptop']['last_seen'] == $NOW and not L['laptop']['online']"

printf '%s\n' 'bob | computer | laptop | 21' >"$U/uploaders.conf"
dev UPLOADERS_CONF="$U/uploaders.conf"
check "a device uploading but not in the file is reported" dj "set(N) == {'Pixel 9a', 'Pixel 7'}"
env PATH="$U/bin:$PATH" DEVICES_NOW="$NOW" bash -c ". '$ROOT/scripts/lib/json.sh'; . '$ROOT/scripts/lib/devices.sh'; devices_suggest '$U/vol'" >"$U/suggest.txt"
check "suggested lines are ready to paste" grep -qx 'bob | phone | Pixel 9a | 7' "$U/suggest.txt"
rm -rf "$U"

echo "backup discovery (lib/protection.sh)"
setup
# Fake DSM: two shares plus a USB disk, three scheduled tasks, two Hyper
# Backup tasks, two snapshots of one share. Shapes copied from DSM 7.3.
V="$T/volume2"
mkdir -p "$V/@sharesnap/paperless/GMT+02-2026.10.11-02.00.04" "$V/@sharesnap/paperless/GMT+02-2026.10.12-02.00.03"
cat >"$T/bin/synoshare" <<EOF
#!/usr/bin/env bash
case \$1 in
--enum) printf 'Share Enum Arguments: [0x3FF0F]  ALL\n3 Listed:\narchive\npaperless\nusbshare1\n' ;;
--get) printf 'SYNOSHARE data dump:\n\t Name .......[%s]\n\t Path .......[$V/%s]\n' "\$2" "\$2" ;;
esac
EOF
cat >"$T/bin/synoschedtask" <<'EOF'
#!/usr/bin/env bash
cat <<'OUT'
               ID: [5]
             Name: [Share [archive] Snapshot]
            State: [disabled]
              App: [SYNO.SDS.Share.Snapshot]
             Type: [weekly]
     Days of week: [Mon,Tue,Wed,Thr,Fri,Sat]
         Run time: [2]:[0]
          CmdArgv: ['/usr/syno/bin/synosnapschedtask.sh' 'local' 'share' 'archive']

               ID: [8]
             Name: [Share [paperless] Snapshot]
            State: [enabled]
              App: [SYNO.SDS.Share.Snapshot]
             Type: [daily]
         Run time: [2]:[0]
          CmdArgv: ['/usr/syno/bin/synosnapschedtask.sh' 'local' 'share' 'paperless']

               ID: [6]
             Name: [Local Copy]
            State: [enabled]
              App: [SYNO.SDS.Backup.Application]
             Type: [daily]
         Run time: [3]:[0]
Repeat every [15] min (s) until [23]:[45]
          CmdArgv: ['/var/packages/HyperBackup/target/bin/dsmbackup' '--backup' '1']
OUT
EOF
chmod +x "$T/bin/synoshare" "$T/bin/synoschedtask"
cat >"$T/synobackup.conf" <<'EOF'
[global]
task_id_max=2
[repo_1]
name="Local Archiving Storage"
remote_share="usbshare1"
target_type="share"
trans_type="local"
[task_1]
backup_folders=["/paperless","/homes/someone/Photos"]
name="Local Copy"
repo_id=1
sched_id=6
[repo_2]
remote_bucket="bucket"
remote_secret="s3cr3t"
remote_url="s3.example.com"
trans_type="aws_s3"
[task_2]
backup_folders=["/archive","/paperless"]
enable_data_encrypt=true
name="Off-site"
repo_id=2
sched_id=7
EOF
prot() { # <PATH> <conf>
	PATH="$1" HYPERBACKUP_CONF="$2" bash -c ". '$ROOT/scripts/lib/json.sh'; . '$ROOT/scripts/lib/protection.sh'; protection_json"
}
prot "$T/bin:$PATH" "$T/synobackup.conf" >"$T/prot.json"
pj() { python3 -c "import json,sys; d=json.load(open('$T/prot.json')); sys.exit(0 if ($1) else 1)"; }
check "protection is valid JSON" valid_json "$T/prot.json"
check "USB disks are not listed as data" pj "[s['name'] for s in d['shares']] == ['archive', 'paperless']"
check "a share's path comes from synoshare" pj "d['shares'][1]['path'] == '$V/paperless'"
check "daily snapshot schedule" pj "d['shares'][1]['snapshot']['schedule'] == 'daily 02:00' and d['shares'][1]['snapshot']['enabled']"
check "newest snapshot and count from @sharesnap" pj "d['shares'][1]['snapshot']['latest'] == $(date -d '2026-10-12 02:00:03 +0200' +%s) and d['shares'][1]['snapshot']['count'] == 2"
check "a disabled weekly snapshot keeps its days" pj "d['shares'][0]['snapshot'] == {'enabled': False, 'schedule': 'Mon,Tue,Wed,Thr,Fri,Sat 02:00', 'latest': None, 'count': None}"
check "a USB task" pj "d['tasks'][0]['dest'] == 'usb' and d['tasks'][0]['target'] == 'usbshare1' and not d['tasks'][0]['encrypted']"
check "its schedule comes from its sched_id" pj "d['tasks'][0]['schedule'] == 'daily 03:00' and d['tasks'][0]['enabled']"
check "its folders pass through" pj "d['tasks'][0]['folders'] == ['/paperless', '/homes/someone/Photos']"
check "an encrypted S3 task" pj "d['tasks'][1]['dest'] == 's3' and d['tasks'][1]['target'] == 's3.example.com' and d['tasks'][1]['encrypted']"
check "a task without a schedule is not enabled" pj "d['tasks'][1]['schedule'] is None and not d['tasks'][1]['enabled']"
check "no secret leaks" not_in_file s3cr3t "$T/prot.json"
prot /usr/bin:/bin /nonexistent >"$T/none.json"
check "off DSM: empty, still valid" grep -qx '{"shares": \[\], "tasks": \[\]}' "$T/none.json"
rm -rf "$T"

echo
if [ "$fails" -gt 0 ]; then
	echo "$fails test(s) failed"
	exit 1
fi
echo "all tests passed"
