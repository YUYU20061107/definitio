"""W1/W2 semantic controls, immutable history, raster masks and evidence links."""
import base64
import copy
import json

import numpy as np
import pytest
from shapely.geometry import Point, box, mapping

from definitio.agent import AgentService
from definitio.model import GISFault
from definitio.runtime import Runtime
from scripts.verify_bundle import verify


@pytest.fixture
def service(tmp_path):
    return AgentService(Runtime(tmp_path / "completion.sqlite3"))


def vector(service, name="source", rows=None, crs=32649):
    rows = rows if rows is not None else [(box(0, 0, 1, 1), {"key": "A", "value": 2.0})]
    return service.runtime.import_dataset(name, {"type": "FeatureCollection", "features": [
        {"type": "Feature", "geometry": mapping(g), "properties": p} for g, p in rows]}, crs)


def raster(service, name="dem", data=None, **changes):
    data = data if data is not None else [[float(2*c+3*r) for c in range(7)] for r in range(7)]
    spec = {"data": data, "transform": [1, 0, 0, 0, -1, 7], "dtype": "float32", "nodata": None, "band_name": "elevation", **changes}
    result = service.call("raster_import", {"name": name, "raster": spec, "crs": 32649, "confirm": True, "request_id": "import-"+name})
    assert result["ok"], result
    return service.store.dataset(result["data"]["dataset"]["id"])


def call(service, verb, **p):
    return service.call(verb, {"confirm": True, "request_id": "call-"+p.get("target", p.get("new_name", p.get("target_name", verb))), **p})


def output(service, reply):
    assert reply["ok"], reply
    return service.store.dataset(reply["data"]["outputs"][0]["id"])


def test_delete_fields_preflight_binding_reuse_and_source(service):
    src = vector(service)
    pf = service.call("destructive_preflight", {"dataset": src["id"], "fields": ["value"], "target": "stripped"})
    assert pf["ok"], pf
    args = {"dataset": src["id"], "fields": ["value"], "target": "stripped", "target_echo": "stripped", "preflight_id": pf["data"]["preflight"]["preflight_id"]}
    denied = call(service, "gis_delete_field", **{**args, "target": "changed", "target_echo": "changed"})
    assert denied["error"]["code"] == "PREFLIGHT_REQUIRED"
    reply = call(service, "gis_delete_field", **args)
    d = output(service, reply)
    assert [f["name"] for f in d["fields"]] == ["key"]
    assert service.store.dataset(src["id"])["fingerprint"] == src["fingerprint"]
    assert call(service, "gis_delete_field", **args)["data"]["replayed"]
    assert service.call("gis_delete_field", {**args, "request_id": "unconfirmed"})["error"]["code"] == "CONFIRM_REQUIRED"
    assert verify(service.runtime.evidence_bundle(reply["data"]["task"]["id"]))["status"] == "PASS"


def test_rename_changes_head_not_snapshot_and_retries(service):
    src = vector(service)
    reply = call(service, "gis_rename_dataset", dataset=src["id"], new_name="renamed")
    assert reply["ok"], reply
    assert service.store.dataset("renamed")["id"] == src["id"]
    assert service.store.dataset(src["id"])["fingerprint"] == src["fingerprint"]
    with pytest.raises(GISFault): service.store.dataset("source")
    assert call(service, "gis_rename_dataset", dataset=src["id"], new_name="renamed")["data"]["replayed"]
    assert call(service, "gis_rename_dataset", dataset=src["id"], new_name="renamed2")["ok"]
    assert len(service.store.datasets()) == 1


def test_append_versions_preserve_old_target_and_repeat_once(service):
    src, target = vector(service), vector(service, "existing")
    reply = call(service, "gis_append_dataset", source=src["id"], target="existing")
    d = output(service, reply)
    assert d["feature_count"] == 2 and d["id"] != target["id"]
    assert service.store.dataset("existing")["id"] == d["id"]
    assert service.store.dataset(target["id"])["feature_count"] == 1
    assert call(service, "gis_append_dataset", source=src["id"], target="existing")["data"]["replayed"]
    assert service.store.dataset("existing")["feature_count"] == 2
    assert verify(service.runtime.evidence_bundle(reply["data"]["task"]["id"]))["status"] == "PASS"


