import datetime
import json

from . import db, fit, ingest, judges, payload, rooms, tagger
from .settings import MAX_ROOM_DROP, MAX_SPEAKER_DROP


class SanityError(RuntimeError):
    pass


def log(msg):
    print(msg, flush=True)


def last_snapshot(conn):
    with conn.cursor() as cur:
        cur.execute("SELECT n_rooms, n_speakers, summary FROM rating_snapshots "
                    "ORDER BY built_at DESC LIMIT 1")
        return cur.fetchone()


def publish_gate(conn, n_rooms, n_speakers):
    """Flag suspicious shrinkage vs the last snapshot before overwriting the payload."""
    prev = last_snapshot(conn)
    if not prev:
        return []
    prev_rooms, prev_speakers, _ = prev
    problems = []
    if prev_rooms and n_rooms < prev_rooms * (1 - MAX_ROOM_DROP):
        problems.append("rooms dropped %d -> %d (more than %.0f%%)"
                        % (prev_rooms, n_rooms, MAX_ROOM_DROP * 100))
    if prev_speakers and n_speakers < prev_speakers * (1 - MAX_SPEAKER_DROP):
        problems.append("speakers dropped %d -> %d (more than %.0f%%)"
                        % (prev_speakers, n_speakers, MAX_SPEAKER_DROP * 100))
    return problems


def rebuild(conn, log=log, publish_anyway=False, refit=False):
    tagger.tag_new_motions(conn, log=log)
    mm, occs, texts = fit.motion_map(conn)
    log("motion map: %d rounds -> %d distinct motions" % (len(mm), len(occs)))
    stamp = fit.fingerprint(conn, mm)
    cached = None if refit else fit.load_cache(conn, stamp)
    if cached:
        base_tab, abl_tab = cached
        with conn.cursor() as cur:
            cur.execute("SELECT count(*) FROM rooms")
            n_rooms = cur.fetchone()[0]
        stats = db.get_artifact(conn, "rooms_meta", {}).get("stats") or {}
        log("fit inputs unchanged: reusing cached fits over %d rooms" % n_rooms)
    else:
        with conn.cursor() as cur:
            cur.execute("SELECT payload FROM raw_judges ORDER BY row_id")
            judge_records = [p for (p,) in cur.fetchall()]
        judges_struct = judges.build(judge_records)
        db.set_artifact(conn, "judges", judges_struct)
        log("judges: %d identities" % len(judges_struct.get("d") or {}))
        n_rooms, scale, stats = rooms.rebuild_all(conn, judges_struct)
        log("rooms rebuilt: %d (speak scale %.3f)" % (n_rooms, scale))
        base_tab = fit.fit(conn, False, mm, log=log)
        abl_tab = fit.fit(conn, True, mm, log=log)
        fit.save_cache(conn, stamp, base_tab, abl_tab)

    built_at = datetime.datetime.now(datetime.UTC)
    built_date = built_at.date().isoformat()
    data, rest, rows_b, rows_a = payload.build(
        conn, base_tab, abl_tab, occs, texts, built_date, log=log)

    problems = publish_gate(conn, n_rooms, len(rows_b))
    if problems:
        if not publish_anyway:
            raise SanityError("refusing to publish: " + "; ".join(problems))
        log("publishing despite: " + "; ".join(problems))

    payload.store(conn, data, rest, built_at)
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO rating_snapshots (built_at, n_rooms, n_speakers, summary) "
            "VALUES (%s, %s, %s, %s)",
            (built_at, n_rooms, len(rows_b),
             json.dumps({"top10": [r["name"] for r in rows_b[:10]],
                         "room_stats": stats})))
    conn.commit()
    log("payload stored at %s" % built_at.isoformat())
    return rows_b


def run_pipeline(conn, force=False, fit_only=False, skip_ingest=False,
                 publish_anyway=False, refit=False, log=log):
    """Ingest then rebuild; skips the rebuild when nothing new arrived unless forced."""
    db.migrate(conn)
    n_new = 0
    if not (fit_only or skip_ingest):
        n_new = ingest.run(conn, log=log)
    if n_new == 0 and not (force or fit_only):
        log("nothing new ingested, skipping rebuild")
        return {"ingested": 0, "rebuilt": False}
    rows_b = rebuild(conn, log=log, publish_anyway=publish_anyway, refit=refit)
    return {"ingested": n_new, "rebuilt": True, "n_speakers": len(rows_b)}
