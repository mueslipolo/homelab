# forgejo-runner

Instance-wide Forgejo Actions runner for `.forgejo/workflows/` (checks on every
push, Renovate daily).

**Setting it up: [guide 04, Forgejo: the NAS deploys from git](../../docs/guides/04-forgejo-gitops.md#5-ci-runner-checks-renovate-optional).**
This page is the reference.

|            |                                                                                                                |
| ---------- | -------------------------------------------------------------------------------------------------------------- |
| Reached at | not exposed                                                                                                    |
| Data       | `RUNNER_DATA_ROOT` → `config.yaml`, `.runner`, job cache. Nothing to back up: re-register with the same secret |
| Needs      | [forgejo](../forgejo/README.md) (port `3000` on the host), `proxy` network, `/var/run/docker.sock`             |
| Used by    | `.forgejo/workflows/checks.yaml`, `.forgejo/workflows/renovate.yaml`                                           |
| Config     | `.env.example` (every variable), `${RUNNER_DATA_ROOT}/config.yaml` (NAS only, below)                           |

`network_mode: host` reaches Forgejo at `127.0.0.1:3000`; docker.sock is mounted
directly (`docker_host: automount`), no docker-in-docker
([architecture](../../docs/architecture.md#ci-and-secrets)).

## config.yaml

Registration (in the Forgejo container; `--scope ""` = instance-wide;
idempotent; the printed UUID derives from the secret):

```bash
openssl rand -hex 20
docker exec forgejo forgejo forgejo-cli actions register \
  --secret <the-secret> --scope "" --labels docker --name nas-runner
```

```yaml
runner:
  file: .runner
  capacity: 1
  labels:
    - docker
cache:
  enabled: true
container:
  docker_host: "automount"
  network: proxy
server:
  connections:
    nas-runner:
      url: http://127.0.0.1:3000/
      uuid: <uuid-from-register>
      token: <the-secret>
```

## Gotchas

- **The `checks` workflow runs in `homelab-ci:1`**, an image with its tools
  baked in (`.forgejo/ci/Dockerfile`: git, shellcheck, jq, gitleaks, uv, Node
  and prettier), built on the NAS by the `build-images` job and only when that
  folder changes. A push no longer apt-gets and downloads its tools. The runner
  uses the local image and never pulls it: right after a fresh install, or if
  the image was pruned, jobs fail with `pull access denied for homelab-ci`
  until the next scheduler tick has built it (or run `scripts/build-images.sh
.forgejo/ci=homelab-ci:1` by hand).

- **Register with `forgejo-cli`, not a web UI token:** `forgejo-runner register --token …`
  fails with `invalid_argument: runner registration token not found` and is deprecated.
- **Data wiped?** Re-register with the **same secret** (same UUID) and rewrite
  `config.yaml`. A new secret makes a new runner entry.
- **`config.yaml` is read only at start**, silently: `docker restart forgejo-runner`
  after edits. What's live:
  ```bash
  grep -A4 '^container:' "$RUNNER_DATA_ROOT"/config.yaml
  docker run --rm --network proxy alpine wget -qO- http://forgejo:3000/api/v1/version
  ```
- **Keep `container.network: proxy`:** job containers don't inherit host
  networking; on their own `WORKFLOW-<hash>` bridge `forgejo:3000` doesn't
  resolve and Renovate fails like an auth error. A hostname is no way out: it
  resolves to Caddy's macvlan IP, unreachable from the host.
- **`runs-on: docker` jobs run in `node:22-bookworm`** (bare label default),
  which has no `docker` CLI. Use a job-level `container:` image, not `docker run`.
- **Root (`user: "0:0"`), no `cap_drop`:** docker.sock is `root:root` 660 and
  UID 1000 can't use it even with `group_add: ["0"]` on DSM. Socket access is
  root-equivalent anyway.
- **Secrets:** only Renovate's tokens and `PRIVATE_PATTERNS`, as Forgejo Actions
  **Secrets**. Names can't start with `GITHUB_`, `GITEA_` or `FORGEJO_`.
  Deploys never use this runner.
- **Image** pinned in `compose.yaml`, updated by Renovate.
