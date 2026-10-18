# ntfy

Push notifications: an ntfy server for the phones, fed by the scheduler, the
\*arr apps, Seerr and DSM.

**Setting it up: [guide 11, Notifications with ntfy](../../docs/guides/11-notifications.md).**
This page is the reference.

|            |                                                                                                                                                                          |
| ---------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| Reached at | `https://ntfy.<internal-domain>` (Caddy → `ntfy:8080`, no Authelia gate: ntfy's own users and tokens); `http://ntfy:8080` on `proxy`; `http://127.0.0.1:2586` on the NAS |
| Data       | `NTFY_DATA_ROOT` → `auth.db` (users, access rules, tokens), `cache.db` (messages, 72 h)                                                                                  |
| Needs      | `proxy` network, [edge](../edge/README.md)                                                                                                                               |
| Used by    | the `renovate-notify` job, Sonarr/Radarr/Prowlarr, Seerr ([mediarr](../mediarr/README.md)), DSM                                                                          |
| Config     | `.env.example` (every variable); the rest is environment in `compose.yaml`                                                                                               |

## Gotchas

- **`NTFY_AUTH_DEFAULT_ACCESS: deny-all`.** Every topic needs a user or token.
  The senders share one publish-only token, set per topic with `ntfy access`.
- **Loopback port `127.0.0.1:2586`** is for DSM and the scheduler: they run on
  the host, which cannot reach Caddy's macvlan IP. It is not on the LAN.
- **Listens on 8080**, not ntfy's default 80: it runs as `PUID:PGID`, and an
  unprivileged user can't bind below 1024.
- **`NTFY_UPSTREAM_BASE_URL: https://ntfy.sh`** is for iPhones only: a
  content-free ping relayed to Apple's push service; the app then fetches the
  message from here.
- **Phones away from home** need the VPN: ntfy is LAN-only like everything
  else. Messages wait 72 h (`NTFY_CACHE_DURATION`).
- **DSM webhooks can't set headers**: the token goes in the `auth` URL
  parameter, raw base64 of `Bearer <token>` (no trailing `=`).
- **Users and tokens are made with the CLI** (`ntfy user`, `ntfy access`,
  `ntfy token` in the container), kept in `auth.db`. ntfy's declarative
  `NTFY_AUTH_USERS` would put password hashes in Portainer's variables, and
  deletes users removed from it.
