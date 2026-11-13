"""D3 version-bound commit, staged file publication, three-state honesty."""
import json
import sqlite3

import pytest
from shapely.geometry import box, mapping

from definitio import commit
from definitio.model import GISFault
from definitio.runtime import Runtime


@pytest.fixture
def runtime(tmp_path):
    return Runtime(tmp_path / "workspace.sqlite3")


def src(runtime):
    return runtime.import_dataset("roads", {"type": "FeatureCollection", "features": [
        {"type": "Feature", "geometry": mapping(box(500000, 2540000, 500100, 2540100)),
         "properties": {"road_id": "R1"}}]}, "EPSG:32649")


def plan(runtime, d, exports=None):
    return runtime.declare("buffer", [{"verb": "gis_buffer", "parameters": {
        "input": d["id"], "target": "buf", "distance": 10, "unit": "m", "dissolve": False}}], exports=exports)


def tamper(runtime, dataset_id, **changes):
    with sqlite3.connect(runtime.store.path) as db:
        row = db.execute("SELECT document FROM datasets WHERE id=?", (dataset_id,)).fetchone()
        document = json.loads(row[0])
        document.update(changes)
        db.execute("UPDATE datasets SET document=? WHERE id=?", (json.dumps(document), dataset_id))


def test_plan_binds_input_versions_and_write_scope(runtime):
    d = src(runtime)
    task = plan(runtime, d, exports=[{"step": 1, "name": "buf.geojson", "format": "geojson"}])
    assert task["write_scope"]["format"] == "definitio-write-scope-v1"
    assert task["write_scope"]["dataset_targets"] == [{"step": 1, "verb": "gis_buffer", "target": "buf", "mode": "create", "kind": "dataset"}]
    assert task["write_scope"]["file_artifacts"] == [{"step": 1, "name": "buf.geojson", "format": "geojson"}]
    assert task["input_snapshot_refs"][0]["id"] == d["id"]
    assert task["input_snapshot_refs"][0]["fingerprint"] == d["fingerprint"]
    assert task["input_snapshot_refs"][0]["record_digest"]
    assert task["commit_state"] == "pending_confirmation"


def test_changed_input_rejects_the_old_plan_with_an_explicit_reason(runtime):
    d = src(runtime)
    task = plan(runtime, d)
    tamper(runtime, d["id"], feature_count=999)
    task = runtime.execute(task["id"], task["plan_digest"], True)
    assert task["task_state"] == "FAILED"
    assert task["commit_state"] == "rejected_state_changed"
    assert task["error"]["details"]["rule"] == "version_binding_changed"
    assert task["error"]["details"]["changed_inputs"][0]["id"] == d["id"]
    assert not task["output_refs"]


def test_occupied_target_rejects_the_old_plan(runtime):
    d = src(runtime)
    task = plan(runtime, d)
    runtime.import_dataset("buf", d["geojson"], "EPSG:32649")
    task = runtime.execute(task["id"], task["plan_digest"], True)
    assert task["commit_state"] == "rejected_state_changed"
    assert task["error"]["details"]["changed_targets"][0]["target"] == "buf"


def test_commit_is_pending_until_real_readback_confirms(runtime):
    d = src(runtime)
    task = plan(runtime, d)
    task = runtime.execute(task["id"], task["plan_digest"], True)
    assert task["commit_state"] == "committed"
    assert task["readback"]["confirmed"] is True
    assert task["readback"]["outputs"][0]["fingerprint"] == runtime.store.dataset(task["output_refs"][0])["fingerprint"]


def test_file_artifact_is_staged_verified_then_published(runtime, tmp_path):
    d = src(runtime)
    task = plan(runtime, d, exports=[{"step": 1, "name": "buf.geojson", "format": "geojson"}])
    task = runtime.execute(task["id"], task["plan_digest"], True)
    assert task["task_state"] == "COMMITTED", task.get("error")
    record = runtime.store.file_artifacts()[0]
    published = tmp_path / "artifacts" / "buf.geojson"
    assert published.exists() and not list((tmp_path / "artifacts" / ".staging").iterdir())
    document = json.loads(published.read_text(encoding="utf-8"))
    assert document["definitio"]["fingerprint"] == record["sha256_dataset"]
    assert record["bytes"] == published.stat().st_size
    readback = commit.readback_file_artifact(tmp_path, record)
    assert readback["confirmed"] is True and readback["feature_count"] == 1
    assert task["file_artifact_refs"] == [record["id"]]
    assert task["readback"]["file_artifacts"][0]["confirmed"] is True


