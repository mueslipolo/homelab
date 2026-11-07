# Immich: the family photo library

> **Needs:** [03 HTTPS](03-https-letsencrypt.md), [02 Dockhand](02-dockhand.md) (Immich is deployed by Dockhand), [05 Authelia](05-sso-authelia.md) (OIDC login; without it, Immich's own password login) · **Optional:** [10 Backups and jobs](10-backups-and-jobs.md) (strongly advised before phase B), [09 Dashboard](09-dashboard.md) (the dump's light) · **Used by:** the phones; later the photo migration with kairos
> **Reference:** [`stacks/immich/README.md`](../../stacks/immich/README.md)

## What you get

Immich at `https://immich.home.example.com`: one library per person, logged
in through Authelia, with the Immich app backing up each Android phone's
camera on the home Wi-Fi. Synology Photos is removed at the end, **its files
kept**: the photos in each home's `Photos` folder (`Photos/MobileBackup` and
the rest) are the originals a later migration will read. Nothing in this guide
deletes, moves or edits them.

Three phases, each ending with a check you can stop at:

| Phase                      | Steps | Done when                                                                                |
| -------------------------- | ----- | ---------------------------------------------------------------------------------------- |
| **A** Immich and its login | 1–7   | one person logs in through Authelia on the web; the dump and the backups run             |
| **B** Phones, in parallel  | 8–10  | both phones back up to Immich and to Synology Photos, until your tests pass              |
| **C** Synology Photos goes | 11–14 | its backup is off on the phones, the package is uninstalled, the file count is unchanged |

The examples use two people, `alice` (the admin) and `bob`.

## Before you start

| What                     | Example                                              | How                                                                                                                                       |
| ------------------------ | ---------------------------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------- |
| Caddy and the domain     | `immich.home.example.com`                            | [03 HTTPS](03-https-letsencrypt.md); the site block is already in the Caddyfile                                                           |
| Authelia with OIDC       | `https://auth.home.example.com`                      | [05 Authelia](05-sso-authelia.md), steps 1–3                                                                                              |
| An email per person      | `alice@example.com`                                  | LDAP Server → user → Email. Immich creates an account only for a login that carries an email, and matches existing accounts by it         |
| Folders                  | `/volume1/immich`, `/volume1/docker/immich`          | step 1                                                                                                                                    |
| A database password      | —                                                    | `openssl rand -hex 32` (letters and digits only)                                                                                          |
| Memory                   | 2–5 GB free while the phones' first backups run      | `free -m` on the NAS. Short of it: delete the kairos stack first ([Free memory](#free-memory-the-kairos-stack))                           |
| The Immich app           | on both phones                                       | Google Play or F-Droid, "Immich"                                                                                                          |
| The phones on home Wi-Fi | `immich.home.example.com` resolves to Caddy's LAN IP | as for every app here ([03](03-https-letsencrypt.md)). Immich is LAN-only: away from home the app waits, and catches up on the home Wi-Fi |

## Phase A: Immich and its login

### 1. Folders

`immich` as its own shared folder, so Hyper Backup and Snapshot Replication
can take it: Control Panel → Shared Folder → Create, name `immich`, on the
volume with room for every photo and video twice (the phones now, the
migration later). Permissions: the admin only; nobody edits these files by
hand (Immich does not rescan its own storage). Then the database's folder:

```bash
sudo mkdir -p /volume1/docker/immich/postgres
```

**Check:** `ls -ld /volume1/immich /volume1/docker/immich/postgres` lists both.

### 2. The OIDC client in Authelia

1. One secret for the client `immich`:

   ```bash
   AUTHELIA_VERSION=$(grep -oE 'authelia/authelia:[^ ]+' stacks/authelia/compose.yaml | cut -d: -f2)
   sudo docker run --rm "authelia/authelia:$AUTHELIA_VERSION" \
     authelia crypto hash generate pbkdf2 --random --random.length 64 --random.charset alphanumeric
   ```

   `Digest` → Authelia's secret file:

   ```bash
   echo -n '<Digest>' | sudo tee /volume1/docker/authelia/secrets/immich_client_secret_hash >/dev/null
   sudo chmod 600 /volume1/docker/authelia/secrets/immich_client_secret_hash
   ```

   `Random Password` → Immich, in step 5. Keep it until then.

