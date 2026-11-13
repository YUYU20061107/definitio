"""Scope denials, real preview state, structured readback and compact discovery."""
import copy
import json
from concurrent.futures import ThreadPoolExecutor

import pytest

from definitio.agent import AgentService, compact_tools
from definitio.model import canonical, digest
from definitio.runtime import Runtime
from test_agent import fixture_geojson, imported

SESSION = "test-session-secret-" + "a" * 32
OTHER = "test-session-secret-" + "b" * 32


@pytest.fixture
def service(tmp_path):
    return AgentService(Runtime(tmp_path / "workspace.sqlite3"))


def grant(service, maximum=3, **changes):
    a = {"target_prefix": "job_", "verbs": ["dataset_import", "gis_copy_dataset", "gis_add_field", "gis_dissolve"],
         "max_writes": maximum, "ttl_seconds": 600, "confirm": True, "request_id": "grant-1", **changes}
    r = service.call("authorization_grant", a, SESSION)
    assert r["ok"], r
    return r["data"]["authorization"]["id"]


def scoped_copy(source, g, target="job_copy", **changes):
    return {"source": source, "target": target, "authorization_id": g, **changes}


def test_grant_requires_confirmation_and_a_session(service):
    a = {"target_prefix": "job_", "verbs": ["gis_copy_dataset"], "max_writes": 2, "ttl_seconds": 60, "request_id": "g"}
    assert service.call("authorization_grant", a, SESSION)["error"]["code"] == "CONFIRM_REQUIRED"
    a["confirm"] = True
    assert service.call("authorization_grant", a)["error"]["code"] == "AUTHORIZATION_REQUIRED"


def test_scope_session_verb_prefix_and_empty_prefix_denied(service):
    source = imported(service)
    g = grant(service, verbs=["gis_copy_dataset"])
    for a, session, code in [
        (scoped_copy(source, g), OTHER, "AUTHORIZATION_REQUIRED"),
        (scoped_copy(source, g, target="outside"), SESSION, "AUTHORIZATION_SCOPE")]:
        r = service.call("gis_copy_dataset", a, session)
        assert not r["ok"] and r["error"]["code"] == code, r
    r = service.call("gis_dissolve", {"input": source, "target": "job_groups", "fields": [], "authorization_id": g}, SESSION)
    assert r["error"]["code"] == "AUTHORIZATION_SCOPE"
    a = {"target_prefix": "", "verbs": ["gis_copy_dataset"], "max_writes": 1, "ttl_seconds": 60, "confirm": True, "request_id": "bad"}
    assert service.call("authorization_grant", a, SESSION)["error"]["code"] == "INVALID_ARGUMENT"
    assert len(service.store.datasets()) == 1


def test_auto_request_retry_budget_and_revocation(service):
    source = imported(service)
    g = grant(service, maximum=1)
    a = scoped_copy(source, g)
    first = service.call("gis_copy_dataset", a, SESSION)
    again = service.call("gis_copy_dataset", a, SESSION)
    assert first["ok"] and again["data"]["replayed"], (first, again)
    assert first["data"]["request_id"] == again["data"]["request_id"]
    r = service.call("gis_copy_dataset", scoped_copy(source, g, target="job_second"), SESSION)
    assert r["error"]["code"] == "AUTHORIZATION_EXHAUSTED"
    revoked = service.call("authorization_revoke", {"authorization_id": g}, SESSION)
    assert revoked["data"]["authorization"]["remaining"] == 0
    assert not revoked["data"]["authorization"]["active"]
    # Accepted receipts can be recovered after revocation, without new computation.
    assert service.call("gis_copy_dataset", a, SESSION)["data"]["replayed"]
    assert len(service.store.datasets()) == 2


def test_expired_and_revoked_grants_cannot_start_new_writes(service):
    source = imported(service)
    g = grant(service)
    with service.store.connect() as db:
        db.execute("UPDATE agent_grants SET expires=0 WHERE id=?", (g,))
    assert service.call("gis_copy_dataset", scoped_copy(source, g), SESSION)["error"]["code"] == "AUTHORIZATION_REQUIRED"
    g = grant(service, request_id="another")
    service.call("authorization_revoke", {"authorization_id": g}, SESSION)
    assert service.call("gis_copy_dataset", scoped_copy(source, g), SESSION)["error"]["code"] == "AUTHORIZATION_REQUIRED"


def test_parallel_last_quota_is_reserved_once(service):
    source = imported(service)
    g = grant(service, maximum=1)
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda n: service.call("gis_copy_dataset", scoped_copy(source, g, target="job_" + str(n)), SESSION), [1, 2]))
    assert sum(r["ok"] for r in results) == 1
    assert [r["error"]["code"] for r in results if not r["ok"]] == ["AUTHORIZATION_EXHAUSTED"]


def test_plan_reserves_all_writes_and_records_grant(service):
    source = imported(service)
    g = grant(service, maximum=2)
    plan = service.call("task_plan", {"goal": "two copies", "request_id": "p", "steps": [
        {"verb": "gis_copy_dataset", "parameters": {"source": source, "target": "job_one"}},
        {"verb": "gis_copy_dataset", "parameters": {"source": "$previous", "target": "job_two"}}]})["data"]["task"]
    a = {"task_id": plan["id"], "plan_digest": plan["plan_digest"], "authorization_id": g}
    wrong = service.call("task_execute", {**a, "plan_digest": "wrong"}, SESSION)
    assert not wrong["ok"]
    r = service.call("task_execute", a, SESSION)
    assert r["ok"] and len(r["data"]["outputs"]) == 2, r
    assert service.call("task_execute", a, SESSION)["ok"]
    state = service.call("authorization_get", {"authorization_id": g}, SESSION)["data"]["authorization"]
    assert state["used"] == 2
    assert service.store.task(plan["id"])["governance_state"]["authorization_id"] == g