def test_append_conflicting_plans_do_not_merge_silently(service):
    src, dst = vector(service), vector(service, "existing")
    args = [{"verb": "gis_append_dataset", "parameters": {"source": src["id"], "target": "existing"}}]
    first = service.runtime.declare("one", args)
    second = service.runtime.declare("two", args)
    assert service.runtime.execute(first["id"], first["plan_digest"], True)["task_state"] == "COMMITTED"
    failed = service.runtime.execute(second["id"], second["plan_digest"], True)
    assert failed["task_state"] == "ISOLATED" and failed["error"]["code"] == "TARGET_STATE_UNKNOWN"


def test_append_schema_crs_self_and_missing_target_denied(service):
    src = vector(service)
    mismatch = vector(service, "mismatch", [(Point(0, 0), {"different": 3})])
    assert call(service, "gis_append_dataset", source=src["id"], target="mismatch")["error"]["code"] == "SCHEMA_MISMATCH"
    assert call(service, "gis_append_dataset", source=src["id"], target="source")["error"]["code"] == "INVALID_ARGUMENT"
    assert call(service, "gis_append_dataset", source=src["id"], target="missing")["error"]["code"] == "NOT_FOUND"


def test_import_uri_requires_license_and_retains_zero_null(service):
    src = vector(service)
    doc = {**src["geojson"], "definitio": {"crs": 32649, "schema": src["fields"], "source_license": {"allow_import": True, "evidence_ref": "synthetic-self-authored"}}}
    uri = "data:application/geo+json;base64," + base64.b64encode(json.dumps(doc).encode()).decode()
    d = output(service, call(service, "gis_import_data", source_uri=uri, target_name="imported", format="geojson", kind="vector"))
    assert d["fingerprint"] == src["fingerprint"]
    del doc["definitio"]["source_license"]
    uri = "data:application/geo+json;base64," + base64.b64encode(json.dumps(doc).encode()).decode()
    assert call(service, "gis_import_data", source_uri=uri, target_name="denied", format="geojson", kind="vector")["error"]["code"] == "LICENSE_BLOCKED"


@pytest.mark.parametrize("summary", ["first", "all"])
def test_spatial_join_left_outer_collision_and_multimatch(service, summary):
    src = vector(service, rows=[(box(0, 0, 3, 3), {"key": "A"}), (box(10, 10, 11, 11), {"key": "B"})])
    join = vector(service, "join", [(box(0, 0, 2, 2), {"key": "large"}), (box(0, 0, 1, 1), {"key": "small"})])
    d = output(service, call(service, "gis_spatial_join", input=src["id"], join=join["id"], target="joined", predicate="intersects", summary=summary))
    rows = d["geojson"]["features"]
    assert len(rows) == (2 if summary == "first" else 3)
    assert rows[0]["properties"] == {"key": "A", "join_key": "large"}
    assert rows[-1]["properties"] == {"key": "B", "join_key": None}


def test_spatial_join_unstable_tie_and_crs_denied(service):
    src = vector(service)
    join = vector(service, "join", [(box(0, 0, 1, 1), {"k": "x"}), (box(0, 0, 1, 1), {"k": "y"})])
    assert call(service, "gis_spatial_join", input=src["id"], join=join["id"], target="tie", predicate="intersects", summary="first")["error"]["code"] == "SCHEMA_MISMATCH"
    other = vector(service, "other", crs=4326)
    assert call(service, "gis_spatial_join", input=src["id"], join=other["id"], target="crs", predicate="intersects", summary="all")["error"]["code"] == "CRS_MISMATCH"


