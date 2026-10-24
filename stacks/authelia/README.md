# authelia

Single sign-on: forward-auth gate for apps without OIDC, OIDC provider for
Paperless, Portainer, Forgejo and Mealie. Users and groups come from
Synology's LDAP Server.

**Setting it up: [guide 05, single sign-on](../../docs/guides/05-sso-authelia.md).**
This page is the reference.

|            |                                                                                                                                                                                                                |
| ---------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Reached at | `https://auth.<internal-domain>` via Caddy (`authelia:9091` inside, no published port). Login: it is the portal                                                                                                |
| Data       | `AUTHELIA_SECRETS_ROOT` → secret files (**back up**)                                                                                                                                                           |
|            | `AUTHELIA_DATA_ROOT` → `db.sqlite3` (consent, regulation, OIDC state; disposable), `notification.txt` (filesystem notifier, no SMTP), old `users_database.yml` (rollback)                                      |
| Needs      | `proxy` network, [edge](../edge/README.md), DSM LDAP Server at `PROXY_GATEWAY_IP:389`                                                                                                                          |
| Used by    | gated apps ([mediarr](../mediarr/README.md), [dashboard](../dashboard/README.md)); OIDC clients [paperless](../paperless/README.md), [mealie](../mealie/README.md), [forgejo](../forgejo/README.md), Portainer |
| Config     | `.env.example` (every variable, and the list of secret files), `config/configuration.yml`                                                                                                                      |

## Modes per app

| Mode                            | Apps                                                                 | Configured in                                                           |
| ------------------------------- | -------------------------------------------------------------------- | ----------------------------------------------------------------------- |
| **Gate** (Caddy `forward_auth`) | dashboard, sonarr, radarr, prowlarr, qbittorrent, seerr, maintainerr | `import authelia` in the Caddyfile + an `access_control` rule           |
| **OIDC**                        | Paperless, Mealie (`one_factor`); Portainer, Forgejo (`admins_only`) | a client block in `configuration.yml` + a `<client>_client_secret_hash` |
| **Bypass**                      | Jellyfin                                                             | neither                                                                 |

## Gotchas

- **Secrets are files, not Portainer variables** (Authelia advises against
  env vars: `docker inspect` shows them).
- **Digest to Authelia, password to the app.** Swapped, the handshake fails
  with an auth error, not a config error.
- **`storage_encryption_key` encrypts sensitive data in the database; the JWKS
  key (`oidc_jwks_private_key.pem`) signs OIDC tokens.**
- **Keep `X_AUTHELIA_CONFIG_FILTERS: template`**, else `{{ mustEnv … }}` stays
  literal and fails URL parsing. Keys use `secret` (strips trailing newlines),
  not `fileContent`.
- **Restart after a config change.** Authelia reads `configuration.yml` only
  at start, and a Portainer redeploy does not recreate the container when only
  that file changed: a new host gets 403 until Authelia restarts (Portainer →
  Containers → Restart). Check what it runs:
  `docker exec <authelia> grep -c <new rule> /config/configuration.yml`.
- **Mount the `config/` folder, never `configuration.yml` alone**: a
  single-file bind mount can stay pinned to the file the container started
  with.
- **`config/` can't be `:ro`**: the entrypoint chowns `/config`.
- **Caps:** `cap_drop: [ALL]` + `CHOWN, FOWNER, SETUID, SETGID, DAC_OVERRIDE`
  (root-then-drop entrypoint).
- **`default_policy: deny`**: a gated host without an `access_control` rule
  is refused. Keep the `bypass` rule for `auth.<internal-domain>`: a portal
  behind its own login is unreachable.
- **Synology LDAP schema:** `posixAccount`, `posixGroup` + `memberUid` (not
  `groupOfUniqueNames`). Keep the literal `{username_attribute}` in
  `users_filter`. Bind user `uid=root,cn=users,<LDAP_BASE_DN>`.
- **Users and groups:** DSM LDAP Server UI only, no restart. Rollback:
  `ldap:` → `file:` (the untouched `users_database.yml`).
- **`admins_only`** (`default_policy: deny`, `group:infra-admins`) for
  Portainer and Forgejo. The group is not named `admins` (collides with the
  policy) nor `administrators` (Synology's, carries DSM/SSH rights).
- **Portainer: `require_pkce: false`**: Portainer CE sends no `code_challenge`.
- **Consent:** `consent_mode: 'pre-configured'`, 1 year; the default asks at
  every login.
- **Denied users see no Authelia page**: look for `error=access_denied` in the
  client's URL (Portainer shows nothing).
- **SQLite** (`/data/db.sqlite3`): a handful of users write little. Login
  sessions are in memory, not in the database. Switching backends starts
  empty: everyone consents again.
- **Regulation:** 5 failures in 10 minutes → 1-hour ban.

## Postgres instead of SQLite

Optional, if SQLite logs `database is locked`. In `compose.yaml`, add the
service and a private network, and give Authelia that network, a
`depends_on` and `AUTHELIA_STORAGE_POSTGRES_PASSWORD_FILE: /secrets/postgres_password`:

```yaml
services:
  postgres:
    image: postgres:17
    restart: unless-stopped
    environment:
      POSTGRES_DB: authelia
      POSTGRES_USER: authelia
      POSTGRES_PASSWORD: ${POSTGRES_PASSWORD}
      PGUSER: authelia
    volumes:
      - ${AUTHELIA_PGDATA_PATH}:/var/lib/postgresql/data
    networks:
      - authelia_db
    healthcheck:
      test: ["CMD", "pg_isready"]
      interval: 30s
  authelia:
    depends_on:
      postgres:
        condition: service_healthy
    networks:
      - proxy
      - authelia_db

networks:
  authelia_db:
    driver: bridge
```

In `config/configuration.yml`, replace `storage.local` with:

```yaml
storage:
  postgres:
    address: 'tcp://postgres:5432'
    database: 'authelia'
    username: 'authelia'
```

Then the same random value in `secrets/postgres_password` and in Portainer's
`POSTGRES_PASSWORD`, and an empty folder for `AUTHELIA_PGDATA_PATH`. The
`postgres` image needs `CHOWN, FOWNER, SETUID, SETGID, DAC_OVERRIDE` if you
keep `cap_drop: [ALL]` (its entrypoint chowns the data folder).
