import datetime
import hashlib
import json


class FakeCopy:
    def __init__(self, conn):
        self.conn = conn

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def write_row(self, row):
        row_id, t, stage, payload = row
        self.conn.rooms.append((row_id, t, stage, json.loads(payload)))


class FakeCursor:
    def __init__(self, conn):
        self.conn = conn
        self.rows = []
        self.itersize = 0

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def execute(self, q, params=None):
        self.rows = self.conn.query(" ".join(q.split()), params)

    def fetchone(self):
        return self.rows[0] if self.rows else None

    def fetchall(self):
        return list(self.rows)

    def __iter__(self):
        return iter(self.rows)

    def copy(self, q):
        return FakeCopy(self.conn)


class FakeConn:
    def __init__(self, raw_tabs=None, raw_motions=None, raw_judges=None,
                 extra_games=None, artifacts=None, tournaments=None):
        self.raw_tabs = dict(raw_tabs or {})
        self.raw_motions = dict(raw_motions or {})
        self.raw_judges = dict(raw_judges or {})
        self.extra_games = list(extra_games or [])
        self.artifacts = dict(artifacts or {})
        self.tournaments = dict(tournaments or {})
        self.rooms = []
        self.payloads = {}
        self.snapshots = []
        self.model_files = {}
        self.commits = 0

    def cursor(self, name=None):
        return FakeCursor(self)

    def commit(self):
        self.commits += 1

    def close(self):
        pass

    def query(self, q, params):
        if q.startswith("CREATE TABLE") or q.startswith("\nCREATE TABLE"):
            return []
        if "FROM artifacts WHERE name" in q:
            name = params[0]
            if name in self.artifacts:
                return [(self.artifacts[name],)]
            return []
        if "md5(coalesce(string_agg(" in q:
            table = q.rsplit(" FROM ", 1)[1].strip()
            rows = {"raw_tabs": self.raw_tabs, "raw_judges": self.raw_judges,
                    "extra_games": self.extra_games, "tournaments": self.tournaments}[table]
            blob = json.dumps(rows, sort_keys=True, default=str).encode("utf-8")
            return [(len(rows), hashlib.md5(blob).hexdigest())]
        if q.startswith("INSERT INTO model_files"):
            model, name, body = params
            self.model_files[(model, name)] = bytes(body)
            return []
        if q.startswith("SELECT count(*) FROM rooms"):
            return [(len(self.rooms),)]
        if q.startswith("INSERT INTO artifacts"):
            self.artifacts[params[0]] = json.loads(params[1])
            return []
        if "SELECT payload FROM raw_tabs" in q:
            return [(self.raw_tabs[k],) for k in sorted(self.raw_tabs)]
        if "row_id, payload FROM raw_motions" in q:
            return [(k, self.raw_motions[k]) for k in sorted(self.raw_motions)]
        if "SELECT payload FROM raw_motions" in q:
            return [(self.raw_motions[k],) for k in sorted(self.raw_motions)]
        if "SELECT payload FROM raw_judges" in q:
            return [(self.raw_judges[k],) for k in sorted(self.raw_judges)]
        if "SELECT DISTINCT row_id FROM rooms" in q:
            return [(r,) for r in sorted({r[0] for r in self.rooms})]
        if "SELECT payload FROM rooms" in q:
            return [(r[3],) for r in sorted(self.rooms, key=lambda r: r[0])]
        if q.startswith("DELETE FROM rooms"):
            self.rooms = []
            return []
        if "FROM extra_games" in q:
            games = self.extra_games
            if params:
                games = [g for g in games if g.get("source") in params[0]]
            return [(dict(g),) for g in sorted(games, key=lambda g: g["row"])]
        if q.startswith("INSERT INTO payloads"):
            name, encoding, built_at, body = params
            self.payloads[(name, encoding)] = (built_at, body)
            return []
        if q.startswith("INSERT INTO rating_snapshots"):
            self.snapshots.append(params)
            return []
        if "n_rooms, n_speakers, summary FROM rating_snapshots" in q:
            if not self.snapshots:
                return []
            last = max(self.snapshots, key=lambda s: s[0])
            return [(last[1], last[2], json.loads(last[3]))]
        if "SELECT name, body FROM model_files" in q:
            return [(n, b) for (m, n), b in self.model_files.items() if m == params[0]]
        if "SELECT row_id, name FROM tournaments WHERE row_id = ANY" in q:
            return [(k, self.tournaments[k].get("name")) for k in sorted(self.tournaments)
                    if k in (params[0] or [])]
        if "SELECT name FROM tournaments WHERE row_id" in q:
            t = self.tournaments.get(params[0])
            return [(t.get("name"),)] if t else []
        if "SELECT row_id, speaking_class FROM tournaments" in q:
            return [(k, v.get("speaking_class")) for k, v in sorted(self.tournaments.items())]
        if "FROM tournaments WHERE start_date IS NULL" in q:
            out = []
            for k in sorted(self.tournaments):
                v = self.tournaments[k]
                if v.get("start_date") is None:
                    out.append((k, v.get("name"), v.get("source_url"),
                                v.get("status"), v.get("error")))
            return out
        if q.startswith("UPDATE tournaments SET start_date"):
            date, row_id = params
            t = self.tournaments.get(row_id)
            if t is None or t.get("start_date") is not None:
                return []
            t["start_date"] = datetime.date.fromisoformat(date)
            if t.get("status") == "failed":
                t["status"] = "pending"
                t["error"] = None
            return [(row_id,)]
        raise AssertionError("unhandled query: %s" % q)


class G:
    def __init__(self, mu, sigma):
        self.mu = mu
        self.sigma = sigma


class FakeTab:
    def __init__(self, curves, motion_bias=None):
        self._curves = curves
        self._motion_bias = motion_bias or {}

    def curves(self):
        return self._curves

    def motion_bias(self):
        return self._motion_bias
