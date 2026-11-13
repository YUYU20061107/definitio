"""R2: per-verb postcondition predicates for the vector family.

Every verb gets a happy-path check-id assertion and, where a same-kernel or
analytic bound exists, a defect-injection test proving the check names the fault.
"""
import base64
import json

import pytest
from shapely.geometry import box, mapping

from definitio.runtime import Runtime
from definitio.model import GISFault


@pytest.fixture
def runtime(tmp_path):
    return Runtime(tmp_path / "workspace.sqlite3")


def polygons(runtime, name="src", crs="EPSG:32649"):
    return runtime.import_dataset(name, {"type": "FeatureCollection", "features": [
        {"type": "Feature", "geometry": mapping(box(500000, 2540000, 500100, 2540100)),
         "properties": {"g": "A", "population": 100}},
        {"type": "Feature", "geometry": mapping(box(500200, 2540200, 500300, 2540300)),
         "properties": {"g": "B", "population": None}}]}, crs)


def run_verb(runtime, verb, parameters, confirm=True):
    task = runtime.declare(verb, [{"verb": verb, "parameters": parameters}])
    return runtime.execute(task["id"], task["plan_digest"], confirm)


def checks_of(runtime, task, step=1):
    ev = runtime.store.evidence(task["steps"][step - 1]["evidence_ref"])
    found = next(f for f in ev["verification"]["found"] if f["predicate"] == "operation_readback_invariants")
    return {c["id"]: c["passed"] for c in found["details"]["checks"]}, found["details"]


def corrupt(runtime, monkeypatch, mutate):
    from definitio.operations import compute as real_compute

    def wrapper(verb, p, inputs):
        return mutate(real_compute(verb, p, inputs), verb, p, inputs)
    monkeypatch.setattr("definitio.runtime.compute", wrapper)


def test_buffer_bounds_validity_and_count_semantics(runtime):
    d = polygons(runtime)
    for dissolve in (False, True):
        task = run_verb(runtime, "gis_buffer", {"input": d["id"], "target": f"b{dissolve}",
                                                "distance": 50, "unit": "m", "dissolve": dissolve})
        assert task["task_state"] == "COMMITTED", task.get("error")
        checks, _ = checks_of(runtime, task)
        assert checks["buffer_geometry_valid"] is True
        assert checks["buffer_area_upper_bound"] is True
        assert checks["buffer_area_lower_bound"] is True
        assert checks["buffer_count_semantics"] is True


def test_buffer_upper_bound_names_an_oversized_output(runtime, monkeypatch):
    d = polygons(runtime)

    def mutate(proposal, verb, p, inputs):
        proposal["geojson"]["features"][0]["geometry"] = mapping(box(0, 0, 100000, 100000))
        return proposal
    corrupt(runtime, monkeypatch, mutate)
    task = run_verb(runtime, "gis_buffer", {"input": d["id"], "target": "bad",
                                            "distance": 50, "unit": "m", "dissolve": False})
    assert task["task_state"] == "FAILED" and task["error"]["code"] == "BACKEND_ERROR"
    assert task["error"]["details"]["check"] == "buffer_area_upper_bound"


def test_negative_buffer_erosion_stays_inside_input(runtime):
    d = polygons(runtime)
    task = run_verb(runtime, "gis_buffer", {"input": d["id"], "target": "erode",
                                            "distance": -10, "unit": "m", "dissolve": False})
    assert task["task_state"] == "COMMITTED", task.get("error")
    checks, _ = checks_of(runtime, task)
    assert checks["buffer_area_lower_bound"] is True


def test_spatial_join_counts_and_left_outer_nulls(runtime):
    left = polygons(runtime)
    right = runtime.import_dataset("zones", {"type": "FeatureCollection", "features": [
        {"type": "Feature", "geometry": mapping(box(500050, 2540050, 500150, 2540150)),
         "properties": {"zone": "Z1"}}]}, "EPSG:32649")
    for summary in ("first", "all"):
        task = run_verb(runtime, "gis_spatial_join", {"input": left["id"], "join": right["id"],
                                                      "target": f"j{summary}", "predicate": "intersects", "summary": summary})
        assert task["task_state"] == "COMMITTED", task.get("error")
        checks, _ = checks_of(runtime, task)
        assert checks["join_count_accounting"] is True
        assert checks["join_proposal_counts"] is True
        if summary == "first":
            assert checks["join_left_outer_nulls"] is True


