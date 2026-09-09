import collections
import datetime
import gzip
import itertools
import json
import math
import re
from dataclasses import dataclass, field

import brotli

from . import board, db
from .fit import mid
from .idnorm import canon
from .rooms import (
    is_anon,
    keyname,
    norm_name,
    prelim_seq_list,
    resolve_roster,
    side_code,
    speaks_by_team,
    team_rosters,
)
from .settings import EPOCH

SIDES = ["og", "oo", "cg", "co", "aff", "neg"]
BIAS_SCALE = 400
BREAK_EDGE = 4

DEPTH_LABEL = {0: "Finalist", 1: "Semifinalist", 2: "Quarterfinalist",
               3: "Octofinalist", 4: "Double-Octofinalist",
               5: "Triple-Octofinalist"}

# Score-only tournaments with no surviving tab; names/dates reconstructed by hand.
RECOVERED = {90016: ("Thessaloniki WUDC 2016", "2015-12-27"),
             90017: ("Dutch WUDC 2017", "2016-12-27"),
             90018: ("Southern African UDC 2018", "2018-07-04"),
             90019: ("Cape Town WUDC 2019", "2018-12-27"),
             90118: ("Novi Sad EUDC 2018", "2018-08-13"),
             90020: ("Southern African UDC 2017", "2017-07-08"),
             92020: ("NAUDC 2020", "2020-02-01")}


def day_of(iso: str) -> int:
    return (datetime.date.fromisoformat(iso) - EPOCH).days


def phi(x: float) -> float:
    """Standard normal CDF."""
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def elim_info(name):
    """Classify an elim round name into (break category, depth from the final)."""
    n = (name or "").strip().lower()
    n = n.replace("-", " ").replace("_", " ")
    n = re.sub(r"\s+", " ", n)
    cat = "Open"
    for key, lab in (("esl", "ESL"), ("efl", "EFL"), ("novice", "Novice"),
                     ("rookie", "Novice"), ("junior", "Novice"),
                     ("masters", "Masters"), ("pro am", "Pro-Am"),
                     ("proam", "Pro-Am"), ("high school", "Schools"),
                     ("schools", "Schools"), ("school", "Schools"),
                     ("u16", "U16"), ("u18", "U18"), ("women", "Women's"),
                     ("bronze", "Bronze"), ("silver", "Silver"),
                     ("gold", "Gold"), ("plate", "Plate")):
        if key in n:
            cat = lab
            break
    depth = None
    if "triple octo" in n or "triple oct" in n:
        depth = 5
    elif "double octo" in n or "doble octavos" in n:
        depth = 4
    elif "octo" in n or "octavos" in n or "round of 16" in n or "ro16" in n:
        depth = 3
    elif "quarter" in n or "cuartos" in n or "ro8" in n or "pre semi" in n or "presemi" in n:
        depth = 2
    elif "semi" in n or "pre final" in n or "prefinal" in n:
        depth = 1
    elif "final" in n:
        depth = 0
    if depth is None:
        return cat, None
    return cat, depth


def speaker_rows(tab, disp):
    rows = []
    for key, pts in tab.curves().items():
        if not isinstance(key, str) or key.startswith("anon::"):
            continue
        last_t, g = pts[-1]
        rows.append({
            "name": disp.get(key) or key.title(), "key": key,
            "skill_mu": round(g.mu, 4), "skill_sigma": round(g.sigma, 4),
            "conservative_skill": round(g.mu - 2 * g.sigma, 4),
            "tournaments": len(pts),
            "last_seen": (EPOCH + datetime.timedelta(days=last_t)).isoformat()})
    rows.sort(key=lambda r: -r["conservative_skill"])
    for i, r in enumerate(rows):
        r["rank"] = i + 1
    return rows


def ranking_lookup(rows):
    return {r["key"]: (r["skill_mu"], r["skill_sigma"], r["tournaments"], r["last_seen"])
            for r in rows}


def motion_bias_tables(abl_tab, occs, texts):
    bias_by_round = {}
    by_text = {}
    for (slug, side), g in sorted(abl_tab.motion_bias().items()):
        if slug.startswith("m"):
            places = occs.get(slug, [])
        else:
            row, seq = slug.split("::", 1)
            places = [(int(row), seq)]
        for row, seq in places:
            bias_by_round.setdefault((int(row), str(seq)), {})[side] = (g.mu, g.sigma)
        if slug.startswith("m") and side in ("og", "oo", "cg", "co"):
            by_text.setdefault(mid(texts.get(slug, slug)), {})[side] = (g.mu, g.sigma)
    by_text = {k: v for k, v in by_text.items() if len(v) == 4}
    return bias_by_round, by_text


