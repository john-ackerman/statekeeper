"""What a state-first session start would put in front of the model (SessionStart and compact): core states about the agent and the people it works with,
the working-note state, and what is tracked now (open PRs, running agents) from the deterministic writers. Pure read; used in shadow mode only (logged,
never emitted) until it is enabled.
"""
from __future__ import annotations

from datetime import datetime, timezone

from .store import Store

STALE_HOURS = 6

NOTE_THING = "working note"
TOPIC_INDEX = __import__("os").environ.get("STATEKEEPER_TOPIC_INDEX", "1").strip().lower() not in ("0", "off", "false", "no")
WITH_NOTE = __import__("os").environ.get("STATEKEEPER_SESSION_NOTE", "1").strip().lower() not in ("0", "off", "false", "no")   # 0: no hand-written note
RUNNING = __import__("os").environ.get("STATEKEEPER_RUNNING", "1").strip().lower() not in ("0", "off", "false", "no")          # the what's-running roll-up
BOARD_AGENT_PREFIX = "board agent "
ASK_PREFIX = "board ask "
WATCHER_THING = "work-board watcher"


def whats_running(store: Store, *, now: datetime | None = None) -> list[str]:
    """What is in flight, from outside reads and recent notes, not from the hand-written note: agents on the work board that are still running or
    waiting (with what they are for), the newest release, and recent notes saying something is pending for John. Open PRs are in "Tracked now"."""
    from .intents import latest_release, pending_for_john
    now = now or datetime.now(timezone.utc)
    out: list[str] = []
    pool = store.searchable_states()
    agents = []
    for s in pool:
        if s.state_key == "agent.status" and s.thing_name.startswith(BOARD_AGENT_PREFIX) and not s.text.startswith(("reported", "done", "finished", "cancelled")):
            try:
                h = (now - datetime.fromisoformat((s.confirmed_utc or s.recorded_utc)[:19]).replace(tzinfo=timezone.utc)).total_seconds() / 3600
            except ValueError:
                continue
            if h <= STALE_HOURS:
                agents.append(f"  - {s.thing_name[len(BOARD_AGENT_PREFIX):]}: {s.text}")
    if agents:
        out += ["Agents on the board (running or waiting; `board.sh agent-get <id>`):", *sorted(agents)[:15]]
    asks, watcher = [], []
    for s in pool:
        if s.state_key not in ("ask.state", "watcher.status"):
            continue
        try:
            h = (now - datetime.fromisoformat((s.confirmed_utc or s.recorded_utc)[:19]).replace(tzinfo=timezone.utc)).total_seconds() / 3600
        except ValueError:
            continue
        if h > STALE_HOURS:
            continue                                       # not re-read lately: the board or the observer is down, so say nothing rather than guess
        if s.state_key == "ask.state" and s.thing_name.startswith(ASK_PREFIX) and s.text.startswith("open"):
            asks.append((not s.thing_name.startswith(ASK_PREFIX + "auto-"), s.text.split(" · since ")[-1], s.text))
        elif s.state_key == "watcher.status" and s.thing_name == WATCHER_THING:
            watcher.append(f"{s.thing_name}: {s.text}")
    if asks:
        asks.sort(reverse=True)                            # asks John or an agent wrote first (the board's own auto-* chores last), newest first
        out += [f"Open asks on the board ({len(asks)}; `board.sh ask-list`):",
                *[f"  - {t.split(' · ', 1)[1].replace(' · since ', ' since ', 1)}" for _h, _d, t in asks[:8]]]
        if len(asks) > 8:
            out.append(f"  - ... and {len(asks) - 8} more")
    out += watcher[:1]
    rel = latest_release(pool)
    if rel:
        out += [f"Last release: {rel[0].thing_name}: " + "; ".join(" ".join(x.text.split())[:160] for x in rel)]
    pend = pending_for_john(pool)
    if pend:
        out += ["Pending for John (notes from the last 72h):", *[f"  - #{s.id} {' '.join(s.text.split())[:180]}" for s in pend[:8]]]
    return out


