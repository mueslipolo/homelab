# immich

The family photo library: Immich (server, machine learning) with its own
Postgres and Valkey. The phones back up to it with the Immich app.

**Setting it up: [guide 12, Immich](../../docs/guides/12-immich.md).**
This page is the reference.

|            |                                                                                                                                                                                    |
| ---------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Reached at | `https://immich.<internal-domain>` (Caddy → `immich-server:2283`), web and phone app. Login: OIDC via Authelia, password login kept as a fallback                                  |
| Data       | `IMMICH_UPLOAD_LOCATION` → `upload/`, `library/`, `profile/` (originals: **back up**), `backups/` (Immich's database dumps: **back up**), `thumbs/`, `encoded-video/` (made again) |
|            | `IMMICH_DOCKER_ROOT` → `postgres/` (the live database; restored from `backups/`, not copied as files)                                                                              |
|            | Docker volume `model-cache` (downloaded models, disposable)                                                                                                                        |
| Needs      | `proxy` network (`immich-server` only; private `immich_net`), [edge](../edge/README.md), [authelia](../authelia/README.md) (client `immich`)                                       |
| Used by    | the phones (Immich app); later the kairos migration (Immich's API, from a laptop)                                                                                                  |
| Config     | `.env.example` (every variable); OAuth, jobs and transcoding in Immich's Administration > Settings (stored in its database)                                                        |

## Gotchas

- **Settings live in Immich's database, not in git.** `IMMICH_CONFIG_FILE`
  exists, but it has no variable substitution (the OIDC secret would be in the
  file) and it locks every other setting in the web UI. So OAuth, job
  concurrency, the dump schedule and transcoding are set in the UI, and the
  database dumps keep them.
- **OIDC must match Authelia.** Client `immich`, `client_secret_post` (Immich's
  default), scope `openid email profile`. Authelia's `redirect_uris`:
  `https://immich.<internal-domain>/auth/login` (web), `/user-settings`
  (linking an account by hand), `app.immich:///oauth-callback` (the phone
  app), and `/api/oauth/mobile-redirect` (only used if Immich's _Mobile
  Redirect URI Override_ is turned on). The secret in Immich is the plaintext
  Random Password, never the Digest.
- **One Immich account per person, matched by email.** On an OIDC login Immich
  looks for the account linked to that Authelia user (`sub`), then for an
  account with the same email, which it links; otherwise it creates one
  (_Auto Register_). So each LDAP user needs an email address, and the admin
  account created at first start should use the admin's LDAP email: their
  first OIDC login then lands in it.
- **No `import authelia` in Caddy.** The phone app calls the API directly with
  its own token; a forward-auth gate would break it. Immich does its own login
  (OIDC or password).
- **Memory.** Limits are ceilings, not reservations: server 4 GB (~3 GB seen
  while importing), machine learning 2 GB (~0.5 GB idle, models unloaded after
  5 idle minutes), Postgres 2 GB (Immich's stated minimum under a limit; ~0.4
  GB seen), Valkey 128 MB. Idle the stack uses about 1.5 GB; the first phone
  backups and their ML jobs use the most.
- **CPU.** The J4125 has 4 slow cores, shared with Paperless's OCR. ML takes
  at most 2 requests at once (`MACHINE_LEARNING_REQUEST_THREADS`), and the
  guide sets Smart Search and Face Detection to 1 job each (defaults 2). The
  first backup's thumbnails and ML run for hours; that is expected.
- **Hardware acceleration.** ML runs on the CPU: OpenVINO targets Iris Xe and
  Arc, and Immich warns that older integrated GPUs and low-RAM servers have
  issues. Video transcoding can use Quick Sync on the UHD 600 (the commented
  `devices:`), as Jellyfin does; off by default.
- **`DAC_OVERRIDE` on `immich-server`**: it runs as root and writes into a DSM
  shared folder. **Valkey** keeps `CHOWN, SETUID, SETGID` (its entrypoint drops
  to its own user); **Postgres** the same five capabilities as Paperless's.
- **Service names are part of the configuration.** `immich-machine-learning`
  is Immich's default ML URL; `DB_HOSTNAME`/`REDIS_HOSTNAME` point at
  `immich-database`/`immich-redis`, names nothing else on `proxy` uses.
- **Updating.** Renovate bumps `immich-server` and `immich-machine-learning`
  together (group `immich`), in one PR: read Immich's release notes before
  merging it, some ask for a step before updating. It leaves Immich's Postgres image
  alone: change it only when an Immich release's `docker-compose.yml` does.
- **Backups.** Immich dumps its database itself (Administration > Settings >
  Database Dump Settings), into `IMMICH_UPLOAD_LOCATION/backups`; the guide moves it
  to 20:20, before Hyper Backup. Hyper Backup takes `upload/`, `library/`,
  `profile/` and `backups/`. A dump alone holds no photos; the files alone
  are unusable without a dump. `IMMICH_DUMP_DIR` in `jobs.env` puts the dump's
  age on the dashboard.
- **Never edit files under `IMMICH_UPLOAD_LOCATION` by hand.** Immich does not
  rescan its own storage: a moved or deleted file becomes a broken asset.