HIDDEN_NAME = "(hidden)"


@dataclass
class Inputs:
    merges: dict
    display: dict
    display_raw: dict
    clean: dict
    extra_motions: dict
    tagged: dict
    neighbors_raw: dict
    judges_struct: dict
    motions_by_row: dict
    sco_games: dict
    used_rows: set
    hidden_players: set
    hidden_insts: set

    def pkey(self, name: str) -> str:
        k = canon(name)
        return self.merges.get(k, k)


def load_inputs(conn) -> Inputs:
    merges = db.get_artifact(conn, "id_merges", {})
    hidden = db.get_artifact(conn, "hidden", {"players": [], "institutions": []})
    display = dict(db.get_artifact(conn, "legacy_display", {}))
    display.update(db.get_artifact(conn, "rooms_meta", {}).get("display") or {})

    board.configure(db.get_artifact(conn, "inst_alias", {"skip": [], "alias": {}}))

    motions_by_row = {}
    with conn.cursor() as cur:
        cur.execute("SELECT row_id, payload FROM raw_motions")
        for row_id, payload in cur.fetchall():
            if payload and not payload.get("_err"):
                motions_by_row[str(row_id)] = payload

    sco_games = collections.defaultdict(list)
    used_rows = set()
    for g in db.iter_extra_games(conn):
        sco_games[g["row"]].append(g)
        used_rows.add(g["row"])
    with conn.cursor() as cur:
        cur.execute("SELECT DISTINCT row_id FROM rooms")
        used_rows.update(r for (r,) in cur.fetchall())

    return Inputs(
        merges=merges,
        display=display,
        display_raw=db.get_artifact(conn, "display_raw", {}),
        clean=db.get_artifact(conn, "motions_clean", {}),
        extra_motions=db.get_artifact(conn, "motions_extra", {}),
        tagged=db.get_artifact(conn, "motions_tags", {}),
        neighbors_raw=db.get_artifact(conn, "motions_neighbors", {}),
        judges_struct=db.get_artifact(conn, "judges", {}),
        motions_by_row=motions_by_row,
        hidden_players={merges.get(k, k) for k in (hidden.get("players") or [])},
        hidden_insts=set(hidden.get("institutions") or []),
        sco_games=sco_games,
        used_rows=used_rows)


class PlayerIndex:
    def __init__(self):
        self.of = {}
        self.keys = []

    def id(self, key: str) -> int:
        i = self.of.get(key)
        if i is None:
            i = self.of[key] = len(self.keys)
            self.keys.append(key)
        return i

    def __len__(self) -> int:
        return len(self.keys)


@dataclass
class World:
    pid: PlayerIndex = field(default_factory=PlayerIndex)
    tours: list = field(default_factory=list)
    careers: dict = field(default_factory=lambda: collections.defaultdict(list))
    rparts: dict = field(default_factory=dict)
    rooms_pp: list = field(default_factory=list)
    seq2ridx: dict = field(default_factory=dict)
    nroom: dict = field(default_factory=dict)
    rosters: dict = field(default_factory=dict)
    broke_by_tid: dict = field(default_factory=dict)
    row_of_tid: dict = field(default_factory=dict)


