"""X-01 / X-02 regression (08 tree, 2026-11-17 red-team closure repair round).

X-01 (P1): task_get collapsed into a bare NOT_FOUND when a committed task's
output *dataset* had been deleted externally -- `_task_reply` dereferenced every
output ref before the (already repaired) verify path could run. The dataset side
now mirrors the file side's RT-05 live-readback accounting: the task state stays
visible, the missing output is named in unavailable_outputs, and the reply fails
as TARGET_STATE_UNKNOWN instead of masquerading as success or as "no such task".

X-02 (P2): the authorization layer always supported capabilities/max_file_exports,
but the published authorization_grant tool schema omitted both fields, so a
session grant could never carry the file_export capability. The schema now
publishes them; the authorization layer still enforces the cap before any write.
"""
import sqlite3

import pytest
from shapely.geometry import box, mapping

from definitio.agent import AgentService
from definitio.runtime import Runtime

SESSION = "xfix-session-" + "x" * 32


@pytest.fixture
def rt(tmp_path):
    return Runtime(tmp_path / "workspace.sqlite3")


@pytest.fixture
def service(rt):
    return AgentService(rt)


def src(rt, name="src"):
    return rt.import_dataset(name, {"type": "FeatureCollection", "features": [
        {"type": "Feature", "geometry": mapping(box(0, 0, 10, 10)),
         "properties": {"value": 7}}]}, "EPSG:32649")


def buffer_task(rt, target="safe_buf", exports=None):
    return rt.declare("x buffer", [{"verb": "gis_buffer", "parameters": {
        "input": src(rt)["id"], "target": target, "distance": 10, "unit": "m",
        "dissolve": False}}], exports=exports)


def committed_buffer(rt):
    task = buffer_task(rt)
    task = rt.execute(task["id"], task["plan_digest"], True)
    assert task["task_state"] == "COMMITTED", task.get("error")
    return task


def delete_dataset_row(rt, ref):
    with sqlite3.connect(rt.store.path) as db:
        db.execute("DELETE FROM datasets WHERE id=?", (ref,))
        db.commit()


# ---------------------------------------------------------------------------
# X-01  task_get after the committed output dataset is deleted
# ---------------------------------------------------------------------------

def test_task_get_keeps_state_when_output_dataset_deleted(rt, service):
    task = committed_buffer(rt)
    ref = task["output_refs"][0]
    delete_dataset_row(rt, ref)
    reply = service.call("task_get", {"task_id": task["id"]})
    assert reply["ok"] is False
    assert reply["error"]["code"] == "TARGET_STATE_UNKNOWN"
    data = reply["data"]
    # The task state itself must survive: this is not "no such task".
    assert data["task"]["id"] == task["id"]
    assert data["task"]["task_state"] == "COMMITTED"
    assert data["outputs"] == []
    assert [u["ref"] for u in data["unavailable_outputs"]] == [ref]
    assert data["unavailable_outputs"][0]["error"]["code"] == "NOT_FOUND"


def test_task_get_verify_still_reports_when_output_dataset_deleted(rt, service):
    task = committed_buffer(rt)
    delete_dataset_row(rt, task["output_refs"][0])
    reply = service.call("task_get", {"task_id": task["id"], "verify": True})
    assert reply["error"]["code"] == "TARGET_STATE_UNKNOWN"
    verification = reply["data"]["verification"]
    assert verification["ok"] is False
    # 08 verify semantics: the step whose output vanished cannot be re-judged, so
    # it is skipped and the run is "incomplete" -- while live_status states the
    # precise fact (dataset missing_or_corrupt, overall state corrupted). Both
    # layers refuse to call it verified; nothing is silently passed.
    assert verification["status"] == "incomplete"
    assert verification["coverage"]["checked_steps"] == 0
    assert verification["coverage"]["skipped_items"][0]["step"] == 1
    assert verification["live_status"]["state"] == "corrupted"
    assert verification["live_status"]["datasets"][0]["state"] == "missing_or_corrupt"


