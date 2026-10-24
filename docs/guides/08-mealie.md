# Mealie: recipes and meal plans

> **Needs:** [03 HTTPS](03-https-letsencrypt.md), [05 Authelia](05-sso-authelia.md) (the only login) · **Optional:** [02 Portainer](02-portainer.md) (or plain `docker compose`), [10 Backups and jobs](10-backups-and-jobs.md) (daily backup) · **Used by:** —
> **Reference:** [`stacks/mealie/README.md`](../../stacks/mealie/README.md)

## What you get

Mealie at `https://mealie.home.example.com`, with its own postgres. Opening it
goes straight to Authelia and back: Mealie's login screen and passwords are
off.

- **OIDC, not the Caddy gate:** behind the gate, users logged in twice
  (Authelia, then Mealie's own form). `OIDC_AUTO_REDIRECT=true` and
  `ALLOW_PASSWORD_LOGIN=false` give one.
- **Its own shared folder** (`/volume1/mealie`), with `dumps/` next to the live
  data: Hyper Backup takes the dumps only.

## Before you start

| What                    | Example                 | How                                                                                        |
| ----------------------- | ----------------------- | ------------------------------------------------------------------------------------------ |
| Caddy and Authelia      | `auth.home.example.com` | [03 HTTPS](03-https-letsencrypt.md), [05 Authelia](05-sso-authelia.md)                     |
| A shared folder         | `/volume1/mealie`       | DSM → Shared Folder, not under `docker`                                                    |
| An Authelia OIDC client | client `mealie`         | [05 Authelia](05-sso-authelia.md#add-an-app), "Add an app" (OIDC); redirect URIs in step 2 |
| A postgres password     | —                       | `openssl rand -hex 32`                                                                     |

## Steps

### 1. Folders

```bash
sudo mkdir -p /volume1/mealie/{pgdata,data,dumps}
sudo chmod 700 /volume1/mealie/dumps     # root-only, like the other dumps
```

### 2. The OIDC client

Generate the secret as in [05 Authelia](05-sso-authelia.md): `Random Password` →
`MEALIE_OIDC_CLIENT_SECRET`, `Digest` → Authelia's `mealie_client_secret_hash`.
The client's redirect URIs, exactly (no wildcards):

```
https://mealie.home.example.com/login
https://mealie.home.example.com/login?direct=1
```

### 3. Deploy

Variables, from [`stacks/mealie/.env.example`](../../stacks/mealie/.env.example):

```bash
INTERNAL_DOMAIN=home.example.com
MEALIE_PGDATA_PATH=/volume1/mealie/pgdata
MEALIE_DATA_PATH=/volume1/mealie/data
POSTGRES_PASSWORD=<secret>
MEALIE_OIDC_CLIENT_SECRET=<Random Password from step 2>
```

**With Portainer:** Stacks → Add stack → Repository, compose path
`stacks/mealie/compose.yaml` → Deploy ([02 Portainer](02-portainer.md)).
**With docker compose:** `.env` from `.env.example` (`chmod 600`), `sudo docker compose up -d`.

**Check:** `postgres` turns healthy, then `mealie` starts.

### 4. Route it through Caddy

```
mealie.{$INTERNAL_DOMAIN} {
	import tls_wildcard
	reverse_proxy mealie:9000
}
```

No `import authelia` ([03 HTTPS, "Add an app"](03-https-letsencrypt.md#add-an-app)).

**Check:** `https://mealie.home.example.com` goes to Authelia and back, logged in.

### 5. Backups

As an **admin** user in Mealie: profile → API Tokens → create. Put it in the
scheduler's `jobs.env` as `MEALIE_API_TOKEN`; the `mealie-backup` job then
writes Mealie's own backup into `dumps/` daily
([10 Backups and jobs](10-backups-and-jobs.md)).

**Check:** after the job's first run, `sudo ls -l /volume1/mealie/dumps` shows a zip.

## Troubleshooting

| Symptom                                                    | Cause → fix                                                                                                                       |
| ---------------------------------------------------------- | --------------------------------------------------------------------------------------------------------------------------------- |
| `postgres` never healthy: `chmod: Operation not permitted` | Its `cap_add` (`CHOWN, FOWNER, SETUID, SETGID, DAC_OVERRIDE`) removed: the entrypoint chmods its data dir as root at every start. |
| Authelia: redirect URI mismatch                            | Both `/login` and `/login?direct=1` must be in the client, exactly.                                                               |
| `mealie-backup` job fails with `401`/`403`                 | The token is not from an admin user, or was revoked.                                                                              |

## Next

- [10 Backups and jobs](10-backups-and-jobs.md): Hyper Backup on `dumps/`, restore.
- [09 Dashboard](09-dashboard.md): Mealie's dump age as a backup light.