class TabTournament:
    def __init__(self, w: World, inp: Inputs, rec: dict):
        self.w = w
        self.inp = inp
        self.rec = rec
        self.row = rec["row"]
        self.tid = len(w.tours)
        self.date = rec.get("date")
        self.rds = sorted(rec.get("rounds") or [], key=lambda r: r.get("seq") or 0)
        self.prelim_seqs = prelim_seq_list(rec)
        self.teams = team_rosters(rec)
        self.spk = speaks_by_team(rec)
        self.rmeta = []
        self.rp = {}
        self.tp = {}
        self.broke = collections.defaultdict(set)
        self.troster = {}
        self.tpts = collections.Counter()
        self.tspk = collections.Counter()
        self.sco_ridx = {}

    def assemble(self) -> None:
        if self.row not in self.inp.used_rows:
            return
        self.add_sco_rounds()
        for rd in self.rds:
            self.add_round(rd)
        self.apply_sco_games()
        if self.tp:
            self.finish()

    def round_motions(self, seq):
        return (self.inp.motions_by_row.get(str(self.row)) or {}).get(str(seq)) or []

    def add_sco_rounds(self) -> None:
        games = self.inp.sco_games.get(self.row)
        if not games:
            return
        tab_seqs = {rd.get("seq") for rd in self.rds}
        for s in sorted({g["seq"] for g in games} - tab_seqs,
                        key=lambda x: (str(type(x)), x)):
            nm = ("Round %s" % s) if isinstance(s, int) else str(s)
            self.sco_ridx[s] = len(self.rmeta)
            self.w.seq2ridx.setdefault(self.tid, {})[str(s)] = len(self.rmeta)
            ms = [{k: v for k, v in mo.items() if k in ("t", "i", "tg")}
                  for mo in self.round_motions(s) if isinstance(mo, dict)]
            self.rmeta.append([s, "P", nm, None, "", ms])

    def add_round(self, rd: dict) -> None:
        rooms_ = rd.get("rooms")
        if not rooms_:
            return
        seq = rd.get("seq")
        is_prelim = (seq in self.prelim_seqs) if self.prelim_seqs else (rd.get("stage") == "P")
        pidx = self.prelim_seqs.index(seq) if seq in self.prelim_seqs else None
        nm = (rd.get("name") or "").strip() or ("Round %s" % seq)
        if is_prelim:
            cat, depth = "", None
        else:
            cat, depth = elim_info(nm)
        ridx = len(self.rmeta)
        self.w.seq2ridx.setdefault(self.tid, {})[str(seq)] = ridx
        ms = self.round_motions(seq)
        if not ms:
            byname = (self.inp.extra_motions.get(str(self.row)) or {}).get("byname") or {}
            if nm in byname:
                ms = [byname[nm]]
        ms = [{k: v for k, v in mo.items() if k in ("t", "i", "tg")}
              for mo in ms if isinstance(mo, dict)]
        self.rmeta.append([seq, "P" if is_prelim else "E", nm, depth, cat, ms])
        self.w.nroom[(self.tid, ridx)] = sum(1 for rm in rooms_ if len(rm) >= 2)
        for rm in rooms_:
            if len(rm) >= 2:
                self.add_room(rm, ridx, is_prelim, pidx, cat)

    def add_room(self, rm: list, ridx: int, is_prelim: bool, pidx, cat: str) -> None:
        ents = []
        for x in rm:
            tk, roster = resolve_roster(x, self.teams, self.spk)
            if not roster or x.get("sort") is None:
                return
            ents.append((tk, x.get("team"), roster, x.get("sort"),
                         side_code(x.get("side"), len(rm))))
        best = max(e[3] for e in ents)
        room_acc = []
        for tk, traw, roster, sort, side in ents:
            pids = [self.w.pid.id(self.inp.pkey(p)) for p in roster if not is_anon(p)]
            acc = None
            if is_prelim:
                acc = {"refs": [], "pids": pids, "side": side}
                room_acc.append(acc)
            self.troster.setdefault(tk, set()).update(pids)
            if not is_prelim:
                self.rp.setdefault(ridx, set()).update(pids)
            if is_prelim:
                res = sort - 1
                self.tpts[tk] += res
                self.tspk.setdefault(tk, 0)
            else:
                res = 10 if sort == best else 11  # 10 = advanced, 11 = eliminated
                self.broke[cat or "Open"].add(tk)
            for p in roster:
                if is_anon(p):
                    continue
                i = self.w.pid.id(self.inp.pkey(p))
                e = self.tp.get(i)
                if e is None:
                    e = self.tp[i] = {"team": norm_name(traw or tk), "tk": tk,
                                      "mates": collections.Counter(), "r": []}
                sc = None
                if is_prelim and pidx is not None:
                    s = self.spk.get(tk, {}).get(norm_name(p))
                    if s and pidx < len(s) and s[pidx] is not None:
                        sc = round(float(s[pidx]), 2)
                        if sc == int(sc):
                            sc = int(sc)
                if sc is not None:
                    self.tspk[tk] += sc
                # Wire format: [round idx, result, side idx, speaks(, expected points)].
                e["r"].append([ridx, res, SIDES.index(side) if side in SIDES else -1, sc])
                if acc is not None:
                    acc["refs"].append(e["r"][-1])
                for q in roster:
                    if q is not p and not is_anon(q):
                        e["mates"][self.inp.pkey(q)] += 1
        if is_prelim and len(room_acc) >= 2:
            self.w.rooms_pp.append((self.tid, ridx, room_acc))

    def apply_sco_games(self) -> None:
        for g in (self.inp.sco_games.get(self.row) or []):
            ri = self.sco_ridx.get(g["seq"])
            if ri is None:
                continue
            if g["obs"] == "C":
                self.apply_sco_continuous(g, ri)
            else:
                self.apply_sco_ordinal(g, ri)

    def sco_entry(self, key: str) -> dict:
        return self.tp.setdefault(self.w.pid.id(key),
                                  {"team": "", "mates": collections.Counter(), "r": []})

    def apply_sco_continuous(self, g: dict, ri: int) -> None:
        for team, val in zip(g["c"], g["r"], strict=False):
            for k in team:
                if k.startswith("anon::"):
                    continue
                self.sco_entry(k)["r"].append([ri, -1, -1, round(float(val), 2)])
        if len(g["c"]) == 2 and all(len(t) == 1 for t in g["c"]):
            a, b = g["c"][0][0], g["c"][1][0]
            if not a.startswith("anon::") and not b.startswith("anon::"):
                self.tp[self.w.pid.id(a)]["mates"][b] += 1
                self.tp[self.w.pid.id(b)]["mates"][a] += 1

    def apply_sco_ordinal(self, g: dict, ri: int) -> None:
        for ci, team in enumerate(g["c"]):
            real = g.get("sch") == "ptsb4" and self.rmeta[ri][1] == "P"
            res = int(g["r"][ci]) if real else (10 if g["r"][ci] == max(g["r"]) else 11)
            tnm = (g.get("tm") or [None] * len(g["c"]))[ci]
            tky = keyname(tnm) if tnm else None
            if real and tky:
                self.tpts[tky] += res
            for k in team:
                if k.startswith("anon::"):
                    continue
                e = self.sco_entry(k)
                if tnm and not e["team"]:
                    e["team"] = tnm
                if tky and not e.get("tk"):
                    e["tk"] = tky
                self.troster.setdefault(tky, set()).add(self.w.pid.id(k))
                e["r"].append([ri, res, -1, None])
                for q in team:
                    if q != k and not q.startswith("anon::"):
                        e["mates"][q] += 1

    def finish(self) -> None:
        w = self.w
        w.tours.append({"i": self.tid, "row": self.row,
                        "n": self.rec.get("name") or ("row %d" % self.row),
                        "d": self.date, "u": self.rec.get("url") or "",
                        "rounds": self.rmeta,
                        "field": 0, "bs": {},
                        "xm": ((self.inp.extra_motions.get(str(self.row)) or {})
                               .get("extra") or [])})
        w.broke_by_tid[self.tid] = (self.date, self.broke, self.troster)
        w.rosters[self.tid] = self.troster
        w.row_of_tid[self.row] = self.tid
        w.rparts[self.tid] = self.rp
        order = sorted(self.tpts, key=lambda k: (-self.tpts[k], -self.tspk.get(k, 0)))
        standing = {k: n + 1 for n, k in enumerate(order)}
        for i, e in self.tp.items():
            mates = [w.pid.id(m) for m, _ in e["mates"].most_common(4)]
            # Career entry: [tid, team name, mate pids, rounds(, rank, field size)].
            row_c = [self.tid, e["team"], mates, e["r"]]
            rk = standing.get(e.get("tk"))
            if rk:
                row_c.append(rk)
                row_c.append(len(order))
            w.careers[i].append(row_c)
        w.tours[-1]["field"] = len(self.tp)


