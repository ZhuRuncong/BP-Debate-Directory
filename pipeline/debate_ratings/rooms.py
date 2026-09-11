import collections
import datetime
import json
import re
import statistics

from . import db
from .idnorm import canon, clean_display
from .settings import EPOCH


def norm_name(s):
    return re.sub(r"\s+", " ", (s or "")).strip()


def keyname(s):
    return norm_name(s).casefold()


ANON_EXACT = {"anonymous", "(anonymous)", "redacted", "anon", "n/a", "na",
              "code name", "codename",
              "tba", "tbd", "swing", "swing speaker", "unknown", "-", "--",
              ".", "..", "...", "?", "??", "x", "xx", "xxx"}
ANON_PREFIX = ("code name:", "codename:", "anonymous ", "redacted ")
PLACEHOLDER = re.compile(
    r"^(?:swing\s+|reply\s+)?"
    r"(?:speaker|spk|debater|deb|team|swing|iron|sub|reply|member|participant|person|player)"
    r"(?:\s*[a-z]?\s*[\d.\-]{0,5}\s*[a-z]?|\s+(?:low|high|team|swing))?$")
CODE_ONLY = re.compile(r"^[a-z]?\d{1,2}[a-z]?$")


def is_anon(name):
    n = canon(name)
    if len(n) < 2:
        return True
    if n in ANON_EXACT:
        return True
    if any(n.startswith(p) for p in ANON_PREFIX):
        return True
    if PLACEHOLDER.match(n) or CODE_ONLY.match(n):
        return True
    return False


SIDE_MAP = {
    "opening government": "og", "opening opposition": "oo",
    "closing government": "cg", "closing opposition": "co",
    "og": "og", "oo": "oo", "cg": "cg", "co": "co",
    "government": "aff", "opposition": "neg", "proposition": "aff",
    "affirmative": "aff", "negative": "neg", "aff": "aff", "neg": "neg",
    "prop": "aff", "opp": "neg", "gov": "aff",
    "proposición": "aff", "oposición": "neg", "gobierno": "aff",
}
SQRT2 = 2 ** 0.5
SPEAK_FLOOR = 55.0
SPEAK_MAXDEV = 12.0


def usable_room_scores(scored):
    """Reject a room's speaks when any score is implausibly low (data-entry junk)."""
    vals = list(scored.values())
    if not vals:
        return False
    med = statistics.median(vals)
    for x in vals:
        if x < SPEAK_FLOOR or (med - x) > SPEAK_MAXDEV:
            return False
    return True


def side_code(s, nteams):
    c = SIDE_MAP.get((s or "").strip().casefold(), "")
    if nteams == 2 and c in ("og", "oo"):
        c = "aff" if c == "og" else "neg"  # BP labels on a two-team room
    return c


def pick_display(key, counter):
    own = [(n, v) for n, v in counter.items() if canon(n) == key]
    return max(own or counter.items(), key=lambda nv: nv[1])[0]


def team_rosters(rec: dict) -> dict[str, list[str]]:
    return {keyname(k): [norm_name(x) for x in v]
            for k, v in (rec.get("teams") or {}).items()}


def speaks_by_team(rec: dict) -> dict[str, dict[str, list]]:
    out = collections.defaultdict(dict)
    for nm, d in (rec.get("speaks") or {}).items():
        out[keyname(d.get("team"))][norm_name(nm)] = d.get("scores") or []
    return out


def prelim_seq_list(rec: dict) -> list:
    rds = sorted(rec.get("rounds") or [], key=lambda r: r.get("seq") or 0)
    stage_p = [rd["seq"] for rd in rds if rd.get("stage") == "P"]
    return stage_p if stage_p else (rec.get("prelim_seqs") or [])


def resolve_roster(entry: dict, teams: dict, spk_by_team: dict) -> tuple[str, list[str]]:
    tk = keyname(entry.get("team"))
    roster = teams.get(tk)
    if not roster and entry.get("roster"):
        roster = [norm_name(p) for p in re.split(r",\s*", entry["roster"]) if p.strip()]
    if not roster or all(is_anon(p) for p in roster):
        alt = list(spk_by_team.get(tk, {}))
        if alt and not all(is_anon(p) for p in alt):
            roster = alt
    return tk, [p for p in dict.fromkeys(roster or []) if p][:6]


def apply_roster_fixes(rec: dict, fixes: dict) -> dict:
    """Name placeholder speakers ("Speaker 2") on one team at one tournament."""
    fix = {keyname(t): {keyname(o): n for o, n in m.items()}
           for t, m in (fixes.get(str(rec["row"])) or {}).items()}
    if not fix:
        return rec

    def swap(team, name):
        return fix.get(keyname(team), {}).get(keyname(name), name)

    rec = dict(rec)
    rec["teams"] = {t: [swap(t, p) for p in ps] for t, ps in (rec.get("teams") or {}).items()}
    rec["speaks"] = {swap(s.get("team"), n): s for n, s in (rec.get("speaks") or {}).items()}
    return rec


