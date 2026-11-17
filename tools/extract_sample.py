"""Run the free-text extractor over two seeded samples of real chat messages and report reject rates (a measured number, not a verdict on quality).

    python tools/extract_sample.py --messages messages.json [--db out/statekeeper.db] [--n 20] [--user-source user]

`messages.json` is a JSON list of {"id", "source", "created", "content"}; messages whose source equals --user-source are the person's, the rest the agent's.
Sample A: n chat-worthy messages, half the person's, half the agent's. Sample B: n of the person's decision messages (decision cues, >= 150 chars), disjoint from A.
Writes proposals into a COPY of the database (out/extract-sample.db), the full records to out/extract-sample.json and a readable listing to out/extract-sample.md.
Needs `claude -p` (Haiku, headless, no settings). The message file is only read.
"""
from __future__ import annotations

import argparse
import json
import random
import re
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from statekeeper import SqliteStore  # noqa: E402
from statekeeper.extract import Extractor  # noqa: E402

SKIP = re.compile(r"^\s*(\[SYSTEM|<task-notification|<system-reminder|\[handback|Say hi\b|login\b|Failed to authenticate|Nothing (else )?(queued|new)|Quiet\b|No change)|SYSTEM NOTIFICATION|OAuth session expired", re.I)
DECISION = re.compile(r"\b(i decid|decided|let'?s (go|do|use|keep|drop)|go with|i lean|my lean|we should|i want|i'?d rather|don'?t (do|want|use)|do it|approve|that'?s (the )?(rule|plan)|from now on|always|never|instead of|the reason)\b", re.I)


def chat_messages(path: str) -> list[dict]:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def pick(rows: list[dict], k: int, seed: int) -> list[dict]:
    return random.Random(seed).sample(rows, min(k, len(rows)))


def summarize(records: list[dict]) -> dict:
    seen = sum(r["facts_seen"] for r in records)
    rejected: dict[str, int] = {}
    for r in records:
        for k, v in r["rejected"].items():
            rejected[k] = rejected.get(k, 0) + v
    written = sum(len(r["written"]) for r in records)
    secs = [r["seconds"] for r in records if "seconds" in r]
    return {"messages": len(records), "errors": sum(1 for r in records if "error" in r), "retries": sum(r["retries"] for r in records),
            "retries_that_recovered": sum(1 for r in records if r["retries"] and "error" not in r), "facts_seen": seen, "written": written,
            "rejected_by_rule": rejected, "rule_reject_rate": round(sum(rejected.values()) / seen, 3) if seen else None,
            "facts_per_message": round(written / len(records), 2) if records else None, "median_seconds": sorted(secs)[len(secs) // 2] if secs else None}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--messages", required=True)
    ap.add_argument("--user-source", default="user")
    ap.add_argument("--db", default="out/statekeeper.db")
    ap.add_argument("--n", type=int, default=20)
    args = ap.parse_args()
    rows = chat_messages(args.messages)
    worthy = [f for f in rows if not SKIP.search((f.get("content") or "")[:400])]
    people = [f for f in worthy if f.get("source") == args.user_source and len(f["content"]) >= 150]
    others = [f for f in worthy if f.get("source") != args.user_source and len(f["content"]) >= 500]
    a = pick(people, args.n // 2, 20261116) + pick(others, args.n - args.n // 2, 20261116)
    used = {f["id"] for f in a}
    b = pick([f for f in people if f["id"] not in used and DECISION.search(f["content"])], args.n, 20261117)
    copy = ROOT / "out" / "extract-sample.db"
    shutil.copyfile(args.db, copy)
    store = SqliteStore(copy)
    ex = Extractor(store, session="extract-sample", user_source=args.user_source)
    result = {}
    for name, sample in (("A_chat_sample", a), ("B_user_decisions", b)):
        recs = []
        for i, m in enumerate(sample, 1):
            rec = ex.extract(m)
            rec["chars"] = len(m["content"])
            recs.append(rec)
            print(f"{name} {i}/{len(sample)} #{m['id']} {m.get('source')} {rec.get('seconds')}s written={len(rec['written'])} rejected={rec['rejected']} retries={rec['retries']} {rec.get('error', '')}", flush=True)
        result[name] = {"summary": summarize(recs), "records": recs}
    Path("out/extract-sample.json").write_text(json.dumps(result, indent=1), encoding="utf-8")
    lines = []
    for name, v in result.items():
        lines.append(f"# {name}\n{json.dumps(v['summary'])}\n")
        for r in v["records"]:
            lines.append(f"## message #{r['message']} ({r['source']}) retries={r['retries']} {r.get('error', '')}")
            lines += [f"- [{w['kind']}] {w['thing']}: {w['text']}" for w in r["written"]]
            lines += [f"- REJECTED {k} x{n}" for k, n in r["rejected"].items()]
    Path("out/extract-sample.md").write_text("\n".join(lines), encoding="utf-8")
    print(json.dumps({k: v["summary"] for k, v in result.items()}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
