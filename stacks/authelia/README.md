# authelia

SSO: forward-auth gate for full-gate apps and OIDC provider for Paperless, Portainer, Forgejo and Mealie; users and groups come from Synology's LDAP Server.

|            |                                                                                                                      |
| ---------- | -------------------------------------------------------------------------------------------------------------------- |
| Reached at | `https://auth.<internal-domain>` via Caddy (`authelia:9091` internally, no published port) — login: it is the portal |
| Data       | `AUTHELIA_SECRETS_ROOT` → secret files (back up)                                                                     |
|            | `AUTHELIA_DATA_ROOT` → `notification.txt` (filesystem notifier, no SMTP), old `users_database.yml`                   |
|            | `AUTHELIA_PGDATA_PATH` → Postgres: sessions, consent, regulation (disposable)                                        |
| Needs      | `proxy` network, `edge`, DSM LDAP Server (at `PROXY_GATEWAY_IP:389`); stack creates private `authelia_db`            |
| Config     | `.env.example` (every variable), `config/configuration.yml`                                                          |

## Deploy

See `docs/INSTALL.md` for the install order.

1. DSM LDAP Server: set it up (its FQDN gives `LDAP_BASE_DN`); create group `infra-admins` with the admins.
2. Secrets on the NAS — every `/secrets/*` file in `compose.yaml` must exist:
   ```bash
   mkdir -p <nas-volume-path>/docker/authelia/{secrets,data,pgdata}
   cd <nas-volume-path>/docker/authelia/secrets && chmod 700 .
   for s in session_secret storage_encryption_key jwt_secret oidc_hmac_secret; do openssl rand -hex 32 > $s; done
   echo -n '<LDAP bind password>' > ldap_bind_password
   echo -n '<POSTGRES_PASSWORD value>' > postgres_password
   AUTHELIA_VERSION=<tag from stacks/authelia/compose.yaml>
   docker run --name authelia-keygen "authelia/authelia:$AUTHELIA_VERSION" authelia crypto pair rsa generate --directory /tmp
   docker cp authelia-keygen:/tmp/private.pem oidc_jwks_private_key.pem && docker rm authelia-keygen
   chmod 600 *
   ```
   Per client (`paperless`, `portainer`, `forgejo`, `mealie`):
   ```bash
   docker run --rm "authelia/authelia:$AUTHELIA_VERSION" authelia crypto hash generate pbkdf2 --random --random.length 64 --random.charset alphanumeric
   ```
   `Digest:` → file `<client>_client_secret_hash` (`chmod 600`). `Random Password:` → the app (Portainer/Forgejo settings UI; `PAPERLESS_OIDC_CLIENT_SECRET`, `MEALIE_OIDC_CLIENT_SECRET`).
3. Portainer → Stacks → Add stack → Repository, `stacks/authelia/compose.yaml`, all `.env.example` variables → Deploy.
4. **Check:** `curl -s https://auth.<internal-domain>/api/health` → `{"status":"OK"}`; log in at `https://dashboard.<internal-domain>`.

## Adding an app

- **Gated:** `import authelia` in `stacks/edge/Caddyfile` **plus** an `access_control` rule — `default_policy: 'deny'` blocks gated hosts without one.
- **OIDC:** new hash file + client block in `config/configuration.yml` (copy one), list it in `.env.example`, redeploy.

## Gotchas

- **Secrets are files, not Portainer variables** (Authelia advises against env vars: `docker inspect`). Only exception: `POSTGRES_PASSWORD` for the Postgres server — it must equal `secrets/postgres_password`.
- **Digest to Authelia, password to the app** — swapped, the handshake fails with an auth error, not a config error.
- **Losing `storage_encryption_key` or the JWKS key breaks every OIDC client.**
- **Keep `X_AUTHELIA_CONFIG_FILTERS: template`** — else `{{ mustEnv … }}` stays literal and fails URL parsing. Keys use `secret` (strips trailing newlines), not `fileContent`.
- **`config/` mount can't be `:ro`**: the entrypoint chowns `/config`.
- **Mount the `config/` folder, never `configuration.yml` alone.** A single-file
  bind mount pins the file the container started with. Portainer's Git redeploy
  replaces the file, so the running Authelia kept its old access rules (a new
  host got 403) until restarted. To check what is running:
  `docker exec <authelia> grep -c <new rule> /config/configuration.yml`.
- **Caps:** `cap_drop: [ALL]` + `CHOWN, FOWNER, SETUID, SETGID, DAC_OVERRIDE` on both services (root-then-drop entrypoints).
- **Keep the `bypass` rule for `auth.<internal-domain>`** — a portal behind its own login is unreachable.
- **Synology LDAP schema:** `posixAccount`, `posixGroup` + `memberUid` (checked with `ldapsearch`; not `groupOfUniqueNames`). Keep the literal `{username_attribute}` in `users_filter`. Bind user `uid=root,cn=users,<LDAP_BASE_DN>`.
- **Users/groups:** DSM LDAP Server UI only, no restart. Rollback: `ldap:` → `file:` (the untouched `users_database.yml`).
- **`admins_only` policy** (`default_policy: deny`, `group:infra-admins`) for Portainer/Forgejo; Paperless/Mealie `one_factor`. Not named `admins` (collides with the policy) nor Synology's `administrators`, keeping DSM/SSH rights separate (`docs/ARCHITECTURE.md`).
- **Portainer: `require_pkce: false`** — Portainer CE sends no `code_challenge`.
- **Consent:** `consent_mode: 'pre-configured'`, `pre_configured_consent_duration: '1 year'`; the default asks every login.
- **Denied users see no Authelia page** — look for `error=access_denied` in the client's URL (Portainer shows nothing).
- **Postgres, not SQLite:** SQLite gave real `database is locked` errors. A backend switch starts empty: everyone logs in and re-consents once.
- **Regulation:** 5 failures in 10 minutes → 1-hour ban.
