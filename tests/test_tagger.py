import json

import httpx
from debate_ratings import tagger
from debate_ratings.fit import mid

from tests.fakes import FakeConn

HAND = "THW ban fossil fuel subsidies"
NEW = ["THW abolish zoos", "THBT the IMF has done more harm than good", "THW legalise all drugs"]


def world(**artifacts):
    motions = {"r1": {"1": [{"t": HAND}] + [{"t": t} for t in NEW]}}
    base = {"motions_tags_hand": {mid(HAND): ["Environment", "Economics"]},
            "motions_tags": {mid(HAND): ["Environment", "Economics"]}}
    return FakeConn(raw_motions=motions, artifacts={**base, **artifacts})


def fake_client(reply, calls=None):
    def handle(req):
        if calls is not None:
            calls.append(json.loads(req.content))
        return httpx.Response(200, json={"choices": [{"message": {"content": reply}}]})
    return httpx.Client(transport=httpx.MockTransport(handle))


def test_tags_new_motions_keeping_only_allowed_tags(monkeypatch):
    monkeypatch.setenv("TAGGER_API_KEY", "k")
    conn, calls = world(), []
    reply = json.dumps({"1": ["Environment", "Animals"], "2": ["Economics"] * 2, "3": ["Nonsense"]})
    assert tagger.tag_new_motions(conn, log=lambda *a: None, client=fake_client(reply, calls)) == 2
    tags = conn.artifacts["motions_tags"]
    assert tags[mid(NEW[0])] == ["Environment"]
    assert tags[mid(NEW[1])] == ["Economics"]
    assert mid(NEW[2]) not in tags  # nothing usable: retried next run
    system = calls[0]["messages"][0]["content"]
    assert "- Environment: climate" in system and HAND in system


def test_caps_tags_and_tolerates_code_fences(monkeypatch):
    monkeypatch.setenv("TAGGER_API_KEY", "k")
    conn, five = world(), ["Sport", "Art", "Media", "Business", "Policy"]
    reply = "```json\n" + json.dumps({"1": five}) + "\n```"
    tagger.tag_new_motions(conn, log=lambda *a: None, client=fake_client(reply))
    assert conn.artifacts["motions_tags"][mid(NEW[0])] == five[:4]


def test_no_key_or_failed_call_leaves_motions_untagged(monkeypatch):
    monkeypatch.delenv("TAGGER_API_KEY", raising=False)
    conn = world()
    assert tagger.tag_new_motions(conn, log=lambda *a: None) == 0

    monkeypatch.setenv("TAGGER_API_KEY", "k")
    monkeypatch.setattr(tagger, "RETRY_WAITS", (0,))
    down = httpx.Client(transport=httpx.MockTransport(lambda req: httpx.Response(429)))
    assert tagger.tag_new_motions(conn, log=lambda *a: None, client=down) == 0
    assert tagger.tag_new_motions(conn, log=lambda *a: None, client=fake_client("not json")) == 0
    assert len(conn.artifacts["motions_tags"]) == 1
