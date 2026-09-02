from debate_ratings import judges


def panel(names_roles, teams=("a", "b", "c", "d")):
    return {"t": sorted(teams),
            "a": [{"n": n, "r": r, "i": "", "id": None} for n, r in names_roles]}


def build(rounds):
    return judges.build([{"row": 1, "rounds": rounds}])


def test_accent_variants_merge_with_shared_mate():
    out = build({
        "1": [panel([("José Chair", "c"), ("Amy Mate", "p")])],
        "2": [panel([("Jose Chair", "c"), ("Amy Mate", "p")])],
    })
    keys = set(out["d"])
    assert len(keys & {"josé chair", "jose chair"}) == 1
    crews = [crew for seqs in out["p"].values() for ps in seqs.values()
             for crew, _teams in ps]
    chairs = {k for crew in crews for k, r in crew if r == "c"}
    assert len(chairs) == 1


def test_same_panel_vetoes_merge():
    out = build({
        "1": [panel([("Ann Dupe", "p"), ("Añn Dupe", "p"), ("Bob Mate", "p")])],
        "2": [panel([("Ann Dupe", "p"), ("Bob Mate", "p")])],
        "3": [panel([("Añn Dupe", "p"), ("Bob Mate", "p")])],
    })
    assert {"ann dupe", "añn dupe"} <= set(out["d"])


def test_middle_name_merges_with_shared_mate():
    out = build({
        "1": [panel([("Cara Smith", "c"), ("Dan Mate", "p")])],
        "2": [panel([("Cara Jane Smith", "c"), ("Dan Mate", "p")])],
    })
    assert "cara smith" in out["d"]
    assert "cara jane smith" not in out["d"]
    assert out["d"]["cara smith"] == "Cara Smith"


def test_no_merge_without_evidence():
    out = build({
        "1": [panel([("Eve Jones", "c"), ("Amy Mate", "p")])],
        "2": [panel([("Eve Marie Jones", "c"), ("Ben Other", "p")])],
    })
    assert {"eve jones", "eve marie jones"} <= set(out["d"])


def test_anonymous_judges_kept_as_placeholders():
    out = build({
        "1": [panel([("Adjudicator 3", "c"), ("Real Judge", "p")])],
    })
    assert list(out["d"]) == ["real judge"]
    crew = out["p"]["1"]["1"][0][0]
    assert crew[0][0].startswith("anon::")
    assert crew[0][1] == "c"
