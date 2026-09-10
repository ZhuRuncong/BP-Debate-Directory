import argparse
import collections
import gzip
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "pipeline"))

from debate_ratings import db
from debate_ratings.idnorm import canon, name_tokens


def load_payload(conn):
    with conn.cursor() as cur:
        cur.execute("SELECT name, body FROM payloads WHERE encoding = 'gz'")
        blobs = {n: bytes(b) for n, b in cur.fetchall()}
    if "data" not in blobs or "rest" not in blobs:
        raise SystemExit("no payload stored yet - run the pipeline first")
    return (json.loads(gzip.decompress(blobs["data"])),
            json.loads(gzip.decompress(blobs["rest"])))


def subseq(short, long_):
    it = iter(long_)
    return all(any(t == x or (len(t) == 1 and x.startswith(t)) for x in it)
               for t in short)


def dropped(short, long_):
    rest, out = list(long_), []
    for t in short:
        for k, x in enumerate(rest):
            if t == x or (len(t) == 1 and x.startswith(t)):
                out.extend(rest[:k])
                rest = rest[k + 1:]
                break
    return out + rest


def propose(data, rest):
    players = data["players"]
    toks = {i: name_tokens(p[0]) for i, p in enumerate(players)}
    seen = collections.Counter()
    teammate = collections.defaultdict(set)
    for i, car in rest["careers"].items():
        i = int(i)
        seen[i] = len(car)
        for e in car:
            for m in e[2]:
                teammate[i].add(m)

    by_ends = collections.defaultdict(list)
    for i, t in toks.items():
        if len(t) >= 2:
            by_ends[(t[0], t[-1])].append(i)

    out = []
    for ks in by_ends.values():
        ks.sort(key=lambda x: len(toks[x]))
        for a in range(len(ks)):
            for b in range(a + 1, len(ks)):
                lo, hi = ks[a], ks[b]
                if len(toks[lo]) == len(toks[hi]) or not subseq(toks[lo], toks[hi]):
                    continue
                if not all(len(t) == 1 for t in dropped(toks[lo], toks[hi])):
                    continue
                if hi in teammate[lo] or lo in teammate[hi]:
                    continue
                root, alias = (hi, lo) if seen[hi] >= seen[lo] else (lo, hi)
                out.append((players[alias][0], players[root][0]))
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true", help="write them into id_merges")
    args = ap.parse_args()

    conn = db.connect()
    data, rest = load_payload(conn)
    pairs = propose(data, rest)
    merges = db.get_artifact(conn, "id_merges", {})
    new = {canon(a): canon(r) for a, r in pairs
           if canon(a) not in merges and canon(a) != canon(r)}
    for alias, root in pairs:
        print("  %-34s -> %s" % (alias[:32], root))
    print("%d proposals, %d not already merged" % (len(pairs), len(new)))

    if not args.apply:
        print("(dry run; pass --apply to write them)")
        return
    chained = {a: r for a, r in new.items() if r in merges or r in new}
    for a in chained:
        del new[a]
    merges.update(new)
    db.set_artifact(conn, "id_merges", merges)
    print("applied %d; skipped %d that would chain" % (len(new), len(chained)))


if __name__ == "__main__":
    main()
