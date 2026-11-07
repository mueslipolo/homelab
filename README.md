# homelab

Docker stacks for a Synology NAS: HTTPS with Let's Encrypt, single sign-on,
media automation, documents, recipes, photos and a status dashboard, deployed by
Dockhand from a self-hosted Forgejo. Everything is LAN-only; nothing is
port-forwarded.

Each part has a guide you can follow on its own: take what you need.

![The dashboard's home page, with made-up data: service links, and the state of storage, backups and the system](docs/images/dashboard-services.png)

## How it fits together

![Overview: LAN clients, Caddy, Authelia, the apps, Forgejo and Dockhand, backups](docs/images/overview.excalidraw.svg)

Why each piece is there: [architecture](docs/architecture.md).

## Guides

| Guide                                                              | You get                                                                                                                                                  |
| ------------------------------------------------------------------ | -------------------------------------------------------------------------------------------------------------------------------------------------------- |
| [01 Synology prep](docs/guides/01-synology-prep.md)                | DSM ready for containers: Container Manager, SSH, folders, the `proxy` network                                                                           |
| [02 Dockhand](docs/guides/02-dockhand.md)                          | a web UI that deploys compose stacks from git, within seconds of a push, or pasted                                                                       |
| [03 HTTPS with Let's Encrypt](docs/guides/03-https-letsencrypt.md) | `https://<app>.home.example.com` with a wildcard certificate by DNS-01 (Infomaniak, or any provider with a Caddy plugin), Caddy on its own LAN IP        |
| [04 Forgejo and GitOps](docs/guides/04-forgejo-gitops.md)          | git on the NAS; change → push → redeploy; CI and Renovate update PRs                                                                                     |
| [05 Single sign-on](docs/guides/05-sso-authelia.md)                | Authelia over Synology's LDAP (or a users file): one login, gate or OIDC per app                                                                         |
| [06 mediarr](docs/guides/06-mediarr.md)                            | Jellyfin, Sonarr, Radarr, Prowlarr, qBittorrent, Seerr, Maintainerr, wired together                                                                      |
| [07 Paperless](docs/guides/07-paperless.md)                        | Paperless-ngx; optional paperless-tarmed for Swiss medical bills                                                                                         |
| [08 Mealie](docs/guides/08-mealie.md)                              | recipes and meal plans                                                                                                                                   |
| [09 Dashboard](docs/guides/09-dashboard.md)                        | a home page with every service and the health of jobs, backups, storage                                                                                  |
| [10 Backups and jobs](docs/guides/10-backups-and-jobs.md)          | Hyper Backup, snapshots, app dumps, one scheduler for every job                                                                                          |
| [11 Notifications](docs/guides/11-notifications.md)                | ntfy push notifications on your phone: updates waiting, failed jobs, phones that stopped backing up, disk space, bills, downloads, DSM and router events |
| [12 Immich](docs/guides/12-immich.md)                              | the family photo library with Authelia login; the phones back up to it; Synology Photos removed, its files kept                                          |
| [13 Media re-encoding](docs/guides/13-media-reencoding.md)         | shrink H.264 files to HEVC on a desktop GPU                                                                                                              |

The whole thing on a new NAS, in order: [install everything](docs/install-everything.md).

## Other docs

| Doc                                                               | For                                                                           |
| ----------------------------------------------------------------- | ----------------------------------------------------------------------------- |
| [adapting](docs/adapting.md)                                      | what is required, what a variable sets, what is Synology-only                 |
| [architecture](docs/architecture.md)                              | why it is built this way                                                      |
| [operations](docs/operations.md)                                  | the maintainer's page: rules, checks, updating, restore, conventions          |
| [slow disk writes](docs/notes/slow-disk-writes.md)                | why SQLite said "database is locked", and how to measure the disk             |
| [QuickConnect review](docs/notes/quickconnect-security-review.md) | remote photo sharing: QuickConnect against Cloudflare Tunnel, no decision yet |
| `stacks/<name>/README.md`                                         | one stack's reference: variables, what it needs, gotchas                      |

## Layout

```
stacks/<name>/     one Dockhand stack each: compose.yaml, .env.example (every variable,
                   no real values), README.md, and any config it bind-mounts (Dockhand's
                   git deploy only resolves paths inside the stack's folder)
docs/guides/       the guides above
docs/notes/        one-off investigations: slow disk writes, the QuickConnect review
scripts/           runs on the NAS: the scheduler (run-jobs.sh, jobs.conf) and its jobs,
                   bootstrap.sh, and by hand immich-import.sh, disk-diag.sh
tools/             runs on your computer: immich-verify/ (photos against Immich, by hash),
                   duplicates.sh, reencode/ (H.264 to HEVC), mikrotik/ (router alerts)
dev/               for this repo: check-no-real-values.sh, publish-github.sh,
                   dashboard-demo/ (screenshots)
.forgejo/          CI: checks and Renovate
```

Application **state** (databases, `/config`, media) stays on the NAS, never in
the repo.

**Requires** an x86_64 Docker host and a domain at a DNS provider with a Caddy
DNS module. **Written with** a Synology on DSM 7.2 and Infomaniak DNS: what is
Synology-only, optional, or just this homelab's choice is in
[adapting](docs/adapting.md).