2. The authelia stack's variables in Dockhand: add `immich` to
   `OIDC_CLIENTS`. If the variable is not set yet, **add it**, set to the apps
   you run plus `immich`, e.g. `paperless,mealie,forgejo,dockhand,immich`:
   without it the compose default applies, which does not list `immich`.
   Deploy the stack (Re-pull images off).
3. Restart Authelia (`sudo docker restart <authelia-container>`): it reads its
   configuration only at start.

The client itself (redirect URIs, scopes, `client_secret_post`) is in
`stacks/authelia/config/configuration.yml` and only applies when `immich` is
in `OIDC_CLIENTS`, so the order above never starts Authelia without the file.

**Check:** `sudo docker logs <authelia-container> 2>&1 | tail -5` shows no
error, and `curl -s https://auth.home.example.com/api/health` →
`{"status":"OK"}`.

### 3. Deploy

Variables, from [`stacks/immich/.env.example`](../../stacks/immich/.env.example):

```bash
TZ=Europe/Berlin                              # your time zone
IMMICH_UPLOAD_LOCATION=/volume1/immich
IMMICH_DOCKER_ROOT=/volume1/docker/immich
DB_PASSWORD=<secret>
```

**With Dockhand:** Stacks → from Git, as in [02 Dockhand, step 4](02-dockhand.md#4-deploy-a-git-stack):
name `immich` (not the pre-filled repository name), compose path
`stacks/immich/compose.yaml`, the variables above (`DB_PASSWORD` marked
**secret**), Enable scheduled sync on (daily, `15 4 * * *`, with its webhook,
[02, step 5](02-dockhand.md#5-a-webhook-per-stack-with-forgejo)), Re-pull
images, Force redeploy and Build images on deploy off → Deploy. The first
deploy pulls about 3 GB of images.

**Check:** four containers; `immich-server`, `immich-database` and
`immich-redis` turn healthy within a few minutes (the first start creates the
database), and `sudo docker logs <immich-server-container>` ends without
errors.

### 4. Through Caddy

The `immich` block is already in the Caddyfile; Caddy reloads it by itself
once the edge stack has the new file. No `import authelia`: the phone app
talks to Immich's API directly, and Immich does its own login.

**Check:** `curl -s https://immich.home.example.com/api/server/ping` →
`{"res":"pong"}`, and the page shows Immich's welcome screen.

### 5. The admin, then the Authelia login

1. **Getting Started**: create the admin with **alice's LDAP email**, her
   name and a strong password. That password stays as the fallback login.
   Finish the onboarding (theme, privacy settings; leave the storage
   template off: where the files go is decided with the migration).
2. Administration → Settings → **Authentication Settings** → **OAuth**:

   | Setting                      | Value                                                   |
   | ---------------------------- | ------------------------------------------------------- |
   | Login with OAuth             | on                                                      |
   | Issuer URL                   | `https://auth.home.example.com`                         |
   | Client ID                    | `immich`                                                |
   | Client Secret                | the `Random Password` of step 2                         |
   | Token endpoint auth method   | `client_secret_post` (the default)                      |
   | Scope                        | `openid email profile` (the default)                    |
   | Signing algorithms           | defaults (`RS256`, profile `none`)                      |
   | Storage label claim          | `preferred_username` (the default: `alice`, `bob`)      |
   | Role claim, quota claim      | defaults (Authelia sends neither; roles stay in Immich) |
   | Button text                  | `Login with Authelia`                                   |
   | Auto register                | on (bob's account is made at his first login)           |
   | Auto launch                  | off for now: the password form stays visible            |
   | Mobile redirect URI override | off                                                     |

   Save. **Password Login** stays on, as the fallback.

3. Administration → Settings → **Server Settings** → **External domain**:
   `https://immich.home.example.com` (used in shared links).

**Check:** log out, **Login with Authelia** → Authelia → back in alice's
account, with Administration visible. A new, empty account instead means the
emails differ: see Troubleshooting.

### 6. Fit it to the DS920+

Four slow cores, shared with Paperless's OCR. The stack already lets machine
learning serve 2 requests at once (`MACHINE_LEARNING_REQUEST_THREADS`); in
Administration → Job Queues → **Manage Concurrency**:

| Job                  | Concurrency | Default |
| -------------------- | ----------- | ------- |
| Smart Search         | 1           | 2       |
| Face Detection       | 1           | 2       |
| Thumbnail Generation | 2           | 3       |
| OCR                  | 1           | 1       |
| Video Conversion     | 1           | 1       |

The first backups still keep these queues busy for hours: Immich catches up
by itself. Machine learning runs on the CPU (the UHD 600 is too old for
Immich's OpenVINO backend to be worth it); video can use Quick Sync, optional:
[Video on the iGPU](#video-on-the-igpu-optional).

**Check:** Job Queues show the new values.

### 7. Backups

Immich dumps its own database every night into
`/volume1/immich/backups` (14 kept). Move it before the off-site backup:
Administration → Settings → **Database Dump Settings** → cron
`20 20 * * *` (20:20, after the other dumps, before Hyper Backup at 20:40).
Then, as in [10 Backups and jobs](10-backups-and-jobs.md):

1. **Hyper Backup**, both tasks: add the `immich` shared folder's `upload`,
   `library`, `profile` and `backups`. Leave out `thumbs` and `encoded-video`:
   Immich makes them again. The photos without a dump, or a dump without the
   photos, restore nothing.
2. **Snapshot Replication**: immutable snapshots of `immich`, like `homes`.
3. The dump's age on the dashboard:
   `IMMICH_DUMP_DIR=/volume1/immich/backups` in `jobs.env`.

**Check:** Job Queues → Create job → **Create Database Dump**; a
`immich-db-backup-…sql.gz` appears in `/volume1/immich/backups`, and the
dashboard's Backups tab shows "Immich database dump" at the next tick.

## Phase B: the phones, in parallel with Synology Photos

### 8. Alice's phone

1. Immich app → server URL `https://immich.home.example.com` → **Login with
   Authelia** → Authelia in the browser → back in the app, logged in.
2. Cloud icon (top right) → **Backup Albums** → select **Camera** only.
   Not WhatsApp, Screenshots, Download or the other app folders: they stay
   on the phone and in Synology Photos' copy, and the migration sorts them
   later.
3. **Enable Backup**. In the backup options: cellular data for photos and
   videos **off** (the default; Immich is unreachable away from home anyway),
   background backup **on**.
4. Android → Apps → Immich → Battery → **Unrestricted**, notifications
   allowed. Some phones still stop background apps:
   [dontkillmyapp.com](https://dontkillmyapp.com) has the steps per brand.
5. The first backup: app open, on the home Wi-Fi, charging, overnight.

Don't use "Free up space" or delete from the phone in either app during the
parallel run.

**Check:** the app's backup screen shows nothing left to upload, and the web
timeline shows the camera's photos with their dates. Administration → Job
Queues: no failed jobs.

### 9. Bob's account and phone

Bob logs in from the app (or the web) with **Login with Authelia**: Immich
creates his account, storage label `bob`. Then step 8 on his phone.

**Check:** Administration → Users lists `alice` and `bob`; bob's timeline
holds only his photos.

Then, to see each other's photos without sharing them one by one: **Partner
Sharing**, in both accounts (it works one way: alice adding bob shows alice's
photos to bob).

1. Account Settings → Partner Sharing → **Add partner** → the other account.
2. On the partner's entry, **Show in timeline**: the partner's photos join your
   timeline by date. Without it, they are under Partners in the sidebar.

What it does not do:

- **Read-only.** Each photo stays its owner's: the partner can view and
  download it, not edit, delete or archive it.
- **Archived photos and the locked folder stay private.**
- **People are per account.** Faces are recognised in each account
  separately; your People page shows the faces in your own photos only.
- **Albums are separate.** Share an album on its own for both to add to it.
- **Duplicates are per account.** A photo both phones have (a WhatsApp group)
  is stored twice and shows twice in a shared timeline.
  `immich-verify overlap alice bob` lists them
  ([tools](../../tools/README.md#immich-verify)).

**Check:** bob's timeline shows alice's photos next to his own.

### 10. Run both for a while

Both apps back up the camera: Immich into `/volume1/immich`, Synology Photos
into `homes/<user>/Photos/MobileBackup` as before. Keep it so until these
checks pass on both phones; then phase C can start. Ideally one of them covers
a day away from home, to see the app catch up when back.

**Checks:**

- A photo taken yesterday is in Immich and in Synology Photos.
- After a trip, the Immich app caught up on the home Wi-Fi by itself.
- Job Queues: no failed jobs. Dashboard: Immich's dump green, Hyper Backup
  green.
- `free -m`: the NAS is not swapping hard; `sudo docker inspect --format '{{.State.OOMKilled}}' <container>` is `false` for the Immich containers.

## Phase C: Synology Photos goes, its files stay

Start once phase B's checks pass on both phones. The files that
Synology Photos wrote are the originals of the later migration: every step
below keeps them, and the count before and after proves it.

### 11. Stop Synology Photos' backup on the phones

In the Synology Photos app on each phone: Settings → **Photo Backup** → off
(the wording varies between app versions). Then uninstall the app from the
phone: that removes the app, not the photos on the phone or the NAS.

In `uploaders.conf` (the dashboard's "phones that must keep uploading", [10](10-backups-and-jobs.md)),
point each phone at its owner's Immich folder, or it turns red: the fifth
column is the folder to watch. It covers the person, not the device, and
depends on the storage template (Administration → Settings → Storage
Template):

| Storage template | Folder                                                                                                                                                                                                      |
| ---------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| off              | `/volume1/immich/upload/<Immich user ID>` (Account Settings → Account → User ID)                                                                                                                            |
| on               | `/volume1/immich/library/<storage label>` (`alice`, `bob`; the user ID for an account without one). New files pass through `upload/` and move here after their metadata is read: `upload/` stays near empty |

Turning the template on later moves every file (the "Storage Template
Migration" job, one asset at a time: pause Thumbnail Generation while it
runs: two to three times faster) and makes the `upload/` lines go red:
change them once it is done.

**Check:** a day later, the phone is green on the Backups tab, and no new
file appears under `Photos/MobileBackup`.

### 12. Count, and snapshot

Before touching the package, record what is there (`@eaDir` holds Synology's
thumbnails, not photos):

```bash
sudo find /volume1/homes -path '*/Photos/*' -type f ! -path '*/@eaDir/*' | sort > ~/photos-before.txt
wc -l ~/photos-before.txt
sudo du -sh /volume1/homes/*/Photos /volume1/homes/@*/*/*/Photos 2>/dev/null
```

Add `/volume1/photo` to the `find` if Synology Photos' Shared Space was used.

Then Snapshot Replication → Snapshots → `homes` → **Take Snapshot**,
immutable, description "before Synology Photos uninstall". Check that the
last Hyper Backup of `homes` succeeded.

**Check:** the snapshot is listed; `photos-before.txt` is not empty.

### 13. Stop, then uninstall the package

1. Package Center → Synology Photos → **Stop**. Live with it stopped for a
   few days: shared links and the Photos web page stop; the files do not
   change.
2. If a Hyper Backup task includes Synology Photos under its applications,
   remove it from the task first (else the task reports an error).
3. Package Center → Synology Photos → **Uninstall**. DSM asks whether to
   delete the package's data as well: **leave every delete option
   unticked**. If the dialog mentions deleting photos, files or shared
   folders, cancel and stop here.

What the uninstall removes: the package and, only if ticked, its database
(albums, people, shared links, its index). The photo files in the homes and
in `/volume1/photo` are ordinary files in shared folders, which uninstalling
a package does not remove. Synology does not document this dialog in detail;
the count below is what proves it on your NAS.

**Check:**

```bash
sudo find /volume1/homes -path '*/Photos/*' -type f ! -path '*/@eaDir/*' | sort > ~/photos-after.txt
diff ~/photos-before.txt ~/photos-after.txt && echo "same files"
```

`same files`. Anything else: restore from the step 12 snapshot (Snapshot
Replication → Snapshots → `homes` → Restore, or copy single files from
`#snapshot` in File Station) before anything else.

### 14. Keep the originals where they are

Leave `homes/<user>/Photos` in Hyper Backup and Snapshot Replication, and the
`@eaDir` folders in place: the migration reads these folders read-only and
skips them.

## Import existing photos

The photos already on the NAS (each home's `Photos`) and a Google Takeout go
into Immich's own storage with `scripts/immich-import.sh`, a small wrapper
around [immich-go](https://github.com/simulot/immich-go). It reads the sources
read-only, runs immich-go in a container on `proxy` (the NAS cannot reach
Caddy's macvlan address, so it talks to `immich-server` directly) and is a dry
run unless `--apply`.

1. **A key per person:** in that person's Immich account, Account settings →
   API Keys → New, permission **all**. Save it as `~/immich-go/token.<login>` of the admin user
   you run `sudo` from, `chmod 600`. The key never goes on a command line: the
   script hands it to immich-go in a root-only file, removed afterwards.
   immich-go pauses Immich's jobs during the upload, which only an admin's key
   may do: for someone who is not an Immich admin, add `--admin <your login>`
   (your own `token.<login>`); the photos still go to their account.
2. **Dry run**, from the scheduler's clone:

   ```bash
   cd /volume1/docker/homelab-ops
   sudo scripts/immich-import.sh alice "/volume1/homes/alice/Photos"
   ```

   It downloads immich-go the first time, then lists what it would upload,
   what is already in Immich ("server has duplicate"), what it skips (DSM's
   `@eaDir`, `#recycle`, `#snapshot`, Synology thumbnails) and any errors. The
   full log is in `~/immich-go/logs/`.

3. **The import:** the same command with `--apply`. Every photo gets the tag
   `import/<login>/<date>`, to find (or remove) the batch. A Takeout:
   `sudo scripts/immich-import.sh bob /volume1/homes/bob/Takeout/takeout-*.zip --takeout`
   (its albums, dates, descriptions and people come along).

**Duplicates:** Immich keeps one copy of a file per person, by content: running
it again, or a photo the phone app already sent, uploads nothing. The same
photo in two people's folders is uploaded to both: decide whose it is before
importing the second person. Near-duplicates (resized, recompressed) stay;
Administration → Utilities → **Duplicates** reviews them afterwards.

**Dates:** a photo with no date inside takes the one in its name
(`IMG-20161012-WA0000`), immich-go's `--date-from-name`, on by default.

**From a desktop, or before deleting a source:**
[`tools/immich-verify`](../../tools/README.md#immich-verify) hashes folders and
Takeout zips where they are and asks Immich which it already has, sending only
the hashes; `--extract` copies out the missing ones for immich-go. Only
photos Immich already has, byte for byte, count; a source is safe to delete
once that says so and Immich's storage has been backed up since (step 7).

## Free memory: the kairos stack

kairos no longer runs on the NAS: it becomes a one-off tool, run from a
laptop, for the migration into Immich. Its stack, jobs, Caddy site and
Authelia rule are gone from this repo (git history keeps them). Once, at any
phase, which frees up to 2 GB:

1. Dockhand → Stacks → kairos → **Delete** the stack. Not Stop:
   a stopped `restart: always` container starts again at the next reboot
   ([operations](../operations.md#reboots)). The `docker-prune` job removes
   its unused images later.
2. Keep its data folder (`KAIROS_DATA`) and its catalog dumps
   (`KAIROS_DUMP_DIR`), or archive a copy of them: the catalog holds the date
   corrections made in kairos, which the migration can reuse. Do not delete
   them.
3. Remove `KAIROS_DUMP_DIR` from `jobs.env` (or its dump light turns red after
   2 days), and `KAIROS_PROXY_SECRET` from the edge stack's variables.

## Video on the iGPU (optional)

The DS920+'s UHD 600 has Quick Sync, which Jellyfin already uses here.
Immich transcodes only videos the browser cannot play (HEVC from the phones,
by default to 720p H.264); on the CPU that is slow but works, so start without
it. To try it:

1. In `stacks/immich/compose.yaml`, uncomment the `devices:` block of
   `immich-server` (the two `/dev/dri` devices); push.
2. Administration → Settings → **Video Transcoding Settings** → Hardware
   Acceleration → **Quick Sync**; hardware decoding on.

**Check:** Job Queues → Video Conversion → run on a phone's HEVC video; no
error in `sudo docker logs <immich-server-container>`, and the video plays in
the browser. Errors: set Hardware Acceleration back to Disabled (or try
VAAPI); Immich calls hardware transcoding experimental.

## Rollback

| Phase | Undo                                                                                                                                                                                                                                                                              |
| ----- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| A     | Delete the immich stack in Dockhand; remove `immich` from Authelia's `OIDC_CLIENTS` and restart Authelia. `/volume1/immich` and `/volume1/docker/immich` can then go. Nothing else changed.                                                                                       |
| B     | Turn backup off in the Immich app (or uninstall it). Synology Photos never stopped, so nothing is missing; what is in Immich stays there.                                                                                                                                         |
| C     | Before the uninstall: Package Center → Synology Photos → Run, reinstall the Synology Photos app, turn its backup on. After it: install Synology Photos again; it indexes the same folders (its albums return only if its database was kept). Missing files: the step 12 snapshot. |

## Troubleshooting

| Symptom                                                             | Cause → fix                                                                                                                                                                                                 |
| ------------------------------------------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Authelia does not start after step 2                                | `immich` is in `OIDC_CLIENTS` but `immich_client_secret_hash` is missing or empty: create it, or take `immich` out again.                                                                                   |
| Authelia: `redirect_uri` not allowed                                | The URL differs from the client's list, or Authelia still runs without the `immich` client: check `OIDC_CLIENTS`, restart Authelia.                                                                         |
| Login fails with an authentication error                            | Digest and Random Password swapped: the Digest goes to Authelia, the password to Immich.                                                                                                                    |
| "OAuth profile does not have an email address"                      | The LDAP user has no email: add it in LDAP Server, log in again.                                                                                                                                            |
| Login with Authelia lands in a new, empty account                   | The admin's email differs from the LDAP email. Administration → Users: delete the new account, give the admin the LDAP email, log in again.                                                                 |
| Immich: OAuth error, server log shows a timeout to `auth.…`         | `immich-server` must be on `proxy` (Caddy's alias for `auth.<domain>`); the macvlan IP is unreachable from the NAS.                                                                                         |
| Phone: after Authelia the browser stays open, the app never logs in | The browser did not hand `app.immich:///oauth-callback` to the app. Turn on **Mobile redirect URI override** with `https://immich.home.example.com/api/oauth/mobile-redirect` (Authelia already allows it). |
| Phone: server unreachable at home                                   | Android's Private DNS bypasses the LAN's DNS: set it to Off or Automatic, or check the name resolves to Caddy.                                                                                              |
| Phone: server unreachable away from home                            | Expected: LAN-only. The app catches up on the home Wi-Fi.                                                                                                                                                   |
| Background backup stops                                             | Battery optimization: Immich to Unrestricted (step 8).                                                                                                                                                      |
| `immich-server` restarts, `EACCES` on `/data`                       | It needs `DAC_OVERRIDE` on a DSM shared folder (kept in `compose.yaml`), and `IMMICH_UPLOAD_LOCATION` must exist.                                                                                           |
| A container restarts under load, `OOMKilled` true                   | Its memory limit: raise it in `compose.yaml`, or free memory ([kairos stack](#free-memory-the-kairos-stack)).                                                                                               |

## Next

- [10 Backups and jobs](10-backups-and-jobs.md): restore one photo and one dump into a scratch folder once.
- The migration of the existing photos into Immich (kairos, run from a laptop) is planned separately, once Immich has been lived with.
