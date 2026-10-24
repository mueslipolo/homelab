"""Webhook behaviour: auth, id parsing, and that work is dispatched, not inlined."""

from __future__ import annotations

import pytest

pytest.importorskip("fastapi")

from fastapi.testclient import TestClient  # noqa: E402

import paperless_tarmed.app as app_module  # noqa: E402

SECRET = "s3cret"
AUTH = {"X-Paperless-Tarmed-Secret": SECRET}


@pytest.fixture()
def client(monkeypatch):
    """A client whose background task records instead of touching Paperless."""
    monkeypatch.setenv("PAPERLESS_TARMED_WEBHOOK_SECRET", SECRET)
    seen: list[int] = []
    monkeypatch.setattr(app_module, "process", lambda doc_id, cfg: seen.append(doc_id))
    with TestClient(app_module.app) as c:
        c.seen = seen  # type: ignore[attr-defined]
        yield c


def test_health(client) -> None:
    assert client.get("/health").json() == {"status": "ok"}


def test_secret_is_required(client) -> None:
    assert client.post("/hook", json={"doc_id": 7}).status_code == 401
    assert client.post("/hook", json={"doc_id": 7}, headers={"X-Paperless-Tarmed-Secret": "wrong"}).status_code == 401
    assert client.seen == [], "no work may be dispatched for a rejected request"


@pytest.mark.parametrize(
    "kwargs,expected",
    [
        ({"json": {"doc_id": 7}}, 7),
        ({"json": {"document_id": "42"}}, 42),  # string ids arrive from templates
        ({"json": {"id": 5}}, 5),
    ],
)
def test_document_id_shapes(client, kwargs, expected) -> None:
    resp = client.post("/hook", headers=AUTH, **kwargs)
    assert resp.status_code == 200
    assert resp.json() == {"accepted": expected}
    assert client.seen == [expected]


def test_document_id_from_query_string(client) -> None:
    resp = client.post("/hook?doc_id=9", json={}, headers=AUTH)
    assert resp.json() == {"accepted": 9}


def test_missing_document_id_is_a_bad_request(client) -> None:
    assert client.post("/hook", json={"nope": 1}, headers=AUTH).status_code == 400
    assert client.seen == []


def test_a_form_body_is_a_bad_request(client) -> None:
    assert client.post("/hook", data={"doc_id": "7"}, headers=AUTH).status_code == 400
    assert client.seen == []


def test_no_secret_configured_refuses_every_request(monkeypatch) -> None:
    """An unset secret must not quietly disable auth."""
    monkeypatch.delenv("PAPERLESS_TARMED_WEBHOOK_SECRET", raising=False)
    seen: list[int] = []
    monkeypatch.setattr(app_module, "process", lambda doc_id, cfg: seen.append(doc_id))
    with TestClient(app_module.app) as c:
        assert c.post("/hook", json={"doc_id": 1}).status_code == 503
    assert seen == []
