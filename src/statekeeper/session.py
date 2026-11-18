"""What a state-first session start would put in front of the model (SessionStart and compact): core states about the agent and the people it works with,
the working-note state, and what is tracked now (open PRs, running agents) from the deterministic writers. Pure read; used in shadow mode only (logged,
never emitted) until it is enabled.
"""
from __future__ import annotations

from datetime import datetime, timezone

from .store import Store

STALE_HOURS = 6

NOTE_THING = "working note"


def session_block(store: Store, *, budget: int = 9000) -> str:
    lines: list[str] = []
    core: list[str] = []
    for t in store.things():
        if t.retired or t.kind not in ("agent", "person"):
            continue
        for s in store.states_of(t.id):
            if s.status == "active" and s.volatility in ("rule", "stable"):
                core.append(f"- [{t.name}] {s.text}")
    if core:
        lines += ["Core: what is standing about me and the people I work with.", *core]
    note = store.find(NOTE_THING)
    if note:
        cur = [s for s in store.states_of(note.id) if s.state_key == "note" and s.status == "active"]
        if cur:
            lines += ["", f"Working note (last changed {cur[0].recorded_utc[:16]}, verified {(cur[0].confirmed_utc or '')[:16]}):", cur[0].text]
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
        lines += ["", "Tracked now (from outside reads; each has a last-verified time in the store):", *tracked[:40]]
    text = "\n".join(lines)
    return text if len(text) <= budget else text[:budget - 20] + "\n[truncated]"
