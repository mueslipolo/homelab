#!/usr/bin/env bash
# Publishes the repo to the public GitHub copy as one snapshot commit.
#
# Forgejo holds the working history: your commits, Renovate's daily ones, PR
# merges. GitHub gets none of those; each publish adds a single commit authored
# by the public identity, with the files exactly as on Forgejo's main and a
# message listing your changes since the previous publish (Renovate's only
# counted). Nothing is ever force-pushed to GitHub.
#
#   tools/publish-github.sh        show what would be published, ask, push
#   tools/publish-github.sh -n     only show it
#   tools/publish-github.sh -y     no question
#
# Before pushing, the snapshot must pass the same checks as a commit:
# scripts/check-no-real-values.sh (with your .private-patterns) and gitleaks.
#
# Settings (environment): SOURCE_REMOTE (origin: Forgejo), PUBLIC_REMOTE (github),
# BRANCH (main), PUBLIC_NAME, PUBLIC_EMAIL (default: this repo's git user).
set -euo pipefail

readonly SOURCE_REMOTE=${SOURCE_REMOTE:-origin}
readonly PUBLIC_REMOTE=${PUBLIC_REMOTE:-github}
readonly BRANCH=${BRANCH:-main}
readonly GITLEAKS_IMAGE=zricethezav/gitleaks:v8.24.2 # in step with .pre-commit-config.yaml
readonly TRAILER=Published-from

dry=0 yes=0
while getopts ny opt; do
	case $opt in
	n) dry=1 ;;
	y) yes=1 ;;
	*) sed -n '2,20p' "$0" >&2; exit 2 ;;
	esac
done

cd "$(git rev-parse --show-toplevel)"
name=${PUBLIC_NAME:-$(git config user.name)}
email=${PUBLIC_EMAIL:-$(git config user.email)}

git fetch -q "$SOURCE_REMOTE" "$BRANCH"
src=$(git rev-parse FETCH_HEAD)
git fetch -q "$PUBLIC_REMOTE" "$BRANCH"
pub=$(git rev-parse FETCH_HEAD)

if [ "$(git rev-parse "$src^{tree}")" = "$(git rev-parse "$pub^{tree}")" ]; then
	echo "Nothing to publish: $PUBLIC_REMOTE/$BRANCH already has the files of $SOURCE_REMOTE/$BRANCH."
	exit 0
fi

# What changed since the last publish: from the trailer it left, or, the first
# time, everything committed after the public commit's date.
prev=$(git log -1 --format=%B "$pub" | sed -n "s/^$TRAILER: //p" | tail -n 1)
if [ -n "$prev" ] && git cat-file -e "$prev^{commit}" 2>/dev/null; then
	range=("$prev..$src")
else
	range=(--since="$(git log -1 --format=%cI "$pub")" "$src")
fi
mine=$(git log --no-merges --format='%s%x09%ae' "${range[@]}" |
	awk -F'\t' '$1 !~ /^chore\(deps\)/ && $2 != "bot@renovateapp.com" { print "- " $1 }')
deps=$(git log --no-merges --format='%s%x09%ae' "${range[@]}" |
	awk -F'\t' '$1 ~ /^chore\(deps\)/ || $2 == "bot@renovateapp.com"' | wc -l | tr -d ' ')

n=$(printf '%s' "$mine" | grep -c '^- ' || true)
if [ "$n" -eq 1 ]; then
	title=${mine#- }
else
	title="$n change$([ "$n" -eq 1 ] || echo s)"
fi
[ "$deps" -gt 0 ] && title+=", $deps dependency update$([ "$deps" -eq 1 ] || echo s)"
msg=$title
if [ "$n" -gt 1 ]; then msg+=$'\n\n'"$mine"; fi
msg+=$'\n\n'"$TRAILER: $src"

# The checks run on exactly the files that would be published.
tmp=$(mktemp -d)
trap 'rm -rf "$tmp"' EXIT
git archive "$src" | tar -x -C "$tmp"
[ -f .private-patterns ] || echo "warning: no .private-patterns here: only the generic real-values checks run" >&2
patterns=$PWD/.private-patterns
(
	cd "$tmp"
	[ -f "$patterns" ] && cp "$patterns" .private-patterns
	find . -type f ! -path './.private-patterns' -print0 | xargs -0 "$OLDPWD/scripts/check-no-real-values.sh"
) || { echo "Not published: real values in the snapshot (above)." >&2; exit 1; }
rm -f "$tmp/.private-patterns"
# gitleaks exits 3 for a finding (--exit-code), anything else non-zero means it
# could not run: a different message, not a false alarm.
if command -v gitleaks >/dev/null; then
	# `dir` since 8.19; older ones (Ubuntu's package) scan a folder with detect.
	if gitleaks dir --help >/dev/null 2>&1; then
		leaks=(gitleaks dir --no-banner --exit-code 3 "$tmp")
	else
		leaks=(gitleaks detect --no-git --source "$tmp" --no-banner --exit-code 3)
	fi
elif docker info >/dev/null 2>&1; then
	leaks=(docker run --rm -v "$tmp:/scan:ro" "$GITLEAKS_IMAGE" dir --no-banner --exit-code 3 /scan)
else
	echo "Not published: gitleaks is needed. Install it (e.g. sudo apt install gitleaks), or let this user run docker." >&2
	exit 1
fi
rc=0
out=$("${leaks[@]}" 2>&1) || rc=$?
if [ "$rc" -eq 3 ]; then
	printf '%s\n' "$out" >&2
	echo "Not published: gitleaks found a secret (above)." >&2
	exit 1
elif [ "$rc" -ne 0 ]; then
	printf '%s\n' "$out" >&2
	echo "Not published: gitleaks could not run (above)." >&2
	exit 1
fi

echo "Publishing $SOURCE_REMOTE/$BRANCH ($(git rev-parse --short "$src")) to $PUBLIC_REMOTE/$BRANCH as $name <$email>:"
echo
printf '%s\n' "$msg" | sed 's/^/    /'
echo
git diff --stat "$pub" "$src" | tail -n 1
[ "$dry" -eq 1 ] && exit 0
if [ "$yes" -eq 0 ]; then
	read -r -p "Push? [y/N] " answer
	[ "$answer" = y ] || [ "$answer" = Y ] || { echo "Not published."; exit 0; }
fi

new=$(GIT_AUTHOR_NAME=$name GIT_AUTHOR_EMAIL=$email GIT_COMMITTER_NAME=$name GIT_COMMITTER_EMAIL=$email \
	git commit-tree "$src^{tree}" -p "$pub" -m "$msg")
git push -q "$PUBLIC_REMOTE" "$new:refs/heads/$BRANCH"
echo "Published: $(git rev-parse --short "$new")"
