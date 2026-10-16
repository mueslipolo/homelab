# Synology preparation

> **Needs:** nothing · **Used by:** every other guide
> **Reference:** [`scripts/bootstrap.sh`](../../scripts/bootstrap.sh) · **Why it is built this way:** [architecture](../architecture.md#identity)

## What you get

A Synology NAS ready to run the stacks of this repo: Docker, SSH, the shared
folders the stacks write to, and the `proxy` network every web-facing
container joins.

```
DSM 7.2+
 ├─ Container Manager ── Docker engine + docker compose
 ├─ SSH (local admin account) ── where every command in these guides runs
 ├─ shared folders: docker/ (config) + one per data set (forgejo, media, …)
 └─ Docker network `proxy` ── Caddy ⇄ apps, by container name
```

- **One shared folder per data set**, not one big `docker/`: Hyper Backup and
  Snapshot Replication select whole shared folders, so each data set gets its
  own backup rule ([10 Backups](10-backups-and-jobs.md)). `docker/` holds
  config only.
- **SSH stays on a local account:** DSM's SSH accepts only the local
  `administrators` group, not LDAP users.

## Before you start

| What                   | Example       | How                                                                                                                        |
| ---------------------- | ------------- | -------------------------------------------------------------------------------------------------------------------------- |
| A Synology on DSM 7.2+ | DS920+        | Container Manager must be offered for your model: check Synology's package list for it.                                    |
| The NAS's fixed LAN IP | `192.0.2.10`  | DHCP reservation or static IP: Portainer and Caddy refer to it.                                                            |
| A local admin account  | `admin-local` | Member of `administrators`. Used for SSH and `sudo`; day-to-day users can come from LDAP later ([05](05-sso-authelia.md)). |
| The volume path        | `/volume1`    | Control Panel → Shared Folder shows which volume each share is on.                                                         |

## Steps

### 1. Packages

Package Center → install **Container Manager**. It provides `docker` and
`docker compose` (v2).

Optional, only for the guides that need them:

| Package              | Needed by                                                                       |
| -------------------- | ------------------------------------------------------------------------------- |
| LDAP Server          | [05 Authelia](05-sso-authelia.md): one account for DSM, SMB, Drive and apps     |
| Git Server           | [10 Backups and jobs](10-backups-and-jobs.md): provides `git` for the scheduler |
| Hyper Backup         | [10 Backups and jobs](10-backups-and-jobs.md)                                   |
| Snapshot Replication | [10 Backups and jobs](10-backups-and-jobs.md)                                   |

**Check:** `sudo docker version` and `sudo docker compose version` answer (step 2 first).

### 2. SSH

Control Panel → Terminal & SNMP → **Enable SSH service**. Log in with the
local admin account; Docker needs `sudo`.

```bash
ssh admin-local@192.0.2.10
sudo docker ps
```

**Check:** `sudo docker ps` lists containers (possibly none) without error.

### 3. Shared folders

Control Panel → Shared Folder → Create. Create them as shared folders, not
`mkdir`: only shared folders can be backed up and snapshotted.

| Shared folder | Holds                                                   | Used by                               |
| ------------- | ------------------------------------------------------- | ------------------------------------- |
| `docker`      | per-stack config (`docker/<stack>/…`), Portainer's data | [02](02-portainer.md) and every stack |
| `forgejo`     | git repositories, dumps                                 | [04 Forgejo](04-forgejo-gitops.md)    |
| `media`       | downloads and the media library, one tree (hardlinks)   | [06 mediarr](06-mediarr.md)           |
| `paperless`   | documents                                               | [07 Paperless](07-paperless.md)       |
| `mealie`      | recipes, dumps                                          | [08 Mealie](08-mealie.md)             |

Create only those for the stacks you deploy. Exact sub-paths: each stack's
`.env.example`.

**Check:** `ls /volume1` shows the folders.

### 4. User and group IDs

Stacks that write to shared folders run as a given UID/GID (`PUID`/`PGID` for
linuxserver images, `PAPERLESS_UID`…, or a fixed `user:`). Use the account
that should own the files:

```bash
id media-user          # uid=1026(media-user) gid=100(users) groups=100(users),101(administrators)
```

**Check:** the folders a stack writes to are owned by that UID/GID
(`ls -ln /volume1/media`), or give them to it with `sudo chown -R 1026:100 …`.

### 5. The `proxy` network

Every web-facing container joins it; Caddy reaches apps there by container
name ([03 HTTPS](03-https-letsencrypt.md)).

```bash
sudo docker network create proxy     # or: sudo scripts/bootstrap.sh (idempotent)
```

**Check:** `sudo docker network inspect proxy --format '{{json .IPAM.Config}}'`
prints its subnet and gateway. Note the gateway: it is `PROXY_GATEWAY_IP` in
the edge stack.

## Troubleshooting

| Symptom                                           | Cause → fix                                                                                |
| ------------------------------------------------- | ------------------------------------------------------------------------------------------ |
| `permission denied … docker.sock`                 | Missing `sudo`: the socket is `root:root` 660 on DSM.                                      |
| SSH login refused for an LDAP or non-admin user   | Expected: DSM's SSH takes only the local `administrators` group. Use the local admin.      |
| Shared folder missing from Hyper Backup's sources | It was created with `mkdir`. Create it in Control Panel → Shared Folder and move the data. |
| A container cannot write its folder (`EACCES`)    | Wrong owner: `ls -ln`, then `chown` to the stack's UID/GID.                                |
| `docker compose` not found                        | Old Container Manager: use `docker-compose`, or update the package.                        |

## Next

- [02 Portainer](02-portainer.md): deploy stacks from a UI, or from git.
- [03 HTTPS](03-https-letsencrypt.md): `https://<app>.home.example.com` with a real certificate.
- [05 Authelia](05-sso-authelia.md): LDAP Server and one login for everything (accounts: moving a local
  user to LDAP means _renaming_ the local account, never delete-then-create).
