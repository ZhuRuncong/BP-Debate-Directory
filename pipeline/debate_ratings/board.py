import collections
import re

INST_SKIP = set()
INST_ALIAS = {}
INST_MIN_PEOPLE = 4


def configure(alias_payload):
    global INST_SKIP, INST_ALIAS
    INST_SKIP = set(alias_payload.get('skip') or [])
    INST_ALIAS = alias_payload.get('alias') or {}

SUFFIX = re.compile(r'^(.*\S)\s+(?:[A-Za-z]{1,2}|\d{1,2})$')
NONWORD = re.compile(r'[^a-z0-9]+', re.IGNORECASE)
CAPS = re.compile(r'\b[a-z]')

def canon_inst(n):
    return INST_ALIAS.get(n.lower(), n) if n else n

def words(s):
    return ' ' + NONWORD.sub(' ', s.lower()).strip() + ' '

def inst_cands(teams):

    names = [(t or '').strip() for t in teams]
    norm = [words(s) if s else '' for s in names]
    cnt = {}
    for idx, nm in enumerate(names):
        if not nm:
            continue
        m = SUFFIX.match(nm)
        if not m:
            continue
        key = m.group(1).lower()
        if len(key) < 2 or key in INST_SKIP:
            continue
        c = cnt.setdefault(key, {'n': 0, 'forms': collections.Counter(), 'idxs': set()})
        c['n'] += 1
        c['idxs'].add(idx)
        c['forms'][m.group(1)] += 1

    for key, c in cnt.items():
        pat = words(key)
        for idx, n in enumerate(norm):
            if n and idx not in c['idxs'] and pat in n:
                c['n'] += 1
                c['idxs'].add(idx)
    out = {}
    for c in cnt.values():
        if c['n'] < 2:
            continue
        form = c['forms'].most_common(1)[0][0]
        shown = form
        if form == form.upper() and any(len(w) > 4 for w in re.split(r'[^A-Za-z]+', form)):
            shown = CAPS.sub(lambda m: m.group(0).upper(), form.lower())
        name = canon_inst(shown)
        key = name.lower()
        o = out.get(key)
        if o:
            o['n'] += c['n']
            o['idxs'] |= c['idxs']
        else:
            out[key] = {'key': key, 'name': name, 'n': c['n'], 'idxs': set(c['idxs'])}
    return list(out.values())

def inst_spans(name, reg):

    w = NONWORD.sub(' ', (name or '').lower()).split()
    cand = []
    for L in range(len(w), 0, -1):
        for i in range(len(w) - L + 1):
            k = ' '.join(w[i:i + L])
            if k in reg and k not in INST_SKIP:
                cand.append((L, i, k))
    cand.sort(key=lambda x: (-x[0], x[1]))
    used, out = set(), []
    for L, i, k in cand:
        if any(j in used for j in range(i, i + L)):
            continue
        used.update(range(i, i + L))
        out.append((i, k))
    out.sort()
    return out

def attribute(n, cands, days):

    at = [None] * n
    if not cands:
        return at
    rank = sorted(cands, key=lambda c: -c['n'])
    known = []
    for j in range(n):
        for c in rank:
            if j in c['idxs']:
                at[j] = c['key']
                break
        if at[j]:
            known.append(j)
    if not known:
        return at
    out = list(at)
    for j in range(n):
        if at[j]:
            continue
        best, gap = known[0], float('inf')
        for k in known:
            d = abs(days[k] - days[j])
            if d < gap:
                gap, best = d, k
        out[j] = at[best]
    return out

def institutions(players, teams_of, days_of):

    cands = {i: inst_cands(teams_of[i]) for i in players}
    forms = collections.defaultdict(collections.Counter)
    for cs in cands.values():
        for c in cs:
            forms[c['key']][c['name']] += 1
    REG = {k: m.most_common(1)[0][0] for k, m in forms.items()}

    for i in players:
        own = {c['key']: c for c in cands[i]}
        for j, nm in enumerate(teams_of[i]):
            nm = (nm or '').strip()
            if not nm:
                continue
            m = SUFFIX.match(nm)
            key = (m.group(1) if m else nm).lower()
            if key in INST_SKIP:
                continue
            if key not in REG:
                key = canon_inst(key).lower()
            if key not in REG:

                sp = inst_spans(nm, REG)
                if len(sp) != 1 or sp[0][0] != 0:
                    continue
                key = sp[0][1]
            c = own.get(key)
            if not c:
                c = {'key': key, 'name': REG[key], 'n': 1, 'idxs': set()}
                own[key] = c
                cands[i].append(c)
            c['idxs'].add(j)

    at = {i: attribute(len(teams_of[i]), cands[i], days_of[i]) for i in players}
    while True:
        seats = collections.Counter()
        for a in at.values():
            for k in {x for x in a if x is not None}:
                seats[k] += 1
        cut = {k for k, v in seats.items() if v < INST_MIN_PEOPLE}
        if not cut:
            break
        for i in players:
            if not any(c['key'] in cut for c in cands[i]):
                continue
            cands[i] = [c for c in cands[i] if c['key'] not in cut]
            at[i] = attribute(len(teams_of[i]), cands[i], days_of[i])

    out = {}
    for i in players:
        name_of = lambda k: REG.get(k) or next(
            (c['name'] for c in cands[i] if c['key'] == k), k)
        runs = []
        for k in at[i]:
            if k is not None and (not runs or runs[-1][0] != k):
                runs.append((k, name_of(k)))
        last = None
        for k in at[i]:
            if k is not None:
                last = k
        out[i] = {'inst': name_of(last) if last else None,
                  'insts': runs, 'at': at[i]}
    return out
