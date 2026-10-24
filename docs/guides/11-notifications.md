# Notifications with ntfy

> **Needs:** [01 Synology prep](01-synology-prep.md), [03 HTTPS](03-https-letsencrypt.md) · **Optional:** [02 Portainer](02-portainer.md) (or plain `docker compose`), a VPN to your LAN (WireGuard) for phones away from home · **Used by:** [04 Forgejo](04-forgejo-gitops.md) (updates waiting for approval), [06 mediarr](06-mediarr.md), [10 Backups and jobs](10-backups-and-jobs.md)
> **Reference:** [`stacks/ntfy/README.md`](../../stacks/ntfy/README.md)

## What you get

Push notifications on your phone from everything that needs your attention, on
a server of your own: [ntfy](https://ntfy.sh), at `https://ntfy.home.example.com`.

A message is one HTTP request to a _topic_; the ntfy app shows it as a normal
phone notification. This setup uses four topics, so you can mute one:

| Topic       | From                                                                      | Example                                                                             |
| ----------- | ------------------------------------------------------------------------- | ----------------------------------------------------------------------------------- |
| `homelab`   | the `renovate-notify` job, the NAS alerts (step 10), the router (step 12) | "1 update waiting for your approval", "Anna · Pixel 8: nothing uploaded for 4 days" |
| `paperless` | paperless-tarmed (step 11)                                                | "Bill processed": Dr Exemple · CHF 182.40 · 14.10.2026                              |
| `media`     | Sonarr, Radarr, Prowlarr, Seerr                                           | "Dune: Part Two is now available"                                                   |
| `nas`       | DSM                                                                       | "Hyper Backup task Off-site failed"                                                 |

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
for t in homelab media nas paperless; do sudo docker exec $C ntfy access homelab $t write-only; done
sudo docker exec $C ntfy token add homelab                   # prints tk_…: keep it, every sender uses it
```

**Check:**

```bash
curl -s -o /dev/null -w '%{http_code}\n' -d test http://127.0.0.1:2586/homelab                           # 403
curl -s -o /dev/null -w '%{http_code}\n' -H 'Authorization: Bearer tk_…' -d test http://127.0.0.1:2586/homelab   # 200
```

### 5. The phone

1. ntfy app → Settings → **Default server** `https://ntfy.home.example.com`,
   and **Manage users** → add your account for that server. Do this first:
   every topic you add then uses your server, with no per-topic server field.
2. Subscribe to `homelab`, `media`, `nas` and `paperless` (+ → topic name). Subscriptions
   live in each app, not on the server: the web app and every phone need their
   own, and a fresh login shows none.
3. Android: keep **instant delivery** on (the app keeps a connection open; that
   is how it receives), and Settings → **Connection protocol** →
   **WebSockets** works through Caddy as is.
4. The VPN, so it works away from home:
   - **iPhone, WireGuard app:** the tunnel → **On-Demand activation** → on
     cellular, and on Wi-Fi except your home network's name.
   - **Android:** the official WireGuard app has no on-demand rule; use
     Settings → Network → VPN → the tunnel → **Always-on VPN**, or a client
     with automatic tunnelling on untrusted networks (e.g. WG Tunnel).

**Check:** send a test, from the web app (a topic → the message field at the
bottom), the phone app (a topic → ⋮ → **Send test notification**) or the NAS:

```bash
curl -H 'Authorization: Bearer tk_…' -H 'Title: Test' -d 'ntfy works' http://127.0.0.1:2586/homelab
```

It arrives on the phone; then once more on mobile data, Wi-Fi off.

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

Each app: Settings → **Connect** (Prowlarr: **Notifications**) → + → **ntfy**:

| Field        | Value                                                                           |
| ------------ | ------------------------------------------------------------------------------- |
| Server URL   | `http://ntfy:8080`                                                              |
| Access token | `tk_…` (or username/password fields: leave them empty)                          |
| Topics       | `media`                                                                         |
| Triggers     | Sonarr/Radarr: _On File Import_, _On Health Issue_; Prowlarr: _On Health Issue_ |

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
2. Control Panel → Notification → **Webhook** → Add → provider **Custom**:
   - URL `http://127.0.0.1:2586/?auth=<parameter>`, method **POST**, content
     type `application/json`
   - body, with DSM's `@@TEXT@@` placeholder for the message. Quote every
     value, and the key is `message` (ntfy ignores `text`):
     ```json
     { "topic": "nas", "title": "DSM", "message": "@@TEXT@@" }
     ```
   - If test messages arrive but some real alerts don't, use plain text
     instead: URL `http://127.0.0.1:2586/nas?auth=<parameter>&title=DSM`,
     content type `text/plain`, body `@@TEXT@@`.
3. Notification → **Rules**: assign the webhook a rule that includes storage,
   Hyper Backup and Snapshot Replication failures.

**Check:** DSM's **Send test message**.

### 10. NAS alerts

Every 15 minutes, right after refreshing the dashboard's `status.json`, the
scheduler runs `scripts/alerts.sh`, which pushes to `homelab` these items
when the dashboard would show them amber or red, using the same thresholds.
Stacks or images behind git and a stale storage scan are not pushed:

| Item                                                                | Amber                                                              | Red                                                                                                |
| ------------------------------------------------------------------- | ------------------------------------------------------------------ | -------------------------------------------------------------------------------------------------- |
| a scheduled job                                                     |                                                                    | its last run failed                                                                                |
| a Hyper Backup task                                                 | partial, or last run over 2 days ago                               | failed                                                                                             |
| a dump (Forgejo, Mealie)                                            |                                                                    | none, or over 2 days old                                                                           |
| a shared folder's snapshots                                         | newest over 2 days old                                             |                                                                                                    |
| a container (anything with a restart policy: the stacks, Portainer) | unhealthy, or restarted by docker after a crash in the last 20 min | restart-looping, or exited with an error or out of memory (a stop, exit 0/137/143, is not a crash) |
| a phone or computer in `uploaders.conf`                             | silent half its max days                                           | silent its max days                                                                                |
| the volume                                                          | 80 % used                                                          | 90 % used                                                                                          |
| the scheduler's git clone                                           | cannot update                                                      |                                                                                                    |
| growth between the last two daily scans (`storage.json`)            | more than `ALERT_GROWTH_GB`                                        |                                                                                                    |

It pushes on changes only: once when an item turns amber, again if it turns
red, and a quiet "ok again" when it recovers. Nothing to set up beyond the
`NTFY_TOKEN` already in `jobs.env` (step 6); optionally:

```bash
DASHBOARD_URL=https://dashboard.home.example.com   # tapping opens the matching tab
ALERT_GROWTH_GB=100                                # default 100
```

**Check:** `$OPS_HOME/logs/alerts.log` after the next tick says how many it
sent; the first run reports whatever is already amber or red.

### 11. Paperless bills

paperless-tarmed pushes "Bill processed" (provider · amount · date), or "Bill
needs review", for each justificatif, to its own topic `paperless` so you can
mute it. The patient's name stays off the lock screen unless you ask for it.

1. Allow the token on the topic:
   `sudo docker exec $C ntfy access homelab paperless write-only`
2. The paperless-tarmed stack's variables in Portainer:
   `PAPERLESS_TARMED_NTFY_URL=http://ntfy:8080`,
   `PAPERLESS_TARMED_NTFY_TOKEN=tk_…`, and
   `PAPERLESS_TARMED_PUBLIC_URL=https://paperless.home.example.com` for the
   link (`PAPERLESS_TARMED_NTFY_PATIENT=1` adds the patient's name).
3. Subscribe to `paperless` on the phone.

**Check:** reprocess a known justificatif (`scripts/fire-hook.sh <id>` in the
stack, [`INSTALL.md`](../../stacks/paperless-tarmed/INSTALL.md)); the push
arrives and opens the document.

### 12. MikroTik

[`tools/mikrotik/ntfy.rsc`](../../tools/mikrotik/ntfy.rsc) (RouterOS 7)
pushes to `homelab`: a device on the LAN without a static lease (once per
boot), internet down and back, a WireGuard peer connecting or going quiet. The
router posts through Caddy, `https://ntfy.home.example.com`, so its DNS must
resolve that name. Tested on a hEX with a wAP ax behind it.

Steps 1, 3, 4 and 5 below are what `tools/mikrotik/deploy.sh` does for you once the
certificates (step 2) are in place and `ssh router` / `ssh ap` work with a key:
put `NTFY_HOST` and `NTFY_TOKEN` in `tools/mikrotik/mikrotik.env` (gitignored),
then `tools/mikrotik/deploy.sh -t` deploys both and sends a test from each.

1. Replace `ntfy.home.example.com` and `tk_REPLACE_ME` in the file, upload it
   (Files, or `scp ntfy.rsc admin@192.0.2.1:`), then `/import ntfy.rsc`.
   Re-importing replaces it.
2. Let the router check ntfy's certificate (Let's Encrypt's roots):
   ```
   /tool fetch url=https://letsencrypt.org/certs/isrgrootx1.pem
   /tool fetch url=https://letsencrypt.org/certs/isrg-root-x2.pem
   /certificate import file-name=isrgrootx1.pem passphrase=""
   /certificate import file-name=isrg-root-x2.pem passphrase=""
   ```
3. Attach the lease script to your LAN DHCP server: the commented
   `/ip dhcp-server set … lease-script=…` line at the end of the file, with
   your server's name (`/ip dhcp-server print`). It replaces any existing
   lease-script.
4. A Wi-Fi access point that is itself a router (a MikroTik wAP's default:
   its own DHCP and NAT, e.g. `192.168.88.x`) hands out the Wi-Fi devices'
   addresses, so the main router never sees them join. Do steps 1–3 on the
   AP too, attaching the lease script to its DHCP server with `where="Wi-Fi"`
   added at its end (alerts then say "New device on the Wi-Fi"), and remove the AP's
   copies of the other two checks, which the main router already runs:
   `/tool netwatch remove [find where comment~"^ntfy-inet"]` and
   `/system scheduler remove [find where name="ntfy-wg"]`. A guest network's DHCP
   server can alert too, as "New device on the guest Wi-Fi" (`where="guest Wi-Fi"`;
   `GUEST_DHCP_SERVER` for `deploy.sh`): every visitor's device, once per boot.
5. The lease script also names each lease after the device's host name (as
   its comment) when it connects, without touching a comment you wrote. To
   name the existing leases at once:
   `/ip dhcp-server lease { :foreach l in=[find where host-name!=""] do={ :if ([:len [get $l comment]] = 0) do={ set $l comment=[get $l host-name] } } }`
6. Known devices don't alert once their lease is static:
   `/ip dhcp-server lease make-static [find where mac-address="AA:BB:CC:DD:EE:FF"]`,
   or every current one with `[find where dynamic]`.

**Check:**
`:local s [:parse [/system script get ntfy-send source]]; $s title="Router test" message="hello"`
arrives on the phone, and `/log print where message~"ntfy"` shows no error.
A made-up device tests the lease script without a new one:
`:local f [:parse [/system script get ntfy-dhcp source]]; $f bound=1 mac="02:00:00:00:00:99" ip="192.0.2.250" host="test-device"`.
A device that was connected during `make-static` is known and stays quiet; to
see a real alert, remove its lease, reconnect it, and make it static again.
Then a new phone on the Wi-Fi gets "New device on the LAN", and a WireGuard
peer that connects gets "VPN: … connected" within a minute. A phone without
WireGuard keepalive that sits idle stops handshaking and shows as "gone
quiet"; while the internet is down, iPhones receive nothing (their wake-up ping
needs ntfy.sh).

