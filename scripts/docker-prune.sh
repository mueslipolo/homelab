#!/usr/bin/env bash
# Deletes Docker images no container uses and old build cache, so image
# updates (Renovate bumps, build-images rebuilds, CI images) don't pile up on
# the volume. Runs as root from scripts/run-jobs.sh (see scripts/jobs.conf).
#
# Only images unused by every container, running or stopped, are removed, so
# nothing deployed is touched. An image deleted while its stack is stopped is
# pulled again on the next deploy; paperless-tarmed's is rebuilt by
# build-images.
set -euo pipefail

readonly AGE="${DOCKER_PRUNE_AGE:-168h}" # created more than a week ago

docker image prune -af --filter "until=$AGE" | tail -n 1
docker builder prune -af --filter "until=$AGE" | tail -n 1
