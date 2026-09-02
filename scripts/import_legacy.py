import argparse
import csv
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "pipeline"))

from debate_ratings import db

ARTIFACTS = {
    "id_merges": "data/id_merges.json",
    "excluded_rows": "data/excluded_rows.json",
    "motions_clean": "data/motions/clean.json",
    "motions_tags": "data/motions/tags_merged.json",
    "motions_neighbors": "data/motions/neighbors.json",
    "motions_extra": "data/motions_extra.json",
    "motions_tags_hand": "data/motions/tags_hand.json",
    "inst_alias": "data/inst_alias.json",
    "tier_shape": "data/tier_shape.json",
}

EXTRA_GAME_FILES = {
    "sheets": "data/games_sheets.jsonl",
    "videos": "data/games_videos.jsonl",
    "hague": "data/games_hague.jsonl",
    "scoreonly": "data/games_scoreonly.jsonl",
}

RECOVERED_NAMES = {90016: "Thessaloniki WUDC 2016", 90017: "Dutch WUDC 2017",
                   90018: "Southern African UDC 2018", 90019: "Cape Town WUDC 2019",
                   90118: "Novi Sad EUDC 2018", 90020: "Southern African UDC 2017",
                   92020: "NAUDC 2020"}


def load_json(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def import_artifacts(conn, legacy):
    for name, rel in ARTIFACTS.items():
        p = legacy / rel
        if not p.exists():
            print("  missing, skipped: %s" % rel)
            continue
        db.set_artifact(conn, name, load_json(p))
        print("  artifact %s" % name)
    meta = load_json(legacy / "data/build_meta.json")
    db.set_artifact(conn, "legacy_display", meta.get("display") or {})
    db.set_artifact(conn, "display_raw", meta.get("display_raw") or {})
    rm = load_json(legacy / "data/rooms_meta.json")
    db.set_artifact(conn, "rooms_meta", {k: v for k, v in rm.items() if k != "touched"})
    print("  artifact legacy_display / display_raw / rooms_meta")


def import_tournaments(conn, legacy):
    api = load_json(legacy / "data/api_cache.json")
    name2cls = {}
    with open(legacy / "tournament ratings.csv", encoding="utf-8-sig") as f:
        for r in csv.DictReader(f):
            name2cls[r["tournament"].strip()] = (r.get("speaking_class") or "").strip()
    n = 0
    with conn.cursor() as cur:
        for k, a in api.items():
            name = (a.get("name") or "").strip()
            cur.execute(
                "INSERT INTO tournaments (row_id, name, source_url, speaking_class, status) "
                "VALUES (%s, %s, %s, %s, 'ingested') "
                "ON CONFLICT (row_id) DO UPDATE SET name = EXCLUDED.name, "
                "source_url = EXCLUDED.source_url, "
                "speaking_class = EXCLUDED.speaking_class, status = 'ingested'",
                (int(k), name or ("row %s" % k), a.get("url"), name2cls.get(name)))
            n += 1
        for rid, name in RECOVERED_NAMES.items():
            cur.execute(
                "INSERT INTO tournaments (row_id, name, speaking_class, status) "
                "VALUES (%s, %s, %s, 'ingested') ON CONFLICT (row_id) DO NOTHING",
                (rid, name, name2cls.get(name)))
            n += 1
    conn.commit()
    print("  tournaments: %d" % n)


def import_blob_dir(conn, dirpath, table):
    files = sorted(dirpath.glob("*.json"))
    n = 0
    with conn.cursor() as cur:
        for fp in files:
            try:
                rec = load_json(fp)
            except Exception:
                continue
            row_id = int(fp.stem)
            cur.execute(
                f"INSERT INTO {table} (row_id, payload) VALUES (%s, %s) "
                "ON CONFLICT (row_id) DO UPDATE SET payload = EXCLUDED.payload",
                (row_id, json.dumps(rec, ensure_ascii=False)))
            n += 1
            if n % 200 == 0:
                conn.commit()
    conn.commit()
    print("  %s: %d" % (table, n))


def import_raw_tabs(conn, legacy):
    api = {int(k) for k in load_json(legacy / "data/api_cache.json")}
    files = sorted((legacy / "data/full_cache").glob("*.json"))
    n = 0
    with conn.cursor() as cur:
        for fp in files:
            row_id = int(fp.stem)
            try:
                rec = load_json(fp)
            except Exception:
                continue
            if row_id not in api:
                cur.execute(
                    "INSERT INTO tournaments (row_id, name, source_url, status) "
                    "VALUES (%s, %s, %s, 'ingested') ON CONFLICT (row_id) DO NOTHING",
                    (row_id, rec.get("name") or ("row %d" % row_id), rec.get("url")))
            cur.execute(
                "INSERT INTO raw_tabs (row_id, payload) VALUES (%s, %s) "
                "ON CONFLICT (row_id) DO UPDATE SET payload = EXCLUDED.payload",
                (row_id, json.dumps(rec, ensure_ascii=False)))
            if rec.get("date"):
                cur.execute("UPDATE tournaments SET start_date = %s WHERE row_id = %s "
                            "AND start_date IS NULL", (rec["date"], row_id))
            n += 1
            if n % 100 == 0:
                conn.commit()
                print("  raw_tabs %d/%d" % (n, len(files)), end="\r")
    conn.commit()
    print("  raw_tabs: %d" % n)


def import_raw_motions(conn, legacy):
    cache = load_json(legacy / "data/motions_cache.json")
    n = 0
    with conn.cursor() as cur:
        for row, payload in cache.items():
            if not payload or payload.get("_err"):
                continue
            cur.execute(
                "INSERT INTO raw_motions (row_id, payload) VALUES (%s, %s) "
                "ON CONFLICT (row_id) DO UPDATE SET payload = EXCLUDED.payload",
                (int(row), json.dumps(payload, ensure_ascii=False)))
            n += 1
    conn.commit()
    print("  raw_motions: %d" % n)


def import_rooms(conn, legacy):
    with conn.cursor() as cur:
        cur.execute("DELETE FROM rooms")
        with cur.copy("COPY rooms (row_id, t, stage, payload) FROM STDIN") as copy:
            n = 0
            with open(legacy / "data/rooms.jsonl", encoding="utf-8") as f:
                for line in f:
                    r = json.loads(line)
                    copy.write_row((r["row"], r["t"], r["stage"], line.strip()))
                    n += 1
    conn.commit()
    print("  rooms: %d" % n)


def import_extra_games(conn, legacy):
    with conn.cursor() as cur:
        cur.execute("DELETE FROM extra_games")
        with cur.copy("COPY extra_games (source, row_id, t, payload) FROM STDIN") as copy:
            for source, rel in EXTRA_GAME_FILES.items():
                p = legacy / rel
                if not p.exists():
                    continue
                n = 0
                with open(p, encoding="utf-8") as f:
                    for line in f:
                        g = json.loads(line)
                        copy.write_row((source, g["row"], g["t"], line.strip()))
                        n += 1
                print("  extra_games[%s]: %d" % (source, n))
    conn.commit()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--legacy-dir", default=r"C:\Users\allen\DebateELO")
    args = ap.parse_args()
    legacy = Path(args.legacy_dir)
    conn = db.connect()
    db.migrate(conn)
    import_artifacts(conn, legacy)
    import_tournaments(conn, legacy)
    import_raw_tabs(conn, legacy)
    import_raw_motions(conn, legacy)
    import_blob_dir(conn, legacy / "data/judges_cache", "raw_judges")
    import_rooms(conn, legacy)
    import_extra_games(conn, legacy)
    db.set_artifact(conn, "judges", load_json(legacy / "data/judges.json"))
    print("done")


if __name__ == "__main__":
    main()