def test_spatial_join_proposal_count_tamper_is_named(runtime, monkeypatch):
    left = polygons(runtime)
    right = runtime.import_dataset("zones", {"type": "FeatureCollection", "features": [
        {"type": "Feature", "geometry": mapping(box(500050, 2540050, 500150, 2540150)),
         "properties": {"zone": "Z1"}}]}, "EPSG:32649")

    def mutate(proposal, verb, p, inputs):
        proposal["matched_count"] += 1
        return proposal
    corrupt(runtime, monkeypatch, mutate)
    task = run_verb(runtime, "gis_spatial_join", {"input": left["id"], "join": right["id"],
                                                  "target": "bad", "predicate": "intersects", "summary": "first"})
    assert task["task_state"] == "FAILED"
    assert task["error"]["details"]["check"] == "join_proposal_counts"


def test_clip_and_overlay_subset_bounds(runtime):
    d = polygons(runtime)
    boundary = runtime.import_dataset("boundary", {"type": "FeatureCollection", "features": [
        {"type": "Feature", "geometry": mapping(box(500000, 2540000, 500050, 2540050)),
         "properties": {}}]}, "EPSG:32649")
    task = run_verb(runtime, "gis_clip", {"input": d["id"], "clip_by": boundary["id"], "target": "clipped"})
    assert task["task_state"] == "COMMITTED", task.get("error")
    checks, _ = checks_of(runtime, task)
    for key in ("subset_area_bound", "subset_within_input", "attributes_retained", "count_bound"):
        assert checks[key] is True
    for operation in ("intersection", "difference"):
        task = run_verb(runtime, "gis_overlay", {"input": d["id"], "overlay": boundary["id"],
                                                 "target": f"ov{operation}", "operation": operation})
        assert task["task_state"] == "COMMITTED", task.get("error")
        checks, _ = checks_of(runtime, task)
        assert checks["subset_area_bound"] is True and checks["subset_within_input"] is True


def test_clip_subset_bound_names_an_enlarged_output(runtime, monkeypatch):
    d = polygons(runtime)
    boundary = runtime.import_dataset("boundary", {"type": "FeatureCollection", "features": [
        {"type": "Feature", "geometry": mapping(box(500000, 2540000, 500050, 2540050)),
         "properties": {}}]}, "EPSG:32649")

    def mutate(proposal, verb, p, inputs):
        proposal["geojson"]["features"][0]["geometry"] = mapping(box(499000, 2539000, 501000, 2541000))
        return proposal
    corrupt(runtime, monkeypatch, mutate)
    task = run_verb(runtime, "gis_clip", {"input": d["id"], "clip_by": boundary["id"], "target": "bad"})
    assert task["task_state"] == "FAILED"
    assert task["error"]["details"]["check"] in ("subset_area_bound", "subset_within_input")


def test_reproject_roundtrip_and_bounds(runtime):
    d = runtime.import_dataset("geo", {"type": "FeatureCollection", "features": [
        {"type": "Feature", "geometry": mapping(box(113.0, 23.0, 113.1, 23.1)), "properties": {"g": "A"}},
        {"type": "Feature", "geometry": mapping(box(113.2, 23.2, 113.3, 23.3)), "properties": {"g": "B"}}]},
        "EPSG:4326")
    task = run_verb(runtime, "gis_reproject", {"input": d["id"], "target": "utm", "target_crs": "EPSG:32649"})
    assert task["task_state"] == "COMMITTED", task.get("error")
    checks, _ = checks_of(runtime, task)
    assert checks["reproject_roundtrip_spot"] is True
    assert checks["reproject_bounds_consistency"] is True
    assert checks["reproject_count_unchanged"] is True
    assert checks["reproject_attributes_unchanged"] is True


