import datetime
import gzip
import json

import pytest
from debate_ratings import fit, pipeline, priors

from . import world

QUIET = lambda *a, **k: None


def test_run_pipeline_fit_only_publishes():
    conn = world.make_conn()
    res = pipeline.run_pipeline(conn, fit_only=True, log=QUIET)
    assert res["rebuilt"] is True
    assert res["n_speakers"] > 0
    assert set(conn.payloads) == {("data", "gz"), ("data", "br"),
                                  ("rest", "gz"), ("rest", "br")}
    assert len(conn.snapshots) == 1


def test_skip_ingest_without_force_skips_rebuild():
    conn = world.make_conn()
    res = pipeline.run_pipeline(conn, skip_ingest=True, log=QUIET)
    assert res == {"ingested": 0, "requests": 0, "rebuilt": False}
    assert conn.payloads == {}


def test_publish_gate_blocks_regressions():
    conn = world.make_conn()
    pipeline.run_pipeline(conn, fit_only=True, log=QUIET)
    conn.snapshots.append((datetime.datetime.now(datetime.UTC) + datetime.timedelta(seconds=1),
                       10000, 10000, "{}"))
    with pytest.raises(pipeline.SanityError):
        pipeline.run_pipeline(conn, fit_only=True, log=QUIET)
    assert len(conn.payloads) == 4
    res = pipeline.run_pipeline(conn, fit_only=True, publish_anyway=True, log=QUIET)
    assert res["rebuilt"] is True


def test_quality_priors_follow_first_tournament_tier():
    conn = world.make_conn()
    pipeline.run_pipeline(conn, fit_only=True, log=QUIET)
    assert priors.quality_prior_mus(conn, 0.0) == {}
    mus = priors.quality_prior_mus(conn, 1.0)
    assert mus["ann alpha"] < mus["pat cape"]
    assert abs(sum(mus.values()) / len(mus)) < 1e-9
    assert not any(k.startswith(("anon::", "ANON::")) for k in mus)
    doubled = priors.quality_prior_mus(conn, 2.0)
    assert doubled["pat cape"] == pytest.approx(2 * mus["pat cape"])


def test_fit_enrolls_quality_priors(monkeypatch):
    conn = world.make_conn()
    pipeline.run_pipeline(conn, fit_only=True, log=QUIET)
    lines = []
    monkeypatch.setattr(fit, "QUALITY_PRIOR_STRENGTH", 1.0)
    tab = fit.fit(conn, False, {}, log=lines.append)
    assert any("quality priors" in ln for ln in lines)
    assert tab.size > 0



def _count_fits(monkeypatch):
    calls, real = [], fit.fit
    monkeypatch.setattr(fit, "fit", lambda *a, **k: calls.append(1) or real(*a, **k))
    return calls


def _bodies(conn):
    return {k: v[1] for k, v in conn.payloads.items()}


def test_unchanged_inputs_reuse_the_cached_fits(monkeypatch):
    conn = world.make_conn()
    pipeline.run_pipeline(conn, fit_only=True, log=QUIET)
    first = _bodies(conn)
    calls = _count_fits(monkeypatch)
    pipeline.run_pipeline(conn, fit_only=True, log=QUIET)
    assert calls == []
    assert _bodies(conn) == first


def test_hiding_needs_no_refit(monkeypatch):
    conn = world.make_conn()
    pipeline.run_pipeline(conn, fit_only=True, log=QUIET)
    conn.artifacts["hidden"] = {"players": ["edward delta"], "institutions": []}
    calls = _count_fits(monkeypatch)
    pipeline.run_pipeline(conn, fit_only=True, log=QUIET)
    assert calls == []
    data = json.loads(gzip.decompress(conn.payloads[("data", "gz")][1]))
    assert "Edward Delta" not in [p[0] for p in data["players"]]


def test_a_merge_forces_a_refit(monkeypatch):
    conn = world.make_conn()
    pipeline.run_pipeline(conn, fit_only=True, log=QUIET)
    conn.artifacts["id_merges"] = dict(conn.artifacts["id_merges"], **{"eddie delta": "edward delta"})
    calls = _count_fits(monkeypatch)
    pipeline.run_pipeline(conn, fit_only=True, log=QUIET)
    assert len(calls) == 2


def test_merges_reach_score_only_games():
    conn = world.make_conn()
    conn.artifacts["id_merges"] = dict(conn.artifacts["id_merges"], **{"sue cape": "ann alpha"})
    pipeline.run_pipeline(conn, fit_only=True, log=QUIET)
    data = json.loads(gzip.decompress(conn.payloads[("data", "gz")][1]))
    rest = json.loads(gzip.decompress(conn.payloads[("rest", "gz")][1]))
    names = [p[0] for p in data["players"]]
    assert "Sue Cape" not in names
    ann = str(names.index("Ann Alpha"))
    assert {data["tournaments"][e[0]]["n"] for e in rest["careers"][ann]} == {
        "Fixture Open 2024", "Cape Town WUDC 2019"}


def test_refit_option_bypasses_the_cache(monkeypatch):
    conn = world.make_conn()
    pipeline.run_pipeline(conn, fit_only=True, log=QUIET)
    calls = _count_fits(monkeypatch)
    pipeline.run_pipeline(conn, fit_only=True, refit=True, log=QUIET)
    assert len(calls) == 2
