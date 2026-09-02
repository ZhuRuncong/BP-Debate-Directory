import csv
import io

import httpx

from .settings import SHEET_CSV_URL, USER_AGENT

# To flag data drift
REQUIRED_COLUMNS = {"tournament", "home_url", "year", "date", "format", "speaking_class"}
SKIP_URL_PARTS = ("docs.google", "people.hws.edu", "wsdc2018.com",
                  "paullau.wordpress", "web.archive.org")


def fetch_rows():
    r = httpx.get(SHEET_CSV_URL, follow_redirects=True, timeout=60,
                  headers={"User-Agent": USER_AGENT})
    r.raise_for_status()
    reader = csv.DictReader(io.StringIO(r.text))
    missing = REQUIRED_COLUMNS - set(reader.fieldnames or [])
    if missing:
        raise RuntimeError("sheet header drift, missing columns: %s" % sorted(missing))
    out = []
    for i, row in enumerate(reader):
        name = (row.get("tournament") or "").strip()
        url = (row.get("home_url") or "").strip()
        if not name:
            continue
        out.append({
            "row_id": i + 2,
            "name": name,
            "url": url,
            "date": (row.get("date") or "").strip() or None,
            "format": (row.get("format") or "").strip(),
            "speaking_class": (row.get("speaking_class") or "").strip(),
            "to_skip": (row.get("to_skip") or "").strip(),
        })
    return out


def crawlable(row):
    if not row["url"] or not row["url"].startswith("http"):
        return False
    if any(x in row["url"] for x in SKIP_URL_PARTS):
        return False
    if row["to_skip"]:
        return False
    return True
