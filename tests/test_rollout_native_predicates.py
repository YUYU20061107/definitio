"""R2: raster/pointcloud/resource/service postcondition predicates.

Raster and pointcloud run on the real installed GDAL/PDAL/QGIS. Service verbs
whose backends are environment-blocked are verified at predicate level with
synthetic receipts; the tests say so explicitly and never claim backend evidence.
"""
import math

import numpy as np
import pytest
from shapely.geometry import box, mapping

from definitio import verifier
from definitio.runtime import Runtime
from definitio.agent import AgentService


@pytest.fixture
def service(tmp_path):
    return AgentService(Runtime(tmp_path / "workspace.sqlite3"))


def raster(service, name="dem", data=None, **changes):
    data = data if data is not None else [[float(2 * c + 3 * r) for c in range(7)] for r in range(7)]
    spec = {"data": data, "transform": [1, 0, 0, 0, -1, 7], "dtype": "float32", "nodata": None,
            "band_name": "elevation", **changes}
    result = service.call("raster_import", {"name": name, "raster": spec, "crs": 32649,
                                            "confirm": True, "request_id": "import-" + name})
    assert result["ok"], result
    return service.store.dataset(result["data"]["dataset"]["id"])


def call(service, verb, **p):
    return service.call(verb, {"confirm": True, "request_id": "call-" + str(p.get("target", verb)), **p})


def output(service, reply):
    assert reply["ok"], reply
    return service.store.dataset(reply["data"]["outputs"][0]["id"])


def checks_of(service, reply):
    task = service.store.task(reply["data"]["task"]["id"])
    ev = service.store.evidence(task["steps"][0]["evidence_ref"])
    found = next(f for f in ev["verification"]["found"] if f["predicate"] == "operation_readback_invariants")
    return {c["id"]: c["passed"] for c in found["details"]["checks"]}, found["details"]


def test_raster_calc_grid_mask_and_pixel_spot(service):
    src = raster(service, data=[[0, 1, None], [2, 3, 4]])
    reply = call(service, "gis_raster_calc", inputs=[src["id"]], expression="A * 2 + 1",
                 target="calc", nodata_policy="propagate")
    checks, _ = checks_of(service, reply)
    assert checks["raster_grid_alignment"] is True
    assert checks["raster_nodata_accounting"] is True
    assert checks["raster_nodata_mask_semantics"] is True
    assert checks["raster_pixel_spot"] is True


def test_raster_calc_pixel_tamper_is_named(service, monkeypatch):
    src = raster(service, data=[[0, 1, None], [2, 3, 4]])
    from definitio.raster import compute as real_compute

    def corrupt(verb, p, datasets):
        proposal = real_compute(verb, p, datasets)
        proposal["raster"]["data"][1][2] = 999.0
        return proposal
    monkeypatch.setattr("definitio.raster.compute", corrupt)
    reply = call(service, "gis_raster_calc", inputs=[src["id"]], expression="A * 2 + 1",
                 target="bad", nodata_policy="propagate")
    assert not reply["ok"]
    assert reply["error"]["details"].get("check") in ("raster_pixel_spot", "grid_and_pixels_readback")


@pytest.mark.parametrize("verb,extra", [("gis_slope", {"unit": "degree"}),
                                        ("gis_aspect", {"convention": "north_zero_clockwise", "flat_policy": "nodata"}),
                                        ("gis_hillshade", {"azimuth": 315, "altitude": 45})])
def test_terrain_mask_and_horn_spot(service, verb, extra):
    src = raster(service)
    reply = call(service, verb, input=src["id"], target="terrain", z_factor=1, nodata_policy="propagate", **extra)
    checks, _ = checks_of(service, reply)
    assert checks["raster_grid_alignment"] is True
    assert checks["raster_nodata_accounting"] is True
    assert checks["raster_nodata_mask_semantics"] is True
    assert checks["raster_pixel_spot"] is True


def test_terrain_z_factor_two_matches_declared_convention(service):
    src = raster(service)
    reply = call(service, "gis_slope", input=src["id"], target="slope2", z_factor=2,
                 unit="degree", nodata_policy="propagate")
    d = output(service, reply)
    assert abs(d["raster"]["data"][3][3] - math.degrees(math.atan(2 * math.sqrt(13)))) < 1e-4
    checks, _ = checks_of(service, reply)
    assert checks["raster_pixel_spot"] is True


def test_terrain_pixel_tamper_is_named(service, monkeypatch):
    src = raster(service)
    from definitio.raster import compute as real_compute

    def corrupt(verb, p, datasets):
        proposal = real_compute(verb, p, datasets)
        proposal["raster"]["data"][3][3] += 5.0
        return proposal
    monkeypatch.setattr("definitio.raster.compute", corrupt)
    reply = call(service, "gis_slope", input=src["id"], target="bad", z_factor=1,
                 unit="degree", nodata_policy="propagate")
    assert not reply["ok"]
    assert reply["error"]["details"].get("check") in ("raster_pixel_spot", "grid_and_pixels_readback")


