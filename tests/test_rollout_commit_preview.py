"""R3: ad-hoc readback lists live in the single verifier; dry-run judges the same output."""
import pytest
from shapely.geometry import box, mapping

from definitio import verifier
from definitio.runtime import Runtime
from definitio.agent import AgentService
from definitio.preview import preview


@pytest.fixture
def runtime(tmp_path):
    return Runtime(tmp_path / "workspace.sqlite3")


def src(runtime, name="src"):
    return runtime.import_dataset(name, {"type": "FeatureCollection", "features": [
        {"type": "Feature", "geometry": mapping(box(500000, 2540000, 500100, 2540100)),
         "properties": {"g": "A"}}]}, "EPSG:32649")


def invariant_checks(evidence):
    found = next(f for f in evidence["verification"]["found"]
                 if f["predicate"] == "operation_readback_invariants")
    return found["details"]["checks"]


def test_raster_readback_check_is_emitted_by_the_verifier(runtime):
    from definitio.raster import validate_raster
    raster, crs = validate_raster({"data": [[1.0, 2.0], [3.0, 4.0]],
        "transform": [1.0, 0.0, 500000.0, 0.0, -1.0, 2541000.0],
        "dtype": "float32", "nodata": None, "band_name": "e"}, "EPSG:32649")
    d = runtime.store.insert_raster("grid", raster, crs, {"kind": "agent_raster_import"})
    task = runtime.declare("calc", [{"verb": "gis_raster_calc", "parameters": {
        "inputs": [d["id"]], "expression": "A * 2", "target": "out", "nodata_policy": "propagate"}}])
    task = runtime.execute(task["id"], task["plan_digest"], True)
    assert task["task_state"] == "COMMITTED", task.get("error")
    ev = runtime.store.evidence(task["steps"][0]["evidence_ref"])
    ids = [c["id"] for c in invariant_checks(ev)]
    assert "grid_and_pixels_readback" in ids
    assert any(c["id"] == "grid_and_pixels_readback" for c in ev["postcondition_checks"])


def test_rename_has_real_readback_checks(runtime):
    d = src(runtime)
    task = runtime.declare("rename", [{"verb": "gis_rename_dataset", "parameters": {
        "dataset": d["id"], "new_name": "renamed"}}])
    task = runtime.execute(task["id"], task["plan_digest"], True)
    assert task["task_state"] == "COMMITTED", task.get("error")
    ev = runtime.store.evidence(task["steps"][0]["evidence_ref"])
    checks = {c["id"]: c["passed"] for c in invariant_checks(ev)}
    assert checks["rename_content_unchanged"] is True
    assert checks["rename_head_resolves"] is True
    assert checks["rename_old_name_gone"] is True


def test_delete_absence_check_is_a_real_readback(runtime):
    d = src(runtime)
    pf = runtime.delete_preflight(d["id"])
    task = runtime.declare("delete", [{"verb": "gis_delete_dataset", "parameters": {
        "dataset": d["id"], "target_echo": d["id"], "preflight_id": pf["preflight_id"]}}])
    task = runtime.execute(task["id"], task["plan_digest"], True)
    assert task["task_state"] == "COMMITTED", task.get("error")
    ev = runtime.store.evidence(task["steps"][0]["evidence_ref"])
    checks = {c["id"]: c["passed"] for c in invariant_checks(ev)}
    assert checks["dataset_absent_readback"] is True


def test_preview_and_execute_run_the_same_invariant_checks(runtime):
    d = src(runtime)
    steps = [{"verb": "gis_buffer", "parameters": {"input": d["id"], "target": "b",
              "distance": 10, "unit": "m", "dissolve": False}}]
    pre = preview(runtime, "buffer", steps)
    assert pre["ok"], pre
    pre_ids = [c["id"] for c in pre["steps"][0]["verification"]["found"][-1]["details"]["checks"]
               if c["passed"] is not None]
    task = runtime.declare("buffer", steps)
    task = runtime.execute(task["id"], task["plan_digest"], True)
    assert task["task_state"] == "COMMITTED", task.get("error")
    ev = runtime.store.evidence(task["steps"][0]["evidence_ref"])
    exe_ids = [c["id"] for c in invariant_checks(ev) if c["passed"] is not None]
    assert "schema_readback" in pre_ids and "crs_readback" in pre_ids
    assert set(pre_ids) == set(exe_ids)


def test_preview_execute_verify_share_predicate_objects(runtime, monkeypatch):
    d = src(runtime)
    service = AgentService(runtime)
    seen = {"preview": [], "commit": [], "verify": []}
    original = verifier.PREDICATES

    def wrapped(fn, bucket):
        def inner(ctx):
            seen[bucket].append(fn)
            return fn(ctx)
        inner.__name__ = fn.__name__
        return inner

    steps = [{"verb": "gis_buffer", "parameters": {"input": d["id"], "target": "b",
              "distance": 10, "unit": "m", "dissolve": False}}]
    monkeypatch.setattr(verifier, "PREDICATES", tuple(wrapped(f, "preview") for f in original))
    monkeypatch.setitem(verifier.POLICY, "preview", verifier.PREDICATES)
    assert preview(runtime, "p", steps)["ok"]
    task = runtime.declare("buffer", steps)
    monkeypatch.setattr(verifier, "PREDICATES", tuple(wrapped(f, "commit") for f in original))
    monkeypatch.setitem(verifier.POLICY, "commit", verifier.PREDICATES)
    runtime.execute(task["id"], task["plan_digest"], True)
    monkeypatch.setattr(verifier, "PREDICATES", tuple(wrapped(f, "verify") for f in original))
    monkeypatch.setitem(verifier.POLICY, "verify", verifier.PREDICATES)
    reply = service.call("task_get", {"task_id": task["id"], "verify": True})
    assert reply["ok"], reply
    assert seen["preview"] and seen["commit"] and seen["verify"]
    assert {id(f) for f in seen["preview"]} == {id(f) for f in seen["commit"]} == {id(f) for f in seen["verify"]}


def test_preview_rename_reports_unconfirmed_head_checks_without_failing(runtime):
    d = src(runtime)
    result = preview(runtime, "rename", [{"verb": "gis_rename_dataset", "parameters": {
        "dataset": d["id"], "new_name": "renamed"}}])
    assert result["ok"], result
    checks = result["steps"][0]["verification"]["found"][-1]["details"]["checks"]
    by_id = {c["id"]: c["passed"] for c in checks}
    assert by_id["rename_content_unchanged"] is True
    assert by_id["rename_head_resolves"] is None
    assert result["steps"][0]["verification"]["ok"] is True


def test_unconfirmed_checks_are_excluded_from_the_hard_gate(runtime):
    d = src(runtime)
    result = preview(runtime, "rename", [{"verb": "gis_rename_dataset", "parameters": {
        "dataset": d["id"], "new_name": "renamed"}}])
    checks = result["steps"][0]["verification"]["found"][-1]["details"]
    assert any(c["passed"] is None for c in checks["checks"])
    assert checks["unconfirmed_checks"]
    legacy = verifier.legacy_checks(result["steps"][0]["verification"])
    assert all(c["passed"] is not None for c in legacy)