def test_reproject_roundtrip_names_shifted_coordinates(runtime, monkeypatch):
    d = runtime.import_dataset("geo", {"type": "FeatureCollection", "features": [
        {"type": "Feature", "geometry": mapping(box(113.0, 23.0, 113.1, 23.1)), "properties": {"g": "A"}}]},
        "EPSG:4326")

    def mutate(proposal, verb, p, inputs):
        geom = proposal["geojson"]["features"][0]["geometry"]
        geom["coordinates"] = [[[c[0] + 100.0, c[1] + 100.0] for c in ring] for ring in geom["coordinates"]]
        return proposal
    corrupt(runtime, monkeypatch, mutate)
    task = run_verb(runtime, "gis_reproject", {"input": d["id"], "target": "bad", "target_crs": "EPSG:32649"})
    assert task["task_state"] == "FAILED"
    assert task["error"]["details"]["check"] in ("reproject_roundtrip_spot", "reproject_bounds_consistency")


def test_merge_multiset_fingerprint(runtime):
    a = polygons(runtime, "a")
    b = polygons(runtime, "b")
    task = run_verb(runtime, "gis_merge_datasets", {"inputs": [a["id"], b["id"]], "target": "merged"})
    assert task["task_state"] == "COMMITTED", task.get("error")
    checks, _ = checks_of(runtime, task)
    assert checks["merge_count_readback"] is True and checks["merge_fingerprint_multiset"] is True


def test_merge_multiset_names_a_dropped_record(runtime, monkeypatch):
    a = polygons(runtime, "a")
    b = polygons(runtime, "b")

    def mutate(proposal, verb, p, inputs):
        proposal["geojson"]["features"].pop()
        return proposal
    corrupt(runtime, monkeypatch, mutate)
    task = run_verb(runtime, "gis_merge_datasets", {"inputs": [a["id"], b["id"]], "target": "bad"})
    assert task["task_state"] == "FAILED"
    assert task["error"]["details"]["check"] in ("merge_count_readback", "merge_fingerprint_multiset")


def test_append_version_accounting(runtime):
    a = polygons(runtime, "a")
    b = polygons(runtime, "b")
    task = run_verb(runtime, "gis_append_dataset", {"source": a["id"], "target": b["id"]})
    assert task["task_state"] == "COMMITTED", task.get("error")
    checks, _ = checks_of(runtime, task)
    assert checks["append_version_accounting"] is True
    assert checks["append_count_readback"] is True and checks["append_multiset_readback"] is True


def test_assign_projection_keeps_coordinates(runtime):
    d = polygons(runtime)
    task = run_verb(runtime, "gis_assign_projection", {"dataset": d["id"], "target": "relabeled", "crs": "EPSG:3857"})
    assert task["task_state"] == "COMMITTED", task.get("error")
    checks, _ = checks_of(runtime, task)
    assert checks["assign_projection_coordinates_unchanged"] is True


def test_assign_projection_names_moved_coordinates(runtime, monkeypatch):
    d = polygons(runtime)

    def mutate(proposal, verb, p, inputs):
        proposal["geojson"]["features"][0]["geometry"]["coordinates"][0][0][0] += 1
        return proposal
    corrupt(runtime, monkeypatch, mutate)
    task = run_verb(runtime, "gis_assign_projection", {"dataset": d["id"], "target": "bad", "crs": "EPSG:3857"})
    assert task["task_state"] == "FAILED"
    assert task["error"]["details"]["check"] == "assign_projection_coordinates_unchanged"


def test_create_empty_contract(runtime):
    task = run_verb(runtime, "gis_create_dataset", {"target": "empty", "crs": "EPSG:32649",
                                                    "fields": [{"name": "x", "type": "integer", "nullable": True}]})
    assert task["task_state"] == "COMMITTED", task.get("error")
    checks, _ = checks_of(runtime, task)
    assert checks["create_empty_contract"] is True


def import_uri(document):
    return "data:application/geo+json;base64," + base64.b64encode(json.dumps(document).encode()).decode()


def licensed_doc():
    return {"type": "FeatureCollection", "features": [
        {"type": "Feature", "geometry": mapping(box(1, 1, 2, 2)), "properties": {"v": 7}}],
        "definitio": {"crs": "EPSG:32649", "schema": [{"name": "v", "type": "integer", "nullable": False}],
                      "source_license": {"allow_import": True, "evidence_ref": "ev-1"}}}


def test_import_binds_source_document_and_license(runtime):
    task = run_verb(runtime, "gis_import_data", {"source_uri": import_uri(licensed_doc()),
                                                 "target_name": "imported", "format": "geojson", "kind": "vector"})
    assert task["task_state"] == "COMMITTED", task.get("error")
    checks, _ = checks_of(runtime, task)
    assert checks["import_source_fingerprint_binding"] is True
    assert checks["import_license_attestation"] is True
    assert checks["import_content_fingerprint"] is True


