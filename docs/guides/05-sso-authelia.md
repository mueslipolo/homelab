# Single sign-on with Authelia

> **Needs:** [01 Synology prep](01-synology-prep.md), [03 HTTPS](03-https-letsencrypt.md) · **Optional:** [02 Dockhand](02-dockhand.md) (or plain `docker compose`) · **Used by:** [06 mediarr](06-mediarr.md), [07 Paperless](07-paperless.md), [08 Mealie](08-mealie.md), [09 dashboard](09-dashboard.md), [12 Immich](12-immich.md), [Dockhand](02-dockhand.md) and [Forgejo](04-forgejo-gitops.md) logins
> **Reference:** [`stacks/authelia/README.md`](../../stacks/authelia/README.md) · **Why it is built this way:** [architecture](../architecture.md#identity)

## What you get

One account per person, kept in Synology's LDAP Server and shared by DSM, SMB,
Drive and every web app. One login at `https://auth.home.example.com` covers
every app until the session ends (Authelia's defaults: 1 hour, 5 minutes
idle); OIDC consent is remembered for a year.

Each app uses one of three modes, chosen by how it logs in:

| Mode       | How                                                                                   | Use for                                                                                 |
| ---------- | ------------------------------------------------------------------------------------- | --------------------------------------------------------------------------------------- |
| **Gate**   | `import authelia` in the Caddyfile + an `access_control` rule; the app's login is off | apps without OIDC: Sonarr, Radarr, Prowlarr, qBittorrent, Seerr, Maintainerr, dashboard |
| **OIDC**   | the app is an OIDC client of Authelia; no `import authelia`                           | apps with OIDC: Paperless, Mealie, Immich, Dockhand, Forgejo                            |
| **Bypass** | neither; the app keeps its own login                                                  | clients that cannot do a browser login (Jellyfin on a Chromecast or TV)                 |

Single factor only: the setup is LAN-only. Add a second factor before
exposing anything to the internet.

**Without an LDAP server:** set `AUTH_BACKEND=file` and keep the users in a
file instead ([Without LDAP](../../stacks/authelia/README.md#without-ldap)).
Skip the three LDAP rows below and the `ldap_bind_password` secret. The
web apps still share one login; DSM, SMB and Drive keep their own passwords.

## Before you start

| What                       | Example                                   | How                                                                                                                                                               |
| -------------------------- | ----------------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| HTTPS working              | `https://whoami.home.example.com`         | [03 HTTPS](03-https-letsencrypt.md). Authelia's cookie domain and OIDC issuer are `INTERNAL_DOMAIN`.                                                              |
| `PROXY_GATEWAY_IP`         | `192.0.2.1`                               | `sudo docker network inspect proxy --format '{{json .IPAM.Config}}'`: how containers reach host services such as LDAP.                                            |
| Synology LDAP Server       | base DN `dc=home,dc=example,dc=com`       | Package Center → LDAP Server → Settings → enable, FQDN `home.example.com` (it gives the base DN), set the root password.                                          |
| A group for admins         | `infra-admins`                            | LDAP Server → Manage groups (or `ADMIN_GROUP` names another). Members may log in to Dockhand and Forgejo. Not `admins` or `administrators` (see Troubleshooting). |
| DSM joined to its own LDAP | —                                         | Control Panel → Domain/LDAP → LDAP, the NAS itself. Same accounts for DSM, SMB and Drive.                                                                         |
| Folders                    | `/volume1/docker/authelia/{secrets,data}` | Below.                                                                                                                                                            |

Keep the DSM/SSH admin a **local** account: Synology's SSH only accepts the
local `administrators` group. To move an existing person to LDAP, rename their
local account first; never delete and recreate it (home folder, ACLs).

## Steps

### 1. Create the secrets

Every secret file your settings use must exist, or Authelia does not start
(the list, and which setting needs which: `stacks/authelia/.env.example`).

```bash
sudo mkdir -p /volume1/docker/authelia/{secrets,data}
cd /volume1/docker/authelia/secrets && sudo chmod 700 .
for s in session_secret storage_encryption_key jwt_secret oidc_hmac_secret; do openssl rand -hex 32 | sudo tee $s >/dev/null; done
echo -n '<LDAP root password>' | sudo tee ldap_bind_password >/dev/null   # LDAP only
AUTHELIA_VERSION=4.39.28   # the tag in stacks/authelia/compose.yaml
sudo docker run --name authelia-keygen "authelia/authelia:$AUTHELIA_VERSION" authelia crypto pair rsa generate --directory /tmp
sudo docker cp authelia-keygen:/tmp/private.pem oidc_jwks_private_key.pem && sudo docker rm authelia-keygen
```

One client secret per OIDC app (`paperless`, `mealie`, `forgejo`,
`dockhand`, `immich`). Skip the ones you don't run, and leave them out of
`OIDC_CLIENTS` (step 3):

```bash
sudo docker run --rm "authelia/authelia:$AUTHELIA_VERSION" authelia crypto hash generate pbkdf2 --random --random.length 64 --random.charset alphanumeric
```

- `Digest:` → file `<client>_client_secret_hash`, for Authelia.
- `Random Password:` → the app, in step 5. Keep it until then.

```bash
sudo chmod 600 *
```

**Check:** `ls` lists the ten files (fewer if you skipped clients), none empty.

### 2. Adjust the access rules

`config/configuration.yml` → `access_control.rules`: one `one_factor` rule per
**gated** hostname. `default_policy: deny` blocks a gated host without a rule.
The dashboard has two: all of it for `infra-admins`, only its family page for
everyone else ([dashboard: two pages](../../stacks/dashboard/README.md#two-pages)).
OIDC apps need no rule here; their client block's `authorization_policy`
applies (`admins_only` = group `infra-admins`).

### 3. Deploy

Variables, from [`stacks/authelia/.env.example`](../../stacks/authelia/.env.example):

```bash
INTERNAL_DOMAIN=home.example.com
TZ=Europe/Berlin                    # your time zone (tz database name)
AUTH_BACKEND=ldap                   # or file: users in users_database.yml
PROXY_GATEWAY_IP=192.0.2.1          # ldap only
LDAP_BASE_DN=dc=home,dc=example,dc=com
OIDC_CLIENTS=paperless,mealie,forgejo,dockhand    # the OIDC apps you run
AUTHELIA_SECRETS_ROOT=/volume1/docker/authelia/secrets
AUTHELIA_DATA_ROOT=/volume1/docker/authelia/data
```

**With Dockhand:** Stacks → from Git, name `authelia`, compose path
`stacks/authelia/compose.yaml`, the variables → Deploy
([02 Dockhand](02-dockhand.md#4-deploy-a-git-stack)). Set `OIDC_CLIENTS`
explicitly, even to the default: a later client (`immich`) is then one name
added to a list you can see, not a default you have to remember.

**With docker compose:** from a copy of `stacks/authelia/` (with its
`config/` folder): `cp .env.example .env`, fill it in, `sudo docker compose up -d`.

The Caddyfile already has `auth.{$INTERNAL_DOMAIN}` → `authelia:9091`, and
the `edge` stack gives Caddy the `proxy` alias `auth.<domain>` so containers
can reach the portal ([03](03-https-letsencrypt.md), reference "Reaching
Caddy itself").

**Check:** `curl -s https://auth.home.example.com/api/health` →
`{"status":"OK"}`, and an LDAP user can log in at `https://auth.home.example.com`.

### 4. Gate an app

In your Caddyfile, `import authelia` in the app's block:

```
sonarr.{$INTERNAL_DOMAIN} {
	import tls_wildcard
	import authelia
	reverse_proxy sonarr:8989 {
		import arr_no_client_ip
	}
}
```

plus its `access_control` rule (step 2), then restart Authelia: it reads its
configuration only at start, and a Dockhand deploy does not restart it for
a config change. Caddy reloads its Caddyfile by itself.
Turn the app's own login off for requests from Caddy, or users log in twice:
[06 mediarr](06-mediarr.md#4-turn-off-the-apps-own-logins) has the commands
for the \*arr apps and qBittorrent.

**Check:** `https://sonarr.home.example.com` → Authelia's login, then Sonarr
with no second login.

### 5. Connect the OIDC apps

Issuer for all: `https://auth.home.example.com`, discovery
`https://auth.home.example.com/.well-known/openid-configuration`. The client
secret is the `Random Password` from step 1.

| App       | Where                                                                                                                | Settings                                                                                                                                                       |
| --------- | -------------------------------------------------------------------------------------------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Paperless | `PAPERLESS_OIDC_CLIENT_SECRET` in its stack ([07](07-paperless.md))                                                  | `stacks/paperless/compose.yaml` builds the rest (`PAPERLESS_SOCIALACCOUNT_PROVIDERS`, provider id `authelia`).                                                 |
| Mealie    | `MEALIE_OIDC_CLIENT_SECRET` in its stack ([08](08-mealie.md))                                                        | `stacks/mealie/compose.yaml` sets `OIDC_*`, with auto-redirect to Authelia.                                                                                    |
| Immich    | Administration → Settings → Authentication Settings → OAuth ([12](12-immich.md#5-the-admin-then-the-authelia-login)) | Issuer URL, client ID `immich`, the secret; everything else Immich's defaults (`client_secret_post`). Button text, auto register: [12](12-immich.md).          |
| Forgejo   | Site Administration → Authentication Sources → Add, type OAuth2                                                      | Name **`authelia`** (it is in the callback URL `/user/oauth2/authelia/callback`), provider OpenID Connect, client ID `forgejo`, the secret, the discovery URL. |
| Dockhand  | Settings → Authentication → OIDC ([02, step 8](02-dockhand.md#8-log-in-through-authelia-optional))                   | Issuer URL, client ID `dockhand`, the secret, redirect URI `https://dockhand.home.example.com/api/auth/oidc/callback`, scopes `openid profile email groups`.   |

An app reaches `auth.home.example.com` through Caddy's alias on `proxy`, so
its container must be on `proxy` (every compose file here puts it there).

**Check:** each app's login button sends you to Authelia and back logged in. A
user outside `infra-admins` is refused by Dockhand and Forgejo.

## Add an app

| Mode   | Do                                                                                                                                                                                                                     |
| ------ | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Gate   | `import authelia` in its Caddy block + an `access_control` rule; restart Authelia (Caddy reloads by itself); turn the app's login off for local addresses.                                                             |
| OIDC   | Generate a secret (step 1), copy a client block in `config/configuration.yml` (new `client_id`, `redirect_uris`, the hash file, `authorization_policy`), list the file in `.env.example`, redeploy; configure the app. |
| Bypass | Nothing in Authelia; no `import authelia`. The app's own login is the only control.                                                                                                                                    |

Users and groups: in the LDAP Server UI only. Authelia sees changes at the next
login, without a restart.

## Troubleshooting

| Symptom                                                  | Cause → fix                                                                                                                                                                                              |
| -------------------------------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Authelia restarts, log names a `/secrets/...` file       | The file is missing or unreadable: step 1.                                                                                                                                                               |
| Log: URL parsing error with a literal `{{ mustEnv` in it | `X_AUTHELIA_CONFIG_FILTERS: template` was removed from `compose.yaml`; the templates stay literal.                                                                                                       |
| Login fails for every user, log shows an LDAP bind error | `ldap_bind_password` wrong, `LDAP_BASE_DN` wrong, or `PROXY_GATEWAY_IP` not the `proxy` gateway. Test: `ldapsearch -x -H ldap://<nas-ip> -D 'uid=root,cn=users,<base DN>' -W -b '<base DN>' uid=<user>`. |
| A newly gated host gives `403`                           | No `access_control` rule for it, or Authelia still runs the old rules: restart it. Check: `sudo docker exec <authelia> grep -c <host> /config/configuration.yml`.                                        |
| OIDC login fails with an authentication error            | Digest and password swapped: the **digest** goes to Authelia, the **password** to the app.                                                                                                               |
| A user is refused and sees no Authelia page              | `authorization_policy: admins_only` and not in `infra-admins`. The app's URL carries `error=access_denied`.                                                                                              |
| OIDC app: `No route to host` to `auth.home.example.com`  | The app container is not on `proxy`, so it cannot use Caddy's alias; the macvlan IP is unreachable from the NAS.                                                                                         |
| Every OIDC client broken after a restore                 | `storage_encryption_key` or `oidc_jwks_private_key.pem` changed. Back up the secrets folder ([10 backups](10-backups-and-jobs.md)).                                                                      |
| Locked out for an hour                                   | Regulation: 5 failures in 10 minutes → 1-hour ban.                                                                                                                                                       |

Don't name the admin group `admins` (it collides with the policy name) or
`administrators` (Synology's local group, which carries DSM and SSH rights).

## Next

- [06 mediarr](06-mediarr.md), [09 dashboard](09-dashboard.md): gated apps.
- [07 Paperless](07-paperless.md), [08 Mealie](08-mealie.md), [12 Immich](12-immich.md): OIDC apps.
- [10 backups](10-backups-and-jobs.md): back up the secrets folder; the
  database holds only disposable state (OIDC consent and tokens, login attempts);
  login sessions live in memory.
