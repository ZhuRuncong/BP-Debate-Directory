import datetime

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
    assert res == {"ingested": 0, "rebuilt": False}
    assert conn.payloads == {}


def test_publish_gate_blocks_regressions():
    conn = world.make_conn()
    pipeline.run_pipeline(conn, fit_only=True, log=QUIET)
    conn.snapshots.append((datetime.datetime.now(datetime.UTC), 10000, 10000, "{}"))
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
