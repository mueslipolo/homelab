#!/usr/bin/env bash
# Creates the shared `proxy` Docker network. Idempotent — safe to re-run.
set -euo pipefail

readonly PROXY_NETWORK="proxy"

main() {
	if docker network inspect "${PROXY_NETWORK}" >/dev/null 2>&1; then
		echo "Network '${PROXY_NETWORK}' already exists, nothing to do."
		return 0
	fi

	docker network create "${PROXY_NETWORK}"
	echo "Created network '${PROXY_NETWORK}'."
}

main "$@"
