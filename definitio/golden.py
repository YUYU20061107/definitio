"""Golden chain definition, offline fixtures and independent truth formulas.

The chain is: facility import -> coordinate transformation -> buffer/service area ->
spatial join -> metric summary -> save and re-read the artifact.
Truth is computed independently of the engine's compute path: shoelace on the published
coordinates and the analytic area of the regular polygon GEOS produces for quad_segs=8.
"""
from __future__ import annotations

import base64
import json
import math

from rasterio.warp import transform

STAGES = [("设施数据接入", "gis_import_data"),
          ("坐标转换", "gis_reproject"),
          ("缓冲/服务区分析", "gis_buffer"),
          ("空间连接", "gis_spatial_join"),
          ("指标汇总", "gis_field_statistics"),
          ("保存并回读产物", "task_plan.exports + artifact_view")]
BUFFER_SEGMENTS = 8
BUFFER_METRES = 500.0
TARGET_CRS = "EPSG:32649"
SOURCE_CRS = "EPSG:4326"
ZONE_HALF_WIDTH = 2000.0
CENTRES = [(500000.0, 2541000.0), (504000.0, 2541000.0), (508000.0, 2541000.0)]
POPULATIONS = [1000, 2500, 4000]


def shoelace_area(ring):
    """Independent planar area; no GEOS, no engine code."""
    total = 0.0
    for (x1, y1), (x2, y2) in zip(ring, ring[1:]):
        total += x1 * y2 - x2 * y1
    return abs(total) / 2.0


def outer_ring(geometry):
    """First exterior ring of a Polygon or MultiPolygon, for independent shoelace truth."""
    if geometry["type"] == "MultiPolygon":
        return geometry["coordinates"][0][0]
    if geometry["type"] == "Polygon":
        return geometry["coordinates"][0]
    raise AssertionError(f"not polygonal: {geometry['type']}")


def buffer_truth_area(radius):
    """Analytic area of the regular 4*quad_segs gon GEOS emits for a point buffer."""
    sides = 4 * BUFFER_SEGMENTS
    return 0.5 * sides * radius * radius * math.sin(2 * math.pi / sides)


def fixtures(service, session):
    lonlat = transform(TARGET_CRS, SOURCE_CRS, [c[0] for c in CENTRES], [c[1] for c in CENTRES])
    facilities = {"type": "FeatureCollection", "features": [
        {"type": "Feature", "geometry": {"type": "Point", "coordinates": [lon, lat]},
         "properties": {"facility_id": f"F{i + 1}", "name": f"facility-{i + 1}"}}
        for i, (lon, lat) in enumerate(zip(*lonlat))]}
    zones = {"type": "FeatureCollection", "features": [
        {"type": "Feature", "geometry": {"type": "Polygon", "coordinates": [[
            [cx - ZONE_HALF_WIDTH, cy - ZONE_HALF_WIDTH], [cx + ZONE_HALF_WIDTH, cy - ZONE_HALF_WIDTH],
            [cx + ZONE_HALF_WIDTH, cy + ZONE_HALF_WIDTH], [cx - ZONE_HALF_WIDTH, cy + ZONE_HALF_WIDTH],
            [cx - ZONE_HALF_WIDTH, cy - ZONE_HALF_WIDTH]]]},
         "properties": {"zone_id": f"Z{i + 1}", "population": POPULATIONS[i]}}
        for i, (cx, cy) in enumerate(CENTRES)]}
    imported = service.call("dataset_import", {"name": "gc_zones", "geojson": zones, "crs": TARGET_CRS,
                                               "confirm": True, "request_id": "gc-zones-1"}, session)
    if not imported["ok"]:
        raise AssertionError(imported)
    envelope = {"definitio": {"crs": SOURCE_CRS,
                              "schema": [{"name": "facility_id", "type": "string", "nullable": False},
                                         {"name": "name", "type": "string", "nullable": False}],
                              "source_license": {"allow_import": True, "evidence_ref": "offline-synthetic-fixture"}},
                **facilities}
    uri = "data:application/geo+json;base64," + base64.b64encode(
        json.dumps(envelope, ensure_ascii=False).encode("utf-8")).decode("ascii")
    return {"zones_id": imported["data"]["dataset"]["id"], "facilities_uri": uri,
            "facilities_geojson": facilities, "zones_geojson": zones,
            "buffer_metres": BUFFER_METRES, "expected_join_count": len(CENTRES),
            "expected_population_sum": sum(POPULATIONS), "centres": CENTRES,
            "expected_buffer_area": buffer_truth_area(BUFFER_METRES)}


def chain_steps(facilities_uri, zones_id, prefix):
    return [
        {"verb": "gis_import_data", "parameters": {"source_uri": facilities_uri, "target_name": prefix + "facilities",
                                                   "format": "geojson", "kind": "vector"}},
        {"verb": "gis_reproject", "parameters": {"input": "$previous", "target": prefix + "projected",
                                                 "target_crs": TARGET_CRS}},
        {"verb": "gis_buffer", "parameters": {"input": "$previous", "target": prefix + "service_area",
                                              "distance": BUFFER_METRES, "unit": "m", "dissolve": False}},
        {"verb": "gis_spatial_join", "parameters": {"input": "$previous", "join": zones_id,
                                                    "target": prefix + "joined", "predicate": "intersects",
                                                    "summary": "all"}},
        {"verb": "gis_field_statistics", "parameters": {"dataset": "$previous", "fields": ["population"],
                                                        "group_by": [], "statistics": ["count", "sum"],
                                                        "output": "result", "null_policy": "exclude"}},
    ]