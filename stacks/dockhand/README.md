# dockhand

The web UI that deploys every other stack from git:
each `stacks/<name>/compose.yaml` is a Dockhand **git stack** synced from
Forgejo, with its variables (secrets included) kept in Dockhand, out of git.

**Setting it up: [guide 02, Dockhand](../../docs/guides/02-dockhand.md).**
This page is the reference.

|            |                                                                                                                                                                                                               |
| ---------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Reached at | `https://dockhand.<internal-domain>` (Caddy → `dockhand:3000`; login: OIDC, group `infra-admins`); `http://127.0.0.1:3010` on the NAS (the scheduler); `http://dockhand:3000` on `proxy` (Forgejo's webhooks) |
| Data       | `DOCKHAND_DATA_ROOT`: `db/` (SQLite: users, git credential, stacks, their variables), `.encryption_key`, `git-repos/` (clones), `stacks/` (each stack's deployed files). Backup: Hyper Backup of the folder   |
| Needs      | `docker.sock`; `proxy` network; [forgejo](../forgejo/README.md) (the git source); [edge](../edge/README.md) for its hostname; [authelia](../authelia/README.md) for SSO                                       |
| Used by    | every git stack; the scheduler: `dashboard-status` (stack list) and `redeploy-images` (`DOCKHAND_TOKEN`, [guide 10](../../docs/guides/10-backups-and-jobs.md))                                                |
| Config     | `.env.example` (every variable); started by hand with `docker compose`, never as a stack of its own                                                                                                           |

## Gotchas

- **Started by hand:** it deploys everything else, so
  nothing deploys it. From the scheduler's clone, with `dockhand.env` next to
  `jobs.env`:
  `sudo docker compose -p dockhand --env-file <dockhand.env> -f stacks/dockhand/compose.yaml up -d`.
  Upgrades the same way after you merge its Renovate PR (like Forgejo's).
  Not Dockhand's self-update, not a git stack.
- **`-baseline` image:** the default build needs AVX2, which the DS920+'s
  Celeron lacks. On a CPU with AVX2 (`grep -c avx2 /proc/cpuinfo` > 0) the tag
  without `-baseline` works too.
- **Same data path inside and outside** (`DATA_DIR` = the mount): Dockhand runs
  `docker compose` in its container and the daemon resolves the paths it gets
  on the host. A different inside path breaks every `./…` bind mount
  (`./caddy`, `./config`, `./html`).
- **Its own login must be turned on, and is off on a fresh install:** until
  then the UI and the API answer anyone who reaches them (Settings →
  Authentication). Turn it on at the first setup, before anything else
  (guide 02, step 2): `DOCKHAND_HOST_IP=0.0.0.0` puts it on the LAN until
  Caddy serves it, since DSM's SSH server refuses the port forwarding a tunnel
  would need. API tokens need it
  too. Login is OIDC through Authelia (`dockhand` client,
  `admins_only`), not Caddy's `forward_auth` gate: both would mean two logins
  (Mealie's reason too). Keep one local admin as a way in when Authelia is down.
- **Free edition: every user is an admin.** Roles are a paid feature, so
  Authelia's `admins_only` policy is the only thing that keeps family accounts
  out.
- **Back up the data folder whole:** `.encryption_key` decrypts the git
  credential and every secret variable in `db/`; one without the other is
  useless. Anyone with the backup has the secrets.
- **Each git stack syncs on its own schedule** ("Enable scheduled sync", not
  the containers' "Auto-update", which stays off): hourly at a minute of its
  own, or daily (`5 4 * * *`, `10 4 * * *`…) for a stack with a webhook. Each
  sync is a fresh (blobless) clone, then a deploy only when files under the
  stack's folder changed. A deploy is `docker compose up -d --remove-orphans`,
  without `--force-recreate`.
- **The stack's name is the compose project**, and what the dashboard and the
  scheduler match on: the form pre-fills the repository's name (`homelab`),
  change it to the folder's. The form refuses a name whose containers it sees
  running.
- **Never turn on "Re-pull images" for `paperless-tarmed`:** it
  runs `up --pull always`, which overrides `pull_policy: never` and fails on an
  image no registry has.
- **Never "Force redeploy"** on a stack: it redeploys and recreates on every
  sync.
- **Webhooks are per git stack** (`/api/git/stacks/<id>/webhook`, secret
  required): Forgejo calls `http://dockhand:3000/…` over `proxy` (type Gitea,
  `FORGEJO__webhook__ALLOWED_HOST_LIST=dockhand` in its compose).
  `/api/git/webhook/<id>` takes a _repository_ id and deploys the repository
  as one stack named after it: not for this repo.
- **`forgejo` is a pasted stack** (Stacks → Create), not a git stack: it is
  the git server. Dockhand's API lists only git stacks, so the dashboard shows
  its containers but not the stack.
- **API token:** Profile → API tokens, `dh_…`, with its user's rights (all of
  them, free edition). In `jobs.env` only, which stays root-only.
- **License:** Business Source License 1.1. Free for a personal home lab; each
  release becomes Apache 2.0 four years after it is published.