def test_reclassify_rule_mapping_truth(service):
    src = raster(service, data=[[0, 1, 2, 3, None]])
    rules = [{"min": 0, "max": 2, "min_inclusive": True, "max_inclusive": False, "value": 10},
             {"min": 2, "max": 3, "min_inclusive": True, "max_inclusive": True, "value": 20}]
    reply = call(service, "gis_reclassify", input=src["id"], target="classes", rules=rules,
                 unmapped="fail", nodata_policy="propagate")
    checks, _ = checks_of(service, reply)
    assert checks["raster_nodata_mask_semantics"] is True
    assert checks["raster_pixel_spot"] is True


def test_reclassify_wrong_class_is_named(service, monkeypatch):
    src = raster(service, data=[[0, 1, 2, 3, None]])
    rules = [{"min": 0, "max": 3, "min_inclusive": True, "max_inclusive": True, "value": 10}]
    from definitio.raster import compute as real_compute

    def corrupt(verb, p, datasets):
        proposal = real_compute(verb, p, datasets)
        proposal["raster"]["data"][0][3] = 77.0
        return proposal
    monkeypatch.setattr("definitio.raster.compute", corrupt)
    reply = call(service, "gis_reclassify", input=src["id"], target="bad", rules=rules,
                 unmapped="fail", nodata_policy="propagate")
    assert not reply["ok"]
    assert reply["error"]["details"].get("check") in ("raster_pixel_spot", "grid_and_pixels_readback")


def test_zonal_counts_and_stats_recompute(service):
    values = raster(service, "values", data=[[0, 2], [None, 4]], transform=[1, 0, 0, 0, -1, 2])
    zones = service.runtime.import_dataset("zones", {"type": "FeatureCollection", "features": [
        {"type": "Feature", "geometry": mapping(box(0, 0, 2, 2)), "properties": {"zone": "full"}},
        {"type": "Feature", "geometry": mapping(box(10, 10, 11, 11)), "properties": {"zone": "empty"}}]},
        "EPSG:32649")
    reply = call(service, "gis_zonal_statistics", zones=zones["id"], values=values["id"], target="stats",
                 statistics=["count", "sum", "mean", "min", "max"], output="table", zone_field="zone",
                 nodata_policy="exclude", pixel_rule="center", weighting="none")
    checks, _ = checks_of(service, reply)
    assert checks["zonal_zone_count"] is True
    assert checks["zonal_table_schema"] is True
    assert checks["zonal_pixel_accounting"] is True
    assert checks["zonal_stats_recompute"] is True


def test_zonal_wrong_sum_is_named(service, monkeypatch):
    values = raster(service, "values", data=[[0, 2], [None, 4]], transform=[1, 0, 0, 0, -1, 2])
    zones = service.runtime.import_dataset("zones", {"type": "FeatureCollection", "features": [
        {"type": "Feature", "geometry": mapping(box(0, 0, 2, 2)), "properties": {"zone": "full"}}]},
        "EPSG:32649")
    from definitio.raster import compute as real_compute

    def corrupt(verb, p, datasets):
        proposal = real_compute(verb, p, datasets)
        proposal["geojson"]["features"][0]["properties"]["sum"] += 1
        return proposal
    monkeypatch.setattr("definitio.raster.compute", corrupt)
    reply = call(service, "gis_zonal_statistics", zones=zones["id"], values=values["id"], target="bad",
                 statistics=["count", "sum"], output="table", zone_field="zone",
                 nodata_policy="exclude", pixel_rule="center", weighting="none")
    assert not reply["ok"]
    assert reply["error"]["details"].get("check") in ("zonal_stats_recompute", "schema_readback")


def pdal_available():
    from definitio.pointcloud import executables
    try:
        executables()
        return True
    except Exception:
        return False


@pytest.mark.skipif(not pdal_available(), reason="PDAL not configured")
def test_pointcloud_decimate_constructed_truth_and_fingerprint(service):
    import base64
    from pathlib import Path
    las = Path(__file__).resolve().parents[1] / "examples" / "pdal-grid.las"
    reply = service.call("pointcloud_import", {"name": "cloud", "format": "las", "confirm": True,
        "request_id": "pc-imp", "base64": base64.b64encode(las.read_bytes()).decode()})
    assert reply["ok"], reply
    src = service.store.dataset(reply["data"]["dataset"]["id"])
    reply = call(service, "gis_pointcloud_process", input=src["id"], target="thin",
                 action="thin_decimate", params={"step": 3, "offset": 0})
    checks, _ = checks_of(service, reply)
    assert checks["pointcloud_count_accounting"] is True
    assert checks["pointcloud_dimensions_preserved"] is True
    assert checks["pointcloud_fingerprint_recomputed"] is True
    d = output(service, reply)
    assert d["point_count"] == len(range(0, src["point_count"], 3))


