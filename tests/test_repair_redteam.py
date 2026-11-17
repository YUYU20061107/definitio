"""Red-team regression for the 2026-11-17 repair round.

The thirteen historical counterexamples from
`evidence/redteam-20261116/RedTeam-AgentNativeGIS-20261116.md` are restated here as
assertions of *correct* behaviour, together with the legitimate positive cases that the
old validators wrongly rejected and boundary cases for the same shared predicates.

Every case builds a private temporary workspace: no production data, no network, no
model calls. Nothing in this file may be relaxed to make a failing case pass; a failure
here means the engine certified something it could not prove.

PORTING NOTE (08 tree, 2026-11-17 closure repair round): this file was ported verbatim
from the D-drive parallel repair (evidence/repair-20261117, 47 items) with exactly one
fixture adaptation -- the grant() helper below. The D tree passed "file_export" as a
*verb*; the 08 authorization model takes side-effect classes via capabilities +
max_file_exports (see authorization.EFFECT_KINDS), so the helper translates
verbs=["...", "file_export"] into capabilities=["dataset", "file_export"] with
max_file_exports=maximum. No assertion was changed. Items that fail on the 08 tree are
kept failing and recorded in repair/redteam-closure-20261117/REDTEAM-REGRESSION.md for
adjudication; they are not relaxed here.
"""
import json
import sqlite3
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from shapely.geometry import LineString, MultiPoint, MultiPolygon, box, mapping

import definitio.runtime as runtime_module
from definitio import commit, completion
from definitio.agent import AgentService
from definitio.runtime import Runtime

SESSION = "repair-redteam-session-" + "r" * 32


@pytest.fixture
def rt(tmp_path):
    return Runtime(tmp_path / "workspace.sqlite3")


@pytest.fixture
def service(rt):
    return AgentService(rt)


def feature(geom, **props):
    return {"type": "Feature", "geometry": mapping(geom), "properties": props or {"value": 7}}


def import_fc(rt, name, features, crs="EPSG:32649"):
    return rt.import_dataset(name, {"type": "FeatureCollection", "features": features}, crs)


def run_verb(rt, verb, parameters, confirm=True):
    task = rt.declare(verb, [{"verb": verb, "parameters": parameters}])
    return rt.execute(task["id"], task["plan_digest"], confirm)


def run_corrupted(rt, verb, parameters, mutate):
    """Replace the operator's own proposal after it computed, leaving every self-consistent
    fingerprint, schema and count intact. Only a real spatial predicate can catch this."""
    task = rt.declare(verb, [{"verb": verb, "parameters": parameters}])
    real = runtime_module.compute

    def corrupt(name, p, inputs):
        proposal = real(name, p, inputs)
        if name == verb:
            mutate(proposal)
        return proposal

    with patch.object(runtime_module, "compute", corrupt):
        return rt.execute(task["id"], task["plan_digest"], True)


def failed_check(task):
    return (task.get("error") or {}).get("details", {}).get("check")


def set_geometry(index=0):
    def mutate(proposal):
        proposal["geojson"]["features"][index]["geometry"] = mapping(
            MultiPolygon([box(1000, 1000, 1010, 1010)]))
    return mutate


# ---------------------------------------------------------------------------
# RT-01 / RT-09  buffer: position, offset distance, per-component bounds
# ---------------------------------------------------------------------------

def test_buffer_rejects_a_result_at_the_wrong_position(rt):
    d = import_fc(rt, "src", [feature(box(0, 0, 10, 10))])
    task = run_corrupted(rt, "gis_buffer",
                         {"input": d["id"], "target": "buf", "distance": 10, "unit": "m", "dissolve": False},
                         set_geometry())
    assert task["task_state"] == "FAILED"
    assert failed_check(task) in ("buffer_offset_distance", "buffer_same_kernel_equivalence",
                                  "buffer_contains_source", "buffer_area_upper_bound")
    assert task["output_refs"] == []


def test_buffer_rejects_an_unbuffered_identity_result(rt):
    d = import_fc(rt, "src2", [feature(box(100, 100, 110, 110))])
    task = run_corrupted(rt, "gis_buffer",
                         {"input": d["id"], "target": "buf2", "distance": 10, "unit": "m", "dissolve": False},
                         lambda p: p["geojson"]["features"][0].__setitem__(
                             "geometry", mapping(box(100, 100, 110, 110))))
    assert task["task_state"] == "FAILED"
    assert failed_check(task) in ("buffer_offset_distance", "buffer_same_kernel_equivalence",
                                  "buffer_contains_source")


