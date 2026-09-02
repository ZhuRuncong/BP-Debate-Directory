import collections
import html
import json
import re
from urllib.parse import urlparse

import httpx

from .settings import USER_AGENT

HEADERS = {"User-Agent": USER_AGENT}

_client = None


def client() -> httpx.Client:
    global _client
    if _client is None:
        _client = httpx.Client(headers=HEADERS, timeout=40, follow_redirects=True)
    return _client


def get(url, timeout=40):
    try:
        r = client().get(url, timeout=timeout)
    except httpx.TransportError:
        r = client().get(url, timeout=timeout)  # one retry for flaky old hosts
    r.raise_for_status()
    return r.text


def get_json(url, timeout=40):
    return json.loads(get(url, timeout))


def tables_data(page):
    """Extract the tablesData JSON Tabbycat embeds in its page scripts."""
    i = page.find("tablesData:")
    if i < 0:
        return None
    seg = page[i + 11:].lstrip()
    try:
        val, _ = json.JSONDecoder().raw_decode(seg)
        return val if isinstance(val, list) else None
    except Exception:
        return None


TAG = re.compile(r"<[^>]+>")


def strip_html(s):
    return html.unescape(TAG.sub("", s or "")).strip()


def parse_results_page(page, stats=None):
    arr = tables_data(page)
    if not arr:
        return None
    out = []
    for tb in arr:
        heads = [(h.get("key") or h.get("title") or "") for h in tb.get("head", [])]
        lower = [str(h).lower() for h in heads]

        def col(*names):
            for n in names:
                if n in lower:
                    return lower.index(n)
            return None

        c_team, c_res, c_side = col("team"), col("result"), col("side")
        c_ballot = col("ballot")
        if c_team is None or c_res is None:
            continue
        rows = []
        for row in tb.get("data", []):
            try:
                team = row[c_team].get("text") if isinstance(row[c_team], dict) else row[c_team]
                rc = row[c_res] if isinstance(row[c_res], dict) else {}
                sort = rc.get("sort")
                text = strip_html(str(rc.get("text", "")))
                pop = (rc.get("popover") or {}).get("title") or ""
                side = ""
                if c_side is not None:
                    sc = row[c_side]
                    side = strip_html(sc.get("text") if isinstance(sc, dict) else str(sc))
                debate = None
                if c_ballot is not None and isinstance(row[c_ballot], dict):
                    m = re.search(r"/debate/(\d+)/", row[c_ballot].get("link") or "")
                    if m:
                        debate = int(m.group(1))
                opp = None
                m = re.match(r"(Won against|Lost to|Drew with) (.+)", pop)
                if m:
                    opp = (m.group(1), m.group(2))
                roster = None
                if isinstance(row[c_team], dict):
                    cont = ((row[c_team].get("popover") or {}).get("content") or [])
                    if cont and "link" not in cont[0]:
                        roster = strip_html(cont[0].get("text", ""))
                mates = None
                for c in ((rc.get("popover") or {}).get("content") or []):
                    t = str(c.get("text") or "")
                    if "Teams in debate" in t:
                        parts = re.split(r"<br\s*/?>", t)[1:]
                        names = [re.sub(r"\s*\((?:OG|OO|CG|CO|Aff|Neg|Affirmative|Negative)\)\s*$",
                                        "", strip_html(x)).strip() for x in parts]
                        names = [x for x in names if x]
                        if len(names) >= 2:
                            mates = names
                        break
                rows.append({"team": strip_html(str(team)), "sort": sort, "text": text,
                             "side": side, "debate": debate, "opp": opp, "roster": roster,
                             "mates": mates})
            except Exception:
                if stats is not None:
                    stats["result_rows_dropped"] += 1
                continue
        if stats is not None:
            stats["result_rows_parsed"] += len(rows)
        if rows:
            out.append(rows)
    return out


def norm(s):
    return re.sub(r"\s+", " ", (s or "")).strip().casefold()


# Plausible sort multisets for a 4-team room: BP ranks or advancing/eliminated.
VALID4 = ([1, 2, 3, 4], [1, 1, 2, 2], [1, 1, 1, 2])


