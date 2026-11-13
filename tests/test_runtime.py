"""Analytic fixtures and adversarial control-plane checks; not G1 certification."""
import copy
import io
import json
import threading
import zipfile
from concurrent.futures import ThreadPoolExecutor

import pytest
from fastapi.testclient import TestClient
from shapely.geometry import MultiPolygon, box, mapping, shape

from definitio.model import GISFault, digest, logical_fingerprint, validate_geojson
from definitio.runtime import Runtime
from definitio.server import create_app
from scripts.verify_bundle import verify


def gj(rows):
    return {"type": "FeatureCollection", "features": [
        {"type": "Feature", "geometry": mapping(g) if g is not None else None, "properties": p} for g, p in rows]}


@pytest.fixture
def runtime(tmp_path):
    return Runtime(tmp_path / "workspace.sqlite3")


@pytest.fixture
def source(runtime):
    return runtime.import_dataset("source", gj([
        (box(0,0,1,1), {"group_code":"A"}), (box(1,0,2,1), {"group_code":"A"}),
        (box(4,0,5,1), {"group_code":"A"}), (box(0,0,1,1), {"group_code":"B"}),
        (box(10,0,11,1), {"group_code":None})]), "EPSG:32649")


def run(runtime, verb, parameters, confirm=True):
    task = runtime.declare("test", [{"verb":verb,"parameters":parameters}])
    return runtime.execute(task["id"], task["plan_digest"], confirm)


def dissolve(runtime, source, target="dissolved", fields=None):
    return run(runtime, "gis_dissolve", {"input":source["id"],"target":target,"fields":["group_code"] if fields is None else fields})


def test_analytic_dissolve_per_group_not_global_union(runtime, source):
    t = dissolve(runtime, source)
    assert t["task_state"] == "COMMITTED"
    result = runtime.store.dataset(t["output_refs"][0])
    features = {f["properties"]["group_code"]:shape(f["geometry"]) for f in result["geojson"]["features"]}
    assert set(features) == {"A","B",None}
    assert features["A"].geom_type == "MultiPolygon" and len(features["A"].geoms) == 2
    assert features["A"].area == 3 and features["B"].area == 1 and features[None].area == 1
    assert sum(g.area for g in features.values()) == 5
    assert runtime.store.dataset(source["id"])["fingerprint"] == source["fingerprint"]
    evidence = runtime.store.evidence(t["evidence_refs"][0])
    assert evidence["processed_feature_count"] == 5
    assert sorted(record for group in evidence["group_manifest"] for record in group["source_records"]) == [1,2,3,4,5]


def test_unconfirmed_task_has_no_output(runtime, source):
    t=runtime.declare("test",[{"verb":"gis_dissolve","parameters":{"input":source["id"],"target":"out","fields":[]}}])
    with pytest.raises(GISFault,match="必须确认") as caught: runtime.execute(t["id"],t["plan_digest"],False)
    assert caught.value.code == "CONFIRM_REQUIRED"
    assert len(runtime.store.datasets()) == 1
    assert runtime.store.task(t["id"])["task_state"] == "DECLARED"


def test_plan_hash_prevents_parameter_change(runtime,source):
    t=runtime.declare("test",[{"verb":"gis_copy_dataset","parameters":{"source":source["id"],"target":"out"}}])
    with pytest.raises(GISFault): runtime.execute(t["id"],"tampered",True)
    assert len(runtime.store.datasets()) == 1


def test_empty_input_preserves_schema_and_zero_records(runtime):
    source=runtime.import_dataset("empty",gj([]),"EPSG:32649",[{"name":"group_code","type":"string","nullable":True}])
    t=dissolve(runtime,source)
    output=runtime.store.dataset(t["output_refs"][0])
    assert output["feature_count"] == 0 and output["fields"] == source["fields"]
    evidence=runtime.store.evidence(t["evidence_refs"][0])
    assert evidence["result_empty"] is True and evidence["empty_reason"] == "input_empty"


