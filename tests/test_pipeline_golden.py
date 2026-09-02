import json
import os
import pathlib

from debate_ratings import db, fit, judges, payload, rooms

from . import world

GOLDEN_DIR = pathlib.Path(__file__).parent / "golden"
QUIET = lambda *a, **k: None


def run_pipeline(conn=None):
    conn = conn or world.make_conn()
    judge_records = [conn.raw_judges[k] for k in sorted(conn.raw_judges)]
    judges_struct = judges.build(judge_records)
    db.set_artifact(conn, "judges", judges_struct)
    rooms.rebuild_all(conn, judges_struct)
    mm, occs, texts = fit.motion_map(conn)
    base_tab = fit.fit(conn, False, mm, log=QUIET)
    abl_tab = fit.fit(conn, True, mm, log=QUIET)
    data, rest, rows_b, rows_a = payload.build(
        conn, base_tab, abl_tab, occs, texts, "2026-01-01", log=QUIET)
    return conn, data, rest, rows_b


def diffs(a, b, path="$", tol=2e-3, out=None):
    if out is None:
        out = []
    if len(out) > 20:
        return out
    if isinstance(a, dict) and isinstance(b, dict):
        for k in a.keys() | b.keys():
            if k not in a or k not in b:
                out.append("%s.%s: only in %s" % (path, k, "golden" if k in a else "actual"))
            else:
                diffs(a[k], b[k], "%s.%s" % (path, k), tol, out)
    elif isinstance(a, list) and isinstance(b, list):
        if len(a) != len(b):
            out.append("%s: length %d != %d" % (path, len(a), len(b)))
        else:
            for i, (x, y) in enumerate(zip(a, b, strict=True)):
                diffs(x, y, "%s[%d]" % (path, i), tol, out)
    elif isinstance(a, float) or isinstance(b, float):
        if a is None or b is None or abs(a - b) > tol + abs(b) * 1e-3:
            out.append("%s: %r != %r" % (path, a, b))
    elif a != b:
        out.append("%s: %r != %r" % (path, a, b))
    return out


def normalize(obj):
    return json.loads(json.dumps(obj, ensure_ascii=False))


def check_golden(name, obj, tol):
    GOLDEN_DIR.mkdir(exist_ok=True)
    f = GOLDEN_DIR / (name + ".json")
    obj = normalize(obj)
    if os.environ.get("UPDATE_GOLDEN") or not f.exists():
        f.write_text(json.dumps(obj, ensure_ascii=False, indent=1, sort_keys=True),
                     encoding="utf-8")
        return
    golden = json.loads(f.read_text(encoding="utf-8"))
    found = diffs(golden, obj, tol=tol)
    assert not found, "golden %s mismatch:\n%s" % (name, "\n".join(found))


def test_pipeline_matches_golden():
    conn, data, rest, rows_b = run_pipeline()
    rooms_dump = [{"row": r[0], "t": r[1], "stage": r[2], "payload": r[3]}
                  for r in conn.rooms]
    check_golden("rooms", rooms_dump, tol=1e-9)
    check_golden("payload_data", data, tol=2e-3)
    check_golden("payload_rest", rest, tol=2e-3)
    assert rows_b[0]["rank"] == 1
    assert all(not r["key"].startswith("anon::") for r in rows_b)


def test_pipeline_basic_shape():
    conn, data, rest, rows_b = run_pipeline()
    names = {t["n"] for t in data["tournaments"]}
    assert "Fixture Open 2024" in names
    assert "Cape Town WUDC 2019" in names
    players = {p[0] for p in data["players"]}
    assert "Ann Alpha" in players
    assert "Bob Alpha" in players
    assert "Oli Delta" in players
    assert "Ned Delta" in players
    keys = {r["key"] for r in rows_b}
    assert "edward delta" in keys
    assert "ned delta" not in keys
    assert data["tags"] == ["Economics", "Education", "Politics"]
    assert "Judy Chair" in data["jn"]
    assert conn.payloads == {}