@pytest.mark.skipif(not pdal_available(), reason="PDAL not configured")
def test_pointcloud_merge_sum_truth(service):
    import base64
    from pathlib import Path
    las = Path(__file__).resolve().parents[1] / "examples" / "pdal-grid.las"
    encoded = base64.b64encode(las.read_bytes()).decode()
    a = service.call("pointcloud_import", {"name": "a", "format": "las", "confirm": True,
        "request_id": "pc-a", "base64": encoded})["data"]["dataset"]
    b = service.call("pointcloud_import", {"name": "b", "format": "las", "confirm": True,
        "request_id": "pc-b", "base64": encoded})["data"]["dataset"]
    reply = call(service, "gis_pointcloud_process", input=a["id"], target="merged",
                 action="merge", params={"inputs": [b["id"]]})
    checks, _ = checks_of(service, reply)
    assert checks["pointcloud_count_accounting"] is True
    d = output(service, reply)
    assert d["point_count"] == a["point_count"] + b["point_count"]


def unit_context(verb, proposal, inputs, parameters=None):
    return verifier.context(stage="commit", verb=verb, vid="", parameters=parameters or {}, inputs=inputs,
                            proposal=proposal, output=None, store=None, db=None, planned_inputs=[],
                            write_scope=None, actual_writes=[], authorization=None, workspace=None,
                            file_artifact=None)


def run_invariants(ctx):
    result = verifier.run(ctx, predicates=(verifier.operation_readback_invariants,))
    found = result["found"][0]
    return {c["id"]: c["passed"] for c in found["details"]["checks"]}, found["details"]


def test_gp_count_predicate_on_synthetic_receipt():
    # ArcPy backend is environment-blocked; predicate logic is verified on a receipt.
    inputs = [{"feature_count": 7, "fingerprint": "f" * 64}]
    checks, _ = run_invariants(unit_context("gis_run_gp", {"result": {"count": 7}}, inputs))
    assert checks["gp_count_readback"] is True
    checks, _ = run_invariants(unit_context("gis_run_gp", {"result": {"count": 8}}, inputs))
    assert checks["gp_count_readback"] is False


def test_tile_metadata_predicate_and_honest_unconfirmed():
    receipt = {"result": {"sha256": "a" * 64, "byte_length": 128, "expires_in_seconds": 300,
                          "media_type": "application/vnd.mapbox-vector-tile"}}
    checks, details = run_invariants(unit_context("gis_serve_vector_tile", receipt, []))
    assert checks["tile_handle_metadata"] is True
    assert any(u["id"] == "tile_bytes_rehash" for u in details["unconfirmed_checks"])


def test_publish_receipt_predicate():
    inputs = [{"feature_count": 3, "fingerprint": "b" * 64}]
    good = {"result": {"readback_fingerprint": "b" * 64, "source_fingerprint": "b" * 64,
                       "feature_count": 3, "published": True, "catalog_writeback_status": "pending"}}
    checks, _ = run_invariants(unit_context("gis_publish_layer", good, inputs))
    assert checks["publish_readback_receipt"] is True
    bad = {"result": {**good["result"], "readback_fingerprint": "c" * 64}}
    checks, _ = run_invariants(unit_context("gis_publish_layer", bad, inputs))
    assert checks["publish_readback_receipt"] is False


def test_services_and_sta_receipt_predicates():
    services = {"result": {"service_count": 1, "services": [{"response_sha256": "d" * 64}]}}
    checks, _ = run_invariants(unit_context("gis_list_services", services, []))
    assert checks["services_receipt_structure"] is True
    sta = {"result": {"returned_count": 2, "data": [{}, {}], "local_filtering": False,
                      "local_page_concatenation": False, "response_sha256": "e" * 64}}
    checks, _ = run_invariants(unit_context("gis_query_observations", sta, []))
    assert checks["sta_receipt_structure"] is True
    sta["result"]["returned_count"] = 3
    checks, _ = run_invariants(unit_context("gis_query_observations", sta, []))
    assert checks["sta_receipt_structure"] is False


def test_processing_centroids_real_qgis_output(service):
    from pathlib import Path
    import os
    python = Path(os.environ.get("DEFINITIO_QGIS_PYTHON", r"C:\Program Files\QGIS 3.44.15\bin\python-qgis-ltr.bat"))
    if not python.is_file():
        pytest.skip("QGIS Python not configured")
    d = service.runtime.import_dataset("src", {"type": "FeatureCollection", "features": [
        {"type": "Feature", "geometry": mapping(box(500000, 2540000, 500100, 2540100)), "properties": {"g": "A"}},
        {"type": "Feature", "geometry": mapping(box(500200, 2540200, 500300, 2540300)), "properties": {"g": "B"}}]},
        "EPSG:32649")
    reply = call(service, "gis_run_processing", algorithm="native:centroids",
                 params={"INPUT": d["id"], "ALL_PARTS": False, "OUTPUT": "centroids"})
    checks, _ = checks_of(service, reply)
    assert checks["processing_centroid_semantics"] is True
    assert checks["schema_readback"] is True