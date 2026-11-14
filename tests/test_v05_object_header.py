"""T2: object header v0.1 normative slots and 40-verb consistency assertion.

Coverage is explicit: every entry in operations.SPECS is mapped to exactly one
of the engine's two header constructors and validated, with per-verb evidence
kept in a report dict so a skipped verb cannot masquerade as a pass.
"""
import pytest

from definitio.agent import AgentService
from definitio.guidance import FAMILIES
from definitio.model import crs_info, now
from definitio.operations import SPECS
from definitio.runtime import Runtime
from definitio.semantics import (
    OBJECT_HEADER_SLOTS, OBJECT_HEADER_SPEC, object_header, result_header,
    validate_object_header,
)

RASTER_VERBS = set(FAMILIES["raster"][1])
POINTCLOUD_VERBS = set(FAMILIES["pointcloud"][1])
MAP_VERBS = set(FAMILIES["map"][1])
SERVICE_VERBS = set(FAMILIES["services"][1])
PROCESSING_VERBS = {"gis_run_gp", "gis_run_processing"}


@pytest.fixture
def service(tmp_path):
    return AgentService(Runtime(tmp_path / "header.sqlite3"))


def fixture_geojson():
    return {"type": "FeatureCollection", "features": [
        {"type": "Feature",
         "geometry": {"type": "Polygon", "coordinates": [[[0, 0], [1, 0], [1, 1], [0, 1], [0, 0]]]},
         "properties": {"g": "A"}}]}


def imported(service):
    reply = service.call("dataset_import", {
        "name": "header_src", "geojson": fixture_geojson(), "crs": "EPSG:32649",
        "confirm": True, "request_id": "header-import", "source_kind": "synthetic_fixture"})
    assert reply["ok"], reply
    return reply["data"]["dataset"]["id"]


def base_document():
    return {"id": "ds_v05", "name": "object", "fingerprint": "fp_v05", "created_at": now(),
            "crs": crs_info("EPSG:32649"), "fields": [{"name": "g", "type": "string"}],
            "feature_count": 1, "geometry_types": ["Polygon"], "bounds": [0, 0, 1, 1]}


def document_for(kind):
    doc = base_document()
    if kind == "raster":
        doc.update(kind="raster", grid={"width": 4, "height": 4, "dtype": "float32"})
    elif kind == "pointcloud":
        doc.update(kind="pointcloud", point_count=128, point_dimensions=["X", "Y", "Z"])
    elif kind == "resource":
        doc.update(kind="map", resource={"layers": []}, bounds=[0, 0, 1, 1])
    return doc


def kind_for(verb, spec):
    if verb in RASTER_VERBS:
        return "raster"
    if verb in POINTCLOUD_VERBS:
        return "pointcloud"
    if verb in MAP_VERBS:
        return "resource"
    if spec[2] == "R0" or verb in SERVICE_VERBS or verb in PROCESSING_VERBS:
        return "analysis"
    return "vector"


def header_for(verb, spec, kind):
    doc = document_for(kind)
    if kind == "analysis":
        return result_header(verb=verb, vid=spec[0], parameters={}, result={"ok": True},
                             inputs=[doc], task_id="task_v05", step_id="step_1",
                             evidence_ref="ev_v05", result_index=0, workspace="workspace_v05")
    return object_header(doc, operation={"verb": verb, "parameters": {}}, inputs=[doc])


def test_object_header_v01_covers_all_40_verbs_without_blanks():
    assert len(SPECS) == 40
    report = {}
    for verb, spec in SPECS.items():
        kind = kind_for(verb, spec)
        header = header_for(verb, spec, kind)
        verdict = validate_object_header(header)
        report[verb] = {"kind": kind, "object_type": verdict["object_type"],
                        "ok": verdict["ok"], "missing": verdict["missing"],
                        "slots": verdict["present"]}
    assert len(report) == 40
    assert all(item["ok"] for item in report.values()), [v for v, i in report.items() if not i["ok"]]
    assert all(set(OBJECT_HEADER_SLOTS) == set(item["slots"]) for item in report.values())
    assert len({item["kind"] for item in report.values()}) >= 4
    # Explicit kind distribution: reviewable, not a blanket loop.
    counts = {}
    for item in report.values():
        counts[item["kind"]] = counts.get(item["kind"], 0) + 1
    assert sum(counts.values()) == 40
    assert counts.get("vector", 0) and counts.get("analysis", 0)
    assert counts.get("raster", 0) and counts.get("pointcloud", 0)


def test_object_header_v01_names_each_missing_slot():
    header = object_header(base_document())
    assert validate_object_header(header)["spec"] == OBJECT_HEADER_SPEC
    assert validate_object_header(header)["ok"]
    cases = {
        "crs": lambda h: h.pop("crs"),
        "extent": lambda h: h["structure"].pop("bounds"),
        "field_summary": lambda h: h["structure"].pop("fields"),
        "version": lambda h: h["identity"].pop("version"),
    }
    for slot, remove in cases.items():
        import copy
        broken = copy.deepcopy(header)
        remove(broken)
        verdict = validate_object_header(broken)
        assert not verdict["ok"]
        assert slot in verdict["missing"], (slot, verdict)
        if slot == "extent":
            # feature_count/geometry_types remain, but neither is an extent slot.
            assert "field_summary" not in verdict["missing"]
    assert validate_object_header(None)["missing"] == list(OBJECT_HEADER_SLOTS)


def test_object_header_v01_holds_on_real_runtime_outputs(service):
    source = imported(service)
    copied = service.call("gis_copy_dataset", {
        "source": source, "target": "header_copy", "confirm": True, "request_id": "header-copy"})
    assert copied["ok"], copied
    dataset_id = copied["data"]["outputs"][0]["id"]
    dataset_header = service.store.dataset(dataset_id)["semantic"]
    assert validate_object_header(dataset_header)["ok"], validate_object_header(dataset_header)

    measured = service.call("gis_measure", {
        "operation": "area", "a_wkt": "POLYGON ((0 0,2 0,2 3,0 3,0 0))", "crs": "EPSG:32649"})
    assert measured["ok"], measured
    task = measured["data"].get("task") or service.store.tasks()[0]
    evidence = service.store.evidence(task["evidence_refs"][0])
    result_header_value = evidence["semantic"]
    verdict = validate_object_header(result_header_value)
    assert verdict["ok"], verdict
    assert verdict["object_type"] == "analysis_result"
    assert verdict["present"]["extent"] == "structure.result_keys"
