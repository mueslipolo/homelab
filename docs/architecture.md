# Architecture

One Synology NAS, one operator, a family of users. The shape of the system and
the reason behind each non-obvious choice — one or two lines each. Git history
has the long versions.

![LAN clients reach Caddy on its own LAN IP; Caddy proxies to the apps on the proxy network, asks Authelia (backed by the LDAP Server) for gated hosts, and gets its certificate by DNS-01. Portainer deploys the stacks from Forgejo; scheduled jobs write dumps before Hyper Backup copies them off-site.](images/overview.excalidraw.svg)

The drawing is an Excalidraw file: open it in [excalidraw.com](https://excalidraw.com) to edit, then export as SVG with "Embed scene" over the same file.

## Deployment

- **Portainer Git stacks, not Container Manager.** Container Manager cannot
  adopt containers it did not create and has no git source.
- **Portainer and Forgejo are bootstrapped by hand** (`docker run`, Web editor
  stack): neither can deploy itself from a git server that runs on itself.
- **Portainer clones from Forgejo's raw IP** (`http://<nas-ip>:3000`), never a
  hostname: it must work before Caddy and DNS do.

## Network and certificates

- **Caddy has its own LAN IP (macvlan).** DSM's nginx holds port 443 on the
  NAS's IP for good, and DSM's reverse proxy would terminate TLS itself.
- **Certificate by DNS-01 at Infomaniak**, from a locally built Caddy
  (`xcaddy`): wildcard support, no inbound port, and the domain-controlling
  token stays in an auditable build rather than a third-party image.
- **Containers reach host services via the `proxy` gateway IP**
  (`PROXY_GATEWAY_IP`): Docker isolates bridge networks from each other.
  Used for Portainer and the LDAP Server; Forgejo is routed the same way via
  its published port 3000.
- **Containers reach Caddy via a DNS alias** for `auth.<internal-domain>` on
  `proxy`: a macvlan IP is unreachable from the same host (OIDC clients need
  it). Portainer must be connected to `proxy` for that.
  A macvlan shim on the host was rejected: host state that DSM reboots and
  updates do not keep.
- **LAN-only.** Nothing is port-forwarded; the public certificate is for
  trusted TLS, not reachability. That is why Authelia is single-factor and
  Caddy sets no security headers — revisit both before exposing anything.
  Remote photo sharing was researched without a decision:
  `docs/superpowers/specs/2026-08-09-quickconnect-security-review.md`.

## Git hosting

- **Forgejo on the NAS, not GitHub**: Portainer CE authenticates private Git
  stacks by token only, and owning the toolchain was the goal. Forgejo over
  Gitea for its non-profit governance.

## Identity

Authelia in front of everything that can take it, by how each app logs in:

| Mode                            | Apps                                                                 | Why                                                                                                                                                        |
| ------------------------------- | -------------------------------------------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **Gate** (Caddy `forward_auth`) | dashboard, sonarr, radarr, prowlarr, qbittorrent, seerr, maintainerr | their own login disabled (`AuthenticationRequired`, qBittorrent subnet whitelist); they talk to each other over Docker networks, never the public hostname |
| **OIDC** (app is a client)      | Paperless, Portainer, Forgejo, Mealie                                | Portainer/Forgejo limited to the `infra-admins` group; Mealie moved here from the gate to avoid a double login                                             |
| **Bypass**                      | Jellyfin                                                             | Chromecast cannot complete a browser login; LAN-only is the control                                                                                        |
| **Not exposed**                 | paperless-tarmed                                                     | webhook on `proxy` only, shared-secret header                                                                                                              |

- **Accounts come from Synology's LDAP Server**, shared by DSM, SMB, Drive and
  Authelia — one account everywhere (Authelia alone cannot authenticate SMB).
  Schema `posixAccount`/`posixGroup` + `memberUid`.
- **DSM/SSH/Docker admin stays a local account**: Synology's SSH only accepts
  the local `administrators` group. Users moved to LDAP by _renaming_ the local
  account, never delete-then-create.

## CI and secrets

- **The runner mounts `docker.sock` directly**, no isolation: Portainer already
  has the same access, and there is no untrusted pusher. One runner for the
  whole instance.
- **CI secrets live in Forgejo's encrypted Actions Secrets**: Renovate's API
  token, a zero-scope GitHub token for release notes, and `PRIVATE_PATTERNS`
  (the site-specific check patterns, which cannot be committed).
- **Scheduled jobs run from DSM, not CI** (`scripts/run-jobs.sh`): backups
  must work while Forgejo is down.