@pytest.mark.parametrize("verb,extra", [("gis_slope", {"unit": "degree"}), ("gis_aspect", {"convention": "north_zero_clockwise", "flat_policy": "nodata"}), ("gis_hillshade", {"azimuth": 315, "altitude": 45})])
def test_terrain_analytic_plane_mask_and_bundle(service, verb, extra):
    src = raster(service)
    reply = call(service, verb, input=src["id"], target="terrain", z_factor=1, nodata_policy="propagate", **extra)
    d = output(service, reply)
    data = d["raster"]["data"]
    assert all(v is None for v in data[0] + data[-1])
    assert data[3][3] is not None and data[3][3] == data[2][2]
    if verb == "gis_slope": assert abs(data[3][3] - np.degrees(np.arctan(np.sqrt(13)))) < 1e-5
    if verb == "gis_aspect": assert abs(data[3][3] - (np.degrees(np.arctan2(-2, 3)) % 360)) < 1e-5
    assert service.store.dataset(src["id"])["fingerprint"] == src["fingerprint"]
    bundle = service.runtime.evidence_bundle(reply["data"]["task"]["id"])
    assert verify(bundle)["status"] == "PASS"
    bundle["outputs"][0]["raster"]["data"][3][3] += 1
    assert verify(bundle)["status"] == "FAIL"


def test_raster_calc_nodata_zero_code_execution_and_grid(service):
    src = raster(service, data=[[0, 1, None], [2, 3, 4]])
    d = output(service, call(service, "gis_raster_calc", inputs=[src["id"]], expression="A * 2 + 1", target="calc", nodata_policy="propagate"))
    assert d["raster"]["data"] == [[1, 3, None], [5, 7, 9]]
    for n, expression in enumerate(["__import__('os')", "A / 0", "A[0]", "A ** 3"]):
        r = call(service, "gis_raster_calc", inputs=[src["id"]], expression=expression, target="bad"+str(n), nodata_policy="propagate")
        assert not r["ok"] and r["error"]["code"] == "INVALID_ARGUMENT", r
    other = raster(service, "shifted", data=[[1, 2, 3], [4, 5, 6]], transform=[1, 0, .5, 0, -1, 7])
    assert call(service, "gis_raster_calc", inputs=[src["id"], other["id"]], expression="A+B", target="grid", nodata_policy="propagate")["error"]["code"] == "SCHEMA_MISMATCH"


def test_reclassification_endpoints_overlap_and_unmapped(service):
    src = raster(service, data=[[0, 1, 2, 3, None]])
    rules = [{"min": 0, "max": 2, "min_inclusive": True, "max_inclusive": False, "value": 10}, {"min": 2, "max": 3, "min_inclusive": True, "max_inclusive": True, "value": 20}]
    d = output(service, call(service, "gis_reclassify", input=src["id"], target="classes", rules=rules, unmapped="fail", nodata_policy="propagate"))
    assert d["raster"]["data"] == [[10, 10, 20, 20, None]]
    rules[0]["max_inclusive"] = True
    assert call(service, "gis_reclassify", input=src["id"], target="overlap", rules=rules, unmapped="fail", nodata_policy="propagate")["error"]["code"] == "INVALID_ARGUMENT"


def test_zonal_retains_empty_zone_and_distinguishes_nodata(service):
    values = raster(service, data=[[0, 2], [None, 4]], transform=[1, 0, 0, 0, -1, 2])
    zones = vector(service, "zones", [(box(0, 0, 2, 2), {"zone": "full"}), (box(10, 10, 11, 11), {"zone": "empty"})])
    d = output(service, call(service, "gis_zonal_statistics", zones=zones["id"], values=values["id"], target="stats", statistics=["count", "sum", "mean", "min", "max"], output="table", zone_field="zone", nodata_policy="exclude", pixel_rule="center", weighting="none"))
    rows = [f["properties"] for f in d["geojson"]["features"]]
    assert rows[0] == {"zone": "full", "count": 3, "sum": 6, "mean": 2, "min": 0, "max": 4}
    assert rows[1] == {"zone": "empty", "count": 0, "sum": None, "mean": None, "min": None, "max": None}


def test_flat_nodata_and_all_masked_terrain(service):
    flat = raster(service, data=[[0]*5 for _ in range(5)])
    d = output(service, call(service, "gis_aspect", input=flat["id"], target="flat", z_factor=1, convention="north_zero_clockwise", flat_policy="nodata", nodata_policy="propagate"))
    assert d["grid"]["valid_pixel_count"] == 0
    masked = raster(service, "masked", data=[[None]*5 for _ in range(5)])
    d = output(service, call(service, "gis_slope", input=masked["id"], target="empty", z_factor=1, unit="degree", nodata_policy="propagate"))
    assert d["grid"]["valid_pixel_count"] == 0


