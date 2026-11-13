"""Golden chain acceptance A1-A5. All offline, all in a private temporary workspace."""
import json
import sqlite3

import pytest

from definitio import golden
from definitio.agent import AgentService
from definitio.model import GISFault
from definitio.runtime import Runtime

SESSION = "golden-chain-acceptance-session-token-0001"


@pytest.fixture
def service(tmp_path):
    return AgentService(Runtime(tmp_path / "workspace.sqlite3"))


def plan_chain(service, request_id, exports=None):
    fx = golden.fixtures(service, SESSION)
    steps = golden.chain_steps(fx["facilities_uri"], fx["zones_id"], "gc_")
    arguments = {"goal": "golden chain", "request_id": request_id, "steps": steps}
    if exports is not None:
        arguments["exports"] = exports
    planned = service.call("task_plan", arguments)
    assert planned["ok"], planned
    return fx, steps, planned


def execute(service, planned, **extra):
    return service.call("task_execute", {"task_id": planned["data"]["task"]["id"],
                                         "plan_digest": planned["data"]["task"]["plan_digest"],
                                         "confirm": True, **extra})


def tamper(service, dataset_id, **changes):
    with sqlite3.connect(service.runtime.store.path) as db:
        row = db.execute("SELECT document FROM datasets WHERE id=?", (dataset_id,)).fetchone()
        document = json.loads(row[0])
        document.update(changes)
        db.execute("UPDATE datasets SET document=? WHERE id=?", (json.dumps(document), dataset_id))


def test_a1_end_to_end_and_independent_readback_matches_truth(service, tmp_path):
    fx, steps, planned = plan_chain(service, "gc-plan-a1", [{"step": 4, "name": "gc_join.geojson", "format": "geojson"}])
    dry = service.call("task_dry_run", {"goal": "golden chain", "steps": steps})
    assert dry["ok"], dry
    assert [s["verb"] for s in dry["data"]["preview"]["steps"]] == [verb for _, verb in golden.STAGES[:5]]
    assert dry["data"]["preview"]["workspace_writes"] == 0

    executed = execute(service, planned)
    assert executed["ok"], executed
    task = executed["data"]["task"]
    assert task["task_state"] == "COMMITTED"
    record = service.runtime.store.task(task["id"])
    assert record["commit_state"] == "committed" and record["readback"]["confirmed"] is True

    published = tmp_path / "artifacts" / "gc_join.geojson"
    document = json.loads(published.read_text(encoding="utf-8"))
    assert len(document["features"]) == fx["expected_join_count"]
    total = sum(f["properties"]["population"] for f in document["features"])
    assert total == fx["expected_population_sum"]
    areas = [golden.shoelace_area(golden.outer_ring(f["geometry"])) for f in document["features"]]
    truth = fx["expected_buffer_area"]
    assert all(abs(area - truth) / truth < 1e-9 for area in areas), (areas, truth)
    assert document["definitio"]["crs"]["axis_order"] == "east,north"

    stats = record["steps"][4]["result"]
    assert stats["groups"][0]["fields"]["population"]["sum"] == fx["expected_population_sum"]
    assert stats["groups"][0]["fields"]["population"]["count"] == fx["expected_join_count"]
    assert record["steps"][4]["checkpoint"]["side_effects"] == "none"

    viewed = service.call("artifact_view", {"uri": f"definitio://artifacts/{record['file_artifact_refs'][0]}", "view": "page"})
    assert viewed["ok"], viewed
    assert viewed["data"]["integrity"]["sha256_json_utf8"]

    buffer_header = service.runtime.store.dataset(task["output_refs"][2])["semantic"]
    assert buffer_header["lineage"]["operation"]["verb"] == "gis_buffer"
    assert buffer_header["units"]["operation"]["distance"]["unit"] == "m"
    assert buffer_header["crs"]["code"] == "EPSG:32649"
    assert buffer_header["axis_order"]["sequence"] == "east,north"
    projected_id = task["output_refs"][1]
    assert buffer_header["lineage"]["inputs"][0]["version_id"] == projected_id

    verified = service.call("task_get", {"task_id": task["id"], "verify": True})
    assert verified["ok"] and verified["data"]["verification"]["ok"]
    assert all(item["unconfirmed"] for item in verified["data"]["verification"]["steps"])


