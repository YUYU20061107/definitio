"""Contract, governance and native readback checks for the final eight verbs."""
import base64
import copy
import hashlib
import json
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import pytest
from fastapi.testclient import TestClient

from definitio.agent import AgentService
from definitio.completion import ENTITIES, worker
from definitio.model import GISFault, digest
from definitio.runtime import Runtime
from definitio.server import create_app
from scripts.verify_bundle import verify

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def service(tmp_path, monkeypatch):
    monkeypatch.setenv("DEFINITIO_BACKEND_CONFIG", str(ROOT / "data/backends.json"))
    return AgentService(Runtime(tmp_path / "workspace.sqlite3"))


def source(s, name="source"):
    return s.runtime.import_dataset(name, {"type": "FeatureCollection", "features": [
        {"type": "Feature", "geometry": {"type": "Point", "coordinates": [113 + i, 23 + i]}, "properties": {"key": str(i), "value": i}} for i in range(2)]}, 4326)


def value(s, reply):
    assert reply["ok"], reply
    return s.artifact(reply["data"]["results"][0]["uri"])


def register(s, kind, name, properties):
    reply = s.call("workspace_resource", {"kind": kind, "name": name, "properties": properties, "confirm": True, "request_id": name})
    assert reply["ok"], reply
    return s.store.dataset(reply["data"]["dataset"]["id"])


def catalog(s, d, commercial=False):
    lic = register(s, "license", "license", {"dataset": d["id"], "document": "Self-authored synthetic fixture, licensed for noncommercial validation only.", "commercial_use": commercial, "issuer": "test fixture generator"})
    return register(s, "catalog", "catalog", {"dataset": d["id"], "evidence_ref": lic["id"]})


@pytest.mark.parametrize("tool,code", [("management.Delete", "DESTRUCTIVE_BLOCKED"), ("management.DropTable", "DESTRUCTIVE_BLOCKED"), ("script.Execute", "DEFER_BLOCKED"), ("analysis.Buffer", "NOT_SUPPORTED"), ("unknown", "NOT_SUPPORTED")])
def test_gp_rejects_unregistered_tools_before_task_persistence(service, tool, code):
    d = source(service)
    reply = service.call("gis_run_gp", {"backend": "local-arcpy-10.8", "tool": tool, "args": {"in_rows": d["id"]}, "request_id": "gp", "confirm": True})
    assert not reply["ok"] and reply["error"]["code"] == code, reply
    assert not service.store.tasks() and len(service.store.datasets()) == 1


def test_gp_unknown_parameter_is_rejected_and_requires_confirmation(service):
    d = source(service)
    a = {"backend": "local-arcpy-10.8", "tool": "management.GetCount", "args": {"in_rows": d["id"]}, "request_id": "gp"}
    assert service.call("gis_run_gp", a)["error"]["code"] == "CONFIRM_REQUIRED"
    a["args"]["where_clause"] = "1=1"
    assert service.call("gis_run_gp", a)["error"]["code"] == "INVALID_ARGUMENT"
    assert not service.store.tasks()


def test_delete_physically_removes_and_replay_returns_destruction_receipt(service):
    d = source(service)
    pf = service.runtime.delete_preflight(d["id"])
    a = {"dataset": d["id"], "target_echo": pf["target_echo"], "preflight_id": pf["preflight_id"], "request_id": "delete", "confirm": True}
    first = service.call("gis_delete_dataset", a)
    assert value(service, first)["destroyed"] is True
    with pytest.raises(GISFault) as caught:
        service.store.dataset(d["id"])
    assert caught.value.code == "NOT_FOUND"
    assert service.call("gis_delete_dataset", a)["data"]["task"]["id"] == first["data"]["task"]["id"]
    bundle = service.runtime.evidence_bundle(first["data"]["task"]["id"])
    assert verify(bundle)["status"] == "PASS" and not bundle["inputs"] and len(bundle["destroyed_inputs"]) == 1
    assert bundle["evidence"][0]["input_unchanged"] is False
    bundle["evidence"][0]["input_fingerprints"][0]["after"] = d["fingerprint"]
    bundle["bundle_sha256"] = digest({k: v for k, v in bundle.items() if k != "bundle_sha256"})
    assert verify(bundle)["status"] == "FAIL"


