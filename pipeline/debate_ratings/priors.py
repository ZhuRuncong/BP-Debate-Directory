from . import db

CLASS_LEVEL = {"S-E": 0,
               "S-D": 1, "S-C": 1,
               "S-B": 2,
               "S-A": 3, "S-A+": 3,
               "S-AA": 4, "S-AA+": 4,
               "S-AAA": 5, "S-AAA+": 5,
               "S-WUDC": 6}


def row_levels(conn):
    with conn.cursor() as cur:
        cur.execute("SELECT row_id, speaking_class FROM tournaments")
        rows = cur.fetchall()
    out = {}
    for row_id, cls in rows:
        lvl = CLASS_LEVEL.get((cls or "").strip())
        if lvl is not None:
            out[row_id] = lvl
    return out


def first_appearances(conn):
    lv = row_levels(conn)
    first = {}

    def see(name, t, lvl):
        nf = name.casefold()
        if nf.startswith("anon::") or nf.startswith("side::"):
            return
        cur = first.get(name)
        if cur is None or t < cur[0]:
            first[name] = (t, lvl)

    for r in db.iter_rooms(conn):
        lvl = lv.get(r["row"])
        if lvl is None:
            continue
        for tm in r["teams"]:
            for p in tm["roster"]:
                see(p, r["t"], lvl)
    for g in db.iter_extra_games(conn, ("sheets", "videos", "hague")):
        lvl = lv.get(g["row"])
        if lvl is None:
            continue
        for team in g["c"]:
            for pn in team:
                see(pn, g["t"], lvl)
    return first


def quality_prior_mus(conn, strength):
    if not strength:
        return {}
    first = first_appearances(conn)
    if not first:
        return {}
    shape = {int(k): v for k, v in
             (db.get_artifact(conn, "tier_shape", {}) or {}).items()}
    offs = [shape.get(lvl, 0.0) for _, lvl in first.values()]
    mean_off = sum(offs) / len(offs)
    return {p: strength * (shape.get(lvl, 0.0) - mean_off)
            for p, (t, lvl) in first.items()}