def test_buffer_rejects_a_wrong_distance_multiple(rt):
    d = import_fc(rt, "src", [feature(box(0, 0, 10, 10))])
    task = run_corrupted(rt, "gis_buffer",
                         {"input": d["id"], "target": "buf", "distance": 10, "unit": "m", "dissolve": False},
                         lambda p: p["geojson"]["features"][0].__setitem__(
                             "geometry", mapping(box(-10, -10, 20, 20))))
    assert task["task_state"] == "FAILED"
    assert failed_check(task) in ("buffer_area_upper_bound", "buffer_offset_distance",
                                  "buffer_same_kernel_equivalence")


def test_negative_buffer_grows_instead_of_eroding_is_rejected(rt):
    d = import_fc(rt, "src", [feature(box(500000, 2540000, 500100, 2540100))])
    task = run_corrupted(rt, "gis_buffer",
                         {"input": d["id"], "target": "erode", "distance": -10, "unit": "m", "dissolve": False},
                         lambda p: p["geojson"]["features"][0].__setitem__(
                             "geometry", mapping(box(499990, 2539990, 500110, 2540110))))
    assert task["task_state"] == "FAILED"
    assert failed_check(task) in ("buffer_erosion_within_source", "buffer_area_lower_bound",
                                  "buffer_same_kernel_equivalence")


@pytest.mark.parametrize("geometry", [
    MultiPoint([(0, 0), (100, 0)]),
    LineString([(0, 0), (100, 0)]),
    box(0, 0, 10, 10),
])
def test_legitimate_buffer_geometry_kinds_are_accepted(rt, geometry):
    d = import_fc(rt, "src", [feature(geometry)])
    task = run_verb(rt, "gis_buffer",
                    {"input": d["id"], "target": "buf", "distance": 10, "unit": "m", "dissolve": False})
    assert task["task_state"] == "COMMITTED", task.get("error")


def test_multipoint_buffer_is_not_rejected_by_a_single_component_bound(rt):
    """RT-09: two separated points carry two pi*r^2 caps, not one."""
    d = import_fc(rt, "mp", [feature(MultiPoint([(0, 0), (100, 0)]))])
    task = run_verb(rt, "gis_buffer",
                    {"input": d["id"], "target": "buf", "distance": 10, "unit": "m", "dissolve": True})
    assert task["task_state"] == "COMMITTED", task.get("error")
    checks = {c["id"]: c["passed"] for c in
              rt.store.evidence(task["steps"][0]["evidence_ref"])["postcondition_checks"]}
    assert checks["buffer_area_upper_bound"] is True


def test_buffer_in_degree_units_is_refused_before_any_write(rt):
    d = import_fc(rt, "geo", [feature(box(0, 0, 0.1, 0.1))], crs="EPSG:4326")
    task = run_verb(rt, "gis_buffer",
                    {"input": d["id"], "target": "buf", "distance": 10, "unit": "m", "dissolve": False})
    assert task["task_state"] == "FAILED"
    assert task["error"]["code"] == "NOT_SUPPORTED"
    assert task["actual_writes"] == []


# ---------------------------------------------------------------------------
# RT-02  clip: second operand, record identity, attribute retention
# ---------------------------------------------------------------------------

def clip_setup(rt):
    d = import_fc(rt, "src", [feature(box(0, 0, 10, 10))])
    mask = import_fc(rt, "mask", [feature(box(0, 0, 2, 10), value=7)])
    return d, mask


def test_clip_rejects_a_result_outside_the_mask(rt):
    d, mask = clip_setup(rt)
    task = run_corrupted(rt, "gis_clip",
                         {"input": d["id"], "clip_by": mask["id"], "target": "clipped"},
                         lambda p: p["geojson"]["features"][0].__setitem__(
                             "geometry", mapping(box(8, 0, 10, 10))))
    assert task["task_state"] == "FAILED"
    assert failed_check(task) in ("clip_geometry_index_aligned", "clip_output_within_mask",
                                  "subset_within_input")


def test_clip_rejects_a_result_that_lost_area(rt):
    d, mask = clip_setup(rt)
    task = run_corrupted(rt, "gis_clip",
                         {"input": d["id"], "clip_by": mask["id"], "target": "clipped"},
                         lambda p: p["geojson"]["features"][0].__setitem__(
                             "geometry", mapping(box(0, 0, 0.5, 2))))
    assert task["task_state"] == "FAILED"
    assert failed_check(task) == "clip_geometry_index_aligned"


