# dashboard

Static homepage (nginx-unprivileged) linking every service. Tabs: **Services**
(default), **Backups**, **System** (deployments, jobs), **Network**; the left
panel sums up each tab's state and opens it. Tabs have links (`#backups`, ...).
No secrets, no database.

**Setting it up: [guide 09, Dashboard](../../docs/guides/09-dashboard.md).**
This page is the reference.

|            |                                                                                                                                                                                       |
| ---------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Reached at | `https://dashboard.<internal-domain>` (Caddy → `dashboard:8080`). Login: Authelia gate                                                                                                |
| Data       | `DASHBOARD_LOCAL_DIR` → `devices.json` (by hand), `status.json`, `storage.json` (by the scheduler); mounted read-only at `html/local/`                                                |
| Needs      | `proxy` network only, [edge](../edge/README.md), [authelia](../authelia/README.md); the scheduler ([guide 10](../../docs/guides/10-backups-and-jobs.md)) for everything but the cards |
| Used by    | —                                                                                                                                                                                     |
| Config     | `.env.example` (`DASHBOARD_LOCAL_DIR`, required); `jobs.env` for what the scripts read                                                                                                |

## `devices.json`

Network cards, tagline, diagram labels — real hostnames, so never in git. Every key optional; without the file the Network tab shows the diagram with generic labels only. Edits show on next page load.

```json
{
  "tagline": "nas-name · Synology DS920+",
  "devices": [
    { "name": "Synology DS920+", "desc": "NAS", "url": "https://<nas-host>:5001", "label": "<nas-host>:5001" }
  ],
  "diagram": {
    "isp":    { "label": "ISP router", "sub": "<ip>" },
    "router": { "label": "Router",     "sub": "<host>" },
    "ap":     { "label": "WiFi AP",    "sub": "<ip>" },
    "nas":    { "label": "NAS",        "sub": "<host>" },
    "host":   { "sub": "<last octet>" },
    "caddy":  { "sub": "<last octet>" }
  }
}
```

`diagram` keys = the `data-node-*` attributes in `html/index.html`.

## `status.json`

`scripts/dashboard-status.sh` writes it (atomic, mode 644) at the end of every 15-minute `run-jobs.sh` tick, from `jobs.env`: `DASHBOARD_LOCAL_DIR`, `STATUS_VOLUME` (required), `FORGEJO_DUMP_DIR`, `MEALIE_DUMP_DIR`, `HYPERBACKUP_LOG` (optional). The browser computes ages, so a stalled script never looks green.

**Backups tab:** `scripts/lib/protection.sh` reads what protects each shared folder from DSM itself, with nothing to configure:

- shared folders: `synoshare`; `usbshare*` are USB disks, so they are left out
- snapshots: the schedule from `synoschedtask`, the newest snapshot from `<volume>/@sharesnap/<share>/`
- Hyper Backup tasks: `HYPERBACKUP_CONF` (default `/var/packages/HyperBackup/etc/synobackup.conf`), giving each task's destination, encryption and folders, and its schedule through `sched_id`

No secret is copied. A folder with a dump in it (`FORGEJO_DUMP_DIR`...) shows that dump's age, which is why copying just its `dumps` subfolder (◐) is enough. Off DSM, the tab shows the last runs only.

| Light                                     | Amber                                  | Red               |
| ----------------------------------------- | -------------------------------------- | ----------------- |
| Volume usage                              | ≥ 80 %                                 | ≥ 90 %            |
| Newest file per dump folder               | —                                      | > 2 days, or none |
| Hyper Backup task                         | partial, > 2 days, or no result at all | failed            |
| Scheduled job                             | > 26 h                                 | non-zero exit     |
| Scheduler                                 | `git pull` failing                     | report > 2 h      |
| Status report (run outside `run-jobs.sh`) | —                                      | > 36 h            |

Hyper Backup log: `/var/packages/HyperBackup/var/log/synolog/synobackup.log`, else DSM's `/var/log/synolog/synobackup.log` (stale since a package upgrade: every task looks old). `HYPERBACKUP_LOG` overrides. Parsing expects English log lines; "no result" = no readable log or other wording.

## Editing the page

- Services: `GROUPS` / `OPS` arrays in `html/index.html`'s script; logo in `html/icons/` named after the subdomain, or mapped in `ICON`.
- Links derive from the page's address: card **README** links, CI runs, Renovate Dependency Dashboard and docs on `forgejo.<internal-domain>` under the `REPO` path.
- `html/fonts/`: Archivo, Martian Mono (latin subset, SIL OFL), local so nothing loads from the internet.
- `html/icons/`: homarr-labs/dashboard-icons (Apache-2.0; logos are trademarks). `maintainerr.png` downscaled to 128 px (upstream SVG wraps a 980 px PNG).

## Deployments panel