def test_all_empty_group_retained(runtime):
    source=runtime.import_dataset("emptygroup",gj([(MultiPolygon([]),{"group_code":"A"})]),"EPSG:32649")
    t=dissolve(runtime,source)
    output=runtime.store.dataset(t["output_refs"][0])
    assert output["feature_count"] == 1 and output["empty_geometry_count"] == 1
    assert output["geometry_types"] == ["MultiPolygon"]


def test_explicit_empty_fields_forms_one_group(runtime,source):
    t=dissolve(runtime,source,fields=[])
    output=runtime.store.dataset(t["output_refs"][0])
    assert output["feature_count"] == 1 and output["fields"] == []
    assert shape(output["geojson"]["features"][0]["geometry"]).area == 4
    assert runtime.store.evidence(t["evidence_refs"][0])["dropped_fields"] == ["group_code"]


def test_missing_fields_is_not_empty_fields(runtime,source):
    with pytest.raises(GISFault) as caught: run(runtime,"gis_dissolve",{"input":source["id"],"target":"out"})
    assert caught.value.code == "INVALID_ARGUMENT"


def test_null_empty_string_and_literal_null_are_separate(runtime):
    source=runtime.import_dataset("nulls",gj([(box(i,0,i+1,1),{"group_code":v}) for i,v in enumerate([None,"","null"])]),"EPSG:32649")
    t=dissolve(runtime,source)
    assert runtime.store.dataset(t["output_refs"][0])["feature_count"] == 3


def test_duplicate_geometry_not_double_counted(runtime):
    source=runtime.import_dataset("duplicate",gj([(box(0,0,1,1),{"group_code":"A"})]*2),"EPSG:32649")
    t=dissolve(runtime,source)
    assert shape(runtime.store.dataset(t["output_refs"][0])["geojson"]["features"][0]["geometry"]).area == 1


def test_reorder_logical_fingerprint(runtime,source):
    reversed_geojson=copy.deepcopy(source["geojson"])
    reversed_geojson["features"].reverse()
    other=runtime.import_dataset("reverse",reversed_geojson,"EPSG:32649",source["fields"])
    assert source["fingerprint"] == other["fingerprint"]


@pytest.mark.parametrize("crs",[None,"EPSG:0",0,True,"unknown"])
def test_unknown_crs_rejected(crs):
    with pytest.raises(GISFault) as caught: validate_geojson(gj([]),crs)
    assert caught.value.code == "UNKNOWN_CRS"


def test_no_silent_dimension_drop(runtime):
    raw={"type":"FeatureCollection","features":[{"type":"Feature","geometry":{"type":"Point","coordinates":[1,2,3]},"properties":{}}]}
    with pytest.raises(GISFault) as caught: runtime.import_dataset("z",raw,"EPSG:32649")
    assert caught.value.code == "DIMENSION_MISMATCH"


def test_mixed_property_types_rejected(runtime):
    with pytest.raises(GISFault) as caught: runtime.import_dataset("types",gj([(box(0,0,1,1),{"x":0}),(box(1,0,2,1),{"x":False})]),"EPSG:32649")
    assert caught.value.code == "SCHEMA_MISMATCH"


def test_target_collision_never_overwrites(runtime,source):
    with pytest.raises(GISFault) as caught: dissolve(runtime,source,target=source["name"])
    assert caught.value.code == "ALREADY_EXISTS"
    assert runtime.store.dataset(source["id"])["fingerprint"] == source["fingerprint"]


def test_target_occupied_after_plan(runtime,source):
    t=runtime.declare("test",[{"verb":"gis_copy_dataset","parameters":{"source":source["id"],"target":"race"}}])
    occupied=runtime.import_dataset("race",gj([]),"EPSG:32649")
    t=runtime.execute(t["id"],t["plan_digest"],True)
    assert t["task_state"] == "FAILED" and t["error"]["code"] == "ALREADY_EXISTS"
    assert runtime.store.dataset("race")["fingerprint"] == occupied["fingerprint"]