def test_clip_with_duplicate_properties_matches_by_record_not_by_value(rt):
    """RT-02 (B): equal property values are not record identity."""
    d = import_fc(rt, "dup", [
        {"type": "Feature", "geometry": mapping(box(0, 0, 10, 10)), "properties": {"value": 7}},
        {"type": "Feature", "geometry": mapping(box(100, 100, 110, 110)), "properties": {"value": 7}},
    ])
    mask = import_fc(rt, "mask", [feature(box(99, 99, 111, 111), value=7)])
    task = run_verb(rt, "gis_clip", {"input": d["id"], "clip_by": mask["id"], "target": "clipped"})
    assert task["task_state"] == "COMMITTED", task.get("error")
    assert task["verification"]["found"][-1]["passed"] is True
    out = rt.store.dataset(task["output_refs"][0])
    assert out["feature_count"] == 1
    assert out["bounds"] == [100.0, 100.0, 110.0, 110.0]


@pytest.mark.parametrize("operation", ["intersection", "difference"])
def test_overlay_keeps_both_sides_of_the_contract(rt, operation):
    d = import_fc(rt, "src", [feature(box(0, 0, 10, 10))])
    other = import_fc(rt, "other", [feature(box(5, 5, 20, 20), value=7)])
    task = run_verb(rt, "gis_overlay", {"input": d["id"], "overlay": other["id"],
                                        "target": "ov", "operation": operation})
    assert task["task_state"] == "COMMITTED", task.get("error")


def test_overlay_difference_that_keeps_the_subtracted_region_is_rejected(rt):
    d = import_fc(rt, "src", [feature(box(0, 0, 10, 10))])
    other = import_fc(rt, "other", [feature(box(0, 0, 5, 10), value=7)])
    task = run_corrupted(rt, "gis_overlay",
                         {"input": d["id"], "overlay": other["id"], "target": "ov",
                          "operation": "difference"},
                         lambda p: p["geojson"]["features"][0].__setitem__(
                             "geometry", mapping(box(0, 0, 5, 10))))
    assert task["task_state"] == "FAILED"
    assert failed_check(task) in ("clip_output_outside_subtracted_mask", "clip_geometry_index_aligned")


# ---------------------------------------------------------------------------
# RT-03  spatial join: which record was actually joined
# ---------------------------------------------------------------------------

def join_setup(rt):
    left = import_fc(rt, "left", [feature(box(0, 0, 10, 10), value=7)])
    right = import_fc(rt, "zones", [feature(box(5, 5, 15, 15), zone="correct")])
    return left, right


def test_join_rejects_a_wrong_joined_attribute(rt):
    left, right = join_setup(rt)
    def mutate(proposal):
        proposal["geojson"]["features"][0]["properties"]["zone"] = "WRONG_ZONE"
    task = run_corrupted(rt, "gis_spatial_join",
                         {"input": left["id"], "join": right["id"], "target": "joined",
                          "predicate": "intersects", "summary": "first"}, mutate)
    assert task["task_state"] == "FAILED"
    assert failed_check(task) == "join_record_correspondence"


def test_join_rejects_a_swapped_right_record(rt):
    left = import_fc(rt, "left", [feature(box(0, 0, 10, 10), value=7)])
    right = import_fc(rt, "zones", [feature(box(5, 5, 15, 15), zone="A"),
                                    feature(box(6, 6, 16, 16), zone="B")])
    def mutate(proposal):
        proposal["geojson"]["features"][0]["properties"]["zone"] = "B"
    task = run_corrupted(rt, "gis_spatial_join",
                         {"input": left["id"], "join": right["id"], "target": "joined",
                          "predicate": "intersects", "summary": "all"}, mutate)
    assert task["task_state"] == "FAILED"
    assert failed_check(task) == "join_record_correspondence"


@pytest.mark.parametrize("summary", ["first", "all"])
def test_join_one_to_many_and_left_outer_nulls_still_commit(rt, summary):
    left = import_fc(rt, "left", [
        feature(box(0, 0, 10, 10), value=1),
        feature(box(1000, 1000, 1010, 1010), value=2),
    ])
    right = import_fc(rt, "zones", [
        feature(box(5, 5, 15, 15), zone="Z1"),
        feature(box(6, 6, 16, 16), zone="Z2"),
    ])
    task = run_verb(rt, "gis_spatial_join",
                    {"input": left["id"], "join": right["id"], "target": f"j_{summary}",
                     "predicate": "intersects", "summary": summary})
    assert task["task_state"] == "COMMITTED", task.get("error")
    out = rt.store.dataset(task["output_refs"][0])
    assert out["feature_count"] == (3 if summary == "all" else 2)


# ---------------------------------------------------------------------------
# RT-04  object-level authorization
# ---------------------------------------------------------------------------

def grant(service, verbs, prefix="safe_", maximum=2):
    # 08-tree fixture adaptation (see module docstring): "file_export" is a
    # side-effect capability here, not a verb.
    caps = ["dataset"] + (["file_export"] if "file_export" in verbs else [])
    real_verbs = [v for v in verbs if v != "file_export"]
    reply = service.call("authorization_grant",
                         {"target_prefix": prefix, "verbs": real_verbs, "max_writes": maximum,
                          "capabilities": caps,
                          "max_file_exports": maximum if "file_export" in verbs else 0,
                          "ttl_seconds": 600, "confirm": True, "request_id": "grant-" + "-".join(verbs)},
                         session=SESSION)
    assert reply["ok"], reply
    return reply["data"]["authorization"]["id"]