def add_recovered(w: World, inp: Inputs) -> None:
    """Synthesize partial tournaments from score-only games for RECOVERED rows."""
    tab_rows = set(w.row_of_tid)
    rec_games = {r: gs for r, gs in inp.sco_games.items()
                 if r in RECOVERED and r not in tab_rows}
    for row, gs in sorted(rec_games.items()):
        name, date = RECOVERED[row]
        tid = len(w.tours)
        seqs = sorted({g["seq"] for g in gs})
        xm = inp.extra_motions.get(str(row)) or {}
        byname = xm.get("byname") or {}
        rmeta = []
        for s in seqs:
            nm = ("Round %s" % s) if (isinstance(s, int) and s <= 9) else str(s)
            ms = [byname[nm]] if nm in byname else []
            rmeta.append([s, "P" if isinstance(s, int) and s <= 9 else "E",
                          nm, None, "", ms])
        sidx = {s: i for i, s in enumerate(seqs)}
        tp = {}
        for g in gs:
            ri = sidx[g["seq"]]
            if g["obs"] == "C":
                for team, val in zip(g["c"], g["r"], strict=False):
                    for k in team:
                        if k.startswith("anon::"):
                            continue
                        e = tp.setdefault(w.pid.id(k), {"team": "", "mates":
                                          collections.Counter(), "r": []})
                        e["r"].append([ri, -1, -1, round(float(val), 2)])
                if len(g["c"]) == 2 and all(len(t) == 1 for t in g["c"]):
                    a, b = g["c"][0][0], g["c"][1][0]
                    if not a.startswith("anon::") and not b.startswith("anon::"):
                        tp[w.pid.id(a)]["mates"][b] += 1
                        tp[w.pid.id(b)]["mates"][a] += 1
            else:
                for ti, team in enumerate(g["c"]):
                    res = 10 if g["r"][ti] == max(g["r"]) else 11
                    for k in team:
                        if k.startswith("anon::"):
                            continue
                        e = tp.setdefault(w.pid.id(k), {"team": "", "mates":
                                          collections.Counter(), "r": []})
                        e["r"].append([ri, res, -1, None])
                        for q in team:
                            if q != k and not q.startswith("anon::"):
                                e["mates"][q] += 1
        if not tp:
            continue
        w.tours.append({"i": tid, "row": row, "n": name, "d": date, "u": "",
                        "rounds": rmeta, "xm": xm.get("extra") or [],
                        "field": len(tp), "partial": 1})
        for i, e in tp.items():
            w.careers[i].append([tid, e["team"],
                                 [w.pid.id(m) for m, _ in e["mates"].most_common(4)],
                                 e["r"]])


