"""Merge profiles that are one debater entered under slightly different names.

Candidates are profiles whose names share two parts. A pair merges when the
names line up part for part (exactly, as an initial, or as a near spelling),
with extra parts on one side at most, and the two careers back it up: close in
time, at the same region's tournaments, not at two institutions at once, and
under a name rare enough that two people carrying it would be a coincidence.
Profiles that met at a tournament are never merged: nobody debates themselves.

    DATABASE_URL=... python -m debate_ratings.automerge [--dry-run] [--show N]
"""
import argparse
import collections
import difflib
import gzip
import itertools
import json
import re

from . import db
from .idnorm import SPLIT_SEP, canon, name_tokens

DECISIONS = "merge_decisions"  # pair_key -> "y"/"n"; a decided pair is never reconsidered
MUHAMMAD = {"md", "mohd", "muhammad", "mohammad", "mohammed", "muhammed", "mohamed",
            "muhamad", "mohamad", "muhmmad"}
CJK = re.compile("[㐀-鿿豈-﫿]+")
FUZZY_LEN = 5       # shorter parts differ by one letter too easily to call a typo
FUZZY_RATIO = 0.8
FLEXIBLE = 0.2      # a part seen this often in both first and last place reads either way
MAX_GAP = 8         # years between careers beyond which no name is evidence enough
THRESHOLD = 2.0
EXACT, VARIANT, INITIAL = 3, 2, 1


def pair_key(a: str, b: str) -> str:
    return " | ".join(sorted([a, b]))


def apply_merge(merges: dict, alias: str, root: str) -> None:
    """Point alias at root, keeping every chain one step deep."""
    root = merges.get(root, root)
    for variant, target in list(merges.items()):
        if target == alias:
            merges[variant] = root
    merges[alias] = root


def part_match(a: str, b: str) -> int:
    if a == b:
        return EXACT
    if a in MUHAMMAD and b in MUHAMMAD:
        return VARIANT
    if (len(a) >= FUZZY_LEN and len(b) >= FUZZY_LEN and a[0] == b[0]
            and difflib.SequenceMatcher(None, a, b).ratio() >= FUZZY_RATIO):
        return VARIANT
    if (len(a) == 1 and b.startswith(a)) or (len(b) == 1 and a.startswith(b)):
        return INITIAL
    return 0


def align(ta: list, tb: list):
    """Pair up name parts, best matches first; returns (pairs, unmatched a, unmatched b)."""
    ranked = sorted(((part_match(x, y), p, q) for p, x in enumerate(ta) for q, y in enumerate(tb)),
                    reverse=True)
    used_a, used_b, pairs = set(), set(), []
    for s, p, q in ranked:
        if s and p not in used_a and q not in used_b:
            used_a.add(p)
            used_b.add(q)
            pairs.append((p, q, s))
    return (sorted(pairs), [x for p, x in enumerate(ta) if p not in used_a],
            [y for q, y in enumerate(tb) if q not in used_b])


