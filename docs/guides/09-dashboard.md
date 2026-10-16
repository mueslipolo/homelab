# Dashboard: one page for every service and its health

> **Needs:** [03 HTTPS](03-https-letsencrypt.md) · **Optional:** [05 Authelia](05-sso-authelia.md) (login gate), [10 Backups and jobs](10-backups-and-jobs.md) (everything beyond the links), [02 Portainer](02-portainer.md) (or plain `docker compose`) · **Used by:** —
> **Reference:** [`stacks/dashboard/README.md`](../../stacks/dashboard/README.md)

## What you get

A static page at `https://dashboard.home.example.com` (unprivileged nginx,
read-only container): a card per service, and tabs for backups, deployments,
jobs, storage and the network.

```
scheduler (DSM task, every 15 min) ──writes──▶ /volume1/docker/dashboard-local/
                                                 status.json, storage.json   (by the jobs)
                                                 devices.json                (by hand)
                                                         │ mounted read-only at html/local/
browser ◀── Caddy ◀── dashboard (nginx, html/ from git) ─┘
```

- **No backend, no secrets:** the page reads JSON files. The scripts that
  write them run as root on the NAS ([10 Backups and jobs](10-backups-and-jobs.md));
  without them you get the service cards only.
- **The browser computes ages**, so a stalled script turns its light red rather
  than staying green.
- **Real hostnames stay out of git:** they live in `devices.json`, next to the
  generated files.

## Before you start

| What                  | Example                           | How                                            |
| --------------------- | --------------------------------- | ---------------------------------------------- |
| Caddy                 | `dashboard.home.example.com`      | [03 HTTPS](03-https-letsencrypt.md)            |
| A local folder        | `/volume1/docker/dashboard-local` | Readable by everyone (nginx runs unprivileged) |
| The scheduler (later) | `run-jobs.sh` every 15 minutes    | [10 Backups and jobs](10-backups-and-jobs.md)  |

## Steps

### 1. The local folder

```bash
sudo mkdir -p /volume1/docker/dashboard-local
sudo chmod 755 /volume1/docker/dashboard-local
```

Optionally add `devices.json` (mode 644): the Network tab's cards and labels,
see the [reference](../../stacks/dashboard/README.md#devicesjson). Without it,
the tab shows the diagram with generic labels.

### 2. Make the cards yours

The services are the `GROUPS` / `OPS` arrays in `stacks/dashboard/html/index.html`.
Links derive from the page's address (`https://<app>.home.example.com`), so a
card needs only its subdomain. Logos: `html/icons/<subdomain>.png`, or mapped
in `ICON`.

### 3. Deploy

The only variable is `DASHBOARD_LOCAL_DIR=/volume1/docker/dashboard-local`;
the deploy fails without it.

**With Portainer:** Stacks → Add stack → Repository, compose path
`stacks/dashboard/compose.yaml` → Deploy ([02 Portainer](02-portainer.md)).
**With docker compose:** in a copy of `stacks/dashboard/`,
`echo DASHBOARD_LOCAL_DIR=/volume1/docker/dashboard-local > .env && sudo docker compose up -d`.

### 4. Route it through Caddy

```
dashboard.{$INTERNAL_DOMAIN} {
	import tls_wildcard
	import authelia
	reverse_proxy dashboard:8080
}
```

Drop `import authelia` without [05 Authelia](05-sso-authelia.md); with it, the
host also needs an `access_control` rule in Authelia.

**Check:** `https://dashboard.home.example.com` shows the cards; the left
panel says "No status report".

### 5. Health, backups, storage

Set up the scheduler with the same `DASHBOARD_LOCAL_DIR` in its `jobs.env`
([10 Backups and jobs](10-backups-and-jobs.md)). Each tick ends with
`scripts/dashboard-status.sh` writing `status.json`; the nightly
`storage-scan` job writes `storage.json`. Optional in `jobs.env`:
`PORTAINER_TOKEN` (System tab: stacks and their deployed commit) and
`uploaders.conf` (Backups tab: phones and computers that must keep uploading).

**Check:** within 15 minutes "No status report" turns into status lights;
the Storage tab fills after the first night.

## Troubleshooting

| Symptom                                 | Cause → fix                                                                                                           |
| --------------------------------------- | --------------------------------------------------------------------------------------------------------------------- |
| `403` or a tab stays empty              | A file in the local folder is not world-readable (nginx runs unprivileged): `chmod 644`.                              |
| Deploy fails: mount source missing      | `DASHBOARD_LOCAL_DIR` unset, or the folder does not exist.                                                            |
| Still "No status report" after 15 min   | The scheduler does not write here: compare `DASHBOARD_LOCAL_DIR` in `jobs.env`, read `$OPS_HOME/logs/`.               |
| Every Hyper Backup task looks old       | DSM's log moved after a package upgrade: set `HYPERBACKUP_LOG` in `jobs.env` (reference, `status.json`).              |
| Stacks all "unknown"                    | Their deployed commit is not in the scheduler's clone (history rewritten?): Pull and redeploy each.                   |
| Container restarts after a compose edit | `read_only: true` needs its tmpfs mounts (`/tmp`, `/var/cache/nginx`, `/var/run`), and nginx listens on 8080, not 80. |

## Next

- [10 Backups and jobs](10-backups-and-jobs.md): the jobs that feed the lights.
- The [reference](../../stacks/dashboard/README.md): what each light means and where its data comes from.
