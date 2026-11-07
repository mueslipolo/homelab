# The build environment only (build.sh): Rust on Alpine gives a static musl
# binary; musl-dev has the C headers ring (TLS) needs.
FROM docker.io/library/rust:1.88-alpine
RUN apk add --no-cache musl-dev