def test_missing_schema_field_fails_without_output(runtime,source):
    t=dissolve(runtime,source,fields=["absent"])
    assert t["task_state"] == "FAILED" and t["error"]["code"] == "SCHEMA_MISMATCH"
    assert len(runtime.store.datasets()) == 1


def test_float_group_scope_explicit(runtime):
    source=runtime.import_dataset("float",gj([(box(0,0,1,1),{"group_code":1.1})]),"EPSG:32649")
    t=dissolve(runtime,source)
    assert t["error"]["code"] == "NOT_SUPPORTED" and not t["output_refs"]


def test_geographic_buffer_is_not_planar_metres(runtime):
    source=runtime.import_dataset("degrees",gj([(box(113,23,113.01,23.01),{})]),"EPSG:4326")
    t=run(runtime,"gis_buffer",{"input":source["id"],"target":"out","distance":10,"unit":"m","dissolve":False})
    assert t["error"]["code"] == "NOT_SUPPORTED" and not t["output_refs"]


def test_buffer_real_geometry_and_no_input_change(runtime,source):
    t=run(runtime,"gis_buffer",{"input":source["id"],"target":"out","distance":1,"unit":"m","dissolve":False})
    output=runtime.store.dataset(t["output_refs"][0])
    assert output["feature_count"] == 5 and output["bounds"][0] == -1
    assert runtime.store.dataset(source["id"])["fingerprint"] == source["fingerprint"]


def test_valid_empty_clip_is_success(runtime,source):
    boundary=runtime.import_dataset("boundary",gj([(box(100,100,110,110),{})]),"EPSG:32649")
    t=run(runtime,"gis_clip",{"input":source["id"],"clip_by":boundary["id"],"target":"clip"})
    assert t["task_state"] == "COMMITTED"
    assert runtime.store.dataset(t["output_refs"][0])["feature_count"] == 0


def test_crs_mismatch_rejected(runtime,source):
    boundary=runtime.import_dataset("boundary",gj([(box(0,0,1,1),{})]),"EPSG:3857")
    t=run(runtime,"gis_clip",{"input":source["id"],"clip_by":boundary["id"],"target":"clip"})
    assert t["error"]["code"] == "CRS_MISMATCH"


def test_reproject_numeric_control_point(runtime):
    source=runtime.import_dataset("origin",{"type":"FeatureCollection","features":[{"type":"Feature","geometry":{"type":"Point","coordinates":[0,0]},"properties":{}}]},"EPSG:4326")
    t=run(runtime,"gis_reproject",{"input":source["id"],"target":"mercator","target_crs":"EPSG:3857"})
    output=runtime.store.dataset(t["output_refs"][0])
    assert output["crs"]["code"] == "EPSG:3857"
    assert output["geojson"]["features"][0]["geometry"]["coordinates"] == [0.0,0.0]


def test_metadata_assignment_keeps_coordinates(runtime,source):
    t=run(runtime,"gis_assign_projection",{"dataset":source["id"],"target":"assigned","crs":"EPSG:3857"})
    output=runtime.store.dataset(t["output_refs"][0])
    assert output["geojson"] == source["geojson"] and output["crs"]["code"] == "EPSG:3857"


def test_merge_schema_not_implicitly_unioned(runtime,source):
    other=runtime.import_dataset("other",gj([(box(0,0,1,1),{"new_field":"x"})]),"EPSG:32649")
    t=run(runtime,"gis_merge_datasets",{"inputs":[source["id"],other["id"]],"target":"merged"})
    assert t["error"]["code"] == "SCHEMA_MISMATCH"


def test_strict_merge_succeeds(runtime,source):
    t=run(runtime,"gis_merge_datasets",{"inputs":[source["id"],source["id"]],"target":"merged"})
    assert runtime.store.dataset(t["output_refs"][0])["feature_count"] == 10