def test_import_source_fingerprint_tamper_is_named(runtime, monkeypatch):
    def mutate(proposal, verb, p, inputs):
        proposal["source_fingerprint"] = "0" * 64
        return proposal
    corrupt(runtime, monkeypatch, mutate)
    task = run_verb(runtime, "gis_import_data", {"source_uri": import_uri(licensed_doc()),
                                                 "target_name": "bad", "format": "geojson", "kind": "vector"})
    assert task["task_state"] == "FAILED"
    assert task["error"]["details"]["check"] == "import_source_fingerprint_binding"


def test_add_field_defaults_and_schema(runtime):
    d = polygons(runtime)
    task = run_verb(runtime, "gis_add_field", {"dataset": d["id"], "target": "added",
                                               "field": {"name": "note", "type": "string", "nullable": True, "default": "n/a"}})
    assert task["task_state"] == "COMMITTED", task.get("error")
    checks, _ = checks_of(runtime, task)
    for key in ("add_field_default_values", "add_field_schema_append", "add_field_count_unchanged", "add_field_other_fields_unchanged"):
        assert checks[key] is True


def test_add_field_wrong_default_is_named(runtime, monkeypatch):
    d = polygons(runtime)

    def mutate(proposal, verb, p, inputs):
        proposal["geojson"]["features"][0]["properties"]["note"] = "wrong"
        return proposal
    corrupt(runtime, monkeypatch, mutate)
    task = run_verb(runtime, "gis_add_field", {"dataset": d["id"], "target": "bad",
                                               "field": {"name": "note", "type": "string", "nullable": True, "default": "n/a"}})
    assert task["task_state"] == "FAILED"
    assert task["error"]["details"]["check"] == "add_field_default_values"


def numeric(runtime, name="num"):
    return runtime.import_dataset(name, {"type": "FeatureCollection", "features": [
        {"type": "Feature", "geometry": mapping(box(500000, 2540000, 500100, 2540100)),
         "properties": {"population": 100}},
        {"type": "Feature", "geometry": mapping(box(500200, 2540200, 500300, 2540300)),
         "properties": {"population": 50}}]}, "EPSG:32649")


def test_calculate_field_recompute(runtime):
    d = numeric(runtime)
    task = run_verb(runtime, "gis_calculate_field", {"dataset": d["id"], "target": "calc",
                                                     "field": "population", "expression": "population * 2"})
    assert task["task_state"] == "COMMITTED", task.get("error")
    checks, _ = checks_of(runtime, task)
    assert checks["calculate_field_recompute"] is True
    out = runtime.store.dataset(task["output_refs"][0])
    assert out["geojson"]["features"][0]["properties"]["population"] == 200


def test_calculate_field_wrong_value_is_named(runtime, monkeypatch):
    d = numeric(runtime)

    def mutate(proposal, verb, p, inputs):
        proposal["geojson"]["features"][0]["properties"]["population"] = 999
        return proposal
    corrupt(runtime, monkeypatch, mutate)
    task = run_verb(runtime, "gis_calculate_field", {"dataset": d["id"], "target": "bad",
                                                     "field": "population", "expression": "population * 2"})
    assert task["task_state"] == "FAILED"
    assert task["error"]["details"]["check"] == "calculate_field_recompute"


def test_delete_field_keeps_remaining_values(runtime):
    d = polygons(runtime)
    pf = runtime.field_preflight(d["id"], ["population"], "trimmed")
    task = run_verb(runtime, "gis_delete_field", {"dataset": d["id"], "target": "trimmed",
                                                  "fields": ["population"], "target_echo": "trimmed",
                                                  "preflight_id": pf["preflight_id"]})
    assert task["task_state"] == "COMMITTED", task.get("error")
    checks, _ = checks_of(runtime, task)
    assert checks["delete_field_values_unchanged"] is True
    assert checks["removed_fields_absent"] is True