def build_players(w: World, inp: Inputs, base: dict, abl: dict):
    alias_of = collections.defaultdict(set)
    for variant, rootk in inp.merges.items():
        nm = (inp.display_raw.get(variant) or variant).strip()
        if nm:
            alias_of[rootk].add(nm)

    players, aliases = [], {}
    for k in w.pid.keys:
        b = base.get(k)
        a = abl.get(k)
        if k in inp.hidden_players:  # keep the slot so every index stays stable
            players.append([HIDDEN_NAME, None, None, None, None, None])
            continue
        disp = inp.display.get(k) or k.title()
        alt = sorted(n for n in alias_of.get(k, ()) if n.casefold() != disp.casefold())
        if alt:
            aliases[str(len(players))] = alt
        if b:
            players.append([disp, round(b[0], 3), round(b[1], 3),
                            day_of(b[3]),
                            round(a[0], 3) if a else None,
                            round(a[1], 3) if a else None])
        else:
            players.append([disp, None, None, None, None, None])
    return players, aliases


def curve_tables(abl_tab, pid: PlayerIndex):
    cv, at = {}, {}
    for key, pts in abl_tab.curves().items():
        if not isinstance(key, str) or key.startswith("anon::"):
            continue
        i = pid.of.get(key)
        if i is None:
            continue
        ser = [[t, round(g.mu, 3), round(g.sigma, 3)] for t, g in pts]
        at[i] = {t: mu for t, mu, sg in ser}
        if len(ser) >= 3:
            cv[i] = ser
    return cv, at


def attach_field_strength(w: World, at: dict, log) -> None:
    nrs = 0
    for tid, rp in w.rparts.items():
        d0 = w.tours[tid]["d"]
        if not d0:
            continue
        day = day_of(d0)
        for ridx, pids in rp.items():
            got = [at[i][day] for i in pids if i in at and day in at[i]]
            if len(got) >= 2:
                w.tours[tid]["rounds"][ridx].append(round(sum(got) / len(got), 4))
                nrs += 1
    log("elimination rounds rated by field strength: %d" % nrs)


