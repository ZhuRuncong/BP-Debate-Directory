"""Redaction and merge requests submitted through the site's Google Form."""
import base64
import collections
import datetime
import functools
import gzip
import hashlib
import itertools
import json
import re
import time
import unicodedata

import httpx
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding

from . import db
from .idnorm import canon
from .payload import HIDDEN_NAME
from .settings import FORM_SHEET_ID, GOOGLE_SERVICE_ACCOUNT

STATE = "form_requests"
SHEETS_API = "https://sheets.googleapis.com/v4/spreadsheets/"
SCOPE = "https://www.googleapis.com/auth/spreadsheets"


def b64(raw: bytes) -> bytes:
    return base64.urlsafe_b64encode(raw).rstrip(b"=")


def jwt(claims: dict, pem: str) -> str:
    """The RS256 assertion Google's token endpoint takes from a service account."""
    msg = b64(json.dumps({"alg": "RS256", "typ": "JWT"}).encode()) + b"." + b64(json.dumps(claims).encode())
    key = serialization.load_pem_private_key(pem.encode(), password=None)
    return (msg + b"." + b64(key.sign(msg, padding.PKCS1v15(), hashes.SHA256()))).decode()


class Sheet:
    """The form's response sheet, read and ticked through the Sheets API as a service account."""

    def __init__(self, sheet_id: str, account: dict, http=None):
        self.id = re.sub(r".*/d/([\w-]+).*", r"\1", sheet_id.strip())  # a pasted link works too
        self.account = account
        self.http = http or httpx.Client(timeout=60)
        self.token, self.expires = "", 0

    def auth(self) -> dict:
        now = int(time.time())
        if now > self.expires - 60:
            claims = {"iss": self.account["client_email"], "scope": SCOPE,
                      "aud": self.account["token_uri"], "iat": now, "exp": now + 3600}
            r = self.http.post(self.account["token_uri"], data={
                "grant_type": "urn:ietf:params:oauth:grant-type:jwt-bearer",
                "assertion": jwt(claims, self.account["private_key"])})
            r.raise_for_status()
            got = r.json()
            self.token, self.expires = got["access_token"], now + got["expires_in"]
        return {"Authorization": "Bearer " + self.token}

    def values(self) -> list[list[str]]:
        # No tab name, so the API reads the first visible tab, which is where form responses land.
        r = self.http.get(SHEETS_API + self.id + "/values/A:ZZ", headers=self.auth())
        r.raise_for_status()
        return r.json().get("values") or []

    def tick(self, cells: list[str]) -> None:
        r = self.http.post(SHEETS_API + self.id + "/values:batchUpdate", headers=self.auth(),
                           json={"valueInputOption": "RAW",
                                 "data": [{"range": c, "values": [[True]]} for c in cells]})
        r.raise_for_status()


def configured() -> bool:
    return bool(FORM_SHEET_ID and GOOGLE_SERVICE_ACCOUNT)


@functools.cache
def sheet() -> Sheet:
    if not configured():
        raise RuntimeError("the form needs FORM_SHEET_ID and GOOGLE_SERVICE_ACCOUNT set")
    return Sheet(FORM_SHEET_ID, json.loads(GOOGLE_SERVICE_ACCOUNT))


def column(i: int) -> str:
    """0 -> A, 25 -> Z, 26 -> AA."""
    s = ""
    i += 1
    while i:
        i, rem = divmod(i - 1, 26)
        s = chr(65 + rem) + s
    return s


def fold(name: str) -> str:
    """How people retype a name: case, accents and punctuation don't count, word breaks do."""
    d = unicodedata.normalize("NFKD", canon(name))
    return " ".join("".join(c if c.isalnum() else " " for c in d
                            if not unicodedata.combining(c)).split())


