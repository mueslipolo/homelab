#!/usr/bin/env python3
"""Thin wrapper so the refresh can also be run straight from a checkout.

The implementation lives in `paperless_tarmed.refresh` so that it ships inside the
container image too -- `paperless-tarmed refresh-tariffs` must work on the NAS.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from paperless_tarmed.refresh import main  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(main())
