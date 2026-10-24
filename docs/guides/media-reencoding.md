# Media re-encoding (saves disk space)

> **Needs:** [06 mediarr](06-mediarr.md) (the library), a desktop with an Intel GPU · **Reference:** the scripts in [`stacks/mediarr/`](../../stacks/mediarr/): `reencode.sh`, `reencode-compare.sh`, `reencode-presets.sh`

## What you get

Large H.264 (or older) files re-encoded to HEVC in place. It runs on a desktop
with an Intel GPU and the `media` share mounted, not on the NAS.

**Presets**, by type of material (numbers in `reencode-presets.sh`):

| Preset   | For                                     | Typical result, 1080p   |
| -------- | --------------------------------------- | ----------------------- |
| `sitcom` | sitcoms, old TV, animation, talk shows  | ~1.5–3 Mbit/s, smallest |
| `series` | modern drama series                     | ~3–5 Mbit/s             |
| `film`   | films, cinematic, dark or grainy series | ~5–8 Mbit/s, best       |

Each preset leaves alone files that are already small enough for it.

Measured on a Core Ultra 5 238V: a 23-minute 1080p episode with `sitcom`
takes 2 minutes, 2.4 GB → 381 MB, with no visible loss on old TV material.

## Before you start

| What                             | How                                                                                                                                                                                               |
| -------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| ffmpeg with VA-API               | `vainfo` lists HEVC encode profiles. Optional: libvmaf for quality scores.                                                                                                                        |
| The share, kernel SMB client     | **Not** the file manager's `smb://` (GVFS): it dropped long reads of large files ("Read error at pos …"). Line below, needs `cifs-utils`.                                                         |
| Intel Lunar Lake on Ubuntu 24.04 | The stock driver predates the chip and `vainfo` fails. Use Intel's packages (`ppa:kobuk-team/intel-graphics`, `intel-media-va-driver-non-free`) and `LIBVA_DRIVER_NAME=iHD` (the scripts set it). |

`/etc/fstab`, mounted on first access, as your user:

```
//192.0.2.10/media /mnt/media cifs credentials=/home/<you>/.smb-nas,uid=1000,gid=1000,vers=3.0,iocharset=utf8,noauto,x-systemd.automount,x-systemd.idle-timeout=10min,_netdev 0 0
```

## Steps

### 1. See what would change

```bash
reencode.sh -n sitcom /mnt/media/media/tv          # what would change, per series
```

### 2. Not sure which preset? Compare

```bash
reencode-compare.sh "/mnt/media/media/tv/<series>/<episode>.mkv"
```

It encodes the same excerpt with each preset into
`~/reencode/compare/<episode>/`:

- `summary`: estimated episode size and saving, and VMAF if ffmpeg has libvmaf
- `split-<preset>.mkv`: original left, re-encode right
- `frames/`: matching still frames

Pick the smallest preset you can't tell from the original.

### 3. Convert

```bash
reencode.sh sitcom "/mnt/media/media/tv/<series>"
```

- HEVC and AV1 files are skipped.
- Every audio, subtitle and attachment stream is kept.
- A file is replaced only after the new one checks out: same duration, same
  streams, at least 10 % smaller. An `.mp4`/`.avi` becomes `.mkv`.

**Check:** the series folder shrank, and an episode plays in Jellyfin.

### 4. Tell Sonarr/Radarr

Refresh & Scan the series or film, then unmonitor it or give it a quality
profile without upgrades. Otherwise they may download the large release again.

## Troubleshooting

| Symptom                              | Cause → fix                                                                                                         |
| ------------------------------------ | ------------------------------------------------------------------------------------------------------------------- |
| `Read error at pos …` on large files | The share is mounted through GVFS (`smb://`). Use the kernel CIFS mount above.                                      |
| `vainfo` fails                       | Driver too old for the GPU; see Before you start.                                                                   |
| 3D picture (two images side by side) | `3D`/`SBS`/`HOU` release; re-encoding keeps it 3D. Replace it, and block 3D in Sonarr/Radarr ([06](06-mediarr.md)). |

## Next

- [09 dashboard](09-dashboard.md): the storage tab shows the media library over time.