def test_task_chain_and_persistent_readback(runtime,source):
    t=runtime.declare("按 group_code 融合，然后检查几何",dataset=source["id"])
    t=runtime.execute(t["id"],t["plan_digest"],True)
    assert t["task_state"] == "COMMITTED" and len(t["evidence_refs"]) == 2
    reopened=Runtime(runtime.store.path)
    assert reopened.store.task(t["id"])["steps"][1]["result"]["valid"] is True


def test_committed_replay_and_concurrency_do_not_duplicate(runtime,source):
    t=runtime.declare("融合全部",dataset=source["id"])
    with ThreadPoolExecutor(max_workers=2) as pool:
        results=list(pool.map(lambda _:runtime.execute(t["id"],t["plan_digest"],True),range(2)))
    assert results[0]["output_refs"] == results[1]["output_refs"]
    assert len(runtime.store.datasets()) == 2


def test_failed_step_blocks_downstream(runtime,source):
    t=runtime.declare("bad chain",[
        {"verb":"gis_dissolve","parameters":{"input":source["id"],"target":"bad","fields":["missing"]}},
        {"verb":"gis_validate_geometry","parameters":{"dataset":"$previous","repair":False}}])
    t=runtime.execute(t["id"],t["plan_digest"],True)
    assert [s["state"] for s in t["steps"]] == ["FAILED","BLOCKED"]
    assert len(runtime.store.datasets()) == 1


def test_partial_workflow_is_not_reported_as_success(runtime,source):
    t=runtime.declare("partial",[
        {"verb":"gis_copy_dataset","parameters":{"source":source["id"],"target":"copy"}},
        {"verb":"gis_dissolve","parameters":{"input":"$previous","target":"bad","fields":["missing"]}}])
    t=runtime.execute(t["id"],t["plan_digest"],True)
    assert t["completion"] == "partial" and t["task_state"] == "FAILED"
    assert len(t["output_refs"]) == 1 and len(runtime.store.datasets()) == 2


def test_compute_fault_rolls_back_without_half_target(runtime,source,monkeypatch):
    def fail(*args): raise RuntimeError("injected kernel failure")
    monkeypatch.setattr("definitio.runtime.compute",fail)
    t=dissolve(runtime,source)
    assert t["error"]["code"] == "BACKEND_ERROR" and not t["output_refs"]
    assert len(runtime.store.datasets()) == 1


def test_interrupted_task_is_isolated_on_restart(runtime,source):
    t=runtime.declare("融合全部",dataset=source["id"])
    t["task_state"]="EXECUTING";t["steps"][0]["state"]="EXECUTING";runtime.store.save_task(t)
    other=Runtime(runtime.store.path)
    assert other.store.task(t["id"])["task_state"] == "ISOLATED"
    with pytest.raises(GISFault) as caught: other.execute(t["id"],t["plan_digest"],True)
    assert caught.value.code == "TARGET_STATE_UNKNOWN"


@pytest.mark.parametrize("expression",["__import__('os').system('echo bad')","height.__class__","[1][0]","2 ** 1000000"])
def test_expression_cannot_execute_code(runtime,expression):
    source=runtime.import_dataset("numeric",gj([(box(0,0,1,1),{"height":2})]),"EPSG:32649")
    t=run(runtime,"gis_calculate_field",{"dataset":source["id"],"target":"calc","field":"height","expression":expression})
    assert t["task_state"] == "FAILED" and not t["output_refs"]


def test_safe_field_calculation(runtime):
    source=runtime.import_dataset("numeric",gj([(box(0,0,1,1),{"height":2})]),"EPSG:32649")
    t=run(runtime,"gis_calculate_field",{"dataset":source["id"],"target":"calc","field":"height","expression":"height * 3 + 1"})
    assert runtime.store.dataset(t["output_refs"][0])["geojson"]["features"][0]["properties"]["height"] == 7


def test_statistics_and_null_count(runtime):
    source=runtime.import_dataset("numeric",gj([(box(i,0,i+1,1),{"value":v}) for i,v in enumerate([1,3,None])]),"EPSG:32649")
    t=run(runtime,"gis_field_statistics",{"dataset":source["id"],"fields":["value"],"group_by":[],"statistics":["sum","mean"],"output":"result","null_policy":"exclude"},False)
    stats=t["steps"][0]["result"]["groups"][0]["fields"]["value"]
    assert stats == {"count":2,"null_count":1,"sum":4,"mean":2}
    assert not t["execution_started"]