def test_a_dataset_only_grant_never_covers_a_file_export(rt, service):
    """RT-04: the plan's own declared exports are not user authority."""
    d = import_fc(rt, "src", [feature(box(0, 0, 10, 10))])
    task = rt.declare("grant export", [{"verb": "gis_buffer", "parameters": {
        "input": d["id"], "target": "safe_buf", "distance": 10, "unit": "m", "dissolve": False}}],
        exports=[{"step": 1, "name": "outside_scope.geojson", "format": "geojson"}])
    grant_id = grant(service, ["gis_buffer"], maximum=4)
    reply = service.call("task_execute", {"task_id": task["id"], "plan_digest": task["plan_digest"],
                                          "authorization_id": grant_id}, session=SESSION)
    assert reply["ok"] is False
    assert reply["status"] == "error"
    assert reply["error"]["code"] == "AUTHORIZATION_SCOPE"
    assert not (rt.store.path.parent / "artifacts" / "outside_scope.geojson").exists()
    stored = rt.store.task(task["id"])
    assert stored["task_state"] == "DECLARED" and stored["actual_writes"] == []


def test_an_explicit_file_capability_authorizes_the_export(rt, service):
    d = import_fc(rt, "src", [feature(box(0, 0, 10, 10))])
    task = rt.declare("grant export ok", [{"verb": "gis_buffer", "parameters": {
        "input": d["id"], "target": "safe_buf", "distance": 10, "unit": "m", "dissolve": False}}],
        exports=[{"step": 1, "name": "safe_buf.geojson", "format": "geojson"}])
    grant_id = grant(service, ["gis_buffer", "file_export"], maximum=4)
    reply = service.call("task_execute", {"task_id": task["id"], "plan_digest": task["plan_digest"],
                                          "authorization_id": grant_id}, session=SESSION)
    assert reply["ok"] is True, reply
    assert (rt.store.path.parent / "artifacts" / "safe_buf.geojson").exists()
    stored = rt.store.task(task["id"])
    assert stored["task_state"] == "COMMITTED"
    assert {w["kind"] for w in stored["actual_writes"]} == {"dataset", "file"}


def test_a_target_outside_the_granted_prefix_is_refused(rt, service):
    d = import_fc(rt, "src", [feature(box(0, 0, 10, 10))])
    task = rt.declare("prefix", [{"verb": "gis_buffer", "parameters": {
        "input": d["id"], "target": "outside", "distance": 10, "unit": "m", "dissolve": False}}])
    grant_id = grant(service, ["gis_buffer"])
    reply = service.call("task_execute", {"task_id": task["id"], "plan_digest": task["plan_digest"],
                                          "authorization_id": grant_id}, session=SESSION)
    assert reply["ok"] is False and reply["error"]["code"] == "AUTHORIZATION_SCOPE"
    assert rt.store.task(task["id"])["actual_writes"] == []


def test_the_reservation_counts_the_file_write_too(rt, service):
    d = import_fc(rt, "src", [feature(box(0, 0, 10, 10))])
    task = rt.declare("counting", [{"verb": "gis_buffer", "parameters": {
        "input": d["id"], "target": "safe_buf", "distance": 10, "unit": "m", "dissolve": False}}],
        exports=[{"step": 1, "name": "safe_buf.geojson", "format": "geojson"}])
    grant_id = grant(service, ["gis_buffer", "file_export"], maximum=1)
    reply = service.call("task_execute", {"task_id": task["id"], "plan_digest": task["plan_digest"],
                                          "authorization_id": grant_id}, session=SESSION)
    assert reply["ok"] is False and reply["error"]["code"] == "AUTHORIZATION_EXHAUSTED"


def test_revoked_grant_cannot_execute(rt, service):
    d = import_fc(rt, "src", [feature(box(0, 0, 10, 10))])
    task = rt.declare("revoked", [{"verb": "gis_buffer", "parameters": {
        "input": d["id"], "target": "safe_buf", "distance": 10, "unit": "m", "dissolve": False}}])
    grant_id = grant(service, ["gis_buffer"])
    service.call("authorization_revoke", {"authorization_id": grant_id}, session=SESSION)
    reply = service.call("task_execute", {"task_id": task["id"], "plan_digest": task["plan_digest"],
                                          "authorization_id": grant_id}, session=SESSION)
    assert reply["ok"] is False and reply["error"]["code"] == "AUTHORIZATION_REQUIRED"
    assert rt.store.task(task["id"])["actual_writes"] == []


