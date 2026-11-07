# Operations

The maintainer's page: rules for this repo, updating, restoring, and the
conventions a new stack follows. Setting things up is in the
[guides](../README.md#guides); backups and the scheduler in
[guide 10](guides/10-backups-and-jobs.md).

## Rules

- **Secrets never go in git**, not even encrypted. App secrets: Dockhand's
  per-stack variables, marked secret. Authelia: files on the NAS. CI: Forgejo
  Actions **Secrets**.
- **No real values in tracked files.** Use the placeholders below; real values
  live in the gitignored `CLAUDE.local.md` and
  `PRIVATE-real-values-do-not-publish/`. The tree is publishable as is.
- **GitHub gets snapshots, not the history** (this repo's own publishing; a
  fork can skip it). Forgejo holds the working
  history, Renovate's commits included; `dev/publish-github.sh` adds one
  commit per publish to the public copy, authored by the public identity,
  after the real-values check and gitleaks pass. Never `git push` to GitHub
  directly: it would publish Renovate's and the merges' commits.
- **Images are pinned** to a version tag (exact where upstream publishes one;
  `postgres:17`, `redis:8`, Forgejo `17.0` and nginx `1.31` float within that
  version). Renovate runs daily: minor and patch
  updates merge themselves once CI passes; majors (and Forgejo, Dockhand)
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

Every commit runs **gitleaks** (credentials) and `dev/check-no-real-values.sh`
(domains, IPs, paths, names; site patterns from the untracked
`.private-patterns`). CI (`.forgejo/workflows/checks.yaml`) repeats both on
every push, with the patterns from the `PRIVATE_PATTERNS` Actions secret, plus
shellcheck, the scheduler tests, Prettier, and paperless-tarmed's ruff and
pytest.

## Updating a stack

Every git stack in Dockhand has a Forgejo webhook and a scheduled sync (hourly
at its own minute, or daily for a stack with a webhook), _Force redeploy_ and
_Re-pull images_ off, **Deploy** for a redeploy by hand
([guide 02](guides/02-dockhand.md)). So:

1. Change it here and push to Forgejo; or Renovate commits a minor update
   itself; or you approve a major on the Dependency Dashboard and merge its PR.
2. Within seconds the webhook makes Dockhand redeploy the stacks whose files
   changed (without one, at the next scheduled sync). Nothing to click. A
   merge therefore restarts containers at any hour.

Exceptions:

- **`forgejo`** is a pasted stack: paste the new `compose.yaml` into
  Dockhand by hand, Renovate bumps included.
- **`dockhand`** itself is started by hand: after its Renovate PR, run its
  `docker compose … up -d` again ([guide 02](guides/02-dockhand.md), step 2).
- **Locally built images** (`paperless-tarmed`): Dockhand never builds them
  (rebuilds on redeploy were unreliable). The webhook redeploys on the push, before the new
  image exists, so the old one keeps running. The `build-images` job builds
  the image, and the `redeploy-images` job, in the same tick, redeploys each
  stack whose container does not run the image under its tag, through
  Dockhand's API (`DOCKHAND_TOKEN`). Nothing to click: a push is live within
  15 minutes. A stack pinned to another tag (`PAPERLESS_TARMED_IMAGE` set to
  another version) is left alone, and a container running a
  `docker exec` waits for a later tick. A deploy that failed because the image
  did not exist yet (Dockhand shows the stack's sync in error, the old
  container still running) is retried once it does.
- **`edge`** builds on deploy (`pull_policy: build`), and a redeploy may
  still reuse the old image. After a `Dockerfile` change, check its container
  was recreated (`docker inspect --format '{{.Created}}' <caddy-container>`);
  if not, Deploy it by hand. A Caddyfile change needs nothing: Caddy
  reloads it itself (`--watch`).
- **`authelia`**: a redeploy does not restart it when only
  `config/configuration.yml` changed, and it reads the file at start. After an
  access-rule or OIDC client change, restart its container (Dockhand →
  Containers → Restart).

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
- **Dockhand**: restore its data folder (`db/` and `.encryption_key`
  together), then start it ([guide 02](guides/02-dockhand.md), step 2): every
  stack's record and variables come back with it.
- **Authelia**: restore the secrets folder, redeploy; users log in again.
- **Paperless**: restore the share, or `document_importer` the `export/` folder into a fresh install.

**After a total loss**, rebuild in [install-everything](install-everything.md)'s order, restoring each
stack's data before deploying it; Forgejo's dump goes back with [guide 04](guides/04-forgejo-gitops.md). If
Forgejo cannot be restored, any clone of this repo works: deploy the stacks as
pasted stacks in Dockhand from it until Forgejo is back.

## Container logs

How this was found, and how to measure it again:
[slow disk writes](notes/slow-disk-writes.md).

Every service logs with Docker's `local` driver, 3 files of 10 MB, set in
each compose file. Container Manager's default, the `db` driver, writes each
log line to a SQLite file (`<docker root>/containers/<id>/log.db`). Measured
on 2026-10-24 with the NAS otherwise idle: about 210 writes/s on the RAID 6
volume, 90 % busy, from a few KB of log text per container every 30 s; a
4 KB forced write took 0.1–1.3 s, and Authelia's SQLite failed with
`database is locked`. With every container on `local`, the same 4 KB write
took 0.05–0.11 s. `docker logs` and Dockhand show `local` logs; DSM's
Container Manager log view does not.

## Reboots

Container Manager, not Docker, restarts `unless-stopped` containers after a
reboot. At shutdown its package script
(`/var/packages/ContainerManager/scripts/start-stop-status`) records every
running or `always` container in `etc/LastRunningContainer` and stops them one
by one through DSM's web API. To Docker that is a manual stop, so on boot it
restarts only the `always` ones, all at once, before its API opens. The script
then starts its recorded list one by one, about 11 s each. Measured on
2026-10-25 with every stack on `unless-stopped`: the containers came up between
+171 s and +522 s after boot, Caddy last, so nothing answered for about 9
minutes. Hence `restart: always` everywhere: Docker starts everything in
parallel, and the script's starts find them already running.

- **Dockhand owns the stacks; Container Manager is the engine.** No Container
  Manager projects for them, and no edits in its UI (auto-restart, ports): the
  next redeploy overwrites them.
- **To keep a stack off, delete it in Dockhand (and check its containers are
  gone); don't stop it.** A stopped `always` container starts again at the
  next reboot.
- **No start order on boot.** Docker ignores `depends_on` when it restarts
  containers itself; the apps retry their database, and `always` restarts any
  that exit meanwhile.
- **Shutdown is still serial**: Container Manager stops each container in
  turn, as before.

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
- Every service: `restart: always` (see Reboots),
  `security_opt: [no-new-privileges:true]`, `cap_drop: [ALL]` plus only the commented
  `cap_add` it needs, a `deploy.resources.limits.memory` (exception:
  `forgejo-runner`, which has docker.sock, has no `cap_drop`).
- Every service: `logging: *logging`, the `x-logging` block at the top of each
  compose file (see Container logs).
- Images pinned to a version tag, exact where upstream publishes one (Renovate
  updates them). A locally built image
  is built by the `build-images` job (`scripts/jobs.conf`), not by Dockhand:
  compose gets `pull_policy: never` and no `build:` (as `paperless-tarmed`).
  List it in the `redeploy-images` job too, so a new build starts by itself.
- `container_name` only when a host command uses it (`forgejo`,
  `forgejo-runner`, `paperless-tarmed`); containers reach each other by
  service name.
- User IDs follow the image's own convention: `PUID`/`PGID` (linuxserver),
  `PAPERLESS_UID`/`PAPERLESS_GID`, a fixed `user:` where on-disk ownership
  requires it (Forgejo `1000:1000`).
