import datetime
import hmac
import json
import os
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from . import db, form, pipeline
from .idnorm import canon
from .rooms import TEAM_RENAME
from .settings import FORM_POLL_MINUTES

RUN_OPTIONS = ("force", "fit_only", "skip_ingest", "publish_anyway", "refit", "requests")
LOG_TAIL = 200
KINDS = ("player", "institution")
HIDDEN_EMPTY = {"players": [], "institutions": []}


def utcnow():
    return datetime.datetime.now(datetime.UTC).isoformat()


def norm_key(kind: str, value: str) -> str:
    """Match how the pipeline keys each kind: canon() for people, lowercase for institutions."""
    return canon(value) if kind == "player" else (value or "").strip().lower()


class Job:
    def __init__(self, options: dict):
        self.options = options
        self.state = "running"
        self.error = None
        self.result = None
        self.started = utcnow()
        self.finished = None
        self.lines = []
        self.lock = threading.Lock()

    def log(self, msg):
        with self.lock:
            self.lines.append(str(msg))
        pipeline.log(msg)

    def status(self) -> dict:
        with self.lock:
            return {"state": self.state, "options": self.options,
                    "started": self.started, "finished": self.finished,
                    "error": self.error, "result": self.result,
                    "log": self.lines[-LOG_TAIL:]}


class Runner:
    """Runs at most one pipeline job at a time on a background thread."""

    def __init__(self, connect):
        self.connect = connect
        self.lock = threading.Lock()
        self.job = None

    def start(self, options: dict):
        with self.lock:
            if self.job and self.job.state == "running":
                return None
            self.job = Job(options)
            threading.Thread(target=self._run, args=(self.job,), daemon=True).start()
            return self.job

    def _run(self, job: Job):
        conn = None
        try:
            conn = self.connect()
            job.result = pipeline.run_pipeline(conn, log=job.log, **job.options)
            job.state = "done"
        except Exception as e:
            job.error = "%s: %s" % (type(e).__name__, e)
            job.state = "failed"
        finally:
            job.finished = utcnow()
            if conn is not None:
                conn.close()

    def status(self) -> dict:
        with self.lock:
            return self.job.status() if self.job else {"state": "idle"}


