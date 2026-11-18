"""Why was a state injected? Prints, for one turn, each state that was shown (or a cap left out) and how it was connected to the input.

    python tools/explain.py <turn_ref | session_id> [#id] [--db PATH]

A session id means its latest turn that showed anything. With `#id` (or `id`), only that state. Reads the outcome log (each `surfaced` outcome carries
the connection: the name matched, the meaning score and shared words, topic carry-over, a live keyed value) and the active negative links.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from statekeeper import SqliteStore  # noqa: E402
from statekeeper.retrieve import explain  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("turn")
    ap.add_argument("state", nargs="?", default=None)
    ap.add_argument("--db", default=os.environ.get("STATEKEEPER_DB") or str(Path.home() / ".claude" / "statekeeper" / "shadow.db"))
    args = ap.parse_args()
    store = SqliteStore(args.db)
    turn = args.turn
    if ":" not in turn:
        refs = [r for (r,) in store.db.execute("SELECT turn_ref FROM outcomes WHERE kind = 'surfaced' AND turn_ref LIKE ? ORDER BY id DESC LIMIT 1", (f"{turn}:%",))]
        if not refs:
            print(f"no turn of session {turn} showed anything")
            return 1
        turn = refs[0]
    want = int(args.state.lstrip("#")) if args.state else None
    prompt = next((t for ref, _s, _c, t in store.turn_texts("prompt") if ref == turn), None)
    print(f"turn {turn}" + (f"\nprompt: {' '.join(prompt.split())[:300]}" if prompt else ""))
    found = False
    for o in store.outcomes(turn_ref=turn):
        if o.kind not in ("surfaced", "omitted", "used", "contradicted", "unhelpful") or (want and o.state_id != want):
            continue
        found = True
        st = store.get_state(o.state_id) if o.state_id else None
        head = f"#{o.state_id} [{st.thing_name if st else '?'}] {' '.join((st.text if st else '').split())[:120]}"
        if o.kind == "surfaced":
            detail = json.loads(o.detail or "{}")
            print(f"\n{head}\n  shown because: {explain(detail.get('reason') or {})}")
        else:
            print(f"  {o.kind} (source {o.source})" if want or o.kind != "omitted" else f"\n{head}\n  omitted by a cap or the budget")
        if want and st:
            for un in store.unlinks(state_id=st.id):
                print(f"  active negative link {un.id}: {un.match} / thing {un.thing_id} / concept {un.concept!r} ({un.reason}, {un.actor})")
    if not found:
        print("nothing recorded for that state on that turn" if want else "nothing shown on that turn")
    return 0


if __name__ == "__main__":
    sys.exit(main())
