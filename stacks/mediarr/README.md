# mediarr

Media automation and streaming: sonarr, radarr, prowlarr, qbittorrent,
flaresolverr, jellyfin, jellyseerr, maintainerr.

**Setting it up: [guide 06, media](../../docs/guides/06-mediarr.md).** Shrinking
files with `reencode*.sh`: [media re-encoding](../../docs/guides/media-reencoding.md).
This page is the reference.

|            |                                                                                                                                   |
| ---------- | --------------------------------------------------------------------------------------------------------------------------------- |
| Reached at | `https://<app>.<internal-domain>` for sonarr, radarr, prowlarr, qbittorrent, jellyseerr, maintainerr. Login: Authelia gate        |
|            | `https://jellyfin.<internal-domain>`. Login: Jellyfin's own (no Authelia: Chromecast, [architecture](../../docs/architecture.md)) |
|            | flaresolverr: internal only, no hostname                                                                                          |
| Data       | `MEDIARR_CONFIG_ROOT` → one subfolder per service                                                                                 |
|            | `MEDIA_ROOT` → `torrents/` + `media/`, one root so imports are hardlinks                                                          |
| Needs      | `proxy` network, [edge](../edge/README.md), [authelia](../authelia/README.md), host `/dev/dri`; private `mediarr_net` for all     |
| Used by    | [dashboard](../dashboard/README.md) storage tab (`STORAGE_MEDIA_DIR`)                                                             |
| Config     | `.env.example` (every variable); `TZ` in `compose.yaml`                                                                           |

## Gotchas

- **Never set `AuthenticationMethod` to `None`.** Recent versions reject it and
  lock the app. Leave `Basic`/`Forms`; `DisabledForLocalAddresses` suffices
  because Caddy's requests come from a Docker private range.
- **qBittorrent whitelist = `proxy` subnet**, not the LAN. Update it if the
  network is recreated with another subnet.
- **Same `/data/torrents` path in qBittorrent and the \*arrs**: hardlinks, no
  remote path mapping. Don't mount downloads and library separately.
- **`jellyseerr` stays `:latest`.** The image has no version label to pin;
  Renovate can't track it, upgrade by hand. Other images are pinned.
- **Keep linuxserver tags as `…-lsNNN`.** `renovate.json`'s regex rule for
  `linuxserver/**` needs it; a bare tag silently stops getting updates.
- **Jellyfin transcoding** maps `/dev/dri/renderD128` and `/dev/dri/card0`;
  `PUID` must be able to open them (host `render`/`video` group).
- **Hardening.** All: `no-new-privileges`, `cap_drop: [ALL]`. LSIO images (all
  but flaresolverr, maintainerr) add `CHOWN, SETUID, SETGID, DAC_OVERRIDE`.
  maintainerr runs as `${PUID}:${PGID}` without caps; if it can't write its
  config folder, give it the same `cap_add` (untested).
- **Memory limits are estimates** (jellyfin 2G, qbittorrent 1G, flaresolverr 1G
  after OOMs on Cloudflare challenges at 256M, others 512M). Tune with
  `docker stats`.
- **No wiring in compose.** No `depends_on`; the apps are connected in their
  UIs (guide 06, steps 5–10).
- **Indexers behind Cloudflare challenges FlareSolverr can't solve** are
  disabled here (7 of 14: 1337x, BitSearch, NorTorrent, OxTorrent-vip,
  TorrentGalaxyClone, YourBittorrent, ZkTorrent). Not a resource issue (CPU <
  3 %).
- **Orphaned `bazarr` container** on a NAS that ran an older version: stop and
  remove it before deploying (bazarr is no longer in the stack).