@pytest.mark.parametrize("name", ["../escape.geojson", "a/b.geojson", "..\\x.geojson"])
def test_artifact_names_cannot_carry_a_path(rt, name):
    d = import_fc(rt, "src", [feature(box(0, 0, 10, 10))])
    with pytest.raises(Exception) as caught:
        rt.declare("path", [{"verb": "gis_buffer", "parameters": {
            "input": d["id"], "target": "buf", "distance": 10, "unit": "m", "dissolve": False}}],
            exports=[{"step": 1, "name": name, "format": "geojson"}])
    assert getattr(caught.value, "code", "") == "INVALID_ARGUMENT"


# ---------------------------------------------------------------------------
# RT-05 / P0-07  a verified artifact is bound to current state, not to its receipt
# ---------------------------------------------------------------------------

def export_task(rt, name="buf.geojson"):
    d = import_fc(rt, "src", [feature(box(0, 0, 10, 10))])
    task = rt.declare("export", [{"verb": "gis_buffer", "parameters": {
        "input": d["id"], "target": "safe_buf", "distance": 10, "unit": "m", "dissolve": False}}],
        exports=[{"step": 1, "name": name, "format": "geojson"}])
    return rt.execute(task["id"], task["plan_digest"], True)


def test_replay_reports_a_missing_file_instead_of_the_old_receipt(rt):
    task = export_task(rt)
    assert task["task_state"] == "COMMITTED"
    (rt.store.path.parent / "artifacts" / "buf.geojson").unlink()
    with pytest.raises(Exception) as caught:
        rt.execute(task["id"], task["plan_digest"], True)
    assert getattr(caught.value, "code", "") == "TARGET_STATE_UNKNOWN"
    assert "已提交文件产物的当前状态" in str(caught.value)


def test_verify_reports_a_missing_file_as_not_ok(service):
    task = export_task(service.runtime)
    (service.runtime.store.path.parent / "artifacts" / "buf.geojson").unlink()
    reply = service.call("task_get", {"task_id": task["id"], "verify": True})
    verification = reply["data"]["verification"]
    assert verification["ok"] is False
    assert any(f["predicate"] == "artifact_state_binding" and f["passed"] is False
               for step in verification["steps"] for f in step["found"])


def test_verify_reports_a_deleted_output_dataset_as_not_ok(service):
    rt = service.runtime
    d = import_fc(rt, "src", [feature(box(0, 0, 10, 10))])
    task = run_verb(rt, "gis_buffer",
                    {"input": d["id"], "target": "buf", "distance": 10, "unit": "m", "dissolve": False})
    assert task["task_state"] == "COMMITTED"
    with sqlite3.connect(rt.store.path) as db:
        db.execute("DELETE FROM datasets WHERE id=?", (task["output_refs"][0],))
        db.commit()
    reply = service.call("task_get", {"task_id": task["id"], "verify": True})
    verification = reply["data"]["verification"]
    assert verification["ok"] is False
    assert any(f["predicate"] == "output_state_binding" and f["passed"] is False
               for step in verification["steps"] for f in step["found"])


# ---------------------------------------------------------------------------
# RT-08  re-verification must have something to verify
# ---------------------------------------------------------------------------

def test_verify_of_a_failed_task_is_not_ok(rt, service):
    d = import_fc(rt, "src", [feature(box(0, 0, 10, 10))])
    task = rt.declare("boom", [{"verb": "gis_buffer", "parameters": {
        "input": d["id"], "target": "buf", "distance": 10, "unit": "m", "dissolve": False}}])
    def explode(name, p, inputs):
        raise RuntimeError("injected computation failure")
    with patch.object(runtime_module, "compute", explode):
        task = rt.execute(task["id"], task["plan_digest"], True)
    assert task["task_state"] == "FAILED"
    verification = service.verify_task(task)
    assert verification["ok"] is False
    assert verification["status"] == "task_not_committed"
    assert verification["steps"] == []
    assert verification["coverage"]["steps_checked"] == 0


def test_verify_detects_a_tampered_read_only_result(rt, service):
    d = import_fc(rt, "src", [feature(box(0, 0, 10, 10))])
    task = run_verb(rt, "gis_field_statistics", {
        "dataset": d["id"], "fields": ["value"], "group_by": [], "statistics": ["sum"],
        "output": "result", "null_policy": "exclude"})
    assert task["task_state"] == "COMMITTED"
    with sqlite3.connect(rt.store.path) as db:
        row = db.execute("SELECT document FROM tasks WHERE id=?", (task["id"],)).fetchone()
        document = json.loads(row[0])
        document["steps"][0]["result"]["groups"][0]["fields"]["value"]["sum"] = 999999
        db.execute("UPDATE tasks SET document=? WHERE id=?", (json.dumps(document), task["id"]))
        db.commit()
    verification = service.verify_task(rt.store.task(task["id"]))
    assert verification["ok"] is False
    assert any(f["predicate"] == "readonly_result_binding" and f["passed"] is False
               for step in verification["steps"] for f in step["found"])


