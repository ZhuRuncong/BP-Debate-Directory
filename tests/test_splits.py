from debate_ratings import splits

from .fakes import FakeConn

QUIET = lambda *a, **k: None


def world():
    """Liam Evans at a Canadian and a European tournament, each with a local field."""
    tours = [{"d": "2025-10-01", "row": 1}, {"d": "2026-01-31", "row": 2}]
    careers = {"0": [[0, "HH Peppercorns", [], []], [1, "TalTech Trio", [], []]],
               "1": [[0, "McGill A", [], []]], "2": [[1, "Tartu A", [], []]]}
    inst_at = [["hart house", "taltech"], ["mcgill"], ["tartu"]]
    data = {"players": [["Liam Evans"], ["Ann Local"], ["Eva Local"]], "tournaments": tours,
            "board": [None] * 3, "instAt": inst_at, "instNames": {},
            "instRegion": {"hart house": "Americas", "mcgill": "Americas",
                           "taltech": "Europe", "tartu": "Europe"}}
    return data, {"careers": careers, "keys": ["liam evans", "ann local", "eva local"]}


def test_a_region_rule_splits_off_that_regions_appearances():
    conn = FakeConn(artifacts={splits.REGION_SPLITS: {"liam evans": {"Europe": "europe"}}})
    assert splits.run(conn, *world(), log=QUIET) == 1
    assert conn.artifacts["id_splits"] == {"2": {"TalTech Trio": {"liam evans": "europe"}}}
    assert splits.run(conn, *world(), log=QUIET) == 0  # already split


def test_no_rule_no_splits():
    conn = FakeConn()
    assert splits.run(conn, *world(), log=QUIET) == 0
    assert "id_splits" not in conn.artifacts