For each image the NAS builds (`build-images` in `scripts/jobs.conf`): the
last commit touching the stack in git (**code**), the image under its tag
(**built**: commit, build time), and the container (**running**: its build's
commit, start time). Status: **up to date**; **build pending** (new code, built
within 15 min) or **build failed** (the job's error); **redeploy needed** (new
image ready: Pull and redeploy); **not running**. The stack name must equal its
container name.

## Devices (Backups tab)

Are the phones and computers that upload to the NAS still doing it? `scripts/lib/devices.sh` reads two signals per device, and the newer one counts:

- **connected:** Synology Drive's client list (`synowebapi`, `SYNO.SynologyDrive.Connection`). It holds only devices connected right now, so the last sighting is kept in `$OPS_HOME/state/devices-seen.tsv`.
- **photos (phones):** when the backup last added a photo to `<home>/Photos/MobileBackup/<device>/DCIM`. It checks folder dates and skips `@eaDir`.

Which devices: `uploaders.conf` next to `jobs.env` (`UPLOADERS_CONF` overrides), from `scripts/uploaders.conf.example`. It isn't in git, because it holds real names. A device silent for its `max days` turns red, and amber at half. A device uploading but not listed shows as "not monitored" with the line to add, so a new or renamed phone gets noticed. Without the file, the tab shows what it sees.

## Storage tab

`storage.json` is written nightly by the `storage-scan` job (`scripts/storage-scan.sh`, [guide 10](../../docs/guides/10-backups-and-jobs.md)). Its history (one line per day) and its probe cache live in `$OPS_HOME/storage/`, so trends fill in day by day from the first run.

| Part                                               | Source                                                                                                                                                                                                                                                                                                                                                                                   |
| -------------------------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Where the space goes                               | `df`. Shared folders, Drive history, Hyper Backup cache and snapshots come from btrfs's per-subvolume counters, Docker from `docker system df`. Drive history counts only its own blocks (`btrfs filesystem du`): most of it is clones of the files in the shares. If btrfs doesn't report each subvolume's own space, shared blocks are counted in each part and the page says how much |
| Used space, changes, 7/30-day deltas, weekly trend | the history file. Growth: used now minus 30 days ago. The "full in" rate adds up only the days that grew                                                                                                                                                                                                                                                                                 |
| Media library                                      | every video under `STORAGE_MEDIA_DIR`: `tv/<series>`, `movies/<film>`. Length and codec come from ffprobe in the Jellyfin container, once per file                                                                                                                                                                                                                                       |
| Re-encode estimate                                 | `reencode-presets.sh`'s rule: preset `series` for tv/ and `film` for movies/. Only files that aren't HEVC/AV1 and are above the preset's threshold count, each estimated at ~4.5 or ~7 Mbit/s. An estimate                                                                                                                                                                               |
| What could be freed                                | the above, plus the share recycle bins and Docker's reclaimable space                                                                                                                                                                                                                                                                                                                    |

## Stacks

The System tab lists every Portainer stack:

- **up to date**: the commit Portainer deployed includes the last commit that touched the stack's folder
- **redeploy needed**: the folder changed since. Normal for up to 5 minutes after a push (GitOps polling); longer means polling is off or failing: Pull and redeploy
- **unknown**: the deployed commit is not in the NAS clone
- **not checked**: the scheduler's clone cannot pull (e.g. after a force-push), so its "code" is old. The Scheduler row says why and how to reset it; the stacks themselves need nothing
- **stopped**
- **Web editor**: not deployed from git, so there is nothing to compare

Below, under Jobs and scheduler, **Containers** (`scripts/lib/containers.sh`):
every container with a restart policy. One "N running, no crash" row while all
are fine; otherwise a row per container that is restart-looping, crashed (an
exit code other than 0, 137, 143, or out of memory), unhealthy, or restarted
by docker in the last 20 minutes. `scripts/alerts.sh` pushes the same to ntfy.

`scripts/lib/stacks.sh` asks Portainer's API (`PORTAINER_URL`, default `https://127.0.0.1:9443`) with `PORTAINER_TOKEN` from `jobs.env`. Create the token under Portainer → My account → Access tokens. It has its user's rights, so `jobs.env` must stay root-only. Only name, status, compose path and deployed commit are kept: the API also returns each stack's environment, passwords included. JSON is read with `jq` from the `ghcr.io/jqlang/jq` image (DSM has none). An image tag that moves without a git change (`latest`) is not seen.

## Gotchas

- **Port 8080, not 80**: unprivileged nginx can't bind below 1024.
- **`read_only: true`**: tmpfs `/tmp`, `/var/cache/nginx`, `/var/run` must stay.
- **Keep `html/local/.gitkeep`**: the mount point must exist in Portainer's clone.
- **Files must be world-readable**: nginx runs unprivileged.
