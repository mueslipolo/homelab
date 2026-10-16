# Paperless-ngx: the document archive

> **Needs:** [03 HTTPS](03-https-letsencrypt.md) · **Optional:** [05 Authelia](05-sso-authelia.md) (single sign-on), [02 Portainer](02-portainer.md) (or plain `docker compose`), [10 Backups and jobs](10-backups-and-jobs.md) (the nightly export) · **Used by:** paperless-tarmed (below)
> **Reference:** [`stacks/paperless/README.md`](../../stacks/paperless/README.md)

## What you get

Paperless-ngx at `https://paperless.home.example.com`: scanned or dropped
documents are OCR'd (French + English), classified and searchable. Office files
and emails are converted by Tika and Gotenberg.

```
consume folder (SMB share) ──▶ webserver ──▶ media/ (originals + archive PDFs)
                                 │  paperless_net: db (postgres), broker (redis), tika, gotenberg
                                 └─ proxy ──▶ Caddy ──▶ browser
```

- **Two roots on purpose:** `PAPERLESS_DOCKER_ROOT` holds the disposable
  state (postgres, redis), `PAPERLESS_DATA_ROOT` the documents. Only the
  second needs backing up.
- **Only `webserver` joins `proxy`**: the database, redis, Tika and Gotenberg
  stay on the private `paperless_net`.
- **Login through Authelia (OIDC)**, local login kept as a fallback.

## Before you start

| What                               | Example                                                                       | How                                                                                   |
| ---------------------------------- | ----------------------------------------------------------------------------- | ------------------------------------------------------------------------------------- |
| Caddy and the domain               | `paperless.home.example.com`                                                  | [03 HTTPS](03-https-letsencrypt.md), plus the block in step 4                         |
| Folders                            | `/volume1/docker/paperless`, `/volume1/paperless`, `/volume1/paperless-inbox` | Shared folders in DSM; `paperless` and the inbox as their own shares (backup, SMB)    |
| The user Paperless runs as         | `1026`:`100` (UID:GID)                                                        | `id <user>` on the NAS: a user that can write the inbox share                         |
| Two secrets                        | —                                                                             | `openssl rand -hex 32`, once for `POSTGRES_PASSWORD`, once for `PAPERLESS_SECRET_KEY` |
| An Authelia OIDC client (optional) | client `paperless`                                                            | [05 Authelia](05-sso-authelia.md), "Add an OIDC client"                               |

## Steps

### 1. Folders

```bash
sudo mkdir -p /volume1/docker/paperless/{db,redis} /volume1/paperless/{data,media,export,scripts} /volume1/paperless-inbox
sudo chown 1026:100 /volume1/docker/paperless/redis   # broker runs as PAPERLESS_UID:GID
```

**Check:** `ls -ln /volume1/docker/paperless` shows `redis` owned by the UID/GID above.

### 2. The OIDC secret (skip without Authelia)

```bash
AUTHELIA_VERSION=$(grep -oE 'authelia/authelia:[^ ]+' stacks/authelia/compose.yaml | cut -d: -f2)
sudo docker run --rm "authelia/authelia:$AUTHELIA_VERSION" \
  authelia crypto hash generate pbkdf2 --random --random.length 64 --random.charset alphanumeric
```

`Random Password` → `PAPERLESS_OIDC_CLIENT_SECRET` here. `Digest` → Authelia's
`paperless_client_secret_hash` file. Swapped, the login fails with an auth
error, not a config error.

Without Authelia, remove `PAPERLESS_APPS` and `PAPERLESS_SOCIALACCOUNT_PROVIDERS`
from `stacks/paperless/compose.yaml` and log in locally.

### 3. Deploy

Variables, from [`stacks/paperless/.env.example`](../../stacks/paperless/.env.example):

```bash
INTERNAL_DOMAIN=home.example.com
PAPERLESS_DOCKER_ROOT=/volume1/docker/paperless
PAPERLESS_DATA_ROOT=/volume1/paperless
PAPERLESS_INBOX_PATH=/volume1/paperless-inbox
PAPERLESS_UID=<uid>                 # e.g. 1026
PAPERLESS_GID=<gid>                 # e.g. 100
POSTGRES_PASSWORD=<secret>
PAPERLESS_SECRET_KEY=<secret>
PAPERLESS_OIDC_CLIENT_SECRET=<Random Password from step 2>
```