def test_raster_preview_chain_and_artifact_are_real_and_readonly(service):
    src = raster(service)
    with service.store.connect() as db:
        before = [db.execute("SELECT count(*) FROM "+t).fetchone()[0] for t in ["datasets", "tasks", "evidence", "agent_requests"]]
    args = {"goal": "calc then slope", "steps": [
        {"verb": "gis_raster_calc", "parameters": {"inputs": [src["id"]], "expression": "A*2", "target": "twice", "nodata_policy": "propagate"}},
        {"verb": "gis_slope", "parameters": {"input": "$previous", "target": "slope", "z_factor": 1, "unit": "degree", "nodata_policy": "propagate"}}]}
    reply = service.call("task_dry_run", args)
    assert reply["ok"] and reply["data"]["preview"]["steps"][1]["output"]["grid"]["valid_pixel_count"] == 25, reply
    with service.store.connect() as db:
        assert before == [db.execute("SELECT count(*) FROM "+t).fetchone()[0] for t in ["datasets", "tasks", "evidence", "agent_requests"]]
    assert service.call("artifact_view", {"uri": f"definitio://datasets/{src['id']}/raster", "pointer": "/data", "view": "page", "limit": 2})["data"]["returned"] == 2


@pytest.mark.parametrize("change", [{"transform": [1, .1, 0, 0, -1, 7]}, {"data": [[1, 2], [3]]}, {"dtype": "uint8", "data": [[256]]}, {"data": [[True]]}])
def test_invalid_raster_import_is_atomic(service, change):
    spec = {"data": [[0]], "transform": [1, 0, 0, 0, -1, 1], "dtype": "float32", "nodata": None, "band_name": "test", **change}
    reply = service.call("raster_import", {"name": "bad", "raster": spec, "crs": 32649, "confirm": True, "request_id": "bad"})
    assert not reply["ok"] and len(service.store.datasets()) == 0


def test_http_geotiff_export_preserves_grid_crs_zero_and_mask(tmp_path):
    from fastapi.testclient import TestClient
    from rasterio.io import MemoryFile
    from definitio.server import create_app
    with TestClient(create_app(tmp_path, ui=False)) as client:
        body = {"name": "raster_import", "arguments": {"name": "grid", "raster": {"data": [[0, None], [2, 4]],
                "transform": [2, 0, 500000, 0, -2, 2500000], "dtype": "float32", "nodata": None,
                "band_name": "elevation"}, "crs": 32649, "confirm": True, "request_id": "raster-http"}}
        reply = client.post("/api/agent/call", json=body).json()
        assert reply["ok"], reply
        ref = reply["data"]["dataset"]["id"]
        download = client.get(f"/api/datasets/{ref}/export")
        assert download.status_code == 200 and download.headers["content-type"] == "image/tiff"
        with MemoryFile(download.content) as memory, memory.open() as ds:
            data = ds.read(1, masked=True)
            assert ds.crs.to_epsg() == 32649 and ds.transform.a == 2
            assert data[0, 0] == 0 and not data.mask[0, 0] and data.mask[0, 1]


def test_non_square_aspect_and_new_target_grants_cannot_mutate_heads(service):
    dem = raster(service, transform=[1, 0, 0, 0, -2, 7])
    denied = call(service, "gis_aspect", input=dem["id"], target="aspect", z_factor=1, convention="north_zero_clockwise", flat_policy="nodata", nodata_policy="propagate")
    assert denied["error"]["code"] == "NOT_SUPPORTED"
    src = vector(service)
    session = "explicit-lifecycle-session-"+"a"*32
    grant = service.call("authorization_grant", {"target_prefix": "job_", "verbs": ["gis_rename_dataset"], "max_writes": 1, "ttl_seconds": 60, "confirm": True, "request_id": "grant"}, session)
    denied = service.call("gis_rename_dataset", {"dataset": src["id"], "new_name": "job_rename", "authorization_id": grant["data"]["authorization"]["id"]}, session)
    assert denied["error"]["code"] == "CONFIRM_REQUIRED"
