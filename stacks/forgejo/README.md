# forgejo

Self-hosted git (rootless Forgejo, SQLite) hosting this repo; every Repository Stack pulls from it.

|            |                                                                                                                                          |
| ---------- | ---------------------------------------------------------------------------------------------------------------------------------------- |
| Reached at | `https://forgejo.<internal-domain>` — login: OIDC, `admins_only` (group `infra-admins`). Also `http://<nas-ip>:3000`, SSH `<nas-ip>:222` |
| Data       | `FORGEJO_DATA_ROOT` → `/var/lib/gitea`: repos, SQLite DB, LFS, attachments, `custom/conf/app.ini`                                        |
| Needs      | `proxy` network (its OIDC calls to `auth.<internal-domain>`); `authelia` for SSO                                                         |
| Config     | `.env.example` (every variable)                                                                                                          |

## Deploy

See `docs/INSTALL.md` for the install order (right after Portainer).

1. Make `FORGEJO_DATA_ROOT` its own DSM Shared Folder (e.g. `<nas-volume-path>/forgejo/data`, selectable in Hyper Backup):
   ```bash
   sudo chown -R 1000:1000 <nas-volume-path>/forgejo/data
   ```
2. Portainer → Stacks → Add stack → **Web editor**: paste `compose.yaml`, set `FORGEJO_DATA_ROOT` → Deploy.
3. Finish first-run setup at `http://<nas-ip>:3000`, push this repo. After `authelia`: Site Administration → Authentication Sources → OAuth2 source named `authelia` (matches the `/user/oauth2/authelia/callback` redirect), discovery `https://auth.<internal-domain>/.well-known/openid-configuration`, client `forgejo` + its Random Password.
4. Backups: the scheduler's `forgejo-dump` job (`scripts/jobs.conf`) runs `scripts/forgejo-dump.sh` daily once it is set up (`docs/OPERATIONS.md`).
5. **Check:** `curl -s http://<nas-ip>:3000/api/v1/version`.

## Gotchas

- **Web editor, not Repository:** Portainer can't pull Forgejo's stack from Forgejo while it's down. `compose.yaml` in git is the reference — paste every change (Renovate bumps too) into the Web editor by hand.
- **Keep port `3000` published:** Repository Stacks clone `http://<nas-ip>:3000` (raw IP, `docs/ARCHITECTURE.md`), the runner uses `127.0.0.1:3000`, Caddy uses `PROXY_GATEWAY_IP:3000`.
- **`user: "1000:1000"`** (the image's `git` user), not `PUID`/`PGID` — existing data is UID 1000.
- **Backup = the dump only.** A raw SQLite/repo copy restores only into the same Forgejo version. No dump job, no backup.
- **`FORGEJO__service__DISABLE_REGISTRATION=true`** — no signup.
- **Only `proxy`;** the old `forgejo_default` network was dropped as unused.
- **`compose.yaml` was rebuilt from `docker inspect`** (the original stack file was unretrievable) — verified.
- **Data moved** from `<nas-volume-path>/docker/forgejo-data`; `docker/` holds config only.
- **Image** pinned in `compose.yaml`; check Forgejo releases after a long gap.
