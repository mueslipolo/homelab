"""End-to-end extraction against local sample documents.

Real justificatifs carry patient names, addresses, birth dates and insurance
numbers, so neither the PDFs nor their expected values live in this repository.
Both sit in the gitignored `samples/` directory:

    samples/*.pdf
    samples/expected.json    {"patients": [...], "documents": {"<file>": {...}}}

These tests skip when that fixture is absent. Regenerate it after verifying the
output by hand:

    paperless-tarmed extract samples/*.pdf --json
"""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import pytest

from paperless_tarmed.config import Config
from paperless_tarmed.pipeline import extract_pdf
from paperless_tarmed.text import pages_from_pdf

SAMPLES = Path(__file__).resolve().parent.parent / "samples"
FIXTURE = SAMPLES / "expected.json"

# Pinned so the plausibility window never makes these tests time-dependent.
TODAY = date(2026, 12, 31)


def _fixture() -> dict:
    if not FIXTURE.exists():
        pytest.skip(f"no {FIXTURE} -- local sample fixture not present")
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def _cases() -> list[str]:
    if not FIXTURE.exists():
        return []
    return sorted(json.loads(FIXTURE.read_text(encoding="utf-8"))["documents"])


def _config(fixture: dict) -> Config:
    return Config(patients=list(fixture.get("patients") or []))


@pytest.mark.parametrize("name", _cases())
def test_sample_matches_expected(name: str) -> None:
    fixture = _fixture()
    expected = fixture["documents"][name]
    path = SAMPLES / name
    if not path.exists():
        pytest.skip(f"{name} not present")

    result = extract_pdf(path, _config(fixture), today=TODAY)

    for key, want in expected["values"].items():
        outcome = result.get(key)
        if want is None:
            assert not outcome.ok, f"{key} unexpectedly extracted as {outcome.value!r}"
        else:
            assert outcome.ok, f"{key}: {outcome.reason}"
            assert str(outcome.value) == want, key

    assert result.title.value == expected["title"]


@pytest.mark.parametrize("name", _cases())
def test_sample_warnings_are_stable(name: str) -> None:
    """A clean sample must stay clean, or the review tag stops meaning anything."""
    fixture = _fixture()
    path = SAMPLES / name
    if not path.exists():
        pytest.skip(f"{name} not present")
    result = extract_pdf(path, _config(fixture), today=TODAY)
    assert result.warnings == fixture["documents"][name].get("warnings", [])


@pytest.mark.parametrize("name", _cases())
def test_no_control_characters_survive_sanitising(name: str) -> None:
    """Regression: a native PDF renders 'Justificatif' as 'Justi\\x07catif'.

    The fi ligature has no ToUnicode entry, so the text layer carries a control
    character. Before sanitize(), page detection missed such documents entirely.
    """
    path = SAMPLES / name
    if not path.exists():
        pytest.skip(f"{name} not present")
    for page in pages_from_pdf(path):
        stray = {c for c in page if ord(c) < 32 and c not in "\n\t"}
        assert not stray, f"control characters left in text: {stray!r}"