class App:
    def __init__(self, connect=db.connect):
        self.connect = connect
        self.runner = Runner(connect)

    def trigger_run(self, body: dict):
        unknown = set(body) - set(RUN_OPTIONS)
        if unknown:
            return 400, {"error": "unknown options: %s" % sorted(unknown)}
        options = {}
        for k in RUN_OPTIONS:
            v = body.get(k, False)
            if not isinstance(v, bool):
                return 400, {"error": "%s must be a boolean" % k}
            options[k] = v
        job = self.runner.start(options)
        if job is None:
            return 409, {"error": "a run is already in progress"}
        return 202, {"state": "running", "options": options}

    def run_status(self):
        return 200, self.runner.status()

    def check_requests(self):
        """Start a run when the form has requests to act on or rows to tick."""
        reqs = form.fetch()
        conn = self.connect()
        try:
            due = form.waiting(conn, reqs)
        finally:
            conn.close()
        return self.trigger_run({"requests": True, "skip_ingest": True}) if due else None

    def list_requests(self):
        """Every request still in the sheet, newest first; review items stay open until ticked Done."""
        try:
            live = form.fetch()
        except Exception as e:
            return 502, {"error": "could not read the form sheet: %s" % e}
        conn = self.connect()
        try:
            state = db.get_artifact(conn, form.STATE, {})
        finally:
            conn.close()
        rows = [dict(state[r["id"]], id=r["id"], ticked=r["done"])
                for r in reversed(live) if r["id"] in state]
        return 200, {"needs_review": [r for r in rows if r["status"] == "review" and not r["ticked"]],
                     "requests": rows}

    def missing_dates(self):
        conn = self.connect()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT row_id, name, source_url, status, error FROM tournaments "
                    "WHERE start_date IS NULL ORDER BY row_id")
                rows = [{"row_id": r, "name": n, "url": u, "status": s, "error": e}
                        for r, n, u, s, e in cur.fetchall()]
        finally:
            conn.close()
        return 200, {"count": len(rows), "missing": rows}

    def fill_dates(self, body: dict):
        """Backfill start dates; a filled date also un-fails the tournament for retry."""
        if not body:
            return 400, {"error": 'expected {"<row_id>": "YYYY-MM-DD", ...}'}
        parsed = {}
        for k, v in body.items():
            try:
                parsed[int(k)] = datetime.date.fromisoformat(v)
            except (TypeError, ValueError):
                return 400, {"error": "bad entry %r: %r" % (k, v)}
        updated, skipped = [], []
        conn = self.connect()
        try:
            with conn.cursor() as cur:
                for row_id, date in sorted(parsed.items()):
                    cur.execute(
                        "UPDATE tournaments SET start_date = %s, "
                        "status = CASE WHEN status = 'failed' THEN 'pending' "
                        "ELSE status END, "
                        "error = CASE WHEN status = 'failed' THEN NULL "
                        "ELSE error END "
                        "WHERE row_id = %s AND start_date IS NULL RETURNING row_id",
                        (date.isoformat(), row_id))
                    (updated if cur.fetchone() else skipped).append(row_id)
            conn.commit()
        finally:
            conn.close()
        return 200, {"updated": updated, "skipped": skipped}


    def _alias_map(self, conn, kind):
        if kind == "player":
            return db.get_artifact(conn, "id_merges", {})
        return db.get_artifact(conn, "inst_alias", {"skip": [], "alias": {}})["alias"]

    def _save_alias(self, conn, kind, amap):
        if kind == "player":
            return db.set_artifact(conn, "id_merges", amap)
        payload = db.get_artifact(conn, "inst_alias", {"skip": [], "alias": {}})
        payload["alias"] = amap
        db.set_artifact(conn, "inst_alias", payload)

    def list_aliases(self):
        conn = self.connect()
        try:
            return 200, {k: self._alias_map(conn, k) for k in KINDS}
        finally:
            conn.close()

    def add_alias(self, body: dict):
        """Point one spelling at another; resolution is single-step, so chains are rejected."""
        kind = body.get("kind")
        if kind not in KINDS:
            return 400, {"error": "kind must be one of %s" % (KINDS,)}
        alias, target = body.get("alias"), body.get("target")
        if not isinstance(alias, str) or not isinstance(target, str):
            return 400, {"error": "alias and target must be strings"}
        key = norm_key(kind, alias)
        # institutions display the target verbatim; people are keyed by canon()
        value = target.strip() if kind == "institution" else canon(target)
        if not key or not value:
            return 400, {"error": "alias and target must be non-empty"}
        # an institution may alias to itself to fix its displayed capitalisation ("Knust" -> "KNUST")
        rename = kind == "institution" and key == norm_key(kind, target)
        if key == norm_key(kind, target) and not rename:
            return 400, {"error": "alias and target are the same key"}
        conn = self.connect()
        try:
            amap = self._alias_map(conn, kind)
            vkey = norm_key(kind, value)
            if not rename and vkey in amap and norm_key(kind, amap[vkey]) != vkey:
                return 409, {"error": "target %r is itself an alias of %r"
                             % (value, amap[norm_key(kind, value)])}
            existing = [a for a, v in amap.items() if norm_key(kind, v) == key and a != key]
            if existing and not rename:
                return 409, {"error": "%r is already the target of %s"
                             % (alias, sorted(existing))}
            before = amap.get(key)
            amap[key] = value
            self._save_alias(conn, kind, amap)
        finally:
            conn.close()
        return 200, {"kind": kind, "alias": key, "target": value,
                     "replaced": before, "applies_at": "next rebuild"}

    def remove_alias(self, body: dict):
        kind = body.get("kind")
        if kind not in KINDS:
            return 400, {"error": "kind must be one of %s" % (KINDS,)}
        key = norm_key(kind, body.get("alias") or "")
        if not key:
            return 400, {"error": "alias must be a non-empty string"}
        conn = self.connect()
        try:
            amap = self._alias_map(conn, kind)
            if key not in amap:
                return 404, {"error": "no alias %r" % key}
            removed = amap.pop(key)
            self._save_alias(conn, kind, amap)
        finally:
            conn.close()
        return 200, {"kind": kind, "alias": key, "was": removed,
                     "applies_at": "next rebuild"}

    def list_hidden(self):
        conn = self.connect()
        try:
            return 200, db.get_artifact(conn, "hidden", dict(HIDDEN_EMPTY))
        finally:
            conn.close()

    def set_hidden(self, body: dict, hide: bool):
        kind = body.get("kind")
        if kind not in KINDS:
            return 400, {"error": "kind must be one of %s" % (KINDS,)}
        key = norm_key(kind, body.get("key") or "")
        if not key:
            return 400, {"error": "key must be a non-empty string"}
        field = "players" if kind == "player" else "institutions"
        conn = self.connect()
        try:
            hidden = db.get_artifact(conn, "hidden", dict(HIDDEN_EMPTY))
            keys = set(hidden.get(field) or [])
            if hide == (key in keys):
                return 200, {"kind": kind, "key": key, "hidden": hide,
                             "changed": False, "applies_at": "next rebuild"}
            if hide:
                keys.add(key)
            else:
                keys.discard(key)
            hidden[field] = sorted(keys)
            hidden.setdefault("players", [])
            hidden.setdefault("institutions", [])
            db.set_artifact(conn, "hidden", hidden)
        finally:
            conn.close()
        return 200, {"kind": kind, "key": key, "hidden": hide,
                     "changed": True, "applies_at": "next rebuild"}


    def list_excluded(self):
        conn = self.connect()
        try:
            rows = db.get_artifact(conn, "excluded_rows", [])
            with conn.cursor() as cur:
                cur.execute("SELECT row_id, name FROM tournaments WHERE row_id = ANY(%s)",
                            (list(rows),))
                names = dict(cur.fetchall())
            return 200, {"count": len(rows),
                         "excluded": [{"row_id": r, "name": names.get(r)}
                                      for r in sorted(rows)]}
        finally:
            conn.close()

    def set_excluded(self, body: dict, drop: bool):
        row_id = body.get("row_id")
        if not isinstance(row_id, int) or isinstance(row_id, bool):
            return 400, {"error": "row_id must be an integer"}
        conn = self.connect()
        try:
            with conn.cursor() as cur:
                cur.execute("SELECT name FROM tournaments WHERE row_id = %s", (row_id,))
                found = cur.fetchone()
            if not found:
                return 404, {"error": "no tournament with row_id %d" % row_id}
            rows = set(db.get_artifact(conn, "excluded_rows", []))
            if drop == (row_id in rows):
                return 200, {"row_id": row_id, "name": found[0], "excluded": drop,
                             "changed": False, "applies_at": "next rebuild"}
            if drop:
                rows.add(row_id)
            else:
                rows.discard(row_id)
            db.set_artifact(conn, "excluded_rows", sorted(rows))
        finally:
            conn.close()
        return 200, {"row_id": row_id, "name": found[0], "excluded": drop,
                     "changed": True, "applies_at": "next rebuild"}


    def recrawl(self, body: dict):
        """Queue one tournament to be fetched again and overwritten on the next normal run."""
        row_id = body.get("row_id")
        if not isinstance(row_id, int) or isinstance(row_id, bool):
            return 400, {"error": "row_id must be an integer"}
        conn = self.connect()
        try:
            with conn.cursor() as cur:
                cur.execute("UPDATE tournaments SET status = 'pending', error = NULL "
                            "WHERE row_id = %s RETURNING name", (row_id,))
                found = cur.fetchone()
            conn.commit()
        finally:
            conn.close()
        if not found:
            return 404, {"error": "no tournament with row_id %d" % row_id}
        return 200, {"row_id": row_id, "name": found[0], "applies_at": "next normal run"}

    def list_roster_fixes(self):
        conn = self.connect()
        try:
            return 200, db.get_artifact(conn, "roster_fixes", {})
        finally:
            conn.close()

    def set_roster_fix(self, body: dict, add: bool):
        """Name a placeholder speaker, or rename the team ("rename"), at one tournament."""
        row_id, team, placeholder = body.get("row_id"), body.get("team"), body.get("placeholder")
        name = body.get("name") if add else ""
        if body.get("rename"):
            placeholder, name = TEAM_RENAME, body["rename"] if add else ""
        if not isinstance(row_id, int) or isinstance(row_id, bool):
            return 400, {"error": "row_id must be an integer"}
        if not all(isinstance(v, str) and v.strip() for v in (team, placeholder)) or \
                (add and not (isinstance(name, str) and name.strip())):
            return 400, {"error": "team, placeholder%s must be non-empty strings" % (" and name" if add else "")}
        conn = self.connect()
        try:
            with conn.cursor() as cur:
                cur.execute("SELECT name FROM tournaments WHERE row_id = %s", (row_id,))
                found = cur.fetchone()
            if not found:
                return 404, {"error": "no tournament with row_id %d" % row_id}
            fixes = db.get_artifact(conn, "roster_fixes", {})
            team_fixes = fixes.setdefault(str(row_id), {}).setdefault(team.strip(), {})
            if add:
                team_fixes[placeholder.strip()] = name.strip()
            elif team_fixes.pop(placeholder.strip(), None) is None:
                return 404, {"error": "no fix for %r on %r" % (placeholder, team)}
            fixes = {r: {t: m for t, m in ts.items() if m} for r, ts in fixes.items()}
            db.set_artifact(conn, "roster_fixes", {r: ts for r, ts in fixes.items() if ts})  # drop emptied
        finally:
            conn.close()
        return 200, {"row_id": row_id, "tournament": found[0], "team": team.strip(),
                     "placeholder": placeholder.strip(), "name": name.strip() or None,
                     "applies_at": "next rebuild"}


