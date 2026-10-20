#!/usr/bin/env bash
# Deletes Docker images no container uses and old build cache, so image
# updates (Renovate bumps, build-images rebuilds) don't pile up on the volume.
# Runs as root from scripts/run-jobs.sh (see scripts/jobs.conf).
#
# Only images unused by every container, running or stopped, are removed, so
# nothing deployed is touched. An image deleted while its stack is stopped is
# pulled again on the next deploy; paperless-tarmed's is rebuilt by
# build-images.
#
# Kept although no container uses them between runs: the images the Forgejo
# workflows run in (.forgejo/workflows/*.yaml: Renovate's, homelab-ci). A plain
# `docker image prune -a` deleted them every night once a week old (by their
# build date, not their last use), and they were pulled or rebuilt again the
# next morning. Their previous versions, untagged once replaced, still go.
set -euo pipefail

readonly AGE="${DOCKER_PRUNE_AGE:-168h}" # created more than a week ago
REPO=$(cd "$(dirname -- "$0")/.." && pwd)
readonly REPO

# Images named in the workflows (`image: <ref>` lines).
keep=$(cat "$REPO"/.forgejo/workflows/*.yaml 2>/dev/null |
	sed -n 's/^[[:space:]]*image:[[:space:]]*["'\'']\{0,1\}\([^"'\''[:space:]]*\).*/\1/p' | sort -u)

# Old versions and anything untagged: removed whatever their name.
docker image prune -f --filter "until=$AGE" | tail -n 1

# Tagged, unused, old images, except the kept ones.
used=$(docker ps -aq | xargs -r docker inspect --format '{{.Image}}' | sort -u)
cutoff=$(date -d "-${AGE%h} hours" +%s)
removed=0
while read -r id ref created; do
	[ -n "$id" ] || continue
	grep -qxF -- "$ref" <<<"$keep" && continue
	grep -qxF -- "$id" <<<"$used" && continue
	[ "$(date -d "$created" +%s 2>/dev/null || echo 0)" -lt "$cutoff" ] || continue
	docker rmi -- "$ref" >/dev/null 2>&1 && removed=$((removed + 1))
done < <(docker images --no-trunc --format '{{.ID}} {{.Repository}}:{{.Tag}} {{.CreatedAt}}' | awk '$2 !~ /<none>/ {print $1, $2, $3"T"$4$5}')
echo "Removed $removed unused image(s) older than $AGE; kept the workflows' images: $(tr '\n' ' ' <<<"$keep")"

docker builder prune -af --filter "until=$AGE" | tail -n 1
