# mediarr

Media automation and streaming: sonarr, radarr, prowlarr, qbittorrent, flaresolverr, jellyfin, jellyseerr, maintainerr (no `bazarr`, unused).

|            |                                                                                                                             |
| ---------- | --------------------------------------------------------------------------------------------------------------------------- |
| Reached at | `https://<app>.<internal-domain>` for sonarr, radarr, prowlarr, qbittorrent, jellyseerr, maintainerr — login: Authelia gate |
|            | `https://jellyfin.<internal-domain>` — login: Jellyfin's own (Caddy bypass for Chromecast, `docs/ARCHITECTURE.md`)          |
|            | flaresolverr — internal only, no hostname                                                                                   |
| Data       | `MEDIARR_CONFIG_ROOT` → one subfolder per service                                                                           |
|            | `MEDIA_ROOT` → `torrents/` + `media/`, one root so moves are hardlinks/atomic                                               |
| Needs      | `proxy` network (all but flaresolverr; private `mediarr_net` for all), `edge`, `authelia`, host `/dev/dri`                  |
| Config     | `.env.example` (every variable)                                                                                             |

## Deploy

See `docs/INSTALL.md` for the install order.

1. Create the folders, owned by the `PUID`/`PGID` user (`id <user>`, `getent group <name>`):
   ```bash
   mkdir -p <nas-volume-path>/docker/mediarr-config/{sonarr,radarr,prowlarr,qbittorrent,jellyfin,jellyseerr,maintainerr}
   mkdir -p <nas-volume-path>/media/{torrents,media}
   chown -R <uid>:<gid> <nas-volume-path>/docker/mediarr-config <nas-volume-path>/media
   ```
2. Portainer → Stacks → Add stack → Repository, compose path `stacks/mediarr/compose.yaml`, variables from `.env.example` (real UID/GID only in Portainer) → Deploy.
3. Disable each app's own login, or you log in twice behind Authelia (stop the app, edit, start):
   ```bash
   # Sonarr, Radarr, Prowlarr: each <app>/config.xml
   sed -i 's|<AuthenticationRequired>Enabled</AuthenticationRequired>|<AuthenticationRequired>DisabledForLocalAddresses</AuthenticationRequired>|' <MEDIARR_CONFIG_ROOT>/<app>/config.xml
   # qBittorrent: whitelist the proxy network's subnet (docker network inspect proxy), not the LAN
   sed -i '/^WebUI\\Address=\*$/a WebUI\\AuthSubnetWhitelistEnabled=true\nWebUI\\AuthSubnetWhitelist=<proxy-subnet>' <MEDIARR_CONFIG_ROOT>/qbittorrent/qBittorrent.conf
   ```