class Profiles:
    """What deciding needs about each published profile, from a payload's data and rest."""

    def __init__(self, data: dict, rest: dict):
        players = data["players"]
        self.names = [p[0] for p in players]
        self.keys = rest.get("keys") or [canon(n) for n in self.names]
        self.tours = data["tournaments"]
        self.career = {int(i): c for i, c in rest["careers"].items()}
        # hidden profiles have no career; id-split identities share a name on purpose
        self.toks = {i: name_tokens(canon(self.names[i])) for i in self.career
                     if SPLIT_SEP not in self.keys[i]}
        self.teammate = collections.defaultdict(set)
        for i, car in self.career.items():
            for e in car:
                for m in e[2]:
                    self.teammate[i].add(m)
                    self.teammate[m].add(i)
        self.holding = collections.defaultdict(set)
        self.first, self.last = collections.Counter(), collections.Counter()
        for i, ts in self.toks.items():
            for t in set(ts):
                self.holding[t].add(i)
            if len(ts) >= 2:
                self.first[ts[0]] += 1
                self.last[ts[-1]] += 1
        self.n = max(len(self.toks), 1)
        inst_names, board = data.get("instNames") or {}, data.get("board") or []
        self.inst = {i: inst_names.get(board[i][8], board[i][8] or "")
                     for i in self.career if i < len(board) and board[i]}
        self.region = self.tour_regions(data)
        self.dates = {i: [int(self.tours[e[0]]["d"][:4]) for e in car if self.tours[e[0]].get("d")]
                      for i, car in self.career.items()}

    def tour_regions(self, data):
        """A tournament's region is where most of its field's institutions are."""
        region_of, inst_at = data.get("instRegion") or {}, data.get("instAt") or []
        votes = collections.defaultdict(collections.Counter)
        for i, car in self.career.items():
            for e, k in zip(car, inst_at[i] if i < len(inst_at) else (), strict=False):
                if k in region_of:
                    votes[e[0]][region_of[k]] += 1
        return {t: c.most_common(1)[0][0] for t, c in votes.items()}

    def freq(self, t):
        return len(self.holding[t])

    def regions(self, i):
        return {self.region[e[0]] for e in self.career[i] if e[0] in self.region}

    def tids(self, i):
        return {e[0] for e in self.career[i]}

    def gap(self, i, j):
        """Years between the two careers, 0 when they overlap, None when a date is missing."""
        a, b = self.dates[i], self.dates[j]
        if not a or not b:
            return None
        if min(a) <= max(b) and min(b) <= max(a):
            return 0
        return min(abs(min(a) - max(b)), abs(min(b) - max(a)))

    def met(self, i, j):
        return j in self.teammate[i] or bool(self.tids(i) & self.tids(j))

    def flexible(self, t):
        n = self.first[t] + self.last[t]
        return n > 0 and min(self.first[t], self.last[t]) / n > FLEXIBLE


def candidates(p: Profiles):
    """Every pair of profiles whose names share two parts and who never met."""
    by_parts = collections.defaultdict(list)
    for i, ts in p.toks.items():
        for two in itertools.combinations(sorted(set(ts)), 2):
            by_parts[two].append(i)
    seen = set()
    for group in by_parts.values():
        for i, j in itertools.combinations(sorted(group), 2):
            if (i, j) not in seen and p.keys[i] != p.keys[j]:
                seen.add((i, j))
                if not p.met(i, j):
                    yield i, j


def decide(p: Profiles, i: int, j: int):
    """(merge?, reason) for one candidate pair."""
    ta, tb = p.toks[i], p.toks[j]
    pairs, left_a, left_b = align(ta, tb)
    if left_a and left_b:
        return False, "names disagree"
    hi, hj = set(CJK.findall(p.names[i])), set(CJK.findall(p.names[j]))
    if hi and hj and not hi & hj:
        return False, "different characters"
    shared = set(ta) & set(tb)
    if sum(len(t) > 1 for t in shared) < 2:
        return False, "only initials shared"
    gap = p.gap(i, j)
    if gap is None:
        return False, "no dates"
    if gap > MAX_GAP:
        return False, "careers %d years apart" % gap
    # the matched parts in the other name's order: "Li Ruyi" vs "Ruyi Li" is out of order
    order = [q for _p, q, _s in pairs]
    in_order = order == sorted(order)
    ii, ij = p.inst.get(i), p.inst.get(j)
    same_inst = bool(ii) and ii == ij
    diff_inst = bool(ii) and bool(ij) and not same_inst
    if not in_order and gap >= 2 and not same_inst:
        return False, "reordered name, careers apart"

    score = 0.0
    # how many people should carry these parts together by chance alone
    chance = p.n
    for t in shared:
        chance *= p.freq(t) / p.n
    if chance < 0.02 or min(p.freq(t) for t in shared) <= 3:
        score += 2
    elif chance < 0.1:
        score += 1
    elif chance > 0.3:
        score -= 1.5
    holders = len(set.intersection(*(p.holding[t] for t in shared)))
    if holders > 6:
        score -= 2
    elif holders > 3:
        score -= 1
    score += {0: 1, 1: 1, 2: 0.5}.get(gap, -0.5 if gap <= 5 else -2)
    if same_inst:
        score += 2
    elif diff_inst:
        score -= 1.5 if gap <= 1 else 1
    ri, rj = p.regions(i), p.regions(j)
    if ri & rj:
        score += 0.5
    elif ri and rj:
        # one tournament abroad says little; two careers each abroad say more
        score -= 1.5 if min(len(p.career[i]), len(p.career[j])) >= 2 else 0.5
    if ta == tb:
        score += 1.5  # same parts in the same order: only spelling or an annotation differs
    elif not in_order and all(p.flexible(t) for t in shared):
        score -= 1  # "Yang Yi" and "Yi Yang" can be two people
    if left_a or left_b:
        score -= 0.5
    return score >= THRESHOLD, "score %.1f" % score


