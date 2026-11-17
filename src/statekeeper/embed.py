"""Embeddings: an interface, an OpenAI-compatible HTTP client, and vector math.

Any server that answers POST {base}/embeddings in the OpenAI format works (for example llama.cpp `llama-server --embedding`
with bge-small). With no embedder, retrieval falls back to names only.
"""
from __future__ import annotations

import array
import hashlib
import json
import math
import os
import urllib.request
from typing import Protocol, Sequence


class Embedder(Protocol):
    model: str

    def embed(self, texts: Sequence[str]) -> list[list[float]]:
        """One vector per text, in order. Raises on failure; an empty vector is never returned."""
        ...


class HttpEmbedder:
    def __init__(self, base_url: str | None = None, model: str | None = None, timeout: float = 20.0, batch: int = 32):
        self.base_url = (base_url or os.environ.get("STATEKEEPER_EMBED_URL") or "http://127.0.0.1:8081/v1").rstrip("/")
        self.model = model or os.environ.get("STATEKEEPER_EMBED_MODEL") or "bge-small"
        self.timeout = timeout
        self.batch = batch

    def embed(self, texts: Sequence[str]) -> list[list[float]]:
        out: list[list[float]] = []
        for i in range(0, len(texts), self.batch):
            chunk = [t if t.strip() else "." for t in texts[i:i + self.batch]]
            body = json.dumps({"input": chunk, "model": self.model}).encode("utf-8")
            req = urllib.request.Request(f"{self.base_url}/embeddings", data=body, headers={"Content-Type": "application/json"})
            with urllib.request.urlopen(req, timeout=self.timeout) as r:
                data = json.loads(r.read().decode("utf-8"))["data"]
            out.extend(d["embedding"] for d in sorted(data, key=lambda d: d.get("index", 0)))
        if len(out) != len(texts) or any(len(v) == 0 for v in out):
            raise RuntimeError("the embedding server returned fewer or empty vectors")
        return out


def cosine(a: Sequence[float], b: Sequence[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    return dot / (na * nb) if na and nb else 0.0


def to_blob(v: Sequence[float]) -> bytes:
    return array.array("f", v).tobytes()


def from_blob(b: bytes) -> list[float]:
    a = array.array("f")
    a.frombytes(b)
    return list(a)


def content_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def ensure_vectors(store, embedder: Embedder, *, limit: int = 100000) -> int:
    """Embeds the states (current ones: pending excluded, rejected and superseded excluded) whose text changed or that have no vector, in batches;
    returns how many were embedded. Content-hashed: an unchanged state costs a hash. Proposed states are included on purpose: most of what is
    learned is a proposal for a long time, and a state that cannot be found by meaning cannot be surfaced."""
    have = {(r["owner_id"]): r["content_hash"] for r in store.vectors("state", embedder.model)}
    todo = []
    for s in store.searchable_states():
        text = f"{s.thing_name}: {s.text}"[:300]
        if have.get(s.id) != content_hash(text):
            todo.append((s.id, text))
        if len(todo) >= limit:
            break
    for i in range(0, len(todo), 32):
        part = todo[i:i + 32]
        vectors = embedder.embed([t for _, t in part])
        for (sid, text), v in zip(part, vectors):
            store.put_vector("state", sid, embedder.model, v, content_hash(text))
    return len(todo)
