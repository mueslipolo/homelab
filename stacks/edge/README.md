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
  (`forward_auth`), `errors` (error pages), one block per app, a `*.` catch-all that returns 404 and
  makes Caddy use one wildcard certificate for every block.
- **`caddy/errors/`:** the error pages (see below).
- **`CADDYFILE`** variable: which file in `caddy/` Caddy runs (default
  `Caddyfile`). The whole `caddy/` folder is mounted at `/etc/caddy`.

## Error pages

When Caddy itself can't serve a request, every site shows `caddy/errors/error.html`
(snippet `errors`, imported by each block), with a Go gopher scene per status:

| Status   | When                                                 | Page                                        |
| -------- | ---------------------------------------------------- | ------------------------------------------- |
| 502, 503 | the app (or Authelia, for gated apps) doesn't answer | "This is fine.", tries again every 30 s     |
| 504      | the app answered too slowly                          | "Zzz… nearly done.", tries again every 30 s |
| 404      | a hostname with no block (the `*.` catch-all)        | "Where is it?", link to the dashboard       |
| other    | an error inside Caddy                                | "Uh-oh.", the error ID to look up           |

An app's own error pages (its 404, its login) are untouched: these only cover
errors Caddy raises. The footer shows host, upstream, time and Caddy's error
ID. Caddy logs only requests that end in a 5xx, with `err_id` and `err_msg`:
`docker logs <caddy> 2>&1 | grep <error id>` gives the reason.

**Test them:** `https://dashboard.<internal-domain>/__error/502` (also `504`,
`404`, `500`), behind Authelia. A real one: `docker stop mediarr-sonarr-1`,
open Sonarr, `docker start mediarr-sonarr-1`; the page brings Sonarr back by
itself.

The scenes are inside the page (`caddy/errors/art/*.b64`, included by the
template), so the page needs nothing else to load. Art and credits:
[`caddy/errors/CREDITS.md`](caddy/errors/CREDITS.md).

## Gotchas

- **`INFOMANIAK_API_TOKEN` controls the domain's DNS**: the most sensitive
  secret here. It lives only in Portainer.
- **Stale image on redeploy.** No `image:` key, so `pull_policy: build` stays;
  Portainer may still skip the build ([guide 02, Troubleshooting](../../docs/guides/02-portainer.md#troubleshooting)). After a `Dockerfile`
  change, check `docker inspect --format '{{.Created}}' <caddy-container>` moved.
- **`--watch` applies Caddyfile changes.** A Portainer redeploy does not
  recreate the container when only the Caddyfile changed, and Caddy reads it
  at start, so the old config kept running (a new host got the 404 catch-all).
  Caddy now re-reads the file every second and reloads on change. A file that
  fails to load logs `unable to load latest config` and **stops the watching**:
  the last good config keeps serving, and later changes are ignored until
  `docker restart` of the Caddy container. A config that loads but fails to
  apply logs `applying latest config` and the watching goes on.
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
  `proxy` network's gateway (`docker network inspect proxy`). Observed: the
  NAS's LAN IP and `host.docker.internal` did not answer from the `proxy`
  network; cause not established.
- **Portainer upstream:** `{env.PROXY_GATEWAY_IP}:9443` with
  `transport http { tls_insecure_skip_verify }` (self-signed, on-host hop).
  Never add `https://`: scheme + placeholder crash-loops Caddy.
- **Reaching Caddy itself from a container** (OIDC clients, CI jobs): the host
  and its containers cannot reach the macvlan IP (`No route to host`). Caddy's
  `proxy` alias `auth.${INTERNAL_DOMAIN}` answers instead; `http://authelia:9091`
  does not work because the OIDC issuer URL is fixed.
