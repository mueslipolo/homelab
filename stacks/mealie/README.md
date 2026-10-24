# mealie

Recipe manager: mealie with its own postgres.

**Setting it up: [guide 08, Mealie](../../docs/guides/08-mealie.md).**
This page is the reference.

|            |                                                                                                                                       |
| ---------- | ------------------------------------------------------------------------------------------------------------------------------------- |
| Reached at | `https://mealie.<internal-domain>` (Caddy → `mealie:9000`). Login: OIDC via Authelia, auto-redirect, no password login, no Caddy gate |
| Data       | `MEALIE_PGDATA_PATH` → postgres; `MEALIE_DATA_PATH` → app data; `dumps/` beside them, written by the `mealie-backup` job              |
| Needs      | `proxy` network (app only; private `mealie`), [edge](../edge/README.md), [authelia](../authelia/README.md)                            |
| Used by    | the `mealie-backup` job ([guide 10](../../docs/guides/10-backups-and-jobs.md))                                                        |
| Config     | `.env.example` (every variable)                                                                                                       |

## Gotchas

- **Backup = dumps only.** Hyper Backup takes `dumps/`, written daily by the
  scheduler's `mealie-backup` job (Mealie's own export,
  `POST /api/admin/backups`, run inside the container), not the live data. It needs
  `MEALIE_API_TOKEN` in `jobs.env`, created by an **admin** user (profile →
  API Tokens). The export contains Mealie's secret keys: `dumps/` stays
  root-only (`chmod 700`).
- **postgres capabilities:** `CHOWN, FOWNER, SETUID, SETGID, DAC_OVERRIDE` on
  top of `cap_drop: [ALL]` (the entrypoint chmods its data dir as root at every
  start). Without them: `chmod: ... Operation not permitted`, never healthy.
- **Authelia redirect URIs:** `/login` and `/login?direct=1`, exact (no
  wildcards).
- **OIDC, not the Caddy gate:** the gate plus Mealie's own login meant two
  logins. `OIDC_AUTO_REDIRECT=true` + `ALLOW_PASSWORD_LOGIN=false` give real SSO.
- **Redirect to `http://`?** Not seen (`BASE_URL` suffices).
- **`/api` is protected by Mealie alone** (OIDC-backed session or token), not
  Caddy: the mobile app calls it directly.
