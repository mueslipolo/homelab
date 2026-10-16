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
|            | `AUTHELIA_DATA_ROOT` → `notification.txt` (filesystem notifier, no SMTP), old `users_database.yml` (rollback)                                                                                                  |
|            | `AUTHELIA_PGDATA_PATH` → Postgres: sessions, consent, regulation (disposable)                                                                                                                                  |
| Needs      | `proxy` network, [edge](../edge/README.md), DSM LDAP Server at `PROXY_GATEWAY_IP:389`; the stack creates private `authelia_db`                                                                                 |
| Used by    | gated apps ([mediarr](../mediarr/README.md), [dashboard](../dashboard/README.md)); OIDC clients [paperless](../paperless/README.md), [mealie](../mealie/README.md), [forgejo](../forgejo/README.md), Portainer |
| Config     | `.env.example` (every variable, and the list of secret files), `config/configuration.yml`                                                                                                                      |

## Modes per app

| Mode                            | Apps                                                                      | Configured in                                                           |
| ------------------------------- | ------------------------------------------------------------------------- | ----------------------------------------------------------------------- |
| **Gate** (Caddy `forward_auth`) | dashboard, sonarr, radarr, prowlarr, qbittorrent, jellyseerr, maintainerr | `import authelia` in the Caddyfile + an `access_control` rule           |
| **OIDC**                        | Paperless, Mealie (`one_factor`); Portainer, Forgejo (`admins_only`)      | a client block in `configuration.yml` + a `<client>_client_secret_hash` |
| **Bypass**                      | Jellyfin                                                                  | neither                                                                 |

## Gotchas

- **Secrets are files, not Portainer variables** (Authelia advises against
  env vars: `docker inspect` shows them). Only exception: `POSTGRES_PASSWORD`
  for the Postgres server; it must equal `secrets/postgres_password`.
- **Digest to Authelia, password to the app.** Swapped, the handshake fails
  with an auth error, not a config error.
- **Losing `storage_encryption_key` or the JWKS key breaks every OIDC client.**
- **Keep `X_AUTHELIA_CONFIG_FILTERS: template`**, else `{{ mustEnv … }}` stays
  literal and fails URL parsing. Keys use `secret` (strips trailing newlines),
  not `fileContent`.
- **Mount the `config/` folder, never `configuration.yml` alone.** A
  single-file bind mount keeps the file the container started with; after a
  Git redeploy Authelia ran the old access rules (a new host got 403) until
  restarted. Check: `docker exec <authelia> grep -c <new rule> /config/configuration.yml`.
- **`config/` can't be `:ro`**: the entrypoint chowns `/config`.
- **Caps:** `cap_drop: [ALL]` + `CHOWN, FOWNER, SETUID, SETGID, DAC_OVERRIDE`
  on both services (root-then-drop entrypoints).
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
- **Postgres, not SQLite:** SQLite gave `database is locked` errors under
  forward-auth load. Switching backends starts empty: everyone logs in and
  consents again.
- **Regulation:** 5 failures in 10 minutes → 1-hour ban.
