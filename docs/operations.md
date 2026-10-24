# Operations

The maintainer's page: rules for this repo, updating, restoring, and the
conventions a new stack follows. Setting things up is in the
[guides](../README.md#guides); backups and the scheduler in
[guide 10](guides/10-backups-and-jobs.md).

## Rules

- **Secrets never go in git**, not even encrypted. App secrets: Portainer's
  per-stack environment variables. Authelia: files on the NAS. CI: Forgejo
  Actions **Secrets**.
- **No real values in tracked files.** Use the placeholders below; real values
  live in the gitignored `CLAUDE.local.md` and
  `PRIVATE-real-values-do-not-publish/`. The tree is publishable as is.
- **GitHub gets snapshots, not the history.** Forgejo holds the working
  history, Renovate's commits included; `tools/publish-github.sh` adds one
  commit per publish to the public copy, authored by the public identity,
  after the real-values check and gitleaks pass. Never `git push` to GitHub
  directly: it would publish Renovate's and the merges' commits.
- **Images are pinned** to a version tag (exact where upstream publishes one;
  `postgres:17`, `redis:8`, Forgejo `17.0` and nginx `1.31` float within that
  version). Renovate runs daily: minor and patch
  updates merge themselves once CI passes; majors (and Forgejo, Portainer)
  wait for approval on its Dependency Dashboard, and ntfy tells you
  ([guide 04](guides/04-forgejo-gitops.md#5-ci-runner-checks-renovate-optional),
  [guide 11](guides/11-notifications.md)).

| Real value              | Placeholder in reference pages          | Example in guides   |
| ----------------------- | --------------------------------------- | ------------------- |
| Domain for the apps     | `<internal-domain>` (`INTERNAL_DOMAIN`) | `home.example.com`  |
| NAS hostname / IP       | `<nas-host>` / `<nas-ip>`               | `nas`, `192.0.2.10` |
| NAS volume paths        | `<nas-volume-path>`                     | `/volume1`          |
| PUID / PGID             | `<uid>` / `<gid>`                       | `<uid>` / `<gid>`   |
| Email addresses         | `admin@example.com`                     | `admin@example.com` |
| Tokens, passwords, keys | name only in `.env.example`             | `<token>`           |

## Checks

```bash
pip install pre-commit && pre-commit install
cp .private-patterns.example .private-patterns   # then add your real domain, names, volume…
```

Every commit runs **gitleaks** (credentials) and `scripts/check-no-real-values.sh`
(domains, IPs, paths, names; site patterns from the untracked
`.private-patterns`). CI (`.forgejo/workflows/checks.yaml`) repeats both on
every push, with the patterns from the `PRIVATE_PATTERNS` Actions secret, plus
shellcheck, the scheduler tests, Prettier, and paperless-tarmed's ruff and
pytest.

## Updating a stack

Every Repository stack has Portainer's **GitOps updates** on: polling every
5 minutes, _Force redeployment_ off. So:

1. Change it here and push to Forgejo; or Renovate commits a minor update
   itself; or you approve a major on the Dependency Dashboard and merge its PR.
2. Within 5 minutes Portainer sees the new commit and redeploys the stacks
   whose files changed. Nothing to click. A merge therefore restarts
   containers at any hour.

Exceptions:

- **`forgejo`** is a Web editor stack: paste the new `compose.yaml` into
  Portainer by hand, Renovate bumps included.
- **`paperless-tarmed`**: Portainer never builds its image (it rebuilt
  unreliably). The poll redeploys on the push, before the `build-images` job
  has built the new image (within 15 minutes), so the old one keeps running;
  the dashboard shows amber "build pending", then "redeploy needed". Then **Pull and redeploy** by
  hand.
- **`edge`** builds in Portainer (`pull_policy: build`), and a redeploy may
  still reuse the old image. After a `Dockerfile` change, check its container
  was recreated (`docker inspect --format '{{.Created}}' <caddy-container>`);
  if not, Pull and redeploy by hand. A Caddyfile change needs nothing: Caddy
  reloads it itself (`--watch`).
- **`authelia`**: a redeploy does not restart it when only
  `config/configuration.yml` changed, and it reads the file at start. After an
  access-rule or OIDC client change, restart its container (Portainer →
  Containers → Restart).
- Not the webhook instead of polling: Forgejo would have to reach
  `portainer:9443` over `proxy`, never a public hostname.

## Restore

Restore into a scratch location first; nothing counts as backed up until it
has been restored once.

- **Forgejo** (from a dump):
  1. Stop the stack, unzip the dump.
  2. Recreate `FORGEJO_DATA_ROOT` owned by `1000:1000`.
  3. Copy `data/` into it, `repos/` to the repository root named in
     `app.ini`, `app.ini` to `custom/conf/app.ini`.
  4. `sqlite3 <[database] PATH from app.ini> < forgejo-db.sql`
  5. Start, then `forgejo admin regenerate hooks` in the container.
- **Authelia**: restore the secrets folder, redeploy; users log in again.
- **Paperless**: restore the share, or `document_importer` the `export/` folder into a fresh install.

**After a total loss**, rebuild in [install-everything](install-everything.md)'s order, restoring each
stack's data before deploying it; Forgejo's dump goes back with [guide 04](guides/04-forgejo-gitops.md). If
Forgejo cannot be restored, any clone of this repo works: deploy the stacks as
Web editor stacks from it until Forgejo is back.

## Container logs

How this was found, and how to measure it again:
[slow disk writes](slow-disk-writes.md).

Every service logs with Docker's `local` driver, 3 files of 10 MB, set in
each compose file. Container Manager's default, the `db` driver, writes each
log line to a SQLite file (`<docker root>/containers/<id>/log.db`). Measured
on 2026-10-24 with the NAS otherwise idle: about 210 writes/s on the RAID 6
volume, 90 % busy, from a few KB of log text per container every 30 s; a
4 KB forced write took 0.1–1.3 s, and Authelia's SQLite failed with
`database is locked`. With every container on `local`, the same 4 KB write
took 0.05–0.11 s. `docker logs` and Portainer show `local` logs; DSM's
Container Manager log view does not. Portainer itself, started with
`docker run`, needs `--log-driver local` when recreated.

## Conventions (for a new stack)

- Files: `compose.yaml`, `.env.example` (every `${VAR}`, no real values),
  `README.md`: the reference page (summary table with _Used by_ → a bold
  "Setting it up: guide NN" line → Gotchas).
- A guide per stack or topic in `docs/guides/`, same shape as the others:
  header (_Needs_ / _Optional_ / _Used by_ / _Reference_) → What you get →
  Before you start → Steps, each ending with a **Check** → Troubleshooting →
  Next. Concrete examples (`home.example.com`, `192.0.2.x`, `/volume1`), never
  real values. Add it to the guide table in the root `README.md`.
- Diagrams only where a flow needs a picture; otherwise a table. Drawn in
  Excalidraw and saved as `docs/images/<name>.excalidraw.svg` (export with
  "Embed scene", so the file stays editable), white background.
- Markdown is formatted with Prettier (`.prettierrc.yaml`: aligned tables, line
  breaks kept); pre-commit fixes it, CI checks it.
- Every service: `restart: unless-stopped`,
  `security_opt: [no-new-privileges:true]`, `cap_drop: [ALL]` plus only the commented
  `cap_add` it needs, a `deploy.resources.limits.memory` (exception:
  `forgejo-runner`, which has docker.sock, has no `cap_drop`).
- Every service: `logging: *logging`, the `x-logging` block at the top of each
  compose file (see Container logs).
- Images pinned to a version tag, exact where upstream publishes one (Renovate
  updates them). A locally built image
  is built by the `build-images` job (`scripts/jobs.conf`), not by Portainer:
  compose gets `pull_policy: never` and no `build:` (as `paperless-tarmed`).
- `container_name` only when a host command uses it (`forgejo`,
  `forgejo-runner`, `paperless-tarmed`); containers reach each other by
  service name.
- User IDs follow the image's own convention: `PUID`/`PGID` (linuxserver),
  `PAPERLESS_UID`/`PAPERLESS_GID`, a fixed `user:` where on-disk ownership
  requires it (Forgejo `1000:1000`).
