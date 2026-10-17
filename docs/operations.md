# Operations

The maintainer's page: rules for this repo, updating, restoring, and the
conventions a new stack follows. Setting things up is in the
[guides](../README.md#guides); backups and the scheduler in
[guide 10](guides/10-backups-and-jobs.md).

## Rules

- **Secrets never go in git**, not even encrypted. App secrets: Portainer's
  per-stack environment variables. Authelia: files on the NAS. CI: Forgejo
  Actions **Secrets**.
- **No real values in tracked files.** Use the placeholders below; real values
  live in the gitignored `CLAUDE.local.md` and
  `PRIVATE-real-values-do-not-publish/`. The tree is publishable as is; the
  history was squashed before publishing, and stays clean.
- **Images are pinned** to exact tags; Renovate opens weekly update PRs
  (major updates wait for approval on its Dependency Dashboard).

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

Every commit runs **gitleaks** (credentials) and `scripts/check-no-real-values.sh`
(domains, IPs, paths, names; site patterns from the untracked
`.private-patterns`). CI (`.forgejo/workflows/checks.yaml`) repeats both on
every push, with the patterns from the `PRIVATE_PATTERNS` Actions secret, plus
shellcheck, the scheduler tests, Prettier, and paperless-tarmed's ruff and
pytest.

## Updating a stack

Every Repository stack has Portainer's **GitOps updates** on: polling every
5 minutes, _Force redeployment_ off. So:

1. Change it here (or merge Renovate's PR), push to Forgejo.
2. Within 5 minutes Portainer sees the new commit and redeploys the stacks
   whose files changed. Nothing to click. A merge therefore restarts
   containers at any hour.

Exceptions:

- **`forgejo`** is a Web editor stack: paste the new `compose.yaml` into
  Portainer by hand, Renovate bumps included.
- **`paperless-tarmed`**: Portainer never builds its image (it rebuilt
  unreliably). The poll redeploys on the push, before the `build-images` job
  has built the new image (within 15 minutes), so the old one keeps running;
  the dashboard shows amber "update waiting". Then **Pull and redeploy** by
  hand.
- **`edge`** builds in Portainer (`pull_policy: build`), and a redeploy may
  still reuse the old image. After a `Dockerfile` change, check its container
  was recreated (`docker inspect --format '{{.Created}}' <caddy-container>`);
  if not, Pull and redeploy by hand. A Caddyfile change needs nothing: Caddy
  reloads it itself (`--watch`).
- **`authelia`**: a redeploy does not restart it when only
  `config/configuration.yml` changed, and it reads the file at start. After an
  access-rule or OIDC client change, restart its container (Portainer →
  Containers → Restart).
- Not the webhook instead of polling: Forgejo would have to reach
  `portainer:9443` over `proxy`, never a public hostname.

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

**After a total loss**, rebuild in [install-everything](install-everything.md)'s order, restoring each
stack's data before deploying it; Forgejo's dump goes back with [guide 04](guides/04-forgejo-gitops.md). If
Forgejo cannot be restored, any clone of this repo works: deploy the stacks as
Web editor stacks from it until Forgejo is back.

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
