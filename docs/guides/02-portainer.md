# Portainer: deploying stacks

> **Needs:** [01 Synology prep](01-synology-prep.md) · **Optional:** [04 Forgejo](04-forgejo-gitops.md) (a git source on the NAS) · **Used by:** every stack guide
> **Reference:** [root `README.md`](../../README.md) · **Why it is built this way:** [architecture](../architecture.md#deployment)

## What you get

A web UI that deploys each `stacks/<name>/compose.yaml` as a **stack**, keeps
its variables (secrets included) out of git, and redeploys from git with one
button.

```
git (Forgejo on the NAS, or a GitHub fork)
   │ clone on deploy / "Pull and redeploy"
   ▼
Portainer ──docker.sock──▶ stack = one compose project, variables stored in Portainer
```

- **Portainer, not Container Manager's projects:** Container Manager cannot
  adopt containers it did not create, and has no git source.
- **Portainer itself is a plain `docker run`:** it is what deploys everything
  else, so nothing deploys it.

## Before you start

| What                    | Example                          | How                                                                            |
| ----------------------- | -------------------------------- | ------------------------------------------------------------------------------ |
| SSH + Docker on the NAS | —                                | [01 Synology prep](01-synology-prep.md), steps 1–2.                            |
| The `proxy` network     | —                                | [01](01-synology-prep.md), step 5. Portainer joins it so Caddy can reach it.   |
| A data folder           | `/volume1/docker/portainer-data` | In the `docker` shared folder: `sudo mkdir -p /volume1/docker/portainer-data`. |
| A hostname (optional)   | `portainer.home.example.com`     | Only if it will be served through Caddy ([03](03-https-letsencrypt.md)).       |

## Steps

### 1. Install Portainer

```bash
sudo docker run -d --name portainer --restart=always \
  -p 9443:9443 \
  -v /var/run/docker.sock:/var/run/docker.sock \
  -v /volume1/docker/portainer-data:/volume1/docker/portainer-data \
  portainer/portainer-ce:2.39.5 \
  --data /volume1/docker/portainer-data \
  --trusted-origins portainer.home.example.com
sudo docker network connect proxy portainer
```

- **The data path is identical inside and outside the container**, and
  `--data` points at it. Portainer clones Git stacks into its data folder and
  hands those paths to the Docker daemon for bind mounts; with the usual
  `-v …:/data`, Git stacks fail with `Bind mount failed: ... does not exist`.
- **`--trusted-origins`:** a bare hostname, no scheme and no port (either
  crashes Portainer at start). Omit it if you don't use Caddy.
- **`docker network connect proxy portainer`** again after every re-creation
  of the container (upgrades included).
- **Upgrade:** `docker rm -f portainer`, the same `docker run` with the new
  tag, `network connect` again. The data folder keeps everything.

**Check:** `https://192.0.2.10:9443` (self-signed certificate) → create the
admin account within a few minutes of the start, or Portainer locks setup and
needs a restart.

### 2. Choose how to deploy a stack

| Kind (Stacks → Add stack) | Source                        | Builds images | Updates               | Use for                                                         |
| ------------------------- | ----------------------------- | ------------- | --------------------- | --------------------------------------------------------------- |
| **Repository**            | a git repo + compose path     | yes¹          | **Pull and redeploy** | everything, once a git source exists                            |
| **Web editor**            | compose pasted into Portainer | **no**        | paste the new file    | Forgejo itself ([04](04-forgejo-gitops.md)); trying a stack out |

¹ Stacks with `build:` (like `edge`) build on deploy, but Portainer sometimes
reuses the old image: see Troubleshooting.

Repository stacks also mount files next to `compose.yaml` (Caddyfile,
Authelia's config): Portainer clones the repo, so `./…` paths work, **only
inside the stack's own folder**.

### 3. Deploy a Repository stack

Stacks → Add stack → **Repository**:

| Field                 | Value                                                                                                                                               |
| --------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------- |
| Name                  | the folder name (`edge`, `mediarr`…): the dashboard and the scheduler match stacks by it                                                            |
| Repository URL        | `http://192.0.2.10:3000/<owner>/homelab.git` (Forgejo, **raw IP**: it must work before Caddy and DNS do), or `https://github.com/<you>/homelab.git` |
| Authentication        | for a private repo: the git user + an access token with repository read                                                                             |
| Reference             | `refs/heads/main`                                                                                                                                   |
| Compose path          | `stacks/<name>/compose.yaml`                                                                                                                        |
| Environment variables | every variable in `stacks/<name>/.env.example`, with real values ("Advanced mode" pastes a whole `.env`)                                            |

Secrets go in these variables, never in git. Exception: Authelia reads its
secrets from files ([05](05-sso-authelia.md)).

**Check:** Stacks → the stack → all containers `running` (and `healthy` where
they have a healthcheck).

### 4. Update a stack

1. Change the stack in git (or merge a Renovate PR, [04](04-forgejo-gitops.md)), push.
2. Portainer → Stacks → the stack → **Pull and redeploy**.

**Automatic updates** (stack → GitOps updates) suit image-only stacks:

- **Polling**, e.g. every 5 minutes. Not the webhook: Forgejo would have to
  reach `portainer:9443` over `proxy`, never a public hostname.
- **Force redeployment off.**
- Not for stacks that build (`edge`, see below). A merge then restarts
  containers at any hour: decide per stack.

**Check:** after a redeploy, the stack's page shows the new commit, and
`docker ps` shows the containers recreated (`CREATED` column).

### 5. Behind Caddy (optional)

With [03 HTTPS](03-https-letsencrypt.md) in place, Caddy serves Portainer at
`https://portainer.home.example.com`. Portainer runs outside `proxy`'s compose
projects, so Caddy reaches it through the `proxy` gateway with TLS
verification off (the hop never leaves the host):

```
portainer.{$INTERNAL_DOMAIN} {
	import tls_wildcard
	reverse_proxy {env.PROXY_GATEWAY_IP}:9443 {
		transport http {
			tls_insecure_skip_verify
		}
	}
}
```

Never write `https://{env.PROXY_GATEWAY_IP}:9443`: a scheme plus a
placeholder crash-loops Caddy. Login through Authelia (OIDC):
[05](05-sso-authelia.md).

**Check:** `https://portainer.home.example.com` loads without a certificate
warning.

## Troubleshooting

| Symptom                                                        | Cause → fix                                                                                                                                                                                                                                                       |
| -------------------------------------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `Bind mount failed: ... does not exist` on a Repository stack  | Portainer's data path differs inside and outside the container. Recreate it with the identical `-v` path and `--data` (step 1).                                                                                                                                   |
| Portainer exits at start after adding `--trusted-origins`      | A scheme or port in the value. Bare hostname only.                                                                                                                                                                                                                |
| Caddy: `502` for Portainer                                     | Portainer was recreated and left `proxy`: `docker network connect proxy portainer`.                                                                                                                                                                               |
| Redeploy "succeeds" but a built image is old                   | Portainer skipped the build. Keep `pull_policy: build` and no `image:` key; check `docker inspect -f '{{.Created}}' <container>` moved. This repo builds `paperless-tarmed` outside Portainer for that reason ([10](10-backups-and-jobs.md), `build-images` job). |
| A changed config file is not picked up after Pull and redeploy | Single-file bind mount: the container keeps the old file. Mount the folder, or restart the container.                                                                                                                                                             |
| Repository stack: `authentication required`                    | Private repo: add the git user and token under Authentication.                                                                                                                                                                                                    |
| A bind mount to `../something` is empty                        | Paths outside `stacks/<name>/` are not resolved in Portainer's clone. Keep mounted files in the stack's folder.                                                                                                                                                   |

## Next

- [03 HTTPS](03-https-letsencrypt.md): the `edge` stack, the first Repository stack to deploy.
- [04 Forgejo](04-forgejo-gitops.md): host the repo on the NAS and deploy from it.