def attach_break_strength(w: World, at: dict, log) -> None:
    """Rate each break by its bubble: weakest teams in vs strongest teams out."""
    nbs = 0
    for tid, (date, broke, troster) in w.broke_by_tid.items():
        if not date:
            continue
        day = day_of(date)

        def rate(tk):
            got = [at[i][day] for i in troster.get(tk, ()) if i in at and day in at[i]]
            return sum(got) / len(got) if got else None

        rated = {tk: r for tk in troster for r in [rate(tk)] if r is not None}
        broke_any = set().union(*broke.values()) if broke else set()
        out_bs = {}
        w.tours[tid]["bn"] = {cat: len(teams) for cat, teams in broke.items()}
        for cat, teams in broke.items():
            miss = set(rated) - (teams if cat == "Open" else broke_any)
            got_in = sorted(rated[t] for t in teams if t in rated)[:BREAK_EDGE]
            left_out = sorted((rated[t] for t in miss), reverse=True)[:BREAK_EDGE]
            edge = got_in + left_out
            if edge:
                out_bs[cat] = round(sum(edge) / len(edge), 4)
        if out_bs:
            w.tours[tid]["bs"] = out_bs
            nbs += 1
    log("break strength computed for %d tournaments" % nbs)


def attach_tags(tours: list, tagged: dict) -> list:
    if not tagged:
        return []
    tag_names = sorted({t for v in tagged.values() for t in v}, key=str.lower)
    tag_idx = {t: i for i, t in enumerate(tag_names)}

    def attach(mo):
        t = mo.get("t")
        if not t:
            return
        tg = tagged.get(mid(t))
        if tg:
            mo["tg"] = [tag_idx[x] for x in tg]

    for t in tours:
        for rd in t["rounds"]:
            for mo in (rd[5] or []):
                attach(mo)
        for mo in (t.get("xm") or []):
            attach(mo)
    return tag_names


