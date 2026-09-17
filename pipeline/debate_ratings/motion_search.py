import hashlib
import json
import os
import threading
import time

import numpy as np

from . import db
from .fit import mid
from .tagger import fetch_model_dir, mean_pool

EMBEDDER = "motion_embedder"
INDEX = "motion_index"
DIM = 384
MAXLEN = 256
MIN_SCORE = 0.35  # below the site's cutoff, so it can be tuned without touching the api
TOP_HITS = 1000  # broad topics clear the site's cutoff with ~300 motions; this only guards response size
RECHECK_SECONDS = 60


class Encoder:
    """Sentence embeddings as unit vectors, so a dot product is cosine similarity."""

    def __init__(self, path):
        import torch
        from transformers import AutoModel, AutoTokenizer

        self.torch = torch
        torch.set_num_threads(min(8, os.cpu_count() or 4))
        self.tok = AutoTokenizer.from_pretrained(path)
        self.enc = AutoModel.from_pretrained(path).eval()
        self.lock = threading.Lock()  # a fast tokenizer can't be used from two threads at once

    def __call__(self, texts, batch=64):
        torch = self.torch
        out = np.zeros((len(texts), DIM), dtype=np.float32)
        order = sorted(range(len(texts)), key=lambda i: len(texts[i]))  # similar lengths pad less
        for s in range(0, len(order), batch):
            idx = order[s:s + batch]
            with self.lock, torch.no_grad():
                b = self.tok([texts[i] for i in idx], padding=True, truncation=True,
                             max_length=MAXLEN, return_tensors="pt")
                v = mean_pool(self.enc(**b).last_hidden_state, b["attention_mask"])
                out[idx] = torch.nn.functional.normalize(v, dim=-1).numpy()
        return out


_encoder = None
_encoder_lock = threading.Lock()


def encoder(conn):
    """The process's one Encoder, shared by indexing and search; None until the model is uploaded."""
    global _encoder
    with _encoder_lock:
        if _encoder is None:
            path = fetch_model_dir(conn, EMBEDDER)
            if path:
                _encoder = Encoder(path)
        return _encoder


def corpus(balance: list) -> list:
    """(motion id, text) rows to embed: each motion alone, and with its infoslide when it has one."""
    first = {}
    for b in balance:
        k = mid(b[2])
        text, info = first.get(k, (b[2], ""))
        first[k] = (text, info or b[3] or "")
    rows = []
    for k, (text, info) in first.items():
        rows.append((k, text))
        if info:
            rows.append((k, text + "\n" + info))
    return rows


def update_index(conn, balance: list, log=print) -> int:
    """Embed texts the stored index lacks and store it for search; returns how many were embedded."""
    rows = corpus(balance)
    if not rows:
        return 0
    old = db.get_model_files(conn, INDEX)
    have = {}
    if old.get("stamp"):
        vecs = np.frombuffer(old["vectors.f16"], dtype=np.float16).reshape(-1, DIM)
        have = {tk: vecs[j] for j, (_, tk) in enumerate(json.loads(old["keys.json"]))}
    # mid() lowercases and collapses whitespace, which the uncased model ignores anyway
    keys = [[k, mid(text)] for k, text in rows]
    todo = {tk: text for (_, tk), (_, text) in zip(keys, rows, strict=True) if tk not in have}
    if todo:
        try:
            enc = encoder(conn)
        except ImportError:
            log("motion index: torch/transformers unavailable, %d texts left unembedded" % len(todo))
            return 0
        if enc is None:
            log("motion index: no embedding model uploaded, %d texts left unembedded" % len(todo))
            return 0
        have.update(zip(todo, enc(list(todo.values())).astype(np.float16), strict=True))
    body = json.dumps(keys).encode("utf-8")
    stamp = hashlib.sha256(body).hexdigest()[:16].encode("utf-8")
    if old.get("stamp") == stamp:
        log("motion index: unchanged")
        return 0
    db.put_model_file(conn, INDEX, "stamp", b"")  # searchers ignore a half-written index
    db.put_model_file(conn, INDEX, "keys.json", body)
    db.put_model_file(conn, INDEX, "vectors.f16", np.stack([have[tk] for _, tk in keys]).tobytes())
    db.put_model_file(conn, INDEX, "stamp", stamp)
    log("motion index: %d motions, %d texts newly embedded" % (len({k for k, _ in rows}), len(todo)))
    return len(todo)


class Searcher:
    """Ranks motions against the stored index, picking up a rebuilt one within RECHECK_SECONDS."""

    def __init__(self, connect):
        self.connect = connect
        self.lock = threading.Lock()
        self.checked = float("-inf")
        self.stamp = None
        self.index = None
        self.enc = None

    def ready(self) -> bool:
        with self.lock:
            if time.monotonic() - self.checked >= RECHECK_SECONDS:
                self.checked = time.monotonic()
                conn = self.connect()
                try:
                    try:
                        self.enc = encoder(conn)
                    except ImportError:
                        pass  # an api run without the ml extra just can't search
                    stamp = db.get_model_file(conn, INDEX, "stamp")
                    if stamp and stamp != self.stamp:
                        self._load(db.get_model_files(conn, INDEX))
                finally:
                    conn.close()
        return self.enc is not None and self.index is not None

    def _load(self, files):
        if not files.get("stamp"):
            return
        pos = {}
        owner = np.array([pos.setdefault(k, len(pos)) for k, _ in json.loads(files["keys.json"])])
        vectors = np.frombuffer(files["vectors.f16"], dtype=np.float16).reshape(-1, DIM).astype(np.float32)
        self.index = (list(pos), owner, vectors)
        self.stamp = files["stamp"]

    def search(self, q: str):
        """[[motion id, score], ...] best first; a motion scores its best row. None until searchable."""
        if not self.ready():
            return None
        mids, owner, vectors = self.index
        best = np.full(len(mids), -1.0, dtype=np.float32)
        np.maximum.at(best, owner, vectors @ self.enc([q])[0])
        top = np.argsort(-best)[:TOP_HITS]
        return [[mids[i], round(float(best[i]), 3)] for i in top if best[i] >= MIN_SCORE]
