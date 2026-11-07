#!/usr/bin/env bash
# Starts each locally built image once it is ready: when the image under a
# stack's tag is not the one its container runs, asks Dockhand to redeploy
# that stack. Run every tick by run-jobs.sh, after build-images.
#
#   redeploy-images.sh <stack>=<image:tag> [...]
#
# The images come from build-images.sh (paperless-tarmed), or from an app's own
# CI on this NAS that tags them. Their stacks have
# `pull_policy: never`, so a redeploy starts whatever the tag points at.
#
# Why: Dockhand's sync (its Forgejo webhook, or its scheduled sync) only
# notices new commits, and the sync that follows a push comes before the build
# is done, so a new image waited for a redeploy by hand. Syncing stays on; this
# only covers what it cannot see.
#
# The redeploy is Dockhand's own, so it keeps the stack's environment and git
# credentials, fetches the branch it follows, and recreates the container on
# the new image; the old one runs until then: POST
# /api/git/stacks/<id>/deploy, answered once the deploy is over. A plain
# `docker compose up -d`, which recreates a container whose image changed
# under its tag (Compose's mustRecreate compares the image digest). Read from
# Dockhand 1.0.56's source.
#
# A container running a `docker exec` (a first pass started by hand) is left
# alone until a later tick (see `exec_running`). The scheduler's own jobs never overlap this one:
# run-jobs.sh runs one job at a time.
#
# A container started from another tag than the one given here (a stack
# pinned to another version through PAPERLESS_TARMED_IMAGE) is left alone.
#
# A stack whose last deploy failed (a sync error "Deploy failed: ...",
# typically "No such image" before its image was built; the old container
# keeps running under its old tag), or without a container, is redeployed once
# its image exists. One that is not a Dockhand git stack here is left alone.
# (Dockhand also retries a failed deploy by itself, at its next scheduled
# sync.)
#
# The stack name must equal its container name (as for the dashboard).
# Needs DOCKHAND_TOKEN in jobs.env; without it, exits 77 (not set up).
set -euo pipefail

REPO=$(cd "$(dirname -- "$0")/.." && pwd)
readonly REPO
# shellcheck source=scripts/lib/json.sh
. "$REPO/scripts/lib/json.sh"
# shellcheck source=scripts/lib/stacks.sh
. "$REPO/scripts/lib/stacks.sh"

# How long a redeploy may take before the container runs the new image
# (Dockhand answers once its deploy is over, within DEPLOY_TIMEOUT).
readonly WAIT=${REDEPLOY_WAIT:-180}
readonly STEP=${REDEPLOY_STEP:-5}
readonly DEPLOY_TIMEOUT=${REDEPLOY_DEPLOY_TIMEOUT:-300}