## Troubleshooting

| Symptom                                               | Cause → fix                                                                                                                                                                                                                                                                                                                                                                                                                     |
| ----------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Logged in, but no topics                              | Normal: subscriptions are per app. Add `homelab`, `media`, `nas` with **+**.                                                                                                                                                                                                                                                                                                                                                    |
| A sender gets `403`                                   | Token missing or wrong, or no `write` access to that topic: `ntfy access` in the container lists the rules.                                                                                                                                                                                                                                                                                                                     |
| An \*arr or Seerr test: connection refused or timeout | Use `http://ntfy:8080`, not `https://ntfy.<domain>`: containers can't reach Caddy's macvlan IP. The app must be on `proxy`.                                                                                                                                                                                                                                                                                                     |
| DSM's test: nothing arrives                           | Send the same request with `curl` (the URL with the `auth` parameter, the JSON body): `200` and it arrives means ntfy is fine and DSM sends something else; `403` means the `auth` value or the topic's access. To see DSM's request, point the webhook at a request logger for a moment: `docker run --rm -d --name echo -p 127.0.0.1:2587:8080 mendhak/http-https-echo:31`, port `2587` in the URL, test, `docker logs echo`. |
| DSM's test fails                                      | URL `http://127.0.0.1:2586/…` (not the hostname, for the same reason); `auth` parameter built from `Bearer <token>`, without trailing `=`.                                                                                                                                                                                                                                                                                      |
| Nothing on the phone away from home                   | The VPN didn't connect: check the on-demand rule. Messages are kept 3 days and arrive once the phone reaches the server.                                                                                                                                                                                                                                                                                                        |
| iPhone: nothing, or "new message" without text        | The ping came, but the app couldn't fetch the message: same VPN cause.                                                                                                                                                                                                                                                                                                                                                          |
| `renovate-notify`: "No open Dependency Dashboard"     | Renovate hasn't run yet, or its dashboard issue is closed.                                                                                                                                                                                                                                                                                                                                                                      |
| An update was not notified                            | It was already in the previous run's list (`$OPS_HOME/state/renovate-notified.txt`); delete the file to be told about everything waiting again.                                                                                                                                                                                                                                                                                 |

## Next

- [10 Backups and jobs](10-backups-and-jobs.md): every job also pings
  healthchecks.io, which keeps alerting by email when the NAS itself is down,
  something ntfy on that NAS cannot do.
- [06 mediarr](06-mediarr.md), [05 Authelia](05-sso-authelia.md).
