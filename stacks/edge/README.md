# edge

Caddy reverse proxy: wildcard TLS for `*.<internal-domain>` (Infomaniak DNS-01) and one `reverse_proxy` block per app in `Caddyfile`.

|            |                                                                                                                                                                                              |
| ---------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Reached at | Every `https://<app>.<internal-domain>`, ports `80`/`443` on Caddy's own macvlan IP `CADDY_LAN_IP` (DSM's nginx keeps 443 on the NAS IP) — login: per app, `import authelia` = Authelia gate |
| Data       | named volumes `caddy_data` (certs, ACME account), `caddy_config`                                                                                                                             |
| Needs      | `proxy` network; a free LAN IP + MAC; router DNS `*.<internal-domain>` → `CADDY_LAN_IP`; internet at build time; `authelia` for gated hosts                                                  |
| Config     | `.env.example` (every variable), `Caddyfile`, `Dockerfile`                                                                                                                                   |

Built locally (`xcaddy build --with github.com/caddy-dns/infomaniak`, version pinned in `Dockerfile`) so the token stays in an auditable build. Why macvlan: `docs/ARCHITECTURE.md`.

## Deploy

See `docs/INSTALL.md` for the install order.

1. Create `proxy` and read its gateway (`PROXY_GATEWAY_IP`):
   ```bash
   scripts/bootstrap.sh
   docker network inspect proxy --format '{{json .IPAM.Config}}'
   ```
   Choose `CADDY_LAN_IP`/`CADDY_LAN_MAC` (unused), `LAN_SUBNET`, `LAN_GATEWAY`, `LAN_PARENT_INTERFACE` (the NAS's LAN NIC). Get an Infomaniak API token.
2. Portainer → Stacks → Add stack → Repository, `stacks/edge/compose.yaml`, all `.env.example` variables → Deploy.
3. Router DNS `*.<internal-domain>` → `CADDY_LAN_IP`. Portainer's route also needs `docker network connect proxy portainer` (again after any recreation) and `--trusted-origins portainer.<internal-domain>` (root `README.md`).
4. **Check:** `curl -s https://nope.<internal-domain>` → `Not found — no app registered at this hostname.` (valid cert, no `-k`).

## Gotchas

- **`INFOMANIAK_API_TOKEN` controls the domain** — the repo's top secret. It and the email/LAN values live only in Portainer.
- **Stale image on redeploy.** No `image:` key, so keep `pull_policy: build`; even then Portainer may not rebuild (root `README.md`). After a `Dockerfile` change, confirm `docker inspect --format '{{.Created}}' <caddy-container>` moved.
- **`mac_address` is service-level** (+ `networks.lan.priority: 1000`): the NAS's Docker (API 1.43) predates per-network MACs (API 1.44+).
- **Keep `cap_add: [NET_BIND_SERVICE]`** — the xcaddy binary carries that file capability; without it: `exec: operation not permitted`.
- **Bind mounts stay inside `stacks/edge/`** — Portainer's clone resolves nothing outside it.
- **`{$VAR}` ≠ `{env.VAR}`:** parse-time for site addresses, runtime for the token and upstreams.
- **Reaching host-level services (Portainer, Forgejo, LDAP):** use `PROXY_GATEWAY_IP`. Docker's bridge isolation (`DOCKER-ISOLATION-STAGE-1/2`, not DSM's firewall) blocks other routes; `host.docker.internal` is `docker0`, another bridge. Two DSM firewall rules added while debugging do nothing and should be removed.
- **Portainer upstream:** `{env.PROXY_GATEWAY_IP}:9443` + `transport http { tls_insecure_skip_verify }` (self-signed, on-host hop). Never add `https://` — scheme + placeholder crash-loops Caddy.
- **Forgejo upstream** is `PROXY_GATEWAY_IP:3000`; `forgejo:3000` would also work, not yet switched.
- **Reaching Caddy itself from another container:** `proxy` containers (OIDC clients, CI jobs) can't hairpin to the macvlan IP (`OSError: [Errno 113] No route to host`). Caddy's `proxy` alias `auth.${INTERNAL_DOMAIN}` fixes it; `http://authelia:9091` doesn't (issuer is fixed). A host macvlan shim was rejected (host state across DSM reboots).
- **Unknown hostnames** get the `*.<internal-domain>` 404 catch-all.
