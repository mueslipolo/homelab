"""Tests for how report/apply decide which documents to work on.

Covers the fix for two related irritations: `apply` used to refuse to run until
`pull` had been run separately, and its `--limit` sliced the *cache* after
loading, so `--limit 10` meant "ten of whatever is on disk" rather than "ten
documents matching my filters".

All data here is invented.
"""

from __future__ import annotations

import argparse
import json

import pytest

from paperless_tarmed.cache import Cache
from paperless_tarmed.cli import _documents_for, _select, _wants_online
from paperless_tarmed.config import Config

FIELDS = {"patient": {"id": 1, "name": "Concerné", "data_type": "text"}}


def _args(**kw):
    base = dict(
        document_type=None, tag=None, query=None, ids=None, created_after=None, limit=None
    )
    base.update(kw)
    return argparse.Namespace(**base)


class FakeApi:
    """Just enough Paperless to exercise selection."""

    def __init__(self, docs):
        self._docs = docs
        self.downloaded: list[int] = []
        self.queried_with: dict | None = None

    def document_type_id(self, name):
        return 7 if name else None

    def tag_id(self, name, *, create=False):
        return 9 if name else None

    def find_field(self, name):
        raise AssertionError("not needed for these tests")

    def documents(self, **kw):
        self.queried_with = kw
        docs = self._docs
        if kw.get("limit"):
            docs = docs[: kw["limit"]]
        return iter(docs)

    def download(self, doc_id, dest):
        self.downloaded.append(doc_id)
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(b"%PDF-1.4 fake\n")


# ---------------------------------------------------------------- _wants_online


@pytest.mark.parametrize(
    "kw",
    [
        {"document_type": "Justificatif"},
        {"tag": "todo"},
        {"query": "remboursement"},
        {"ids": [1]},
        {"created_after": "18m"},
        {"limit": 10},
    ],
)
def test_any_selection_flag_goes_online(kw):
    assert _wants_online(_args(**kw)) is True


def test_no_flags_stays_offline():
    """The tweak-a-regex-and-re-report loop must not need the network."""
    assert _wants_online(_args()) is False


def test_configured_document_type_alone_does_not_force_online():
    """PAPERLESS_TARMED_DOCUMENT_TYPE is set in every real deployment; it must
    not turn a bare `report` into a network call."""
    assert _wants_online(_args()) is False


# ---------------------------------------------------------------------- _select


def test_limit_is_applied_by_the_query_not_to_the_cache(tmp_path):
    """The actual bug: with 50 documents cached, `--limit 3` used to hand back
    three arbitrary cached ones instead of asking Paperless for three."""
    cache = Cache(tmp_path)
    for stale in (901, 902, 903, 904):
        cache.save_meta({"id": stale, "title": f"already cached {stale}"})
        cache.pdf_path(stale).write_bytes(b"%PDF-1.4 stale\n")

    api = FakeApi([{"id": i, "title": f"doc {i}"} for i in (11, 12, 13, 14, 15)])
    cfg = Config(cache_dir=tmp_path, fields={"patient": "Concerné"})

    docs, fetched, cached = _select(api, _args(document_type="X", limit=3), cfg, cache)

    assert api.queried_with["limit"] == 3
    assert [d["id"] for d in docs] == [11, 12, 13]
    assert fetched == 3 and cached == 0
    assert 901 not in [d["id"] for d in docs]


def test_missing_pdfs_are_downloaded_so_pull_is_not_required(tmp_path):
    cache = Cache(tmp_path)
    api = FakeApi([{"id": 21, "title": "a"}, {"id": 22, "title": "b"}])
    cfg = Config(cache_dir=tmp_path, fields={"patient": "Concerné"})

    docs, fetched, cached = _select(api, _args(ids=[21, 22]), cfg, cache)

    assert api.downloaded == [21, 22]
    assert fetched == 2 and cached == 0
    assert all(cache.pdf_path(d["id"]).exists() for d in docs)


def test_already_cached_pdfs_are_not_redownloaded(tmp_path):
    cache = Cache(tmp_path)
    cache.save_meta({"id": 31, "title": "cached"})
    cache.pdf_path(31).write_bytes(b"%PDF-1.4 cached\n")
    api = FakeApi([{"id": 31, "title": "cached"}, {"id": 32, "title": "new"}])
    cfg = Config(cache_dir=tmp_path, fields={"patient": "Concerné"})

    _, fetched, cached = _select(api, _args(ids=[31, 32]), cfg, cache)

    assert api.downloaded == [32]
    assert (fetched, cached) == (1, 1)


