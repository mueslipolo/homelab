"""What the webhook actually writes.

It used to write every extracted value unconditionally and always rewrite the
title. That made the automated path less careful than `apply`: manual
corrections were discarded silently, and because every run modified the
document, a "Document Updated" trigger fed itself -- re-extracting and
re-renaming files on disk indefinitely (PAPERLESS_FILENAME_FORMAT embeds the
title). These tests pin the safer behaviour.

All data here is invented.
"""

from __future__ import annotations

import json

import pytest

from paperless_tarmed.config import Config
from paperless_tarmed.paperless import CustomField

PAGE = """Justificatif de remboursement Release 4.5G/fr
Patient Nom MUSTER Prénom Anna
Traitement 23.07.2026 - 23.07.2026
Fournisseur de prestations Dupont Marie
EAN 7600000000001
Tarif 001
Montant de la facture: 252.00
"""


class FakeApi:
    """Records writes instead of performing them."""

    def __init__(self, doc: dict):
        self.doc = doc
        self.set_calls: list[dict] = []
        self.patch_calls: list[dict] = []
        self.tagged: list[int] = []
        self.notes: list[str] = []

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return None

    def find_field(self, name):
        return CustomField(
            id=2, name=name, data_type="text", select_options={}, select_labels={}
        )

    def download(self, doc_id, dest):
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(b"%PDF-1.4 fake\n")
        return dest

    def document(self, doc_id):
        return self.doc

    @staticmethod
    def encode_value(field, value, currency=None):
        return str(value)

    def set_custom_fields(self, doc_id, values):
        self.set_calls.append(values)

    def patch_document(self, doc_id, **fields):
        self.patch_calls.append(fields)

    def tag_id(self, name, *, create=False):
        return 99

    def document_type_id(self, value):
        return 18

    def add_tag(self, doc_id, tag):
        self.tagged.append(tag)

    def add_note(self, doc_id, text):
        self.notes.append(text)


@pytest.fixture()
def run(monkeypatch, tmp_path):
    """Invoke process() against a fake Paperless and a fixed extraction."""
    import paperless_tarmed.app as app_module
    import paperless_tarmed.paperless as paperless_module

    def _run(doc: dict, page: str = PAGE, **cfg_kw):
        api = FakeApi(doc)
        monkeypatch.setattr(paperless_module, "Paperless", lambda cfg: api)
        monkeypatch.setattr(
            app_module, "extract_pdf",
            lambda path, cfg, doc_id=None: _extraction(cfg, doc_id, page),
        )
        cfg = Config(
            paperless_url="http://x",
            paperless_token="t",
            fields={"patient": "Concernés"},
            cache_dir=tmp_path,
            **cfg_kw,
        )
        app_module.process(1, cfg)
        return api

    def _extraction(cfg, doc_id, page):
        from paperless_tarmed.pipeline import extract_from_pages

        return extract_from_pages([page], cfg, doc_id=doc_id)

    return _run


def test_fills_an_empty_field(run):
    api = run({"id": 1, "title": "scan001", "custom_fields": []})
    assert api.set_calls == [{2: "Anna"}], "an empty field should be filled"


def test_leaves_an_existing_value_alone(run):
    """The whole point: a hand-entered value must survive an automated run."""
    api = run({"id": 1, "title": "scan001",
               "custom_fields": [{"field": 2, "value": "Bruno"}]})
    assert api.set_calls == [], "a differing value must not be overwritten"
    assert api.patch_calls == []


def test_overwrite_flag_replaces_a_differing_value(run):
    api = run({"id": 1, "title": "scan001",
               "custom_fields": [{"field": 2, "value": "Bruno"}]},
              webhook_overwrite=True)
    assert api.set_calls == [{2: "Anna"}]


def test_title_is_left_alone_by_default(run):
    """PAPERLESS_FILENAME_FORMAT can put the title in the on-disk path, so an
    automated retitle renames files. It needs an explicit opt-in."""
    api = run({"id": 1, "title": "scan001", "custom_fields": []})
    assert api.patch_calls == []


def test_title_is_written_when_enabled(run):
    api = run({"id": 1, "title": "scan001", "custom_fields": []},
              webhook_title=True)
    assert len(api.patch_calls) == 1
    assert "Anna" in api.patch_calls[0]["title"]


