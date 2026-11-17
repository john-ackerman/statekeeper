"""Semantic closure of open commitments over stored transcripts (offline; needs the local embedding server). Writes pairs to judge.

    python tools/commitment_closure.py <transcript.jsonl> [...] [--threshold 0.78] [--out out/commitment-closure]

For every commitment open at the end of its turn, the next 10 turns' text sentences and action descriptions are embedded and the closest one is found.
Reports how many close by meaning versus by the word-overlap proxy, the score distribution, and writes a sample of pairs (agreeing, newly closed, near-miss)
for judging. The judging is the agent's until the owner reviews.
"""
from __future__ import annotations

import argparse
import json
import random
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "tools"))

from commitment_replay import FOLLOW, turns  # noqa: E402
from statekeeper.commitments import Action, _stems, closure_by_meaning, fulfilment_chunks, open_commitments  # noqa: E402
from statekeeper.embed import HttpEmbedder  # noqa: E402

PUNCT = re.compile(r"[{}\"]")
SENTENCES = re.compile(r"(?<=[.!?])\s+|\n+")


chunks_of = fulfilment_chunks


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("transcripts", nargs="+")
    ap.add_argument("--threshold", type=float, default=0.78)
    ap.add_argument("--out", default="out/commitment-closure")
    args = ap.parse_args()
    emb = HttpEmbedder()
    rows = []
    for p in args.transcripts:
        all_turns = list(turns(p))
        for t, (user, events) in enumerate(all_turns):
            for c in open_commitments(events):
                later = [e for _, ev in all_turns[t + 1:t + 1 + FOLLOW] for e in ev]
                word = any((isinstance(e, Action) and c.stems & _stems(e.tool + " " + e.detail)) or (isinstance(e, str) and len(c.stems & _stems(e)) >= 2) for e in later)
                score, chunk, closed = closure_by_meaning(c.sentence, chunks_of(later)[:400], emb, args.threshold)
                rows.append({"commitment": c.sentence, "word_closed": word, "score": round(score, 3), "semantic_closed": closed, "closest": chunk[:240]})
    scores = sorted(r["score"] for r in rows)
    summary = {"open": len(rows), "word_closed": sum(r["word_closed"] for r in rows), "semantic_closed": sum(r["semantic_closed"] for r in rows),
               "closed_by_both": sum(r["word_closed"] and r["semantic_closed"] for r in rows), "semantic_only": sum(r["semantic_closed"] and not r["word_closed"] for r in rows),
               "word_only": sum(r["word_closed"] and not r["semantic_closed"] for r in rows), "neither": sum(not r["word_closed"] and not r["semantic_closed"] for r in rows),
               "score_quartiles": [scores[len(scores) // 4], scores[len(scores) // 2], scores[3 * len(scores) // 4]] if scores else [], "threshold": args.threshold}
    rng = random.Random(20261116)
    both = [r for r in rows if r["word_closed"] and r["semantic_closed"]]
    only = [r for r in rows if r["semantic_closed"] and not r["word_closed"]]
    near = sorted((r for r in rows if not r["semantic_closed"]), key=lambda r: -r["score"])[:10]
    sample = [("both", r) for r in rng.sample(both, min(8, len(both)))] + [("semantic-only", r) for r in rng.sample(only, min(10, len(only)))] + [("near-miss", r) for r in near]
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    Path(args.out + ".json").write_text(json.dumps({"summary": summary, "rows": rows}, indent=1), encoding="utf-8")
    Path(args.out + ".md").write_text("\n\n".join(f"{i}. [{k}] score {r['score']} word_closed={r['word_closed']}\n   COMMITMENT: {r['commitment']}\n   CLOSEST LATER: {r['closest']}" for i, (k, r) in enumerate(sample, 1)), encoding="utf-8")
    print(json.dumps(summary))
    return 0


if __name__ == "__main__":
    sys.exit(main())