4. maintainerr UI: add Sonarr/Radarr/Jellyfin/Jellyseerr integrations (API key in each app's Settings → General), then cleanup rules (e.g. "unwatched > 90 days AND not requested in 30 days → unmonitor + delete"). Nothing is deleted until a rule exists.
5. **Check:** `https://sonarr.<internal-domain>` → Authelia login, then Sonarr with no second login.

## Re-encoding (saves disk space)

`reencode.sh` re-encodes large H.264 (or older) files to HEVC in place, on a
desktop with an Intel GPU and the `media` share mounted. It runs there, not on
the NAS: the NAS's GPU is about 5× slower and gives a worse size for the same
quality.

**Presets**, by type of material (numbers in `reencode-presets.sh`):

| Preset   | For                                     | Typical result, 1080p   |
| -------- | --------------------------------------- | ----------------------- |
| `sitcom` | sitcoms, old TV, animation, talk shows  | ~1.5–3 Mbit/s, smallest |
| `series` | modern drama series                     | ~3–5 Mbit/s             |
| `film`   | films, cinematic, dark or grainy series | ~5–8 Mbit/s, best       |

Each preset leaves alone files that are already small enough for it.

```bash
reencode.sh -n sitcom /mnt/media/media/tv                    # what would change, per series
reencode-compare.sh "/mnt/media/media/tv/30 Rock/<episode>.mkv"   # not sure? compare the presets
reencode.sh sitcom "/mnt/media/media/tv/30 Rock"             # convert a series
```

- `reencode-compare.sh` encodes the same excerpt with each preset and writes to
  `~/reencode/compare/<episode>/`:
  - `summary`: the estimated episode size and saving, and VMAF if ffmpeg has
    libvmaf (95+ means indistinguishable)
  - `split-<preset>.mkv`: original left, re-encode right
  - `frames/`: matching still frames

  Pick the smallest preset you can't tell from the original.

- `reencode.sh` skips HEVC/AV1 files. It keeps every audio, subtitle and
  attachment stream, and replaces a file only after checking the new one:
  same duration, same streams, at least 10 % smaller. An `.mp4`/`.avi`
  becomes `.mkv`.
- **Mount the share with the kernel's SMB client, not the file manager's
  `smb://` (GVFS).** GVFS dropped long reads of large files ("Read error at
  pos …"), while the same files read fine on the NAS. Set it up once with
  sudo; after that the share mounts on first access, as your user:
  `//<nas-ip>/media /mnt/media cifs credentials=<home>/.smb-nas,uid=1000,gid=1000,vers=3.0,iocharset=utf8,noauto,x-systemd.automount,x-systemd.idle-timeout=10min,_netdev 0 0`
  in `/etc/fstab`. This needs `cifs-utils`.
- Measured on a Core Ultra 5 238V: a 23-minute 1080p episode with `sitcom`
  takes 2 min, 2.4 GB → 381 MB, with no visible loss on old TV material.
- **Use HEVC, not AV1:** the NAS can't decode AV1 in hardware, so Jellyfin would
  transcode it on the CPU.
- **3D releases** (`3D`, `SBS`, `HOU` in the name) show two pictures side by
  side on a 2D screen. Replace them; re-encoding keeps them 3D. Block them in
  Sonarr/Radarr with a custom format scored -10000.
- **Afterwards:** Sonarr/Radarr → Refresh & Scan, then unmonitor the series or
  use a profile without upgrades. Otherwise they may download the large
  release again.
- **Desktop setup** (Ubuntu 24.04 on Lunar Lake): the stock driver predates the
  chip and `vainfo` fails. Use Intel's packages
  (`ppa:kobuk-team/intel-graphics`, `intel-media-va-driver-non-free`) and
  `LIBVA_DRIVER_NAME=iHD`. The scripts set the latter.

## Gotchas

- **Never set `AuthenticationMethod` to `None`.** Recent versions reject it and lock the app. Leave `Basic`/`forms`; `DisabledForLocalAddresses` suffices because Caddy's requests come from a Docker private range.
- **qBittorrent whitelist = `proxy` subnet.** Update it if the network is recreated with another subnet.
- **Orphaned `bazarr` container** (NAS that ran an older version only): Portainer → Containers → `bazarr` → Stop, Remove, before deploying.
- **`jellyseerr` stays `:latest`.** No version label to pin; Renovate can't track it, upgrade by hand. Other images are pinned in `compose.yaml`.
- **Keep linuxserver tags as `…-lsNNN`.** `renovate.json`'s regex rule for `linuxserver/**` needs it; a bare tag silently stops getting updates.
- **Jellyfin transcoding.** Maps `/dev/dri/renderD128` and `/dev/dri/card0`; check host `render`/`video` group membership.
- **Hardening.** All: `no-new-privileges`, `cap_drop: [ALL]`. LSIO images (all but flaresolverr, maintainerr) add `CHOWN, SETUID, SETGID, DAC_OVERRIDE`. maintainerr runs as `${PUID}:${PGID}` without caps; if it can't write its config folder, give it the same `cap_add` (untested).
- **Memory limits are estimates** (jellyfin 2G, qbittorrent 1G, flaresolverr 1G after OOMs on Cloudflare challenges at 256M, others 512M). Tune with `docker stats`.
- **No wiring in compose.** No `depends_on`; qBittorrent isn't API-wired as a download client.
- **7 of 14 Prowlarr indexers disabled** (1337x, BitSearch, NorTorrent, OxTorrent-vip, TorrentGalaxyClone, YourBittorrent, ZkTorrent): Cloudflare challenges FlareSolverr can't solve (not resources, CPU < 3%). BitSearch fails even without FlareSolverr; not investigated.
