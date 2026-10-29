"""Keep apart different people who share a name, by where they debate.

A rule {"liam evans": {"Europe": "europe"}} in the region_splits artifact makes every
appearance of that name at a European tournament a separate person, now and in tabs
ingested later. Each appearance is written to id_splits, so one can still be moved by hand.
"""
from . import automerge, db

REGION_SPLITS = "region_splits"


def run(conn, data: dict, rest: dict, log=print) -> int:
    """Tag each untagged appearance a rule covers; returns how many were added."""
    rules = db.get_artifact(conn, REGION_SPLITS, {})
    if not rules:
        return 0
    splits = db.get_artifact(conn, "id_splits", {})
    p = automerge.Profiles(data, rest)
    added = 0
    for i, car in p.career.items():
        rule = rules.get(p.keys[i])
        if not rule:
            continue
        for e in car:
            tag = rule.get(p.region.get(e[0]))
            row = p.tours[e[0]].get("row")
            if not tag or row is None or not e[1]:
                continue
            names = splits.setdefault(str(row), {}).setdefault(e[1], {})
            if names.get(p.keys[i]) != tag:
                names[p.keys[i]] = tag
                added += 1
    if added:
        db.set_artifact(conn, "id_splits", splits)
        log("region splits: %d appearances moved to their own person" % added)
    return added
