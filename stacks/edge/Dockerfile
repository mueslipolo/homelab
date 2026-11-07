FROM caddy:2.11-builder-alpine AS builder
# The DNS provider's module (github.com/caddy-dns/<name>): the official image
# has none. compose.yaml passes DNS_PROVIDER.
ARG DNS_PROVIDER
RUN test -n "$DNS_PROVIDER" && xcaddy build --with "github.com/caddy-dns/$DNS_PROVIDER"

FROM caddy:2.11-alpine
COPY --from=builder /usr/bin/caddy /usr/bin/caddy