[ $# -gt 0 ] || { echo "usage: redeploy-images.sh <stack>=<image:tag> ..." >&2; exit 2; }
dockhand_ready || { echo "DOCKHAND_TOKEN is not set: nothing redeployed"; exit 77; }

stacks=""   # Dockhand's stack list, "<name> <id> <status>" per line, read once
failed=0

# Fills $stacks once per run. Not in a $( ) subshell: that would lose it.
# Status: 1 active, 4 its last deploy failed (lib/stacks.sh).
load_stacks() {
	local list id status name
	[ -z "$stacks" ] || return 0
	list=$(stacks_list) || return 1
	stacks=" " # read, even if empty
	while IFS=$'\x1f' read -r id status name _; do
		if [ -n "$name" ]; then stacks+=$'\n'"$name $id $status"; fi
	done <<<"$list"
}

# Whether a `docker exec` command (a pass started by hand) is alive in the container. Not
# Docker's list of execs (.ExecIDs): on the NAS it kept listing a pass that had been stopped,
# whose process lingered as a zombie, and a release waited behind it for good. Instead, the
# processes: an exec's process is started from outside the container (its parent is not in
# the container) and is not the main process; a zombie, or a leftover the main process
# adopted, does not count.
exec_running() { # <container>
	local main
	main=$(docker inspect --format '{{ .State.Pid }}' "$1") || return 1
	docker top "$1" -eo pid,ppid,stat | awk -v main="$main" '
		NR > 1 { pid[NR] = $1; parent[NR] = $2; state[NR] = $3; inside[$1] = 1 }
		END {
			for (i in pid)
				if (pid[i] != main && !(parent[i] in inside) && state[i] !~ /^Z/) alive = 1
			exit !alive
		}'
}

# "<id> <status>" of a stack; fails if there is none.
stack_ref() { # <stack>
	awk -v n="$1" '$1 == n { print $2, $3; f = 1 } END { exit !f }' <<<"$stacks"
}

# Asks Dockhand to redeploy the stack; fails, saying why, if it refuses.
redeploy() { # <stack> <id>
	local body error
	body=$(dockhand_api POST "/api/git/stacks/$2/deploy" "$DEPLOY_TIMEOUT") || {
		echo "$1: Dockhand refused the deploy" >&2
		return 1
	}
	# Answered 200 either way: the outcome is in the body.
	_jq -e '.success == true' <<<"$body" >/dev/null 2>&1 && return 0
	error=$(_jq -r '.error // "no reason given"' <<<"$body" 2>/dev/null) || error="unreadable answer"
	echo "$1: Dockhand's deploy failed: $error" >&2
	return 1
}

for spec in "$@"; do
	stack=${spec%%=*}
	image=${spec#*=}
	case $stack in */*) continue ;; esac # a tool image (.forgejo/ci): nothing runs it

	want=$(docker image inspect --format '{{ .Id }}' "$image" 2>/dev/null) || {
		echo "$stack: no $image yet, nothing to start"
		continue
	}
	have=$(docker inspect --format '{{ .Image }}' "$stack" 2>/dev/null) || have=""
	if [ "$have" = "$want" ]; then
		echo "$stack: runs $image"
		continue
	fi

	if ! load_stacks; then
		echo "$stack: Dockhand did not answer" >&2
		failed=$((failed + 1))
		continue
	fi
	if ! ref=$(stack_ref "$stack"); then
		if [ -n "$have" ]; then
			echo "$stack: has a container but is not a Dockhand git stack" >&2
			failed=$((failed + 1))
		else
			echo "$stack: not deployed here, skipped"
		fi
		continue
	fi
	read -r id status <<<"$ref"

	if [ -n "$have" ] && exec_running "$stack"; then
		echo "$stack: a command runs in it (docker exec): redeployed on a later tick"
		continue
	fi
	if [ "$status" = 4 ]; then
		# Its last deploy failed, typically "No such image" before this image
		# was built: the old container, if any, still runs, under its old tag.
		echo "$stack: its last deploy failed, and $image exists now: redeploying"
	elif [ -z "$have" ]; then
		echo "$stack: a Dockhand stack without a container, and $image exists: deploying"
	else
		# Started from another tag by a deploy that worked: the stack pins a
		# version (PAPERLESS_TARMED_IMAGE=...).
		tag=$(docker inspect --format '{{ .Config.Image }}' "$stack")
		if [ "$tag" != "$image" ]; then
			echo "$stack: pinned to $tag, not $image: left alone"
			continue
		fi
		echo "$stack: new $image (${want:7:12}, running ${have:7:12}): redeploying"
	fi
	if ! redeploy "$stack" "$id"; then
		failed=$((failed + 1))
		continue
	fi

	waited=0
	while have=$(docker inspect --format '{{ .Image }}' "$stack" 2>/dev/null || true); [ "$have" != "$want" ]; do
		if [ "$waited" -ge "$WAIT" ]; then
			echo "$stack: still not running the new $image after ${WAIT}s: see the stack in Dockhand" >&2
			failed=$((failed + 1))
			continue 2
		fi
		sleep "$STEP"
		waited=$((waited + STEP))
	done
	echo "$stack: redeployed, runs $image"
done

[ "$failed" -eq 0 ] || { echo "$failed stack(s) not redeployed" >&2; exit 1; }
