# forgejo

Self-hosted git (rootless Forgejo, SQLite) holding this repo; every Repository
stack is deployed from it.

**Setting it up: [guide 04, Forgejo: the NAS deploys from git](../../docs/guides/04-forgejo-gitops.md).**
This page is the reference.

|            |                                                                                                                                                 |
| ---------- | ----------------------------------------------------------------------------------------------------------------------------------------------- |
| Reached at | `https://forgejo.<internal-domain>` (login: OIDC, group `infra-admins`); `http://<nas-ip>:3000`; SSH `<nas-ip>:222`                             |
| Data       | `FORGEJO_DATA_ROOT` → `/var/lib/gitea`: repos, SQLite DB, LFS, attachments, `custom/conf/app.ini`. Backup: the `forgejo-dump` job only          |
| Needs      | `proxy` network (its OIDC calls to `auth.<internal-domain>`); [authelia](../authelia/README.md) for SSO                                         |
| Used by    | Portainer (every Repository stack), [forgejo-runner](../forgejo-runner/README.md), the scheduler's clone, [edge](../edge/README.md) (its route) |
| Config     | `.env.example` (every variable)                                                                                                                 |

## Gotchas

- **Web editor stack, not Repository:** Portainer can't pull Forgejo's stack
  from Forgejo while it's down. `compose.yaml` in git is the reference: paste
  every change, Renovate bumps included, into the Web editor by hand.
- **Keep port `3000` published:** Repository stacks clone
  `http://<nas-ip>:3000` (raw IP, [architecture](../../docs/architecture.md#deployment)),
  the runner uses `127.0.0.1:3000`, Caddy uses `PROXY_GATEWAY_IP:3000`.
- **`user: "1000:1000"`** (the image's `git` user), not `PUID`/`PGID`: the
  data folder must be owned by UID 1000.
- **Backup = the dump only.** A raw copy of a running instance can be
  inconsistent; the dump is consistent. No dump job, no backup
  ([guide 10](../../docs/guides/10-backups-and-jobs.md), restore steps in
  [operations](../../docs/operations.md)).
- **`FORGEJO__service__DISABLE_REGISTRATION=true`:** no signup.
- **`FORGEJO__service__REQUIRE_SIGNIN_VIEW=true`:** nothing is visible signed
  out, `/explore` and user pages included. A link opened without a session (from
  ntfy, on a phone) goes to the login page and back, not to a 404 on a private repo.
  Git and the API still work with a token or deploy key.
- **OIDC source name `authelia`:** it is part of the callback URL
  (`/user/oauth2/authelia/callback`) registered in Authelia.
- **Data on its own shared folder** (`<nas-volume-path>/forgejo/data`), so
  Hyper Backup can select it; `docker/` holds config only.
- **Image** pinned in `compose.yaml`; read Forgejo's release notes after a long gap.
