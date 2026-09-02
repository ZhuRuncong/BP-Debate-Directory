from debaterskill import Debate, Motion, Outround, Team


def room_to_debate(r, motions=False, motion_map=None):
    teams = []
    for tm in r["teams"]:
        iron = bool(tm.get("iron")) and len(tm["roster"]) == 1
        teams.append(Team(name="%s::%s" % (r["row"], tm["team"]),
                          speakers=tm["roster"],
                          speaks=tm["speaks"],
                          side=tm["side"] or None,
                          points=tm["sort"],
                          iron=iron,
                          advancing=(tm["sort"] == 2.0) if r["stage"] == "E" else None))
    motion = None
    if motions:
        slug = ((motion_map or {}).get((r["row"], str(r["seq"])))
                or "%s::%s" % (r["row"], r["seq"]))
        motion = Motion(slug)
    cls = Outround if r["stage"] == "E" else Debate
    return cls(teams, r["t"], motion=motion, scale=r["scale"], chair=r.get("chair"))


def load_debates(rooms_iter, rows=None, motions=False, max_day=None, stage=None,
                 motion_map=None):
    out = []
    for r in rooms_iter:
        if rows is not None and r["row"] not in rows:
            continue
        if max_day is not None and r["t"] > max_day:
            continue
        if stage is not None and r["stage"] != stage:
            continue
        out.append(room_to_debate(r, motions=motions, motion_map=motion_map))
    return out


def add_extras(tab, games_iter, speak_scale, motions=False, max_day=None,
               scale_mult=1.0):
    n = 0
    for g in games_iter:
        if max_day is not None and g["t"] > max_day:
            continue
        cont = g["obs"] == "C"
        r = list(g["r"])
        if cont:
            sc = (g.get("sc") or speak_scale) * scale_mult
            r = [x / sc for x in r]
        lineups = [list(team) for team in g["c"]]
        if motions:
            motion = Motion("%s::%s" % (g["row"], g["seq"]))
            sides = g.get("sides") or []
            for i, lineup in enumerate(lineups):
                sd = sides[i] if i < len(sides) else ""
                if sd:
                    lineup.append((motion, sd))
        tab.add_ballot(lineups, r, day=g["t"], continuous=cont, tag="raw")
        n += 1
    return n
