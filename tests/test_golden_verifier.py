"""D2 single verifier implementation shared by the agent verify path and the commit path."""
import pytest
from shapely.geometry import box, mapping

from definitio import verifier
from definitio.runtime import Runtime


@pytest.fixture
def runtime(tmp_path):
    return Runtime(tmp_path / "workspace.sqlite3")


def polygon(runtime, name="src", crs="EPSG:32649"):
    return runtime.import_dataset(name, {"type": "FeatureCollection", "features": [
        {"type": "Feature", "geometry": mapping(box(500000, 2540000, 500100, 2540100)),
         "properties": {"road_id": "R1"}}]}, crs)


def blank_context(**overrides):
    base = dict(stage="preview", verb="gis_buffer", vid="V18", parameters={}, inputs=[], proposal=None,
                output=None, store=None, db=None, planned_inputs=[], write_scope=None, actual_writes=[],
                authorization=None, workspace=None, file_artifact=None)
    base.update(overrides)
    return verifier.context(**base)


def test_result_is_three_part_and_never_a_blanket_verified_flag():
    result = verifier.run(blank_context(parameters={"distance": 500, "unit": "m"}))
    assert result["format"] == "definitio-verification-v1"
    assert set(result) >= {"checked", "found", "unconfirmed", "ok"}
    assert "verified" not in result
    assert len(result["checked"]) == len(verifier.PREDICATES)
    assert all(set(c) >= {"predicate", "subject", "method"} for c in result["checked"])
    assert all(set(f) >= {"predicate", "passed", "observation"} for f in result["found"])
    assert all(set(u) >= {"predicate", "limit"} for u in result["unconfirmed"])


def test_distance_unit_predicate_names_the_exact_mismatch(runtime):
    src = polygon(runtime, crs="EPSG:4326")
    ctx = blank_context(stage="precheck", parameters={"distance": 500, "unit": "m"}, inputs=[src],
                        store=runtime.store,
                        planned_inputs=[{"id": src["id"], "name": "src", "fingerprint": src["fingerprint"]}])
    result = verifier.run(ctx, predicates=(verifier.distance_unit_compatibility,))
    found = result["found"][0]
    assert found["predicate"] == "distance_unit_compatibility" and found["passed"] is False
    assert found["details"]["crs_projected"] is False
    assert found["details"]["requested_unit"] == "m"
    assert result["ok"] is False


def test_input_version_predicate_reports_both_fingerprints(runtime):
    src = polygon(runtime)
    ctx = blank_context(stage="verify", inputs=[src], store=runtime.store,
                        planned_inputs=[{"id": src["id"], "name": "src", "fingerprint": "0" * 64}])
    result = verifier.run(ctx, predicates=(verifier.input_version_binding,))
    found = result["found"][0]
    assert found["passed"] is False
    assert found["details"]["changed"] == [{"id": src["id"], "planned": "0" * 64,
                                            "actual": src["fingerprint"]}]


def test_write_scope_predicate_rejects_an_out_of_scope_target(runtime):
    ctx = blank_context(stage="commit", store=runtime.store,
                        write_scope={"dataset_targets": [{"step": 1, "target": "allowed"}], "file_artifacts": []},
                        actual_writes=[{"kind": "dataset", "target": "intruder"}])
    result = verifier.run(ctx, predicates=(verifier.write_scope,))
    assert result["found"][0]["passed"] is False
    assert result["found"][0]["details"]["out_of_scope"] == [{"kind": "dataset", "target": "intruder"}]


def test_spatial_join_predicate_rejects_a_non_vector_join_side(runtime):
    from definitio.raster import validate_raster
    left = polygon(runtime)
    raster, crs = validate_raster({"data": [[1.0, 2.0], [3.0, 4.0]],
                                   "transform": [1.0, 0.0, 500000.0, 0.0, -1.0, 2541000.0],
                                   "dtype": "float32", "nodata": None, "band_name": "elevation"}, "EPSG:32649")
    right = runtime.store.insert_raster("grid", raster, crs, {"kind": "agent_raster_import"})
    ctx = blank_context(stage="precheck", verb="gis_spatial_join", vid="V22",
                        parameters={"predicate": "intersects", "summary": "all"}, inputs=[left, right])
    result = verifier.run(ctx, predicates=(verifier.spatial_join_contract,))
    assert result["found"][0]["passed"] is False
    assert any("矢量" in problem for problem in result["found"][0]["details"]["problems"])