def parse(values: list[list[str]]) -> list[dict]:
    head = [str(h).strip().casefold() for h in values[0]] if values else []

    def col(word):
        return next((i for i, h in enumerate(head) if word in h), None)

    at, kind, done = col("timestamp"), col("request type"), col("done")
    names = [i for i, h in enumerate(head) if "name" in h]
    if at is None or kind is None or done is None or not names:
        raise RuntimeError("form sheet header drift: %s" % head)
    out = []
    for n, r in enumerate(values[1:], start=2):
        r = [str(c) for c in r] + [""] * (len(head) - len(r))
        if not r[at].strip():
            continue
        req = {"at": r[at].strip(), "kind": r[kind].strip(),
               "names": [r[i].strip() for i in names if r[i].strip()]}
        # Keyed by content, so correcting a name in the sheet resubmits the row.
        blob = json.dumps([req["at"], req["kind"], req["names"]]).encode("utf-8")
        req["id"] = hashlib.sha1(blob).hexdigest()[:12]
        req["done"] = r[done].strip().casefold() == "true"
        req["cell"] = "%s%d" % (column(done), n)
        out.append(req)
    return out


def fetch() -> list[dict]:
    return parse(sheet().values())


def waiting(conn, reqs: list[dict]) -> list[dict]:
    """Unticked rows a run should look at: new requests, and completed ones not yet ticked."""
    seen = db.get_artifact(conn, STATE, {})
    return [r for r in reqs if not r["done"] and (r["id"] not in seen or (
        seen[r["id"]]["status"] == "done" and not seen[r["id"]].get("ticked")))]


def builds(conn) -> int:
    """Publishes so far; counting them orders applies and publishes without trusting the clock."""
    with conn.cursor() as cur:
        cur.execute("SELECT count(*) FROM rating_snapshots")
        return cur.fetchone()[0]


def live(entry: dict, n_builds: int) -> bool:
    """Whether a request's change is on the site: a publish has happened since it was applied."""
    return not (entry.get("hid") or entry.get("merged")) or n_builds > entry["build"]


class Site:
    """Everyone the published site shows, found by any spelling it displays for them."""

    def __init__(self, conn, merges: dict, with_tours: bool):
        with conn.cursor() as cur:
            cur.execute("SELECT name, body FROM payloads WHERE encoding = 'gz'")
            blobs = {name: bytes(body) for name, body in cur.fetchall()}
        if "data" not in blobs:
            raise RuntimeError("nothing published yet to match names against")
        data = json.loads(gzip.decompress(blobs["data"]))
        # rest parses to ~250 MB and only the merge checks need it
        rest = json.loads(gzip.decompress(blobs["rest"])) if with_tours else {}
        self.merges = merges
        self.tour_names = [t["n"] for t in data["tournaments"]]
        self.by_name = collections.defaultdict(set)
        self.tours = collections.defaultdict(set)
        careers = rest.get("careers") or {}
        for i, p in enumerate(data["players"]):
            if p[0] != HIDDEN_NAME:
                self.add(self.key(p[0]), [p[0], *data["aliases"].get(str(i), [])],
                         (e[0] for e in careers.get(str(i), ())))
        for n, sat in zip(data["jn"], rest.get("jc") or [()] * len(data["jn"]), strict=True):
            self.add(self.key(n), [n], (r[0] for r in sat))

    def add(self, k: str, names: list, tours) -> None:
        for n in (k, *names):
            self.by_name[fold(n)].add(k)
        self.tours[k].update(tours)

    def key(self, name: str) -> str:
        k = canon(name)
        return self.merges.get(k, k)

    def find(self, name: str) -> set:
        f = fold(name)
        return {self.merges.get(k, k) for k in self.by_name.get(f, ())} if f else set()


def redact(site: Site, hidden: dict, names: list) -> dict:
    have = set(hidden["players"])
    gone = {fold(k) for h in have for k in (h, site.merges.get(h, h))}
    keys, missing = set(), []
    for n in names:
        found = site.find(n)
        keys |= found
        if not found and not {fold(n), fold(site.key(n))} & gone:
            missing.append(n)
    hidden["players"] = sorted(have | keys)
    out = {"status": "review" if missing else "done", "hid": sorted(keys - have)}
    if missing:
        out["note"] = "not on the site: " + ", ".join(missing)
    return out


