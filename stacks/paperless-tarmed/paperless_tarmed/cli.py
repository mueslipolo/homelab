"""Command line interface.

    paperless-tarmed extract FILE.pdf...   offline, no API at all -- the dev loop
    paperless-tarmed fields                what we can extract, and where it would go
    paperless-tarmed pull                  fetch matching documents + PDFs into the cache
    paperless-tarmed report                extract over the cache, print the quality table
    paperless-tarmed apply                 write back; DRY RUN unless --apply is given
    paperless-tarmed serve                 run the webhook
    paperless-tarmed refresh-tariffs       update the tariff table from the official source
"""

from __future__ import annotations

import argparse
import json
import logging
from pathlib import Path
from typing import Any

from . import values as V
from .cache import Cache
from .config import Config, load_dotenv, parse_since
from .models import Extraction
from .normalize import fold
from .pipeline import extract_pdf
from .planning import (
    append_journal,
    encode_writes,
    outcome_marks,
    review_reasons,
    should_tag_for_review,
)
from .planning import current_values as _current_values
from .planning import planned_writes as _planned_writes
from .report import DIFFER, FAIL, TITLE, Row, build_row, render_csv, render_table

log = logging.getLogger("paperless_tarmed")


# --------------------------------------------------------------------- helpers


def _add_filters(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--document-type",
        help="Document type name or numeric id (default: from config).",
    )
    parser.add_argument("--tag", help="Only documents carrying this tag.")
    parser.add_argument("--query", help="Full-text query passed to Paperless.")
    parser.add_argument("--id", type=int, action="append", dest="ids", help="Specific document id (repeatable).")
    parser.add_argument(
        "--since",
        dest="created_after",
        metavar="WHEN",
        help="Only documents created since WHEN: a relative span (2y, 18m, 90d, 6w) "
        "or an ISO date (2025-01-01). Default: PAPERLESS_TARMED_SINCE.",
    )
    parser.add_argument("--limit", type=int, help="Stop after this many documents.")


def _require_mapping(cfg: Config) -> None:
    if unknown := cfg.unknown_field_keys():
        raise SystemExit(
            f"PAPERLESS_TARMED_FIELDS maps unknown value(s): {', '.join(unknown)}.\n"
            f"Available: {', '.join(V.ALL)}"
        )
    if not cfg.fields:
        raise SystemExit(
            "No field mapping configured. Set PAPERLESS_TARMED_FIELDS to say which "
            "extracted values go to which Paperless custom fields, e.g.\n"
            "  PAPERLESS_TARMED_FIELDS=patient=Patient,treatment_date=Treatment date\n"
            "Run `paperless-tarmed fields` to see what is available."
        )


def _resolve_fields(paperless, cfg: Config) -> dict[str, Any]:
    """Resolve each mapped custom field name to its id and type, once."""
    from .paperless import PaperlessError

    resolved = {}
    for key, name in cfg.fields.items():
        try:
            f = paperless.find_field(name)
        except PaperlessError as exc:
            # Point at the config, not the instance: the name came from the
            # mapping, and a stale shell export silently beats .env.
            raise SystemExit(
                f"{exc}\n\n"
                f"That name came from PAPERLESS_TARMED_FIELDS ({key}={name!r}).\n"
                f"Fix the mapping, and check for a stale shell export -- a real\n"
                f"environment variable takes precedence over .env:\n"
                f"  echo \"$PAPERLESS_TARMED_FIELDS\"\n"
                f"  unset PAPERLESS_TARMED_FIELDS   # then rely on .env"
            ) from None
        resolved[key] = {
            "id": f.id,
            "name": f.name,
            "data_type": f.data_type,
            "select_options": f.select_options,
            "select_labels": f.select_labels,
        }
    return resolved


def _currency(result: Extraction, cfg: Config) -> str:
    """The invoice's own currency, falling back to configuration."""
    raw = getattr(result, "raw", None)
    return (raw.currency if raw and raw.currency else None) or cfg.currency


#: CLI flags that name a set of documents. Any of them means the user asked for
#: a specific selection, so it is resolved against Paperless and anything the
#: cache is missing is fetched. With none of them, commands read the cache as-is
#: — which is what keeps the tweak-a-regex-and-re-report loop offline.
_SELECTION_FLAGS = ("document_type", "tag", "query", "ids", "created_after", "limit")


def _wants_online(args: argparse.Namespace) -> bool:
    return any(getattr(args, name, None) for name in _SELECTION_FLAGS)