def test_geometry_validation_reports_null(runtime):
    source=runtime.import_dataset("nullgeom",gj([(None,{})]),"EPSG:32649")
    t=run(runtime,"gis_validate_geometry",{"dataset":source["id"],"repair":False},False)
    assert t["steps"][0]["result"]["valid"] is False


def test_planner_does_not_guess_group_field(runtime,source):
    with pytest.raises(GISFault): runtime.declare("融合",dataset=source["id"])


def test_unsupported_verb_not_silently_simulated(runtime):
    with pytest.raises(GISFault) as caught: runtime.declare("unsupported",[{"verb":"gis_arbitrary_sql","parameters":{}}])
    assert caught.value.code == "NOT_SUPPORTED"


def test_bundle_roundtrip_and_tamper_detection(runtime,source):
    t=dissolve(runtime,source)
    bundle=runtime.evidence_bundle(t["id"])
    assert verify(json.loads(json.dumps(bundle)))["status"] == "PASS"
    bundle["outputs"][0]["geojson"]["features"][0]["properties"]["group_code"]="forged"
    bundle["bundle_sha256"]=digest({k:v for k,v in bundle.items() if k!="bundle_sha256"})
    assert verify(bundle)["status"] == "FAIL"


@pytest.fixture
def client(tmp_path):
    with TestClient(create_app(tmp_path)) as c: yield c


def test_http_e2e_and_download_crc(client):
    demo=client.post('/api/demo',json={"confirm":True}).json()
    source=demo["dataset_refs"][0]
    task=client.post('/api/tasks/plan',json={"goal":"按 group_code 融合，然后检查几何","dataset":source}).json()
    assert client.post(f'/api/tasks/{task["id"]}/execute',json={"plan_digest":task["plan_digest"]}).status_code == 409
    response=client.post(f'/api/tasks/{task["id"]}/execute',json={"plan_digest":task["plan_digest"],"confirm":True})
    assert response.status_code == 200 and response.json()["task_state"] == "COMMITTED"
    archive=client.get(f'/api/tasks/{task["id"]}/bundle')
    with zipfile.ZipFile(io.BytesIO(archive.content)) as z:
        assert z.testzip() is None
        assert verify(json.loads(z.read('bundle.json')))["status"] == "PASS"
    exported=client.get(f'/api/datasets/{source}/export').json()
    assert exported['definitio']['crs']['code'] == 'EPSG:32649'


def test_cross_site_write_rejected(client):
    assert client.post('/api/demo',json={"confirm":True},headers={"origin":"https://evil.example"}).status_code == 403


def test_dns_rebinding_host_rejected(client):
    assert client.get('/api/datasets',headers={"host":"evil.example"}).status_code == 403


def test_import_requires_explicit_confirm(client):
    assert client.post('/api/datasets',json={"name":"x","geojson":gj([]),"crs":"EPSG:4326"}).status_code == 409


def test_capability_status_does_not_claim_g1_pass(client):
    result=client.get('/api/capabilities').json()
    assert result['total'] == 40 and result['implemented'] == 40
    assert all(v['certification']=='NOT_CERTIFIED' for v in result['verbs'])


def test_create_empty_dataset_with_explicit_schema(runtime):
    fields=[{"name":"name","type":"string","nullable":True}]
    t=run(runtime,"gis_create_dataset",{"target":"new","crs":"EPSG:32649","fields":fields})
    d=runtime.store.dataset(t["output_refs"][0])
    assert d["fields"] == fields and d["feature_count"] == 0


def test_add_field_copies_input(runtime,source):
    t=run(runtime,"gis_add_field",{"dataset":source["id"],"target":"new","field":{"name":"value","type":"integer","nullable":False,"default":7}})
    d=runtime.store.dataset(t["output_refs"][0])
    assert all(f["properties"]["value"]==7 for f in d["geojson"]["features"])
    assert [f["name"] for f in runtime.store.dataset(source["id"])["fields"]] == ["group_code"]


