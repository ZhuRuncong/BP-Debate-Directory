from debate_ratings.adapters import load_debates, room_to_debate
from debaterskill import Debate, Outround


def room(stage="P", iron=False):
    return {
        "row": 5, "seq": 1, "t": 4000, "stage": stage, "scale": 3.0, "chair": "some judge",
        "teams": [
            {"team": "a", "side": "og", "sort": 3.0, "roster": ["p1"] if iron else ["p1", "p2"],
             "iron": iron, "speaks": None},
            {"team": "b", "side": "oo", "sort": 2.0, "roster": ["p3", "p4"], "iron": False,
             "speaks": [78.0, 76.0]},
            {"team": "c", "side": "cg", "sort": 1.0, "roster": ["p5", "p6"], "iron": False,
             "speaks": None},
            {"team": "d", "side": "co", "sort": 0.0, "roster": ["p7", "p8"], "iron": False,
             "speaks": None},
        ]}


def test_prelim_room_maps_to_debate():
    d = room_to_debate(room())
    assert isinstance(d, Debate)
    assert d.teams[0].name == "5::a"
    assert d.teams[0].points == 3.0
    assert d.teams[1].speaks == [78.0, 76.0]
    assert d.chair == "some judge"


def test_iron_flag_requires_single_roster():
    d = room_to_debate(room(iron=True))
    assert d.teams[0].iron is True
    r = room()
    r["teams"][0]["iron"] = True
    d2 = room_to_debate(r)
    assert d2.teams[0].iron is False


def test_outround_sets_advancing():
    d = room_to_debate(room(stage="E"))
    assert isinstance(d, Outround)
    assert d.teams[1].advancing is True
    assert d.teams[2].advancing is False


def test_load_debates_filters():
    rooms = [room(), room(stage="E")]
    assert len(load_debates(iter(rooms))) == 2
    assert len(load_debates(iter(rooms), stage="P")) == 1
    assert len(load_debates(iter(rooms), max_day=100)) == 0
    assert len(load_debates(iter(rooms), rows={5})) == 2
    assert len(load_debates(iter(rooms), rows={6})) == 0


def test_motion_slug_falls_back_to_round():
    d = room_to_debate(room(), motions=True, motion_map={})
    assert d.motion.slug == "5::1"
    d2 = room_to_debate(room(), motions=True, motion_map={(5, "1"): "mabc"})
    assert d2.motion.slug == "mabc"
