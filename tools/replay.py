"""Replay real past inputs through state-first retrieval and write a judging sheet (HTML), optionally beside what an earlier recall system surfaced.

    python tools/replay.py --export out/legacy-export.json --db out/statekeeper.db --n 30 --out out/replay-sheet.html [--floor 0.72] [--baseline baseline.json]

The export is the JSON document `tools/import_legacy.py` reads (its "inputs" are the person's past messages). The optional baseline is a JSON list of
{"id": <input id>, "text": "...", "old": [[name, why, first_line], ...]} recorded from the earlier system on the same inputs; it picks the turns and fills the
left column. Without it the last --n inputs are replayed and the left column is empty. The right column is this project's retrieval over the imported
states. The sheet judges nothing: a person clicks good/bad per entry and "missed" per column.
Writes a .json beside the sheet with sizes and latencies. Needs an embedding server for meaning matches; without it only names surface.
"""
from __future__ import annotations

import argparse
import html
import json
import statistics
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from statekeeper import SqliteStore, surface  # noqa: E402
from statekeeper.embed import HttpEmbedder, ensure_vectors  # noqa: E402
from statekeeper.importer import import_export  # noqa: E402

PAGE_HEAD = """<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Recall replay judging sheet</title>
<style>
:root{--bg:#fff;--fg:#1d2433;--mute:#5a6475;--card:#f4f6fa;--line:#d9dee8;--good:#1d7a46;--bad:#b3261e}
@media (prefers-color-scheme:dark){:root{--bg:#14171d;--fg:#e6e9ef;--mute:#9aa4b5;--card:#1d222b;--line:#323a48;--good:#4cc38a;--bad:#ff7b72}}
body{background:var(--bg);color:var(--fg);font:15px/1.45 system-ui,sans-serif;padding:16px;max-width:1200px;margin:auto}
h1{font-size:20px} p.how{color:var(--mute)} .turn{border:1px solid var(--line);border-radius:8px;margin:14px 0;background:var(--card)}
.msg{padding:10px 12px;border-bottom:1px solid var(--line)} .id{font-weight:700;color:var(--mute)}
.cols{display:grid;grid-template-columns:1fr 1fr} @media(max-width:800px){.cols{grid-template-columns:1fr}}
.col{padding:8px 12px;border-right:1px solid var(--line)} .col:last-child{border-right:0} h3{margin:4px 0 8px;font-size:14px;color:var(--mute)}
.entry{display:flex;gap:8px;justify-content:space-between;align-items:flex-start;padding:6px 0;border-top:1px dashed var(--line)}
.why{color:var(--mute);font-size:12px} .fact{font-size:13px;color:var(--mute)} .none{color:var(--mute);font-style:italic;padding:6px 0}
.judge{display:flex;gap:4px;flex-shrink:0} button{border:1px solid var(--line);background:var(--bg);color:var(--fg);border-radius:6px;padding:3px 9px;cursor:pointer}
button.on.g{background:var(--good);color:#fff;border-color:var(--good)} button.on.b{background:var(--bad);color:#fff;border-color:var(--bad)}
.miss{padding-top:8px;font-size:13px;color:var(--mute)} #bar{position:sticky;top:0;background:var(--bg);padding:8px 0;border-bottom:1px solid var(--line);display:flex;gap:12px;align-items:center;flex-wrap:wrap}
#out{width:100%;height:120px;margin-top:8px}
</style></head><body>
<h1>Recall replay: what would the agent have been shown?</h1>
<p class="how">Real past messages. Left: what the earlier recall surfaced (if recorded). Right: what state-first retrieval surfaces now. Click <b>good</b> if the surfaced state is relevant to that message, <b>bad</b> if not; click <b>yes, missed</b> if something the message needed is missing. Skip what you cannot judge. Clicks save in this browser; <b>Copy results</b> gives JSON to paste back.</p>
<div id="bar"><span id="count"></span><button id="copy">Copy results</button><span id="msgcopy"></span></div>
"""
PAGE_TAIL = """<textarea id="out" readonly placeholder="results appear here after Copy results"></textarea>
<script>
const KEY='statekeeper-replay-judgments-v1';
let state={};try{state=JSON.parse(localStorage.getItem(KEY)||'{}')}catch(e){state={}}
function save(){try{localStorage.setItem(KEY,JSON.stringify(state))}catch(e){}}
function paint(){document.querySelectorAll('[data-key]').forEach(el=>{const v=state[el.dataset.key];el.querySelectorAll('button').forEach(b=>{b.classList.toggle('on',(b.classList.contains('g')&&v==='good')||(b.classList.contains('b')&&v==='bad'))})});
 document.getElementById('count').textContent=Object.keys(state).length+' judged'}
document.addEventListener('click',e=>{const b=e.target.closest('button');if(!b||b.id==='copy')return;const box=b.closest('[data-key]');if(!box)return;
 const k=box.dataset.key,v=b.classList.contains('g')?'good':'bad';if(state[k]===v)delete state[k];else state[k]=v;save();paint()});
document.getElementById('copy').onclick=()=>{const t=JSON.stringify({sheet:'statekeeper-replay',judgments:state},null,1);const o=document.getElementById('out');o.value=t;o.select();
 try{navigator.clipboard.writeText(t);document.getElementById('msgcopy').textContent='copied'}catch(e){document.getElementById('msgcopy').textContent='select and copy the box below'}};
paint();
</script></body></html>"""


