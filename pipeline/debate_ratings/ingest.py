import datetime
import json

from . import sheet, tabbycat
from .settings import MIN_AGE_DAYS


def sync_sheet(conn):
    rows = sheet.fetch_rows()
    with conn.cursor() as cur:
        for r in rows:
            cur.execute(
                "INSERT INTO tournaments (row_id, name, start_date, source_url, "
                "speaking_class, format) VALUES (%s, %s, %s, %s, %s, %s) "
                "ON CONFLICT (row_id) DO UPDATE SET name = EXCLUDED.name, "
                "start_date = COALESCE(EXCLUDED.start_date, tournaments.start_date), "
                "source_url = EXCLUDED.source_url, "
                "speaking_class = EXCLUDED.speaking_class, format = EXCLUDED.format",
                (r["row_id"], r["name"], r["date"], r["url"],
                 r["speaking_class"], r["format"]))
    conn.commit()
    return rows


def due_tournaments(conn, sheet_rows, today=None):
    """Pending crawlable tournaments at least MIN_AGE_DAYS old, so tabs are final."""
    today = today or datetime.date.today()
    cutoff = today - datetime.timedelta(days=MIN_AGE_DAYS)
    crawlable = {r["row_id"]: r for r in sheet_rows if sheet.crawlable(r)}
    with conn.cursor() as cur:
        cur.execute(
            "SELECT row_id, start_date FROM tournaments WHERE status = 'pending'")
        pending = cur.fetchall()
    due = []
    for row_id, start_date in pending:
        r = crawlable.get(row_id)
        if not r:
            continue
        if start_date is not None and start_date > cutoff:
            continue
        due.append((row_id, r["name"], r["url"], start_date))
    return due


def ingest_tournament(conn, row_id, name, url, start_date, today=None):
    today = today or datetime.date.today()
    cutoff = today - datetime.timedelta(days=MIN_AGE_DAYS)
    rec = tabbycat.fetch_tournament(row_id, name, url,
                                    start_date.isoformat() if start_date else None)
    if not rec.get("rounds") or all(not rd.get("rooms") for rd in rec["rounds"]):
        raise RuntimeError("no rooms found: %s" % "; ".join(rec.get("errors") or []))
    if not rec.get("date"):
        raise RuntimeError("no start date on sheet or tab")
    # Date may only be discovered from the tab itself; re-check the age gate.
    if datetime.date.fromisoformat(rec["date"]) > cutoff:
        return None
    motions = {}
    try:
        motions = tabbycat.fetch_motions(url)
    except Exception:
        pass  # motions are optional; many tabs hide them
    judges = tabbycat.fetch_judges(rec)
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO raw_tabs (row_id, payload) VALUES (%s, %s) "
            "ON CONFLICT (row_id) DO UPDATE SET payload = EXCLUDED.payload",
            (row_id, json.dumps(rec, ensure_ascii=False)))
        if motions:
            cur.execute(
                "INSERT INTO raw_motions (row_id, payload) VALUES (%s, %s) "
                "ON CONFLICT (row_id) DO UPDATE SET payload = EXCLUDED.payload",
                (row_id, json.dumps(motions, ensure_ascii=False)))
        if judges.get("rounds"):
            cur.execute(
                "INSERT INTO raw_judges (row_id, payload) VALUES (%s, %s) "
                "ON CONFLICT (row_id) DO UPDATE SET payload = EXCLUDED.payload",
                (row_id, json.dumps(judges, ensure_ascii=False)))
        cur.execute(
            "UPDATE tournaments SET status = 'ingested', error = NULL, "
            "fetched_at = now(), start_date = COALESCE(start_date, %s) "
            "WHERE row_id = %s",
            (rec["date"], row_id))
    conn.commit()
    return rec


def mark_failed(conn, row_id, error):
    with conn.cursor() as cur:
        cur.execute("UPDATE tournaments SET status = 'failed', error = %s "
                    "WHERE row_id = %s", (str(error)[:500], row_id))
    conn.commit()


def run(conn, log=print, today=None):
    rows = sync_sheet(conn)
    due = due_tournaments(conn, rows, today=today)
    log("tournaments due for ingest: %d" % len(due))
    n_ok = 0
    for row_id, name, url, start_date in due:
        try:
            rec = ingest_tournament(conn, row_id, name, url, start_date, today=today)
        except Exception as e:
            log("  FAILED %s (%s): %s" % (name, row_id, e))
            mark_failed(conn, row_id, e)
            continue
        if rec is None:
            log("  skipped %s (%s): under %d days old" % (name, row_id, MIN_AGE_DAYS))
            continue
        n_rooms = sum(len(rd.get("rooms") or []) for rd in rec["rounds"])
        log("  ingested %s (%s): %d rooms" % (name, row_id, n_rooms))
        pstats = rec.get("parse_stats") or {}
        dropped = {k: v for k, v in pstats.items() if k.endswith("_dropped") and v}
        if dropped:
            log("  WARNING %s (%s): parse drops %s" % (name, row_id, dropped))
        n_ok += 1
    return n_ok
