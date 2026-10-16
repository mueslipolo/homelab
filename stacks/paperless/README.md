# paperless

Document archive: paperless-ngx with postgres, redis, gotenberg and tika.

**Setting it up: [guide 07, Paperless-ngx](../../docs/guides/07-paperless.md).**
This page is the reference.

|            |                                                                                                                           |
| ---------- | ------------------------------------------------------------------------------------------------------------------------- |
| Reached at | `https://paperless.<internal-domain>` (Caddy → `webserver:8000`). Login: OIDC via Authelia, local login as fallback       |
| Data       | `PAPERLESS_DOCKER_ROOT` → `db/`, `redis/` (state, disposable)                                                             |
|            | `PAPERLESS_DATA_ROOT` → `data/`, `media/`, `export/`, `scripts/` (documents: back up)                                     |
|            | `PAPERLESS_INBOX_PATH` → consume folder                                                                                   |
| Needs      | `proxy` network (`webserver` only; private `paperless_net`), [edge](../edge/README.md), [authelia](../authelia/README.md) |
| Used by    | [paperless-tarmed](../paperless-tarmed/README.md) (API over `proxy`); the `paperless-export` job                          |
| Config     | `.env.example` (every variable); time zone, OCR languages, file name format in `compose.yaml`                             |

## Gotchas

- **OIDC must match Authelia.** `provider_id` `authelia` fixes the callback
  `https://paperless.<internal-domain>/accounts/oidc/authelia/login/callback/`
  (= Authelia's `redirect_uris`); `client_id` `paperless`; the secret here is
  the plaintext Random Password, never the Digest. Mistakes fail as auth
  errors, not config errors.
- **"A user with that username already exists"** is expected: django-allauth
  won't auto-link OIDC to a local account. Link it once from the local login:
  My Profile → Connect new social account.
- **Keep `webserver` in `PAPERLESS_ALLOWED_HOSTS`.** paperless-tarmed calls
  `http://webserver:8000` over `proxy`; without it that app gets a bodyless 400
  (DisallowedHost), and nothing is logged here.
- **`PAPERLESS_ALLOWED_HOSTS` takes bare hostnames; `PAPERLESS_URL` keeps `https://`.**
- **`broker` runs as `PAPERLESS_UID:PAPERLESS_GID`**, so it must own `redis/`.
- **Capabilities** on top of `cap_drop: [ALL]`: `webserver`
  `CHOWN, SETUID, SETGID, DAC_OVERRIDE` (UID remap); `db` adds `FOWNER` (postgres
  root-then-drop).
- **`webserver` waits** for healthy `db` and `broker`.
- **`gotenberg`'s `command:`** (JavaScript off, file allow-list) is deliberate
  hardening.
- **`tika` is pinned by digest**: it has no date-style tag. Other images: exact
  tags, bumped by Renovate.
- **No `PAPERLESS_API_KEY`**: not a real setting. API tokens live in the UI.
