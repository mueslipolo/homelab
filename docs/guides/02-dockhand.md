# Dockhand: deploying stacks

> **Needs:** [01 Synology prep](01-synology-prep.md) · **Optional:** [04 Forgejo](04-forgejo-gitops.md) (a git source on the NAS), [03 HTTPS](03-https-letsencrypt.md) (its hostname), [05 Authelia](05-sso-authelia.md) (login), [10 Backups and jobs](10-backups-and-jobs.md) (the scheduler's token) · **Used by:** every stack guide
> **Reference:** [`stacks/dockhand/README.md`](../../stacks/dockhand/README.md) · **Why it is built this way:** [architecture](../architecture.md#deployment)

## What you get

[Dockhand](https://github.com/Finsys/dockhand), a web UI that deploys each
`stacks/<name>/compose.yaml` as a **git stack**: it keeps the stack's variables
(secrets included) out of git, and redeploys it when its folder changes in git,
within seconds of a push with a webhook, at its scheduled sync without.

- **Dockhand, not Container Manager's projects:** Container Manager cannot
  adopt containers it did not create, and has no git source.
- **Dockhand itself is started by hand** with `docker compose`: it is what
  deploys everything else, so nothing deploys it.
- **A stack's variables:** plain ones in a `.env.dockhand` next to the deployed
  files, **secret** ones encrypted in Dockhand's database and passed to compose
  in its environment only.
- **License:** Business Source License 1.1, free for a personal home lab;
  each release turns Apache 2.0 after four years. Weekly releases: read the
  release notes before an update.

Written against Dockhand 1.0.56; the menu names below are that version's and
may move.

## Before you start

| What                    | Example                         | How                                                                                                                        |
| ----------------------- | ------------------------------- | -------------------------------------------------------------------------------------------------------------------------- |
| SSH + Docker on the NAS | —                               | [01 Synology prep](01-synology-prep.md), steps 1–2.                                                                        |
| The `proxy` network     | —                               | [01](01-synology-prep.md), step 5. Caddy reaches Dockhand on it, and Dockhand reaches Authelia ([05](05-sso-authelia.md)). |
| A data folder           | `/volume1/docker/dockhand-data` | In the `docker` shared folder (Hyper Backup): `sudo mkdir -p /volume1/docker/dockhand-data`.                               |
| Its compose file        | `/volume1/docker/dockhand/`     | `stacks/dockhand/compose.yaml` and `.env.example` from your clone of this repo, copied to the NAS (File Station or `scp`). |
| A hostname (optional)   | `dockhand.home.example.com`     | Only if it will be served through Caddy ([03](03-https-letsencrypt.md)).                                                   |

## Steps

### 1. Choose the image

```bash
grep -c avx2 /proc/cpuinfo    # on the NAS
```

`0` (the DS920+'s Celeron J4125): keep `fnsys/dockhand:<version>-baseline` in
`compose.yaml`. Dockhand's default image needs AVX2 and exits at once with
`Illegal instruction` without it. Anything else: the tag without `-baseline`
works too.

**Check:** the tag in `compose.yaml` matches.

### 2. Start Dockhand

```bash
cd /volume1/docker/dockhand
sudo cp .env.example dockhand.env && sudo chmod 600 dockhand.env   # edit it
echo 'DOCKHAND_HOST_IP=0.0.0.0' | sudo tee -a dockhand.env         # until Caddy serves it (step 7)
sudo docker compose -p dockhand --env-file dockhand.env -f compose.yaml up -d
```

- **`DOCKHAND_HOST_IP=0.0.0.0`** publishes port 3010 on the LAN for the first
  setup. Without it, the port is on the NAS's loopback only (for the scheduler),
  and DSM's SSH server refuses port forwarding, so no tunnel reaches it.
- **Same data path inside and outside** (`compose.yaml` keeps them equal):
  Dockhand runs `docker compose` itself and hands the daemon paths under its
  data folder.
- **Once the scheduler's clone exists** ([10](10-backups-and-jobs.md), step 2),
  start and upgrade Dockhand from there, so a Renovate bump of its tag reaches
  it with the next pull (keep `dockhand.env` outside the clone, e.g. next to
  `jobs.env`):
  `sudo docker compose -p dockhand --env-file <dockhand.env> -f /volume1/docker/homelab-ops/stacks/dockhand/compose.yaml up -d`.

Then **right away**, at `http://192.0.2.10:3010`:

1. Create the first user (a local admin: the way in when Authelia is down).
2. **Settings → Authentication: turn authentication on.** A fresh install has
   it off, and then the UI and the API answer anyone who reaches them. API
   tokens need it on too.
3. Settings → Environments: the local Docker socket (`/var/run/docker.sock`).
   Add it if it is not listed.

**Check:** `sudo docker logs dockhand` ends without errors; a private window at
`http://192.0.2.10:3010` asks for a login;
`sudo docker stats --no-stream dockhand` stays well under the 1 GB limit.

### 3. The git source

Settings → Git:

1. **Credential** (private repository only): username + password, the git user
   and an access token with repository read (Forgejo: `read:repository`,
   [04](04-forgejo-gitops.md), step 2).
2. **Repository:** `http://192.0.2.10:3000/<owner>/homelab.git` (Forgejo, the
   **raw IP**: it must work before Caddy and DNS do, and after they break), or
   `https://github.com/<you>/homelab.git`; branch `main`; that credential.
   **Test** it.

An SSH deploy key works too, but Dockhand does not check the host key
(`StrictHostKeyChecking=no`): a token over the LAN is no worse.

**Check:** the repository's test passes.

### 4. Deploy a git stack

Stacks → **from Git**:

| Field                          | Value                                                                                                                                                                                                                                              |
| ------------------------------ | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Name                           | the folder name (`edge`, `mediarr`…): the compose project, and what the dashboard and the scheduler match stacks by. **Dockhand pre-fills the repository's name (`homelab`): change it**                                                           |
| Repository                     | the one from step 3                                                                                                                                                                                                                                |
| Compose path                   | `stacks/<name>/compose.yaml`                                                                                                                                                                                                                       |
| Environment variables          | every variable in `stacks/<name>/.env.example`, with real values. The panel's **text view** takes `NAME=value` lines pasted at once. Mark every password, token and key **secret**                                                                 |
| Enable scheduled sync          | on: hourly at the stack's own minute (`7 * * * *`, `13 * * * *`…), or daily (`5 4 * * *`, `10 4 * * *`…) for a stack with a webhook (step 5). Each sync re-clones the repo: every 5 minutes for ten stacks would be 120 clones an hour on the RAID |
| Enable webhook                 | on, with a secret, once Forgejo may call Dockhand (step 5)                                                                                                                                                                                         |
| Re-pull images, Force redeploy | off. **Re-pull images** must stay off for a stack whose image the NAS builds itself (`paperless-tarmed`, `pull_policy: never`)                                                                                                                     |
| Build images on deploy         | off                                                                                                                                                                                                                                                |

Then **Deploy**.

- **"Enable scheduled sync" is the git option.** Not the containers' own
  **Auto-update**, which stays off: Renovate bumps the tags in git.
- **A sync deploys only when the stack's folder changed**: a plain
  `docker compose up -d --remove-orphans`, which recreates only the containers
  whose definition changed.
- **Files next to `compose.yaml`** (Caddy's and Authelia's config folders) are
  mounted from Dockhand's copy of the stack: `./…` paths work, **only inside
  the stack's own folder**.
- **Secrets go in the variables, never in git.** Exception: Authelia reads its
  secrets from files ([05](05-sso-authelia.md)).

**Check:** the stack's containers run (and are `healthy` where they have a
healthcheck), from Dockhand's folder:

```bash
sudo docker ps --filter label=com.docker.compose.project=<name>
sudo docker inspect --format '{{ index .Config.Labels "com.docker.compose.project.working_dir" }}' \
  $(sudo docker ps -q --filter label=com.docker.compose.project=<name>)   # under /volume1/docker/dockhand-data/stacks/
```

### 5. A webhook per stack (with Forgejo)

With a webhook, a push deploys within seconds instead of at the next scheduled
sync; keep the scheduled sync (daily, then) as the safety net. Dockhand has one
webhook per stack (`/api/git/stacks/<id>/webhook`), checks Forgejo's signature
(`X-Hub-Signature-256`) against the stack's secret, and deploys only if the
stack's folder changed. Forgejo is on `proxy`, so it calls Dockhand by name,
not through Caddy.

1. **Forgejo may call Dockhand:** it refuses webhooks to LAN addresses unless
   allowed; `stacks/forgejo/compose.yaml` allows `dockhand`
   (`FORGEJO__webhook__ALLOWED_HOST_LIST`).
2. **Dockhand:** the stack → edit → Enable webhook **on**, with a secret
   (`openssl rand -hex 32`); save. Note the stack's id in the webhook URL it
   shows, but not the URL itself: it is the `https://dockhand.<internal-domain>`
   one, which Forgejo cannot reach (Caddy's macvlan IP, from the same host).
3. **Forgejo:** the `homelab` repository → Settings → Webhooks → Add webhook →
   **Gitea** (sends `X-Hub-Signature-256`):

   | Field       | Value                                              |
   | ----------- | -------------------------------------------------- |
   | Target URL  | `http://dockhand:3000/api/git/stacks/<id>/webhook` |
   | HTTP method | POST, content type `application/json`              |
   | Secret      | the secret from action 2                           |
   | Trigger on  | Push events, branch filter `main`                  |

   Add webhook, then **Test delivery**.

One Forgejo webhook per stack: every push calls them all, and the stacks whose
folder did not change skip. `/api/git/webhook/<id>` is not it: it takes a
_repository_ id and deploys the whole repository as one stack named after it.

**Check:** Test delivery answers `200` with `"success":true` (`"skipped":true`
when nothing changed is fine too). Then a real push: change something in the
stack's folder (not just Markdown), push, and within seconds Dockhand's stack
shows the new commit and the changed container was recreated.

### 6. The scheduler's token (with [10](10-backups-and-jobs.md))

Dockhand → Profile → API tokens → create one (`dh_…`, shown once), into
`jobs.env` as `DOCKHAND_TOKEN` ([10](10-backups-and-jobs.md), step 3). The
scheduler reaches Dockhand on `http://127.0.0.1:3010`: the stack list for the
dashboard, and redeploys of the images the NAS builds itself. The token has its
user's rights (all of them, free edition): `jobs.env` stays root-only.

**Check:** after the next tick, the dashboard's System tab lists every git
stack ([09](09-dashboard.md)).

### 7. Behind Caddy

With [03 HTTPS](03-https-letsencrypt.md) in place, the full Caddyfile already
serves `https://dockhand.home.example.com` (no `import authelia`: its login is
its own, or OIDC through Authelia, step 8). With `Caddyfile.minimal`, add its
block:

```
dockhand.{$INTERNAL_DOMAIN} {
	import tls_wildcard
	reverse_proxy dockhand:3000
}
```

Then take port 3010 off the LAN: remove the `DOCKHAND_HOST_IP` line from
`dockhand.env`, then the same `docker compose … up -d` as in step 2.

**Check:** `https://dockhand.home.example.com` asks for a login without a
certificate warning; `http://192.0.2.10:3010` no longer answers;
`curl -s -o /dev/null -w '%{http_code}\n' http://127.0.0.1:3010/` on the NAS
still does.

### 8. Log in through Authelia (optional)

With [05 Authelia](05-sso-authelia.md) running. Following guide 05 with the
default `OIDC_CLIENTS` does actions 1–3; then only action 4 is left. On the
NAS (`AUTHELIA_SECRETS_ROOT`, here `/volume1/docker/authelia/secrets`):

1. **Generate the secret.** This only prints two values; nothing changes yet:

   ```bash
   AUTHELIA_VERSION=4.39.28   # the tag in stacks/authelia/compose.yaml
   sudo docker run --rm "authelia/authelia:$AUTHELIA_VERSION" authelia crypto hash generate pbkdf2 --random --random.length 64 --random.charset alphanumeric
   ```

   `Random Password: …` is for Dockhand (action 4); `Digest: $pbkdf2-…` is for
   Authelia (action 2).

2. **Give Authelia the Digest**, in single quotes (it contains `$`), without
   the `Digest: ` prefix; same owner as the other `*_client_secret_hash` files
   (`ls -l` the folder, `sudo chown` if needed):

   ```bash
   echo -n '<Digest>' | sudo tee /volume1/docker/authelia/secrets/dockhand_client_secret_hash >/dev/null
   sudo chmod 600 /volume1/docker/authelia/secrets/dockhand_client_secret_hash
   ```

3. **Turn the client on.** `dockhand` must be in the authelia stack's
   `OIDC_CLIENTS`: it is in the compose default, but a stack that sets the
   variable must list it. Then restart Authelia, which reads its configuration
   only at start:

   ```bash
   sudo docker restart $(sudo docker ps -q --filter name=authelia)
   sudo docker logs --tail 5 $(sudo docker ps -q --filter name=authelia)   # no error
   ```

4. **Give Dockhand the Random Password.** Settings → Authentication → OIDC, add
   a provider:

   | Field        | Value                                                      |
   | ------------ | ---------------------------------------------------------- |
   | Issuer URL   | `https://auth.home.example.com`                            |
   | Client ID    | `dockhand`                                                 |
   | Secret       | the `Random Password`                                      |
   | Redirect URI | `https://dockhand.home.example.com/api/auth/oidc/callback` |
   | Scopes       | `openid profile email groups`                              |

Dockhand reaches `auth.home.example.com` through Caddy's alias on `proxy`.
Users are created at their first login. The free edition makes every user an
admin: the `admins_only` policy (group `infra-admins`) is what keeps family
accounts out.

**Check:** a user in `infra-admins` logs in through Authelia; one outside it is
refused (`error=access_denied` in the URL). The local admin still logs in.

## Day to day

| Task                       | In Dockhand                                                                                                                                                                                        |
| -------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Change a stack             | push to git; the webhook (or the scheduled sync) deploys it                                                                                                                                        |
| Deploy now                 | the stack → **Deploy**                                                                                                                                                                             |
| Change a variable          | the stack → edit → its variables → save, then **Deploy**                                                                                                                                           |
| Restart a container        | Containers → the container → **Restart** (a deploy does not restart a container whose definition did not change)                                                                                   |
| A pasted stack (`forgejo`) | Stacks → Create, paste the compose file; update it by pasting the new file, Renovate bumps included                                                                                                |
| Take a stack off for good  | the stack → **Delete**, then `docker ps` shows none of its containers (else `docker stop`, then `docker rm` them). Not Stop: a stopped `restart: always` container starts again at the next reboot |
| Upgrade Dockhand           | after its Renovate PR is merged and the scheduler's clone has pulled it (every tick): the `docker compose … up -d` of step 2                                                                       |

## Troubleshooting

| Symptom                                                                     | Cause → fix                                                                                                                                                                                                                                 |
| --------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Dockhand exits at once, `Illegal instruction`                               | The image needs AVX2: use the `-baseline` tag (step 1).                                                                                                                                                                                     |
| `http://<nas-ip>:3010` does not answer on a fresh install                   | `DOCKHAND_HOST_IP=0.0.0.0` is missing from `dockhand.env` (step 2). There is no SSH tunnel to the loopback port: DSM's SSH server refuses port forwarding.                                                                                  |
| A `./…` bind mount is an empty folder, or `bind source path does not exist` | `DATA_DIR` differs from the path mounted (`compose.yaml` keeps them equal). Recreate Dockhand with the same path on both sides.                                                                                                             |
| Creating the git stack: `A stack named … already exists`                    | Containers of that compose project are running (started by hand, or left from an earlier deploy). Stop, then remove them (`docker stop`, then `docker rm`: databases shut down cleanly), then create it.                                    |
| The stack is named `homelab`, or the dashboard does not list it             | The form pre-filled the repository's name. Delete the stack and create it again with the folder's name.                                                                                                                                     |
| Sync error `Authentication failed` / 401 from Forgejo                       | The credential: the git user + a token with `read:repository`; the raw IP, not the hostname.                                                                                                                                                |
| `pull access denied for paperless-tarmed`                                   | Re-pull images is on: it runs `up --pull always`, which overrides `pull_policy: never`. Turn it off.                                                                                                                                        |
| A stack is recreated at every sync                                          | Force redeploy is on. Turn it off.                                                                                                                                                                                                          |
| A changed config file is not picked up after a deploy                       | A deploy only recreates a container when its compose definition changed, not when a mounted file did. Mount the folder (a single file can stay pinned to the old one), and make the app reload (Caddy: `--watch`) or restart the container. |
| Webhook test delivery: `401`                                                | The secret, or the signature header. If it stays `401` with the right secret, use the **Forgejo** webhook type instead of Gitea.                                                                                                            |
| Webhook test delivery: `webhook can only call allowed HTTP servers`         | Forgejo's `FORGEJO__webhook__ALLOWED_HOST_LIST` does not list `dockhand` (step 5).                                                                                                                                                          |
| Webhook delivery: `Response 0`, nothing reaches Dockhand                    | The Target URL is Dockhand's displayed `https://dockhand.<internal-domain>/…`: Forgejo cannot reach Caddy from the same host. Use `http://dockhand:3000/api/git/stacks/<id>/webhook` (step 5).                                              |
| OIDC: Authelia says the redirect URI is invalid                             | The Redirect URI must be exactly `https://dockhand.home.example.com/api/auth/oidc/callback`.                                                                                                                                                |
| OIDC: Authelia's log names `dockhand_client_secret_hash`, or no client      | The file is missing, or `dockhand` is not in the authelia stack's `OIDC_CLIENTS` (step 8).                                                                                                                                                  |
| OIDC: `No route to host` / discovery fails                                  | Dockhand is not on `proxy` (`compose.yaml` puts it there; recreated by hand without it?).                                                                                                                                                   |
| `redeploy-images`: `Dockhand's deploy failed: …`                            | Dockhand's reason; the stack's Deploys tab has the log. A failed deploy is retried by Dockhand at its next sync, and by the job at its next tick.                                                                                           |

## Next

- [03 HTTPS](03-https-letsencrypt.md): the `edge` stack, the first git stack to deploy.
- [04 Forgejo](04-forgejo-gitops.md): host the repo on the NAS and deploy from it.
- [10 Backups and jobs](10-backups-and-jobs.md): Hyper Backup of
  `/volume1/docker/dockhand-data` (its `.encryption_key` and database go
  together).
