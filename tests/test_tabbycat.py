import collections
import json

from debate_ratings.payload import elim_info
from debate_ratings.rooms import find_chair, speaks_by_team
from debate_ratings.tabbycat import group_rooms, parse_results_page, parse_speaker_tab


def row(team, sort=1, debate=None, opp=None, mates=None):
    return {"team": team, "sort": sort, "text": "", "side": "",
            "debate": debate, "opp": opp, "roster": None, "mates": mates}


def test_group_by_debate_link():
    rows = [row("a", debate=7), row("b", debate=7), row("c", debate=8), row("d", debate=8)]
    rooms, how = group_rooms(rows)
    assert how == "debate-link"
    assert sorted(len(r) for r in rooms) == [2, 2]


def test_group_by_mates_popover():
    rows = [row(t, mates=m) for t, m in
            (("a", ["a", "b"]), ("b", ["a", "b"]), ("c", ["c", "d"]), ("d", ["c", "d"]))]
    rooms, how = group_rooms(rows)
    assert how == "mates-popover"
    assert len(rooms) == 2


def test_group_by_reciprocal_opponents():
    rows = [row("a", opp=("Won against", "b")), row("b", opp=("Lost to", "a")),
            row("c", opp=("Won against", "d")), row("d", opp=("Lost to", "c"))]
    rooms, how = group_rooms(rows)
    assert how == "vs-pair"
    assert {frozenset((r[0]["team"], r[1]["team"])) for r in rooms} == \
        {frozenset("ab"), frozenset("cd")}


def test_group_consecutive_fours_checks_sorts():
    rows = [row(t, sort=s) for t, s in
            (("a", 1), ("b", 2), ("c", 3), ("d", 4),
             ("e", 4), ("f", 3), ("g", 2), ("h", 1))]
    rooms, how = group_rooms(rows)
    assert how == "consec4"
    assert len(rooms) == 2

    bad = [row(t, sort=1) for t in "abcdefgh"]
    rooms, how = group_rooms(bad)
    assert rooms is None


def test_single_small_table_is_one_room():
    rows = [row("a", 1), row("b", 2)]
    rooms, how = group_rooms(rows)
    assert how == "single"
    assert rooms == [rows]


def page_with(tables):
    return "var x = {tablesData: %s};" % json.dumps(tables)


def test_parse_results_page_counts_drops():
    table = {"head": [{"key": "team"}, {"key": "result"}],
             "data": [[{"text": "Alpha A"}, {"sort": 1, "text": "1st"}],
                      [],
                      [{"text": "Beta B"}, {"sort": 2, "text": "2nd"}]]}
    stats = collections.Counter()
    out = parse_results_page(page_with([table]), stats=stats)
    assert len(out) == 1
    assert len(out[0]) == 2
    assert stats["result_rows_parsed"] == 2
    assert stats["result_rows_dropped"] == 1


def test_parse_speaker_tab_scores_and_drops():
    table = {"head": [{"key": "name"}, {"key": "team"}, {"title": "R1"}, {"title": "R2"}],
             "data": [[{"text": "Ann One"}, {"text": "Alpha A"},
                       {"text": "76.0"}, {"text": "n/a"}],
                      []]}
    stats = collections.Counter()
    out = parse_speaker_tab(page_with([table]), stats=stats)
    assert out["Ann One"] == {"team": "Alpha A", "scores": [76.0, None]}
    assert stats["speaker_rows_parsed"] == 1
    assert stats["speaker_rows_dropped"] == 1


def test_speaker_tab_keeps_a_name_repeated_across_teams():
    table = {"head": [{"key": "name"}, {"key": "team"}, {"title": "R1"}],
             "data": [[{"text": "Speaker 1"}, {"text": "Backpack"}, {"text": "81"}],
                      [{"text": "Speaker 1"}, {"text": "Oxford C"}, {"text": "74"}]]}
    rec = {"speaks": parse_speaker_tab(page_with([table]))}
    by_team = speaks_by_team(rec)
    assert by_team["backpack"]["Speaker 1"] == [81.0]
    assert by_team["oxford c"]["Speaker 1"] == [74.0]


def test_elim_info():
    assert elim_info("Open Quarterfinal") == ("Open", 2)
    assert elim_info("ESL Grand Final") == ("ESL", 0)
    assert elim_info("Novice Semi-Final") == ("Novice", 1)
    assert elim_info("Double-Octofinals") == ("Open", 4)
    assert elim_info("Round 5") == ("Open", None)


def test_find_chair():
    lookup = {"1": [(frozenset("abcd"), "j1"), (frozenset("wxyz"), "j2")]}
    assert find_chair(lookup, 1, frozenset("abcd")) == "j1"
    assert find_chair(lookup, 1, frozenset("abce")) == "j1"
    assert find_chair(lookup, 1, frozenset("abxy")) is None
    assert find_chair(lookup, 2, frozenset("abcd")) is None
    tied = {"1": [(frozenset("abcx"), "j1"), (frozenset("abcy"), "j2")]}
    assert find_chair(tied, 1, frozenset("abcd")) is None
