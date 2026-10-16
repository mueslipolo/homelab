"""How often each extraction warning occurs, from a `report --csv` file.

Counts only: no names, dates or amounts leave the container. Used to decide
which warnings should put a document up for review.

    docker exec paperless-tarmed paperless-tarmed report --document-type 18 --csv --out /tmp/r.csv
    docker exec -i paperless-tarmed python3 - /tmp/r.csv < stacks/paperless-tarmed/scripts/warning-summary.py
"""

import collections
import csv
import re
import sys

# Warnings that only shorten the title: patient, date and amount are fine.
TITLE_ONLY = ("provider name not found", "no service lines parsed from the table", "no category")


def kind(warning: str) -> str:
    """The warning without its specifics (names, dates, amounts, lists)."""
    return re.sub(r"'[^']*'|\[[^\]]*\]|\d[\d.,-]*", "…", warning).strip()


def main(path: str) -> None:
    with open(path, encoding="utf-8") as fh:
        rows = list(csv.DictReader(fh))
    seen, alone, failed = collections.Counter(), collections.Counter(), collections.Counter()
    with_warning = title_only = 0
    for row in rows:
        for key in ("patient", "treatment_date", "total"):
            if row.get(f"{key}_status") == "fail":
                failed[key] += 1
        kinds = {kind(w) for w in row["warnings"].split("; ") if w.strip()}
        if not kinds:
            continue
        with_warning += 1
        seen.update(kinds)
        if len(kinds) == 1:
            alone.update(kinds)
        if all(k.startswith(TITLE_ONLY) for k in kinds):
            title_only += 1
    print(f"{len(rows)} documents, {with_warning} with a warning, {title_only} with title-only warnings")
    print("core value not read:", dict(failed) or "none")
    print("\n with  only  warning")
    for warning, n in seen.most_common(25):
        print(f"{n:5} {alone[warning]:5}  {warning}")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "/tmp/r.csv")