def test_verify_of_a_committed_task_reports_verified_with_coverage(rt, service):
    d = import_fc(rt, "src", [feature(box(0, 0, 10, 10))])
    task = run_verb(rt, "gis_buffer",
                    {"input": d["id"], "target": "buf", "distance": 10, "unit": "m", "dissolve": False})
    verification = service.verify_task(task)
    assert verification["status"] == "verified" and verification["ok"] is True
    assert verification["coverage"] == {"steps_total": 1, "steps_checked": 1, "steps_skipped": 0,
                                        "file_artifacts_declared": 0}


# ---------------------------------------------------------------------------
# RT-06  external side effect followed by an unparseable response
# ---------------------------------------------------------------------------

class _StubClient:
    def __init__(self, *a, **k):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *a):
        pass

    def get(self, *a, **k):
        return SimpleNamespace(status_code=404)


def test_publish_after_a_side_effect_is_isolated_not_rolled_back(rt):
    d = import_fc(rt, "src", [feature(box(0, 0, 10, 10))])
    effects = []

    def fake_request(cfg, method, path, **kwargs):
        if method in ("POST", "PUT"):
            effects.append({"method": method, "path": path})
        return (b'{"style":{}}' if path.startswith("rest/styles") else b'{', "mock://offline", 200)

    cfg = {"sandbox": True, "allow_publish": True, "url": "http://127.0.0.1:1",
           "workspace_prefix": "definitio_", "user": "synthetic", "password": "synthetic"}
    with patch.object(completion, "licensed_dataset", return_value=(d, {"id": "synthetic_license"})), \
            patch.object(completion, "config", return_value=cfg), \
            patch.object(completion, "worker", return_value={"base64": ""}), \
            patch.object(completion, "request", fake_request), \
            patch.object(completion.httpx, "Client", _StubClient):
        task = rt.declare("publish malformed response", [{"verb": "gis_publish_layer", "parameters": {
            "dataset_ref": d["id"], "workspace": "definitio_redteam", "style": "polygon",
            "purpose": "noncommercial"}}])
        task = rt.execute(task["id"], task["plan_digest"], True)
    assert len(effects) == 3, effects
    assert task["task_state"] == "ISOLATED"
    assert task["error"]["code"] == "TARGET_STATE_UNKNOWN"
    assert task["execution_started"] is True
    assert task["steps"][0]["state"] == "ISOLATED"


def test_publish_transport_failure_before_any_write_is_not_isolated(rt):
    d = import_fc(rt, "src", [feature(box(0, 0, 10, 10))])
    from definitio.model import GISFault

    def refuse(cfg, method, path, **kwargs):
        raise GISFault("BACKEND_ERROR", "connection refused")

    cfg = {"sandbox": True, "allow_publish": True, "url": "http://127.0.0.1:1",
           "workspace_prefix": "definitio_", "user": "synthetic", "password": "synthetic"}
    with patch.object(completion, "licensed_dataset", return_value=(d, {"id": "synthetic_license"})), \
            patch.object(completion, "config", return_value=cfg), \
            patch.object(completion, "worker", return_value={"base64": ""}), \
            patch.object(completion, "request", refuse), \
            patch.object(completion.httpx, "Client", _StubClient):
        task = rt.declare("publish preflight", [{"verb": "gis_publish_layer", "parameters": {
            "dataset_ref": d["id"], "workspace": "definitio_redteam", "style": "polygon",
            "purpose": "noncommercial"}}])
        task = rt.execute(task["id"], task["plan_digest"], True)
    assert task["task_state"] == "FAILED"
    assert task["error"]["code"] == "BACKEND_ERROR"


# ---------------------------------------------------------------------------
# RT-07  a failed publication must not poison its name or request key
# ---------------------------------------------------------------------------

def test_a_publication_that_vanishes_is_rolled_back_and_can_be_retried(rt):
    d = import_fc(rt, "src", [feature(box(0, 0, 10, 10))])
    task = rt.declare("export", [{"verb": "gis_buffer", "parameters": {
        "input": d["id"], "target": "safe_buf", "distance": 10, "unit": "m", "dissolve": False}}],
        exports=[{"step": 1, "name": "buf.geojson", "format": "geojson"}])
    with patch.object(commit, "readback_file_artifact",
                      return_value={"confirmed": False, "reason": "published_file_missing"}):
        task = rt.execute(task["id"], task["plan_digest"], True)
    assert task["task_state"] == "FAILED"
    assert rt.store.file_artifacts() == []
    assert not (rt.store.path.parent / "artifacts" / "buf.geojson").exists()
    events = rt.store.file_artifact_events("buf.geojson")
    assert [e["event"] for e in events] == ["rolled_back"]
    # The name and the request key are free again, so recovery is a normal resume.
    resumed = rt.resume(task["id"], task["plan_digest"], True)
    assert resumed["task_state"] == "COMMITTED"
    assert (rt.store.path.parent / "artifacts" / "buf.geojson").exists()


