# authelia

Single sign-on: forward-auth gate for apps without OIDC, OIDC provider for
Paperless, Mealie, Immich, Forgejo and Dockhand. Users and groups come from an LDAP
directory (here Synology's LDAP Server) or, without one, from a file
(`AUTH_BACKEND=file`, [Without LDAP](#without-ldap)).

**Setting it up: [guide 05, single sign-on](../../docs/guides/05-sso-authelia.md).**
This page is the reference.

|            |                                                                                                                                                                                                                                                                       |
| ---------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Reached at | `https://auth.<internal-domain>` via Caddy (`authelia:9091` inside, no published port). Login: it is the portal                                                                                                                                                       |
| Data       | `AUTHELIA_SECRETS_ROOT` → secret files (**back up**)                                                                                                                                                                                                                  |
|            | `AUTHELIA_DATA_ROOT` → `db.sqlite3` (consent, regulation, OIDC state; disposable), `notification.txt` (filesystem notifier, no SMTP), `users_database.yml` (the users, with `AUTH_BACKEND=file`)                                                                      |
| Needs      | `proxy` network, [edge](../edge/README.md); with `AUTH_BACKEND=ldap`, an LDAP server (default: DSM's LDAP Server at `PROXY_GATEWAY_IP:389`)                                                                                                                           |
| Used by    | gated apps ([mediarr](../mediarr/README.md), [dashboard](../dashboard/README.md)); OIDC clients [paperless](../paperless/README.md), [mealie](../mealie/README.md), [forgejo](../forgejo/README.md), [immich](../immich/README.md), [dockhand](../dockhand/README.md) |
| Config     | `.env.example` (every variable, and the list of secret files), `config/configuration.yml`                                                                                                                                                                             |

## Modes per app

| Mode                            | Apps                                                                        | Configured in                                                 |
| ------------------------------- | --------------------------------------------------------------------------- | ------------------------------------------------------------- |
| **Gate** (Caddy `forward_auth`) | dashboard, sonarr, radarr, prowlarr, qbittorrent, seerr, maintainerr        | `import authelia` in the Caddyfile + an `access_control` rule |
| **OIDC**                        | Paperless, Mealie, Immich (`one_factor`); Dockhand, Forgejo (`admins_only`) | listed in `OIDC_CLIENTS` + a `<client>_client_secret_hash`    |
| **Bypass**                      | Jellyfin                                                                    | neither                                                       |

## Gotchas

- **Secrets are files, not stack variables in Dockhand** (Authelia advises against
  env vars: `docker inspect` shows them).
- **Digest to Authelia, password to the app.** Swapped, the handshake fails
  with an auth error, not a config error.
- **`storage_encryption_key` encrypts sensitive data in the database; the JWKS
  key (`oidc_jwks_private_key.pem`) signs OIDC tokens.**
- **Keep `X_AUTHELIA_CONFIG_FILTERS: template`**, else `{{ mustEnv … }}` stays
  literal and fails URL parsing. Keys use `secret` (strips trailing newlines),
  not `fileContent`.
- **Restart after a config change.** Authelia reads `configuration.yml` only
  at start, and a Dockhand deploy does not recreate the container when only
  that file changed: a new host gets 403 until Authelia restarts (Dockhand →
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
  `users_filter`. Bind user `uid=root,cn=users,<LDAP_BASE_DN>` unless
  `LDAP_BIND_DN` says otherwise. Another directory may need other filters.
- **Users and groups:** with LDAP, in the directory (DSM LDAP Server UI), no
  restart; with `file`, in `users_database.yml`, reloaded when it changes.
- **`admins_only`** (`default_policy: deny`, group `ADMIN_GROUP`, default
  `infra-admins`) for Dockhand and Forgejo; Mealie makes the same group its
  admins. Not `admins` (collides with the policy) nor `administrators`
  (Synology's, carries DSM/SSH rights).
- **`OIDC_CLIENTS`** lists the apps that log in by OIDC (default
  `paperless,mealie,forgejo,dockhand`; `immich` only when listed, so a
  redeploy never needs a secret file that does not exist yet).
  Only those need a `<client>_client_secret_hash`; empty turns OIDC off, and
  `oidc_hmac_secret` and the JWKS key are then unused. **Set it explicitly in
  the stack's variables** as soon as the list differs from the default: a
  value replaces the default, so a client missing from it is simply off.
- **Immich: `app.immich:///oauth-callback`** is a redirect URI for the phone
  app (a custom scheme, accepted for a confidential client); `client_secret_post`,
  Immich's default.
- **Consent:** `consent_mode: 'pre-configured'`, 1 year; the default asks at
  every login.
- **Denied users see no Authelia page**: look for `error=access_denied` in the
  client's URL.
- **SQLite** (`/data/db.sqlite3`): a handful of users write little. Login
  sessions are in memory, not in the database. Switching backends starts
  empty: everyone consents again.
- **`database is locked` means a slow disk, not too many users.** It came
  from Container Manager's `db` log driver keeping the RAID busy; every
  container now logs with `local`
  ([slow disk writes](../../docs/notes/slow-disk-writes.md)).
- **Regulation:** 5 failures in 10 minutes → 1-hour ban.

## Without LDAP

`AUTH_BACKEND=file`: the users are in `AUTHELIA_DATA_ROOT/users_database.yml`,
and `PROXY_GATEWAY_IP`, `LDAP_*` and `ldap_bind_password` are not needed. One
entry per user; the password is a digest:

```bash
docker run --rm authelia/authelia:4.39.28 authelia crypto hash generate argon2 --password '<password>'
```

```yaml
users:
  alice:
    displayname: Alice
    password: "$argon2id$v=19$m=65536,t=3,p=4$…" # the Digest line from above
    email: alice@example.com
    groups: [infra-admins] # ADMIN_GROUP: Dockhand, Forgejo, Mealie admin
  bob:
    displayname: Bob
    password: "$argon2id$…"
    email: bob@example.com
    groups: []
```

The file is read again when it changes: no restart. DSM, SMB and Drive keep
their own accounts then; LDAP is what makes one password work everywhere.

## Postgres instead of SQLite

Optional, if SQLite logs `database is locked`. In `compose.yaml`, add the
service and a private network, and give Authelia that network, a
`depends_on` and `AUTHELIA_STORAGE_POSTGRES_PASSWORD_FILE: /secrets/postgres_password`:

```yaml
services:
  postgres:
    image: postgres:17
    restart: always
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

Then the same random value in `secrets/postgres_password` and in the stack's
`POSTGRES_PASSWORD` variable in Dockhand, and an empty folder for `AUTHELIA_PGDATA_PATH`. The
`postgres` image needs `CHOWN, FOWNER, SETUID, SETGID, DAC_OVERRIDE` if you
keep `cap_drop: [ALL]` (its entrypoint chowns the data folder).