def test_refresh_redownloads_everything(tmp_path):
    cache = Cache(tmp_path)
    cache.save_meta({"id": 41, "title": "cached"})
    cache.pdf_path(41).write_bytes(b"%PDF-1.4 cached\n")
    api = FakeApi([{"id": 41, "title": "cached"}])
    cfg = Config(cache_dir=tmp_path, fields={"patient": "Concerné"})

    _, fetched, cached = _select(api, _args(ids=[41]), cfg, cache, refresh=True)

    assert api.downloaded == [41]
    assert (fetched, cached) == (1, 0)


def test_unfiltered_selection_is_refused(tmp_path):
    """Never walk the whole archive by accident."""
    cache = Cache(tmp_path)
    api = FakeApi([])
    cfg = Config(cache_dir=tmp_path, fields={"patient": "Concerné"})

    with pytest.raises(SystemExit) as exc:
        _select(api, _args(limit=5), cfg, cache)
    assert "Refusing to select the entire archive" in str(exc.value)


def test_missing_document_type_names_itself(tmp_path):
    cache = Cache(tmp_path)
    cfg = Config(cache_dir=tmp_path, fields={"patient": "Concerné"})

    class NoTypes(FakeApi):
        def document_type_id(self, name):
            return None

    with pytest.raises(SystemExit) as exc:
        _select(NoTypes([]), _args(document_type="Nope"), cfg, cache)
    assert "'Nope' not found" in str(exc.value)


# -------------------------------------------------------------- _documents_for


def test_offline_path_reads_the_cache_without_an_api(tmp_path):
    cache = Cache(tmp_path)
    (tmp_path / "fields.json").write_text(json.dumps(FIELDS), encoding="utf-8")
    cache.save_meta({"id": 51, "title": "cached"})
    cache.pdf_path(51).write_bytes(b"%PDF-1.4\n")
    cfg = Config(cache_dir=tmp_path, fields={"patient": "Concerné"})

    docs, fields = _documents_for(None, _args(), cfg, cache)

    assert [d["id"] for d in docs] == [51]
    assert fields == FIELDS


def test_offline_path_says_it_is_not_the_whole_archive(tmp_path, capsys):
    """Run with no selection, apply worked on the 19 cached documents while the
    user expected all 659 -- and said nothing to suggest otherwise."""
    cache = Cache(tmp_path)
    (tmp_path / "fields.json").write_text(json.dumps(FIELDS), encoding="utf-8")
    cache.save_meta({"id": 51, "title": "cached"})
    cache.pdf_path(51).write_bytes(b"%PDF-1.4\n")
    cfg = Config(cache_dir=tmp_path, fields={"patient": "Concerné"},
                 document_type="Justificatif de remboursement")

    _documents_for(None, _args(), cfg, cache)

    out = capsys.readouterr().out
    assert "not the whole archive" in out
    assert "--document-type 'Justificatif de remboursement'" in out


def test_empty_selection_explains_both_causes(tmp_path):
    cache = Cache(tmp_path)
    (tmp_path / "fields.json").write_text(json.dumps(FIELDS), encoding="utf-8")
    cfg = Config(cache_dir=tmp_path, fields={"patient": "Concerné"})

    with pytest.raises(SystemExit) as exc:
        _documents_for(None, _args(), cfg, cache)
    assert "No documents selected" in str(exc.value)


# ------------------------------------------------------- flags actually exist

def test_report_and_apply_accept_every_selection_flag():
    """The selection plumbing is useless if the flags cannot be typed.

    `report --limit 10` failed with "unrecognized arguments" once, because
    _add_filters was wired to `pull` only.
    """
    from paperless_tarmed.cli import build_parser

    parser = build_parser()
    for command in ("pull", "report", "apply"):
        ns = parser.parse_args([
            command,
            "--document-type", "Justificatif de remboursement",
            "--tag", "todo",
            "--query", "remboursement",
            "--id", "5",
            "--since", "2y",
            "--limit", "10",
        ])
        assert ns.document_type == "Justificatif de remboursement", command
        assert ns.tag == "todo", command
        assert ns.query == "remboursement", command
        assert ns.ids == [5], command
        assert ns.created_after == "2y", command
        assert ns.limit == 10, command
        assert _wants_online(ns) is True, command


def test_bare_report_and_apply_stay_offline():
    from paperless_tarmed.cli import build_parser

    parser = build_parser()
    for command in ("report", "apply"):
        assert _wants_online(parser.parse_args([command])) is False, command
