"""Shared test doubles: a deterministic theme embedder (words of one theme share an axis, so texts about a theme are close and others are not),"""
import math
import sys
import zlib
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import os  # noqa: E402

# switches a live install sets in the hook environment; the tests describe the defaults, and subprocess hooks inherit os.environ
os.environ.pop("STATEKEEPER_SESSION_NOTE", None)

from statekeeper import Evidence, SqliteStore  # noqa: E402

THEMES = [
    ["datep", "calen", "flatp", "date", "pick", "widge"], ["recei", "ackno", "donor", "dupli"], ["pdf", "render", "conve", "core", "quota", "scal"],
    ["cover", "gate", "test", "ratch"], ["copil", "revie", "loop"], ["linea", "ticke", "cycle", "sprin"],
    ["embed", "vecto", "recal"], ["slack", "board", "watch"], ["migra", "table", "schem", "datab"],
]


class ThemeEmbedder:
    model = "theme-embed"

    def __init__(self):
        self.calls = 0
        self.fails = False

    def embed(self, texts):
        self.calls += 1
        if self.fails:
            raise RuntimeError("down")
        return [self._vector(t) for t in texts]

    @staticmethod
    def _vector(text):
        hashed = 61
        v = [0.0] * (len(THEMES) + hashed)
        for w in "".join(c if c.isalnum() else " " for c in text.lower()).split():
            for i, theme in enumerate(THEMES):
                if any(w.startswith(stem) for stem in theme):
                    v[i] += 1.0
                    break
            else:
                v[len(THEMES) + zlib.crc32(w.encode()) % hashed] += 0.15
        n = math.sqrt(sum(x * x for x in v)) or 1.0
        return [x / n for x in v]


def ev(ref="m1", excerpt="a source sentence"):
    return Evidence("message", ref, excerpt)


def fresh_store():
    return SqliteStore(":memory:")