def group_rooms(rows):
    """Group a round's result rows into rooms, trying signals from strongest
    (explicit debate links) to weakest (consecutive-row heuristics)."""
    if all(r["debate"] is not None for r in rows):
        rooms = {}
        for r in rows:
            rooms.setdefault(r["debate"], []).append(r)
        return list(rooms.values()), "debate-link"
    if all(r.get("mates") for r in rows):
        rooms = {}
        for r in rows:
            rooms.setdefault(frozenset(norm(x) for x in r["mates"]), []).append(r)
        out = [rm for rm in rooms.values() if len(rm) >= 2]
        if sum(len(rm) for rm in out) >= 0.9 * len(rows):
            return out, "mates-popover"
    if all(r["opp"] for r in rows):
        byname = {norm(r["team"]): r for r in rows}
        rooms, used = [], set()
        for r in rows:
            if norm(r["team"]) in used:
                continue
            o = byname.get(norm(r["opp"][1]))
            if o and norm(o["team"]) not in used and o["opp"] and norm(o["opp"][1]) == norm(r["team"]):
                rooms.append([r, o])
                used.add(norm(r["team"]))
                used.add(norm(o["team"]))
        if used == {norm(r["team"]) for r in rows}:
            return rooms, "vs-pair"
    if len(rows) in (2, 3, 4):
        return [rows], "single"
    if len(rows) % 4 == 0:
        rooms = [rows[i:i + 4] for i in range(0, len(rows), 4)]
        if all(sorted(x["sort"] for x in rm if x["sort"] is not None) in VALID4
               for rm in rooms):
            return rooms, "consec4"
    if len(rows) % 2 == 0:
        rooms = [rows[i:i + 2] for i in range(0, len(rows), 2)]
        if all(sorted(x["sort"] for x in rm if x["sort"] is not None) == [1, 2]
               for rm in rooms):
            return rooms, "consec2"
    return None, "ungroupable(%d rows)" % len(rows)


def parse_speaker_tab(page, stats=None):
    arr = tables_data(page)
    if not arr:
        return None
    tb = arr[0]
    heads = [(h.get("key") or h.get("title") or "") for h in tb.get("head", [])]
    lower = [str(h).lower() for h in heads]
    if "name" not in lower or "team" not in lower:
        return None
    c_name, c_team = lower.index("name"), lower.index("team")
    # Per-round score columns: "R1", "Rd 2", "1 (x2)", "R3 - a", etc.
    rcols = [i for i, h in enumerate(heads)
             if re.fullmatch(r"[^\W\d_]{0,6}\s*\d+[A-Za-z]{0,2}"
                             r"(?:\s*[-–—/]\s*[A-Za-z0-9]{1,3}"
                             r"|\s*\([^)]{0,14}\))?", str(h), re.UNICODE)]
    out = {}
    for row in tb.get("data", []):
        try:
            name = strip_html(str(row[c_name].get("text") if isinstance(row[c_name], dict) else row[c_name]))
            team = strip_html(str(row[c_team].get("text") if isinstance(row[c_team], dict) else row[c_team]))
            if not name or name.lower() in ("anonymous", "redacted", "?"):
                continue
            scores = []
            for i in rcols:
                cell = row[i]
                txt = strip_html(str(cell.get("text") if isinstance(cell, dict) else cell))
                txt = txt.replace(",", ".")
                try:
                    scores.append(float(txt))
                except ValueError:
                    scores.append(None)
            out[name] = {"team": team, "scores": scores}
        except Exception:
            if stats is not None:
                stats["speaker_rows_dropped"] += 1
            continue
    if stats is not None:
        stats["speaker_rows_parsed"] += len(out)
    return out or None


def split_url(url):
    p = urlparse(url)
    base = f"{p.scheme}://{p.netloc}"
    slug = p.path.strip("/").split("/")[0] if p.path.strip("/") else ""
    return base, slug, f"{base}/{slug}"


def fetch_rounds(base, slug):
    rounds = []
    try:
        rj = get_json(f"{base}/api/v1/tournaments/{slug}/rounds")
        for r in rj:
            if r.get("seq") is None:
                continue
            stage = {"P": "P", "E": "E"}.get(str(r.get("stage") or "")[:1].upper())
            rounds.append({"seq": r["seq"], "stage": stage, "name": r.get("name"),
                           "starts_at": r.get("starts_at")})
    except Exception:
        pass
    rounds.sort(key=lambda r: r["seq"])
    return rounds


