from debate_ratings import automerge, payload, pipeline, rooms

from . import world
from .fakes import FakeConn

QUIET = lambda *a, **k: None


def make(*people):
    """(name, [(tournament year, teammate index or None), ...], inst) -> payload-shaped data, rest."""
    tours, careers, board, inst_at = [], {}, [], []
    for i, (_name, stints, inst) in enumerate(people):
        car = []
        for year, mate in stints:
            tours.append({"d": "%d-06-01" % year})
            car.append([len(tours) - 1, "", [] if mate is None else [mate], []])
        careers[str(i)] = car
        board.append([0] * 8 + [inst])
        inst_at.append([inst] * len(car))
    data = {"players": [[name] for name, _, _ in people], "tournaments": tours, "board": board,
            "instAt": inst_at, "instNames": {}, "instRegion": {"uni": "Europe", "other": "Asia"}}
    return data, {"careers": careers}


def decided(*people):
    p = automerge.Profiles(*make(*people))
    return automerge.decide(p, 0, 1)[0]


def test_typo_in_one_part_merges():
    assert decided(("Hassaan Ahmed Chaudhary", [(2024, None)], "uni"),
                   ("Hassaan Ahmed Chaudhery", [(2025, None)], None))


def test_extra_part_on_one_side_merges():
    assert decided(("Nikhil Santhoshkumar Pillai", [(2024, None), (2024, None)], "uni"),
                   ("Nikhil S Pillai", [(2023, None)], "uni"))


def test_different_parts_on_both_sides_never_merge():
    assert not decided(("Syed Muhammad Hadi", [(2025, None)], "uni"),
                       ("Syed Muhammad Abdullah", [(2025, None)], "uni"))


def test_reordered_name_needs_close_careers():
    assert not decided(("Li Ruyi", [(2020, None)], None), ("Ruyi Li", [(2023, None)], None))
    assert decided(("Li Ruyi", [(2022, None)], "uni"), ("Ruyi Li", [(2023, None)], "uni"))


def test_far_apart_careers_never_merge():
    assert not decided(("Tumo Raymond Moremedi", [(2005, None)], "uni"),
                       ("Tumo R. Moremedi", [(2016, None)], "uni"))


def test_people_who_met_are_not_candidates():
    p = automerge.Profiles(*make(("Tadeas Navratil", [(2026, 1)], None),
                                 ("Tadeas Navrátil", [(2026, 0)], None)))
    assert list(automerge.candidates(p)) == []


def test_contested_short_name_is_left_alone():
    p = automerge.Profiles(*make(("Maria Wanjiku", [(2024, None)], "uni"),
                                 ("Maria Goretti Wanjiku", [(2024, None)], "uni"),
                                 ("Maria Njeri Wanjiku", [(2024, None)], "uni")))
    groups, _, contested = automerge.plan(p, {}, {}, log=QUIET)
    assert groups == [] and contested == 1


def test_run_merges_into_fuller_career_and_remembers():
    data, rest = make(("Gwendolen Da Sousa Correa", [(2020, None), (2021, None)], "uni"),
                      ("Gwendolen Da Sousa Correra", [(2021, None)], "uni"),
                      ("Tadeas Navratil", [(2026, None)], None),
                      ("Tadeas Navrátil", [(2026, None)], None))
    conn = FakeConn(artifacts={automerge.DECISIONS: {"tadeas navratil | tadeas navrátil": "n"}})
    assert automerge.run(conn, data, rest, log=QUIET) == 1
    assert conn.artifacts["id_merges"] == {"gwendolen da sousa correra": "gwendolen da sousa correa"}
    assert conn.artifacts[automerge.DECISIONS]["gwendolen da sousa correa | gwendolen da sousa correra"] == "y"


def test_identities_line_up_with_profile_keys():
    conn = world.make_conn()
    pipeline.run_pipeline(conn, fit_only=True, log=QUIET)
    data, rest = payload.identities(conn)
    assert len(rest["keys"]) == len(data["players"])
    assert set(rest["careers"]) <= {str(i) for i in range(len(data["players"]))}


def test_rebuild_rebuilds_rooms_after_merging(monkeypatch):
    conn = world.make_conn()
    calls = []
    real = rooms.rebuild_all
    monkeypatch.setattr(rooms, "rebuild_all", lambda *a: calls.append(1) or real(*a))
    monkeypatch.setattr(automerge, "run", lambda *a, **k: 1)
    pipeline.run_pipeline(conn, fit_only=True, log=QUIET)
    assert len(calls) == 2
