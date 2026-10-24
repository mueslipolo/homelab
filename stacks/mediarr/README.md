# mediarr

Media automation and streaming: sonarr, radarr, prowlarr, qbittorrent,
flaresolverr, jellyfin, seerr, maintainerr.

**Setting it up: [guide 06, media](../../docs/guides/06-mediarr.md).** Shrinking
files with `reencode*.sh`: [media re-encoding](../../docs/guides/media-reencoding.md).
This page is the reference.

|            |                                                                                                                                   |
| ---------- | --------------------------------------------------------------------------------------------------------------------------------- |
| Reached at | `https://<app>.<internal-domain>` for sonarr, radarr, prowlarr, qbittorrent, seerr, maintainerr. Login: Authelia gate             |
|            | `https://jellyfin.<internal-domain>`. Login: Jellyfin's own (no Authelia: Chromecast, [architecture](../../docs/architecture.md)) |
|            | flaresolverr: internal only, no hostname                                                                                          |
| Data       | `MEDIARR_CONFIG_ROOT` → one subfolder per service                                                                                 |
|            | `MEDIA_ROOT` → `torrents/` + `media/`, one root so imports are hardlinks                                                          |
| Needs      | `proxy` network, [edge](../edge/README.md), [authelia](../authelia/README.md), host `/dev/dri`; private `mediarr_net` for all     |
| Used by    | [dashboard](../dashboard/README.md) storage tab (`STORAGE_MEDIA_DIR`)                                                             |
| Config     | `.env.example` (every variable); `TZ` in `compose.yaml`                                                                           |

## Gotchas

- **Never set `AuthenticationMethod` to `None`.** Leave `Basic`/`Forms`; `DisabledForLocalAddresses` suffices
  because Caddy's requests come from a Docker private range.
- **Caddy drops `X-Forwarded-For` for Sonarr, Radarr and Prowlarr**
  (`arr_no_client_ip` in the Caddyfile). With the header, a LAN client is
  treated as remote and gets the app's own login after Authelia's (tested on
  Prowlarr 2.6 only: `200` without the header, `302 /login` with
  `X-Forwarded-For: 192.168.x.x`). Their logs then show Caddy's address.
- **qBittorrent whitelist = `proxy` subnet**, not the LAN. Update it if the
  network is recreated with another subnet.
- **Same `/data/torrents` path in qBittorrent and the \*arrs**: hardlinks, no
  remote path mapping. Don't mount downloads and library separately.
- **Seerr, not Jellyseerr.** Seerr replaces Jellyseerr
  ([migration guide](https://docs.seerr.dev/migration-guide)). Seerr migrated its config in place on
  first start, so the config folder is still `MEDIARR_CONFIG_ROOT/jellyseerr`,
  and the service keeps a `jellyseerr` network alias for integrations set up
  with that host. `jellyseerr.<internal-domain>` redirects to `seerr.`.
- **Seerr needs `init: true`**: the image has no init process of its own.
- **Keep linuxserver tags as `…-lsNNN`.** `renovate.json`'s regex rule for
  `linuxserver/**` needs it; a bare tag silently stops getting updates.
- **Jellyfin transcoding** maps `/dev/dri/renderD128` and `/dev/dri/card0`;
  `PUID` must be able to open them (host `render`/`video` group).
- **Hardening.** All: `no-new-privileges`, `cap_drop: [ALL]`. LSIO images (all
  but flaresolverr, seerr, maintainerr) add `CHOWN, SETUID, SETGID, DAC_OVERRIDE`.
  maintainerr runs as `${PUID}:${PGID}` without caps; if it can't write its
  config folder, give it the same `cap_add` (untested).
- **Memory limits are estimates** (jellyfin 2G, qbittorrent 1G, flaresolverr 1G
  after OOMs on Cloudflare challenges at 256M, others 512M). Tune with
  `docker stats`.
- **No wiring in compose.** No `depends_on`; the apps are connected in their
  UIs (guide 06, steps 5–10).
- **Indexers behind Cloudflare challenges FlareSolverr can't solve** are
  disabled here (7 of 14: 1337x, BitSearch, NorTorrent, OxTorrent-vip,
  TorrentGalaxyClone, YourBittorrent, ZkTorrent).
- **Orphaned `bazarr` container** on a NAS that ran an older version: stop and
  remove it before deploying (bazarr is no longer in the stack).