@pytest.mark.parametrize("change,code", [({"confirm": False}, "CONFIRM_REQUIRED"), ({"target_echo": "other"}, "PREFLIGHT_REQUIRED"), ({"preflight_id": "missing"}, "PREFLIGHT_REQUIRED")])
def test_delete_invalid_authorization_preserves_input(service, change, code):
    d = source(service)
    pf = service.runtime.delete_preflight(d["id"])
    a = {"dataset": d["id"], "target_echo": pf["target_echo"], "preflight_id": pf["preflight_id"], "request_id": "delete", "confirm": True, **change}
    reply = service.call("gis_delete_dataset", a)
    assert not reply["ok"] and reply["error"]["code"] == code, reply
    assert service.store.verify_snapshot(service.store.dataset(d["id"])) == d["fingerprint"]


def test_delete_dependency_created_after_preflight_blocks_cascade(service):
    d = source(service)
    pf = service.runtime.delete_preflight(d["id"])
    other = service.runtime.declare("reader", [{"verb": "gis_validate_geometry", "parameters": {"dataset": d["id"], "repair": False}}])
    reply = service.call("gis_delete_dataset", {"dataset": d["id"], "target_echo": d["id"], "preflight_id": pf["preflight_id"], "request_id": "delete", "confirm": True})
    assert not reply["ok"] and reply["error"]["code"] == "DEPENDENCY_BLOCKED", reply
    assert service.store.dataset(d["id"])
    with pytest.raises(GISFault) as caught:
        service.runtime.delete_preflight(d["id"])
    assert caught.value.code == "DEPENDENCY_BLOCKED"


def test_expired_delete_preflight_preserves_input(service):
    d = source(service)
    pf = service.runtime.delete_preflight(d["id"])
    with service.store.connect() as db:
        db.execute("UPDATE local_preflights SET expires=0 WHERE id=?", (pf["preflight_id"],))
    reply = service.call("gis_delete_dataset", {"dataset": d["id"], "target_echo": d["id"], "preflight_id": pf["preflight_id"], "request_id": "delete", "confirm": True})
    assert reply["error"]["code"] == "PREFLIGHT_REQUIRED" and service.store.dataset(d["id"])


@pytest.mark.parametrize("change,code", [({"x": 1}, "INVALID_ARGUMENT"), ({"z": -1}, "INVALID_ARGUMENT"), ({"geometry_column": "geom;DROP"}, "INVALID_ARGUMENT"), ({"attributes": ["absent"]}, "SCHEMA_MISMATCH"), ({"confirm": True}, "INVALID_ARGUMENT")])
def test_tile_negative_domains_do_not_create_datasets(service, change, code):
    d = source(service)
    a = {"backend": "postgis", "table": d["id"], "z": 0, "x": 0, "y": 0, "geometry_column": "geometry", **change}
    reply = service.call("gis_serve_vector_tile", a)
    assert not reply["ok"] and reply["error"]["code"] == code, reply
    assert len(service.store.datasets()) == 1


def test_tile_binary_http_download_decode_and_expiration(tmp_path, monkeypatch):
    monkeypatch.setenv("DEFINITIO_BACKEND_CONFIG", str(ROOT / "data/backends.json"))
    app = create_app(tmp_path, ui=False)
    runtime = app.state.runtime
    d = source(AgentService(runtime))
    with TestClient(app) as client:
        reply = client.post("/api/agent/call", json={"name": "gis_serve_vector_tile", "arguments": {"backend": "postgis", "table": d["id"], "z": 0, "x": 0, "y": 0, "geometry_column": "geometry", "attributes": ["key"]}}).json()
        result = value(AgentService(runtime), reply)
        raw = client.get(result["download_uri"])
        assert raw.status_code == 200 and raw.headers["content-type"] == "application/vnd.mapbox-vector-tile"
        assert hashlib.sha256(raw.content).hexdigest() == result["sha256"] and b"base64" not in raw.content
        decoded = worker("decode_mvt", {"base64": base64.b64encode(raw.content).decode()})
        assert decoded["feature_count"] == 2 and set(decoded["keys"]) == {"mvt_id", "key"}, decoded
        assert decoded["values"] == ["0", "1"]
        runtime.binary_handles[result["binary_handle"]]["expires"] = 0
        assert client.get(result["download_uri"]).status_code == 404
        assert len(runtime.store.datasets()) == 1


@pytest.mark.parametrize("verb,parameters", [("gis_publish_layer", {"backend": "geoserver", "workspace": "definitio_denied", "style": "point"}), ("gis_add_layer_from_catalog", {"backend": "qgis", "map": "does-not-exist"})])
def test_license_gate_precedes_confirmation_and_native_calls(service, verb, parameters):
    d = source(service)
    cat = catalog(service, d, commercial=False)
    key = "dataset_ref" if verb == "gis_publish_layer" else "catalog_id"
    reply = service.call(verb, {**parameters, key: cat["id"], "purpose": "commercial", "request_id": "denied"})
    assert not reply["ok"] and reply["error"]["code"] == "LICENSE_BLOCKED", reply
    assert not service.store.tasks()


