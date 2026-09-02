from debate_ratings.rooms import Builder, is_anon, side_code, usable_room_scores


def tab_record():
    rooms = [[
        {"team": "Alpha A", "sort": 4, "text": "1st", "side": "OG", "roster": None},
        {"team": "Beta B", "sort": 3, "text": "2nd", "side": "OO", "roster": None},
        {"team": "Gamma C", "sort": 2, "text": "3rd", "side": "CG", "roster": None},
        {"team": "Delta D", "sort": 1, "text": "4th", "side": "CO", "roster": None},
    ]]
    return {
        "row": 42, "name": "Test IV", "url": "http://x", "date": "2024-03-01",
        "teams": {
            "Alpha A": ["Ann One", "Bob Two"],
            "Beta B": ["Cat Three", "Dan Four"],
            "Gamma C": ["Eve Five", "Fay Six"],
            "Delta D": ["Gil Seven", "Hal Eight"],
        },
        "speaks": {
            "Ann One": {"team": "Alpha A", "scores": [78.0]},
            "Bob Two": {"team": "Alpha A", "scores": [77.0]},
            "Cat Three": {"team": "Beta B", "scores": [76.0]},
            "Dan Four": {"team": "Beta B", "scores": [76.0]},
            "Eve Five": {"team": "Gamma C", "scores": [75.0]},
            "Fay Six": {"team": "Gamma C", "scores": [75.5]},
            "Gil Seven": {"team": "Delta D", "scores": [74.0]},
            "Hal Eight": {"team": "Delta D", "scores": [73.0]},
        },
        "rounds": [{"seq": 1, "stage": "P", "name": "Round 1", "rooms": rooms}],
        "prelim_seqs": [1],
    }


def test_build_tournament_prelim_room():
    b = Builder({}, [], {})
    out = b.build_tournament(tab_record())
    assert len(out) == 1
    room = out[0]
    assert room["stage"] == "P"
    assert room["row"] == 42
    teams = room["teams"]
    assert [t["sort"] for t in teams] == [4.0, 3.0, 2.0, 1.0]
    assert teams[0]["roster"] == ["ann one", "bob two"]
    assert teams[0]["speaks"] == [78.0, 77.0]
    assert teams[0]["side"] == "og"
    assert not any(t["iron"] for t in teams)
    assert len(b.speak_devs) == 4


def test_merges_applied_to_roster():
    b = Builder({"ann one": "annabelle one"}, [], {})
    out = b.build_tournament(tab_record())
    assert out[0]["teams"][0]["roster"][0] == "annabelle one"


def test_excluded_row_skipped():
    b = Builder({}, [42], {})
    assert b.build_tournament(tab_record()) == []


def test_non_bp_tournament_skipped():
    rec = tab_record()
    for rd in rec["rounds"]:
        rd["rooms"] = [rm[:2] for rm in rd["rooms"]]
    b = Builder({}, [], {})
    assert b.build_tournament(rec) == []
    assert b.stats["skipped: non-BP tournament"] == 1


def test_implausible_speaks_dropped():
    rec = tab_record()
    rec["speaks"]["Hal Eight"]["scores"] = [40.0]
    b = Builder({}, [], {})
    out = b.build_tournament(rec)
    assert out[0]["teams"][0]["speaks"] is None
    assert b.stats["room speaks dropped (implausible)"] == 1


def test_anon_and_sides():
    assert is_anon("Swing Speaker")
    assert is_anon("TBD")
    assert not is_anon("Ann One")
    assert side_code("Opening Government", 4) == "og"
    assert side_code("OG", 2) == "aff"
    assert usable_room_scores({"a": 78.0, "b": 74.0})
    assert not usable_room_scores({"a": 78.0, "b": 50.0})
