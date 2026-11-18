"""A short judging sheet (self-contained HTML) of real turns: what Legacy actually injected vs what statekeeper would inject now. The owner judges which is more useful.

    python tools/parity_sheet.py [--db ~/.claude/statekeeper/shadow.db] [--n 24] [--out ~/.claude/statekeeper/reports/parity-sheet.html]
                                 [--turns-from <an earlier sheet.html>]     re-measure the same turns, in the same order, with today's code and store

Sampled (seeded) from turns where at least one side surfaced something, plus a few where neither did (to judge quiet). Statekeeper's side is recomputed now
with the live settings (agent/people and the working note left out, budget 1800 chars). Nothing here changes any store except reading. The sheet judges
nothing; one click per turn, "Copy results" gives JSON.
"""
from __future__ import annotations

import argparse
import html
import json
import random
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "adapters" / "claude_code"))

import shadow_hook  # noqa: E402
from statekeeper import SqliteStore, surface  # noqa: E402
from statekeeper.embed import HttpEmbedder  # noqa: E402

HEAD = """<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Injection judging sheet</title><style>
:root{--bg:#fff;--fg:#1d2433;--mute:#5a6475;--card:#f4f6fa;--line:#d9dee8;--on:#1d7a46}
@media (prefers-color-scheme:dark){:root{--bg:#14171d;--fg:#e6e9ef;--mute:#9aa4b5;--card:#1d222b;--line:#323a48;--on:#4cc38a}}
body{background:var(--bg);color:var(--fg);font:15px/1.45 system-ui,sans-serif;padding:16px;max-width:1200px;margin:auto}
h1{font-size:20px}.how{color:var(--mute)}.turn{border:1px solid var(--line);border-radius:8px;margin:14px 0;background:var(--card)}
.msg{padding:10px 12px;border-bottom:1px solid var(--line)}.cols{display:grid;grid-template-columns:1fr 1fr}@media(max-width:800px){.cols{grid-template-columns:1fr}}
.col{padding:8px 12px;border-right:1px solid var(--line)}.col:last-child{border-right:0}h3{margin:4px 0 8px;font-size:14px;color:var(--mute)}
pre{white-space:pre-wrap;word-break:break-word;font:12.5px/1.4 ui-monospace,monospace;margin:0;max-height:260px;overflow:auto}.none{color:var(--mute);font-style:italic}
.pick{padding:8px 12px;border-top:1px solid var(--line);display:flex;gap:6px;flex-wrap:wrap}button{border:1px solid var(--line);background:var(--bg);color:var(--fg);border-radius:6px;padding:4px 10px;cursor:pointer}
button.on{background:var(--on);color:#fff;border-color:var(--on)}#bar{position:sticky;top:0;background:var(--bg);padding:8px 0;border-bottom:1px solid var(--line);display:flex;gap:12px;align-items:center}
#out{width:100%;height:110px}</style></head><body>
<h1>Which injection would have helped more?</h1>
<p class="how">Real past prompts. Left: what Legacy actually put in front of the model. Right: what statekeeper would put there now (it is not live). Pick one per turn: which was more useful for answering that prompt, or neither (noise or nothing needed). Mark <b>stale/wrong</b> if either stated something out of date. Saved in this browser; <b>Copy results</b> gives JSON.</p>
<div id="bar"><span id="count"></span><button id="copy">Copy results</button></div>
"""
TAIL = """<textarea id="out" readonly placeholder="results appear here after Copy results"></textarea><script>
const KEY='statekeeper-parity-v1';let st={};try{st=JSON.parse(localStorage.getItem(KEY)||'{}')}catch(e){}
const save=()=>{try{localStorage.setItem(KEY,JSON.stringify(st))}catch(e){}};
function paint(){document.querySelectorAll('[data-k]').forEach(el=>el.querySelectorAll('button').forEach(b=>b.classList.toggle('on',st[el.dataset.k]===b.dataset.v||(b.dataset.v==='stale'&&st[el.dataset.k+'-stale']))));document.getElementById('count').textContent=Object.keys(st).filter(k=>!k.endsWith('-stale')).length+' judged'}
document.addEventListener('click',e=>{const b=e.target.closest('button');if(!b||b.id==='copy')return;const box=b.closest('[data-k]');if(!box)return;const k=box.dataset.k;
 if(b.dataset.v==='stale'){st[k+'-stale']=!st[k+'-stale'];if(!st[k+'-stale'])delete st[k+'-stale']}else{if(st[k]===b.dataset.v)delete st[k];else st[k]=b.dataset.v}save();paint()});
document.getElementById('copy').onclick=()=>{const t=JSON.stringify({sheet:'statekeeper-parity',judgments:st},null,1);const o=document.getElementById('out');o.value=t;o.select();try{navigator.clipboard.writeText(t)}catch(e){}};paint();
</script></body></html>"""


