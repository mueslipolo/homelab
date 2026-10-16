# Operations

Backups, restore, scheduled jobs, and the conventions every stack follows.
Real paths, the backup target and test results live in the gitignored
`PRIVATE-real-values-do-not-publish/BACKUP.md`; this file is the shape.

## Backups

| Mechanism (DSM)                                                                    | Covers                     | Against                                                                       |
| ---------------------------------------------------------------------------------- | -------------------------- | ----------------------------------------------------------------------------- |
| **Hyper Backup** → off-site S3 (client-side encrypted), plus a task to a USB drive | the sources below          | NAS loss (S3), disk failure (USB — always connected, so not ransomware-proof) |
| **Snapshot Replication**, immutable snapshots                                      | `homes`, `paperless`       | ransomware, a hijacked admin session                                          |
| **App exports**                                                                    | Forgejo, Mealie, Paperless | restores that do not need the exact same app version                          |

| Stack                                                     | Hyper Backup takes                                                            | Why                                                                                                                 |
| --------------------------------------------------------- | ----------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------- |
| `forgejo`                                                 | **dumps only** (`FORGEJO_DUMP_DIR`)                                           | a raw SQLite/repo copy only restores into the same version; recovery point = last dump                              |
| `mealie`                                                  | **dumps only** (`MEALIE_DUMP_DIR`)                                            | same; Mealie's own backup export, by the `mealie-backup` job. It contains Mealie's secret keys: dumps are root-only |
| `paperless`                                               | the whole share, including the `export/` folder of the `paperless-export` job | originals are irreplaceable; the export restores into any version                                                   |
| `authelia`                                                | **secrets folder only**                                                       | losing `storage_encryption_key` or the JWKS key breaks every OIDC client; its Postgres is only sessions             |
| `mediarr`                                                 | nothing                                                                       | re-downloadable                                                                                                     |
| `edge`, `dashboard`, `forgejo-runner`, `paperless-tarmed` | nothing                                                                       | rebuilt from git; keep `dashboard`'s `devices.json` with your private notes                                         |

**Dumps-only means the dump job must run** — the dashboard turns its light red
after 2 days without one.

## Scheduled jobs

One DSM Task Scheduler entry, every 15 minutes, runs `scripts/run-jobs.sh`.
The jobs and their times are in `scripts/jobs.conf` (git), not in DSM.

| Job                | When                                     | Does                                                                                                                                                                                     |
| ------------------ | ---------------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `forgejo-dump`     | after 20:00 (before the 20:40 S3 backup) | `forgejo dump`, renamed only when complete; keeps 14 days                                                                                                                                |
| `mealie-backup`    | after 20:05 (before the 20:40 S3 backup) | Mealie's own backup (`POST /api/admin/backups`, inside the container), copied out, checked, renamed when complete; keeps 14 days                                                         |
| `paperless-export` | after 20:10 (before the 20:40 S3 backup) | `document_exporter -d` into `PAPERLESS_DATA_ROOT/export`: incremental, mirrors the archive                                                                                               |
| `docker-prune`     | after 04:30                              | deletes images no container uses and build cache, both older than a week                                                                                                                 |
| `storage-scan`     | after 05:00                              | the dashboard's Storage tab: size per shared folder (btrfs), Docker, recycle bins, the media library (ffprobe in the Jellyfin container, new files only), and a daily history for trends |
| `build-images`     | every tick                               | builds locally built images (paperless-tarmed) when their code changed; dashboard shows amber "update waiting" until redeployed                                                          |

Each tick: `git pull --ff-only` its own clone (if Forgejo is down, the last
checkout keeps running) → run due jobs (due = past its time and not yet
successful today; a missed run catches up, a failure retries after an hour;
each with a timeout, a log in `$OPS_HOME/logs/<job>/`, state in
`$OPS_HOME/state/<job>.json`) → refresh the dashboard's `status.json` → ping
healthchecks.io (per job: start and exit code with the log's tail; plus a
heartbeat that also fails when the clone is a day stale).

Every push to `main` therefore runs as root on the NAS within 15 minutes.

### Setup (once)

