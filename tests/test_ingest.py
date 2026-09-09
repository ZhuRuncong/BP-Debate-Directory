from debate_ratings import ingest, sheet


class StubCursor:
    def __init__(self, conn):
        self.conn = conn
        self.rows = []

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def execute(self, q, params=None):
        q = " ".join(q.split())
        if q.startswith("SELECT row_id, source_url, name FROM tournaments"):
            self.rows = [(r, v["source_url"], v["name"])
                         for r, v in sorted(self.conn.t.items())]
        elif q.startswith("INSERT INTO tournaments"):
            rid, name, date, url, sc, fmt = params
            row = self.conn.t.get(rid)
            if row is None:
                self.conn.t[rid] = {"name": name, "start_date": date, "source_url": url,
                                    "speaking_class": sc, "format": fmt}
            else:
                row.update(name=name, source_url=url, speaking_class=sc, format=fmt)
                row["start_date"] = row.get("start_date") or date  # database wins
            self.rows = []

    def fetchall(self):
        return list(self.rows)


class StubConn:
    def __init__(self, t):
        self.t = t

    def cursor(self, name=None):
        return StubCursor(self)

    def commit(self):
        pass


def row(row_id, name, url, date=None):
    return {"row_id": row_id, "name": name, "url": url, "date": date,
            "speaking_class": "", "format": "BP", "to_skip": ""}


def test_sync_matches_by_url_when_the_sheet_shifts(monkeypatch):
    """A row inserted above shifts every row below; the URL must still match."""
    conn = StubConn({7: {"name": "Aberdeen Open 2023", "start_date": None,
                         "source_url": "http://tab/aberdeen/", "speaking_class": "",
                         "format": "BP"}})
    monkeypatch.setattr(sheet, "fetch_rows",
                        lambda: [row(2, "A New Event 2026", "http://tab/new/"),
                                 row(8, "Aberdeen Open 2023", "http://tab/aberdeen")])
    rows = ingest.sync_sheet(conn)
    assert conn.t[7]["name"] == "Aberdeen Open 2023"   # kept its id, not duplicated
    assert rows[1]["row_id"] == 7                       # ingest sees the database id
    assert len(conn.t) == 2
    assert conn.t[8]["name"] == "A New Event 2026"      # new row got a fresh id


def test_sync_keeps_a_researched_date(monkeypatch):
    conn = StubConn({7: {"name": "X", "start_date": "2024-05-01",
                         "source_url": "http://tab/x/", "speaking_class": "",
                         "format": "BP"}})
    monkeypatch.setattr(sheet, "fetch_rows",
                        lambda: [row(2, "X", "http://tab/x/", "2019-01-01")])
    ingest.sync_sheet(conn)
    assert conn.t[7]["start_date"] == "2024-05-01"


def test_iso_date_drops_half_typed_values():
    assert sheet.iso_date("2024-02-") is None
    assert sheet.iso_date("") is None
    assert sheet.iso_date("2024-02-03") == "2024-02-03"