def conflicted(p: Profiles, members: list) -> bool:
    """A linked group is one person only if no two of its names or careers contradict."""
    for a, b in itertools.combinations(members, 2):
        _, left_a, left_b = align(p.toks[a], p.toks[b])
        if p.met(a, b) or (left_a and left_b):
            return True
    return False


def plan(p: Profiles, merges: dict, decisions: dict, log=print):
    """Accepted pairs grouped into identities; returns (groups, accepted pairs, contested groups)."""
    todo = [(i, j) for i, j in candidates(p)
            if p.keys[i] not in merges and p.keys[j] not in merges
            and pair_key(p.keys[i], p.keys[j]) not in decisions]
    yes = [(i, j) for i, j in todo if decide(p, i, j)[0]]
    parent = {}

    def find(x):
        parent.setdefault(x, x)
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    for i, j in yes:
        parent[find(i)] = find(j)
    groups = collections.defaultdict(list)
    for x in list(parent):
        groups[find(x)].append(x)
    kept = [sorted(g) for g in groups.values() if not conflicted(p, g)]
    ok = {x for g in kept for x in g}
    pairs = [(i, j) for i, j in yes if i in ok]
    log("auto-merge: %d candidate pairs undecided, %d accepted" % (len(todo), len(yes)))
    return kept, pairs, len(groups) - len(kept)


def merge(p: Profiles, merges: dict, decisions: dict, log=print) -> list:
    """Fold each accepted group into its fullest career; returns the (alias, root) keys merged."""
    groups, pairs, contested = plan(p, merges, decisions, log=log)
    done = []
    for g in groups:
        root = max(g, key=lambda x: (len(p.career[x]), len(p.names[x])))
        for x in g:
            if x != root:
                apply_merge(merges, p.keys[x], p.keys[root])
                done.append((p.keys[x], p.keys[root]))
    for i, j in pairs:
        decisions[pair_key(p.keys[i], p.keys[j])] = "y"
    log("auto-merge: %d profiles into %d identities, %d contested groups left alone"
        % (len(done), len(groups), contested))
    return done


def run(conn, data: dict, rest: dict, log=print) -> int:
    """Merge whatever the current profiles call for; returns how many profiles merged away."""
    merges = db.get_artifact(conn, "id_merges", {})
    decisions = db.get_artifact(conn, DECISIONS, {})
    done = merge(Profiles(data, rest), merges, decisions, log=log)
    if done:
        db.set_artifact(conn, "id_merges", merges)
        db.set_artifact(conn, DECISIONS, decisions)
    return len(done)


def published(conn):
    with conn.cursor() as cur:
        cur.execute("SELECT name, body FROM payloads WHERE encoding = 'gz'")
        blobs = {n: bytes(b) for n, b in cur.fetchall()}
    return json.loads(gzip.decompress(blobs["data"])), json.loads(gzip.decompress(blobs["rest"]))


def main():
    ap = argparse.ArgumentParser(description="Auto-merge duplicate profiles in the published payload.")
    ap.add_argument("--dry-run", action="store_true", help="print the merges without writing them")
    ap.add_argument("--show", type=int, default=0, metavar="N", help="print N of the merges")
    args = ap.parse_args()
    conn = db.connect()
    data, rest = published(conn)
    p = Profiles(data, rest)
    merges = db.get_artifact(conn, "id_merges", {})
    decisions = db.get_artifact(conn, DECISIONS, {})
    done = merge(p, merges, decisions)
    name = dict(zip(p.keys, p.names, strict=True))
    for alias, root in done[:args.show]:
        print("  %-34s -> %s" % (name.get(alias, alias)[:34], name.get(root, root)))
    if done and not args.dry_run:
        db.set_artifact(conn, "id_merges", merges)
        db.set_artifact(conn, DECISIONS, decisions)
        print("written; a fit_only rebuild puts them on the site")


if __name__ == "__main__":
    main()