def chair_lookup(panels, row):
    out = {}
    for seq, ps in (panels.get(str(row)) or {}).items():
        entries = []
        for panel, teams in ps:
            chair = next((k for k, role in panel if role == "c"), None)
            if chair:
                entries.append((frozenset(teams), chair))
        out[seq] = entries
    return out


def find_chair(lookup, seq, teamset):
    """Match a room to its panel's chair by team overlap; None if ambiguous."""
    entries = lookup.get(str(seq)) or []
    best, best_n, tied = None, 0, False
    for teams, chair in entries:
        if teams == teamset:
            return chair
        n = len(teams & teamset)
        if n > best_n:
            best, best_n, tied = chair, n, False
        elif n == best_n and chair != best:
            tied = True
    if best_n >= max(2, len(teamset) - 1) and not tied:
        return best
    return None


class Builder:
    def __init__(self, merges: dict, excluded_rows: list, panels: dict):
        self.merges = merges
        self.excluded = set(excluded_rows)
        self.panels = panels
        self.stats = collections.Counter()
        self.speak_devs = []
        self.display = {}

    def pkey(self, s):
        if s.startswith("ANON::"):
            return s.casefold()
        k = canon(s)
        return self.merges.get(k, k)

    def build_tournament(self, rec: dict) -> list[dict]:
        stats = self.stats
        out_rooms = []
        row = rec["row"]
        if row in self.excluded:
            return out_rooms
        date = rec.get("date")
        if not date:
            stats["no date"] += 1
            return out_rooms
        t = (datetime.date.fromisoformat(date) - EPOCH).days
        teams = team_rosters(rec)
        sizes = collections.Counter(len(rm) for rd in (rec.get("rounds") or [])
                                    for rm in (rd.get("rooms") or []))
        if not sizes:
            stats["skipped: no rooms"] += 1
            return out_rooms
        if sizes.most_common(1)[0][0] != 4:
            stats["skipped: non-BP tournament"] += 1
            return out_rooms
        prelim_seqs = prelim_seq_list(rec)
        spk_by_team = speaks_by_team(rec)
        chairs = chair_lookup(self.panels, row)

        for rd in rec.get("rounds") or []:
            rooms = rd.get("rooms")
            if not rooms:
                continue
            seq, stage = rd.get("seq"), rd.get("stage")
            is_prelim = seq in prelim_seqs if prelim_seqs else (stage == "P")
            pidx = prelim_seqs.index(seq) if seq in prelim_seqs else None

            for rm in rooms:
                if len(rm) < 2:
                    stats["room <2 teams"] += 1
                    continue
                if len(rm) > 4:
                    stats["room >4 teams"] += 1
                    continue
                entries = []
                for x in rm:
                    tk, roster = resolve_roster(x, teams, spk_by_team)
                    roster = [("ANON::%s::%s::%d" % (row, tk, i)) if is_anon(p) else p
                              for i, p in enumerate(roster)]
                    entries.append({"team": tk, "side": side_code(x.get("side"), len(rm)),
                                    "sort": x.get("sort"), "roster": roster})
                if any(not e["roster"] for e in entries):
                    stats["room missing roster"] += 1
                    continue
                if any(e["sort"] is None for e in entries):
                    stats["room missing result"] += 1
                    continue

                scored = {}
                if is_prelim and pidx is not None:
                    for e in entries:
                        for p in e["roster"]:
                            sc = spk_by_team.get(e["team"], {}).get(p)
                            if sc and pidx < len(sc) and sc[pidx] is not None:
                                scored[p] = sc[pidx]

                for e in entries:
                    roster = e["roster"]
                    if len(roster) > 2:
                        # BP teams speak two; keep whoever actually scored this round.
                        counts = {p: sum(x is not None
                                         for x in spk_by_team.get(e["team"], {}).get(p, []))
                                  for p in roster}
                        roster = sorted(roster, key=lambda p: (p not in scored,
                                                               -scored.get(p, 0.0),
                                                               -counts[p],
                                                               roster.index(p)))[:2]
                        stats["roster truncated to 2"] += 1
                    kept, mapped, seen_k = [], [], set()
                    for p in roster:
                        k = self.pkey(p)
                        if k in seen_k:
                            stats["teammates merged to one"] += 1
                            continue
                        seen_k.add(k)
                        kept.append(p)
                        mapped.append(k)
                        if not p.startswith("ANON::"):
                            self.display.setdefault(k, collections.Counter())[
                                clean_display(p)] += 1
                    e["roster"] = kept
                    e["keys"] = mapped

                flat = [k for e in entries for k in e["keys"]]
                if len(set(flat)) != len(flat):
                    stats["room dropped (speaker in two teams)"] += 1
                    continue

                if not is_prelim:
                    adv = [e for e in entries if e["sort"] == 2]
                    elim = [e for e in entries if e["sort"] == 1]
                    if not adv or not elim:
                        stats["elim without adv/elim split"] += 1
                        continue
                    chair = find_chair(chairs, seq, frozenset(e["team"] for e in entries))
                    stats["chair matched" if chair else "chair missing"] += 1
                    out_rooms.append({
                        "row": row, "seq": seq, "t": t, "stage": "E", "chair": chair,
                        "teams": [{"team": e["team"], "side": e["side"],
                                   "sort": float(e["sort"]),
                                   "roster": e["keys"],
                                   "iron": len(e["keys"]) == 1,
                                   "speaks": None} for e in entries]})
                    stats["elim rooms"] += 1
                    continue

                for e in entries:
                    e["spoke"] = [p for p in e["roster"] if p in scored]
                    e["iron"] = len(e["roster"]) == 1
                complete = all(len(e["spoke"]) == (1 if e["iron"] else 2)
                               for e in entries)
                have = {p: scored[p] for e in entries for p in e["spoke"]}
                if have and not usable_room_scores(have):
                    stats["room speaks dropped (implausible)"] += 1
                    scored = {}
                    for e in entries:
                        e["spoke"] = []
                    complete = False
                if complete and not any(e["iron"] for e in entries):
                    for e in entries:
                        a, b = float(scored[e["spoke"][0]]), float(scored[e["spoke"][1]])
                        # Teammate diff has variance 2*sigma^2; /sqrt(2) recovers per-speaker sigma.
                        d = (a - b) / SQRT2
                        if len(self.speak_devs) < 400000:
                            self.speak_devs.append(d)
                chair = find_chair(chairs, seq, frozenset(e["team"] for e in entries))
                stats["chair matched" if chair else "chair missing"] += 1
                out_rooms.append({
                    "row": row, "seq": seq, "t": t, "stage": "P", "chair": chair,
                    "teams": [{"team": e["team"], "side": e["side"],
                               "sort": float(e["sort"]),
                               "roster": e["keys"],
                               "iron": e["iron"],
                               "speaks": ([scored.get(p) for p in e["roster"]]
                                          if any(p in scored for p in e["roster"])
                                          else None)}
                              for e in entries]})
                stats["prelim rooms"] += 1
                stats["prelim rooms with speaks" if complete else
                      "prelim rooms ordinal only"] += 1
                for e in entries:
                    stats["team-rounds scored" if len(e["spoke"]) == (1 if e["iron"] else 2)
                          else "team-rounds unscored"] += 1
                    if e["iron"]:
                        stats["iron team-rounds"] += 1
        return out_rooms

    def speak_scale(self):
        if len(self.speak_devs) > 100:
            return statistics.pstdev(self.speak_devs)
        return 3.0  # fallback when too few paired speaks to estimate

    def display_map(self):
        return {k: pick_display(k, c) for k, c in self.display.items()}


