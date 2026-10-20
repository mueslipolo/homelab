# The image the checks workflow runs in, with its tools baked in: a push no
# longer runs apt-get, downloads gitleaks, installs uv and fetches prettier.
# Built on the NAS by the build-images job (scripts/jobs.conf) as homelab-ci:1,
# and only when this folder changes; the runner uses it from the local image
# store and never pulls it.

# Prettier, with the Node that runs it.
FROM node:22-bookworm-slim AS node
# Keep in step with .pre-commit-config.yaml.
ARG PRETTIER_VERSION=3.6.2
RUN npm install -g --no-fund --no-audit --loglevel=error "prettier@${PRETTIER_VERSION}"

FROM python:3.15-slim-bookworm
# Keep in step with .pre-commit-config.yaml.
ARG GITLEAKS_VERSION=8.24.2

RUN apt-get update -qq \
	&& apt-get install -y -qq --no-install-recommends git shellcheck curl ca-certificates jq >/dev/null \
	&& rm -rf /var/lib/apt/lists/* \
	&& curl -fsSL "https://github.com/gitleaks/gitleaks/releases/download/v${GITLEAKS_VERSION}/gitleaks_${GITLEAKS_VERSION}_linux_x64.tar.gz" \
	| tar -xz -C /usr/local/bin gitleaks

COPY --from=node /usr/local/bin/node /usr/local/bin/node
COPY --from=node /usr/local/lib/node_modules/prettier /usr/local/lib/node_modules/prettier
RUN ln -s /usr/local/lib/node_modules/prettier/bin/prettier.cjs /usr/local/bin/prettier
COPY --from=ghcr.io/astral-sh/uv:0.13.4 /uv /usr/local/bin/uv