def fetch_tournament(row_id, name, url, date=None):
    base, slug, root = split_url(url)
    rec = {"row": row_id, "name": name, "url": url, "date": date,
           "teams": {}, "rounds": [], "speaks": None, "errors": []}
    pstats = collections.Counter()

    api_rounds = fetch_rounds(base, slug)
    rounds = [(r["seq"], r["stage"], r["name"]) for r in api_rounds]
    if not rounds:
        # No API (old Tabbycat): probe result pages until one is missing.
        for seq in range(1, 15):
            try:
                page = get(f"{root}/results/round/{seq}/")
                if "tablesData" in page:
                    rounds.append((seq, None, None))
            except Exception:
                break
    if not rounds:
        rec["errors"].append("no rounds discovered")
        return rec

    if not date:
        starts = sorted(r["starts_at"][:10] for r in api_rounds if r.get("starts_at"))
        if starts:
            rec["date"] = starts[0]

    turl2names = {}
    try:
        tj = get_json(f"{base}/api/v1/tournaments/{slug}/teams")
        for t in tj:
            variants = [strip_html(str(t.get(k))) for k in
                        ("long_name", "short_name", "reference", "code_name") if t.get(k)]
            spk = [s.get("name") for s in (t.get("speakers") or []) if s.get("name")]
            if variants:
                rec["teams"][variants[0]] = [strip_html(str(x)) for x in spk]
                turl2names[t.get("url")] = variants
    except Exception as e:
        rec["errors"].append("teams API: %s" % type(e).__name__)

    prelim_seqs = []
    for seq, stage, rname in rounds:
        entry = {"seq": seq, "stage": stage, "name": rname, "rooms": None, "method": None}
        try:
            page = get(f"{root}/results/round/{seq}/")
            tabs = parse_results_page(page, stats=pstats)
        except Exception as e:
            entry["method"] = "fetch-fail:%s" % type(e).__name__
            rec["rounds"].append(entry)
            continue
        if not tabs:
            entry["method"] = "no-table"
            rec["rounds"].append(entry)
            continue
        allrooms = []
        for rows in tabs:
            rooms, how = group_rooms(rows)
            if rooms is None and len(tabs) == 1:
                # Last resort: reconstruct rooms from the pairings API.
                try:
                    pj = get_json(f"{base}/api/v1/tournaments/{slug}/rounds/{seq}/pairings")
                    byname = {norm(r["team"]): r for r in rows}
                    rooms = []
                    matched = 0
                    for pair in pj:
                        rm = []
                        for tm in pair.get("teams", []):
                            rr = None
                            for v in turl2names.get(tm.get("team"), []):
                                if norm(v) in byname:
                                    rr = dict(byname[norm(v)])
                                    break
                            if rr is not None:
                                rr["side"] = tm.get("side") or rr["side"]
                                rm.append(rr)
                                matched += 1
                        if len(rm) >= 2:
                            rooms.append(rm)
                    if rooms and matched >= 0.9 * len(rows):
                        how = "pairings-api"
                    else:
                        rooms, how = None, "pairings-lowmatch(%d/%d)" % (matched, len(rows))
                except Exception as e:
                    rooms, how = None, "ungroupable+api:%s" % type(e).__name__
            if rooms:
                allrooms.extend(rooms)
                entry["method"] = (entry["method"] + "+" + how) if entry["method"] else how
            else:
                entry["method"] = (entry["method"] + "+" + str(how)) if entry["method"] else str(how)
        if allrooms:
            entry["rooms"] = [[{"team": x["team"], "sort": x["sort"], "text": x["text"],
                                "side": x["side"], "roster": x["roster"]} for x in rm]
                              for rm in allrooms]
            # Without API stage info, ranked results (not advance/eliminate) mean prelim.
            if stage == "P" or (stage is None and all(
                    x["sort"] in (1, 2, 3, 4) and x["text"] not in ("advancing", "eliminated")
                    for rm in allrooms for x in rm)):
                prelim_seqs.append(seq)
        rec["rounds"].append(entry)

    try:
        page = get(f"{root}/tab/speaker/")
        sp = parse_speaker_tab(page, stats=pstats)
        if sp:
            rec["speaks"] = sp
    except Exception as e:
        rec["errors"].append("speaker tab: %s" % type(e).__name__)
    rec["prelim_seqs"] = prelim_seqs
    rec["parse_stats"] = dict(pstats)
    return rec


def fetch_motions(url):
    base, slug, _ = split_url(url)
    out = {}
    for m in get_json(f"{base}/api/v1/tournaments/{slug}/motions"):
        text = (m.get("text") or "").strip()
        if not text:
            continue
        entry = {"t": text}
        info = (m.get("info_slide_plain") or m.get("info_slide") or "").strip()
        if info:
            entry["i"] = info
        ref = (m.get("reference") or "").strip()
        if ref:
            entry["r"] = ref
        for r in (m.get("rounds") or []):
            seq = None
            mu = re.search(r"/rounds/(\d+)", str(r.get("round") or ""))
            if mu:
                seq = int(mu.group(1))
            elif r.get("seq") is not None:
                seq = r.get("seq")
            if seq is not None:
                out.setdefault(str(seq), []).append(entry)
    return out


VIEW = re.compile(r"^View\s+(.*?)'s\s*(?:\((.*?)\)\s*)?Record\s*$", re.DOTALL)
ADJ_ID = re.compile(r"/adjudicator/(\d+)/")
SPAN = re.compile(r"<span[^>]*>(.*?)</span>", re.DOTALL)
SYM = re.compile(r"[Ⓐ-ⓩ]")  # circled role markers, e.g. Ⓒ chair, Ⓣ trainee
SEP = re.compile(r"^[\s,;/&]*$")


