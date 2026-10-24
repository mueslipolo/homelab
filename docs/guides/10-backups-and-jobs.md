# Backups and scheduled jobs

> **Needs:** [01 Synology prep](01-synology-prep.md) · **Optional:** [04 Forgejo](04-forgejo-gitops.md) (the scheduler pulls its scripts from git), [09 dashboard](09-dashboard.md) (shows the jobs' state)
> **Used by:** [04 Forgejo](04-forgejo-gitops.md), [07 Paperless](07-paperless.md), [08 Mealie](08-mealie.md) (their dump jobs) · **Restore:** [operations](../operations.md#restore)

## What you get

- DSM's own tools for the files: **Hyper Backup** off-site and to USB,
  **Snapshot Replication** with immutable snapshots.
- App-level exports (Forgejo, Mealie, Paperless), written every evening before
  the backup window. Each restores into the same app version, which you then
  upgrade.
- **One** DSM Task Scheduler entry, every 15 minutes, that runs every job
  listed in git (`scripts/jobs.conf`), with timeouts, logs, catch-up after
  downtime, and a healthchecks.io ping per job.

Real paths, the backup target and restore-test results belong in your own
private notes; this page is the shape.

A night, in order:

| Time  | What                                                                     |
| ----- | ------------------------------------------------------------------------ |
| 20:00 | `forgejo-dump`, then `mealie-backup` (20:05), `paperless-export` (20:10) |
| 20:40 | Hyper Backup → off-site S3, client-side encrypted                        |
| 03:00 | Hyper Backup → USB disk                                                  |
| 04:30 | `docker-prune`; 05:00 `storage-scan`                                     |

Snapshot Replication takes its daily immutable snapshots of `homes` and
`paperless` on its own schedule. Every job pings healthchecks.io and lands on
the dashboard.

## What is backed up

| Mechanism (DSM)                                                                    | Covers                     | Against                                                                       |
| ---------------------------------------------------------------------------------- | -------------------------- | ----------------------------------------------------------------------------- |
| **Hyper Backup** → off-site S3 (client-side encrypted), plus a task to a USB drive | the sources below          | NAS loss (S3), disk failure (USB — always connected, so not ransomware-proof) |
| **Snapshot Replication**, immutable snapshots                                      | `homes`, `paperless`       | ransomware, a hijacked admin session                                          |
| **App exports**                                                                    | Forgejo, Mealie, Paperless | restores that do not need the exact same app version                          |

| Stack                                                     | Hyper Backup takes                                                            | Why                                                                                                                                                          |
| --------------------------------------------------------- | ----------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| `forgejo`                                                 | **dumps only** (`FORGEJO_DUMP_DIR`)                                           | a raw copy of a running instance can be inconsistent, the dump is not; recovery point = last dump                                                            |
| `mealie`                                                  | **dumps only** (`MEALIE_DUMP_DIR`)                                            | same; Mealie's own backup export, by the `mealie-backup` job. Dumps are root-only                                                                            |
| `paperless`                                               | the whole share, including the `export/` folder of the `paperless-export` job | originals are irreplaceable; the export restores into the same version, then upgrade                                                                         |
| `authelia`                                                | **secrets folder only**                                                       | `storage_encryption_key` decrypts its Postgres, the JWKS key signs its OIDC tokens; that Postgres holds only disposable state (OIDC consent, login attempts) |
| `ntfy`                                                    | nothing                                                                       | users and tokens are quick to recreate; messages are a 3-day cache                                                                                           |
| `mediarr`                                                 | nothing                                                                       | re-downloadable                                                                                                                                              |
| `edge`, `dashboard`, `forgejo-runner`, `paperless-tarmed` | nothing                                                                       | rebuilt from git; keep `dashboard`'s `devices.json` with your private notes                                                                                  |

**Dumps-only means the dump job must run** — the dashboard turns its light red
after 2 days without one.

Set up in DSM:

1. **Hyper Backup:** one task per destination, sources as in the table; schedule
   it after the dump jobs (here 20:40 off-site, 03:00 USB).
2. **Snapshot Replication:** scheduled, immutable snapshots of the shares that
   must survive ransomware (`homes`, `paperless`).
3. **Check:** restore one file and one dump into a scratch folder.

## Scheduled jobs

One DSM Task Scheduler entry, every 15 minutes, runs `scripts/run-jobs.sh`.
The jobs and their times are in `scripts/jobs.conf` (git), not in DSM.

| Job                | When                                     | Does                                                                                                                                                                                                    |
| ------------------ | ---------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `forgejo-dump`     | after 20:00 (before the 20:40 S3 backup) | `forgejo dump`, renamed only when complete; keeps 14 days                                                                                                                                               |
| `mealie-backup`    | after 20:05 (before the 20:40 S3 backup) | Mealie's own backup (`POST /api/admin/backups`, inside the container), copied out, checked, renamed when complete; keeps 14 days                                                                        |
| `paperless-export` | after 20:10 (before the 20:40 S3 backup) | `document_exporter -d` into `PAPERLESS_DATA_ROOT/export`: incremental, mirrors the archive                                                                                                              |
| `docker-prune`     | after 04:30                              | deletes images no container uses and build cache, both older than a week; keeps the images the CI workflows run in (Renovate's, `homelab-ci`)                                                           |
| `storage-scan`     | after 05:00                              | the dashboard's Storage tab: size per shared folder (btrfs) and its largest folders (du), Docker, the media library (ffprobe in the Jellyfin container, new files only), and a daily history for trends |
| `renovate-notify`  | after 07:00                              | pushes to ntfy what newly waits for approval on Renovate's Dependency Dashboard ([11](11-notifications.md)); once per update                                                                            |
| `build-images`     | every tick                               | builds locally built images (paperless-tarmed, and the CI image `homelab-ci`) when their code changed; dashboard shows amber "build pending", then "redeploy needed" until redeployed                   |

Each tick: `git pull --ff-only` its own clone (if Forgejo is down, the last
checkout keeps running) → run due jobs (due = past its time and not yet
successful today; a missed run catches up, a failure retries after an hour;
each with a timeout, a log in `$OPS_HOME/logs/<job>/`, state in
`$OPS_HOME/state/<job>.json`) → refresh the dashboard's `status.json` → push
what turned amber or red to ntfy ([11](11-notifications.md#10-nas-alerts)) → ping
healthchecks.io (per job: start and exit code with the log's tail; plus a
heartbeat that also fails when the clone is a day stale).

Every push to `main` therefore runs as root on the NAS within 15 minutes.

### Set up the scheduler

1. _Git Server_ package installed (`/usr/bin/git`).
2. Clone as root with a read-only Forgejo **deploy key** (repo → Settings →
   Deploy keys, write off). Root-owned: whoever can write these scripts runs
   code as root.
   ```bash
   sudo ssh-keygen -t ed25519 -N '' -f /root/.ssh/homelab-deploy   # add the .pub as deploy key
   sudo GIT_SSH_COMMAND='ssh -i /root/.ssh/homelab-deploy -o StrictHostKeyChecking=accept-new' \
     git clone ssh://git@localhost:222/admin/homelab.git /volume1/docker/homelab-ops
   sudo git -C /volume1/docker/homelab-ops config core.sshCommand 'ssh -i /root/.ssh/homelab-deploy'
   ```
3. `jobs.env`, outside the clone, `chmod 600`:
   ```bash
   OPS_HOME=/volume1/docker/homelab-ops-local      # state and logs
   HC_PING_KEY=<healthchecks.io project ping key>           # Project Settings → Ping key (not a check's UUID)
   FORGEJO_DUMP_DIR=/volume1/forgejo/dumps         # mkdir, chmod 700
   MEALIE_DUMP_DIR=/volume1/mealie/dumps           # mkdir, chmod 700
   MEALIE_API_TOKEN=<token>                                  # Mealie, admin user: profile → API Tokens
   DASHBOARD_LOCAL_DIR=/volume1/docker/dashboard-local
   STORAGE_MEDIA_DIR=/volume1/media/media           # holds tv/ and movies/; Storage tab's media section
   # uploaders.conf next to this file: devices that must keep uploading (scripts/uploaders.conf.example)
   PORTAINER_TOKEN=<token>                                   # Portainer → My account → Access tokens; dashboard's stack list
   STATUS_VOLUME=/volume1
   FORGEJO_TOKEN=<token>                                     # renovate-notify: Forgejo token that can read the repo's issues
   FORGEJO_REPO=admin/homelab
   NTFY_TOKEN=<token>                                        # renovate-notify and the NAS alerts: ntfy token that can publish ([11](11-notifications.md))
   DASHBOARD_URL=https://dashboard.home.example.com          # optional: alerts open the matching dashboard tab
   ```
4. **Check:** run it once by hand: `sudo bash <clone>/scripts/run-jobs.sh <path>/jobs.env`, then
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

## Troubleshooting

| Symptom                                    | Cause → fix                                                                                                                                         |
| ------------------------------------------ | --------------------------------------------------------------------------------------------------------------------------------------------------- |
| `_scheduler.json` shows `"pull_ok": false` | The clone can't pull: deploy key, Forgejo down, or the branch was force-pushed (`git fetch && git reset --hard origin/main` in the clone, as root). |
| healthchecks.io: `homelab-run-jobs` down   | The DSM task stopped or fails before the ping: run the command from step 4 by hand.                                                                 |
| A dump job red on the dashboard            | Its log in `$OPS_HOME/logs/<job>/`; the job retries an hour later.                                                                                  |

## Next

- [Restore](../operations.md#restore), and test it once: nothing counts as backed up before.
- [09 dashboard](09-dashboard.md) shows each job's state.
