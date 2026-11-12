from debate_ratings import splits

from .fakes import FakeConn

QUIET = lambda *a, **k: None
UCLA = "will ryan\x1fucla"


def world(*new):
    """Two Will Ryans already sorted (Sydney plain, UCLA split off), plus `new` appearances
    on the plain profile: (date, team, partner index)."""
    people = [("Will Ryan", "will ryan"), ("Will Ryan", UCLA), ("Bibi Kuo", "bibi kuo"),
              ("Richard Colgrove", "richard colgrove"), ("Jack Glass", "jack glass"),
              ("Belle Ho", "belle ho"), ("Sam New", "sam new")]
    insts = {2: "sydney", 3: "ucla", 4: "sydney", 5: "ucla", 6: "unsw"}
    tours, careers = [], {str(i): [] for i in range(len(people))}
    inst_at = [[] for _ in people]

    def play(who, partner, date, team, inst):
        tours.append({"d": date, "row": len(tours) + 1, "n": "T%d" % len(tours)})
        tid = len(tours) - 1
        careers[str(who)].append([tid, team, [partner], []])
        careers[str(partner)].append([tid, team, [who], []])
        inst_at[who].append(inst)
        inst_at[partner].append(insts[partner])

    play(0, 2, "2025-03-01", "Sydney Kuo and Ryan", "sydney")
    play(0, 4, "2025-04-01", "USU 2", "sydney")
    play(1, 3, "2025-03-15", "UCLA A", "ucla")
    play(1, 5, "2025-04-15", "UCLA B", "ucla")
    for date, team, partner in new:
        play(0, partner, date, team, None)
    data = {"players": [[n] for n, _ in people], "tournaments": tours, "board": [None] * len(people),
            "instAt": inst_at, "instNames": {}, "instRegion": {"sydney": "Australia", "unsw": "Australia",
                                                              "ucla": "Americas"}}
    return data, {"careers": careers, "keys": [k for _, k in people]}


def conn_for(**extra):
    return FakeConn(artifacts={splits.SHARED: {"will ryan": ["ucla"]}, **extra})


def test_a_newly_declared_name_takes_what_is_there_as_sorted():
    conn = conn_for()
    assert splits.run(conn, *world(("2025-06-01", "Mystery", 6)), log=QUIET) == 0
    assert "id_splits" not in conn.artifacts and len(conn.artifacts[splits.SEEN]["will ryan"]) == 5


def test_a_known_partner_sends_a_new_appearance_to_that_person():
    conn = conn_for()
    splits.run(conn, *world(), log=QUIET)
    assert splits.run(conn, *world(("2025-06-01", "Pacific Coast", 3)), log=QUIET) == 1
    assert conn.artifacts["id_splits"] == {"5": {"Pacific Coast": {"will ryan": "ucla"}}}


def test_a_partner_from_a_known_institution_is_evidence_too():
    conn = conn_for()
    splits.run(conn, *world(), log=QUIET)
    # Sam is new but from UNSW, an Australian partner: the plain (Sydney) one, so nothing moves
    assert splits.run(conn, *world(("2025-06-01", "Spot the engineer", 6)), log=QUIET) == 0
    assert splits.REVIEW not in conn.artifacts


def test_the_calendar_rules_out_someone_already_elsewhere():
    conn = conn_for()
    splits.run(conn, *world(), log=QUIET)
    # the Sydney Will was at another tournament that weekend, so this must be the UCLA one
    assert splits.run(conn, *world(("2025-04-02", "Generic Name", 6)), log=QUIET) == 1


def test_an_appearance_nobody_clearly_fits_is_left_for_review():
    conn = conn_for()
    splits.run(conn, *world(), log=QUIET)
    data, rest = world(("2025-06-01", "Generic Name", 6))
    data["instAt"][6][-1] = None  # a partner with no institution on record
    data["instRegion"].pop("unsw")
    assert splits.run(conn, data, rest, log=QUIET) == 0
    [held] = conn.artifacts[splits.REVIEW]["will ryan"]
    assert held["team"] == "Generic Name" and held["partners"] == ["Sam New"]
    assert splits.run(conn, data, rest, log=QUIET) == 0  # asked once, not every rebuild
    assert len(conn.artifacts[splits.REVIEW]["will ryan"]) == 1


def test_every_spelling_that_merges_into_the_name_is_split():
    conn = conn_for(id_merges={"william ryan": "will ryan"})
    splits.run(conn, *world(), log=QUIET)
    assert splits.run(conn, *world(("2025-06-01", "Pacific Coast", 3)), log=QUIET) == 1
    assert conn.artifacts["id_splits"]["5"]["Pacific Coast"] == {"will ryan": "ucla", "william ryan": "ucla"}
    assert conn.artifacts["id_merges"]["william ryan\x1fucla"] == UCLA