def cell(turn: int, side: str, items: list[tuple[str, str, str]]) -> str:
    body = ""
    for i, (name, why, first) in enumerate(items):
        body += (f'<div class="entry"><div><b>{html.escape(name)}</b> <span class="why">{html.escape(why)}</span><div class="fact">{html.escape(first[:240])}</div></div>'
                 f'<div class="judge" data-key="{turn}-{side}-{i}"><button class="g">good</button><button class="b">bad</button></div></div>')
    if not items:
        body = '<div class="none">nothing surfaced</div>'
    return body + f'<div class="miss" data-key="{turn}-{side}-miss">Needed state missing? <button class="b">yes, missed</button></div>'


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--export", default="out/legacy-export.json")
    ap.add_argument("--baseline", default="")
    ap.add_argument("--db", default="out/statekeeper.db")
    ap.add_argument("--n", type=int, default=30)
    ap.add_argument("--floor", type=float, default=0.72)
    ap.add_argument("--out", default="out/replay-sheet.html")
    args = ap.parse_args()
    data = json.loads(Path(args.export).read_text(encoding="utf-8"))
    store = SqliteStore(args.db)
    if not store.things():
        print("imported:", import_export(store, data))
    embedder = None
    try:
        embedder = HttpEmbedder()
        print("embedded states:", ensure_vectors(store, embedder))
    except Exception as e:
        print(f"no embedding server ({type(e).__name__}); names only")
        embedder = None
    inputs = {i["id"]: i for i in data["inputs"]}
    if args.baseline:
        baseline = {t["id"]: t for t in json.loads(Path(args.baseline).read_text(encoding="utf-8"))}
    else:
        baseline = {i: {"id": i, "text": inputs[i]["content"], "old": []} for i in sorted(inputs)[-args.n:]}
    rows, seconds, sizes, per_turn = [], [], [], []
    for tid in sorted(baseline)[:args.n]:
        text = (inputs.get(tid) or {"content": baseline[tid]["text"]})["content"][:1200]
        t0 = time.time()
        out = surface(store, text, embedder, floor=args.floor, log_turn=f"replay-{tid}")
        seconds.append(time.time() - t0)
        sizes.append(len(out.block))
        per_turn.append(len(out.items))
        entries = []
        for item in out.items:
            first = item.states[0] if item.states else None
            entries.append((item.thing.name, item.why, f"#{first.id} {first.text}" if first else ""))
        rows.append((tid, text, [tuple(x) for x in baseline[tid]["old"]], entries))
    sections = "".join(
        f'<section class="turn"><div class="msg"><span class="id">#{tid}</span> {html.escape(text[:600])}</div><div class="cols">'
        f'<div class="col"><h3>Before (legacy recall)</h3>{cell(tid, "old", old)}</div><div class="col"><h3>State-first (this project)</h3>{cell(tid, "new", new)}</div></div></section>'
        for tid, text, old, new in rows)
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    Path(args.out).write_text(PAGE_HEAD + sections + PAGE_TAIL, encoding="utf-8")
    summary = {"turns": len(rows), "entries_before": sum(len(r[2]) for r in rows), "entries_now": sum(per_turn),
               "empty_turns_now": sum(1 for n in per_turn if n == 0), "median_chars": statistics.median(sizes), "max_chars": max(sizes),
               "median_seconds": round(statistics.median(seconds), 3), "max_seconds": round(max(seconds), 3), "floor": args.floor, "embedder": bool(embedder)}
    Path(args.out).with_suffix(".json").write_text(json.dumps(summary, indent=1), encoding="utf-8")
    print(json.dumps(summary))
    return 0


if __name__ == "__main__":
    sys.exit(main())
