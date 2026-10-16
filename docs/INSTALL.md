# Install on a new NAS

From an empty Synology to every stack running, in dependency order. Each step
links to the stack's README for details; this page only gives the order and
the commands that tie the stacks together. Rebuilding after a loss follows the
same order — see `docs/OPERATIONS.md` for restoring data along the way.

Placeholders used below: `<nas-ip>`, `<nas-volume-path>` (e.g. `/volume1`),
`<internal-domain>`, `<owner>` (your Forgejo user).

## 0. What you need

- Synology with DSM 7.2+, **Container Manager** installed (it provides Docker),
  SSH enabled for the local admin account.
- A domain whose DNS is at **Infomaniak** (Caddy gets its certificate by
  DNS-01 there; another provider means changing `stacks/edge/Dockerfile`).
- A LAN DNS you control (router), to point `*.<internal-domain>` at Caddy.
- One **free LAN IP** for Caddy, outside the DHCP range.
- A copy of this repo (any clone; Forgejo comes later).

## 1. DSM

1. **Shared folders**: `docker` (compose-adjacent config) plus one per data set:
   `forgejo`, `paperless`, `mealie`, `media`. Exact paths per stack: each
   `.env.example`.
2. **Package Center**: _LDAP Server_ (one account per person, a group
   `infra-admins` for those who may use Portainer/Forgejo — see
   `docs/ARCHITECTURE.md`, "Identity"), _Git Server_ (provides `git` for the
   scheduler), _Hyper Backup_, _Snapshot Replication_.
3. Join DSM to its own LDAP server (Control Panel → Domain/LDAP), so the same
   accounts work for DSM, SMB, Drive and the web apps.

## 2. The `proxy` network

Every web-facing container joins it. Copy the repo to the NAS, then:

```bash
sudo scripts/bootstrap.sh                     # creates the 'proxy' network, idempotent
```

## 3. Portainer

Deployed by hand: it is what deploys everything else.

```bash
sudo docker run -d --name portainer --restart=always \
  -p 9443:9443 \
  -v /var/run/docker.sock:/var/run/docker.sock \
  -v <nas-volume-path>/docker/portainer-data:<nas-volume-path>/docker/portainer-data \
  portainer/portainer-ce:2.39.5 \
  --data <nas-volume-path>/docker/portainer-data \
  --trusted-origins portainer.<internal-domain>
sudo docker network connect proxy portainer    # repeat after re-creating the container
```

- The data path must be **identical** inside and outside the container, plus
  `--data` pointing at it — otherwise Git stacks fail with
  `Bind mount failed: ... does not exist`.
- `--trusted-origins`: bare hostname, no scheme or port (either crashes
  Portainer at start).
- **Check:** `https://<nas-ip>:9443`, create the admin account.

## 4. Forgejo

Holds this repo; every other stack is deployed from it.
→ `stacks/forgejo/README.md`

1. Portainer → Stacks → Add stack → **Web editor** (not Repository: Forgejo
   cannot deploy itself from itself). Paste `stacks/forgejo/compose.yaml`,
   set its env vars, deploy.
2. Create the admin user, create the repo `<owner>/homelab`, push this repo:
   `git remote add forgejo http://<nas-ip>:3000/<owner>/homelab.git && git push forgejo main`.
3. Create an access token (Settings → Applications) with repository read
   access, for Portainer.
4. **Check:** `http://<nas-ip>:3000/<owner>/homelab` shows the files.

## Deploying a Repository stack (used from here on)

Portainer → Stacks → Add stack → **Repository**:

| Field                 | Value                                                                                                                             |
| --------------------- | --------------------------------------------------------------------------------------------------------------------------------- |
| Repository URL        | `http://<nas-ip>:3000/<owner>/homelab.git` — the **raw IP**, never a hostname (Portainer must reach it before Caddy and DNS work) |
| Authentication        | Forgejo user + the token from step 4                                                                                              |
| Reference             | `refs/heads/main`                                                                                                                 |
| Compose path          | `stacks/<name>/compose.yaml`                                                                                                      |
| Environment variables | every variable in `stacks/<name>/.env.example`, with real values                                                                  |

Updates later: **Pull and redeploy** on the stack. Secrets never go in git:
they live in these Portainer variables (Authelia: files, see its README).

## 5. edge (Caddy, HTTPS)

→ `stacks/edge/README.md`

1. Infomaniak API token with DNS scope; Caddy's free LAN IP and a MAC for it.
2. Deploy `stacks/edge` (Repository stack).
3. Router DNS: `*.<internal-domain>` → Caddy's LAN IP.
4. **Check:** `https://portainer.<internal-domain>` loads with a valid
   certificate.

## 6. authelia (login)

→ `stacks/authelia/README.md`

1. Create the secret files (session, storage, JWT, OIDC HMAC, JWKS key, LDAP
   bind password, one hash per OIDC client) — commands in the README.
2. Deploy `stacks/authelia`.
3. Configure the OIDC clients in their apps: Portainer (Settings →
   Authentication), Forgejo (Site Administration → Authentication Sources),
   Paperless and Mealie (their env vars, steps 7).
4. **Check:** `https://auth.<internal-domain>` accepts an LDAP login.

## 7. Applications

Any order, except Paperless before paperless-tarmed. Each: create its folders,
deploy as a Repository stack, follow its README's first-run steps.

| Stack                                   | README                               |
| --------------------------------------- | ------------------------------------ |
| mediarr (Jellyfin, \*arr, qBittorrent…) | `stacks/mediarr/README.md`           |
| paperless                               | `stacks/paperless/README.md`         |
| mealie                                  | `stacks/mealie/README.md`            |
| dashboard                               | `stacks/dashboard/README.md`         |
| paperless-tarmed (after paperless)      | `stacks/paperless-tarmed/INSTALL.md` |

## 8. CI (optional)

→ `stacks/forgejo-runner/README.md`

1. Register and deploy the runner.
2. Forgejo → repo → Settings → Actions → **Secrets** (not Variables):
   `RENOVATE_TOKEN` (Forgejo token that may open PRs),
   `RENOVATE_GITHUB_COM_TOKEN` (GitHub PAT with no scopes, for release notes),
   `PRIVATE_PATTERNS` (your `.private-patterns` content).
3. **Check:** a push runs the `checks` workflow green.

## 9. Scheduled jobs and backups

→ `docs/OPERATIONS.md`

1. Scheduler: clone the repo to `<nas-volume-path>/docker/homelab-ops` with a
   read-only deploy key, write `jobs.env`, create **one** DSM Task Scheduler
   entry every 15 minutes, connect healthchecks.io ("Scheduled jobs → Setup (once)").
2. Hyper Backup and Snapshot Replication: sources and rules in "Backups".
3. **Check:** the dashboard's left panel is green except what you expect
   (e.g. a dump that has not run yet).

## 10. Workstation

```bash
pip install pre-commit && pre-commit install   # gitleaks + no-real-values on every commit
cp .private-patterns.example .private-patterns # then add your real domain, names…
```
