# Adapting it to your setup

What this homelab requires, what you set with a variable, and what is just how
this one was built. Everything with a real name in it (domain, paths, users,
devices, tokens) is a variable or a local file already; this page is about the
choices behind them.

## Required

| What                                                  | Why                                                                                            |
| ----------------------------------------------------- | ---------------------------------------------------------------------------------------------- |
| An x86_64 Docker host with Compose                    | two images are built locally (`edge`, `paperless-tarmed`); only tested on x86_64               |
| A domain at a DNS provider with a Caddy DNS-01 module | one wildcard certificate without opening a port; `DNS_PROVIDER` picks the module               |
| LAN DNS that sends `*.<internal-domain>` to Caddy     | everything is LAN-only; no public record                                                       |
| A NIC that supports macvlan, and a free LAN IP        | Caddy gets its own IP for ports 80/443 (on a Synology, DSM's nginx holds them on the NAS's IP) |
| The external `proxy` Docker network                   | every stack joins it (`scripts/bootstrap.sh`)                                                  |
| For the scheduler: root cron every 15 minutes         | bash 4, GNU `date`, `flock`, `curl`; DSM's Task Scheduler is one way, any cron works           |

## Set with a variable or a local file

| What                             | Where                                                                                                                                     |
| -------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------- |
| Domain, IPs, MAC, NIC            | each stack's `.env.example` (`INTERNAL_DOMAIN`, `CADDY_LAN_IP`, `LAN_PARENT_INTERFACE`…)                                                  |
| Folders                          | `*_ROOT` / `*_PATH` variables; any volume, any share names                                                                                |
| Time zone, OCR languages         | `TZ` in every stack (default `Europe/Zurich`); `PAPERLESS_OCR_LANGUAGE` (default `fra+eng`)                                               |
| DNS provider                     | `DNS_PROVIDER` (default `infomaniak`), `DNS_API_TOKEN` ([guide 03](guides/03-https-letsencrypt.md#another-dns-provider))                  |
| Users and groups                 | Authelia: an LDAP directory or a users file (`AUTH_BACKEND`); the admin group (`ADMIN_GROUP`)                                             |
| Which apps log in by OIDC        | `OIDC_CLIENTS` (Authelia)                                                                                                                 |
| UID/GID per stack                | `PUID`/`PGID`, `PAPERLESS_UID`/`GID`                                                                                                      |
| Which apps the dashboard shows   | `hide` / `add` in `devices.json` ([dashboard README](../stacks/dashboard/README.md#devicesjson))                                          |
| Devices that must keep uploading | `uploaders.conf`, any number of users and devices                                                                                         |
| Which jobs run, and when         | a job for an app you don't run skips itself; other changes: a copy in `$OPS_HOME/jobs.conf` ([guide 10](guides/10-backups-and-jobs.md))   |
| Optional services                | healthchecks.io (`HC_PING_KEY`), ntfy (`NTFY_TOKEN`), Renovate notices (`FORGEJO_TOKEN`), the stack list and redeploys (`DOCKHAND_TOKEN`) |

## Optional parts

Each guide's header says what it needs. In short:

- **Dockhand and Forgejo:** plain `docker compose` from any clone works; the
  scheduler pulls from any git host.
- **Authelia:** optional for everything but Mealie (its own login is turned
  off). Without LDAP, `AUTH_BACKEND=file`; the web apps share one login, but
  DSM, SMB and Drive keep their own passwords.
- **Apps:** take any of mediarr, Paperless, Mealie, Immich, ntfy, the dashboard.
  paperless-tarmed reads Swiss medical bills and is only useful in
  Switzerland.
- **Jellyfin's GPU:** `/dev/dri` is an Intel iGPU; without one, drop
  `devices:` ([guide 06](guides/06-mediarr.md)).

## Synology-only

These read DSM itself. Elsewhere they turn off on their own: an empty section,
never a false red.

| Feature                                           | Reads                                                          |
| ------------------------------------------------- | -------------------------------------------------------------- |
| Backups tab: Hyper Backup tasks and their results | Hyper Backup's config and log                                  |
| Backups tab: snapshots per shared folder          | `synoshare`, `synoschedtask`, `@sharesnap`                     |
| Backups tab: phones and computers uploading       | Synology Drive's client list (`synowebapi`), Photos folders    |
| Storage tab: what takes space on the volume       | btrfs quota groups and DSM's `@` folders                       |
| One account for DSM, SMB, Drive and the web apps  | Synology's LDAP Server ([guide 05](guides/05-sso-authelia.md)) |

## This homelab's own choices

Examples to read, not to copy as they are:

- The night schedule in [guide 10](guides/10-backups-and-jobs.md) (off-site at
  20:40, USB at 03:00) and the dump times in `jobs.conf`.
- The network diagram on the dashboard (ISP router → router → Wi-Fi AP and
  NAS), and the MikroTik scripts in `tools/mikrotik/`
  ([guide 11](guides/11-notifications.md), step 12).
- Memory limits and Paperless's worker count, sized for a 4-core, 4 GB NAS.
- [13 Media re-encoding](guides/13-media-reencoding.md) on an Intel desktop GPU.
- The measurements in [slow disk writes](notes/slow-disk-writes.md) (a RAID 6
  volume).