**With Portainer:** Stacks → Add stack → Repository, compose path
`stacks/paperless/compose.yaml`, the variables above → Deploy
([02 Portainer](02-portainer.md)).

**With docker compose:** in a copy of `stacks/paperless/`,
`cp .env.example .env && chmod 600 .env`, fill it in, `sudo docker compose up -d`.

Time zone (`Europe/Zurich`), OCR languages (`fra+eng`) and the file name
format are fixed in `compose.yaml`: change them there.

**Check:** `sudo docker compose ps` (or Portainer) shows all five containers
healthy or running; `webserver` starts only after `db` and `broker` are healthy.

### 4. Route it through Caddy

In your Caddyfile ([03 HTTPS, "Add an app"](03-https-letsencrypt.md#add-an-app)):

```
paperless.{$INTERNAL_DOMAIN} {
	import tls_wildcard
	reverse_proxy webserver:8000
}
```

No `import authelia`: Paperless does its own OIDC login.

**Check:** `https://paperless.home.example.com` shows the login page with an
**Authelia** button (or only the local form without OIDC).

### 5. First users

1. Create the admin: `sudo docker exec -it <webserver-container> python3 manage.py createsuperuser`.
2. With OIDC, an existing local account is linked once: log in locally →
   profile → My Profile → **Connect new social account** → Authelia. Logging
   in through Authelia first gives "A user with that username already exists":
   django-allauth does not link accounts by itself.

**Check:** logging out and in with the Authelia button lands in that account.

### 6. Feed it

- **Consume folder:** files dropped into the inbox share (subfolders too,
  `PAPERLESS_CONSUMER_RECURSIVE`) are imported and removed. Map it on the
  scanner or the laptops over SMB.
- **Separator sheets:** a page with the barcode `paperless:separator` splits a
  batch scan into documents.

**Check:** drop a PDF into the inbox; within a minute it is in the document
list, with OCR'd text.

## Backups

The `paperless-export` job runs `document_exporter` nightly into
`/volume1/paperless/export` (incremental). Back up the whole `paperless` share:
the originals are irreplaceable, and the export restores into any Paperless
version. Setup and restore: [10 Backups and jobs](10-backups-and-jobs.md).

## Add-on: paperless-tarmed

Reads Swiss medical reimbursement forms (TARMED / TARDOC "Justificatif de
remboursement") and fills custom fields (patient, date, total, provider).
It calls Paperless as `http://webserver:8000` over `proxy` and needs the
scheduler's `build-images` job ([10 Backups and jobs](10-backups-and-jobs.md)).

Install: [`stacks/paperless-tarmed/INSTALL.md`](../../stacks/paperless-tarmed/INSTALL.md).
Reference: [`stacks/paperless-tarmed/README.md`](../../stacks/paperless-tarmed/README.md).

## Troubleshooting

| Symptom                                                            | Cause → fix                                                                                                                                              |
| ------------------------------------------------------------------ | -------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `broker` restarts, `Permission denied` on `/data`                  | `redis/` not owned by `PAPERLESS_UID:PAPERLESS_GID` (step 1).                                                                                            |
| `Bad Request (400)` in the browser                                 | `PAPERLESS_ALLOWED_HOSTS` takes bare hostnames, `PAPERLESS_URL` keeps `https://`.                                                                        |
| paperless-tarmed gets a bodyless `400`, nothing in Paperless's log | `webserver` removed from `PAPERLESS_ALLOWED_HOSTS`: Django rejects the `Host: webserver` header (DisallowedHost).                                        |
| Authelia button: `invalid_client` or redirect error                | Secret swapped with the Digest, or the callback differs: it must be exactly `https://paperless.home.example.com/accounts/oidc/authelia/login/callback/`. |
| "A user with that username already exists"                         | Expected: link the local account once (step 5).                                                                                                          |
| Files stay in the inbox                                            | Not readable by `PAPERLESS_UID`, or not a supported type: `sudo docker logs <webserver-container>`.                                                      |

## Next

- [10 Backups and jobs](10-backups-and-jobs.md): the nightly export and Hyper Backup.
- [09 Dashboard](09-dashboard.md): Paperless's card and its backup light.
