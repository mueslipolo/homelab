# Slow disk writes and "database is locked"

> **Tool:** [`tools/disk-diag.sh`](../tools/disk-diag.sh) · **Related:** [operations, "Container logs"](operations.md#container-logs), [authelia](../stacks/authelia/README.md)

On 2026-10-24, Authelia on SQLite failed with `database is locked` while one
person was using it. The cause was not SQLite and not load: Container
Manager's default log driver kept the RAID busy with small forced writes, so
a single commit could take seconds. Every container now logs with Docker's
`local` driver, forced writes take 0.05–0.11 s instead of up to 1.3 s, and
Authelia stays on SQLite.

This page records how that was found, so the next "SQLite is too small for
this" or "the NAS feels slow" starts from measurements.

## The symptom

```text
level=error msg="Error occurred looking up the user preferred second factor method while loading the user information"
  error="error selecting preferred two factor method for user '<user>': database is locked"
  method=POST path=/api/user/info
```

Authelia 4.39.28 opens its SQLite file with `?_txlock=immediate`
(`internal/storage/const.go`): every transaction takes the write lock when it
starts, and another one waits. Authelia sets no wait of its own, so the
driver's default applies, believed to be 5 seconds (not checked in the
driver's source). So the error means a transaction held the file for
seconds, and with one user that is a stall, not contention.

Eleven seconds earlier, the same log had a burst of `Request timeout occurred
while handling request from client ... status_code=408` lines from Caddy's
address. Whether they are related was not established.

## What was measured

Each step ran on the NAS; [`tools/disk-diag.sh`](../tools/disk-diag.sh) now
does all of them in one pass.

| Step | Result                                                                                                                                             | Conclusion                                      |
| ---- | -------------------------------------------------------------------------------------------------------------------------------------------------- | ----------------------------------------------- |
| 1    | One Authelia container; `db.sqlite3` new that morning; one error in the log; no scheduler job running at that time                                 | not a second writer, not an old file, not a job |
| 2    | 4 KB write forced to disk (`dd … conv=fsync`) in Authelia's data folder, 20 times: 0.10–1.26 s, 5 of them over 0.8 s. In memory: 0.00 s            | the disk is slow, not the CPU or SQLite         |
| 3    | 4 × 2 TB drives at 5400–5900 rpm, write cache on, RAID 6, no resync running, last scrub clean                                                      | nothing broken or misconfigured in the array    |
| 4    | The RAID volume, with nobody using the NAS: 212 writes/s, 90 % busy. The top writer: `dockerd`, 11.1 MB in 10 s                                    | something in Docker writes all the time         |
| 5    | Log driver: `db` (Synology's). At least nine `<docker root>/containers/<id>/log.db` files changed within a minute; the busiest grew 32 KB in 30 s  | a few KB of log text become hundreds of writes  |
| 6    | 349 btrfs "snapshots" and 392 qgroups on the volume: 327 of the snapshots are Docker's image layers (btrfs storage driver), 24 are share snapshots | not the share snapshots; Docker's layers add up |

Step 5 is the cause. The `db` driver stores each container's output in its
own SQLite file and commits every line. A commit forces the journal, the
page and the journal's removal to disk, and on RAID 6 every small write
reads and rewrites two parity blocks. A few kilobytes of log text a minute
became a steady stream of small forced writes, and Authelia's commits queued
behind them.

## The fix

Every service in every stack logs with Docker's `local` driver: its own
compact format, buffered, rotated at 3 × 10 MB. Each compose file starts with
the block and every service uses it:

```yaml
x-logging: &logging
  driver: local
  options:
    max-size: 10m
    max-file: "3"

services:
  app:
    logging: *logging
```

Portainer, started with `docker run`, gets the same through
`--log-driver local --log-opt max-size=10m --log-opt max-file=3`
([guide 02](guides/02-portainer.md)).

The setting only applies to a container created after it, so every stack
had to be redeployed and Portainer recreated. Check that nothing is left on
`db`:

```bash
sudo docker ps --format '{{.Names}}' | while read -r c; do
  echo "$c $(sudo docker inspect -f '{{.HostConfig.LogConfig.Type}}' "$c")"
done
```

The trade-off: DSM's Container Manager no longer shows these containers'
logs. `docker logs` and Portainer do.

## The result

Same measurements, NAS idle:

|                       | Before      | All stacks on `local` | Portainer too   |
| --------------------- | ----------- | --------------------- | --------------- |
| 4 KB forced write     | 0.10–1.26 s | 0.05–0.74 s           | **0.05–0.11 s** |
| RAID volume writes/s  | 212         | 62                    | 101             |
| RAID volume busy      | 90 %        | 27 %                  | 41 %            |
| `dockerd` writes/10 s | 11.1 MB     | 4.6 MB                | 3.5 MB          |

Writes per second vary between runs; the forced-write time is what an
application waits for, and its worst case fell from 1.26 s to 0.11 s. Authelia
fails only after a wait of seconds, so SQLite holds. Mealie moved to SQLite
the same day.

## Still open

- **`dockerd` still writes about 3.5 MB per 10 s.** The files changed in a
  minute were mostly the containers' `config.v2.json`, which Docker rewrites
  as healthchecks run. An estimate puts the healthchecks at 2–3 writes/s;
  that was not measured.
- **About 0.05–0.1 s per forced write** is probably the floor of btrfs on
  RAID 6 with 5400 rpm drives. Not measured on an idle volume. Going lower
  takes SSDs (the DS920+ has two M.2 slots).
- **The 408 lines in Authelia's log**: cause not established.

## Checking again

On the NAS, from the scheduler's clone:

```bash
sudo bash <clone>/tools/disk-diag.sh /volume1 /volume1/docker/authelia/data
```

- **Forced writes over 0.3 s, or growing:** look at the volume's writes/s
  and % busy, and at who wrote the most. A container on `db`, a scrub or
  RAID check, or a busy download show up there.
- **The memory line** is the cost of measuring (starting `dd`), about
  0.01–0.02 s; subtract it from the disk line.
- **`database is locked` again:** note its time, run the tool, and compare
  with the tables above. If the disk can't be made quiet, Authelia can go back
  to Postgres ([authelia README](../stacks/authelia/README.md), "Postgres
  instead of SQLite"), which waits instead of failing a request.
