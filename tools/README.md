# tools

Run from the laptop, not the NAS, against the NAS shares mounted with cifs
(mount line: [media re-encoding](../docs/guides/media-reencoding.md)).

| Script                          | Does                                                                                                     |
| ------------------------------- | -------------------------------------------------------------------------------------------------------- |
| `duplicates.sh`                 | finds identical files inside one folder and deletes the extra copies, keeping one each                   |
| `dashboard-demo/screenshots.sh` | regenerates `docs/images/dashboard-*.png`: the dashboard with made-up data, in Playwright's Docker image |

## duplicates.sh

```bash
tools/duplicates.sh "/mnt/homes/<…>/<user>-<…>/Photos"        # shows what it would delete
tools/duplicates.sh -a "/mnt/homes/<…>/<user>-<…>/Photos"     # deletes
```

- One folder at a time, e.g. one person's `Photos`: files elsewhere are never compared.
- Identical means identical content: same size, same first and last bytes, and the same
  BLAKE3 hash of the whole file. Names, dates and folders don't matter; a copy with
  different EXIF data (e.g. an edited photo) is a different file and stays.
- Keeps the most deeply nested copy: the phone backup
  (`MobileBackup/…/DCIM/Camera/2021/10/x.mp4`) rather than a Google Takeout copy
  (`Photos from 2021/x.mp4`). `-k '<pattern>'` protects more paths.
- Skips DSM's `#snapshot`, `#recycle` and `@eaDir` folders, empty files, and `.json` files
  (Google Takeout's per-photo metadata: identical ones can belong to different photos).
  `-m <size>` ignores smaller files.
- Installs a pinned, checksum-verified fclones into `~/.local/share/homelab/` on first run.
  Reports and plans go to `~/duplicates/`.
- **Space comes back** only once the share's recycle bin is emptied and the snapshots
  that still hold the files have expired (`homes`: 14 days, immutable).
- Google Takeout exports keep arriving (every 2 months): run it again after extracting one.