def rebuild_all(conn, judges_struct: dict) -> tuple[int, float, dict]:
    """Rebuild the rooms table from raw tabs; returns (n_rooms, speak_scale, stats)."""
    merges = db.get_artifact(conn, "id_merges", {})
    excluded = db.get_artifact(conn, "excluded_rows", [])
    builder = Builder(merges, excluded, judges_struct.get("p") or {})
    fixes = db.get_artifact(conn, "roster_fixes", {})
    all_rooms = []
    with conn.cursor(name="tabs_cur") as cur:
        cur.itersize = 50
        cur.execute("SELECT payload FROM raw_tabs ORDER BY row_id")
        for (rec,) in cur:
            all_rooms.extend(builder.build_tournament(apply_roster_fixes(rec, fixes)))
    scale = builder.speak_scale()
    for room in all_rooms:
        room["scale"] = scale
    with conn.cursor() as cur:
        cur.execute("DELETE FROM rooms")
        with cur.copy("COPY rooms (row_id, t, stage, payload) FROM STDIN") as copy:
            for room in all_rooms:
                copy.write_row((room["row"], room["t"], room["stage"],
                                json.dumps(room, ensure_ascii=False)))
    db.set_artifact(conn, "rooms_meta", {
        "stats": dict(builder.stats), "speak_scale": scale,
        "n_dev_sample": len(builder.speak_devs),
        "display": builder.display_map()})
    conn.commit()
    return len(all_rooms), scale, dict(builder.stats)