def merge(site: Site, names: list) -> dict:
    found = [site.find(n) for n in names]
    missing = [n for n, f in zip(names, found, strict=True) if not f]
    if missing:
        return {"status": "review", "note": "not on the site: " + ", ".join(missing)}
    keys = list(dict.fromkeys(k for f in found for k in sorted(f)))
    if len(keys) < 2:
        return {"status": "done", "merged": {}, "note": "already one profile"}
    for a, b in itertools.combinations(keys, 2):
        both = site.tours[a] & site.tours[b]
        if both:
            # one person is never two entries at the same tournament
            return {"status": "review", "note": "%s and %s were both at %s"
                    % (a, b, site.tour_names[min(both)])}
    # The full name used at the most tournaments survives; "Ojas" + "Ojas Date" shows as Ojas Date.
    target = max(keys, key=lambda k: (len(fold(k).split()) > 1, len(site.tours[k]), -keys.index(k)))
    words = set(fold(target).split())
    strangers = [k for k in keys if not words & set(fold(k).split())]
    if strangers:
        return {"status": "review", "note": "%s shares no name with %s"
                % (", ".join(strangers), target)}
    moved = {k: target for k in keys if k != target}
    for variant, root in list(site.merges.items()):
        if root in moved:
            site.merges[variant] = target  # resolution is single-step, so no chains
    site.merges.update(moved)
    for k in moved:
        site.tours[target] |= site.tours.pop(k, set())
    return {"status": "done", "merged": moved}


def act(site: Site, hidden: dict, req: dict) -> dict:
    kind = req["kind"].casefold()
    if kind.startswith("redact"):
        return redact(site, hidden, req["names"])
    if kind.startswith("merge"):
        return merge(site, req["names"])
    return {"status": "review", "note": "unknown request type"}


def apply(conn, reqs: list[dict], log=print) -> int:
    """Act on new requests; returns how many applied changes are not on the site yet."""
    state = db.get_artifact(conn, STATE, {})
    n = builds(conn)
    todo = [r for r in reqs if not r["done"] and r["id"] not in state]
    if todo:
        hidden = db.get_artifact(conn, "hidden", {"players": [], "institutions": []})
        hidden.setdefault("players", [])
        merges = db.get_artifact(conn, "id_merges", {})
        before = (list(hidden["players"]), dict(merges))
        site = Site(conn, merges, with_tours=any(r["kind"].casefold().startswith("merge") for r in todo))
        now = datetime.datetime.now(datetime.UTC).isoformat(timespec="seconds")
        for r in todo:
            out = act(site, hidden, r)
            state[r["id"]] = {"at": r["at"], "kind": r["kind"], "names": r["names"],
                              "processed": now, "build": n, **out}
            log("form: %s %s -> %s" % (r["kind"], r["names"], json.dumps(out, ensure_ascii=False)))
        if hidden["players"] != before[0]:
            db.set_artifact(conn, "hidden", hidden)
        if merges != before[1]:
            db.set_artifact(conn, "id_merges", merges)
        db.set_artifact(conn, STATE, state)
    return sum(not live(e, n) for e in state.values())


def tick(conn, log=print) -> int:
    """Tick Done on each row whose request is complete and on the site."""
    state = db.get_artifact(conn, STATE, {})
    n = builds(conn)
    due = [r for r in fetch() if r["id"] in state and state[r["id"]]["status"] == "done"
           and not state[r["id"]].get("ticked") and live(state[r["id"]], n)]
    cells = [r["cell"] for r in due if not r["done"]]
    if cells:
        sheet().tick(cells)
        log("form: ticked Done in %s" % ", ".join(cells))
    for r in due:
        state[r["id"]]["ticked"] = True
    if due:
        db.set_artifact(conn, STATE, state)
    return len(cells)