def build_balance(w: World, inp: Inputs, bias_by_round: dict, by_text: dict,
                  tag_list: list, log) -> list:
    if not tag_list:
        return []
    balance = []
    for ti, t in enumerate(w.tours):
        # Rooms estimate when unknown: ceil(players / 8), i.e. 4 teams of 2.
        est = -(-t["field"] // 8) if t["field"] else 0
        bs = (t.get("bs") or {}).get("Open")
        for ridx, rd in enumerate(t["rounds"]):
            rooms_n = w.nroom.get((t["i"], ridx))
            if not rooms_n:
                rooms_n = est
            b = bias_by_round.get((t["row"], str(rd[0])))
            if b is not None:
                b = {s: v for s, v in b.items() if s in ("og", "oo", "cg", "co")}
            mos = list(rd[5] or [])
            if not mos:
                continue
            for mo in mos:
                cl = inp.clean.get(mid(mo["t"]))
                if cl and cl["skip"]:
                    continue
                mb = by_text.get(mid(cl["t"] if cl else mo["t"])) or b
                if not mb or len(mb) < 4:
                    continue
                SS = ("og", "oo", "cg", "co")
                sides = [round(mb[s][0] * BIAS_SCALE, 2) for s in SS]
                c = [x - sum(sides) / 4.0 for x in sides]
                sg = [mb[s][1] * BIAS_SCALE for s in SS]
                # Imbalance score: RMS of centered biases, penalized by uncertainty.
                adj = (sum(x * x for x in c) / 4.0
                       + 0.75 * sum(x * x for x in sg) / 4.0) ** 0.5
                tg = sorted(set(mo.get("tg") or []))
                balance.append([ti, rd[2], cl["t"] if cl else mo["t"],
                                (cl["i"] if cl else mo.get("i")) or "",
                                tg, rooms_n, bs, *sides, round(adj, 2)])
    log("motion balance rows: %d" % len(balance))
    return balance


def build_neighbours(balance: list, neighbors_raw: dict) -> list:
    if not balance:
        return []
    order, mi_of = [], {}
    for row in balance:
        k = mid(row[2])
        if k not in mi_of:
            mi_of[k] = len(order)
            order.append(k)
        row.append(mi_of[k])
    out = []
    if neighbors_raw:
        for k in order:
            out.append([[mi_of[j], int(round(s * 1000))]
                        for j, s in (neighbors_raw.get(k) or []) if j in mi_of])
    return out


def attach_expected_points(w: World, inp: Inputs, by_text: dict, at: dict, log) -> None:
    """Append each prelim team's expected points: pairwise win probs vs the room."""
    n_ep = 0
    for tid, ridx, room in w.rooms_pp:
        d0 = w.tours[tid]["d"]
        if not d0:
            continue
        day = day_of(d0)
        mb = {}
        for mo in (w.tours[tid]["rounds"][ridx][5] or []):
            t = mo.get("t")
            if not t:
                continue
            c = inp.clean.get(mid(t))
            mb = by_text.get(mid(c["t"] if c else t)) or {}
            if mb:
                break
        st = []
        for acc in room:
            got = [at[i][day] for i in acc["pids"] if i in at and day in at[i]]
            if not got:
                st = None
                break
            _b = mb.get(acc["side"])
            st.append(2.0 * sum(got) / len(got) + float(_b[0] if _b else 0.0))
        if not st or len(st) < 2:
            continue
        for k, acc in enumerate(room):
            ep = round(sum(phi((st[k] - st[j]) / 2.0)
                           for j in range(len(st)) if j != k), 2)
            for ref in acc["refs"]:
                ref.append(ep)
                n_ep += 1
    log("expected points attached: %d player-rounds" % n_ep)


def build_judges(w: World, inp: Inputs, at: dict):
    jn, jc, jlink = [], [], []
    if not inp.judges_struct:
        return jn, jc, jlink
    disp_j = inp.judges_struct.get("d") or {}
    byp = {}
    for key in disp_j:
        byp.setdefault(inp.pkey(disp_j[key] or ""), []).append(key)
    label = {p: max((disp_j[k] or "" for k in ks), key=lambda n: (len(n), n))
             for p, ks in byp.items()}
    jidx = {}
    for p in sorted(byp, key=lambda p: label[p]):
        for key in byp[p]:
            jidx[key] = len(jn)
        jn.append(label[p])
        jc.append([])
        jlink.append(w.pid.of.get(p, -1))
    ROLE = {"p": 0, "c": 1, "t": 2}
    for row, seqs in (inp.judges_struct.get("p") or {}).items():
        tid = w.row_of_tid.get(int(row))
        if tid is None:
            continue
        d0 = w.tours[tid]["d"]
        day = day_of(d0) if d0 else None
        tr = w.rosters.get(tid) or {}
        for seq, ps in seqs.items():
            ridx = (w.seq2ridx.get(tid) or {}).get(seq)
            if ridx is None:
                continue
            for crew, teams_j in ps:
                got = []
                if day is not None:
                    for tk in teams_j:
                        got += [at[i][day] for i in tr.get(tk, ())
                                if i in at and day in at[i]]
                st = round(sum(got) / len(got), 3) if len(got) >= 2 else None
                for key, role in crew:
                    i = jidx.get(key)
                    if i is None:
                        continue
                    jc[i].append([tid, ridx, ROLE.get(role, 0), st])
    for i, rows_j in enumerate(jc):
        seen, keep = set(), []
        for r in rows_j:
            k = (r[0], r[1])
            if k in seen:
                continue
            seen.add(k)
            keep.append(r)
        keep.sort(key=lambda r: (w.tours[r[0]]["d"] or "", r[1]))
        jc[i] = keep
    return jn, jc, jlink


def build_board(w: World, n_players: int, hidden: set):
    pl = sorted(w.careers)
    teams_of = {i: [e[1] or "" for e in w.careers[i]] for i in pl}
    days_of = {}
    for i in pl:
        ds, last = [], 0
        for e in w.careers[i]:
            d0 = w.tours[e[0]]["d"]
            last = day_of(d0) if d0 else last
            ds.append(last)
        days_of[i] = ds
    insts = board.institutions(pl, teams_of, days_of)

    board_rows, inst_at = [], []
    for i in range(n_players):
        car = w.careers.get(i) or []
        nr = wins = finals = breaks = obreaks = pts = ptsr = 0
        allsp = []
        for e in car:
            rm = w.tours[e[0]]["rounds"]
            elim = won = openc = False
            obd = None
            for r in e[3]:
                nr += 1
                meta = rm[r[0]] if r[0] < len(rm) else None
                if not meta:
                    continue
                if r[3] is not None:
                    allsp.append(r[3])
                if meta[1] == "P":
                    if 0 <= r[1] <= 3:
                        pts += r[1]
                        ptsr += 1
                else:
                    elim = True
                    isopen = (not meta[4]) or meta[4] == "Open"
                    if isopen:
                        openc = True
                        if meta[3] is not None and (obd is None or meta[3] < obd):
                            obd = meta[3]
                        if meta[3] == 0 and r[1] == 10:
                            won = True
            if elim:
                breaks += 1
            if openc:
                obreaks += 1
            if won:
                wins += 1
            if obd == 0:
                finals += 1
        rec_sp = allsp[-30:]
        it = insts.get(i) or {}
        # insts is a run of affiliations over time; dropping one must not
        # leave its neighbours showing as a repeat.
        kept = (k for k, _n in (it.get("insts") or []) if k not in hidden)
        own = [k for k, _ in itertools.groupby(kept)]
        primary = it.get("inst")
        board_rows.append([len(car), nr,
                           round(sum(rec_sp) / len(rec_sp), 2) if rec_sp else None,
                           round(pts / ptsr, 3) if ptsr else None,
                           wins, finals, breaks, obreaks,
                           None if primary in hidden else primary, own,
                           (w.tours[car[0][0]]["d"] if car else None)])
        inst_at.append([None if k in hidden else k for k in (it.get("at") or [])]
                       if car else [])

    inames = {}
    for v in insts.values():
        for k, n in v["insts"]:
            if k not in hidden:
                inames[k] = n
    aka = {}
    for k, n in inames.items():
        alts = [a for a, full in board.INST_ALIAS.items()
                if full == n and a != n.lower()]
        if alts:
            aka[k] = sorted(alts)
    return board_rows, inst_at, inames, aka


def build(conn, base_tab, abl_tab, occs, texts, built_date, log=print):
    """Assemble the site payload; returns (data, rest, base rows, ablation rows)."""
    inp = load_inputs(conn)
    rows_b = speaker_rows(base_tab, inp.display)
    rows_a = speaker_rows(abl_tab, inp.display)
    base = ranking_lookup(rows_b)
    abl = ranking_lookup(rows_a)
    bias_by_round, by_text = motion_bias_tables(abl_tab, occs, texts)

    w = World()
    with conn.cursor(name="payload_tabs") as cur:
        cur.itersize = 20
        cur.execute("SELECT payload FROM raw_tabs ORDER BY row_id")
        for (rec,) in cur:
            TabTournament(w, inp, rec).assemble()
    add_recovered(w, inp)

    players, aliases = build_players(w, inp, base, abl)
    for i in w.careers:
        w.careers[i].sort(key=lambda e: w.tours[e[0]]["d"])

    cv, at = curve_tables(abl_tab, w.pid)
    attach_field_strength(w, at, log)
    attach_break_strength(w, at, log)
    tag_list = attach_tags(w.tours, inp.tagged)
    balance = build_balance(w, inp, bias_by_round, by_text, tag_list, log)
    neighbours = build_neighbours(balance, inp.neighbors_raw)
    attach_expected_points(w, inp, by_text, at, log)
    jn, jc, jlink = build_judges(w, inp, at)
    board_rows, inst_at, inames, aka = build_board(w, len(players), inp.hidden_insts)

    data = {"built": built_date,
            "epoch": "2010-01-01",
            "tournaments": w.tours,
            "players": players,
            "aliases": aliases,
            "board": board_rows,
            "instAt": inst_at,
            "instNames": inames,
            "instAka": aka,
            "sides": SIDES,
            "tags": tag_list,
            "jn": jn,
            "jlink": jlink,
            "depthLabel": {str(k): v for k, v in DEPTH_LABEL.items()}}
    rest = {"careers": {str(i): w.careers[i] for i in sorted(w.careers)},
            "curves": {str(i): v for i, v in sorted(cv.items())},
            "balance": balance,
            "nbr": neighbours,
            "jc": jc}
    log("tournaments %d  players %d  careers %d  curves %d"
        % (len(w.tours), len(players), len(w.careers), len(cv)))
    return data, rest, rows_b, rows_a


def store(conn, data, rest, built_at):
    """Upsert gzip and brotli bodies; the Go server picks by Accept-Encoding."""
    n = 0
    with conn.cursor() as cur:
        for name, obj in (("data", data), ("rest", rest)):
            raw = json.dumps(obj, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
            for encoding, body in (("gz", gzip.compress(raw, 9, mtime=0)),
                                   ("br", brotli.compress(raw, quality=11))):
                cur.execute(
                    "INSERT INTO payloads (name, encoding, built_at, body) "
                    "VALUES (%s, %s, %s, %s) "
                    "ON CONFLICT (name, encoding) DO UPDATE "
                    "SET built_at = EXCLUDED.built_at, body = EXCLUDED.body",
                    (name, encoding, built_at, body))
                n += 1
    conn.commit()
    return n