@pytest.mark.parametrize("operation,area",[("intersection",.5),("difference",.5)])
def test_overlay_numeric_area(runtime,operation,area):
    source=runtime.import_dataset("a",gj([(box(0,0,1,1),{"name":"a"})]),"EPSG:32649")
    other=runtime.import_dataset("b",gj([(box(.5,0,2,1),{})]),"EPSG:32649")
    t=run(runtime,"gis_overlay",{"input":source["id"],"overlay":other["id"],"target":"out","operation":operation})
    output=runtime.store.dataset(t["output_refs"][0])
    assert shape(output["geojson"]["features"][0]["geometry"]).area == area
    assert output["geojson"]["features"][0]["properties"] == {"name":"a"}


def test_spatial_filter_is_read_only(runtime,source):
    t=run(runtime,"gis_spatial_filter",{"table":source["id"],"predicate":"intersects","reference_wkt":"POLYGON ((10 0,11 0,11 1,10 1,10 0))"},False)
    assert t["steps"][0]["result"]["feature_count"] == 1
    assert len(runtime.store.datasets()) == 1


def test_measure_analytic_square(runtime):
    t=run(runtime,"gis_measure",{"operation":"area","a_wkt":"POLYGON ((0 0,10 0,10 10,0 10,0 0))","crs":"EPSG:32649"},False)
    assert t["steps"][0]["result"]["value"] == 100 and t["steps"][0]["result"]["unit"] == "m²"


def test_geojson_conversion_copies_snapshot(runtime,source):
    t=run(runtime,"gis_convert_format",{"input":source["id"],"target":"converted","format":"geojson"})
    assert runtime.store.dataset(t["output_refs"][0])["fingerprint"] == source["fingerprint"]


def test_algorithm_queries(runtime):
    t=run(runtime,"gis_list_algorithms",{},False)
    assert t["steps"][0]["result"]["implemented"] == 40
    t=run(runtime,"gis_describe_algorithm",{"algorithm":"gis_dissolve"},False)
    assert t["steps"][0]["result"]["vid"] == "V21"


def test_compact_chinese_planning_without_spaces(runtime,source):
    t=runtime.declare("按group_code融合",dataset=source["id"])
    assert t["steps"][0]["parameters"]["fields"] == ["group_code"]


def test_crs_definition_roundtrip_preserves_wkt_identity():
    from definitio.model import crs_info
    original=crs_info("EPSG:3857")
    assert crs_info(original)["definition_hash"] == original["definition_hash"]


def test_direct_runtime_import_uses_matching_proj_database():
    import subprocess,sys
    result=subprocess.run([sys.executable,"-c","from definitio.runtime import Runtime; from definitio.model import crs_info; print(crs_info('EPSG:32649')['code'])"],capture_output=True,text=True)
    assert result.returncode == 0 and "EPSG:32649" in result.stdout


@pytest.mark.parametrize("mutation",["fake_empty","wrong_group_geometry","missing_manifest_record"])
def test_readback_comparator_rejects_fake_success(runtime,source,monkeypatch,mutation):
    from definitio.operations import compute as real_compute
    def corrupt(verb,p,inputs):
        proposal=real_compute(verb,p,inputs)
        if mutation=="fake_empty": proposal["geojson"]["features"]=[]
        elif mutation=="wrong_group_geometry": proposal["geojson"]["features"][0]["geometry"]=mapping(box(100,100,101,101))
        else: proposal["group_manifest"][0]["source_records"]=[]
        return proposal
    monkeypatch.setattr("definitio.runtime.compute",corrupt)
    t=dissolve(runtime,source)
    assert t["task_state"] == "FAILED" and t["error"]["code"] == "BACKEND_ERROR"
    assert not t["output_refs"] and len(runtime.store.datasets()) == 1
