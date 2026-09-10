import gzip
import json
import threading
import time
from http.server import ThreadingHTTPServer

import httpx
from debate_ratings import api, form, payload, pipeline

from . import world
from .fakes import FakeSheet, form_row


def make_app():
    conn = world.make_conn()
    conn.tournaments[12] = {"name": "Missing IV", "start_date": None,
                            "source_url": "http://x", "speaking_class": "",
                            "status": "failed",
                            "error": "no start date on sheet or tab"}
    return api.App(connect=lambda: conn), conn


def test_missing_dates_lists_undated():
    app, conn = make_app()
    code, out = app.missing_dates()
    assert code == 200
    assert out["count"] == 1
    assert out["missing"][0]["row_id"] == 12
    assert out["missing"][0]["status"] == "failed"


def test_fill_dates_updates_and_requeues():
    app, conn = make_app()
    code, out = app.fill_dates({"12": "2024-05-01", "10": "2024-01-01"})
    assert code == 200
    assert out["updated"] == [12]
    assert out["skipped"] == [10]
    assert conn.tournaments[12]["start_date"].isoformat() == "2024-05-01"
    assert conn.tournaments[12]["status"] == "pending"
    assert conn.tournaments[12]["error"] is None
    assert conn.tournaments[10]["start_date"].isoformat() == "2024-03-01"


def test_fill_dates_validates_input():
    app, _ = make_app()
    assert app.fill_dates({})[0] == 400
    assert app.fill_dates({"12": "not-a-date"})[0] == 400
    assert app.fill_dates({"twelve": "2024-05-01"})[0] == 400


def test_trigger_run_executes_pipeline():
    app, conn = make_app()
    code, out = app.trigger_run({"fit_only": True})
    assert code == 202
    st = app.runner.status()
    for _ in range(600):
        st = app.runner.status()
        if st["state"] != "running":
            break
        time.sleep(0.05)
    assert st["state"] == "done", st.get("error")
    assert st["result"]["rebuilt"] is True
    assert len(conn.payloads) == 4
    assert any("payload stored" in ln for ln in st["log"])


def test_trigger_run_rejects_bad_options():
    app, _ = make_app()
    assert app.trigger_run({"nope": True})[0] == 400
    assert app.trigger_run({"force": "yes"})[0] == 400


def test_only_one_run_at_a_time(monkeypatch):
    app, _ = make_app()
    release = threading.Event()

    def slow(conn, **kw):
        release.wait(5)
        return {"ingested": 0, "rebuilt": False}

    monkeypatch.setattr(pipeline, "run_pipeline", slow)
    assert app.trigger_run({})[0] == 202
    assert app.trigger_run({})[0] == 409
    release.set()
    for _ in range(100):
        if app.runner.status()["state"] != "running":
            break
        time.sleep(0.05)
    assert app.trigger_run({})[0] == 202


