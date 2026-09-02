import datetime

from debate_ratings.fit import mid

from .fakes import FakeConn

EPOCH = datetime.date(2010, 1, 1)


def day(iso):
    return (datetime.date.fromisoformat(iso) - EPOCH).days


DATE = "2024-03-01"
T = day(DATE)

M1 = "This House would ban fixtures"
M2 = "This House believes that golden tests are good"
M3 = "THW win the final"

TEAMS = {
    "Alpha A": ["Ann Alpha", "Bob 'Bobby' Alpha \U0001F3A4"],
    "Alpha B": ["Cara Alpha", "Dan Alpha"],
    "Beta A": ["Ed Beta", "Fay Beta", "Zed Beta"],
    "Beta B": ["Gus Beta", "Hana Beta"],
    "Gamma A": ["Ivo Gamma", "Jo Gamma"],
    "Gamma B": ["Kim Gamma", "Leo Gamma"],
    "Delta A": ["Mia Delta", "Ned Delta"],
    "Delta B": ["Swing Speaker", "TBD"],
}

SPEAKS = {
    "Ann Alpha": ("Alpha A", [78.0, 79.0]),
    "Bob Alpha": ("Alpha A", [77.0, 78.0]),
    "Cara Alpha": ("Alpha B", [76.5, 77.0]),
    "Dan Alpha": ("Alpha B", [76.0, 76.5]),
    "Ed Beta": ("Beta A", [75.5, 76.0]),
    "Fay Beta": ("Beta A", [75.0, 75.5]),
    "Gus Beta": ("Beta B", [74.5, 75.0]),
    "Hana Beta": ("Beta B", [74.0, 74.5]),
    "Ivo Gamma": ("Gamma A", [73.5, 50.0]),
    "Jo Gamma": ("Gamma A", [73.0, 73.5]),
    "Kim Gamma": ("Gamma B", [72.5, 73.0]),
    "Leo Gamma": ("Gamma B", [72.0, 72.5]),
    "Mia Delta": ("Delta A", [71.5, 72.0]),
    "Ned Delta": ("Delta A", [71.0, 71.5]),
    "Oli Delta": ("Delta B", [70.5, 71.0]),
    "Pia Delta": ("Delta B", [70.0, 70.5]),
}


def room(*entries):
    return [{"team": t, "sort": s, "text": "", "side": sd, "roster": None}
            for t, s, sd in entries]


def tab_record():
    return {
        "row": 10, "name": "Fixture Open 2024", "url": "http://tab.example/fix",
        "date": DATE,
        "teams": {k: list(v) for k, v in TEAMS.items()},
        "speaks": {n: {"team": t, "scores": list(sc)} for n, (t, sc) in SPEAKS.items()},
        "rounds": [
            {"seq": 1, "stage": "P", "name": "Round 1", "rooms": [
                room(("Alpha A", 4, "OG"), ("Beta A", 3, "OO"),
                     ("Gamma A", 2, "CG"), ("Delta A", 1, "CO")),
                room(("Alpha B", 4, "OG"), ("Beta B", 3, "OO"),
                     ("Gamma B", 2, "CG"), ("Delta B", 1, "CO")),
            ]},
            {"seq": 2, "stage": "P", "name": "Round 2", "rooms": [
                room(("Alpha A", 4, "OG"), ("Beta B", 3, "OO"),
                     ("Gamma A", 2, "CG"), ("Delta B", 1, "CO")),
                room(("Alpha B", 4, "OG"), ("Beta A", 3, "OO"),
                     ("Gamma B", 2, "CG"), ("Delta A", 1, "CO")),
            ]},
            {"seq": 3, "stage": "E", "name": "Grand Final", "rooms": [
                room(("Alpha A", 2, "OG"), ("Alpha B", 1, "OO"),
                     ("Beta A", 1, "CG"), ("Gamma B", 1, "CO")),
            ]},
        ],
        "prelim_seqs": [1, 2],
    }


def judge_record():
    def panel(chair, teams, extra=()):
        adj = [{"n": chair, "r": "c", "i": "", "id": None}]
        adj += [{"n": n, "r": "p", "i": "", "id": None} for n in extra]
        return {"t": sorted(t.casefold() for t in teams), "a": adj}

    return {
        "row": 10, "name": "Fixture Open 2024", "errors": [],
        "rounds": {
            "1": [panel("Judy Chair", ["Alpha A", "Beta A", "Gamma A", "Delta A"],
                        ["Pan One"]),
                  panel("Cody Chair", ["Alpha B", "Beta B", "Gamma B", "Delta B"])],
            "3": [panel("Judy Chair", ["Alpha A", "Alpha B", "Beta A", "Gamma B"])],
        },
    }


def extra_games():
    d19 = day("2018-12-27")
    return [
        {"source": "scoreonly", "row": 10, "seq": "Trial", "t": T, "obs": "C",
         "c": [["ann alpha"], ["ed beta"]], "r": [76.0, 74.0], "sc": 2.0},
        {"source": "sheets", "row": 90019, "seq": 1, "t": d19, "obs": "O",
         "c": [["pat cape", "quin cape"], ["rex cape", "sue cape"]], "r": [1, 0]},
        {"source": "videos", "row": 90019, "seq": 1, "t": d19, "obs": "C",
         "c": [["pat cape"], ["quin cape"]], "r": [77.0, 75.0], "sc": 2.0},
    ]


def artifacts():
    clean = {
        mid(M1): {"t": M1, "i": "", "skip": False},
        mid(M2): {"t": M2, "i": "", "skip": False},
        mid(M3): {"t": M3, "i": "", "skip": True},
    }
    return {
        "id_merges": {"ned delta": "edward delta"},
        "display_raw": {"ned delta": "Ned Delta"},
        "legacy_display": {"edward delta": "Edward Delta"},
        "excluded_rows": [],
        "motions_clean": clean,
        "motions_tags": {mid(M1): ["Politics", "Economics"], mid(M2): ["Education"]},
        "motions_neighbors": {mid(M1): [[mid(M2), 0.87]]},
        "motions_extra": {"90019": {"byname": {"Round 1": {"t": "THW recover data"}},
                                    "extra": [{"t": "An extra motion"}]}},
        "inst_alias": {"skip": [], "alias": {}},
        "tier_shape": {"3": 0.4, "6": 0.6},
    }


def raw_motions():
    return {10: {"1": [{"t": M1, "i": "Some info"}],
                 "2": [{"t": M2}],
                 "3": [{"t": M3}]}}


def tournaments():
    return {
        10: {"name": "Fixture Open 2024", "start_date": datetime.date.fromisoformat(DATE),
             "source_url": "http://tab.example/fix", "speaking_class": "S-A",
             "status": "ingested", "error": None},
        90019: {"name": "Cape Town WUDC 2019",
                "start_date": datetime.date.fromisoformat("2018-12-27"),
                "source_url": "", "speaking_class": "S-WUDC",
                "status": "ingested", "error": None},
    }


def make_conn():
    return FakeConn(
        raw_tabs={10: tab_record()},
        raw_motions=raw_motions(),
        raw_judges={10: judge_record()},
        extra_games=extra_games(),
        artifacts=artifacts(),
        tournaments=tournaments(),
    )