def test_a2_unit_object_and_version_errors_are_each_named(service):
    fx, steps, _ = plan_chain(service, "gc-plan-a2-base")

    unit_steps = [dict(s) for s in steps]
    unit_steps[1] = {"verb": "gis_assign_projection", "parameters": {
        "dataset": "$previous", "target": "gc_projected", "crs": "EPSG:4326"}}
    unit = service.call("task_dry_run", {"goal": "unit error", "steps": unit_steps})
    assert not unit["ok"]
    assert unit["error"]["details"]["predicate"] == "distance_unit_compatibility"
    assert unit["error"]["details"]["crs_projected"] is False
    assert unit["error"]["details"]["requested_unit"] == "m"
    assert unit["error"]["code"] == "NOT_SUPPORTED"

    raster = service.call("raster_import", {
        "name": "gc_raster", "crs": "EPSG:32649", "confirm": True, "request_id": "gc-raster-1",
        "raster": {"data": [[1.0, 2.0], [3.0, 4.0]], "transform": [1.0, 0.0, 500000.0, 0.0, -1.0, 2541000.0],
                   "dtype": "float32", "nodata": None, "band_name": "elevation"}})
    assert raster["ok"], raster
    object_steps = [dict(s) for s in steps]
    object_steps[3] = {"verb": "gis_spatial_join", "parameters": {
        **steps[3]["parameters"], "join": raster["data"]["dataset"]["id"]}}
    obj = service.call("task_dry_run", {"goal": "object error", "steps": object_steps})
    assert not obj["ok"]
    assert obj["error"]["details"]["predicate"] == "spatial_join_contract"
    assert any("矢量" in problem for problem in obj["error"]["details"]["problems"])

    _, _, planned = plan_chain(service, "gc-plan-a2-version")
    tamper(service, fx["zones_id"], feature_count=999)
    refused = execute(service, planned)
    assert not refused["ok"]
    assert refused["error"]["details"]["rule"] == "version_binding_changed"
    assert refused["error"]["details"]["changed_inputs"][0]["id"] == fx["zones_id"]
    assert refused["error"]["details"]["changed_inputs"][0]["reason"] == "record_digest_changed"


def test_a3_state_change_after_checks_does_not_authorize_the_old_plan(service):
    fx, steps, planned = plan_chain(service, "gc-plan-a3")
    dry = service.call("task_dry_run", {"goal": "golden chain", "steps": steps})
    assert dry["ok"]
    tamper(service, fx["zones_id"], feature_count=77)
    refused = execute(service, planned)
    assert not refused["ok"]
    record = service.runtime.store.task(planned["data"]["task"]["id"])
    assert record["commit_state"] == "rejected_state_changed"
    assert record["error"]["details"]["changed_inputs"][0]["id"] == fx["zones_id"]
    assert not record["output_refs"]
    assert not record["file_artifact_refs"]
    assert not (service.runtime.store.path.parent / "artifacts").exists() or not list(
        (service.runtime.store.path.parent / "artifacts").glob("*.geojson"))


def test_a4_midchain_failure_recovers_from_valid_state(service, monkeypatch):
    fx, steps, planned = plan_chain(service, "gc-plan-a4")
    task_id = planned["data"]["task"]["id"]
    runtime = service.runtime
    import definitio.runtime as runtime_module
    real_compute = runtime_module.compute
    seen = []

    def flaky_compute(verb, p, datasets):
        seen.append(verb)
        if verb == "gis_buffer" and seen.count("gis_buffer") == 1:
            raise GISFault("BACKEND_ERROR", "injected buffer failure")
        return real_compute(verb, p, datasets)

    monkeypatch.setattr(runtime_module, "compute", flaky_compute)
    failed = execute(service, planned)
    assert not failed["ok"]
    assert failed["data"]["task"]["completion"] == "partial"
    assert runtime.store.task(task_id)["steps"][2]["state"] == "FAILED"
    resumed = execute(service, planned, resume=True)
    assert resumed["ok"], resumed
    record = runtime.store.task(task_id)
    assert record["task_state"] == "COMMITTED" and record["resumed_from"] == 3
    assert record["steps"][0]["attempt_seq"] == 1 and record["steps"][1]["attempt_seq"] == 1
    assert record["steps"][2]["attempt_seq"] == 2
    assert seen.count("gis_import_data") == 1 and seen.count("gis_reproject") == 1
    assert seen.count("gis_buffer") == 2
    assert record["commit_state"] == "committed"
    stats = record["steps"][4]["result"]
    assert stats["groups"][0]["fields"]["population"]["sum"] == fx["expected_population_sum"]


def test_a5_commit_and_export_retries_have_no_duplicate_or_out_of_scope_effects(service, tmp_path):
    fx, steps, planned = plan_chain(service, "gc-plan-a5", [{"step": 4, "name": "gc_join.geojson", "format": "geojson"}])
    task_id = planned["data"]["task"]["id"]
    first = execute(service, planned)
    assert first["ok"], first
    before_datasets = len(service.runtime.store.datasets())
    artifacts = tmp_path / "artifacts"
    before_files = sorted(p.name for p in artifacts.iterdir() if p.is_file())
    before_bytes = (artifacts / "gc_join.geojson").read_bytes()
    for attempt in range(3):
        again = execute(service, planned)
        assert again["ok"], again
        assert again["data"]["replayed"] is True
        assert again["data"]["task"]["task_state"] == "COMMITTED"
    assert len(service.runtime.store.datasets()) == before_datasets
    assert sorted(p.name for p in artifacts.iterdir() if p.is_file()) == before_files
    assert (artifacts / "gc_join.geojson").read_bytes() == before_bytes
    assert len(service.runtime.store.file_artifacts()) == 1
    assert not list((artifacts / ".staging").iterdir())
    record = service.runtime.store.task(task_id)
    assert record["replay_count"] == 3
    assert all(write["target"].startswith("gc_") for write in record["actual_writes"])
    scope = next(f for f in record["verification"]["found"] if f["predicate"] == "write_scope")
    assert scope["passed"] is True and scope["details"]["out_of_scope"] == []