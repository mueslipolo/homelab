# Notifications with ntfy

> **Needs:** [01 Synology prep](01-synology-prep.md), [03 HTTPS](03-https-letsencrypt.md) · **Optional:** [02 Portainer](02-portainer.md) (or plain `docker compose`), a VPN to your LAN (WireGuard) for phones away from home · **Used by:** [04 Forgejo](04-forgejo-gitops.md) (updates waiting for approval), [06 mediarr](06-mediarr.md), [10 Backups and jobs](10-backups-and-jobs.md)
> **Reference:** [`stacks/ntfy/README.md`](../../stacks/ntfy/README.md)

## What you get

Push notifications on your phone from everything that needs your attention, on
a server of your own: [ntfy](https://ntfy.sh), at `https://ntfy.home.example.com`.

A message is one HTTP request to a _topic_; the ntfy app shows it as a normal
phone notification. This setup uses three topics, so you can mute one:

| Topic     | From                            | Example                              |
| --------- | ------------------------------- | ------------------------------------ |
| `homelab` | the `renovate-notify` job       | "1 update waiting for your approval" |
| `media`   | Sonarr, Radarr, Prowlarr, Seerr | "Dune: Part Two is now available"    |
| `nas`     | DSM                             | "Hyper Backup task Off-site failed"  |

- **LAN-only, like everything else:** the phone reaches ntfy on the home Wi-Fi,
  and away from home through a VPN that connects on demand (step 5). Without
  it, messages wait on the server (3 days) and arrive once the phone is back.
- **iPhone:** Apple only allows push through its own service, so the server
  sends a content-free "poll now" ping via ntfy.sh, and the app then fetches
  the message from your server. The text never leaves your network.
- **Nothing anonymous:** a publish-only token for the senders, your own
  account for reading.
- **Senders reach ntfy without Caddy:** containers by name over `proxy`
  (`http://ntfy:8080`), the NAS itself (DSM, the scheduler) on a loopback-only
  port (`http://127.0.0.1:2586`). Neither can reach Caddy's macvlan IP.

## Before you start

| What                          | Example                 | How                                                                           |
| ----------------------------- | ----------------------- | ----------------------------------------------------------------------------- |
| HTTPS and the `proxy` network | `ntfy.home.example.com` | [03 HTTPS](03-https-letsencrypt.md); the `ntfy` block is in `caddy/Caddyfile` |
| A data folder                 | `/volume1/docker/ntfy`  | owned by the user the container runs as (`PUID`/`PGID`, `id <user>`)          |
| The ntfy app                  | Android, iOS            | from the app store; on Android also F-Droid                                   |
| A VPN to the LAN              | WireGuard on the router | for phones away from home; any VPN that can connect by itself works           |

## Steps

### 1. Create the folder

```bash
sudo mkdir -p /volume1/docker/ntfy
sudo chown <uid>:<gid> /volume1/docker/ntfy
```

### 2. Deploy

Variables from [`stacks/ntfy/.env.example`](../../stacks/ntfy/.env.example):
`INTERNAL_DOMAIN`, `PUID`, `PGID`, `NTFY_DATA_ROOT`.

- **Portainer:** Stacks → Add stack → Repository, compose path
  `stacks/ntfy/compose.yaml` → Deploy.
- **docker compose:** `cd stacks/ntfy && sudo docker compose up -d`.

**Check:** `curl -s http://127.0.0.1:2586/v1/health` on the NAS →
`{"healthy":true,…}`.

### 3. Route it through Caddy

The repo's `caddy/Caddyfile` has the block; in your own copy:

```
ntfy.{$INTERNAL_DOMAIN} {
	import tls_wildcard
	reverse_proxy ntfy:8080
}
```

No `import authelia`: the apps and the senders call ntfy's API directly, and
ntfy has its own login.

**Check:** `https://ntfy.home.example.com` shows ntfy's web app.

### 4. Users and a token

```bash
C=$(sudo docker ps -q --filter label=com.docker.compose.service=ntfy)
sudo docker exec -it $C ntfy user add --role=admin <you>     # you: phone and web; asks for a password
sudo docker exec -it $C ntfy user add homelab                # the senders; any password, never used
for t in homelab media nas; do sudo docker exec $C ntfy access homelab $t write-only; done
sudo docker exec $C ntfy token add homelab                   # prints tk_…: keep it, every sender uses it
```

**Check:**

```bash
curl -s -o /dev/null -w '%{http_code}\n' -d test http://127.0.0.1:2586/homelab                           # 403
curl -s -o /dev/null -w '%{http_code}\n' -H 'Authorization: Bearer tk_…' -d test http://127.0.0.1:2586/homelab   # 200
```

### 5. The phone

1. ntfy app → Settings → **Default server** `https://ntfy.home.example.com`,
   and **Manage users** → add your account for that server.
2. Subscribe to `homelab`, `media` and `nas`. Android: keep **instant
   delivery** on (the app keeps a connection open; that is how it receives).
3. The VPN, so it works away from home:
   - **iPhone, WireGuard app:** the tunnel → **On-Demand activation** → on
     cellular, and on Wi-Fi except your home network's name.
   - **Android:** the official WireGuard app has no on-demand rule; use
     Settings → Network → VPN → the tunnel → **Always-on VPN**, or a client
     with automatic tunnelling on untrusted networks (e.g. WG Tunnel).

**Check:** the test from step 4 arrives on the phone; then once more on mobile
data, Wi-Fi off.

### 6. Updates waiting for approval

Renovate merges minor updates itself; majors, and Forgejo and Portainer
updates, wait on its Dependency Dashboard issue
([04](04-forgejo-gitops.md#5-ci-runner-checks-renovate-optional)). The
`renovate-notify` job (07:00, after Renovate's 06:00 run) pushes each new one
to `homelab`, once; tapping the notification opens the dashboard issue.

1. Forgejo → Settings → Applications → a token with **read** access to
   repositories and issues.
2. In `jobs.env` ([10](10-backups-and-jobs.md)):
   ```bash
   FORGEJO_TOKEN=<that token>
   FORGEJO_REPO=<owner>/homelab
   NTFY_TOKEN=tk_…
   ```

**Check:** run it once by hand; it prints what it did:

```bash
sudo bash -c 'set -a; . <path>/jobs.env; bash <clone>/scripts/renovate-notify.sh'
```

### 7. Sonarr, Radarr, Prowlarr

Each app: Settings → **Connect** → + → **ntfy**:

| Field        | Value                                                                                         |
| ------------ | --------------------------------------------------------------------------------------------- |
| Server URL   | `http://ntfy:8080`                                                                            |
| Access token | `tk_…` (or username/password fields: leave them empty)                                        |
| Topics       | `media`                                                                                       |
| Triggers     | Sonarr/Radarr: _On Import_ (or _On Download_), _On Health Issue_; Prowlarr: _On Health Issue_ |

**Check:** the **Test** button; a test message arrives.

### 8. Seerr

Settings → Notifications → **Ntfy**: enable, Server URL `http://ntfy:8080`,
topic `media`, authentication **token** `tk_…`. Choose the types, e.g.
_Request Pending Approval_, _Request Available_, _Request Failed_. Set
Settings → General → **Application URL** to `https://seerr.home.example.com`,
so tapping a notification opens the title.

**Check:** **Test**.

### 9. DSM

DSM runs on the NAS itself, so it uses the loopback port, and a webhook can't
set an `Authorization` header: the token goes in an `auth` URL parameter.

1. The parameter, from the token:
   ```bash
   printf 'Bearer %s' tk_… | base64 -w0 | tr -d '='
   ```
2. Control Panel → Notification → **Webhook** (DSM 7.2: Push Service →
   Application webhooks) → Add → provider **Custom**:
   - URL `http://127.0.0.1:2586/?auth=<parameter>`, method **POST**, content
     type `application/json`
   - body fields: `topic` = `nas`, `title` = `DSM`, `message` = the
     message-content variable DSM offers
3. Notification → **Rules** (or Advanced): pick the events for the webhook:
   at least storage, Hyper Backup and Snapshot Replication failures.

**Check:** DSM's **Send test message**.

## Troubleshooting

| Symptom                                               | Cause → fix                                                                                                                                     |
| ----------------------------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------- |
| A sender gets `403`                                   | Token missing or wrong, or no `write` access to that topic: `ntfy access` in the container lists the rules.                                     |
| An \*arr or Seerr test: connection refused or timeout | Use `http://ntfy:8080`, not `https://ntfy.<domain>`: containers can't reach Caddy's macvlan IP. The app must be on `proxy`.                     |
| DSM's test fails                                      | URL `http://127.0.0.1:2586/…` (not the hostname, for the same reason); `auth` parameter built from `Bearer <token>`, without trailing `=`.      |
| Nothing on the phone away from home                   | The VPN didn't connect: check the on-demand rule. Messages are kept 3 days and arrive once the phone reaches the server.                        |
| iPhone: nothing, or "new message" without text        | The ping came, but the app couldn't fetch the message: same VPN cause.                                                                          |
| `renovate-notify`: "No open Dependency Dashboard"     | Renovate hasn't run yet, or its dashboard issue is closed.                                                                                      |
| An update was not notified                            | It was already in the previous run's list (`$OPS_HOME/state/renovate-notified.txt`); delete the file to be told about everything waiting again. |

## Next

- [10 Backups and jobs](10-backups-and-jobs.md): every job also pings
  healthchecks.io, which keeps alerting by email when the NAS itself is down,
  something ntfy on that NAS cannot do.
- [06 mediarr](06-mediarr.md), [05 Authelia](05-sso-authelia.md).
