import datetime
import json

from . import sheet, tabbycat
from .settings import MIN_AGE_DAYS

RECOVERED_BASE = 90000  # ids above this are hand-recovered, not sheet rows


def url_key(u):
    return (u or "").strip().rstrip("/").lower()


def sync_sheet(conn):
    """Match rows to tournaments by URL, then name — never by sheet position.
    Inserting a row mid-sheet shifts every row below it, which would otherwise
    repoint each tournament at the next one's scraped data."""
    rows = sheet.fetch_rows()
    with conn.cursor() as cur:
        cur.execute("SELECT row_id, source_url, name FROM tournaments")
        known = cur.fetchall()
        by_url = {url_key(u): r for r, u, _ in known if u}
        by_name = {(n or "").strip(): r for r, _, n in known if n}
        next_id = max((r for r, _, _ in known if r < RECOVERED_BASE), default=1) + 1
        for r in rows:
            rid = by_url.get(url_key(r["url"])) or by_name.get((r["name"] or "").strip())
            if rid is None:
                rid, next_id = next_id, next_id + 1
            r["row_id"] = rid
            cur.execute(
                "INSERT INTO tournaments (row_id, name, start_date, source_url, "
                "speaking_class, format) VALUES (%s, %s, %s, %s, %s, %s) "
                "ON CONFLICT (row_id) DO UPDATE SET name = EXCLUDED.name, "
                # a researched date (via /dates, or from a tab) outranks the sheet
                "start_date = COALESCE(tournaments.start_date, EXCLUDED.start_date), "
                "source_url = EXCLUDED.source_url, "
                "speaking_class = EXCLUDED.speaking_class, format = EXCLUDED.format",
                (rid, r["name"], r["date"], r["url"],
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
