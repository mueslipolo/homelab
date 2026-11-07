#!/usr/bin/env bash
# immich-import: puts existing photos into Immich, into one person's account,
# with immich-go run in a container on the `proxy` network (the NAS cannot
# reach Caddy's macvlan address, so it talks to Immich directly). Run by hand
# on the NAS, not a scheduled job. Guide 12, "Import existing photos".
set -euo pipefail

readonly IMMICH_GO_VERSION=0.32.0
readonly IMAGE=alpine:3.20

usage() {
	cat <<'EOF'
Usage: sudo immich-import.sh <person> <source>... [options] [-- immich-go options]

Puts the photos of <source> into <person>'s Immich account. A dry run unless
--apply: it lists what would be uploaded, skipped and why, and sends nothing.

  <person>     whose account: the API key is read from token.<person>
  <source>     a folder (read-only), or with --takeout, Google Takeout zips
               or their unpacked folder

Options:
  --apply        upload for real (default: dry run)
  --takeout      the sources are a Google Takeout export: its albums, dates,
                 descriptions and people come along
  --tag TAG      tag every photo (default: import/<person>/<today>)
  --admin LOGIN  an Immich admin whose key (token.<LOGIN>) pauses Immich's
                 jobs during the upload: needed when <person> is not an admin
                 (else add -- --pause-immich-jobs=false)
  -h, --help     this help

Settings (environment):
  IMMICH_IMPORT_DIR  the folder with token.<person> files, immich-go and the
                     logs (default: ~/immich-go of the user who ran sudo)
  IMMICH_SERVER      default http://immich-server:2283
  IMMICH_NETWORK     default proxy

Examples:
  sudo immich-import.sh yves /volume1/homes/yves/Photos
  sudo immich-import.sh yves /volume1/homes/yves/Photos --apply
  sudo immich-import.sh bob /volume1/homes/bob/Takeout/takeout-*.zip --takeout
  sudo immich-import.sh bob /volume1/homes/bob/Photos --admin alice --apply

Running it again sends only what Immich does not have yet for that person.
A file that fails (empty, corrupt) is reported and the rest goes on.
Exit codes: 0 done, 1 failed (see the log), 2 usage.
EOF
}

die() { printf 'immich-import: %s\n' "$1" >&2; exit "${2:-1}"; }
say() { printf '%s\n' "$*" >&2; }

# ---------------------------------------------------------------- arguments
person="" apply=0 takeout=0 tag="" admin="" sources=() extra=()
while [ $# -gt 0 ]; do
	case $1 in
	-h | --help) usage; exit 0 ;;
	--apply) apply=1 ;;
	--takeout) takeout=1 ;;
	--tag) [ $# -ge 2 ] || die "--tag needs a value" 2; tag=$2; shift ;;
	--tag=*) tag=${1#--tag=} ;;
	--admin) [ $# -ge 2 ] || die "--admin needs a login" 2; admin=$2; shift ;;
	--admin=*) admin=${1#--admin=} ;;
	--) shift; extra=("$@"); break ;;
	-*) die "unknown option $1 (see --help; immich-go's own options go after --)" 2 ;;
	*) if [ -z "$person" ]; then person=$1; else sources+=("$1"); fi ;;
	esac
	shift
