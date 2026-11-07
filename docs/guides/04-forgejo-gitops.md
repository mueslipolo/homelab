# Forgejo: the NAS deploys from git

> **Needs:** [01 Synology prep](01-synology-prep.md), [02 Dockhand](02-dockhand.md) · **Optional:** [03 HTTPS](03-https-letsencrypt.md) (hostname), [05 Authelia](05-sso-authelia.md) (SSO) · **Used by:** every git stack, [10 Backups and jobs](10-backups-and-jobs.md) (the scheduler's clone)
> **Reference:** [`stacks/forgejo/README.md`](../../stacks/forgejo/README.md), [`stacks/forgejo-runner/README.md`](../../stacks/forgejo-runner/README.md) · **Why it is built this way:** [architecture](../architecture.md#git-hosting)

## What you get

This repo hosted on the NAS, Dockhand deploying every stack from it within
seconds of a push, and
Renovate checking image updates daily: each one opens a PR that you merge,
and each new PR reaches your phone ([11](11-notifications.md)).

Who reads the repo, and when:

| Reader         | Trigger                                                       | Does                                             |
| -------------- | ------------------------------------------------------------- | ------------------------------------------------ |
| Actions runner | every push; Renovate daily                                    | runs the checks; Renovate opens image-update PRs |
| Dockhand       | every push (a webhook per stack); each stack's scheduled sync | redeploys the stacks whose files changed         |
| Scheduler      | every 15 min ([10](10-backups-and-jobs.md))                   | pulls its clone, then runs the due jobs as root  |

- **Self-hosted, not GitHub:** the toolchain stays on the NAS, and a deploy
  needs no internet. Forgejo over Gitea for its non-profit governance.
- **Without Forgejo:** a fork on GitHub works as Dockhand's git source too
  (public: no authentication). Skip steps 1–3 and the webhooks (scheduled sync
  only); Renovate then runs as the GitHub app instead of step 5.

## Before you start

| What                        | Example                    | How                                                                                      |
| --------------------------- | -------------------------- | ---------------------------------------------------------------------------------------- |
| Dockhand                    | `http://192.0.2.10:3010`   | [02 Dockhand](02-dockhand.md), steps 1–2.                                                |
| A shared folder for Forgejo | `/volume1/forgejo/data`    | A real shared folder ([01](01-synology-prep.md), step 3), so Hyper Backup can select it. |
| Free host ports             | `3000` (HTTP), `222` (SSH) | DSM's own SSH keeps port 22.                                                             |
| A clone of this repo        | on your laptop             | `git clone https://github.com/<you>/homelab.git`.                                        |

## Steps

### 1. Deploy Forgejo

Forgejo cannot be deployed from itself, so it is a **pasted** stack:

```bash
sudo chown -R 1000:1000 /volume1/forgejo/data     # the image's `git` user
```

Dockhand → Stacks → **Create**, name `forgejo`, paste
[`stacks/forgejo/compose.yaml`](../../stacks/forgejo/compose.yaml), variable
`FORGEJO_DATA_ROOT=/volume1/forgejo/data` → Deploy.

**Check:** `curl -s http://192.0.2.10:3000/api/healthz` → `"status": "pass"`.

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
   Dockhand.

**Check:** `http://192.0.2.10:3000/<owner>/homelab` shows the files.

### 3. Deploy the other stacks from Forgejo

Dockhand → Settings → Git ([02](02-dockhand.md), step 3):

- Credential: the Forgejo user and the token from step 2.
- Repository `http://192.0.2.10:3000/<owner>/homelab.git`: the **raw IP**,
  never the hostname. Dockhand must reach it before Caddy and DNS work, and
  after they break.

Then each stack: Stacks → **from Git** ([02](02-dockhand.md), step 4), and
its webhook ([02](02-dockhand.md), step 5): Forgejo calls
`http://dockhand:3000` over `proxy`, which `stacks/forgejo/compose.yaml`
allows (`FORGEJO__webhook__ALLOWED_HOST_LIST=dockhand`).

**Check:** a git stack deploys and shows the commit it deployed; its
webhook's **Test delivery** answers `200`.

### 4. The update loop

1. Change a stack on the laptop, commit, `git push forgejo main`.
2. Forgejo calls each stack's webhook, and Dockhand redeploys the stacks
   whose files changed, within seconds; without a webhook, at the stack's
   scheduled sync ([02](02-dockhand.md), step 4). **Deploy** does it at once,
   by hand.
3. Forgejo's own stack is the exception: paste the new `compose.yaml` into
   Dockhand by hand, Renovate bumps included. So is Dockhand itself, started
   by hand ([02](02-dockhand.md), step 2).

**Check:** after a redeploy the stack's commit matches `git rev-parse --short HEAD`.

### 5. CI: runner, checks, Renovate (optional)

The runner executes `.forgejo/workflows/`: `checks.yaml` on every push
(gitleaks, the no-real-values check, shellcheck, tests) and `renovate.yaml`
daily at 06:00.

What Renovate does with an update (`renovate.json`):

| Update               | What happens                                                                         |
| -------------------- | ------------------------------------------------------------------------------------ |
| any                  | opens a PR, `checks` runs on its branch, the `renovate-notify` job pushes it to ntfy |
| you merge it         | Dockhand deploys it                                                                  |
| Forgejo, Dockhand    | merging deploys nothing: update them by hand after the merge ([02](02-dockhand.md))  |
| Immich server and ML | one PR for both; read Immich's release notes before merging ([12](12-immich.md))     |

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
3. Deploy `stacks/forgejo-runner` as a git stack,
   `RUNNER_DATA_ROOT=/volume1/docker/forgejo-runner/data`.
4. Repo → Settings → Actions → **Secrets** (not Variables):

   | Secret                      | Value                                                         |
   | --------------------------- | ------------------------------------------------------------- |
   | `RENOVATE_TOKEN`            | a Forgejo token of a user who may push branches and open PRs  |
   | `RENOVATE_GITHUB_COM_TOKEN` | a GitHub token with **no scopes**: release notes in PR bodies |
   | `PRIVATE_PATTERNS`          | your `.private-patterns` file, for the no-real-values check   |

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
| Dockhand can't clone after a Caddy or DNS problem                           | The repository uses the hostname. Use `http://<nas-ip>:3000/…`.                                                                                                                                |
| `forgejo-runner register --token …` → `runner registration token not found` | Deprecated flow. Register with `forgejo-cli actions register` in the Forgejo container (step 5).                                                                                               |
| Runner shows **Offline** after editing `config.yaml`                        | Read only at start: `docker restart forgejo-runner`.                                                                                                                                           |
| Renovate fails like an auth error, or clones time out                       | Job containers are not on `proxy` (`container.network: proxy` in `config.yaml`), so `http://forgejo:3000` doesn't resolve. A hostname would hit Caddy's macvlan IP, unreachable from the host. |
| A workflow step: `docker: command not found`                                | Bare `runs-on: docker` jobs run in `node:22-bookworm`. Use a job-level `container:` image.                                                                                                     |
| Secret named `GITHUB_…` rejected                                            | Forgejo reserves `GITHUB_`, `GITEA_`, `FORGEJO_` prefixes. Hence `RENOVATE_GITHUB_COM_TOKEN`.                                                                                                  |
| Renovate found nothing, yet updates exist                                   | Look at the Dependency Dashboard issue: rate-limited, or errored.                                                                                                                              |
| OIDC: `redirect_uri` mismatch                                               | The authentication source is not named `authelia`.                                                                                                                                             |

## Next

- [05 Authelia](05-sso-authelia.md): SSO for Forgejo and Dockhand.
- [10 Backups and jobs](10-backups-and-jobs.md): the daily `forgejo dump`
  and the scheduler, which clones this repo
  with a read-only deploy key.
