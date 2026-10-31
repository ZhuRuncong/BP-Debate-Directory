import json
import sys
import time

from debate_ratings import api, db, job, pipeline

from . import world


def run_in_child(monkeypatch, script):
    monkeypatch.setattr(api, "job_command", lambda options: [sys.executable, "-c", script])
    runner = api.Runner(connect=None, in_child=True)
    runner.start({"fit_only": True})
    for _ in range(6000):
        if runner.status()["state"] != "running":
            break
        time.sleep(0.01)
    return runner.status()


def test_child_logs_and_result_reach_the_status(monkeypatch):
    st = run_in_child(monkeypatch, "print('fitting'); print(%r + '{\"rebuilt\": true}')" % job.RESULT)
    assert st["state"] == "done" and st["result"] == {"rebuilt": True}
    assert st["log"] == ["fitting"] and st["error"] is None


def test_child_error_fails_the_job(monkeypatch):
    st = run_in_child(monkeypatch, "import sys; print(%r + 'SanityError: no'); sys.exit(1)" % job.ERROR)
    assert st["state"] == "failed" and st["error"] == "SanityError: no"


def test_a_killed_child_still_fails_the_job(monkeypatch):
    st = run_in_child(monkeypatch, "import os; os._exit(137)")
    assert st["state"] == "failed" and st["error"] == "job exited with code 137"


def test_job_runs_the_pipeline_and_reports_its_result(monkeypatch, capsys):
    conn = world.make_conn()
    monkeypatch.setattr(db, "connect", lambda: conn)
    monkeypatch.setattr(sys, "argv", ["job", json.dumps({"fit_only": True})])
    assert job.main() == 0
    last = capsys.readouterr().out.strip().splitlines()[-1]
    assert json.loads(last[len(job.RESULT):])["rebuilt"] is True


def test_job_reports_a_failure(monkeypatch, capsys):
    monkeypatch.setattr(db, "connect", world.make_conn)

    def boom(*a, **k):
        raise pipeline.SanityError("refusing to publish")

    monkeypatch.setattr(pipeline, "run_pipeline", boom)
    monkeypatch.setattr(sys, "argv", ["job", "{}"])
    assert job.main() == 1
    assert job.ERROR + "SanityError: refusing to publish" in capsys.readouterr().out
