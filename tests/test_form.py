import base64
import gzip
import json
import urllib.parse

import httpx
import pytest
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa
from debate_ratings import form, pipeline

from . import world
from .fakes import FakeSheet
from .fakes import form_row as row

QUIET = lambda *a, **k: None


def sheet(*rows):
    return form.parse(FakeSheet(*rows).values())


def served(monkeypatch, *rows):
    s = FakeSheet(*rows)
    monkeypatch.setattr(form, "sheet", lambda: s)
    return s


def published():
    """The fixture world, plus namesakes: two Cape Town regulars and an "Ann" at Fixture Open."""
    conn = world.make_conn()
    conn.extra_games += [
        {"source": "sheets", "row": 90019, "seq": 2, "t": world.day("2018-12-28"), "obs": "O",
         "c": [["ann cape"], ["edward delta cape"]], "r": [1, 0]},
        {"source": "scoreonly", "row": 10, "seq": "Trial", "t": world.T, "obs": "C",
         "c": [["ann"], ["fay beta"]], "r": [75.0, 74.0], "sc": 2.0}]
    pipeline.run_pipeline(conn, fit_only=True, log=QUIET)
    return conn


def outcome(conn, req):
    return conn.artifacts[form.STATE][req["id"]]


def unb64(s):
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def test_parse_reads_the_form_sheet():
    reqs = sheet(row("9/10/2026 11:47:09", "Redaction", "Ann Alpha", " Bob Alpha "),
                 ["", "", "", "", "", "", "FALSE"],
                 row("9/10/2026 12:00:00", "Merge", "A", "B", done="TRUE"),
                 ["9/10/2026 12:30:00", "x@example.com", "Redaction", "Cara Alpha"])
    assert [(r["names"], r["done"], r["cell"]) for r in reqs] == [
        (["Ann Alpha", "Bob Alpha"], False, "G2"), (["A", "B"], True, "G4"),
        (["Cara Alpha"], False, "G5")]
    again = sheet(row("9/10/2026 11:47:09", "Redaction", "Ann Alpha", "Bob Alpha"))
    edited = sheet(row("9/10/2026 11:47:09", "Redaction", "Ann Alpha", "Bob Alphas"))
    assert again[0]["id"] == reqs[0]["id"] != edited[0]["id"]


def test_parse_refuses_a_reshaped_sheet():
    with pytest.raises(RuntimeError):
        form.parse([["Timestamp", "Something else"], ["1", "2"]])
    with pytest.raises(RuntimeError):
        form.parse([["Timestamp", "Request type", "Your name"]])  # no Done column to tick


def test_sheet_speaks_the_sheets_api():
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    pem = key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                            serialization.NoEncryption()).decode()
    account = {"client_email": "bot@example.iam.gserviceaccount.com", "private_key": pem,
               "token_uri": "https://oauth2.googleapis.com/token"}
    seen = []

    def google(request):
        seen.append(request)
        if request.url.host == "oauth2.googleapis.com":
            assertion = urllib.parse.parse_qs(request.content.decode())["assertion"][0]
            head, claims, sig = assertion.split(".")
            key.public_key().verify(unb64(sig), (head + "." + claims).encode(),
                                    padding.PKCS1v15(), hashes.SHA256())
            assert json.loads(unb64(claims))["scope"] == form.SCOPE
            return httpx.Response(200, json={"access_token": "tok", "expires_in": 3599})
        assert request.headers["Authorization"] == "Bearer tok"
        if request.method == "GET":
            return httpx.Response(200, json={"values": FakeSheet(row("1", "Redaction", "Ann")).values()})
        return httpx.Response(200, json={})

    s = form.Sheet("https://docs.google.com/spreadsheets/d/abc_123-x/edit?usp=sharing", account,
                   http=httpx.Client(transport=httpx.MockTransport(google)))
    [req] = form.parse(s.values())
    s.tick([req["cell"]])
    assert [r.url.path for r in seen] == ["/token", "/v4/spreadsheets/abc_123-x/values/A:ZZ",
                                          "/v4/spreadsheets/abc_123-x/values:batchUpdate"]
    assert json.loads(seen[-1].content) == {"valueInputOption": "RAW",
                                            "data": [{"range": "G2", "values": [[True]]}]}


def test_redaction_hides_speakers_and_judges_by_any_spelling():
    conn = published()
    [req] = sheet(row("1", "Redaction", "Ann Álpha", "judy chair", "Nobody Here"))
    assert form.apply(conn, [req], log=QUIET) == 1
    assert {"ann alpha", "judy chair"} <= set(conn.artifacts["hidden"]["players"])
    assert outcome(conn, req)["status"] == "done"  # one real name is enough; the rest is noise
    assert outcome(conn, req)["note"] == "not on the site: Nobody Here"
    first = dict(outcome(conn, req))
    form.apply(conn, [req], log=QUIET)
    assert outcome(conn, req) == first


def test_redacting_someone_already_hidden_is_done():
    conn = published()
    conn.artifacts["hidden"] = {"players": ["ann alpha"], "institutions": []}
    pipeline.run_pipeline(conn, fit_only=True, log=QUIET)
    [req] = sheet(row("1", "Redaction", "Ann Alpha"))
    assert form.apply(conn, [req], log=QUIET) == 0
    assert (outcome(conn, req)["status"], outcome(conn, req)["hid"]) == ("done", [])