def test_a_corrupted_publication_is_not_silently_rolled_back(rt):
    d = import_fc(rt, "src", [feature(box(0, 0, 10, 10))])
    task = rt.declare("export", [{"verb": "gis_buffer", "parameters": {
        "input": d["id"], "target": "safe_buf", "distance": 10, "unit": "m", "dissolve": False}}],
        exports=[{"step": 1, "name": "buf.geojson", "format": "geojson"}])
    with patch.object(commit, "readback_file_artifact",
                      return_value={"confirmed": False, "reason": "published_bytes_mismatch"}):
        task = rt.execute(task["id"], task["plan_digest"], True)
    assert task["task_state"] == "FAILED"
    assert task["error"]["code"] == "TARGET_STATE_UNKNOWN"
    # Bytes that do not match the registration are evidence, not litter: keep both.
    assert len(rt.store.file_artifacts()) == 1
    assert (rt.store.path.parent / "artifacts" / "buf.geojson").exists()


# ---------------------------------------------------------------------------
# RT-10 / RT-11 / Phase 4  legitimate results that used to be rejected
# ---------------------------------------------------------------------------

def test_hillshade_back_lit_slope_is_accepted(rt):
    d = rt.import_raster("dem", {"data": [[float(c * 100) for c in range(5)] for _ in range(5)],
                                 "transform": [1, 0, 0, 0, -1, 5], "nodata": None,
                                 "dtype": "float32", "band_name": "elevation"}, "EPSG:32649")
    task = run_verb(rt, "gis_hillshade", {"input": d["id"], "target": "shade", "z_factor": 1,
                                          "azimuth": 90, "altitude": 45, "nodata_policy": "propagate"})
    assert task["task_state"] == "COMMITTED", task.get("error")


def test_reproject_preserves_a_legal_empty_polygon(rt):
    empty = {"type": "Polygon", "coordinates": []}
    d = rt.import_dataset("empty", {"type": "FeatureCollection", "features": [
        {"type": "Feature", "geometry": empty, "properties": {"value": 1}}]}, "EPSG:32649")
    assert d["feature_count"] == 1
    task = run_verb(rt, "gis_reproject", {"input": d["id"], "target": "moved",
                                          "target_crs": "EPSG:4326"})
    assert task["task_state"] == "COMMITTED", task.get("error")


def test_invalid_geometry_is_refused_before_any_write(rt):
    bowtie = {"type": "Polygon", "coordinates": [[[0, 0], [10, 10], [10, 0], [0, 10], [0, 0]]]}
    d = rt.import_dataset("bowtie", {"type": "FeatureCollection", "features": [
        {"type": "Feature", "geometry": bowtie, "properties": {"value": 1}}]}, "EPSG:32649")
    task = run_verb(rt, "gis_buffer", {"input": d["id"], "target": "buf", "distance": 10,
                                       "unit": "m", "dissolve": False})
    assert task["task_state"] == "FAILED"
    assert task["error"]["code"] == "GEOMETRY_INVALID"
    assert task["actual_writes"] == []


def test_empty_clip_result_commits_with_zero_rows(rt):
    d = import_fc(rt, "src", [feature(box(0, 0, 10, 10))])
    far = import_fc(rt, "far", [feature(box(1000, 1000, 1010, 1010), value=7)])
    task = run_verb(rt, "gis_clip", {"input": d["id"], "clip_by": far["id"], "target": "clipped"})
    assert task["task_state"] == "COMMITTED", task.get("error")
    assert rt.store.dataset(task["output_refs"][0])["feature_count"] == 0


# ---------------------------------------------------------------------------
# P1-08  preview / commit consistency
# ---------------------------------------------------------------------------