1. _Git Server_ package installed (`/usr/bin/git`).
2. Clone as root with a read-only Forgejo **deploy key** (repo → Settings →
   Deploy keys, write off). Root-owned: whoever can write these scripts runs
   code as root.
   ```bash
   sudo ssh-keygen -t ed25519 -N '' -f /root/.ssh/homelab-deploy   # add the .pub as deploy key
   sudo GIT_SSH_COMMAND='ssh -i /root/.ssh/homelab-deploy -o StrictHostKeyChecking=accept-new' \
     git clone ssh://git@localhost:222/<owner>/homelab.git <nas-volume-path>/docker/homelab-ops
   sudo git -C <nas-volume-path>/docker/homelab-ops config core.sshCommand 'ssh -i /root/.ssh/homelab-deploy'
   ```
3. `jobs.env`, outside the clone, `chmod 600`:
   ```bash
   OPS_HOME=<nas-volume-path>/docker/homelab-ops-local      # state and logs
   HC_PING_KEY=<healthchecks.io project ping key>           # Project Settings → Ping key (not a check's UUID)
   FORGEJO_DUMP_DIR=<nas-volume-path>/forgejo/dumps         # mkdir, chmod 700
   MEALIE_DUMP_DIR=<nas-volume-path>/mealie/dumps           # mkdir, chmod 700
   MEALIE_API_TOKEN=<token>                                  # Mealie, admin user: profile → API Tokens
   DASHBOARD_LOCAL_DIR=<nas-volume-path>/docker/dashboard-local
   STORAGE_MEDIA_DIR=<nas-volume-path>/media/media           # holds tv/ and movies/; Storage tab's media section
   # uploaders.conf next to this file: devices that must keep uploading (scripts/uploaders.conf.example)
   PORTAINER_TOKEN=<token>                                   # Portainer → My account → Access tokens; dashboard's stack list
   STATUS_VOLUME=<nas-volume-path>
   ```
4. Test once: `sudo bash <clone>/scripts/run-jobs.sh <path>/jobs.env`, then
   `cat $OPS_HOME/state/_scheduler.json` shows `"pull_ok": true`.
5. DSM → Task Scheduler → Create → Scheduled Task → User-defined script: user
   **root**; Schedule _Daily_, first run 00:00, **Frequency every 15 minutes**,
   last run 23:45; email only on abnormal termination. Command:
   `bash <clone>/scripts/run-jobs.sh <path>/jobs.env`
6. healthchecks.io: checks appear on first ping (`homelab-run-jobs`,
   `homelab-<job>`). Set periods — `run-jobs` 15 min / grace 30 min, daily
   jobs 1 day / grace 2 h — and where alerts go (Integrations).

**Adding a job:** a script in `scripts/` that exits non-zero on failure, plus
one line in `jobs.conf` (`name  HH:MM|tick  timeout  script [args]`).

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

**After a total loss**, rebuild in `docs/INSTALL.md`'s order, restoring each
stack's data before deploying it; Forgejo's dump goes back at step 4. If
Forgejo cannot be restored, any clone of this repo works: deploy the stacks as
Web editor stacks from it until Forgejo is back.

## Conventions (for a new stack)

- Files: `compose.yaml`, `.env.example` (every `${VAR}`, no real values),
  `README.md` (shape: summary table → Deploy → Gotchas).
- Markdown is formatted with Prettier (`.prettierrc.yaml`: aligned tables, line
  breaks kept); pre-commit fixes it, CI checks it.
- Every service: `restart: unless-stopped`,
  `security_opt: [no-new-privileges:true]`, `cap_drop: [ALL]` plus only the commented
  `cap_add` it needs, a `deploy.resources.limits.memory`.
- Images pinned to an exact tag (Renovate updates them). A locally built image
  is built by the `build-images` job (`scripts/jobs.conf`), not by Portainer:
  compose gets `pull_policy: never` and no `build:` (as `paperless-tarmed`).
- `container_name` only when a host command uses it (`forgejo`,
  `forgejo-runner`, `paperless-tarmed`); containers reach each other by
  service name.
- User IDs follow the image's own convention: `PUID`/`PGID` (linuxserver),
  `PAPERLESS_UID`/`PAPERLESS_GID`, a fixed `user:` where on-disk ownership
  requires it (Forgejo `1000:1000`).
