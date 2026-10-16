# paperless

Document archive: paperless-ngx with postgres, redis, gotenberg and tika.

|            |                                                                                                |
| ---------- | ---------------------------------------------------------------------------------------------- |
| Reached at | `https://paperless.<internal-domain>` — login: OIDC via Authelia, local login kept as fallback |
| Data       | `PAPERLESS_DOCKER_ROOT` → `db/`, `redis/` (state)                                              |
|            | `PAPERLESS_DATA_ROOT` → `data/`, `media/`, `export/`, `scripts/` (documents)                   |
|            | `PAPERLESS_INBOX_PATH` → consume folder                                                        |
| Needs      | `proxy` network (`webserver` only; private `paperless_net`), `edge`, `authelia`                |
| Config     | `.env.example` (every variable)                                                                |

## Deploy

See `docs/INSTALL.md` for the install order.

1. Create the folders; keep state and documents on separate roots. `broker` runs as `PAPERLESS_UID:PAPERLESS_GID`, so it must own `redis/`:
   ```bash
   mkdir -p <nas-volume-path>/docker/paperless/{db,redis} <nas-volume-path>/paperless/{data,media,export,scripts} <nas-volume-path>/paperless-inbox
   chown <uid>:<gid> <nas-volume-path>/docker/paperless/redis
   ```
2. Generate the OIDC secret (repo root): `Random Password` → `PAPERLESS_OIDC_CLIENT_SECRET`; `Digest` → Authelia's `paperless_client_secret_hash` (`stacks/authelia/README.md`).
   ```bash
   AUTHELIA_VERSION=$(grep -oE 'authelia/authelia:[^ ]+' stacks/authelia/compose.yaml | cut -d: -f2)
   docker run --rm "authelia/authelia:$AUTHELIA_VERSION" authelia crypto hash generate pbkdf2 --random --random.length 64 --random.charset alphanumeric
   ```
3. Portainer → Stacks → Add stack → Repository, compose path `stacks/paperless/compose.yaml`, variables from `.env.example`, secrets only there → Deploy.
4. Existing local accounts, once each: log in locally → profile icon → My Profile → **Connect new social account** → Authelia.
5. **Check:** the login page shows an "Authelia" button that logs you in.

## Gotchas

- **OIDC must match Authelia.** `provider_id` `authelia` fixes the callback `https://paperless.<internal-domain>/accounts/oidc/authelia/login/callback/` (= Authelia's `redirect_uris`); `client_id` `paperless`; the secret here is the plaintext, never the Digest. Mistakes fail as auth errors, not config errors.
- **"A user with that username already exists"** is expected: django-allauth won't auto-link OIDC to a local account. Do step 4.
- **Keep `webserver` in `PAPERLESS_ALLOWED_HOSTS`.** `paperless-tarmed` calls `http://webserver:8000` over `proxy`; without it that app gets a bodyless 400 (DisallowedHost), nothing logged here. See `stacks/paperless-tarmed/README.md`.
- **`PAPERLESS_ALLOWED_HOSTS` takes bare hostnames; `PAPERLESS_URL` keeps `https://`.**
- **Capabilities** on top of `cap_drop: [ALL]`: `webserver` `CHOWN, SETUID, SETGID, DAC_OVERRIDE` (UID remap); `db` adds `FOWNER` too (postgres root-then-drop).
- **`webserver` waits** for healthy `db` and `broker`.
- **`gotenberg`'s `command:`** (JavaScript off, file allow-list) is deliberate hardening.
- **`tika` is pinned by digest**: no date-style tag exists (`apache/tika:26.04` doesn't). Other images: exact tags in `compose.yaml`, Renovate bumps them.
- **No `PAPERLESS_API_KEY`**: not a real setting. API tokens live in the UI (revoke any old one there).