def test_license_cannot_be_rebound_to_other_source(service):
    d = source(service)
    other = source(service, "other")
    cat = catalog(service, d)
    reply = service.call("workspace_resource", {"kind": "catalog", "name": "forged", "properties": {"dataset": other["id"], "evidence_ref": cat["resource"]["evidence_ref"]}, "confirm": True, "request_id": "forged"})
    assert reply["error"]["code"] == "LICENSE_BLOCKED"


@pytest.mark.parametrize("entity", ENTITIES)
def test_actual_sta_all_entity_sets_and_exact_odata_downpush(service, entity):
    result = value(service, service.call("gis_query_observations", {"backend": "istsos", "entity": entity, "top": 1, "skip": 0, "count": True}))
    assert result["http_status"] == 200 and result["entity"] == entity
    assert result["returned_count"] == len(result["data"])
    assert parse_qs(urlsplit(result["request_uri"]).query) == {"$top": ["1"], "$skip": ["0"], "$count": ["true"]}
    assert result["local_filtering"] is False and result["local_page_concatenation"] is False


@pytest.mark.parametrize("change", [{"entity": "Unknown"}, {"top": 101}, {"count": "true"}, {"confirm": True}, {"entity": "Sensors", "thing_id": 1}])
def test_sta_invalid_domains_rejected(service, change):
    reply = service.call("gis_query_observations", {"backend": "istsos", "entity": "Things", **change})
    assert not reply["ok"] and reply["error"]["code"] in ("INVALID_ARGUMENT", "NOT_SUPPORTED"), reply
    assert not service.store.datasets()


def test_actual_services_filter_and_unknown_service_rejection(service):
    result = value(service, service.call("gis_list_services", {"backend": "geoserver", "service": "WMS"}))
    assert result["service_count"] == 1 and result["services"][0]["service"] == "WMS"
    sta = value(service, service.call("gis_list_services", {"backend": "istsos"}))
    assert {e["name"] for e in sta["services"][0]["entities"]} == set(ENTITIES)
    assert service.call("gis_list_services", {"backend": "geoserver", "service": "invented"})["error"]["code"] == "INVALID_ARGUMENT"


def test_map_export_path_layout_overwrite_and_dryrun_are_bounded(service):
    mapdoc = register(service, "map", "empty-map", {"crs": "EPSG:4326", "extent": [112, 22, 114, 24], "width": 128, "height": 128, "purpose": "noncommercial"})
    base = {"backend": "qgis", "map": mapdoc["id"], "target_path": "map.png", "format": "png", "dpi": 96, "confirm": True}
    for n, change in enumerate(({"target_path": "../escape.png"}, {"layout": "missing"}, {"dpi": 0}, {"format": "pdf"})):
        reply = service.call("gis_export_map_image", {**base, **change, "request_id": "bad-" + str(n)})
        assert not reply["ok"], reply
    first = service.call("gis_export_map_image", {**base, "request_id": "image"})
    assert first["ok"], first
    repeat = service.call("gis_export_map_image", {**base, "request_id": "image-other"})
    assert repeat["error"]["code"] == "ALREADY_EXISTS"
    counts = [len(service.store.datasets()), len(service.store.tasks())]
    preview = service.call("task_dry_run", {"goal": "preview", "steps": [{"verb": "gis_export_map_image", "parameters": {**base, "target_path": "preview.png"}}]})
    assert preview["ok"] and counts == [len(service.store.datasets()), len(service.store.tasks())], preview


def test_changed_execution_source_blocks_old_plan_but_keeps_committed_receipt(service):
    d = source(service)
    steps = [{"verb": "gis_copy_dataset", "parameters": {"source": d["id"], "target": "copy"}}]
    committed = service.runtime.declare("copy", steps)
    result = service.runtime.execute(committed["id"], committed["plan_digest"], True)
    steps[0]["parameters"]["target"] = "pending"
    pending = service.runtime.declare("pending", steps)
    service.runtime.source_digest = "changed-execution-code"
    assert service.runtime.execute(result["id"], result["plan_digest"], True)["output_refs"] == result["output_refs"]
    with pytest.raises(GISFault) as caught:
        service.runtime.execute(pending["id"], pending["plan_digest"], True)
    assert caught.value.code == "TARGET_STATE_UNKNOWN" and len(service.store.datasets()) == 2