def test_rows_ticked_done_are_left_alone():
    conn = published()
    assert form.apply(conn, sheet(row("1", "Redaction", "Ann Alpha", done="TRUE")), log=QUIET) == 0
    assert "hidden" not in conn.artifacts


def test_merge_repoints_old_aliases_at_the_survivor():
    conn = published()
    [req] = sheet(row("1", "Merge", "Edward Delta Cape", "Ned Delta"))
    assert form.apply(conn, [req], log=QUIET) == 1
    merges = conn.artifacts["id_merges"]
    assert merges["edward delta"] == merges["ned delta"] == "edward delta cape"
    assert "edward delta cape" not in merges


def test_merge_never_keeps_a_bare_first_name():
    conn = published()
    [req] = sheet(row("1", "Merge", "Ann", "Ann Cape"))
    assert form.apply(conn, [req], log=QUIET) == 1
    assert outcome(conn, req)["merged"] == {"ann": "ann cape"}


def test_merge_holds_back_on_evidence_of_two_people():
    conn = published()
    reqs = sheet(row("1", "Merge", "Ann Alpha", "Bob Alpha"),
                 row("2", "Merge", "Ann Alpha", "Pat Cape"),
                 row("3", "Merge", "Ann Alpha", "Ann Cape"),
                 row("4", "Merge", "Ann Cape", "Edward Delta Cape"))
    assert form.apply(conn, reqs, log=QUIET) == 1
    notes = [outcome(conn, r).get("note") for r in reqs]
    assert notes[0] == "ann alpha and bob alpha were both at Fixture Open 2024"
    assert notes[1] == "pat cape shares no name with ann alpha"
    assert outcome(conn, reqs[2])["merged"] == {"ann cape": "ann alpha"}
    # the merge above already gave Ann Alpha the Cape Town record
    assert notes[3] == "ann alpha and edward delta cape were both at Cape Town WUDC 2019"
    assert conn.artifacts["id_merges"]["ann cape"] == "ann alpha"


def test_completed_requests_are_ticked_once_live(monkeypatch):
    conn = published()
    s = served(monkeypatch, row("1", "Redaction", "Ann Alpha"), row("2", "Redaction", "Nobody Here"))
    res = pipeline.run_pipeline(conn, requests=True, skip_ingest=True, log=QUIET)
    assert (res["requests"], res["rebuilt"], s.ticked) == (1, True, ["G2"])
    data = json.loads(gzip.decompress(conn.payloads[("data", "gz")][1]))
    assert "Ann Alpha" not in [p[0] for p in data["players"]]
    res = pipeline.run_pipeline(conn, requests=True, skip_ingest=True, log=QUIET)
    assert (res["rebuilt"], s.ticked) == (False, ["G2"])


def test_a_failed_publish_is_retried_before_ticking(monkeypatch):
    conn = published()
    s = served(monkeypatch, row("1", "Redaction", "Ann Alpha"))
    rebuild = pipeline.rebuild

    def refuse(*a, **k):
        raise pipeline.SanityError("refusing to publish")

    monkeypatch.setattr(pipeline, "rebuild", refuse)
    with pytest.raises(pipeline.SanityError):
        pipeline.run_pipeline(conn, requests=True, skip_ingest=True, log=QUIET)
    assert s.ticked == []
    monkeypatch.setattr(pipeline, "rebuild", rebuild)
    res = pipeline.run_pipeline(conn, requests=True, skip_ingest=True, log=QUIET)
    assert (res["requests"], res["rebuilt"], s.ticked) == (1, True, ["G2"])


def test_a_failed_tick_is_retried_without_rebuilding(monkeypatch):
    conn = published()
    s = served(monkeypatch, row("1", "Redaction", "Ann Alpha"))

    def offline(cells):
        raise httpx.ConnectError("offline")

    s.tick = offline
    with pytest.raises(httpx.ConnectError):
        pipeline.run_pipeline(conn, requests=True, skip_ingest=True, log=QUIET)
    del s.tick
    res = pipeline.run_pipeline(conn, requests=True, skip_ingest=True, log=QUIET)
    assert (res["requests"], res["rebuilt"], s.ticked) == (0, False, ["G2"])


def test_merge_keeps_differently_accented_names_apart():
    conn = world.make_conn()
    conn.extra_games += [
        {"source": "sheets", "row": 90019, "seq": 2, "t": world.day("2018-12-28"), "obs": "O",
         "c": [["zoë cape"], ["rex cape"]], "r": [1, 0]},
        {"source": "scoreonly", "row": 10, "seq": "Trial", "t": world.T, "obs": "C",
         "c": [["zoè cape"], ["fay beta"]], "r": [75.0, 74.0], "sc": 2.0}]
    pipeline.run_pipeline(conn, fit_only=True, log=QUIET)
    [req] = sheet(row("1", "Merge", "Zoë Cape", "Zoè Cape"))
    assert form.apply(conn, [req], log=QUIET) == 0
    note = outcome(conn, req)["note"]
    assert note.endswith("are differently accented") and "zoë cape" in note and "zoè cape" in note