def test_a_changed_input_after_planning_invalidates_the_plan(rt):
    d = import_fc(rt, "src", [feature(box(0, 0, 10, 10))])
    task = rt.declare("stale", [{"verb": "gis_buffer", "parameters": {
        "input": d["id"], "target": "buf", "distance": 10, "unit": "m", "dissolve": False}}])
    with sqlite3.connect(rt.store.path) as db:
        row = db.execute("SELECT document FROM datasets WHERE id=?", (d["id"],)).fetchone()
        document = json.loads(row[0])
        document["geojson"]["features"][0]["properties"]["value"] = 99
        db.execute("UPDATE datasets SET document=? WHERE id=?", (json.dumps(document), d["id"]))
        db.commit()
    executed = rt.execute(task["id"], task["plan_digest"], True)
    assert executed["task_state"] == "FAILED"
    assert executed["commit_state"] == "rejected_state_changed"
    assert executed["actual_writes"] == []


def test_an_occupied_target_after_planning_is_refused(rt):
    d = import_fc(rt, "src", [feature(box(0, 0, 10, 10))])
    task = rt.declare("occupied", [{"verb": "gis_buffer", "parameters": {
        "input": d["id"], "target": "buf", "distance": 10, "unit": "m", "dissolve": False}}])
    import_fc(rt, "buf", [feature(box(50, 50, 60, 60))])
    executed = rt.execute(task["id"], task["plan_digest"], True)
    assert executed["task_state"] == "FAILED"
    assert executed["commit_state"] == "rejected_state_changed"
    assert executed["actual_writes"] == []


def test_a_changed_plan_digest_is_refused(rt):
    d = import_fc(rt, "src", [feature(box(0, 0, 10, 10))])
    task = rt.declare("digest", [{"verb": "gis_buffer", "parameters": {
        "input": d["id"], "target": "buf", "distance": 10, "unit": "m", "dissolve": False}}])
    with pytest.raises(Exception) as caught:
        rt.execute(task["id"], "0" * 64, True)
    assert "计划摘要" in str(caught.value)


# ---------------------------------------------------------------------------
# Boundary families: axis order, raster NoData, resolved-path confinement
# ---------------------------------------------------------------------------

def test_reproject_records_axis_order_and_unit_of_the_target_crs(rt):
    """The header must state the CRS's declared axis order, the storage order actually
    used, and whether they differ — a silent swap is how lat/lon datasets get misread."""
    d = import_fc(rt, "src", [feature(box(0, 0, 1000, 1000))])
    task = run_verb(rt, "gis_reproject", {"input": d["id"], "target": "moved",
                                          "target_crs": "EPSG:4326"})
    assert task["task_state"] == "COMMITTED", task.get("error")
    out = rt.store.dataset(task["output_refs"][0])
    assert out["crs"]["code"] == "EPSG:4326"
    assert out["crs"]["projected"] is False
    header = out["semantic"]["axis_order"]
    assert header["storage_order"] == "x,y"
    assert header["sequence"] == "north,east" and header["differs_from_storage"] is True

    projected = run_verb(rt, "gis_reproject", {"input": d["id"], "target": "utm",
                                               "target_crs": "EPSG:32649"})
    assert projected["task_state"] == "COMMITTED", projected.get("error")
    utm = rt.store.dataset(projected["output_refs"][0])["semantic"]["axis_order"]
    assert utm["sequence"] == "east,north" and utm["differs_from_storage"] is False


def test_raster_nodata_is_propagated_and_not_counted_as_a_value(rt):
    d = rt.import_raster("dem", {"data": [[1.0, None, 3.0], [4.0, 5.0, None], [7.0, 8.0, 9.0]],
                                 "transform": [1, 0, 0, 0, -1, 3], "nodata": None,
                                 "dtype": "float32", "band_name": "elevation"}, "EPSG:32649")
    task = run_verb(rt, "gis_raster_calc", {"inputs": [d["id"]], "expression": "A * 2",
                                            "target": "calc", "nodata_policy": "propagate"})
    assert task["task_state"] == "COMMITTED", task.get("error")
    out = rt.store.dataset(task["output_refs"][0])
    rows = out["raster"]["data"]
    assert rows[0][1] is None and rows[1][2] is None
    assert rows[0][0] == 2.0


def test_file_artifact_confinement_rejects_escapes_by_resolved_path(rt):
    from definitio import verifier

    task = export_task(rt)  # creates workspace/artifacts with one published file
    assert task["task_state"] == "COMMITTED"
    workspace = str(rt.store.path.parent)

    def finding_for(target):
        ctx = verifier.context(
            stage="verify", verb="task_commit", vid="", parameters={}, inputs=[], proposal=None,
            output=None, store=rt.store, db=None, planned_inputs=[], write_scope={"file_artifacts": []},
            actual_writes=[{"kind": "file", "target": target, "verb": "file_export"}],
            authorization=None, workspace=workspace, file_artifact=None)
        return verifier.file_artifact_confinement(ctx)

    assert finding_for("buf.geojson")["passed"] is True
    for escape in ("../escape.geojson", "a/b.geojson", "C:\\Windows\\x.geojson"):
        assert finding_for(escape)["passed"] is False, escape
