from debate_ratings import board, payload


def setup_function(f):
    board.configure({"skip": [], "alias": {}})


def run(teams_of, days_of=None):
    players = sorted(teams_of)
    days = days_of or {i: list(range(0, 10 * len(teams_of[i]), 10)) for i in players}
    return board.institutions(players, teams_of, days)


def test_repeated_prefix_becomes_institution():
    teams = {0: ["Alpha A", "Alpha B"], 1: ["Alpha B", "Alpha C"],
             2: ["Alpha D"], 3: ["Alpha A"]}
    out = run(teams)
    assert all(out[i]["inst"] == "Alpha" for i in teams)


def test_small_institutions_are_cut():
    teams = {0: ["Alpha A", "Alpha B"], 1: ["Alpha B", "Alpha C"],
             2: ["Alpha D"], 3: ["Alpha A"],
             4: ["Zeta A", "Zeta B"], 5: ["Zeta C", "Zeta A"]}
    out = run(teams)
    assert out[4]["inst"] is None
    assert out[5]["inst"] is None


def test_unknown_team_filled_from_nearest_tournament():
    teams = {0: ["Alpha A", "Alpha B", "Mystery Duo"], 1: ["Alpha B", "Alpha C"],
             2: ["Alpha D"], 3: ["Alpha A"]}
    days = {0: [0, 10, 20], 1: [0, 10], 2: [0], 3: [0]}
    out = run(teams, days)
    assert out[0]["at"] == ["alpha", "alpha", "alpha"]


def test_alias_maps_to_canonical_name():
    board.configure({"skip": [], "alias": {"alpha": "Alpha University"}})
    teams = {0: ["Alpha A", "Alpha B"], 1: ["Alpha B", "Alpha C"],
             2: ["Alpha D"], 3: ["Alpha A"]}
    out = run(teams)
    assert out[0]["inst"] == "Alpha University"


def _world_with_two_institutions():
    w = payload.World()
    w.tours = [{"i": t, "row": t + 1, "n": "Fixture Open %d" % t,
                "d": "202%d-03-01" % (4 + t), "rounds": [], "xm": [],
                "field": 8, "partial": 0} for t in range(2)]
    for i in range(8):
        team = "Alpha University" if i < 4 else "Beta College"
        # institutions need two sightings per person and four people to register
        w.careers[i] = [[t, "%s %s" % (team, "ABCD"[i % 4]), [], []] for t in range(2)]
    return w


def test_build_board_hides_named_institutions():
    board.configure({"skip": [], "alias": {}})
    w = _world_with_two_institutions()

    _rows, _at, inames, _aka = payload.build_board(w, 8, set())
    assert "alpha university" in inames and "beta college" in inames

    rows, inst_at, inames, _aka = payload.build_board(w, 8, {"alpha university"})
    assert "alpha university" not in inames
    assert "beta college" in inames
    assert all("alpha university" not in r[9] for r in rows)
    assert all(r[8] != "alpha university" for r in rows)
    assert all(k != "alpha university" for at in inst_at for k in at)


def _world_that_returns_to_its_first_institution():
    """Four people go Alpha → Beta → Alpha; four stay at Beta so it registers."""
    w = payload.World()
    w.tours = [{"i": t, "row": t + 1, "n": "Fixture Open %d" % t,
                "d": "202%d-03-01" % (4 + t), "rounds": [], "xm": [],
                "field": 8, "partial": 0} for t in range(3)]
    for i in range(8):
        seq = (["Alpha University", "Beta College", "Alpha University"] if i < 4
               else ["Beta College"] * 3)
        w.careers[i] = [[t, "%s %s" % (seq[t], "ABCD"[i % 4]), [], []] for t in range(3)]
    return w


def test_hiding_a_middle_affiliation_leaves_no_repeat():
    board.configure({"skip": [], "alias": {}})
    w = _world_that_returns_to_its_first_institution()

    rows, _at, _names, _aka = payload.build_board(w, 8, set())
    assert rows[0][9] == ["alpha university", "beta college", "alpha university"]

    rows, _at, _names, _aka = payload.build_board(w, 8, {"beta college"})
    assert rows[0][9] == ["alpha university"]
