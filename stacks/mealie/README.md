# mealie

Recipe manager: mealie with its own postgres.

|            |                                                                                                                |
| ---------- | -------------------------------------------------------------------------------------------------------------- |
| Reached at | `https://mealie.<internal-domain>` — login: OIDC via Authelia, auto-redirect, no password login, no Caddy gate |
| Data       | `MEALIE_PGDATA_PATH` → postgres                                                                                |
|            | `MEALIE_DATA_PATH` → app data                                                                                  |
| Needs      | `proxy` network (app only; private `mealie`), `edge`, `authelia`                                               |
| Config     | `.env.example` (every variable)                                                                                |

## Deploy

See `docs/INSTALL.md` for the install order.

1. Create the folders in their own Shared Folder (not under `docker`), with `dumps/` beside the live data:
   ```bash
   mkdir -p <nas-volume-path>/mealie/{pgdata,data,dumps}
   ```
2. Generate the OIDC secret (command in `stacks/authelia/README.md`): `Random Password` → `MEALIE_OIDC_CLIENT_SECRET`; `Digest` → Authelia's `mealie_client_secret_hash`.
3. Portainer → Stacks → Add stack → Repository, compose path `stacks/mealie/compose.yaml`, variables from `.env.example` → Deploy.
   Secrets only in Portainer. `DEFAULT_EMAIL` is PII: real value in Portainer, placeholder in git.
4. **Check:** `https://mealie.<internal-domain>` goes straight to Authelia and back.
5. Backups: as an admin user, create an API token (profile → API Tokens) and add it to the scheduler's `jobs.env` as `MEALIE_API_TOKEN` (`docs/OPERATIONS.md`).

## Gotchas

- **Backup = dumps only.** Hyper Backup takes `dumps/`, written daily by the scheduler's `mealie-backup` job (Mealie's own export, `POST /api/admin/backups`, run inside the container), not live data. Needs `MEALIE_API_TOKEN` in the scheduler's `jobs.env`: as an **admin** user, profile → API Tokens. The export contains Mealie's secret keys, so `dumps/` stays root-only (`chmod 700`). See `docs/OPERATIONS.md`.
- **postgres capabilities.** Needs `CHOWN, FOWNER, SETUID, SETGID, DAC_OVERRIDE` on top of `cap_drop: [ALL]` (entrypoint chmods its data dir as root every start). Without: `chmod: ... Operation not permitted`, never healthy.
- **Authelia redirect URIs:** `/login` and `/login?direct=1`, exact (no wildcards).
- **Why OIDC, not the Caddy gate:** Mealie has no trusted-header login, so the gate meant two logins. `OIDC_AUTO_REDIRECT=true` + `ALLOW_PASSWORD_LOGIN=false` give real SSO.
- **Redirect to `http://`?** Not seen yet (`BASE_URL` should suffice). If it happens, Mealie's docs say add `--forwarded-allow-ips=<caddy-ip>` to its Gunicorn so `X-Forwarded-Proto` is trusted.
- **`/api` is protected by Mealie alone** (OIDC-backed session/token), not Caddy. The mobile app calls it directly; only the web UI is used today.