def block(text: str) -> str:
    return f"<pre>{html.escape(text[:2500])}</pre>" if text.strip() else '<div class="none">nothing injected</div>'


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=str(Path.home() / ".claude" / "statekeeper" / "shadow.db"))
    ap.add_argument("--n", type=int, default=24)
    ap.add_argument("--out", default=str(Path.home() / ".claude" / "statekeeper" / "reports" / "parity-sheet.html"))
    ap.add_argument("--turns-from", default=None, help="an earlier sheet: judge the same prompts again")
    args = ap.parse_args()
    store = SqliteStore(args.db)
    emb = HttpEmbedder(timeout=3.0)
    prompts = {ref: text for ref, _s, _c, text in store.turn_texts("prompt")}
    theirs = {i[0]: i[4] for i in store.injections(source="legacy") if i[3] == "concept_map"}
    rows = []
    for ref, text in prompts.items():
        if text.lstrip().startswith("<") or len(text) < 20:
            continue                                          # automated prompts
        ours = surface(store, text, emb, floor=shadow_hook.LIVE_FLOOR, max_things=shadow_hook.LIVE_MAX_THINGS, budget=shadow_hook.LIVE_BUDGET, exclude_kinds=("agent", "person"),
                       exclude_names=("working note",), max_states=shadow_hook.LIVE_MAX_STATES).block
        rows.append((ref, text, theirs.get(ref, ""), (shadow_hook.MARK + ours) if ours else ""))
    either = [r for r in rows if r[2].strip() or r[3].strip()]
    neither = [r for r in rows if not (r[2].strip() or r[3].strip())]
    if args.turns_from:
        import re
        earlier = [html.unescape(m) for m in re.findall(r'<div class="msg"><b>#\d+</b> (.*?)</div>', Path(args.turns_from).read_text(encoding="utf-8"), re.S)]
        by_head = {text[:500]: r for r in rows for text in [r[1]]}
        pick = [by_head[m] for m in earlier if m in by_head]
    else:
        rng = random.Random(20261117)
        pick = rng.sample(either, min(args.n - 4, len(either))) + rng.sample(neither, min(4, len(neither)))
        rng.shuffle(pick)
    body = "".join(
        f'<section class="turn"><div class="msg"><b>#{i}</b> {html.escape(text[:500])}</div><div class="cols"><div class="col"><h3>Legacy (actual)</h3>{block(p)}</div>'
        f'<div class="col"><h3>Statekeeper (would inject)</h3>{block(s)}</div></div><div class="pick" data-k="t{i}">'
        '<button data-v="statekeeper">statekeeper more useful</button><button data-v="legacy">Legacy more useful</button><button data-v="same">about the same</button>'
        '<button data-v="neither">neither / nothing needed</button><button data-v="stale">something stale or wrong</button></div></section>'
        for i, (ref, text, p, s) in enumerate(pick, 1))
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(HEAD + body + TAIL, encoding="utf-8")
    both_empty = sum(1 for r in pick if not (r[2].strip() or r[3].strip()))
    print(json.dumps({"turns_in_sheet": len(pick), "turns_available": len(rows), "legacy_empty": sum(1 for r in pick if not r[2].strip()), "statekeeper_empty": sum(1 for r in pick if not r[3].strip()),
                      "both_empty": both_empty, "path": str(out)}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
