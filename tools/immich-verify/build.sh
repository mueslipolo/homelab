#!/usr/bin/env bash
# Builds immich-verify as one static Linux binary (musl), in a container: no
# Rust toolchain on the desktop. Crates are cached under target/, so a rebuild
# after a change takes seconds. Docker, or rootless Podman (also when `docker`
# is Podman's stand-in).
#
#   tools/immich-verify/build.sh    ->  tools/immich-verify/target/release/immich-verify
set -euo pipefail

here=$(cd "$(dirname "$0")" && pwd)

# The files the build writes must end up yours. Docker: run as your uid.
# Rootless Podman maps container users to other ids, so -u would leave them
# owned by a stranger: --userns=keep-id keeps your own.
if command -v docker >/dev/null && docker info >/dev/null 2>&1 && ! docker --version | grep -qi podman; then
	engine=docker as_me=(-u "$(id -u):$(id -g)")
elif command -v podman >/dev/null; then
	engine=podman as_me=(--userns=keep-id)
elif command -v docker >/dev/null; then
	echo "build.sh: docker is installed but not usable by $(id -un) (sudo usermod -aG docker $(id -un), then log in again), and there is no podman" >&2
	exit 1
else
	echo "build.sh: needs docker or podman" >&2
	exit 1
fi

"$engine" build -q -t immich-verify-build -f "$here/Dockerfile" "$here" >/dev/null
"$engine" run --rm "${as_me[@]}" -v "$here":/src -w /src \
	-e CARGO_HOME=/src/target/cargo-home \
	immich-verify-build cargo build --release --locked
echo "built with $engine: $here/target/release/immich-verify"