class Handler(BaseHTTPRequestHandler):
    app = None
    token = None

    def send_json(self, code: int, obj: dict):
        body = json.dumps(obj).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def authorized(self) -> bool:
        got = self.headers.get("Authorization") or ""
        want = "Bearer " + self.token
        return hmac.compare_digest(got.encode("utf-8"), want.encode("utf-8"))

    def read_body(self) -> dict:
        n = int(self.headers.get("Content-Length") or 0)
        body = json.loads(self.rfile.read(n).decode("utf-8")) if n else {}
        if not isinstance(body, dict):
            raise ValueError("body must be a JSON object")
        return body

    def do_GET(self):
        if self.path == "/health":
            return self.send_json(200, {"ok": True})  # unauthenticated, for probes
        if not self.authorized():
            return self.send_json(401, {"error": "unauthorized"})
        if self.path == "/status":
            return self.send_json(*self.app.run_status())
        if self.path == "/missing-dates":
            return self.send_json(*self.app.missing_dates())
        if self.path == "/aliases":
            return self.send_json(*self.app.list_aliases())
        if self.path == "/hidden":
            return self.send_json(*self.app.list_hidden())
        if self.path == "/excluded":
            return self.send_json(*self.app.list_excluded())
        if self.path == "/requests":
            return self.send_json(*self.app.list_requests())
        if self.path == "/roster-fixes":
            return self.send_json(*self.app.list_roster_fixes())
        return self.send_json(404, {"error": "not found"})

    def do_POST(self):
        if not self.authorized():
            return self.send_json(401, {"error": "unauthorized"})
        try:
            body = self.read_body()
        except ValueError:
            return self.send_json(400, {"error": "invalid JSON body"})
        if self.path == "/run":
            return self.send_json(*self.app.trigger_run(body))
        if self.path == "/dates":
            return self.send_json(*self.app.fill_dates(body))
        if self.path == "/alias":
            return self.send_json(*self.app.add_alias(body))
        if self.path == "/alias/remove":
            return self.send_json(*self.app.remove_alias(body))
        if self.path == "/hide":
            return self.send_json(*self.app.set_hidden(body, True))
        if self.path == "/unhide":
            return self.send_json(*self.app.set_hidden(body, False))
        if self.path == "/exclude":
            return self.send_json(*self.app.set_excluded(body, True))
        if self.path == "/unexclude":
            return self.send_json(*self.app.set_excluded(body, False))
        if self.path == "/recrawl":
            return self.send_json(*self.app.recrawl(body))
        if self.path == "/roster-fix":
            return self.send_json(*self.app.set_roster_fix(body, True))
        if self.path == "/roster-fix/remove":
            return self.send_json(*self.app.set_roster_fix(body, False))
        return self.send_json(404, {"error": "not found"})

    def log_message(self, fmt, *args):
        print("api: " + fmt % args, flush=True)


def poll_form(app, minutes):
    while True:
        try:
            res = app.check_requests()
            if res:
                print("form: new requests, run returned %d" % res[0], flush=True)
        except Exception as e:
            print("form poll failed: %s: %s" % (type(e).__name__, e), flush=True)
        time.sleep(minutes * 60)


def main():
    token = os.environ.get("ADMIN_TOKEN")
    if not token:
        print("ADMIN_TOKEN is required", flush=True)
        return 1
    port = int(os.environ.get("PORT", "8090"))
    Handler.app = App()
    Handler.token = token
    if FORM_POLL_MINUTES > 0 and form.configured():
        threading.Thread(target=poll_form, args=(Handler.app, FORM_POLL_MINUTES), daemon=True).start()
    else:
        print("form polling off", flush=True)
    server = ThreadingHTTPServer(("0.0.0.0", port), Handler)
    print("admin api listening on :%d" % port, flush=True)
    server.serve_forever()


if __name__ == "__main__":
    raise SystemExit(main())
