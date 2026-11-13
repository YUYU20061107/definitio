"""D1 semantic object headers; axis order is read from the CRS definition, not the EPSG code."""
import pytest
from shapely.geometry import box, mapping

from definitio.model import crs_info, digest
from definitio.semantics import HEADER_FORMAT, axis_order, parse_axis_nodes


def test_axis_order_is_parsed_from_definition_not_epsg_number():
    geographic = crs_info("EPSG:4326")
    projected = crs_info("EPSG:32649")
    assert geographic["axis_order"] == "north,east"
    assert projected["axis_order"] == "east,north"
    assert geographic["coordinate_order"] == "x,y (traditional GIS order)"
    assert "axis_order" in geographic and "code" in geographic
    nodes = parse_axis_nodes(geographic["wkt"])
    assert [n["order"] for n in nodes] == [1, 2]
    assert nodes[0]["direction"] == "north" and nodes[0]["unit"] == "degree"


def test_axis_order_survives_a_wkt_only_definition_without_any_epsg_code():
    wkt = crs_info("EPSG:4326")["wkt"]
    info = crs_info({"wkt": wkt})
    assert info["axis_order"] == "north,east"
    assert axis_order(info)["derivation"] == "parsed_from_crs_wkt_axis_nodes"
    assert axis_order(info)["differs_from_storage"] is True


def test_axis_order_definition_hash_is_unchanged_by_the_new_field():
    info = crs_info("EPSG:3857")
    assert info["definition_hash"] == digest(info["wkt"])
    assert crs_info(info)["definition_hash"] == info["definition_hash"]


@pytest.fixture
def runtime(tmp_path):
    from definitio.runtime import Runtime
    return Runtime(tmp_path / "workspace.sqlite3")


def roads(runtime):
    return runtime.import_dataset("roads", {"type": "FeatureCollection", "features": [
        {"type": "Feature", "geometry": mapping(box(500000, 2540000, 500100, 2540100)),
         "properties": {"road_id": "R1", "lanes": 2}}]}, "EPSG:32649")


def test_imported_dataset_carries_a_full_semantic_header(runtime):
    d = roads(runtime)
    h = d["semantic"]
    assert h["format"] == HEADER_FORMAT
    assert h["identity"]["object_id"] == d["id"]
    assert h["identity"]["object_type"] == "vector_dataset"
    assert h["identity"]["version"]["fingerprint"] == d["fingerprint"]
    assert h["structure"]["fields"] == d["fields"]
    assert h["crs"]["code"] == "EPSG:32649"
    assert h["axis_order"]["sequence"] == "east,north"
    assert h["units"]["linear"] == {"unit": "metre", "metres_per_unit": 1.0}
    assert h["lineage"]["operation"] is None
    assert h["persistence"]["artifact_uri"] == f"definitio://datasets/{d['id']}"
    assert h["scope"]["workspace"]


def test_buffer_result_answers_lineage_unit_crs_and_axis(runtime):
    src = roads(runtime)
    task = runtime.declare("buffer roads", [{"verb": "gis_buffer", "parameters": {
        "input": src["id"], "target": "road_buffer", "distance": 500, "unit": "m", "dissolve": False}}])
    task = runtime.execute(task["id"], task["plan_digest"], True)
    assert task["task_state"] == "COMMITTED"
    out = runtime.store.dataset(task["output_refs"][0])
    h = out["semantic"]
    assert h["lineage"]["operation"]["verb"] == "gis_buffer"
    assert h["lineage"]["operation"]["vid"] == "V18"
    assert h["lineage"]["inputs"] == [{"object_id": src["id"], "name": "roads",
                                      "version_id": src["id"], "fingerprint": src["fingerprint"]}]
    assert h["units"]["operation"]["distance"] == {
        "value": 500, "unit": "m", "crs_unit": "metre", "metres_per_unit": 1.0,
        "value_in_crs_units": 500.0, "method": "divide_by_metres_per_unit"}
    assert h["crs"]["code"] == "EPSG:32649"
    assert h["axis_order"]["sequence"] == "east,north"
    assert h["persistence"]["store"] == "sqlite" and h["persistence"]["record_id"] == out["id"]


def test_header_does_not_change_the_logical_fingerprint(runtime):
    from definitio.model import logical_fingerprint
    src = roads(runtime)
    assert src["fingerprint"] == logical_fingerprint(src["geojson"], src["crs"], src["fields"])


def test_descriptor_and_preview_expose_axis_order(runtime):
    from definitio.agent import AgentService
    from definitio.preview import dataset_summary
    src = roads(runtime)
    service = AgentService(runtime)
    described = service.call("dataset_describe", {"dataset": src["id"]})["data"]["dataset"]
    assert described["crs"]["axis_order"] == "east,north"
    assert described["semantic"]["identity"]["object_id"] == src["id"]
    assert dataset_summary(src)["crs"]["axis_order"] == "east,north"