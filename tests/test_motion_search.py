import json

import numpy as np
from debate_ratings import db, motion_search
from debate_ratings.fit import mid

from .fakes import FakeConn

QUIET = lambda *a, **k: None
WORDS = ("carbon", "tax", "sport", "doping", "china", "taiwan")


class FakeEncoder:
    """One dimension per known word, so similarity is shared vocabulary."""

    def __init__(self):
        self.calls = []

    def __call__(self, texts):
        self.calls.append(list(texts))
        out = np.zeros((len(texts), motion_search.DIM), dtype=np.float32)
        for i, t in enumerate(texts):
            for j, w in enumerate(WORDS):
                out[i, j] = float(w in t.lower())
            out[i] /= max(np.linalg.norm(out[i]), 1e-9)
        return out


def row(text, info=""):
    return [0, "Round 1", text, info, [0], 10, None, 0.0, 0.0, 0.0, 0.0, 0.0]


def test_corpus_adds_an_infoslide_row_only_for_motions_with_one():
    rows = motion_search.corpus([row("THW tax carbon"), row("THW tax carbon", "Info on carbon"),
                                 row("THW ban doping")])
    assert rows == [(mid("THW tax carbon"), "THW tax carbon"),
                    (mid("THW tax carbon"), "THW tax carbon\nInfo on carbon"),
                    (mid("THW ban doping"), "THW ban doping")]


def test_update_index_embeds_only_new_texts_and_drops_removed_motions(monkeypatch):
    enc = FakeEncoder()
    monkeypatch.setattr(motion_search, "encoder", lambda conn: enc)
    written = []
    put = db.put_model_file
    monkeypatch.setattr(db, "put_model_file",
                        lambda conn, model, name, body: (written.append((name, body)),
                                                         put(conn, model, name, body)))
    conn = FakeConn()
    carbon, doping = row("THW tax carbon", "Info on carbon"), row("THW ban doping")
    taiwan = row("THW back Taiwan")

    assert motion_search.update_index(conn, [carbon, doping], log=QUIET) == 3
    assert [n for n, _ in written] == ["stamp", "keys.json", "vectors.f16", "stamp"]
    assert written[0][1] == b"" and written[-1][1]  # the real stamp lands last
    written.clear()
    assert motion_search.update_index(conn, [carbon, doping], log=QUIET) == 0
    assert written == []

    assert motion_search.update_index(conn, [carbon, taiwan], log=QUIET) == 1
    assert enc.calls[-1] == ["THW back Taiwan"]
    keys = json.loads(conn.model_files[(motion_search.INDEX, "keys.json")])
    assert [k for k, _ in keys] == [mid(carbon[2]), mid(carbon[2]), mid(taiwan[2])]
    assert len(conn.model_files[(motion_search.INDEX, "vectors.f16")]) == 3 * motion_search.DIM * 2


def test_update_index_writes_nothing_without_an_uploaded_model():
    conn = FakeConn()
    lines = []
    assert motion_search.update_index(conn, [row("THW ban doping")], log=lines.append) == 0
    assert conn.model_files == {}
    assert "no embedding model uploaded" in lines[0]


def test_searcher_scores_a_motion_by_its_best_row_and_picks_up_a_rebuilt_index(monkeypatch):
    enc = FakeEncoder()
    monkeypatch.setattr(motion_search, "encoder", lambda conn: enc)
    monkeypatch.setattr(motion_search, "RECHECK_SECONDS", 0)
    conn = FakeConn()
    levy = row("THW tax imports", "A carbon levy")  # only its infoslide mentions carbon
    doping = row("THW ban doping in sport")
    motion_search.update_index(conn, [levy, doping], log=QUIET)
    searcher = motion_search.Searcher(lambda: conn)
    assert searcher.search("carbon") == [[mid(levy[2]), 0.707]]  # doping scores 0, under MIN_SCORE

    taiwan = row("THW back Taiwan against China")
    motion_search.update_index(conn, [doping, taiwan], log=QUIET)
    assert searcher.search("taiwan") == [[mid(taiwan[2]), 0.707]]
    assert searcher.search("carbon") == []


def test_searcher_is_not_ready_without_a_model():
    conn = FakeConn()
    assert motion_search.Searcher(lambda: conn).search("carbon") is None
