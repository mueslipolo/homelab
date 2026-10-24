# Installing paperless-tarmed

> **Needs:** [07 Paperless-ngx](../../docs/guides/07-paperless.md), [10 Backups and jobs](../../docs/guides/10-backups-and-jobs.md) (the scheduler builds the image) · **Reference:** [README.md](README.md)

First-time setup on a NAS with Paperless-ngx, Portainer and this repository on
Forgejo. Reference, CLI and troubleshooting: [README.md](README.md). Run commands
over SSH on the NAS.

## 1. Prerequisites

```bash
uname -m                          # x86_64 is tested; others need wheels for every dependency
docker network ls | grep proxy    # the shared network Paperless's webserver is on
mkdir -p <nas-volume-path>/docker/paperless-tarmed/data
chown -R 1000:1000 <nas-volume-path>/docker/paperless-tarmed/data
```

The `chown` is required (the container runs as uid 1000). The scheduler
([guide 10](../../docs/guides/10-backups-and-jobs.md)) must already run: its
`build-images` job builds this image.

**Check:** `ls -ln <nas-volume-path>/docker/paperless-tarmed` shows `data` owned by `1000 1000`.

## 2. Prepare Paperless

1. **Custom fields** (Settings → Custom fields). Create the ones you want filled,
   named as you like:

   | Value                      | Field type     |
   | -------------------------- | -------------- |
   | `patient`                  | Text or Select |
   | `treatment_date`           | Date           |
   | `total`, `total_unrounded` | Monetary       |
   | `category`                 | Text or Select |
   | `provider`, `provider_gln` | Text           |

   `patient`, `treatment_date`, `total` are a good minimum. A Select field needs
   an option for every value written.

2. **Document type** `Justificatif de remboursement`, assigned by your own
   matching rule; this service only reads it.
3. **API token** for a dedicated user that can view and change documents, view
   custom fields and document types, view and add tags, and add notes
   (Settings → Users; then that user's profile → API token).
4. **Webhook secret**: `openssl rand -hex 24`. You need it in steps 3 and 8.

**Check:** the fields and the document type appear in Paperless's settings.

## 3. Deploy the stack in Portainer

Stacks → Add stack → Repository:

| Field          | Value                                                               |
| -------------- | ------------------------------------------------------------------- |
| Name           | `paperless-tarmed`                                                  |
| Repository URL | `http://<nas-ip>:3000/<owner>/homelab.git` (raw IP, not a hostname) |
| Reference      | `refs/heads/main`                                                   |
| Compose path   | `stacks/paperless-tarmed/compose.yaml`                              |
| Authentication | Forgejo user + token                                                |

Environment variables (all of them, with comments, in [.env.example](.env.example)):

| Variable                                                             | Value                                                                                                                                                  |
| -------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------ |
| `PAPERLESS_URL`                                                      | `http://webserver:8000` (Paperless's service name on `proxy`)                                                                                          |
| `PAPERLESS_TOKEN`                                                    | token from step 2.3                                                                                                                                    |
| `PAPERLESS_TARMED_WEBHOOK_SECRET`                                    | secret from step 2.4                                                                                                                                   |
| `PAPERLESS_TARMED_FIELDS`                                            | e.g. `patient=Concerné,treatment_date=Date de traitement,total=Montant`                                                                                |
| `PAPERLESS_TARMED_DOCUMENT_TYPE`                                     | `Justificatif de remboursement`                                                                                                                        |
| `PAPERLESS_TARMED_DATA`                                              | `<nas-volume-path>/docker/paperless-tarmed/data`                                                                                                       |
| `PAPERLESS_TARMED_PATIENTS`                                          | optional allow-list, e.g. `Anna,Bruno`                                                                                                                 |
| `PAPERLESS_TARMED_NTFY_URL`, `_TOKEN`, `PAPERLESS_TARMED_PUBLIC_URL` | optional push per bill: `http://ntfy:8080`, a publish token, `https://paperless.<internal-domain>` ([guide 11](../../docs/guides/11-notifications.md)) |

Do not deploy yet if the image does not exist (step 4).

## 4. Build the image

The `build-images` job builds `paperless-tarmed:0.1.0` within 15 minutes. To
build now, as root:

```bash
cd <nas-volume-path>/docker/homelab-ops
scripts/build-images.sh paperless-tarmed=paperless-tarmed:0.1.0
```

(or `docker build -t paperless-tarmed:0.1.0 stacks/paperless-tarmed`; the job
then rebuilds once, for its label). Then **Deploy the stack**.

**Check:**

```bash
docker image ls paperless-tarmed
docker logs paperless-tarmed | tail
docker exec paperless-tarmed python -c \
  "import urllib.request;print(urllib.request.urlopen('http://127.0.0.1:8000/health').read())"
```

Expect `{"status":"ok"}`.

## 5. Verify the connection and mapping

```bash
docker exec paperless-tarmed paperless-tarmed fields
```

**Check:** no `UNRESOLVED` mapping, and the document type is marked
`<- PAPERLESS_TARMED_DOCUMENT_TYPE`.

## 6. Audit (writes nothing)

```bash
T='Justificatif de remboursement'
docker exec paperless-tarmed paperless-tarmed report --document-type "$T" --limit 10
docker exec paperless-tarmed paperless-tarmed report --document-type "$T"
docker exec paperless-tarmed paperless-tarmed report --only fail,differ
```

The first two download into `/data/cache`; the third reads that cache offline.
Verdicts: README, CLI.

**Check:** every `differ` and `fail` is understood before anything is written.

## 7. Backfill

Dry run unless `--apply`:

```bash
T='Justificatif de remboursement'
docker exec paperless-tarmed paperless-tarmed apply --document-type "$T" --limit 10
docker exec paperless-tarmed paperless-tarmed apply --document-type "$T" --limit 10 --apply
# check those ten in the Paperless UI, then:
docker exec paperless-tarmed paperless-tarmed apply --document-type "$T" --apply
docker exec paperless-tarmed paperless-tarmed apply --document-type "$T" --title --apply   # optional
```

Keep `--document-type`: without a selection flag `apply` covers only the cache.

**Check:** no `FAILED` in the summary (else re-run the same command); look at
documents tagged `extraction-failed`.

## 8. Enable the Paperless workflow

Settings → Workflows → Add:

- **Trigger**: Document Added; filter: document type = `Justificatif de remboursement`
- **Action**: Webhook
  - URL: `http://paperless-tarmed:8000/hook`
  - Use parameters: **off**
  - Send as JSON: **on**
  - Body: `{"doc_id": {{doc_id}}}`
  - Headers: `X-Paperless-Tarmed-Secret: <secret from step 2.4>`

Never Document Updated (README, Gotchas). By default the webhook fills blank
fields only and leaves titles alone.

**Check:** consume one justificatif; `docker logs -f paperless-tarmed` shows
`doc <id>: wrote N field(s)`. Or replay one (writes): `stacks/paperless-tarmed/scripts/fire-hook.sh <id>`.
