"""R1: one canonical CRS projection across preview, descriptor and artifact/export."""
import pytest
from shapely.geometry import box, mapping

from definitio.runtime import Runtime
from definitio.agent import AgentService
from definitio.semantics import CRS_PROJECTION_KEYS, crs_projection
from definitio.preview import dataset_summary
from definitio import commit


@pytest.fixture
def service(tmp_path):
    return AgentService(Runtime(tmp_path / "workspace.sqlite3"))


def _vector(service, crs="EPSG:32649"):
    return service.call("dataset_import", {"name": "src", "request_id": "imp-1", "confirm": True,
        "geojson": {"type": "FeatureCollection", "features": [
            {"type": "Feature", "geometry": mapping(box(500000, 2540000, 500100, 2540100)),
             "properties": {"g": "A"}}]}, "crs": crs})["data"]["dataset"]


def test_projection_keys_are_frozen_and_include_axis_policy():
    assert CRS_PROJECTION_KEYS == ("code", "definition_hash", "projected", "unit",
                                   "metres_per_unit", "coordinate_order", "axis_policy", "axis_order")


def test_three_surfaces_emit_the_identical_projection_for_vector(service):
    d = _vector(service)
    full = service.runtime.store.dataset(d["id"])
    described = service.call("dataset_describe", {"dataset": d["id"]})["data"]["dataset"]["crs"]
    previewed = dataset_summary(full)["crs"]
    exported = commit.dataset_payload(full)["definitio"]["crs"]
    artifact = service.artifact(f"definitio://datasets/{d['id']}/geojson")["definitio"]["crs"]
    assert described == previewed == exported == artifact == crs_projection(full["crs"])
    assert described["axis_order"] == "east,north" and described["axis_policy"] == "explicit_xy"


def test_geographic_axis_order_survives_all_three_surfaces(service):
    d = _vector(service, crs="EPSG:4326")
    full = service.runtime.store.dataset(d["id"])
    for projection in (service.call("dataset_describe", {"dataset": d["id"]})["data"]["dataset"]["crs"],
                       dataset_summary(full)["crs"], commit.dataset_payload(full)["definitio"]["crs"]):
        assert projection["axis_order"] == "north,east"
    assert full["semantic"]["axis_order"]["differs_from_storage"] is True


def test_raster_and_resource_use_the_same_projection(service):
    from definitio.raster import validate_raster
    rt = service.runtime
    raster, crs = validate_raster({"data": [[1.0, 2.0], [3.0, 4.0]],
        "transform": [1.0, 0.0, 500000.0, 0.0, -1.0, 2541000.0],
        "dtype": "float32", "nodata": None, "band_name": "e"}, "EPSG:32649")
    r = rt.store.insert_raster("grid", raster, crs, {"kind": "agent_raster_import"})
    assert dataset_summary(r)["crs"] == service.descriptor(r)["crs"] == crs_projection(r["crs"])
    assert service.artifact(f"definitio://datasets/{r['id']}/raster")["crs"] == crs_projection(r["crs"])
    lic = service.call("workspace_resource", {"kind": "license", "name": "lic", "request_id": "lic-1",
        "confirm": True, "properties": {"dataset": r["id"], "document": "x" * 40,
        "commercial_use": False, "issuer": "tester"}})["data"]["dataset"]
    full = rt.store.dataset(lic["id"])
    assert dataset_summary(full)["crs"] == service.descriptor(full)["crs"] == crs_projection(full["crs"])


def test_pointcloud_projection_matches_when_available(service):
    import base64
    from pathlib import Path
    from definitio.pointcloud import executables
    try:
        executables()
    except Exception:
        pytest.skip("PDAL not configured in this environment")
    las = Path(__file__).resolve().parents[1] / "examples" / "pdal-grid.las"
    reply = service.call("pointcloud_import", {"name": "cloud", "format": "las", "confirm": True,
        "request_id": "pc-imp", "base64": base64.b64encode(las.read_bytes()).decode()})
    assert reply["ok"], reply
    full = service.runtime.store.dataset(reply["data"]["dataset"]["id"])
    assert dataset_summary(full)["crs"] == service.descriptor(full)["crs"] == crs_projection(full["crs"])


def test_control_plane_imports_carry_lineage_operation(service):
    d = _vector(service)
    header = service.runtime.store.dataset(d["id"])["semantic"]
    assert header["lineage"]["operation"]["verb"] == "dataset_import"
    assert header["scope"]["workspace"] is not None


def test_http_import_path_keeps_frozen_header_semantics(service):
    # Round-1 frozen assertion: direct runtime imports carry a full header with
    # operation=None; provenance lives in source.kind. Control-plane imports
    # (above) carry lineage.operation instead.
    rt = service.runtime
    d = rt.import_dataset("http_src", {"type": "FeatureCollection", "features": []}, "EPSG:32649")
    assert d["semantic"]["format"] == "definitio-semantic-object-v1"
    assert d["semantic"]["lineage"]["operation"] is None
    assert d["source"]["kind"] == "user_import"


def test_published_file_artifact_envelope_uses_the_projection(service):
    d = _vector(service)
    rt = service.runtime
    task = rt.declare("copy", [{"verb": "gis_copy_dataset", "parameters": {
        "source": d["id"], "target": "copy"}}], exports=[{"step": 1, "name": "out.geojson", "format": "geojson"}])
    task = rt.execute(task["id"], task["plan_digest"], True)
    assert task["task_state"] == "COMMITTED", task.get("error")
    import json
    from pathlib import Path
    payload = json.loads((rt.store.path.parent / "artifacts" / "out.geojson").read_text(encoding="utf-8"))
    full = rt.store.dataset(task["output_refs"][0])
    assert payload["definitio"]["crs"] == crs_projection(full["crs"])
    assert payload["definitio"]["crs"]["axis_policy"] == "explicit_xy"