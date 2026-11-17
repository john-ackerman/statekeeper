"""Import an export of an earlier fact store (a JSON document) into a statekeeper database, one way: nothing is written back to the source.

    python tools/import_legacy.py --export legacy-export.json --into out/statekeeper.db [--validate-only]

Export format (see src/statekeeper/importer.py for how each part is used):

    {
      "concepts":  [{"id": 1, "name": "billing service", "kind": "thing|person|self|value", "confirmed": true, "aliases": ["billing"], "kind_said": true}],
      "facts":     [{"id": 10, "subject": 1, "object": null, "text": "...", "isState": true, "asOf": "2026-11-01", "recordedAt": null,
                     "status": "accepted|pending|rejected", "extractedBy": null, "supersedes": null, "supersededBy": null, "resolution": null, "sources": ["f1"]}],
      "fragments": {"f1": {"type": "...", "created": "2026-11-01 10:00:00", "content": "source text", "sha256": "..."}},
      "inputs":    [{"id": 5, "source": "user", "created": "2026-11-01 10:00:00", "content": "a past message of the person"}]
    }

Producing that JSON from whatever the earlier system offers (its CLI, its database) is a small script of your own; this tool only validates and imports it.
The import is idempotent per fact, so it can be re-run as the source grows. The export holds message text: keep it out of version control.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from statekeeper import SqliteStore  # noqa: E402
from statekeeper.importer import import_export  # noqa: E402

REQUIRED = {"concepts": list, "facts": list, "fragments": dict}


def validate(data: object) -> list[str]:
    if not isinstance(data, dict):
        return ["the export is not a JSON object"]
    return [f"missing or wrong type: {k} (expected {t.__name__})" for k, t in REQUIRED.items() if not isinstance(data.get(k), t)]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--export", required=True)
    ap.add_argument("--into", default="out/statekeeper.db")
    ap.add_argument("--validate-only", action="store_true")
    args = ap.parse_args()
    data = json.loads(Path(args.export).read_text(encoding="utf-8"))
    problems = validate(data)
    if problems:
        print("\n".join(problems), file=sys.stderr)
        return 2
    print(f"export: {len(data['concepts'])} concepts, {len(data['facts'])} facts, {len(data['fragments'])} fragments, {len(data.get('inputs', []))} inputs")
    if args.validate_only:
        return 0
    Path(args.into).parent.mkdir(parents=True, exist_ok=True)
    print("imported:", import_export(SqliteStore(args.into), data))
    return 0


if __name__ == "__main__":
    sys.exit(main())