def _save_fields(cache: Cache, fields: dict[str, Any]) -> None:
    cache.ensure()
    (cache.root / "fields.json").write_text(
        json.dumps(fields, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def _select(
    api, args: argparse.Namespace, cfg: Config, cache: Cache, *, refresh: bool = False
) -> tuple[list[dict], int, int]:
    """Resolve the filters against Paperless, caching whatever is missing.

    `--limit` is applied by the query, not to the cache afterwards, so it always
    means "this many documents matching my filters" rather than "this many of
    whatever happens to be on disk".

    Returns the matching documents plus (downloaded, already-cached) counts.
    """
    raw_since = args.created_after or cfg.since
    since = parse_since(raw_since) if raw_since else None
    if since:
        print(f"  (only documents created on or after {since})")

    dt_name = args.document_type or cfg.document_type
    dt_id = None
    if dt_name and not args.ids:
        dt_id = api.document_type_id(dt_name)
        if dt_id is None:
            raise SystemExit(f"Document type {dt_name!r} not found on this instance.")
    tag_id = api.tag_id(args.tag) if args.tag else None
    if args.tag and tag_id is None:
        raise SystemExit(f"Tag {args.tag!r} not found.")
    if not any((dt_id, tag_id, args.query, args.ids)):
        raise SystemExit(
            "Refusing to select the entire archive. Narrow it with "
            "--document-type / --tag / --query / --id, or set "
            "PAPERLESS_TARMED_DOCUMENT_TYPE.\n"
            "Run `paperless-tarmed fields` to list the document types."
        )

    docs: list[dict] = []
    fetched = cached = 0
    for doc in api.documents(
        document_type_id=dt_id,
        tag_id=tag_id,
        query=args.query,
        ids=args.ids,
        created_after=since,
        limit=args.limit,
    ):
        doc_id = doc["id"]
        cache.save_meta(doc)
        if cache.pdf_path(doc_id).exists() and not refresh:
            cached += 1
        else:
            api.download(doc_id, cache.pdf_path(doc_id))
            fetched += 1
            print(f"  pulled #{doc_id}  {doc.get('title', '')[:70]}")
        docs.append(doc)
    return docs, fetched, cached


def _documents_for(
    api, args: argparse.Namespace, cfg: Config, cache: Cache
) -> tuple[list[dict], dict[str, Any]]:
    """The documents a report/apply run should work on, and the field mapping.

    Online when the user named a selection, cache-only otherwise.
    """
    if _wants_online(args):
        _require_mapping(cfg)
        fields = _resolve_fields(api, cfg)
        _save_fields(cache, fields)
        docs, _, _ = _select(api, args, cfg, cache)
    else:
        fields = _load_fields(cache)
        docs = list(cache.documents(args.ids))
        # Say so loudly: with no selection this is NOT the archive, only what
        # earlier runs happened to download -- easy to mistake for 'everything'.
        if docs:
            hint = f"--document-type '{cfg.document_type}'" if cfg.document_type else "--document-type"
            print(
                f"  (offline: the {len(docs)} document(s) already in the local cache, "
                f"not the whole archive -- add {hint} to select from Paperless)"
            )
    if not docs:
        raise SystemExit(
            f"No documents selected. Nothing cached in {cfg.cache_dir}, and no "
            "--document-type / --tag / --query / --id given."
        )
    return docs, fields


def _load_fields(cache: Cache) -> dict[str, Any]:
    path = cache.root / "fields.json"
    if not path.exists():
        raise SystemExit(f"No {path} -- run `paperless-tarmed pull` first.")
    return json.loads(path.read_text(encoding="utf-8"))


# -------------------------------------------------------------------- commands


def cmd_extract(args: argparse.Namespace, cfg: Config) -> int:
    if args.show_text:
        # Diagnosing a document the regexes miss needs the text they saw.
        from .text import pages_from_pdf

        for path in args.paths:
            pages = pages_from_pdf(path)
            print(f"=== {path}  ({len(pages)} page(s))")
            for n, page in enumerate(pages, 1):
                print(f"--- page {n} " + "-" * 60)
                print(page.strip() or "(no text layer on this page)")
        return 0

    results = [extract_pdf(path, cfg) for path in args.paths]
    if args.json:
        print(json.dumps([r.to_dict() for r in results], ensure_ascii=False, indent=2))
        return 0
    for path, result in zip(args.paths, results, strict=True):
        print(f"=== {path}")
        for key in V.ALL:
            outcome = result.get(key)
            mark = "ok  " if outcome.ok else "    "
            detail = "" if outcome.ok else f"({outcome.reason})"
            print(f"  {mark} {key:<17} {outcome.value if outcome.ok else ''}{detail}")
        raw = result.raw
        if raw is not None:
            from .catalogue import decide

            tariffs = list(dict.fromkeys(li.tariff for li in raw.line_items))
            print(f"       {'service lines':<17} {len(raw.line_items)} (tariff type(s): {tariffs or 'none'})")
            _, why = decide(raw.line_items, cfg.category_overrides)
            print(f"       {'category from':<17} {why or '(nothing matched)'}")
        print(f"  ==>  {'title':<17} {result.title.value or '(none)'}")
        for warning in result.warnings:
            print(f"  !    {warning}")
    return 0 if all(r.core_ok for r in results) else 1


def cmd_fields(args: argparse.Namespace, cfg: Config) -> int:
    print("Values this extractor can produce:")
    for key in V.ALL:
        marker = "*" if key in V.CORE else " "
        print(f"  {marker} {key:<17} {V.DESCRIPTIONS[key]}")
    print("  (* = plausibility-checked; never written when the check fails)\n")

    if unknown := cfg.unknown_field_keys():
        print(f"WARNING: PAPERLESS_TARMED_FIELDS maps unknown value(s): {', '.join(unknown)}\n")

    if not cfg.paperless_url or not cfg.paperless_token:
        print("Set PAPERLESS_URL and PAPERLESS_TOKEN to also list the instance's fields.")
        return 0

    from .paperless import Paperless, PaperlessError

    with Paperless(cfg) as api:
        print("Custom fields on this instance:")
        for f in sorted(api.custom_fields().values(), key=lambda f: f.id):
            # Show the real labels, not the folded lookup keys.
            options = f" options={sorted(f.select_labels.values())}" if f.is_select else ""
            print(f"  #{f.id:<4} {f.name!r} type={f.data_type}{options}")

        print("\nMapping (PAPERLESS_TARMED_FIELDS):")
        if not cfg.fields:
            print("  (empty -- nothing would be written)")
        for key, name in cfg.fields.items():
            try:
                f = api.find_field(name)
                print(f"  {key:<17} -> #{f.id} {f.name!r} ({f.data_type})")
            except PaperlessError as exc:
                print(f"  {key:<17} -> UNRESOLVED: {exc}")

        _check_select_coverage(api, cfg)

        print("\nDocument types (use one with --document-type):")
        configured = api.document_type_id(cfg.document_type) if cfg.document_type else None
        for dt_id, name, count in sorted(api.document_types(), key=lambda r: r[1]):
            marker = " <- PAPERLESS_TARMED_DOCUMENT_TYPE" if dt_id == configured else ""
            print(f"  #{dt_id:<4} {name!r} ({count} document(s)){marker}")
        if cfg.document_type and configured is None:
            print(f"  WARNING: {cfg.document_type!r} matches none of the above")
    return 0


def _check_select_coverage(api, cfg: Config) -> None:
    """Warn when a Select field cannot represent every value we might write.

    A missing option is not silently wrong -- the write is refused and the
    document tagged -- but finding out during a backfill is worse than being
    told now.
    """
    name = cfg.fields.get(V.PATIENT)
    if not (name and cfg.patients):
        return
    from .paperless import PaperlessError

    try:
        field = api.find_field(name)
    except PaperlessError:
        return
    if not field.is_select:
        return

    missing = [p for p in cfg.patients if fold(p) not in field.select_options]
    if missing:
        print(
            f"\nWARNING: select field {field.name!r} has no option for "
            f"{', '.join(repr(m) for m in missing)}.\n"
            f"         Options are: {', '.join(sorted(field.select_labels.values()))}\n"
            "         Documents for those patients will be tagged instead of written.\n"
            "         Add the options in Paperless, or use a Text field."
        )


def cmd_pull(args: argparse.Namespace, cfg: Config) -> int:
    from .paperless import Paperless

    _require_mapping(cfg)
    cache = Cache(cfg.cache_dir)
    cache.ensure()
    with Paperless(cfg) as api:
        _save_fields(cache, _resolve_fields(api, cfg))
        _, fetched, cached = _select(api, args, cfg, cache, refresh=args.refresh)
    print(f"\n{fetched} downloaded, {cached} already cached -> {cfg.cache_dir}")
    return 0


def cmd_report(args: argparse.Namespace, cfg: Config) -> int:
    cache = Cache(cfg.cache_dir)
    if _wants_online(args):
        from .paperless import Paperless

        with Paperless(cfg) as api:
            docs, fields = _documents_for(api, args, cfg, cache)
    else:
        docs, fields = _documents_for(None, args, cfg, cache)
    keys = [k for k in V.ALL if k in fields]

    rows: list[Row] = []
    results = []
    for doc in docs:
        result = extract_pdf(cache.pdf_path(doc["id"]), cfg, doc_id=doc["id"])
        results.append(result)
        rows.append(build_row(doc["id"], _current_values(doc, fields), result, keys))

    if args.only:
        wanted = {s.strip() for s in args.only.split(",")}
        rows = [r for r in rows if wanted & set(r.status.values())]

    output = (
        render_csv(rows, keys, cfg.paperless_url)
        if args.csv
        else render_table(rows, keys, cfg.paperless_url)
    )
    if args.out:
        Path(args.out).write_text(output, encoding="utf-8")
        print(f"wrote {args.out} ({len(rows)} rows)")
    else:
        print(output, end="")
        if not args.csv:
            from .catalogue import render_summary

            print(render_summary(results), end="")
    return 0


def _write_document(
    api, journal_path: Path, doc_id: int, encoded: dict, title: str | None, title_from: Any
) -> str | None:
    """Write one document's planned changes; return an error message, or None.

    Journals exactly what landed, so a field update followed by a failed title
    change is still on record.
    """
    from .paperless import PaperlessError

    done_fields: dict = {}
    done_title = None
    error = None
    try:
        if encoded:
            api.set_custom_fields(doc_id, encoded)
            done_fields = encoded
        if title:
            api.patch_document(doc_id, title=title)
            done_title = title
    except PaperlessError as exc:
        error = str(exc)
    if done_fields or done_title:
        append_journal(
            journal_path,
            doc_id=doc_id,
            fields=done_fields,
            title_from=title_from,
            title_to=done_title,
            source="cli",
        )
    return error


def cmd_apply(args: argparse.Namespace, cfg: Config) -> int:
    from .paperless import Paperless

    cache = Cache(cfg.cache_dir)
    journal_path = Path(args.journal or (cfg.cache_dir / "journal.jsonl"))
    changed = tagged = done = untouched = 0
    failed: list[tuple[int, str]] = []

    with Paperless(cfg) as api:
        docs, fields = _documents_for(api, args, cfg, cache)
        keys = [k for k in V.ALL if k in fields]
        field_objs = {key: api.find_field(spec["name"]) for key, spec in fields.items()}
        # In a dry run a tag that does not exist yet is not created; -1 stands
        # in for it so the run still shows what would be tagged.
        review_tag = done_tag = None
        if args.tag_failures and cfg.review_tag:
            review_tag = api.tag_id(cfg.review_tag, create=args.apply) or -1
        if args.tag_failures and cfg.done_tag:
            done_tag = api.tag_id(cfg.done_tag, create=args.apply) or -1
        type_id = api.document_type_id(cfg.document_type) if cfg.document_type else None

        for doc in docs:
            doc_id = doc["id"]
            result = extract_pdf(cache.pdf_path(doc_id), cfg, doc_id=doc_id)
            row = build_row(doc_id, _current_values(doc, fields), result, keys)
            writes, title = _planned_writes(
                row, result, fields, overwrite=args.overwrite, do_title=args.title
            )

            encoded, problems = encode_writes(
                writes,
                {key: field_objs[key] for key in keys},
                _currency(result, cfg),
                encode=api.encode_value,
            )

            if not encoded and not title:
                untouched += 1
                if args.verbose:
                    print(f"  #{doc_id} nothing to do ({row.worst})")
            else:
                summary = ", ".join(
                    f"{k}={result.value(k)}" for k in keys if fields[k]["id"] in encoded
                )
                print(f"  #{doc_id} {'WRITE' if args.apply else 'would write'}: {summary or '(no fields)'}")
                if title:
                    print(f"        title: {row.current.get(TITLE)!r} -> {title!r}")
                error = None
                if args.apply:
                    error = _write_document(
                        api, journal_path, doc_id, encoded, title, row.current.get(TITLE)
                    )
                if error:
                    # One stuck document must not end a run over the archive.
                    print(f"        ! write failed: {error}")
                    failed.append((doc_id, error))
                else:
                    changed += 1

            for problem in problems:
                print(f"        ! {problem}")
            failed_review = should_tag_for_review(result, problems, doc, type_id)
            tag, note = outcome_marks(
                doc,
                failed=failed_review,
                found=result.found,
                reasons=review_reasons(result, problems),
                written=[f"{k}={result.value(k)}" for k in keys if fields[k]["id"] in encoded],
                title=title,
                review_tag=review_tag if failed_review else None,
                done_tag=done_tag,
            )
            if tag is not None:
                name = cfg.review_tag if failed_review else cfg.done_tag
                if failed_review:
                    print(f"        tag {name!r}: {review_reasons(result, problems)[0]}")
                elif args.verbose:
                    print(f"        tag {name!r}")
                if args.apply:
                    from .paperless import PaperlessError

                    try:
                        api.add_tag(doc_id, tag)
                    except PaperlessError as exc:
                        print(f"        ! tagging failed: {exc}")
                        failed.append((doc_id, str(exc)))
                        continue
                if failed_review:
                    tagged += 1
                else:
                    done += 1
            if note and args.apply:
                from .paperless import PaperlessError

                try:
                    api.add_note(doc_id, note)
                except PaperlessError as exc:
                    print(f"        ! note not added: {exc}")

    print(f"\n{changed} document(s) {'written' if args.apply else 'pending'}, "
          f"{tagged} tagged for review, {done} newly tagged {cfg.done_tag!r}, {untouched} unchanged")
    if failed:
        print(f"{len(failed)} write(s) FAILED -- re-run the same command to retry them:")
        for doc_id, error in failed:
            print(f"  #{doc_id}: {error}")
    if not args.apply:
        print("DRY RUN -- nothing was modified. Re-run with --apply to write.")
    else:
        print(f"journal: {journal_path}")
    return 1 if failed else 0


def cmd_serve(args: argparse.Namespace, cfg: Config) -> int:
    import uvicorn

    _require_mapping(cfg)
    uvicorn.run(
        "paperless_tarmed.app:app", host=args.host, port=args.port, log_level=args.log_level
    )
    return 0


def cmd_refresh_tariffs(args: argparse.Namespace, cfg: Config) -> int:
    from .refresh import main as refresh_main

    return refresh_main(["--dry-run"] if args.dry_run else [])


# ------------------------------------------------------------------------ main


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="paperless-tarmed",
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("-v", "--verbose", action="store_true")
    parser.add_argument(
        "--env-file",
        default=".env",
        help="Read settings from this file if it exists (default: .env). "
        "Real environment variables take precedence.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("extract", help="Extract from local PDFs. No API access.")
    p.add_argument("paths", nargs="+")
    p.add_argument("--json", action="store_true", help="Machine-readable output.")
    p.add_argument(
        "--show-text",
        action="store_true",
        help="Print the text layer instead of extracting. For diagnosing misses.",
    )
    p.set_defaults(func=cmd_extract)

    p = sub.add_parser("fields", help="Show extractable values and the configured mapping.")
    p.set_defaults(func=cmd_fields)

    p = sub.add_parser("pull", help="Fetch documents and PDFs into the local cache.")
    _add_filters(p)
    p.add_argument("--refresh", action="store_true", help="Re-download already-cached PDFs.")
    p.set_defaults(func=cmd_pull)

    p = sub.add_parser(
        "report",
        help="Quality report. Fetches what a named selection needs; offline with no filters.",
    )
    _add_filters(p)
    p.add_argument("--csv", action="store_true")
    p.add_argument("--out", help="Write to this file instead of stdout.")
    p.add_argument("--only", help=f"Keep rows having any of these verdicts, e.g. '{FAIL},{DIFFER}'.")
    p.set_defaults(func=cmd_report)

    p = sub.add_parser("apply", help="Write results back. Dry run unless --apply.")
    _add_filters(p)
    p.add_argument("--apply", action="store_true", help="Actually write. Off by default.")
    p.add_argument("--overwrite", action="store_true",
                   help="Also replace custom fields that already hold a different value.")
    p.add_argument("--title", action="store_true", help="Also rewrite document titles.")
    p.add_argument("--no-tags", "--no-tag-failures", dest="tag_failures", action="store_false",
                   help="Add neither the review tag nor the done tag (nor a failure note).")
    p.add_argument("--journal", help="Path for the JSONL change journal.")
    p.set_defaults(func=cmd_apply, tag_failures=True)

    p = sub.add_parser("serve", help="Run the webhook receiver.")
    p.add_argument("--host", default="0.0.0.0")
    p.add_argument("--port", type=int, default=8000)
    p.add_argument("--log-level", default="info")
    p.set_defaults(func=cmd_serve)

    p = sub.add_parser("refresh-tariffs", help="Update the tariff table from forum-datenaustausch.ch.")
    p.add_argument("--dry-run", action="store_true", help="Report changes without writing.")
    p.set_defaults(func=cmd_refresh_tariffs)

    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(levelname)s %(name)s: %(message)s",
    )
    if loaded := load_dotenv(args.env_file):
        log.debug("loaded %d setting(s) from %s: %s", len(loaded), args.env_file, ", ".join(loaded))
    return args.func(args, Config.from_env())


if __name__ == "__main__":
    raise SystemExit(main())