SECTION_SHARE = {"tracked": 0.22, "running": 0.14, "note": 0.20, "topics": 0.06}   # of the budget; the core gets what the others leave


def _fit(name: str, lines: list[str], cap: int) -> list[str]:
    """`lines` cut to `cap` characters at a line boundary, saying how much was left out."""
    out, used = [], 0
    for i, ln in enumerate(lines):
        if used + len(ln) + 1 > cap - 60:
            return out + [f"[{name} truncated: {len(lines) - i} more lines]"]
        out.append(ln)
        used += len(ln) + 1
    return out


def session_block(store: Store, *, budget: int = 9000) -> str:
    """The live sections first (Tracked now, What's running), then the working note, the topic index and the core, each within its own share of
    `budget`, so a growing core can never push the live sections off the end."""
    sections = _session_sections(store)
    order = ["tracked", "running", "note", "topics", "core"]
    out: list[str] = []
    spent = 0
    for name in order:
        lines = sections.get(name) or []
        if not lines:
            continue
        cap = int(budget * SECTION_SHARE[name]) if name in SECTION_SHARE else budget - spent
        fitted = _fit(name, lines, max(0, min(cap, budget - spent)))
        if not fitted or fitted[0].startswith("["):
            continue
        piece = "\n".join(([""] if out else []) + fitted)
        out.append(piece)
        spent += len(piece) + 1
    text = "\n".join(out)
    return text if len(text) <= budget else text[:budget - 20] + "\n[truncated]"


def _session_sections(store: Store) -> dict[str, list[str]]:
    core: list[str] = []
    sec: dict[str, list[str]] = {}
    for t in store.things():
        if t.retired or t.kind not in ("agent", "person"):
            continue
        for s in store.states_of(t.id):
            if s.status == "active" and s.volatility in ("rule", "stable"):
                core.append(f"- [{t.name}] {s.text}")
    if core:
        sec["core"] = ["Core: what is standing about me and the people I work with.", *core]
    if TOPIC_INDEX:
        try:
            from .topics import topic_index
            idx = topic_index(store, top=20, budget=600)
            if idx:
                sec["topics"] = idx.split("\n")
        except Exception:
            pass                                           # the index is a convenience: never fail the session start for it
    note = store.find(NOTE_THING) if WITH_NOTE else None
    if note:
        cur = [s for s in store.states_of(note.id) if s.state_key == "note" and s.status == "active"]
        if cur:
            sec["note"] = [f"Working note (last changed {cur[0].recorded_utc[:16]}, verified {(cur[0].confirmed_utc or '')[:16]}):", *cur[0].text.split("\n")]
    tracked: list[str] = []
    now = datetime.now(timezone.utc)

    def age(s) -> str:
        """A tracked value not re-read for hours says so, rather than reading as current."""
        try:
            h = (now - datetime.fromisoformat((s.confirmed_utc or s.recorded_utc)[:19]).replace(tzinfo=timezone.utc)).total_seconds() / 3600
        except ValueError:
            return " (STALE)"
        return f" (STALE, last verified {int(h)}h ago)" if h > STALE_HOURS else ""
    for t in store.things():
        if t.retired:
            continue
        cur = {s.state_key: s for s in store.states_of(t.id) if s.state_key and s.status == "active"}
        if "pr.state" in cur and cur["pr.state"].text.startswith(("draft", "open")):
            tracked.append(f"- {t.name}: {cur['pr.state'].text}; {cur.get('pr.ci').text if cur.get('pr.ci') else ''}; {cur.get('pr.review').text if cur.get('pr.review') else ''}{age(cur['pr.state'])}")
        elif "agent.status" in cur and cur["agent.status"].text.startswith("working") and not age(cur["agent.status"]):
            tracked.append(f"- {t.name}: {cur['agent.status'].text}")
    if tracked:
        sec["tracked"] = ["Tracked now (from outside reads; each has a last-verified time in the store):", *tracked[:40]]
    if RUNNING:
        try:
            running = whats_running(store, now=now)
        except Exception:
            running = []                                   # a convenience: never fail the session start for it
        if running:
            sec["running"] = ["What's running (derived from the store, not hand-written):", *running]
    return sec