def steps(source, bad=False):
    return [
        {"verb": "gis_add_field", "parameters": {"dataset": source, "target": "job_added",
         "field": {"name": "new_group", "type": "integer", "nullable": False, "default": 2}}},
        {"verb": "gis_dissolve", "parameters": {"input": "$previous", "target": "job_groups", "fields": ["missing" if bad else "new_group"]}},
        {"verb": "gis_validate_geometry", "parameters": {"dataset": "$previous", "repair": False}},
    ]


def test_preview_tracks_real_schema_and_writes_nothing(service):
    source = imported(service)
    before = digest(service.store.dataset(source))
    with service.store.connect() as db:
        counts = [db.execute("SELECT COUNT(*) FROM " + name).fetchone()[0] for name in ("datasets", "tasks", "evidence", "agent_requests", "agent_grant_events")]
    r = service.call("task_dry_run", {"goal": "preview", "steps": steps(source)})
    assert r["ok"], r
    p = r["data"]["preview"]
    assert p["workspace_writes"] == 0 and p["steps"][1]["output"]["feature_count"] == 1
    assert p["steps"][0]["effects"]["added_fields"] == ["new_group"]
    assert p["steps"][1]["effects"]["dropped_fields"] == ["group_code"]
    with service.store.connect() as db:
        assert counts == [db.execute("SELECT COUNT(*) FROM " + name).fetchone()[0] for name in ("datasets", "tasks", "evidence", "agent_requests", "agent_grant_events")]
    assert digest(service.store.dataset(source)) == before


def test_preview_detects_missing_intermediate_fields_before_execution(service):
    source = imported(service)
    r = service.call("task_dry_run", {"goal": "bad preview", "steps": steps(source, True)})
    assert not r["ok"] and r["error"]["code"] == "SCHEMA_MISMATCH", r
    assert [s["state"] for s in r["data"]["preview"]["steps"]] == ["PASS", "FAIL", "BLOCKED"]
    assert len(service.store.datasets()) == 1 and not service.store.tasks()


def test_preview_matches_execution_fields_counts_and_crs(service):
    source = imported(service)
    r = service.call("task_plan", {"goal": "preview then execute", "steps": steps(source), "request_id": "plan"})["data"]
    task = r["task"]
    out = service.call("task_execute", {"task_id": task["id"], "plan_digest": task["plan_digest"], "confirm": True})
    assert out["ok"], out
    for predicted, ref in zip([s["output"] for s in r["preview"]["steps"] if "output" in s], out["data"]["task"]["output_refs"]):
        d = service.store.dataset(ref)
        assert predicted["fields"] == d["fields"] and predicted["feature_count"] == d["feature_count"]
        assert predicted["crs"]["definition_hash"] == d["crs"]["definition_hash"]


def test_workspace_combined_search_and_routing(service):
    imported(service)
    r = service.call("workspace_list", {"name_contains": "SOUR", "fields": ["group_code"], "crs": 32649, "geometry_type": "Polygon"})
    assert r["ok"] and r["data"]["total"] == 1
    assert service.call("workspace_list", {"crs": 4326})["data"]["total"] == 0
    assert service.call("workspace_list", {"fields": ["absent"]})["data"]["total"] == 0
    r = service.call("workflow_route", {"intent": "裁剪然后融合"})
    assert r["data"]["families"][0]["family"] == "geometry"
    v = service.call("gis_list_algorithms", {"family": "geometry", "implemented_only": True})["data"]["verbs"]
    assert {a["name"] for a in v} == {"gis_clip", "gis_overlay", "gis_buffer", "gis_dissolve", "gis_spatial_join", "gis_run_processing"}
    assert "single-use" in " ".join(service.call("gis_describe_algorithm", {"algorithm": "gis_delete_dataset"})["data"]["capability"]["prerequisites"])
    assert len(canonical(compact_tools(service.tools.values()))) < 40000


def test_artifact_summary_cursor_drilldown_and_wrong_uri_denied(service):
    source = imported(service)
    uri = "definitio://datasets/" + source + "/geojson"
    r = service.call("artifact_view", {"uri": uri})
    assert r["ok"] and r["data"]["summary"]["collections"]["features"]["count"] == 5
    a = {"uri": uri, "pointer": "/features", "view": "page", "limit": 2}
    rows = []
    while True:
        r = service.call("artifact_view", a)
        assert r["ok"], r
        rows.extend(r["data"]["items"])
        cursor = r["data"]["cursor"]
        if cursor is None:
            break
        a = {"uri": uri, "cursor": cursor, "limit": 2}
    assert len(rows) == 5 and [f["id"] for f in rows] == [1, 2, 3, 4, 5]
    assert not service.call("artifact_view", {"uri": uri, "cursor": "bad"})["ok"]
    assert not service.call("artifact_view", {"uri": uri, "pointer": "/missing"})["ok"]
    assert not service.call("artifact_view", {"uri": "file:///secret"})["ok"]