done
if [ -z "$person" ] || [ ${#sources[@]} -eq 0 ]; then usage >&2; exit 2; fi
[[ $person =~ ^[a-z0-9._-]+$ ]] || die "person '$person': use the login, e.g. yves" 2
[ -z "$admin" ] || [[ $admin =~ ^[a-z0-9._-]+$ ]] || die "--admin '$admin': use the login, e.g. yves" 2
[ "$(id -u)" -eq 0 ] || die "run it with sudo: the photos are readable by their owners only" 2
command -v docker >/dev/null || die "docker not found"

owner=${SUDO_USER:-$(id -un)}
[[ $owner =~ ^[A-Za-z0-9._-]+$ ]] || die "unexpected user name '$owner': set IMMICH_IMPORT_DIR" 2
# DSM has no getent: bash's own ~user lookup (the system's, LDAP included).
home=$(eval "printf '%s' ~$owner")
[ -d "$home" ] || [ -n "${IMMICH_IMPORT_DIR:-}" ] || die "no home folder for $owner: set IMMICH_IMPORT_DIR" 2
dir=${IMMICH_IMPORT_DIR:-$home/immich-go}
server=${IMMICH_SERVER:-http://immich-server:2283}
network=${IMMICH_NETWORK:-proxy}
tag=${tag:-import/$person/$(date +%F)}
token=$dir/token.$person

[ -r "$token" ] || die "no $token: create an API key in $person's Immich account (Account settings > API Keys, permission all) and put it there (chmod 600)"
key=$(tr -d '[:space:]' <"$token")
[ -n "$key" ] || die "$token is empty"
admin_key=""
if [ -n "$admin" ]; then
	[ -r "$dir/token.$admin" ] || die "no $dir/token.$admin: the admin's API key (permission all) for --admin $admin"
	admin_key=$(tr -d '[:space:]' <"$dir/token.$admin")
	[ -n "$admin_key" ] || die "$dir/token.$admin is empty"
fi

for s in "${sources[@]}"; do
	[ -e "$s" ] || die "$s: no such file or folder" 2
	if [ "$takeout" -eq 0 ] && [ ! -d "$s" ]; then die "$s is not a folder (a Takeout zip needs --takeout)" 2; fi
done

# ---------------------------------------------------------------- immich-go
mkdir -p "$dir/logs"
if [ ! -x "$dir/immich-go" ] || ! "$dir/immich-go" version 2>/dev/null | grep -q "version:$IMMICH_GO_VERSION,"; then
	say "downloading immich-go $IMMICH_GO_VERSION into $dir"
	tmp=$(mktemp -d)
	curl -fsSL "https://github.com/simulot/immich-go/releases/download/v$IMMICH_GO_VERSION/immich-go_Linux_x86_64.tar.gz" |
		tar -xz -C "$tmp" immich-go
	install -m 755 "$tmp/immich-go" "$dir/immich-go"
	rm -rf "$tmp"
fi

# The key goes in a config file only root can read, never on a command line
# (ps, docker inspect). Removed on exit.
work=$(mktemp -d)
trap 'rm -rf "$work"' EXIT
umask 077
printf 'upload:\n    server: %s\n    api-key: %s\n' "$server" "$key" >"$work/immich-go.yaml"
# Pausing Immich's jobs during the upload takes an admin's key; the photos
# still go to <person>'s account.
[ -z "$admin_key" ] || printf '    admin-api-key: %s\n' "$admin_key" >>"$work/immich-go.yaml"

# Each source mounted read-only under /import/<n>/<its name>.
mounts=() args=() n=0
for s in "${sources[@]}"; do
	n=$((n + 1))
	abs=$(realpath -- "$s")
	mounts+=(-v "$abs:/import/$n/$(basename -- "$abs"):ro")
	args+=("/import/$n/$(basename -- "$abs")")
done

stamp=$(date +%Y%m%d-%H%M%S)
mode=$([ "$takeout" -eq 1 ] && echo takeout || echo folder)
log=$dir/logs/$stamp-$person-$mode$([ "$apply" -eq 1 ] || echo -dry-run).log
cmd=(upload "$([ "$takeout" -eq 1 ] && echo from-google-photos || echo from-folder)"
	--config /work/immich-go.yaml --no-ui --log-file "/logs/$(basename -- "$log")"
	--ban-file '#recycle/' --ban-file '#snapshot/' --tag "$tag"
	# One broken file (an empty one, say) must not stop thousands: immich-go's
	# default `stop` cancels the uploads in flight too. Errors stay in the log
	# and the report; running again sends only what is missing.
	--on-errors continue)
[ "$apply" -eq 1 ] || cmd+=(--dry-run)

if [ "$apply" -eq 1 ]; then
	say "UPLOADING to $person's account ($server), tag $tag"
else
	say "dry run for $person ($server): nothing is sent"
fi
say "sources: ${sources[*]}"
say "log: $log"

status=0
docker run --rm --network "$network" \
	-v "$dir/immich-go:/usr/local/bin/immich-go:ro" \
	-v "$work:/work:ro" -v "$dir/logs:/logs" \
	-v /etc/localtime:/etc/localtime:ro \
	"${mounts[@]}" "$IMAGE" \
	immich-go "${cmd[@]}" "${extra[@]}" "${args[@]}" >&2 || status=$?

say ""
# immich-go exits 1 when any file failed, even when it went on with the rest:
# its report in the log tells which case this is.
errors=$(sed -n 's/.*Errors: *\([0-9][0-9]*\).*/\1/p' "$log" | tail -1)
if [ "$status" -ne 0 ] && [ -z "$errors" ]; then
	if grep -q "can't pause immich background jobs" "$log" 2>/dev/null; then
		say "$person is not an Immich admin, and only an admin's key can pause Immich's jobs:"
		say "  add --admin <an admin's login> (its key in $dir/token.<login>),"
		say "  or -- --pause-immich-jobs=false (Immich keeps working during the upload)"
		exit 1
	fi
	say "immich-go failed (exit $status) before its report: see $log"
	exit 1
fi
if [ "${errors:-0}" -gt 0 ]; then
	say "$errors file(s) failed, the rest went through. Which and why:"
	say "  grep 'ERR server error' $log"
fi
if [ "$apply" -eq 1 ]; then
	say "done: in Immich, search the tag $tag to see this import"
	[ "${errors:-0}" -eq 0 ] || exit 1
else
	say "dry run done: the counts are above and in $log. Same command with --apply to upload."
fi
