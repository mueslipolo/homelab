#!/usr/bin/env bash
# Regenerates docs/images/dashboard-*.png: the dashboard with made-up data
# (screenshots.py), in Playwright's image: it has the browsers, and the
# Python package goes into a throwaway folder.
#
#   tools/dashboard-demo/screenshots.sh [light|dark]
set -euo pipefail

repo=$(cd "$(dirname "$0")/../.." && pwd)
docker run --rm --init --ipc=host -u "$(id -u):$(id -g)" -e HOME=/tmp \
	-v "$repo":/repo -w /repo \
	mcr.microsoft.com/playwright/python:v1.55.0-noble \
	bash -c 'python3 -m pip install -q --target /tmp/py playwright==1.55.0 &&
		PYTHONPATH=/tmp/py python3 tools/dashboard-demo/screenshots.py "$1"' _ "${1:-light}"