def names_from_text(t):
    out = []
    for m in SPAN.finditer(t or ""):
        raw = strip_html(m.group(1))
        nm = re.sub(r"\s+", " ", SYM.sub("", raw)).strip()
        if nm and not SEP.match(nm):
            out.append((nm, "".join(SYM.findall(raw))))
    if not out:
        flat = SYM.sub("", strip_html(t or ""))
        out = [(re.sub(r"\s+", " ", x).strip(), "") for x in flat.split(",")
               if x.strip() and not SEP.match(x)]
    return out


def panel_from_cell(cell):
    if not isinstance(cell, dict):
        return []
    names = names_from_text(cell.get("text"))
    meta = []
    for c in ((cell.get("popover") or {}).get("content") or []):
        m = VIEW.match(strip_html(str(c.get("text") or "")))
        if not m:
            continue
        role, inst = "", ""
        if m.group(2):
            bits = [b.strip() for b in m.group(2).split(",")]
            head = bits[0].lower()
            if head in ("chair", "panellist", "panelist", "trainee", "solo chair"):
                role = "c" if "chair" in head else ("t" if head == "trainee" else "p")
                inst = ", ".join(bits[1:]).strip()
            else:
                inst = m.group(2).strip()
        aid = ADJ_ID.search(c.get("link") or "")
        meta.append({"r": role, "i": inst, "id": int(aid.group(1)) if aid else None,
                     "first": re.sub(r"\s+", " ", m.group(1)).strip()})
    out = []
    for k, (nm, sym) in enumerate(names):
        md = meta[k] if k < len(meta) else {}
        # Popover order can drift from the visible names; drop meta on mismatch.
        if md and md.get("first") and not nm.lower().startswith(md["first"].lower()):
            md = {}
        role = md.get("r") or ("c" if "Ⓒ" in sym else "t" if "Ⓣ" in sym else "")
        out.append({"n": nm, "r": role, "i": md.get("i") or "", "id": md.get("id")})
    return out


def parse_with_adjs(page):
    arr = tables_data(page)
    if not arr:
        return None
    out = []
    for tb in arr:
        heads = [str(h.get("key") or h.get("title") or "").lower()
                 for h in tb.get("head", [])]
        if "adjudicators" not in heads:
            continue
        ai = heads.index("adjudicators")
        ti = heads.index("team") if "team" in heads else None
        if ti is None:
            continue
        ri = heads.index("result") if "result" in heads else None
        bi = heads.index("ballot") if "ballot" in heads else None
        rows = []
        for row in tb.get("data", []):
            try:
                tc = row[ti]
                team = strip_html(str(tc.get("text") if isinstance(tc, dict) else tc))
                rc = row[ri] if ri is not None and isinstance(row[ri], dict) else {}
                pop = (rc.get("popover") or {}).get("title") or ""
                opp = None
                m = re.match(r"(Won against|Lost to|Drew with) (.+)", pop)
                if m:
                    opp = (m.group(1), m.group(2))
                debate = None
                if bi is not None and isinstance(row[bi], dict):
                    m = re.search(r"/debate/(\d+)/", row[bi].get("link") or "")
                    if m:
                        debate = int(m.group(1))
                rows.append({"team": team, "sort": rc.get("sort"),
                             "text": strip_html(str(rc.get("text", ""))),
                             "side": "", "debate": debate, "opp": opp, "roster": None,
                             "adj": panel_from_cell(row[ai])})
            except Exception:
                continue
        if rows:
            out.append(rows)
    return out or None


def fetch_judges(rec):
    """Re-scrape result pages for adjudicator panels, keyed to already-fetched rounds."""
    base, slug, root = split_url(rec["url"])
    out = {"row": rec["row"], "name": rec.get("name"), "rounds": {}, "errors": []}
    for rd in (rec.get("rounds") or []):
        seq = rd.get("seq")
        if seq is None or not rd.get("rooms"):
            continue
        try:
            tabs = parse_with_adjs(get("%s/results/round/%s/" % (root, seq)))
        except Exception as e:
            out["errors"].append("r%s: %s" % (seq, type(e).__name__))
            continue
        if not tabs:
            continue
        panels = []
        for rows in tabs:
            rooms, _how = group_rooms(rows)
            if not rooms:
                continue
            for rm in rooms:
                adj = next((r["adj"] for r in rm if r["adj"]), [])
                if adj:
                    panels.append({"t": sorted(norm(r["team"]) for r in rm), "a": adj})
        if panels:
            out["rounds"][str(seq)] = panels
    return out