def test_second_run_is_a_no_op_so_an_updated_trigger_cannot_loop(run):
    """The loop guard. Run once against an empty document, then again against
    the state that first run produced: nothing may be written the second time,
    so no 'Document Updated' event is generated to re-trigger us."""
    first = run({"id": 1, "title": "scan001", "custom_fields": []})
    assert first.set_calls == [{2: "Anna"}]

    after = {"id": 1, "title": "scan001", "custom_fields": [{"field": 2, "value": "Anna"}]}
    second = run(after)
    assert second.set_calls == [], "re-running must not write again"
    assert second.patch_calls == []


def test_writes_are_journalled(run, tmp_path):
    run({"id": 1, "title": "scan001", "custom_fields": []})
    entries = [
        json.loads(line)
        for line in (tmp_path / "journal.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    assert len(entries) == 1
    assert entries[0]["doc_id"] == 1
    assert entries[0]["source"] == "webhook", "must be distinguishable from CLI writes"
    assert entries[0]["fields"] == {"2": "Anna"}


def test_nothing_is_journalled_when_nothing_is_written(run, tmp_path):
    run({"id": 1, "title": "scan001", "custom_fields": [{"field": 2, "value": "Anna"}]})
    assert not (tmp_path / "journal.jsonl").exists()


# --- review tag: only for justificatifs ---------------------------------------

LETTER = """Caisse Exemple
Veuillez trouver ci-joint notre courrier concernant votre dossier.
Meilleures salutations
"""

FAILING_JUSTIFICATIF = """Justificatif de remboursement Release 4.5G/fr
Patient Nom MUSTER Prénom Anna
Traitement 23.07.2026 - 23.07.2026
"""


def test_any_other_document_is_not_tagged(run):
    """A letter, an invoice or a QR sheet is not a failed justificatif."""
    api = run({"id": 1, "title": "scan", "custom_fields": [], "document_type": None},
              page=LETTER, review_tag="extraction-failed")
    assert api.tagged == []
    assert api.set_calls == []


def test_a_justificatif_that_cannot_be_read_is_tagged(run):
    api = run({"id": 1, "title": "scan", "custom_fields": [], "document_type": None},
              page=FAILING_JUSTIFICATIF, review_tag="extraction-failed")
    assert api.tagged == [99]


def test_typed_as_justificatif_but_no_page_found_is_tagged(run):
    """Paperless says it is one, yet no form page was found: an unknown layout
    or missed OCR, which must surface rather than pass silently."""
    api = run({"id": 1, "title": "scan", "custom_fields": [], "document_type": 18},
              page=LETTER, review_tag="extraction-failed",
              document_type="Justificatif de remboursement")
    assert api.tagged == [99]


# --- tag and note in Paperless -------------------------------------------------


def test_a_failure_leaves_a_dated_note(run):
    api = run({"id": 1, "title": "scan", "custom_fields": [], "tags": []},
              page=FAILING_JUSTIFICATIF, review_tag="extraction-failed")
    (note,) = api.notes
    assert note.startswith("paperless-tarmed, 20") and "extraction failed" in note


def test_a_success_is_tagged_and_noted(run, monkeypatch):
    import paperless_tarmed.app as app_module
    import paperless_tarmed.planning as planning_module

    monkeypatch.setattr(app_module, "needs_review", lambda result: False)
    monkeypatch.setattr(planning_module, "needs_review", lambda result: False)
    api = run({"id": 1, "title": "scan001", "custom_fields": [], "tags": []}, done_tag="tarmed")
    assert api.tagged == [99]
    (note,) = api.notes
    assert "read and written" in note and "patient=Anna" in note


def test_a_crash_still_leaves_a_note(run, monkeypatch):
    import paperless_tarmed.app as app_module

    def boom(path, cfg, doc_id=None):
        raise ValueError("unreadable PDF")

    api = run({"id": 1, "title": "scan", "custom_fields": [], "tags": []})
    monkeypatch.setattr(app_module, "extract_pdf", boom)
    api.notes.clear()
    app_module.process(1, Config(paperless_url="http://x", paperless_token="t",
                                 fields={"patient": "Concernés"}))
    (note,) = api.notes
    assert "ValueError: unreadable PDF" in note
