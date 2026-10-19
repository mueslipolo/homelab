"""The optional ntfy push after the webhook reads a justificatif.

All data here is invented.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import httpx
import pytest
from test_webhook_writes import PAGE, FakeApi

import paperless_tarmed.notify as notify
from paperless_tarmed import values as V
from paperless_tarmed.config import Config


class FakeResult:
    def __init__(self, **values):
        self._values = values

    def value(self, key):
        return self._values.get(key)


RESULT = FakeResult(**{
    V.PATIENT: "Anna",
    V.PROVIDER: "Dupont",
    V.TOTAL: Decimal("252.00"),
    V.TREATMENT_DATE: date(2026, 7, 23),
})


@pytest.fixture()
def posts(monkeypatch):
    sent: list[dict] = []

    def fake_post(url, *, content, headers, timeout):
        sent.append({"url": url, "body": content.decode(), "headers": headers, "timeout": timeout})
        return httpx.Response(200, request=httpx.Request("POST", url))

    monkeypatch.setattr(notify.httpx, "post", fake_post)
    return sent


def cfg(**kw) -> Config:
    base = {"ntfy_url": "http://ntfy:8080/", "ntfy_token": "tk_test",
            "public_url": "https://paperless.example.com"}
    base.update(kw)
    return Config(**base)


def test_sent_with_token_topic_and_link(posts):
    notify.bill_processed(cfg(), 7, RESULT, "CHF", review=False)
    assert len(posts) == 1
    sent = posts[0]
    assert sent["url"] == "http://ntfy:8080/paperless"
    assert sent["headers"]["Authorization"] == "Bearer tk_test"
    assert sent["headers"]["Title"] == "Bill processed"
    assert sent["headers"]["Click"] == "https://paperless.example.com/documents/7/details"
    assert sent["body"] == "Dupont · CHF 252.00 · 23.07.2026"
    assert sent["timeout"] <= 5


def test_topic_is_configurable(posts):
    notify.bill_processed(cfg(ntfy_topic="bills"), 7, RESULT, "CHF", review=False)
    assert posts[0]["url"] == "http://ntfy:8080/bills"


def test_review_says_so(posts):
    notify.bill_processed(cfg(), 7, RESULT, "CHF", review=True)
    assert posts[0]["headers"]["Title"] == "Bill needs review"


def test_patient_left_out_by_default(posts):
    """The text shows on a lock screen."""
    notify.bill_processed(cfg(), 7, RESULT, "CHF", review=False)
    assert "Anna" not in posts[0]["body"]


def test_patient_included_when_enabled(posts):
    notify.bill_processed(cfg(ntfy_patient=True), 7, RESULT, "CHF", review=False)
    assert posts[0]["body"].startswith("Anna · ")


def test_no_link_without_a_public_url(posts):
    """PAPERLESS_URL is the internal address: no use on a phone."""
    notify.bill_processed(cfg(public_url=""), 7, RESULT, "CHF", review=False)
    assert "Click" not in posts[0]["headers"]


@pytest.mark.parametrize("missing", ["ntfy_url", "ntfy_token"])
def test_not_sent_when_unconfigured(posts, missing):
    notify.bill_processed(cfg(**{missing: ""}), 7, RESULT, "CHF", review=False)
    assert posts == []


@pytest.mark.parametrize("failure", [httpx.ConnectError("refused"), httpx.ReadTimeout("slow")])
def test_a_failure_never_raises(monkeypatch, caplog, failure):
    def boom(*a, **kw):
        raise failure

    monkeypatch.setattr(notify.httpx, "post", boom)
    notify.bill_processed(cfg(), 7, RESULT, "CHF", review=False)
    assert "notification not sent" in caplog.text


def test_an_error_status_never_raises(monkeypatch, caplog):
    def forbidden(url, **kw):
        return httpx.Response(403, request=httpx.Request("POST", url))

    monkeypatch.setattr(notify.httpx, "post", forbidden)
    notify.bill_processed(cfg(), 7, RESULT, "CHF", review=False)
    assert "notification not sent" in caplog.text


# --- wired into the webhook ----------------------------------------------------

LETTER = "Caisse Exemple\nVeuillez trouver ci-joint notre courrier.\n"


@pytest.fixture()
def run(monkeypatch, tmp_path, posts):
    import paperless_tarmed.app as app_module
    import paperless_tarmed.paperless as paperless_module

    def _run(page: str = PAGE, **cfg_kw):
        api = FakeApi({"id": 1, "title": "scan001", "custom_fields": []})
        monkeypatch.setattr(paperless_module, "Paperless", lambda c: api)

        def extraction(path, c, doc_id=None):
            from paperless_tarmed.pipeline import extract_from_pages

            return extract_from_pages([page], c, doc_id=doc_id)

        monkeypatch.setattr(app_module, "extract_pdf", extraction)
        app_module.process(1, Config(paperless_url="http://x", paperless_token="t",
                                     fields={"patient": "Concernés"}, cache_dir=tmp_path,
                                     **cfg_kw))
        return api

    return _run


def test_webhook_notifies_after_a_justificatif(run, posts):
    api = run(ntfy_url="http://ntfy:8080", ntfy_token="tk_test")
    assert api.set_calls, "the document was processed"
    assert len(posts) == 1
    assert "CHF 252.00" in posts[0]["body"]


def test_webhook_quiet_for_other_documents(run, posts):
    run(page=LETTER, ntfy_url="http://ntfy:8080", ntfy_token="tk_test")
    assert posts == []


def test_webhook_quiet_when_unconfigured(run, posts):
    run()
    assert posts == []
