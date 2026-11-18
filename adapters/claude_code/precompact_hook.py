"""PreCompact hook: make compaction a context reset instead of a summary.

Whatever this prints to stdout is handed to the summariser as its instructions. We tell it to write
only a short transition, because the SessionStart hook (inject_hook.py) restores everything that
matters straight afterwards: the standing core, the working note and what is being tracked.

This is faster than a real summary and, unlike /clear, it keeps background agents alive.
It never blocks compaction: any failure prints nothing and exits 0.
"""
import sys

INSTRUCTION = (
    "Do not summarise the conversation. Replace the whole context with exactly this, and nothing else: "
    "\"This is a fresh session continuing earlier work. Your standing identity, the working note and "
    "everything being tracked (open PRs, running agents, pending asks) are loaded from Statekeeper "
    "right after this message. Read them first, check the tracked items before acting, and treat "
    "anything not listed there as not yet known.\""
)


def main():
    try:
        sys.stdin.read()
    except Exception:
        pass
    print(INSTRUCTION)


if __name__ == "__main__":
    try:
        main()
    except Exception:
        pass
    sys.exit(0)
