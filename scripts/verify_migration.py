import argparse
import csv
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "pipeline"))

from debate_ratings import db, rooms


def room_key(r):
    return (r["row"], str(r["seq"]),
            tuple(sorted(tm["team"] for tm in r["teams"])))


def norm_room(r):
    r = dict(r)
    r.pop("scale", None)
    return json.dumps(r, sort_keys=True)


def verify_rooms(conn, legacy):
    imported = {}
    for r in db.iter_rooms(conn):
        imported[room_key(r)] = norm_room(r)
    print("imported rooms: %d" % len(imported))

    judges_struct = db.get_artifact(conn, "judges", {})
    merges = db.get_artifact(conn, "id_merges", {})
    excluded = db.get_artifact(conn, "excluded_rows", [])
    builder = rooms.Builder(merges, excluded, judges_struct.get("p") or {})
    rebuilt = {}
    with conn.cursor(name="tabs_cur") as cur:
        cur.itersize = 50
        cur.execute("SELECT payload FROM raw_tabs ORDER BY row_id")
        for (rec,) in cur:
            for room in builder.build_tournament(rec):
                rebuilt[room_key(room)] = norm_room(room)
    print("rebuilt rooms:  %d" % len(rebuilt))
    print("rebuilt speak scale: %.6f" % builder.speak_scale())

    only_imported = set(imported) - set(rebuilt)
    only_rebuilt = set(rebuilt) - set(imported)
    diff = sum(1 for k in set(imported) & set(rebuilt) if imported[k] != rebuilt[k])
    print("only in import: %d, only in rebuild: %d, differing: %d"
          % (len(only_imported), len(only_rebuilt), diff))
    for k in list(only_imported)[:5]:
        print("  import-only:", k)
    for k in list(only_rebuilt)[:5]:
        print("  rebuild-only:", k)
    for k in [k for k in set(imported) & set(rebuilt) if imported[k] != rebuilt[k]][:3]:
        print("  DIFF", k)
        print("    imported:", imported[k][:300])
        print("    rebuilt: ", rebuilt[k][:300])
    return not only_imported and not only_rebuilt and diff == 0


def verify_ranking(rows_b, legacy):
    ref = {}
    with open(Path(legacy) / "debater_ranking_baseline.csv", encoding="utf-8-sig") as f:
        for r in csv.DictReader(f):
            ref[r["key"]] = (int(r["rank"]), float(r["skill_mu"]))
    new = {r["key"]: (r["rank"], r["skill_mu"]) for r in rows_b}
    common = set(ref) & set(new)
    print("ranking keys: ref %d, new %d, common %d" % (len(ref), len(new), len(common)))
    top = sorted(ref, key=lambda k: ref[k][0])[:100]
    moved = [(k, ref[k][0], new.get(k, (None,))[0]) for k in top
             if new.get(k, (None,))[0] != ref[k][0]]
    print("top-100 rank changes: %d" % len(moved))
    for k, old, nw in moved[:15]:
        print("  %-30s %s -> %s" % (k, old, nw))
    mus = [(ref[k][1], new[k][1]) for k in common]
    import statistics
    diffs = [abs(a - b) for a, b in mus]
    print("mu abs diff: mean %.5f, max %.5f" % (statistics.mean(diffs), max(diffs)))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--legacy-dir", default=r"C:\Users\allen\DebateELO")
    ap.add_argument("--rooms-only", action="store_true")
    args = ap.parse_args()
    conn = db.connect()
    ok = verify_rooms(conn, args.legacy_dir)
    print("rooms rebuild %s" % ("MATCHES import" if ok else "DIFFERS from import"))
    if args.rooms_only:
        return
    from debate_ratings import fit, payload
    mm, occs, texts = fit.motion_map(conn)
    base_tab = fit.fit(conn, False, mm)
    disp = dict(db.get_artifact(conn, "legacy_display", {}))
    disp.update((db.get_artifact(conn, "rooms_meta", {}) or {}).get("display") or {})
    rows_b = payload.speaker_rows(base_tab, disp)
    verify_ranking(rows_b, args.legacy_dir)


if __name__ == "__main__":
    main()
