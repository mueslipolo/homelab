# Architecture

One Synology NAS, one operator, a family of users. The shape of the system and
the reason behind each non-obvious choice — one or two lines each. Git history
has the long versions.

![LAN clients reach Caddy on its own LAN IP; Caddy proxies to the apps on the proxy network, asks Authelia (backed by the LDAP Server) for gated hosts, and gets its certificate by DNS-01. Dockhand deploys the stacks from Forgejo; scheduled jobs write dumps before Hyper Backup copies them off-site.](images/overview.excalidraw.svg)

The drawing is an Excalidraw file: open it in [excalidraw.com](https://excalidraw.com) to edit, then export as SVG with "Embed scene" over the same file.

## Deployment

- **Dockhand git stacks, not Container Manager.** Container Manager cannot
  adopt containers it did not create and has no git source.
- **Dockhand and Forgejo are bootstrapped by hand** (`docker compose`, a pasted
  stack): neither can deploy itself from a git server that runs on itself.
- **Dockhand clones from Forgejo's raw IP** (`http://<nas-ip>:3000`), never a
  hostname: it must work before Caddy and DNS do.
- **A push deploys through a webhook per stack**, Forgejo calling
  `dockhand:3000` over `proxy`; a scheduled sync per stack is the safety net.
  Only a stack whose folder changed is redeployed.
- **The scheduler asks Dockhand's API on the NAS's loopback**
  (`127.0.0.1:3010`): the stack list for the dashboard, and redeploys of
  images the NAS builds itself.

## Network and certificates

- **Caddy has its own LAN IP (macvlan).** DSM's nginx holds port 443 on the
  NAS's IP, and DSM's reverse proxy would terminate TLS itself.
- **Certificate by DNS-01 at Infomaniak**, from a locally built Caddy
  (`xcaddy`): wildcard support, no inbound port, and the domain-controlling
  token stays in an auditable build rather than a third-party image.
- **Containers reach host services via the `proxy` gateway IP**
  (`PROXY_GATEWAY_IP`): Docker isolates bridge networks from each other.
  Used for the LDAP Server; Forgejo is routed the same way via its published
  port 3000.
- **Containers reach Caddy via a DNS alias** for `auth.<internal-domain>` on
  `proxy`: a macvlan IP is unreachable from the same host (OIDC clients need
  it).
  A macvlan shim on the host was rejected: host state that DSM reboots and
  updates do not keep.
- **LAN-only.** Nothing is port-forwarded; the public certificate is for
  trusted TLS, not reachability. That is why Authelia is single-factor and
  Caddy sets no security headers — revisit both before exposing anything.
  Remote photo sharing was researched without a decision:
  [QuickConnect security review](notes/quickconnect-security-review.md).

## Git hosting

- **Forgejo on the NAS, not GitHub**: owning the toolchain was the goal, and
  Dockhand clones over the LAN, with no internet dependency for a deploy. Forgejo over
  Gitea for its non-profit governance.

## Identity

Authelia in front of everything that can take it, by how each app logs in:

| Mode                            | Apps                                                                 | Why                                                                                                                                                                                |
| ------------------------------- | -------------------------------------------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **Gate** (Caddy `forward_auth`) | dashboard, sonarr, radarr, prowlarr, qbittorrent, seerr, maintainerr | their own login disabled (`AuthenticationRequired`, qBittorrent subnet whitelist); they talk to each other over Docker networks, never the public hostname                         |
| **OIDC** (app is a client)      | Paperless, Dockhand, Forgejo, Mealie, Immich                         | Dockhand/Forgejo limited to the `infra-admins` group; Mealie moved here from the gate to avoid a double login; Immich's phone app calls its API directly, which a gate would block |
| **Bypass**                      | Jellyfin, ntfy                                                       | Chromecast cannot complete a browser login, and ntfy's apps and senders call its API: each keeps its own login (ntfy: users and tokens, anonymous denied)                          |
| **Not exposed**                 | paperless-tarmed                                                     | webhook on `proxy` only, shared-secret header                                                                                                                                      |

- **Accounts come from Synology's LDAP Server**, shared by DSM, SMB, Drive and
  Authelia — one account everywhere (Authelia alone cannot authenticate SMB).
  Without a directory, Authelia's `file` backend covers the web apps only.
  Schema `posixAccount`/`posixGroup` + `memberUid`.
- **DSM/SSH/Docker admin stays a local account**: Synology's SSH only accepts
  the local `administrators` group. Users moved to LDAP by _renaming_ the local
  account, never delete-then-create.

## CI and secrets

- **The runner mounts `docker.sock` directly**, no isolation: Dockhand already
  has the same access, and there is no untrusted pusher. One runner for the
  whole instance.
- **CI secrets live in Forgejo's encrypted Actions Secrets**: Renovate's API
  token, a zero-scope GitHub token for release notes, and `PRIVATE_PATTERNS`
  (the site-specific check patterns, which cannot be committed).
- **Scheduled jobs run from DSM, not CI** (`scripts/run-jobs.sh`): backups
  must work while Forgejo is down.
