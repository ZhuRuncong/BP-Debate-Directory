"""Keep apart different people who share a name.

The shared_names artifact lists names that cover more than one person:
{"will ryan": ["ucla", "harvard"]} is the plain "will ryan" profile plus two split-off ones.
Each person's fingerprint is learned from the appearances already sorted to them: partners,
partners' institutions and regions, their own institutions, team-name words, where they
compete. A new appearance goes to whoever it clearly fits. One that fits nobody clearly
stays where it is and is listed in shared_names_review for a human. Every assignment is
written to id_splits, so it can still be moved by hand.
"""
import datetime
import re

from . import automerge, db
from .idnorm import SPLIT_SEP
from .rooms import keyname

SHARED = "shared_names"
SEEN = "shared_names_seen"      # name -> appearances already sorted, never reconsidered
REVIEW = "shared_names_review"  # name -> appearances no fingerprint clearly claimed
CLEAR_WIN = 2.0                 # the winner must score this many times the runner-up
W_PARTNER, W_PARTNER_INST, W_OWN_INST, W_TEAM_WORD, W_REGION = 3.0, 2.0, 2.0, 1.0, 0.5
CLASH_DAYS = 2                  # one person is never at two tournaments starting this close
STOP = {"a", "b", "c", "d", "and", "the", "of", "to", "be", "we", "in", "is", "it", "my", "on"}


def words(team: str) -> set:
    return {w for w in re.findall(r"[a-z]+", team.lower()) if w not in STOP and len(w) > 1}


class Appearance:
    def __init__(self, p, data, i, e):
        inst_at, names, region_of = data.get("instAt") or [], data.get("instNames") or {}, \
            data.get("instRegion") or {}
        self.tid, self.team = e[0], e[1] or ""
        t = p.tours[self.tid]
        self.mark = "%s|%s" % (t.get("row"), keyname(self.team))
        self.date = datetime.date.fromisoformat(t["d"]) if t.get("d") else None
        self.region = p.region.get(self.tid)
        own = dict(zip((x[0] for x in p.career.get(i, [])), inst_at[i] if i < len(inst_at) else [], strict=False))
        self.own_inst = own.get(self.tid)
        self.partners = set(e[2])
        self.partner_insts, self.partner_regions = set(), set()
        for m in e[2]:
            at = dict(zip((x[0] for x in p.career.get(m, [])), inst_at[m] if m < len(inst_at) else [], strict=False))
            board = data.get("board") or []
            k = at.get(self.tid) or (board[m][8] if m < len(board) and board[m] else None)
            if k:
                self.partner_insts.add(k)
                if k in region_of:
                    self.partner_regions.add(region_of[k])
        self.words = words(self.team)
        self.inst_names = names


class Person:
    """What one of the people behind a shared name looks like, from what's been sorted to them."""

    def __init__(self):
        self.partners, self.partner_insts, self.own_insts = set(), set(), set()
        self.partner_regions, self.regions, self.words = set(), set(), set()
        self.dates = {}

    def add(self, a: Appearance):
        self.partners |= a.partners
        self.partner_insts |= a.partner_insts
        self.partner_regions |= a.partner_regions
        if a.own_inst:
            self.own_insts.add(a.own_inst)
        if a.region:
            self.regions.add(a.region)
        self.words |= a.words
        if a.date:
            self.dates[a.tid] = a.date

    def score(self, a: Appearance):
        """How well an appearance fits; None when it can't be them at all."""
        if a.date and any(t != a.tid and abs((d - a.date).days) <= CLASH_DAYS
                          for t, d in self.dates.items()):
            return None
        team = a.team.lower()
        # "UCLAmbrige A" still says UCLA
        named = any(len(k) >= 3 and k in team for k in self.own_insts)
        return (W_PARTNER * len(a.partners & self.partners)
                + W_PARTNER_INST * len(a.partner_insts & (self.partner_insts | self.own_insts))
                + W_OWN_INST * (a.own_inst in self.own_insts or named)
                + W_TEAM_WORD * len(a.words & self.words)
                + W_REGION * len(a.partner_regions & self.partner_regions)
                + W_REGION * (a.region in self.regions))


def decide(people: dict, a: Appearance):
    """(winner tag or None, scores); "" is the plain profile."""
    scores = {tag: person.score(a) for tag, person in people.items()}
    able = sorted(((s, tag) for tag, s in scores.items() if s is not None), reverse=True)
    if len(able) == 1:
        return able[0][1], scores  # the calendar ruled out everyone else
    if not able or able[0][0] <= 0 or able[0][0] < CLEAR_WIN * able[1][0]:
        return None, scores
    return able[0][1], scores


def run(conn, data: dict, rest: dict, log=print) -> int:
    """Sort new appearances of shared names; returns how many split entries were written."""
    shared = db.get_artifact(conn, SHARED, {})
    if not shared:
        return 0
    seen = db.get_artifact(conn, SEEN, {})
    review = db.get_artifact(conn, REVIEW, {})
    splits = db.get_artifact(conn, "id_splits", {})
    merges = db.get_artifact(conn, "id_merges", {})
    p = automerge.Profiles(data, rest)
    where = {k: i for i, k in enumerate(p.keys)}
    written, held = 0, 0
    for name, tags in shared.items():
        idx = {"": where.get(name), **{t: where.get(name + SPLIT_SEP + t) for t in tags}}
        apps = {tag: [Appearance(p, data, i, e) for e in p.career.get(i, [])]
                for tag, i in idx.items() if i is not None}
        marks = set(seen.get(name, []))
        flagged = {r["mark"] for r in review.get(name, [])}
        # a newly declared name: what's on its profiles now has been sorted by hand
        fresh = [a for a in apps.get("", []) if a.mark not in marks] if name in seen else []
        people = {tag: Person() for tag in idx}
        for tag, lst in apps.items():
            for a in lst:
                if a not in fresh and a.mark not in flagged:
                    people[tag].add(a)
        # a tab may spell the person any way that merges into them ("Nate" for "Nate Chan")
        spellings = [name] + sorted(v for v, root in merges.items() if root == name)
        for a in sorted(fresh, key=lambda a: a.date or datetime.date.min):
            win, scores = decide(people, a)
            if win is None:
                review.setdefault(name, []).append({
                    "mark": a.mark, "tournament": p.tours[a.tid].get("n"), "team": a.team,
                    "partners": sorted(p.names[m] for m in a.partners),
                    "scores": {t or "(plain)": s for t, s in scores.items()}})
                held += 1
                continue
            people[win].add(a)
            if win:
                row = str(p.tours[a.tid].get("row"))
                team = splits.setdefault(row, {}).setdefault(a.team, {})
                for n in spellings:
                    team[n] = win
                    if n != name:
                        merges[n + SPLIT_SEP + win] = name + SPLIT_SEP + win
                written += 1
        seen[name] = sorted(marks | {a.mark for lst in apps.values() for a in lst})
    db.set_artifact(conn, SEEN, seen)
    if held:
        db.set_artifact(conn, REVIEW, review)
        log("shared names: %d appearances need a human to say who they are (%s)" % (held, REVIEW))
    if written:
        db.set_artifact(conn, "id_splits", splits)
        db.set_artifact(conn, "id_merges", merges)
        log("shared names: %d appearances moved to the person they fit" % written)
    return written
