# edge

Caddy reverse proxy: one Let's Encrypt wildcard certificate for
`*.<internal-domain>` (Infomaniak DNS-01) and one block per app in
`caddy/Caddyfile`.

**Setting it up: [guide 03, HTTPS with Let's Encrypt and Infomaniak DNS](../../docs/guides/03-https-letsencrypt.md).**
This page is the reference.

|            |                                                                                                                                            |
| ---------- | ------------------------------------------------------------------------------------------------------------------------------------------ |
| Reached at | `https://<app>.<internal-domain>`, ports 80/443 on Caddy's own macvlan IP `CADDY_LAN_IP`. Login per app: `import authelia` = Authelia gate |
| Data       | named volumes `caddy_data` (certificates, ACME account), `caddy_config`. Nothing to back up: certificates are re-issued                    |
| Needs      | `proxy` network; a free LAN IP + MAC; LAN DNS `*.<internal-domain>` → `CADDY_LAN_IP`; internet at build time; `authelia` for gated hosts   |
| Used by    | every app with a hostname; [authelia](../authelia/README.md) (its portal and OIDC issuer)                                                  |
| Config     | `.env.example` (every variable), `caddy/Caddyfile` (this homelab), `caddy/Caddyfile.minimal` (one test app, no Authelia), `Dockerfile`     |

## Files

- **`Dockerfile`:** `xcaddy build --with github.com/caddy-dns/infomaniak`, Caddy
  version pinned. Another DNS provider: swap the module here and the `dns`
  line in the Caddyfile.
- **`caddy/Caddyfile`:** snippets `tls_wildcard` (DNS-01) and `authelia`
  (`forward_auth`), one block per app, a `*.` catch-all that returns 404 and
  makes Caddy use one wildcard certificate for every block.
- **`CADDYFILE`** variable: which file in `caddy/` Caddy runs (default
  `Caddyfile`). The whole `caddy/` folder is mounted at `/etc/caddy`.

## Gotchas

- **`INFOMANIAK_API_TOKEN` controls the domain's DNS**: the most sensitive
  secret here. It lives only in Portainer.
- **Stale image on redeploy.** No `image:` key, so `pull_policy: build` stays;
  Portainer may still skip the build (root `README.md`). After a `Dockerfile`
  change, check `docker inspect --format '{{.Created}}' <caddy-container>` moved.
- **`--watch` applies Caddyfile changes.** A Portainer redeploy does not
  recreate the container when only the Caddyfile changed, and Caddy reads it
  at start, so the old config kept running (a new host got the 404 catch-all).
  Caddy now re-reads the file every second and reloads on change; a broken
  file logs `unable to load latest config` and the last good config stays.
  Mount the folder, not the file: a single-file mount can stay pinned to the
  old file.
- **`mac_address` is service-level** (+ `networks.lan.priority: 1000`): the
  NAS's Docker (API 1.43) predates per-network MACs (API 1.44+).
- **Keep `cap_add: [NET_BIND_SERVICE]`**: the xcaddy binary carries that file
  capability; without it: `exec: operation not permitted`.
- **Bind mounts stay inside `stacks/edge/`**: Portainer's clone resolves
  nothing outside it.
- **`{$VAR}` vs `{env.VAR}`:** parse time for site addresses, runtime for the
  token and upstreams.
- **Host services (Portainer, Forgejo, LDAP) via `PROXY_GATEWAY_IP`**, the
  `proxy` network's gateway (`docker network inspect proxy`). Docker's bridge
  isolation blocks other routes, and `host.docker.internal` is `docker0`,
  another bridge.
- **Portainer upstream:** `{env.PROXY_GATEWAY_IP}:9443` with
  `transport http { tls_insecure_skip_verify }` (self-signed, on-host hop).
  Never add `https://`: scheme + placeholder crash-loops Caddy.
- **Reaching Caddy itself from a container** (OIDC clients, CI jobs): the host
  and its containers cannot reach the macvlan IP (`No route to host`). Caddy's
  `proxy` alias `auth.${INTERNAL_DOMAIN}` answers instead; `http://authelia:9091`
  does not work because the OIDC issuer URL is fixed.