def test_task_get_file_side_symmetry_when_artifact_file_deleted(rt, service):
    """The file-side twin of X-01 (RT-05): deleting the published file must be
    reported by the same task_get+verify surface, as corrupted -- never ok."""
    task = buffer_task(rt, exports=[{"step": 1, "name": "safe_buf.geojson",
                                     "format": "geojson"}])
    task = rt.execute(task["id"], task["plan_digest"], True)
    assert task["task_state"] == "COMMITTED", task.get("error")
    (rt.store.path.parent / "artifacts" / "safe_buf.geojson").unlink()
    reply = service.call("task_get", {"task_id": task["id"], "verify": True})
    verification = reply["data"]["verification"]
    assert verification["ok"] is False
    assert verification["status"] == "corrupted"
    assert verification["live_status"]["file_artifacts"][0]["confirmed"] is False


def test_task_get_committed_task_healthy_path_unchanged(rt, service):
    task = committed_buffer(rt)
    reply = service.call("task_get", {"task_id": task["id"]})
    assert reply["ok"] is True, reply
    assert reply["data"]["unavailable_outputs"] == []
    assert [o["id"] for o in reply["data"]["outputs"]] == task["output_refs"]
    reply_v = service.call("task_get", {"task_id": task["id"], "verify": True})
    assert reply_v["data"]["verification"]["ok"] is True
    assert reply_v["data"]["verification"]["status"] == "complete"


# ---------------------------------------------------------------------------
# X-02  capabilities / max_file_exports through the published grant schema
# ---------------------------------------------------------------------------

def grant(service, **overrides):
    args = {"target_prefix": "safe_", "verbs": ["gis_buffer"], "max_writes": 4,
            "ttl_seconds": 600, "confirm": True, "request_id": "x-grant",
            **overrides}
    return service.call("authorization_grant", args, session=SESSION)


def test_grant_schema_accepts_capabilities_and_max_file_exports(service):
    reply = grant(service, capabilities=["dataset", "file_export"],
                  max_file_exports=2, request_id="x-grant-caps")
    assert reply["ok"], reply
    auth = reply["data"]["authorization"]
    assert auth["capabilities"] == ["dataset", "file_export"]
    assert auth["max_file_exports"] == 2


def test_grant_schema_still_rejects_unknown_capability(service):
    reply = grant(service, capabilities=["dataset", "teleport"],
                  request_id="x-grant-bad")
    assert reply["ok"] is False
    assert reply["error"]["code"] == "INVALID_ARGUMENT"


def test_file_export_within_granted_cap_executes(rt, service):
    task = buffer_task(rt, exports=[{"step": 1, "name": "safe_buf.geojson",
                                     "format": "geojson"}])
    g = grant(service, capabilities=["dataset", "file_export"],
              max_file_exports=1, request_id="x-grant-ok")
    gid = g["data"]["authorization"]["id"]
    reply = service.call("task_execute", {"task_id": task["id"],
                                          "plan_digest": task["plan_digest"],
                                          "authorization_id": gid}, session=SESSION)
    assert reply["ok"] is True, reply
    assert (rt.store.path.parent / "artifacts" / "safe_buf.geojson").exists()
    assert rt.store.task(task["id"])["task_state"] == "COMMITTED"


def test_file_export_beyond_cap_refused_before_any_write(rt, service):
    task = buffer_task(rt, exports=[{"step": 1, "name": "a.geojson", "format": "geojson"},
                                    {"step": 1, "name": "b.geojson", "format": "geojson"}])
    g = grant(service, capabilities=["dataset", "file_export"],
              max_file_exports=1, request_id="x-grant-cap")
    gid = g["data"]["authorization"]["id"]
    reply = service.call("task_execute", {"task_id": task["id"],
                                          "plan_digest": task["plan_digest"],
                                          "authorization_id": gid}, session=SESSION)
    assert reply["ok"] is False
    assert reply["error"]["code"] == "AUTHORIZATION_SCOPE"
    artifacts = rt.store.path.parent / "artifacts"
    assert not artifacts.exists() or not list(artifacts.glob("*.geojson"))
    stored = rt.store.task(task["id"])
    assert stored["task_state"] == "DECLARED" and stored["actual_writes"] == []
