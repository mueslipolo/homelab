#!/usr/bin/env bash
# Builds each locally built image (a stack with a Dockerfile but no `build:` in
# compose) whenever its code changed in git. Run every tick by run-jobs.sh.
#
#   build-images.sh <stack>=<image:tag> [...]
#
# <stack> is a folder under stacks/; a path with a slash is a folder of the
# repo instead, for images that are not a stack (.forgejo/ci, the CI image).
#
# Why: Portainer's "Pull and redeploy" fetched new code for a Git stack but did
# not reliably rebuild its image, even with `pull_policy: build` -- the
# container kept running the previous code, with nothing reporting it. So such
# stacks have no `build:` in compose (`pull_policy: never`): this job is the
# only builder, from this repo's own clone, and a redeploy just starts its image.
#
# Each image is labelled with a hash of its stack directory's code (lib/code.sh:
# every file but Markdown), so an unchanged stack, or one where only a README
# changed, costs one `docker image inspect` and nothing else.
set -euo pipefail

REPO=$(cd "$(dirname -- "$0")/.." && pwd)
readonly REPO
# shellcheck source=scripts/lib/code.sh
. "$REPO/scripts/lib/code.sh"
readonly LABEL="homelab.tree"

[ $# -gt 0 ] || { echo "usage: build-images.sh <stack>=<image:tag> ..." >&2; exit 2; }

built=0
for spec in "$@"; do
	stack=${spec%%=*}
	image=${spec#*=}
	case $stack in */*) rel=$stack ;; *) rel=stacks/$stack ;; esac
	dir="$REPO/$rel"
	[ -f "$dir/Dockerfile" ] || { echo "$stack: no Dockerfile in $dir" >&2; exit 1; }

	tree=$(code_hash "$REPO" "$rel")
	current=$(docker image inspect --format "{{ index .Config.Labels \"$LABEL\" }}" "$image" 2>/dev/null || true)
	if [ "$tree" = "$current" ]; then
		echo "$stack: $image is current"
		continue
	fi

	echo "$stack: building $image (code ${tree:0:12}, image had ${current:-none})"
	# homelab.commit / homelab.built: what the dashboard's light shows. The
	# image's own Created date is not usable: a metadata-only rebuild keeps the
	# base image's.
	docker build --quiet --label "$LABEL=$tree" \
		--label "homelab.commit=$(git -C "$REPO" rev-parse --short HEAD)" \
		--label "homelab.built=$(date +%s)" \
		--tag "$image" "$dir"
	built=$((built + 1))
	case $stack in
	*/*) echo "$stack: built." ;;
	*) echo "$stack: built. Redeploy the stack in Portainer to run it." ;;
	esac
done

echo "$built image(s) built"
