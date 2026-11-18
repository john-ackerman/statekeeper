"""Owner curation of a statekeeper store from the command line: find states, close ones that are no longer true, add a state.

    python tools/curate.py [--db PATH] find <words...>                       standing states whose thing or text has all the words
    python tools/curate.py [--db PATH] show <id>                              one state with its evidence and resolution
    python tools/curate.py [--db PATH] close <id> --reason "..." [--replaced-by N]
    python tools/curate.py [--db PATH] reopen <id> --reason "..."
    python tools/curate.py [--db PATH] unlink <id> <thing> --reason "..." [--match any|name]    (chat: "sk #<id> not <thing>")
    python tools/curate.py [--db PATH] unlink <id> "<words>" --concept --reason "..."   |   unlink <id> --match meaning --reason "..."
    python tools/curate.py [--db PATH] unlink thing:<A> <B> --reason "..."    |   relink <unlink id> --reason "..."   |   unlinks
    python tools/curate.py [--db PATH] feedback <id> useful|wrong|stale|noise [--note "..."]   (also typed in chat as "sk #<id> wrong")
    python tools/curate.py [--db PATH] add <thing> "<text>" --evidence-type T --evidence-ref R [--excerpt E] [--trust owner|verified|derived]
                                       [--kind belief|rule] [--volatility work|stable|rule] [--thing-kind thing|person|agent] [--replaces N] [--object THING]

Every write goes through the Store API (so it is in the event log with the actor) and needs a reason or evidence. Back the database up first
(tools/backup_db.py). `--replaces N` adds the new state and closes #N with it as the replacement.
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from statekeeper import Evidence, SqliteStore, StoreError  # noqa: E402


def line(s) -> str:
    return f"#{s.id} [{s.thing_name}] {s.status}/{s.trust} {s.recorded_utc[:10]}: {' '.join(s.text.split())[:200]}"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=os.environ.get("STATEKEEPER_DB") or str(Path.home() / ".claude" / "statekeeper" / "shadow.db"))
    ap.add_argument("--actor", default="owner")
    sub = ap.add_subparsers(dest="cmd", required=True)
    f = sub.add_parser("find")
    f.add_argument("words", nargs="+")
    sh = sub.add_parser("show")
    sh.add_argument("id", type=int)
    c = sub.add_parser("close")
    c.add_argument("id", type=int)
    c.add_argument("--reason", required=True)
    c.add_argument("--replaced-by", type=int, default=None)
    r = sub.add_parser("reopen")
    r.add_argument("id", type=int)
    r.add_argument("--reason", required=True)
    fb = sub.add_parser("feedback", help="record that a shown state was useful, wrong/stale, or noise (an outcome; the nightly job weighs it)")
    fb.add_argument("id", type=int)
    fb.add_argument("verdict", choices=("useful", "wrong", "stale", "noise"))
    fb.add_argument("--note", default="")
    ul = sub.add_parser("unlink", help="a negative link: 'these may look related but they are not' (never deletes; relink ends it)")
    ul.add_argument("state", help="a state id, or 'thing:<name>' to unlink two things")
    ul.add_argument("target", nargs="?", default=None, help="the thing it is not about (or with --concept, words)")
    ul.add_argument("--concept", action="store_true", help="target is a concept (words), not a thing")
    ul.add_argument("--match", default="any", choices=("any", "name", "meaning"),
                    help="any: never shown when the target is named/on topic; name: only the name connection is cut; meaning (no target): only its own name brings it")
    ul.add_argument("--reason", required=True)
    rl = sub.add_parser("relink")
    rl.add_argument("unlink_id", type=int)
    rl.add_argument("--reason", required=True)
    sub.add_parser("unlinks", help="list active negative links")
    a = sub.add_parser("add")
    a.add_argument("thing")
    a.add_argument("text")
    a.add_argument("--evidence-type", required=True)
    a.add_argument("--evidence-ref", required=True)
    a.add_argument("--excerpt", default=None)
    a.add_argument("--trust", default="owner", choices=("owner", "verified", "derived"))
    a.add_argument("--kind", default="belief", choices=("belief", "rule"))
    a.add_argument("--volatility", default="work", choices=("work", "stable", "rule"))
    a.add_argument("--thing-kind", default="thing", choices=("thing", "person", "agent"))
    a.add_argument("--replaces", type=int, default=None)
    a.add_argument("--object", default=None, help="the thing the state is about besides its subject (it must exist)")
    args = ap.parse_args()
    store = SqliteStore(args.db)
    try:
        if args.cmd == "find":
            want = [w.lower() for w in args.words]
            for s in store.searchable_states():
                hay = f"{s.thing_name} {s.text}".lower()
                if all(w in hay for w in want):
                    print(line(s))
        elif args.cmd == "show":
            s = store.get_state(args.id)
            if s is None:
                print(f"no state #{args.id}", file=sys.stderr)
                return 1
            print(line(s))
            print(f"  standing={s.standing} superseded_by={s.superseded_by} resolution={s.resolution}")
            for e in store.evidence_of([s.id]).get(s.id, []):
                print(f"  evidence {e.ref_type} {e.ref}: {(e.excerpt or '')[:120]}")
            for un in store.unlinks(state_id=s.id):
                print(f"  negative link {un.id}: not {store.get(un.thing_id).name if un.thing_id else un.concept or 'by meaning'} [{un.match}]: {un.reason}")
        elif args.cmd == "close":
            print(line(store.close_state(args.id, args.reason, replaced_by=args.replaced_by, actor=args.actor)))
        elif args.cmd == "feedback":
            from statekeeper.usefulness import log_feedback, parse_feedback
            n = log_feedback(store, parse_feedback(f"sk #{args.id} {args.verdict}"), source="feedback:curate", note=args.note)
            print(f"recorded {args.verdict} for #{args.id}" if n else f"no state #{args.id}")
        elif args.cmd == "unlink":
            if args.state.startswith("thing:"):
                uid = store.unlink(None, thing=args.state[6:], other_thing=args.target, reason=args.reason, actor=args.actor)
            elif args.concept:
                uid = store.unlink(int(args.state.lstrip("#")), concept=args.target, match=args.match, reason=args.reason, actor=args.actor)
            else:
                uid = store.unlink(int(args.state.lstrip("#")), thing=args.target, match=args.match, reason=args.reason, actor=args.actor)
            print(f"unlink {uid} recorded")
        elif args.cmd == "relink":
            store.relink(args.unlink_id, args.reason, actor=args.actor)
            print(f"unlink {args.unlink_id} ended")
        elif args.cmd == "unlinks":
            for un in store.unlinks():
                a_ = store.get(un.thing_id).name if un.thing_id else None
                b_ = store.get(un.other_thing_id).name if un.other_thing_id else None
                what = f"things {a_!r} <-/-> {b_!r}" if un.state_id is None else f"#{un.state_id} not " + (f"thing {a_!r}" if a_ else f"concept {un.concept!r}" if un.concept else "by meaning")
                print(f"{un.id}: {what} [{un.match}] by {un.actor} {un.created_utc[:16]}: {un.reason}")
        elif args.cmd == "reopen":
            print(line(store.reopen(args.id, args.reason, actor=args.actor)))
        elif args.cmd == "add":
            if store.find(args.thing) is None:
                store.add_thing(args.thing, args.thing_kind, actor=args.actor)
            learned = {"owner": "told", "verified": "read", "derived": "inferred"}[args.trust]
            s = store.add_state(args.thing, args.text, evidence=[Evidence(args.evidence_type, args.evidence_ref, args.excerpt)], writer=args.actor,
                                trust=args.trust, learned_how=learned, kind=args.kind, volatility=args.volatility, object=args.object)
            print(line(s))
            if args.replaces:
                store.close_state(args.replaces, f"replaced by #{s.id}", replaced_by=s.id, actor=args.actor)
                print(f"closed #{args.replaces} (replaced by #{s.id})")
    except StoreError as e:
        print(f"refused: {e}", file=sys.stderr)
        return 2
    finally:
        store.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
