# Forgejo: the NAS deploys from git

> **Needs:** [01 Synology prep](01-synology-prep.md), [02 Portainer](02-portainer.md) · **Optional:** [03 HTTPS](03-https-letsencrypt.md) (hostname), [05 Authelia](05-sso-authelia.md) (SSO) · **Used by:** every Repository stack, [10 Backups and jobs](10-backups-and-jobs.md) (the scheduler's clone)
> **Reference:** [`stacks/forgejo/README.md`](../../stacks/forgejo/README.md), [`stacks/forgejo-runner/README.md`](../../stacks/forgejo-runner/README.md) · **Why it is built this way:** [architecture](../architecture.md#git-hosting)

## What you get

This repo hosted on the NAS, Portainer deploying every stack from it, and
weekly Renovate PRs for image updates, checked by CI.

```
laptop ──git push──▶ Forgejo (NAS, :3000 / :222)
                        │  ├─ Actions runner ── checks on every push, Renovate weekly ──▶ PRs
                        │  └─ scheduler clone (pull every 15 min, [10])
                        ▼
                    Portainer ── "Pull and redeploy" ──▶ stacks
```

- **Self-hosted, not GitHub:** Portainer CE authenticates private git sources by
  token only, and the toolchain stays on the NAS. Forgejo over Gitea for its
  non-profit governance.
- **Without Forgejo:** a fork on GitHub works as Portainer's git source too
  (public: no authentication). Skip steps 1–3; Renovate then runs as the
  GitHub app instead of step 5.

## Before you start

| What                        | Example                    | How                                                                                      |
| --------------------------- | -------------------------- | ---------------------------------------------------------------------------------------- |
| Portainer                   | `https://192.0.2.10:9443`  | [02 Portainer](02-portainer.md).                                                         |
| A shared folder for Forgejo | `/volume1/forgejo/data`    | A real shared folder ([01](01-synology-prep.md), step 3), so Hyper Backup can select it. |
| Free host ports             | `3000` (HTTP), `222` (SSH) | DSM's own SSH keeps port 22.                                                             |
| A clone of this repo        | on your laptop             | `git clone https://github.com/<you>/homelab.git`.                                        |

## Steps

### 1. Deploy Forgejo

Forgejo cannot be deployed from itself, so it is a **Web editor** stack:

```bash
sudo chown -R 1000:1000 /volume1/forgejo/data     # the image's `git` user
```

Portainer → Stacks → Add stack → **Web editor**, name `forgejo`, paste
[`stacks/forgejo/compose.yaml`](../../stacks/forgejo/compose.yaml), variable
`FORGEJO_DATA_ROOT=/volume1/forgejo/data` → Deploy.

**Check:** `curl -s http://192.0.2.10:3000/api/v1/version` → `{"version":"…"}`.

### 2. First run and the repo

1. `http://192.0.2.10:3000`: the install page. Keep SQLite; set the server
   domain and base URL to the address users will use
   (`https://forgejo.home.example.com/` with [03](03-https-letsencrypt.md),
   else `http://192.0.2.10:3000/`); create the admin. Registration is off
   (`DISABLE_REGISTRATION`).
2. Create the repo `<owner>/homelab` (empty) and push:
   ```bash
   git remote add forgejo http://192.0.2.10:3000/<owner>/homelab.git
   git push forgejo main
   ```
3. Settings → Applications → **access token** with `read:repository`, for
   Portainer.

**Check:** `http://192.0.2.10:3000/<owner>/homelab` shows the files.

### 3. Deploy the other stacks from Forgejo

Portainer → Add stack → **Repository** ([02](02-portainer.md), step 3) with:

- URL `http://192.0.2.10:3000/<owner>/homelab.git`: the **raw IP**, never
  the hostname. Portainer must reach it before Caddy and DNS work, and after
  they break.
- Authentication: the Forgejo user and the token from step 2.

**Check:** a Repository stack deploys and shows the commit it deployed.

### 4. The update loop

1. Change a stack on the laptop, commit, `git push forgejo main`.
2. Portainer → the stack → **Pull and redeploy** (or automatic polling,
   [02](02-portainer.md), step 4).
3. Forgejo's own stack is the exception: paste the new `compose.yaml` into the
   Web editor by hand, Renovate bumps included.

**Check:** after a redeploy the stack's commit matches `git rev-parse --short HEAD`.

### 5. CI: runner, checks, Renovate (optional)

The runner executes `.forgejo/workflows/`: `checks.yaml` on every push
(gitleaks, the no-real-values check, shellcheck, tests) and `renovate.yaml`
every Monday at 06:00.

1. Register the runner **in the Forgejo container** (not with a web UI token):
   ```bash
   openssl rand -hex 20                                  # the secret; keep it
   sudo docker exec forgejo forgejo forgejo-cli actions register \
     --secret <secret> --scope "" --labels docker --name nas-runner
   ```
   It prints a UUID derived from the secret; keep both.
2. Write `/volume1/docker/forgejo-runner/data/config.yaml` with the UUID and
   secret: the template is in
   [`stacks/forgejo-runner/README.md`](../../stacks/forgejo-runner/README.md#configyaml).
3. Deploy `stacks/forgejo-runner` as a Repository stack,
   `RUNNER_DATA_ROOT=/volume1/docker/forgejo-runner/data`.
4. Repo → Settings → Actions → **Secrets** (not Variables):

   | Secret                      | Value                                                         |
   | --------------------------- | ------------------------------------------------------------- |
   | `RENOVATE_TOKEN`            | a Forgejo token of a user who may push branches and open PRs  |
   | `RENOVATE_GITHUB_COM_TOKEN` | a GitHub token with **no scopes**: release notes in PR bodies |
   | `PRIVATE_PATTERNS`          | your `.private-patterns` file, for the no-real-values check   |

5. In `.forgejo/workflows/renovate.yaml`, set `RENOVATE_REPOSITORIES` to your
   `<owner>/homelab`.

**Check:** Site Administration → Actions → Runners shows `nas-runner`
**Idle**; a push runs `checks` green; Actions → renovate → Run workflow opens
a "Dependency Dashboard" issue.

### 6. Login through Authelia (optional)

With [05 Authelia](05-sso-authelia.md): Site Administration → Authentication
Sources → Add, type OAuth2, provider OpenID Connect, **name `authelia`** (it
is part of the callback URL `/user/oauth2/authelia/callback`), client ID
`forgejo` and its secret, discovery URL
`https://auth.home.example.com/.well-known/openid-configuration`. Only the
`infra-admins` group gets in.

**Check:** the login page offers "Sign in with authelia" and it works for an
`infra-admins` member.

## Troubleshooting

| Symptom                                                                     | Cause → fix                                                                                                                                                                                    |
| --------------------------------------------------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Forgejo: `permission denied` on `/var/lib/gitea`                            | Data folder not owned by `1000:1000`: `chown -R` it (step 1).                                                                                                                                  |
| Portainer can't clone after a Caddy or DNS problem                          | The stack uses the hostname. Use `http://<nas-ip>:3000/…`.                                                                                                                                     |
| `forgejo-runner register --token …` → `runner registration token not found` | Deprecated flow. Register with `forgejo-cli actions register` in the Forgejo container (step 5).                                                                                               |
| Runner shows **Offline** after editing `config.yaml`                        | Read only at start: `docker restart forgejo-runner`.                                                                                                                                           |
| Renovate fails like an auth error, or clones time out                       | Job containers are not on `proxy` (`container.network: proxy` in `config.yaml`), so `http://forgejo:3000` doesn't resolve. A hostname would hit Caddy's macvlan IP, unreachable from the host. |
| A workflow step: `docker: command not found`                                | Bare `runs-on: docker` jobs run in `node:22-bookworm`. Use a job-level `container:` image.                                                                                                     |
| Secret named `GITHUB_…` rejected                                            | Forgejo reserves `GITHUB_`, `GITEA_`, `FORGEJO_` prefixes. Hence `RENOVATE_GITHUB_COM_TOKEN`.                                                                                                  |
| Renovate found nothing, yet updates exist                                   | Look at the Dependency Dashboard issue: rate-limited or awaiting approval (major updates wait for it).                                                                                         |
| OIDC: `redirect_uri` mismatch                                               | The authentication source is not named `authelia`.                                                                                                                                             |

## Next

- [05 Authelia](05-sso-authelia.md): SSO for Forgejo and Portainer.
- [10 Backups and jobs](10-backups-and-jobs.md): the daily `forgejo dump`
  (the only valid Forgejo backup) and the scheduler, which clones this repo
  with a read-only deploy key.
