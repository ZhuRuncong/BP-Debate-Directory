import collections
import re

from .idnorm import canon, clean_display, name_tokens, signature

# Trailing tab decorations: breaking-adj stars/daggers and "- O" orallist marks.
STAR = re.compile(r"(?:\s*[-–]\s*O)?\s*[\*†‡]*\s*$")
PLACEHOLDER = re.compile(
    r"^(?:adj|adjs|adjudicator|judge|panellist|panelist|panel|chair|trainee|"
    r"swing|bye|redacted|reserve|spare|volunteer|guest|tba|tbc|tbd|n/?a|"
    r"unknown|test|vacant|none)"
    r"(?:\s*[\-#no.]*\s*\d*\s*)$", re.IGNORECASE)


def jcanon(name):
    return canon(STAR.sub("", (name or "").strip()))


def is_anon(name):
    c = jcanon(name)
    if len(c) < 2:
        return True
    return bool(PLACEHOLDER.match(c))


def display(name):
    d = clean_display(STAR.sub("", (name or "").strip()))
    if d and d == d.upper() and any(len(w) > 3 for w in re.split(r"[^A-Za-z]+", d)):
        d = d.lower().title()
    return d


def build(judge_records: list[dict]) -> dict:
    """Cluster judge name variants into identities.

    Union-find over canonical names: two variants merge only with co-panelist
    evidence and never if they ever sat on the same panel (distinct people).
    Returns {"d": identity -> display name, "p": row -> seq -> panels}.
    """
    panels = []
    raw_disp = collections.Counter()
    for r in judge_records:
        row = r["row"]
        for seq, ps in (r.get("rounds") or {}).items():
            for pi, p in enumerate(ps):
                crew = []
                for ai, a in enumerate(p["a"]):
                    if is_anon(a["n"]):
                        crew.append(("anon::%s::%s::%d::%d" % (row, seq, pi, ai), a["r"]))
                        continue
                    k = jcanon(a["n"])
                    crew.append((k, a["r"]))
                    raw_disp[(k, display(a["n"]))] += 1
                if crew:
                    panels.append((row, seq, crew, p["t"]))

    appears = collections.Counter()
    mates = collections.defaultdict(set)
    same_panel = collections.defaultdict(set)
    for pi, (_row, _seq, crew, _t) in enumerate(panels):
        ks = [k for k, _r in crew if not k.startswith("anon::")]
        for k in ks:
            appears[k] += 1
            same_panel[k].add(pi)
            for o in ks:
                if o != k:
                    mates[k].add(o)
    keys = set(appears)

    parent = {k: k for k in keys}

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    pset = {k: set(same_panel[k]) for k in keys}
    mset = {k: set(mates[k]) for k in keys}

    def union(a, b):
        ra, rb = find(a), find(b)
        if ra == rb:
            return
        if pset[ra] & pset[rb]:
            return  # appeared on the same panel: cannot be one person
        if appears[ra] < appears[rb]:
            ra, rb = rb, ra
        parent[rb] = ra
        pset[ra] |= pset[rb]
        mset[ra] |= mset[rb]

    def evidence(a, b):
        # Shared co-panelists suggest the two variants are the same person.
        ra, rb = find(a), find(b)
        return len((mset[ra] & mset[rb]) - {ra, rb, a, b})

    def subseq(short, long_):
        # Ordered token subsequence; a single letter matches as an initial.
        it = iter(long_)
        return all(any(t == x or (len(t) == 1 and x.startswith(t)) for x in it)
                   for t in short)

    prop = []
    by_sig = collections.defaultdict(list)
    for k in keys:
        by_sig[signature(k)].append(k)
    for sig, ks in by_sig.items():
        if not sig or len(ks) < 2:
            continue
        for i in range(len(ks)):
            for j in range(i + 1, len(ks)):
                e = evidence(ks[i], ks[j])
                if e:
                    prop.append((e, ks[i], ks[j]))

    toks = {k: name_tokens(k) for k in keys}
    by_fl = collections.defaultdict(list)
    for k, t in toks.items():
        if len(t) >= 2:
            by_fl[(t[0], t[-1])].append(k)
    for _fl, ks in by_fl.items():
        if len(ks) < 2:
            continue
        ks = sorted(ks, key=lambda x: len(toks[x]))
        for i in range(len(ks)):
            for j in range(i + 1, len(ks)):
                a, b = toks[ks[i]], toks[ks[j]]
                if len(a) == len(b) or not subseq(a, b):
                    continue
                e = evidence(ks[i], ks[j])
                if e:
                    prop.append((e, ks[i], ks[j]))

    # Strongest evidence first, so weak proposals meet already-grown clusters.
    prop.sort(key=lambda x: -x[0])
    for _e, a, b in prop:
        union(a, b)

    best = collections.defaultdict(collections.Counter)
    for (k, d), n in raw_disp.items():
        if k in parent:
            best[find(k)][d] += n
    disp = {}
    for r, c in best.items():
        # Prefer spellings of the root variant itself over merged-in aliases.
        own = [(n, v) for n, v in c.items() if canon(n) == r]
        disp[r] = max(own or c.items(), key=lambda nv: nv[1])[0]

    out = {"d": disp, "p": {}}
    for row, seq, crew, teams in panels:
        rec = out["p"].setdefault(str(row), {}).setdefault(str(seq), [])
        rec.append([[[find(k) if k in parent else k, r] for k, r in crew], teams])
    return out
