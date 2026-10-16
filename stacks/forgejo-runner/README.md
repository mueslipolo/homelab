# forgejo-runner

Instance-wide Forgejo Actions runner for `.forgejo/workflows/` (checks, Renovate).

|            |                                                                               |
| ---------- | ----------------------------------------------------------------------------- |
| Reached at | not exposed                                                                   |
| Data       | `RUNNER_DATA_ROOT` → `config.yaml`, `.runner`, job cache                      |
| Needs      | `forgejo` (port `3000` on the host), `proxy` network, `/var/run/docker.sock`  |
| Config     | `.env.example` (every variable), `${RUNNER_DATA_ROOT}/config.yaml` (NAS only) |

`network_mode: host` reaches Forgejo at `127.0.0.1:3000`; docker.sock is mounted directly (`docker_host: automount`), no dind — see `docs/ARCHITECTURE.md`'s CI section.

## Deploy

See `docs/INSTALL.md` for the install order (last).

1. Make a secret and register it in the **Forgejo** container (idempotent; `--scope ""` = instance-wide). The printed UUID derives from the secret; keep both.
   ```bash
   openssl rand -hex 20
   docker exec forgejo forgejo forgejo-cli actions register \
     --secret <the-secret> --scope "" --labels docker --name nas-runner
   ```
2. Write `${RUNNER_DATA_ROOT}/config.yaml`:
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
         uuid: <uuid-from-step-1>
         token: <secret-from-step-1>
   ```
3. Portainer → Stacks → Add stack → Repository, `stacks/forgejo-runner/compose.yaml`, set `RUNNER_DATA_ROOT` → Deploy (it runs `forgejo-runner daemon --config /data/config.yaml`).
4. **Check:** **Idle** in Site Administration → Actions → Runners.

## Gotchas

- **Not the web UI token flow:** `forgejo-runner register --token …` fails with `invalid_argument: runner registration token not found` (server RPC returns 400) and is deprecated.
- **Data wiped?** Re-register with the **same secret** (same UUID), rewrite `config.yaml`. A new secret = a new runner entry.
- **`config.yaml` is read only at start**, silently — `docker restart forgejo-runner` after edits. Check what's live:
  ```bash
  grep -A4 '^container:' "$RUNNER_DATA_ROOT"/config.yaml
  docker run --rm --network proxy alpine wget -qO- http://forgejo:3000/api/v1/version
  ```
- **Keep `container.network: proxy`:** job containers don't inherit host networking; on their `WORKFLOW-<hash>` bridge `forgejo:3000` doesn't resolve and Renovate fails like an auth error.
- **`runs-on: docker` jobs run in `node:22-bookworm`** (bare label default), which has no `docker` CLI. Use a job-level `container:` image, not `docker run`.
- **Root (`user: "0:0"`), no `cap_drop`:** docker.sock is `root:root` 660 and UID 1000 fails even with `group_add: ["0"]` (Synology). Socket access is root-equivalent anyway.
- **Secrets:** only Renovate's tokens, as Forgejo Actions **Secrets** (root `README.md`). Deploys never use this runner.
- **Image** pinned in `compose.yaml`, updated by Renovate.