def test_http_layer_auth():
    app, _ = make_app()
    api.Handler.app = app
    api.Handler.token = "sekrit"
    srv = ThreadingHTTPServer(("127.0.0.1", 0), api.Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    base = "http://127.0.0.1:%d" % srv.server_address[1]
    auth = {"Authorization": "Bearer sekrit"}
    try:
        assert httpx.get(base + "/health").json() == {"ok": True}
        assert httpx.get(base + "/status").status_code == 401
        assert httpx.get(base + "/status",
                         headers={"Authorization": "Bearer wrong"}).status_code == 401
        r = httpx.get(base + "/status", headers=auth)
        assert r.status_code == 200
        assert r.json()["state"] == "idle"
        r = httpx.get(base + "/missing-dates", headers=auth)
        assert r.status_code == 200
        assert r.json()["count"] == 1
        r = httpx.post(base + "/dates", headers=auth, content=b"[]")
        assert r.status_code == 400
        assert httpx.get(base + "/nope", headers=auth).status_code == 404
    finally:
        srv.shutdown()


def test_add_alias_for_player_and_institution():
    app, conn = make_app()
    code, out = app.add_alias({"kind": "player", "alias": "Ted Delta", "target": "Edward Delta"})
    assert code == 200, out
    assert conn.artifacts["id_merges"]["ted delta"] == "edward delta"
    assert out["applies_at"] == "next rebuild"

    code, out = app.add_alias({"kind": "institution", "alias": "UoN", "target": "Nairobi"})
    assert code == 200, out
    assert conn.artifacts["inst_alias"]["alias"]["uon"] == "Nairobi"
    assert conn.artifacts["inst_alias"]["skip"] == []


def test_add_alias_rejects_chains_and_bad_input():
    app, conn = make_app()
    # id_merges already maps "ned delta" -> "edward delta"
    assert app.add_alias({"kind": "player", "alias": "X", "target": "Ned Delta"})[0] == 409
    assert app.add_alias({"kind": "player", "alias": "Edward Delta", "target": "Someone"})[0] == 409
    assert app.add_alias({"kind": "player", "alias": "A", "target": "A"})[0] == 400
    assert app.add_alias({"kind": "school", "alias": "A", "target": "B"})[0] == 400
    assert app.add_alias({"kind": "player", "alias": "", "target": "B"})[0] == 400
    assert app.add_alias({"kind": "player", "alias": "A", "target": 7})[0] == 400


def test_remove_alias():
    app, conn = make_app()
    code, out = app.remove_alias({"kind": "player", "alias": "Ned Delta"})
    assert code == 200
    assert out["was"] == "edward delta"
    assert "ned delta" not in conn.artifacts["id_merges"]
    assert app.remove_alias({"kind": "player", "alias": "Ned Delta"})[0] == 404


def test_hide_and_unhide_round_trip():
    app, conn = make_app()
    code, out = app.set_hidden({"kind": "player", "key": "Edward Delta"}, True)
    assert code == 200 and out["changed"] is True
    assert conn.artifacts["hidden"]["players"] == ["edward delta"]

    assert app.set_hidden({"kind": "player", "key": "Edward Delta"}, True)[1]["changed"] is False

    code, out = app.set_hidden({"kind": "institution", "key": "Fixture University"}, True)
    assert code == 200
    assert conn.artifacts["hidden"]["institutions"] == ["fixture university"]

    assert app.set_hidden({"kind": "player", "key": "Edward Delta"}, False)[1]["changed"] is True
    assert conn.artifacts["hidden"]["players"] == []
    assert conn.artifacts["hidden"]["institutions"] == ["fixture university"]
    assert app.list_hidden()[1]["institutions"] == ["fixture university"]


def test_hidden_player_is_dropped_from_payload():
    conn = world.make_conn()
    conn.artifacts["hidden"] = {"players": ["edward delta"], "institutions": []}
    app = api.App(connect=lambda: conn)
    code, _ = app.trigger_run({"fit_only": True})
    assert code == 202
    for _ in range(600):
        st = app.runner.status()
        if st["state"] != "running":
            break
        time.sleep(0.05)
    assert st["state"] == "done", st.get("error")
    data = json.loads(gzip.decompress(conn.payloads[("data", "gz")][1]))
    names = [p[0] for p in data["players"]]
    assert "Edward Delta" not in names
    assert payload.HIDDEN_NAME in names
    assert "0" not in data["aliases"] or "Ned Delta" not in data["aliases"].get("0", [])
    rest = json.loads(gzip.decompress(conn.payloads[("rest", "gz")][1]))
    gone = names.index(payload.HIDDEN_NAME)
    assert str(gone) not in rest["careers"]
    assert str(gone) not in rest["curves"]
    assert all(gone not in e[2] for car in rest["careers"].values() for e in car)
    assert data["board"][gone][0] == 0


def test_exclude_drops_a_tournament_from_the_ratings():
    app, conn = make_app()
    code, out = app.set_excluded({"row_id": 12}, True)
    assert code == 200 and out["changed"] is True and out["name"] == "Missing IV"
    assert conn.artifacts["excluded_rows"] == [12]

    assert app.set_excluded({"row_id": 12}, True)[1]["changed"] is False
    assert app.list_excluded()[1]["excluded"] == [{"row_id": 12, "name": "Missing IV"}]

    assert app.set_excluded({"row_id": 12}, False)[1]["changed"] is True
    assert conn.artifacts["excluded_rows"] == []


def test_exclude_validates_input():
    app, _ = make_app()
    assert app.set_excluded({"row_id": "12"}, True)[0] == 400
    assert app.set_excluded({}, True)[0] == 400
    assert app.set_excluded({"row_id": 999999}, True)[0] == 404


def test_form_poll_runs_only_when_there_is_work(monkeypatch):
    app, conn = make_app()
    pipeline.run_pipeline(conn, fit_only=True, log=lambda *a, **k: None)
    s = FakeSheet(form_row("9/10/2026 12:00:00", "Redaction", "Ann Alpha"),
                  form_row("9/10/2026 12:05:00", "Redaction", "Nobody Here"))
    monkeypatch.setattr(form, "sheet", lambda: s)
    assert app.check_requests()[0] == 202
    for _ in range(600):
        st = app.runner.status()
        if st["state"] != "running":
            break
        time.sleep(0.05)
    assert st["state"] == "done", st.get("error")
    assert (st["result"]["requests"], s.ticked) == (1, ["G2"])
    assert app.check_requests() is None

    code, out = app.list_requests()
    assert code == 200
    assert [r["names"] for r in out["needs_review"]] == [["Nobody Here"]]
    assert [r["ticked"] for r in out["requests"]] == [False, True]
    s.rows[2][-1] = "TRUE"  # ticking Done by hand closes a review item
    assert app.list_requests()[1]["needs_review"] == []


def test_requests_listing_needs_the_sheet(monkeypatch):
    app, _ = make_app()
    monkeypatch.setattr(form, "FORM_SHEET_ID", "")
    form.sheet.cache_clear()
    code, out = app.list_requests()
    assert code == 502 and "FORM_SHEET_ID" in out["error"]


def test_hiding_a_person_also_hides_their_judging():
    conn = world.make_conn()
    conn.artifacts["hidden"] = {"players": ["judy chair"], "institutions": []}
    app = api.App(connect=lambda: conn)
    assert app.trigger_run({"fit_only": True})[0] == 202
    for _ in range(600):
        st = app.runner.status()
        if st["state"] != "running":
            break
        time.sleep(0.05)
    assert st["state"] == "done", st.get("error")
    data = json.loads(gzip.decompress(conn.payloads[("data", "gz")][1]))
    assert "Judy Chair" not in data["jn"]
    assert "Cody Chair" in data["jn"]
