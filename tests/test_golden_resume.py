"""D4 resumable plans: recover from still-valid results, never rerun the whole chain."""
import json
import sqlite3

import pytest
from shapely.geometry import box, mapping

from definitio import resume
from definitio.model import GISFault
from definitio.runtime import Runtime


@pytest.fixture
def runtime(tmp_path):
    return Runtime(tmp_path / "workspace.sqlite3")


def source(runtime):
    return runtime.import_dataset("roads", {"type": "FeatureCollection", "features": [
        {"type": "Feature", "geometry": mapping(box(500000, 2540000, 500100, 2540100)), "properties": {"road_id": "R1"}},
        {"type": "Feature", "geometry": mapping(box(500200, 2540000, 500300, 2540100)), "properties": {"road_id": "R2"}}]}, "EPSG:32649")


def chain(runtime, d):
    return runtime.declare("chain", [
        {"verb": "gis_buffer", "parameters": {"input": d["id"], "target": "b1", "distance": 10, "unit": "m", "dissolve": False}},
        {"verb": "gis_dissolve", "parameters": {"input": "$previous", "target": "b2", "fields": []}},
        {"verb": "gis_validate_geometry", "parameters": {"dataset": "$previous", "repair": False}}])


def test_every_committed_step_records_a_checkpoint(runtime):
    d = source(runtime)
    declared = chain(runtime, d)
    task = runtime.execute(declared["id"], declared["plan_digest"], True)
    assert task["task_state"] == "COMMITTED"
    for step in task["steps"]:
        cp = step["checkpoint"]
        assert cp["format"] == "definitio-step-checkpoint-v1"
        assert cp["side_effects"] in ("new_dataset", "none")
        assert isinstance(cp["retryable"], bool)
        assert cp["check_location"]["uri"].startswith("definitio://evidence/")
        assert cp["input_versions"] and all({"id", "fingerprint"} <= set(v) for v in cp["input_versions"])
    assert task["steps"][0]["checkpoint"]["dependencies"] == []
    assert task["steps"][1]["checkpoint"]["dependencies"] == [task["steps"][0]["id"]]
    assert task["steps"][0]["checkpoint"]["side_effects"] == "new_dataset"
    assert task["steps"][2]["checkpoint"]["side_effects"] == "none"


def test_midchain_failure_resumes_without_rerunning_valid_steps(runtime, monkeypatch):
    d = source(runtime)
    declared = chain(runtime, d)
    real = runtime._execute_step
    calls = []

    def flaky(task, step, previous):
        calls.append(step["index"])
        if step["index"] == 2 and calls.count(2) == 1:
            raise GISFault("BACKEND_ERROR", "injected mid-chain failure")
        return real(task, step, previous)

    monkeypatch.setattr(runtime, "_execute_step", flaky)
    failed = runtime.execute(declared["id"], declared["plan_digest"], True)
    assert failed["task_state"] == "FAILED" and failed["completion"] == "partial"
    assert failed["steps"][0]["state"] == "COMMITTED"
    first_output = failed["steps"][0]["output_ref"]
    monkeypatch.setattr(runtime, "_execute_step", real)
    resumed = runtime.resume(declared["id"], declared["plan_digest"], True)
    assert resumed["task_state"] == "COMMITTED" and resumed["completion"] == "all"
    assert resumed["steps"][0]["output_ref"] == first_output
    assert resumed["steps"][0]["attempt_seq"] == 1
    assert resumed["resumed_from"] == 2
    assert len(resumed["output_refs"]) == 2
    assert calls.count(1) == 1


def test_changed_input_version_invalidates_dependent_results(runtime, monkeypatch):
    d = source(runtime)
    declared = chain(runtime, d)
    real = runtime._execute_step

    def flaky(task, step, previous):
        if step["index"] == 2:
            raise GISFault("BACKEND_ERROR", "injected mid-chain failure")
        return real(task, step, previous)

    monkeypatch.setattr(runtime, "_execute_step", flaky)
    failed = runtime.execute(declared["id"], declared["plan_digest"], True)
    assert failed["task_state"] == "FAILED" and failed["steps"][0]["state"] == "COMMITTED"
    monkeypatch.setattr(runtime, "_execute_step", real)
    with sqlite3.connect(runtime.store.path) as db:
        row = db.execute("SELECT document FROM datasets WHERE id=?", (d["id"],)).fetchone()
        document = json.loads(row[0])
        document["feature_count"] = 42
        db.execute("UPDATE datasets SET document=? WHERE id=?", (json.dumps(document), d["id"]))
    report = resume.validate_checkpoints(runtime.store.task(declared["id"]), runtime.store)
    assert report["invalidated"] and report["invalidated"][0]["step"] == 1
    with pytest.raises(GISFault) as caught:
        runtime.resume(declared["id"], declared["plan_digest"], True)
    assert caught.value.code == "TARGET_STATE_UNKNOWN"
    assert caught.value.details["rule"] == "resume_dependency_invalidated"
    assert runtime.store.task(declared["id"])["task_state"] == "FAILED"


def test_isolated_tasks_are_never_resumed(runtime):
    d = source(runtime)
    declared = chain(runtime, d)
    task = runtime.store.task(declared["id"])
    task["task_state"] = "ISOLATED"
    task["error"] = {"code": "TARGET_STATE_UNKNOWN", "message": "unknown external state"}
    runtime.store.save_task(task)
    with pytest.raises(GISFault) as caught:
        runtime.resume(declared["id"], declared["plan_digest"], True)
    assert caught.value.code == "TARGET_STATE_UNKNOWN"


def test_previous_reference_still_works_after_resume(runtime):
    d = source(runtime)
    declared = chain(runtime, d)
    task = runtime.execute(declared["id"], declared["plan_digest"], True)
    assert task["steps"][2]["result"]["checked"] == 1


def test_resume_is_idempotent_for_a_committed_plan(runtime):
    d = source(runtime)
    declared = chain(runtime, d)
    task = runtime.execute(declared["id"], declared["plan_digest"], True)
    with pytest.raises(GISFault) as caught:
        runtime.resume(declared["id"], declared["plan_digest"], True)
    assert caught.value.code == "TARGET_STATE_UNKNOWN"
    assert runtime.store.task(declared["id"])["task_state"] == "COMMITTED"


def test_destructive_and_lifecycle_steps_are_marked_non_retryable(runtime):
    d = source(runtime)
    declared = runtime.declare("rename", [{"verb": "gis_rename_dataset", "parameters": {
        "dataset": d["id"], "new_name": "renamed_roads"}}])
    task = runtime.execute(declared["id"], declared["plan_digest"], True)
    assert task["task_state"] == "COMMITTED"
    cp = task["steps"][0]["checkpoint"]
    assert cp["side_effects"] == "rename" and cp["retryable"] is False