def test_publish_retry_is_idempotent_and_never_overwrites(runtime, tmp_path):
    d = src(runtime)
    task = plan(runtime, d, exports=[{"step": 1, "name": "buf.geojson", "format": "geojson"}])
    task = runtime.execute(task["id"], task["plan_digest"], True)
    first = runtime.store.file_artifacts()[0]
    dataset = runtime.store.dataset(first["dataset_id"])
    again = commit.publish_file_artifact(runtime.store, tmp_path, dataset=dataset, name="buf.geojson",
                                         request_key=first["request_key"], task_id=task["id"],
                                         step_id=task["steps"][0]["id"])
    assert again["replayed"] is True and again["artifact"]["id"] == first["id"]
    assert len(runtime.store.file_artifacts()) == 1
    with pytest.raises(GISFault) as caught:
        commit.publish_file_artifact(runtime.store, tmp_path, dataset=dataset, name="buf.geojson",
                                     request_key="different-key", task_id=task["id"],
                                     step_id=task["steps"][0]["id"])
    assert caught.value.code == "ALREADY_EXISTS"


def test_publish_rejects_path_traversal_and_cleans_staging(runtime, tmp_path):
    d = src(runtime)
    for bad in ("../escape.geojson", "sub/dir.geojson", "..\\escape.geojson", "buf.txt"):
        with pytest.raises(GISFault) as caught:
            commit.publish_file_artifact(runtime.store, tmp_path, dataset=d, name=bad,
                                         request_key="k-" + bad, task_id="t", step_id="s")
        assert caught.value.code == "INVALID_ARGUMENT", bad
    staging = tmp_path / "artifacts" / ".staging"
    assert not staging.exists() or not list(staging.iterdir())
    assert not (tmp_path / "artifacts").exists() or not [p for p in (tmp_path / "artifacts").iterdir() if p.name != ".staging"]


def test_failed_verification_cleans_staging_and_publishes_nothing(runtime, tmp_path, monkeypatch):
    d = src(runtime)
    monkeypatch.setattr(commit, "verify_staged", lambda *a, **k: {"ok": False, "reason": "injected"})
    with pytest.raises(GISFault):
        commit.publish_file_artifact(runtime.store, tmp_path, dataset=d, name="buf.geojson",
                                     request_key="k1", task_id="t", step_id="s")
    assert not (tmp_path / "artifacts" / "buf.geojson").exists()
    staging = tmp_path / "artifacts" / ".staging"
    assert not staging.exists() or not list(staging.iterdir())
    assert runtime.store.file_artifacts() == []


def test_write_scope_predicate_sees_the_published_file(runtime, tmp_path):
    d = src(runtime)
    task = plan(runtime, d, exports=[{"step": 1, "name": "buf.geojson", "format": "geojson"}])
    task = runtime.execute(task["id"], task["plan_digest"], True)
    scope_check = next(f for f in task["verification"]["found"] if f["predicate"] == "write_scope")
    assert scope_check["passed"] is True
    assert {"kind": "file", "target": "buf.geojson"} in scope_check["details"]["actual_writes"]
    assert {"kind": "dataset", "target": "buf"} in scope_check["details"]["actual_writes"]


def test_out_of_scope_file_name_is_refused_at_plan_time(runtime):
    d = src(runtime)
    with pytest.raises(GISFault) as caught:
        plan(runtime, d, exports=[{"step": 1, "name": "../escape.geojson", "format": "geojson"}])
    assert caught.value.code == "INVALID_ARGUMENT"


def test_destroy_commit_readback_excludes_the_destroyed_input(runtime):
    d = src(runtime)
    pf = runtime.delete_preflight(d["id"])
    task = runtime.declare("destroy", [{"verb": "gis_delete_dataset", "parameters": {
        "dataset": d["id"], "target_echo": pf["target_echo"], "preflight_id": pf["preflight_id"]}}])
    task = runtime.execute(task["id"], task["plan_digest"], True)
    assert task["task_state"] == "COMMITTED", task.get("error")
    assert task["commit_state"] == "committed"
    assert task["readback"]["confirmed"] is True
    assert task["readback"]["destroyed_inputs"] == [d["id"]]
    assert {"kind": "dataset_destroy", "target": d["id"]} in task["actual_writes"]