def test_agent_verify_and_commit_paths_call_the_same_predicate_objects(runtime, monkeypatch):
    from definitio.agent import AgentService
    src = polygon(runtime)
    service = AgentService(runtime)
    seen = {"commit": [], "verify": []}
    original = verifier.PREDICATES

    def wrapped(fn, bucket):
        def inner(ctx):
            seen[bucket].append(fn)
            return fn(ctx)
        inner.__name__ = fn.__name__
        return inner

    task = runtime.declare("buffer", [{"verb": "gis_buffer", "parameters": {
        "input": src["id"], "target": "b", "distance": 10, "unit": "m", "dissolve": False}}])
    commit_set = tuple(wrapped(f, "commit") for f in original)
    monkeypatch.setattr(verifier, "PREDICATES", commit_set)
    monkeypatch.setitem(verifier.POLICY, "commit", commit_set)
    runtime.execute(task["id"], task["plan_digest"], True)
    verify_set = tuple(wrapped(f, "verify") for f in original)
    monkeypatch.setattr(verifier, "PREDICATES", verify_set)
    monkeypatch.setitem(verifier.POLICY, "verify", verify_set)
    reply = service.call("task_get", {"task_id": task["id"], "verify": True})
    assert reply["ok"], reply
    assert seen["commit"] and seen["verify"]
    assert [f.__name__ for f in seen["commit"]] == [f.__name__ for f in seen["verify"]]
    assert {id(f) for f in seen["commit"]} == {id(f) for f in seen["verify"]}
    assert reply["data"]["verification"]["predicates_digest"] == verifier.run(blank_context())["predicates_digest"]


def test_preview_uses_the_same_predicates_and_reports_unconfirmed_persistence(runtime, monkeypatch):
    from definitio.preview import preview
    src = polygon(runtime)
    seen = []
    original = verifier.PREDICATES

    def wrapped(fn):
        def inner(ctx):
            seen.append(fn)
            return fn(ctx)
        inner.__name__ = fn.__name__
        return inner

    preview_set = tuple(wrapped(f) for f in original)
    monkeypatch.setattr(verifier, "PREDICATES", preview_set)
    monkeypatch.setitem(verifier.POLICY, "preview", preview_set)
    monkeypatch.setitem(verifier.POLICY, "precheck", tuple(wrapped(f) for f in verifier.PRECHECK_PREDICATES))
    result = preview(runtime, "buffer", [{"verb": "gis_buffer", "parameters": {
        "input": src["id"], "target": "b", "distance": 10, "unit": "m", "dissolve": False}}])
    assert result["ok"]
    assert {id(f) for f in seen} == {id(f) for f in original}
    step = result["steps"][0]
    assert step["verification"]["stage"] == "preview"
    assert any(u["predicate"] == "output_reopenable" for u in step["verification"]["unconfirmed"])


def test_legacy_verify_output_still_rolls_back_fake_success(runtime, monkeypatch):
    from definitio.operations import compute as real_compute
    src = runtime.import_dataset("d", {"type": "FeatureCollection", "features": [
        {"type": "Feature", "geometry": mapping(box(0, 0, 1, 1)), "properties": {"g": "A"}},
        {"type": "Feature", "geometry": mapping(box(1, 0, 2, 1)), "properties": {"g": "A"}}]}, "EPSG:32649")

    def corrupt(verb, p, inputs):
        proposal = real_compute(verb, p, inputs)
        proposal["geojson"]["features"] = []
        return proposal

    monkeypatch.setattr("definitio.runtime.compute", corrupt)
    task = runtime.declare("dissolve", [{"verb": "gis_dissolve", "parameters": {
        "input": src["id"], "target": "out", "fields": ["g"]}}])
    task = runtime.execute(task["id"], task["plan_digest"], True)
    assert task["task_state"] == "FAILED" and task["error"]["code"] == "BACKEND_ERROR"
    assert not task["output_refs"] and len(runtime.store.datasets()) == 1


def test_geographic_buffer_still_reports_not_supported_and_names_the_predicate(runtime):
    src = polygon(runtime, crs="EPSG:4326")
    task = runtime.declare("buffer degrees", [{"verb": "gis_buffer", "parameters": {
        "input": src["id"], "target": "out", "distance": 10, "unit": "m", "dissolve": False}}])
    task = runtime.execute(task["id"], task["plan_digest"], True)
    assert task["error"]["code"] == "NOT_SUPPORTED"
    assert task["error"]["details"]["predicate"] == "distance_unit_compatibility"
    assert not task["output_refs"]


def test_check_strategy_is_a_configuration_seam_not_a_user_switch():
    from definitio.agent import tool_definitions
    assert set(verifier.POLICY) == {"precheck", "preview", "commit", "verify", "commit_task"}
    assert verifier.POLICY["commit"] == verifier.PREDICATES
    assert verifier.POLICY["verify"] == verifier.PREDICATES
    assert verifier.POLICY["precheck"] == verifier.PRECHECK_PREDICATES
    ctx = blank_context(stage="commit")
    full = verifier.run(ctx)
    narrowed = verifier.run(ctx, predicates=verifier.POLICY["precheck"])
    assert len(full["checked"]) == 6 and len(narrowed["checked"]) == 3
    assert full["predicates_digest"] != narrowed["predicates_digest"]
    for definition in tool_definitions():
        assert not {"predicates", "check_policy", "verification_mode"} & set(definition["inputSchema"]["properties"])