def test_spatial_filter_subset_and_predicate(runtime):
    d = polygons(runtime)
    task = run_verb(runtime, "gis_spatial_filter", {"table": d["id"], "predicate": "intersects",
                                                    "reference_wkt": "POLYGON ((500000 2540000, 500100 2540000, 500100 2540100, 500000 2540100, 500000 2540000))"})
    assert task["task_state"] == "COMMITTED", task.get("error")
    checks, _ = checks_of(runtime, task)
    assert checks["filter_count_matches"] is True
    assert checks["filter_subset_records"] is True
    assert checks["filter_predicate_recheck"] is True


def test_measure_recompute_and_unit(runtime):
    task = run_verb(runtime, "gis_measure", {"operation": "area",
                                             "a_wkt": "POLYGON ((0 0, 10 0, 10 10, 0 10, 0 0))", "crs": "EPSG:32649"}, confirm=False)
    assert task["task_state"] == "COMMITTED", task.get("error")
    checks, _ = checks_of(runtime, task)
    assert checks["measure_recompute"] is True and checks["measure_unit_label"] is True
    assert task["steps"][0]["result"]["value"] == 100


def test_validate_geometry_recheck_including_invalid(runtime):
    d = runtime.import_dataset("bowtie", {"type": "FeatureCollection", "features": [
        {"type": "Feature", "geometry": {"type": "Polygon", "coordinates": [[[0, 0], [2, 2], [2, 0], [0, 2], [0, 0]]]},
         "properties": {}}]}, "EPSG:32649")
    task = run_verb(runtime, "gis_validate_geometry", {"dataset": d["id"], "repair": False}, confirm=False)
    assert task["task_state"] == "COMMITTED", task.get("error")
    checks, _ = checks_of(runtime, task)
    assert checks["validate_recheck"] is True and checks["validate_count"] is True
    assert checks["validate_flag_consistency"] is True
    assert task["steps"][0]["result"]["valid"] is False


def test_field_statistics_recompute_with_nulls(runtime):
    d = polygons(runtime)
    task = run_verb(runtime, "gis_field_statistics", {"dataset": d["id"], "fields": ["population"],
                                                      "group_by": [], "statistics": ["count", "sum", "min", "max", "mean"],
                                                      "output": "result", "null_policy": "exclude"}, confirm=False)
    assert task["task_state"] == "COMMITTED", task.get("error")
    checks, _ = checks_of(runtime, task)
    for key in ("stats_recompute", "stats_group_set", "stats_input_count", "stats_null_accounting"):
        assert checks[key] is True


def test_field_statistics_wrong_sum_is_named(runtime, monkeypatch):
    d = polygons(runtime)

    def mutate(proposal, verb, p, inputs):
        proposal["result"]["groups"][0]["fields"]["population"]["sum"] += 1
        return proposal
    corrupt(runtime, monkeypatch, mutate)
    task = run_verb(runtime, "gis_field_statistics", {"dataset": d["id"], "fields": ["population"],
                                                      "group_by": [], "statistics": ["count", "sum"],
                                                      "output": "result", "null_policy": "exclude"}, confirm=False)
    assert task["task_state"] == "FAILED"
    assert task["error"]["details"]["check"] == "stats_recompute"


def test_capability_queries_are_basic_only_with_reason(runtime):
    task = run_verb(runtime, "gis_list_algorithms", {}, confirm=False)
    assert task["task_state"] == "COMMITTED", task.get("error")
    checks, details = checks_of(runtime, task)
    assert checks == {}
    task = run_verb(runtime, "gis_describe_algorithm", {"algorithm": "gis_buffer"}, confirm=False)
    assert task["task_state"] == "COMMITTED", task.get("error")
    checks, _ = checks_of(runtime, task)
    assert checks == {}


def test_verify_path_reruns_output_semantic_checks(runtime):
    from definitio.agent import AgentService
    d = polygons(runtime)
    service = AgentService(runtime)
    task = run_verb(runtime, "gis_buffer", {"input": d["id"], "target": "b",
                                            "distance": 50, "unit": "m", "dissolve": False})
    assert task["task_state"] == "COMMITTED", task.get("error")
    reply = service.call("task_get", {"task_id": task["id"], "verify": True})
    assert reply["ok"], reply
    step = reply["data"]["verification"]["steps"][0]
    found = next(f for f in step["found"] if f["predicate"] == "operation_readback_invariants")
    ids = {c["id"] for c in found["details"]["checks"]}
    assert {"buffer_geometry_valid", "buffer_area_upper_bound", "buffer_area_lower_bound", "buffer_count_semantics"} <= ids