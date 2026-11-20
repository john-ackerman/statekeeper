"""sk: the agent-facing command line for a statekeeper store (look things up, write what was learned, keep the working note).

    python tools/sk.py [--db PATH] recall <words...>            what bears on these words (the same selection the prompt hook uses, a little looser)
    python tools/sk.py [--db PATH] search <words...> [--conversation] [--transcripts] [--limit N]
                                                                standing states (thing, text or evidence) containing all the words, newest first;
                                                                --conversation also searches the recorded prompts and answers,
                                                                --transcripts the harness transcripts on disk (all history)
    python tools/sk.py [--db PATH] show <id>                    one state with its full evidence (e.g. a long imported note), links and history
    python tools/sk.py [--db PATH] add <thing> <text|-> [--object THING] [--rule] [--stable] [--evidence REF] [--owner]
                                                                write a state (an agent's claim unless --owner); prints the nearest existing states,
                                                                so a near-duplicate becomes a `supersede` instead
    python tools/sk.py [--db PATH] supersede <id> <text|-> [--reason R]   a change of mind: the new state replaces #id (kept as history)
    python tools/sk.py [--db PATH] alias <thing> <alias>        another name people use for a thing ("sign in to QA" for a login tool)
    python tools/sk.py [--db PATH] core                         the session-start block (core, working note, tracked now)
    python tools/sk.py [--db PATH] note [--content <text|->]    print, or replace, the working note (where I am, what is running, what is next)

Every write goes through the Store API (event log, evidence required). Text `-` reads stdin. STATEKEEPER_DB / --db choose the store;
STATEKEEPER_SESSION (or CLAUDE_SESSION_ID) is recorded as the session of a write when set.
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from statekeeper import Evidence, SqliteStore, StoreError  # noqa: E402

DEFAULT_DB = str(Path.home() / ".claude" / "statekeeper" / "shadow.db")
NOTE_THING = "working note"


def _text(value: str) -> str:
    text = sys.stdin.read() if value == "-" else value
    if not text.strip():
        raise SystemExit("empty text")
    return text.strip()


def _line(s, width: int = 220) -> str:
    rel = f" -> {s.object_name}" if s.object_name else ""
    return f"#{s.id} [{s.thing_name}{rel}] {s.status}/{s.trust} {s.recorded_utc[:10]}: {' '.join(s.text.split())[:width]}"


def _embedder():
    try:
        from statekeeper.embed import HttpEmbedder
        e = HttpEmbedder(timeout=3.0)
        e.embed(["ping"])
        return e
    except Exception:
        return None


def _session() -> str | None:
    return os.environ.get("STATEKEEPER_SESSION") or os.environ.get("CLAUDE_SESSION_ID") or None


def search_transcripts(words: list[str], limit: int, folder: str | None = None) -> list[str]:
    """The person's and the agent's words in the harness's own transcripts (~/.claude/projects/*/*.jsonl), newest first: the whole history,
    including what came before the store recorded turns."""
    import glob
    import json
    want = [w.lower() for w in words]
    base = folder or os.environ.get("STATEKEEPER_TRANSCRIPTS") or str(Path.home() / ".claude" / "projects")
    def mtime(f: str) -> float:
        try:
            return os.path.getmtime(f)
        except OSError:          # a path too long for Windows, or gone
            return 0.0
    files = sorted(glob.glob(os.path.join(base, "*", "*.jsonl")), key=mtime, reverse=True)
    hits: list[tuple[str, str]] = []
    for f in files:
        try:
            fh = open(f, encoding="utf-8", errors="ignore")
        except OSError:
            continue
        with fh:
            for line in fh:
                if want[0] not in line.lower():
                    continue
                try:
                    d = json.loads(line)
                except ValueError:
                    continue
                if d.get("type") not in ("user", "assistant") or d.get("isSidechain"):
                    continue
                c = (d.get("message") or {}).get("content")
                if isinstance(c, list):
                    c = " ".join(x.get("text", "") for x in c if isinstance(x, dict) and x.get("type") == "text")
                if not isinstance(c, str) or not all(w in c.lower() for w in want):
                    continue
                at = max(0, c.lower().find(want[0]) - 80)
                who = "person" if d["type"] == "user" else "agent"
                hits.append((d.get("timestamp", ""), f"[{who} {d.get('timestamp', '')[:16]} {Path(f).stem[:8]}] …{' '.join(c[at:at + 260].split())}…"))
        if len(hits) >= limit * 3:
            break
    return [h for _, h in sorted(hits, reverse=True)[:limit]]


def search(store, words: list[str], *, conversation: bool = False, limit: int = 20) -> list[str]:
    want = [w.lower() for w in words]
    states = store.searchable_states()
    ev = store.evidence_of([s.id for s in states])
    out = []
    for s in sorted(states, key=lambda s: s.recency, reverse=True):
        hay = " ".join([s.thing_name, s.object_name or "", s.text, *((e.excerpt or "") + " " + e.ref for e in ev.get(s.id, []))]).lower()
        if all(w in hay for w in want):
            out.append(_line(s))
    out = out[:limit]
    if conversation:
        hits = []
        for kind in ("prompt", "output"):
            for ref, _sid, created, text in store.turn_texts(kind):
                if all(w in text.lower() for w in want):
                    hits.append((created, kind, ref, text))
        for created, kind, ref, text in sorted(hits, reverse=True)[:limit]:
            low = text.lower()
            at = max(0, low.find(want[0]) - 80)
            out.append(f"[{kind} {created[:16]} {ref}] …{' '.join(text[at:at + 260].split())}…")
    return out


def recall(store, words: str, embedder) -> str:
    from statekeeper import surface
    out = surface(store, words, embedder, floor=store.get_param("relevance_floor", 0.72) - 0.02, max_things=5, max_states=8, budget=4000,
                  exclude_names=(NOTE_THING,))
    return out.block


def nearest(store, text: str, embedder, n: int = 3) -> list[str]:
    if embedder is None:
        return []
    from statekeeper.embed import cosine, ensure_vectors
    try:
        ensure_vectors(store, embedder, limit=64)
        q = embedder.embed([text[:1200]])[0]
    except Exception:
        return []
    states = {s.id: s for s in store.searchable_states()}
    scored = sorted(((cosine(q, v["vector"]), v["owner_id"]) for v in store.vectors("state", embedder.model) if v["owner_id"] in states), reverse=True)[:n]
    return [f"  {c:.2f} {_line(states[sid], 160)}" for c, sid in scored if c >= 0.80]


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="sk")
    ap.add_argument("--db", default=os.environ.get("STATEKEEPER_DB") or DEFAULT_DB)
    ap.add_argument("--actor", default=os.environ.get("STATEKEEPER_ACTOR") or "agent")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("recall")
    p.add_argument("words", nargs="+")
    p = sub.add_parser("search")
    p.add_argument("words", nargs="+")
    p.add_argument("--conversation", action="store_true", help="also the prompts and answers the store recorded")
    p.add_argument("--transcripts", action="store_true", help="also the harness transcripts on disk (the whole conversation history)")
    p.add_argument("--limit", type=int, default=20)
    p = sub.add_parser("show")
    p.add_argument("id", type=lambda v: int(v.lstrip("#")))
    p = sub.add_parser("add")
    p.add_argument("thing")
    p.add_argument("text")
    p.add_argument("--object", default=None)
    p.add_argument("--rule", action="store_true", help="a standing way of working (kind rule)")
    p.add_argument("--stable", action="store_true", help="durable: a tool, a how-to, a decision (volatility stable)")
    p.add_argument("--evidence", default=None, help="what it rests on: a file, URL, command or message (default: this session)")
    p.add_argument("--owner", action="store_true", help="the owner said it (stands at once); otherwise an agent's claim, a proposal")
    p = sub.add_parser("supersede")
    p.add_argument("id", type=lambda v: int(v.lstrip("#")))
    p.add_argument("text")
    p.add_argument("--reason", default="superseded")
    p = sub.add_parser("alias")
    p.add_argument("thing")
    p.add_argument("alias")
    sub.add_parser("core")
    p = sub.add_parser("topic", help="what is current under a topic and its sub-areas (no name: the topic index)")
    p.add_argument("name", nargs="*")
    p.add_argument("--limit", type=int, default=30)
    p = sub.add_parser("note")
    p.add_argument("--content", default=None)
    args = ap.parse_args(argv)
    store = SqliteStore(args.db)
    try:
        if args.cmd == "search":
            lines = search(store, args.words, conversation=args.conversation, limit=args.limit)
            if args.transcripts:
                lines += search_transcripts(args.words, args.limit)
            print("\n".join(lines) if lines else "nothing found")
        elif args.cmd == "recall":
            block = recall(store, " ".join(args.words), _embedder())
            print(block or "nothing bears on that (try `search`)")
        elif args.cmd == "show":
            s = store.get_state(args.id)
            if s is None:
                print(f"no state #{args.id}")
                return 1
            print(_line(s, 100000))
            print(f"  kind {s.kind}, volatility {s.volatility}, writer {s.writer}, confirmed {s.confirmed_utc or '-'}"
                  + (f", superseded by #{s.superseded_by}" if s.superseded_by else "") + (f", resolution: {s.resolution}" if s.resolution else ""))
            if s.verify_handle:
                print(f"  re-check: {s.verify_handle}")
            for e in store.evidence_of([s.id])[s.id]:
                print(f"  evidence {e.ref_type} {e.ref}" + ("  (the state it was split from: `sk show " + e.ref.lstrip("#") + "` for the full source)" if e.ref_type == "state" else ""))
                if e.excerpt:
                    print("    " + e.excerpt.replace("\n", "\n    "))
            for a, b, rel, score in store.links_of(s.id):
                print(f"  {rel} #{b if a == s.id else a} ({score:.2f})")
        elif args.cmd == "add":
            text = _text(args.text)
            emb = _embedder()
            near = nearest(store, f"{args.thing}: {text}", emb)
            ref = args.evidence or (f"session {_session()}" if _session() else "sk add")
            st = store.add_state(args.thing, text, evidence=[Evidence("message" if not args.evidence else "file", ref, None)],
                                 writer=f"{'owner' if args.owner else args.actor}:sk", trust="owner" if args.owner else "claimed",
                                 learned_how="told" if args.owner else "claimed", session_id=_session(), object=args.object,
                                 kind="rule" if args.rule else "belief", volatility="rule" if args.rule else ("stable" if args.stable else "work"))
            print(f"added {_line(st)}")
            from statekeeper.decompose import is_long
            if is_long(st):
                print("long: the nightly job will split it into atomic states (one fact each); or add them yourself, one per `sk add`")
            if near:
                print("nearest existing (if one says the same, `sk supersede <id>` it instead and close this one):\n" + "\n".join(near))
        elif args.cmd == "supersede":
            old = store.get_state(args.id)
            if old is None or not old.standing:
                raise SystemExit(f"#{args.id} is not a standing state")
            text = _text(args.text)
            st = store.add_state(old.thing_name, text, evidence=[Evidence("message", f"session {_session()}" if _session() else "sk supersede", None)],
                                 writer=f"{args.actor}:sk", trust=old.trust if old.trust in ("owner",) else "claimed", learned_how="claimed",
                                 session_id=_session(), object=old.object_name, kind=old.kind, volatility=old.volatility, status="active")
            store.close_state(old.id, args.reason, replaced_by=st.id, actor=args.actor)
            print(f"#{old.id} -> {_line(st)}")
        elif args.cmd == "alias":
            store.add_alias(args.thing, args.alias, "sk alias", actor=args.actor)
            print(f"'{args.alias}' now names {args.thing}")
        elif args.cmd == "topic":
            from statekeeper.topics import topic_index, topic_states
            if not args.name:
                print(topic_index(store, top=60, budget=4000) or "no topics yet")
                return 0
            t, states = topic_states(store, " ".join(args.name), limit=args.limit)
            if t is None:
                print(f"no thing named '{' '.join(args.name)}' (try `sk topic` for the index, or `search`)")
                return 1
            print(f"{t.name}: {len(states)} newest standing states under it (sub-areas included)")
            for s in states:
                print(_line(s))
        elif args.cmd == "core":
            from statekeeper.session import session_block
            print(session_block(store, budget=100000))
        elif args.cmd == "note":
            t = store.find(NOTE_THING)
            if args.content is None:
                cur = [s for s in store.states_of(t.id) if s.state_key == "note" and s.status == "active"] if t else []
                print(cur[0].text if cur else "(no working note)")
            else:
                from statekeeper.writers.common import observe_keyed
                observe_keyed(store, writer=f"{args.actor}:note", thing=NOTE_THING, parent=None, observations={"note": _text(args.content)[:6000]},
                              evidence=Evidence("message", f"session {_session()}" if _session() else "sk note", None), handle="sk note",
                              as_of=None, volatility="live")
                print("working note replaced")
    except StoreError as e:
        print(f"refused: {e}", file=sys.stderr)
        return 2
    finally:
        store.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
