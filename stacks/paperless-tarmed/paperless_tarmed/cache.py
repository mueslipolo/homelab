"""A local cache of documents and their PDFs.

Iterating on a regex against a few hundred documents must not re-download a few
hundred PDFs each time: `pull` once, then `report` as often as you like, offline.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path


class Cache:
    def __init__(self, root: Path):
        self.root = Path(root)
        self.pdf_dir = self.root / "pdf"
        self.meta_dir = self.root / "meta"

    def ensure(self) -> None:
        self.pdf_dir.mkdir(parents=True, exist_ok=True)
        self.meta_dir.mkdir(parents=True, exist_ok=True)

    def pdf_path(self, doc_id: int) -> Path:
        return self.pdf_dir / f"{doc_id}.pdf"

    def meta_path(self, doc_id: int) -> Path:
        return self.meta_dir / f"{doc_id}.json"

    def save_meta(self, doc: dict) -> None:
        self.ensure()
        self.meta_path(doc["id"]).write_text(
            json.dumps(doc, ensure_ascii=False, indent=2), encoding="utf-8"
        )

    def load_meta(self, doc_id: int) -> dict:
        return json.loads(self.meta_path(doc_id).read_text(encoding="utf-8"))

    def documents(self, ids: list[int] | None = None) -> Iterator[dict]:
        """Cached documents, newest id first, optionally filtered."""
        if not self.meta_dir.exists():
            return
        wanted = set(ids) if ids else None
        for path in sorted(
            self.meta_dir.glob("*.json"),
            key=lambda p: int(p.stem) if p.stem.isdigit() else 0,
            reverse=True,
        ):
            if not path.stem.isdigit():
                continue
            doc_id = int(path.stem)
            if wanted and doc_id not in wanted:
                continue
            if not self.pdf_path(doc_id).exists():
                continue
            yield json.loads(path.read_text(encoding="utf-8"))
