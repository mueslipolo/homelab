# dashboard

Static homepage (nginx-unprivileged) linking every service, and a simpler family page for those outside the admin group ([Two pages](#two-pages)). Tabs: **Services**
(default), **Storage**, **Backups**, **System** (stacks, containers, jobs), **Network**; the left
panel sums up each tab's state and opens it. Tabs have links (`#backups`, ...).
No secrets, no database.

**Setting it up: [guide 09, Dashboard](../../docs/guides/09-dashboard.md).**
This page is the reference.

|            |                                                                                                                                                                                                                    |
| ---------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| Reached at | `https://dashboard.<internal-domain>` (Caddy → `dashboard:8080`). Login: Authelia gate, if deployed; a **Log out** link under the title ends the Authelia session (`auth.<internal-domain>/logout`) and comes back |
| Data       | `DASHBOARD_LOCAL_DIR` → `devices.json` (by hand), `status.json`, `storage.json` (by the scheduler); mounted read-only at `html/local/`                                                                             |
| Needs      | `proxy` network, [edge](../edge/README.md); optional: [authelia](../authelia/README.md) (login), the scheduler ([guide 10](../../docs/guides/10-backups-and-jobs.md)) for everything but the cards                 |
| Used by    | —                                                                                                                                                                                                                  |
| Config     | `.env.example` (`DASHBOARD_LOCAL_DIR`, required); `jobs.env` for what the scripts read                                                                                                                             |

## Two pages

- **`index.html`**, the whole dashboard: for the admin group (`ADMIN_GROUP`, default `infra-admins`).
- **`welcome.html`**, the family page: everyone else logged in through Authelia. Five big cards (Photos, Films and series, Ask for a film, Recipes, Documents) in plain words, two tips with the gophers, Log out. Nothing from `local/`: it shows no device, status or path.

Two places decide, so neither alone is trusted:

- **Caddy** (`stacks/edge/caddy/Caddyfile`) picks the page. For a non-admin it serves `welcome.html` at any path, except `icons/`, `fonts/` and `gopher/`. `forward_auth` replaces any `Remote-*` header the browser sends, and Caddy reads the group from Authelia's `Remote-Groups`. The edge stack needs the same `ADMIN_GROUP` as authelia.
- **Authelia** (`access_control`) lets the admin group see everything on `dashboard.`, and everyone else only `/`, `/welcome.html` and those three folders. `local/status.json` asked for directly gets a 403.

Without Authelia there is no group, so everyone gets `index.html`. To change the family's cards, edit `APPS` in `html/welcome.html`. They don't follow `devices.json`'s `hide`/`add`, which the family page never reads. An admin sees the family page at `/welcome.html`: the **Family page** link under Log out.

## `devices.json`

Network cards, tagline, diagram labels, the repo on Forgejo that the README and Ops links point to, and which app cards to show — real names, so never in git. Every key optional; without the file the Network tab shows the diagram with generic labels only, every app card shows, and the Ops group is hidden (it needs `repo`). Edits show on next page load.

- `hide`: subdomains of the built-in cards you don't run (`mealie`, `paperless`…); a group left empty disappears.
- `add`: cards for your own apps: `group` (`apps` or `admin`), `name`, `desc`, `sub` (its subdomain), optional `icon` (a file in `html/icons/`, else `<sub>.svg`, else a generic glyph) and `"sso": false` for an app with its own login.
- The diagram is this homelab's layout (ISP router → router → AP and NAS); `diagram` only relabels its boxes.

```json
{
  "tagline": "nas-name · Synology DS920+",
  "repo": "<owner>/homelab",
  "hide": ["paperless", "mealie"],
  "add": [{ "group": "apps", "name": "Immich", "desc": "Photos", "sub": "immich" }],
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

`scripts/dashboard-status.sh` writes it (atomic, mode 644) at the end of every 15-minute `run-jobs.sh` tick, from `jobs.env`: `DASHBOARD_LOCAL_DIR`, `STATUS_VOLUME` (required), `FORGEJO_DUMP_DIR`, `MEALIE_DUMP_DIR`, `IMMICH_DUMP_DIR`, `HYPERBACKUP_LOG` (optional). The browser computes ages, so a stalled script never looks green.

**Backups tab:** `scripts/lib/protection.sh` reads what protects each shared folder from DSM itself, with nothing to configure:

- shared folders: `synoshare`; `usbshare*` are USB disks, so they are left out
- snapshots: the schedule from `synoschedtask`, the newest snapshot from `<volume>/@sharesnap/<share>/`
- Hyper Backup tasks: `HYPERBACKUP_CONF` (default `/var/packages/HyperBackup/etc/synobackup.conf`), giving each task's destination, encryption and folders, and its schedule through `sched_id`

No secret is copied. A folder with a dump in it (`FORGEJO_DUMP_DIR`...) shows that dump's age, which is why copying just its `dumps` subfolder (◐) is enough. Off DSM, the tab shows the last runs only, and without Hyper Backup's log (not installed, or not a Synology) no Hyper Backup row at all.

| Light                                     | Amber                                      | Red               |
| ----------------------------------------- | ------------------------------------------ | ----------------- |
| Volume usage                              | ≥ 80 %                                     | ≥ 90 %            |
| Newest file per dump folder               | —                                          | > 2 days, or none |
| Hyper Backup task                         | partial, > 2 days, or no result in its log | failed            |
| Newest snapshot per shared folder         | > 2 days (fixed, whatever the schedule)    | —                 |
| Scheduled job                             | > 26 h                                     | non-zero exit     |
| Scheduler                                 | `git pull` failing                         | report > 2 h      |
| Status report (run outside `run-jobs.sh`) | —                                          | > 36 h            |

Hyper Backup log: `/var/packages/HyperBackup/var/log/synolog/synobackup.log`, else DSM's `/var/log/synolog/synobackup.log` (stopped updating here in 2024-11: every task looks old). `HYPERBACKUP_LOG` overrides. Parsing expects English log lines; "no result" = no readable log or other wording.

## Editing the page

- Services: `GROUPS` / `OPS` arrays in `html/index.html`'s script; logo in `html/icons/` named after the subdomain, or mapped in `ICON`.
- Links derive from the page's address: card **README** links, CI runs, Renovate Dependency Dashboard and docs on `forgejo.<internal-domain>` under the `repo` named in `devices.json` (without it, those links are hidden).
- `html/fonts/`: Archivo, Martian Mono (latin subset, SIL OFL), local so nothing loads from the internet.
- `html/gopher/`: one Go gopher per tab, `<tab>.png`, shown in the corner of the open tab; a new tab needs one. Free Gophers Pack by Maria Letta (CC0); the Go gopher by Renee French (CC BY 4.0), credited at the bottom of the sidebar ([`html/gopher/README.md`](html/gopher/README.md)).
- `html/icons/`: homarr-labs/dashboard-icons (Apache-2.0; logos are trademarks), except `dockhand.svg`, Dockhand's own logo from its image (`/app/build/client/logo.svg`). `maintainerr.png` downscaled to 128 px (upstream SVG wraps a 980 px PNG).

## Images built on the NAS

For each image the NAS builds (`build-images` in `scripts/jobs.conf`), the
stack's own row in System → Stacks also checks the image: the last commit
touching the stack (code), the image under its tag (built), and the container
(running). The worse of the stack's and the image's state shows: **build
pending** (new code, built within 15 min), **build-images job failed** (the job's error, possibly from another image),
**redeploy needed** (new image ready: the `redeploy-images` job starts it
within 15 min), **redeploy-images job failed** (its error), **not running**. When
all agree the detail line only says when the image was built; otherwise it
shows the whole chain. The stack name must equal its container name.

An image built elsewhere and only started here (an app's own CI builds each
release on the NAS; `redeploy-images` in `scripts/jobs.conf`) has no code or
build stage: its row shows the version running (the image's OCI version
label), and **redeploy needed** while a newer release waits under its tag.

## Devices (Backups tab)

Are the phones and computers that upload to the NAS still doing it? `scripts/lib/devices.sh` reads two signals per device, and the newer one counts:

- **connected:** Synology Drive's client list (`synowebapi`, `SYNO.SynologyDrive.Connection`): a client whose `client_status` is `on_line` counts as seen now, any other as of its `last_auth_time`. The newest sighting per device is kept in `$OPS_HOME/state/devices-seen.tsv`, so a device stays known once it has left the list.
- **photos (phones):** when the backup last added a photo to `<home>/Photos/MobileBackup/<device>/DCIM`. It takes the newest file's date and skips `@eaDir`.

Which devices: `$OPS_HOME/uploaders.conf` (`UPLOADERS_CONF` overrides), from `scripts/uploaders.conf.example`. It isn't in git, because it holds real names. A device silent for its `max days` turns red, and amber at half. A device uploading but not listed shows as "not monitored" with the line to add, so a new or renamed phone gets noticed. Without the file, the tab shows what it sees.

## Storage tab

`storage.json` is written nightly by the `storage-scan` job (`scripts/storage-scan.sh`, [guide 10](../../docs/guides/10-backups-and-jobs.md)). Its history (one line per day) and its probe cache live in `$OPS_HOME/storage/`, so trends fill in day by day from the first run.

| Part                                               | Source                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                          |
| -------------------------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Where the space goes                               | `df`. Shared folders, Drive history, Hyper Backup cache and snapshots come from btrfs's per-subvolume counters (for snapshots, only what one snapshot holds alone: a file deleted after several snapshots is under Other), Docker from `docker system df`. Drive history counts only its own blocks (`btrfs filesystem du`): most of it is clones of the files in the shares. If btrfs doesn't report each subvolume's own space, shared blocks are counted in each part and the page says how much. Drawn as a treemap: each shared folder split into its 12 largest folders (`du`, one pass per share; a folder holding 90 % of a share is opened, so LDAP homes show per user), scaled to the share's btrfs size. Other is split too: files deleted from a share that its snapshots still hold (every snapshot's file list against the live share's, by inode, so moved files don't count; an estimate from file sizes), btrfs metadata (`btrfs filesystem df`), DSM's own subvolumes, the smallest parts, and the rest, shown as not traced |
| Used space, changes, 7/30-day deltas, weekly trend | the history file. Growth: used now minus 30 days ago. The "full in" rate is that change per day, and is hidden when used space did not grow                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                     |
| Media library                                      | every video under `STORAGE_MEDIA_DIR`: `tv/<series>`, `movies/<film>`. Length comes from ffprobe in the Jellyfin container, once per file. Drawn as one treemap of every series and film; hovering a series splits it by season folder                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                          |

## Stacks

The System tab lists every git stack Dockhand deploys, with a detail line: the commit and
date it was deployed, and its containers (running / total, uptime, and the result of those that have a healthcheck). Its
state:

- **up to date**: the commit deployed (last synced) includes the last commit that touched the stack's folder
- **redeploy needed**: the folder changed since. Normal for the minute after a push (the Forgejo webhook); longer means the webhook and the scheduled sync are off or failing: Deploy the stack in Dockhand
- **deploy failed**: the stack's last deploy failed; Dockhand shows why
- **unknown**: the deployed commit is not in the NAS clone, or Dockhand has no compose path for the stack
- **not checked**: the scheduler's clone cannot pull (e.g. after a force-push), so its "code" is old. The Scheduler row says why and how to reset it; the stacks themselves need nothing

Below, under Containers, jobs and scheduler, **Containers** (`scripts/lib/containers.sh`):
every container with a restart policy. One "N running, no crash" row while all
are fine; otherwise a row per container that is restart-looping, crashed (an
exit code other than 0, 137, 143, or out of memory), unhealthy, or restarted
by docker in the last 20 minutes. `scripts/alerts.sh` pushes the same to ntfy.

`scripts/lib/stacks.sh` asks Dockhand's API (`DOCKHAND_URL`, default `http://127.0.0.1:3010`) with `DOCKHAND_TOKEN`, only when that token is in `jobs.env`. Create it under Dockhand → Profile → API tokens. It has its user's rights, so `jobs.env` must stay root-only. Only name, status, compose path and deployed commit are kept: Dockhand's API also returns each stack's webhook secret. Dockhand's stacks deployed without git (`forgejo`) are not listed; their containers are. JSON is read with `jq` from the `ghcr.io/jqlang/jq` image (DSM has none). An image tag that moves without a git change (`latest`) is not seen.

## Gotchas

- **Port 8080, not 80**: unprivileged nginx can't bind below 1024.
- **`read_only: true`**: tmpfs `/tmp`, `/var/cache/nginx`, `/var/run` must stay.
- **Keep `html/local/.gitkeep`**: the mount point must exist in the deployed copy (Dockhand's stack folder).
- **Files must be world-readable**: nginx runs unprivileged.
