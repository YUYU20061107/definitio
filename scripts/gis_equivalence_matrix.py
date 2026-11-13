"""Generate actual Agent outputs, then compare in a separate installed QGIS Python."""
from __future__ import annotations

import argparse
from collections import Counter
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from shapely import affinity, wkt
from shapely.geometry import mapping

from definitio.agent import AgentService
from definitio.model import GISFault
from definitio.operations import SPECS
from definitio.runtime import Runtime

GEOMETRIES = {
    "Point": "POINT (0 0)", "MultiPoint": "MULTIPOINT ((0 0),(2 1))",
    "LineString": "LINESTRING (0 0,2 1,3 0)",
    "MultiLineString": "MULTILINESTRING ((0 0,2 1),(3 0,4 2))",
    "Polygon": "POLYGON ((0 0,4 0,4 4,0 4,0 0),(1 1,1 2,2 2,2 1,1 1))",
    "MultiPolygon": "MULTIPOLYGON (((0 0,2 0,2 2,0 2,0 0)),((3 0,4 0,4 1,3 1,3 0)))",
    "GeometryCollection": "GEOMETRYCOLLECTION (POINT (0 0),LINESTRING (1 1,2 2))",
    "empty_polygon": "POLYGON EMPTY",
    "invalid_polygon": "POLYGON ((0 0,2 2,0 2,2 0,0 0))",
    "null_geometry": None, "PointZ": "POINT Z (0 0 3)",
}
CONTEXTS = {"projected_m": (32649, 500000, 2500000, 10),
            "projected_us_ft": (2263, 1000000, 200000, 10),
            "geographic": (4326, 114, 23, .001),
            "unknown": (0, 0, 0, 1)}
FIELDS = [{"name": "group_code", "type": "string", "nullable": True},
          {"name": "value", "type": "number", "nullable": True},
          {"name": "seq", "type": "integer", "nullable": False}]


def collect():
    cases = []
    with tempfile.TemporaryDirectory(prefix="definitio-matrix-") as directory:
        service = AgentService(Runtime(Path(directory) / "matrix.sqlite3"))
        for verb in ("gis_list_algorithms", "gis_describe_algorithm"):
            r = service.call(verb, {} if verb == "gis_list_algorithms" else {"algorithm": "gis_buffer"})
            cases.append({"id": verb, "verb": verb, "geometry": "N/A", "context": "N/A",
                          "actual": {"ok": r["ok"], "data": r["data"]}, "kind": "discovery"})
        serial = 0
        for context, (epsg, x, y, scale) in CONTEXTS.items():
            for kind, raw_wkt in GEOMETRIES.items():
                features = []
                for i, (group, value) in enumerate([("A", 2.0), ("A", None), (None, 4.0)]):
                    g = affinity.translate(affinity.scale(wkt.loads(raw_wkt), xfact=scale, yfact=scale, origin=(0, 0)), xoff=x + i * scale, yoff=y) if raw_wkt else None
                    features.append({"type": "Feature", "geometry": mapping(g) if g is not None else None,
                                     "properties": {"group_code": group, "value": value, "seq": i + 1}})
                geojson = {"type": "FeatureCollection", "features": features}
                # Apply the same coordinate context to the independent cut polygon.
                boundary = mapping(affinity.translate(affinity.scale(wkt.loads("POLYGON ((-1 -1,2 -1,2 5,-1 5,-1 -1))"), xfact=scale, yfact=scale, origin=(0, 0)), xoff=x, yoff=y))
                crs = "EPSG:" + str(epsg)
                refs, imported_fault = [], None
                try:
                    source = service.runtime.import_dataset("source_" + context + "_" + kind, geojson, crs, FIELDS)
                    cut = service.runtime.import_dataset("cut_" + context + "_" + kind, {"type": "FeatureCollection", "features": [
                        {"type": "Feature", "geometry": boundary, "properties": {}}]}, crs)
                    refs = [source["id"], cut["id"]]
                except GISFault as fault:
                    imported_fault = fault.payload()
                # Freeze this generator's original 18-verb fixture grid (666 rows).
                # New vector/raster paths are exercised by completion_matrix.py.
                from definitio.vector_extra import SPECS as vector_extra
                from definitio.raster import SPECS as raster_extra
                from definitio.pointcloud import SPECS as pointcloud_extra
                from definitio.processing import SPECS as processing_extra
                for verb in (v for v in SPECS if v not in vector_extra and v not in raster_extra and v not in pointcloud_extra and v not in processing_extra):
                    if verb in ("gis_list_algorithms", "gis_describe_algorithm"):
                        continue
                    if verb == "gis_create_dataset" and kind != "Point":
                        continue
                    serial += 1
                    input_ref, cut_ref = refs if refs else ("unavailable", "unavailable")
                    target = "matrix_" + str(serial)
                    first_wkt = wkt.loads(raw_wkt) if raw_wkt else wkt.loads("POINT EMPTY")
                    first_wkt = affinity.translate(affinity.scale(first_wkt, xfact=scale, yfact=scale, origin=(0, 0)), xoff=x, yoff=y).wkt
                    params = {
                        "gis_create_dataset": {"target": target, "crs": crs, "fields": FIELDS},
                        "gis_add_field": {"dataset": input_ref, "target": target, "field": {"name": "extra", "type": "integer", "nullable": False, "default": 7}},
                        "gis_calculate_field": {"dataset": input_ref, "target": target, "field": "seq", "expression": "seq * 2"},
                        "gis_copy_dataset": {"source": input_ref, "target": target},
                        "gis_merge_datasets": {"inputs": [input_ref, input_ref], "target": target},
                        "gis_assign_projection": {"dataset": input_ref, "target": target, "crs": "EPSG:4326"},
                        "gis_reproject": {"input": input_ref, "target": target, "target_crs": "EPSG:3857"},
                        "gis_convert_format": {"input": input_ref, "target": target, "format": "geojson"},
                        "gis_buffer": {"input": input_ref, "target": target, "distance": 1.0, "unit": "m", "dissolve": False},
                        "gis_clip": {"input": input_ref, "clip_by": cut_ref, "target": target},
                        "gis_overlay": {"input": input_ref, "overlay": cut_ref, "target": target, "operation": "difference"},
                        "gis_dissolve": {"input": input_ref, "target": target, "fields": ["group_code"]},
                        "gis_field_statistics": {"dataset": input_ref, "fields": ["value"], "group_by": ["group_code"], "statistics": ["count", "sum", "mean", "min", "max"], "output": "result", "null_policy": "exclude"},
                        "gis_spatial_filter": {"table": input_ref, "predicate": "intersects", "reference_wkt": wkt.loads(json_to_wkt(boundary)).wkt},
                        "gis_measure": {"operation": "area", "a_wkt": first_wkt, "crs": crs},
                        "gis_validate_geometry": {"dataset": input_ref, "repair": False},
                    }[verb]
                    # Unknown CRS / non-2D import rejection is an explicit input boundary,
                    # never misreported as successful geometry equivalence.
                    if imported_fault and verb not in ("gis_measure", "gis_create_dataset"):
                        actual = {"ok": False, "error": imported_fault, "phase": "import"}
                    else:
                        a = dict(params)
                        if SPECS[verb][2] != "R0":
                            a.update(confirm=True, request_id=target)
                        r = service.call(verb, a)
                        actual = {"ok": r["ok"]}
                        if r["ok"]:
                            if r["data"].get("outputs"):
                                d = service.store.dataset(r["data"]["outputs"][0]["id"])
                                actual["output"] = {k: d[k] for k in ("geojson", "fields", "crs")}
                            else:
                                item = r["data"]["results"][0]
                                actual["result"] = item["value"] if "value" in item else service.artifact(item["uri"])
                        else:
                            actual["error"] = r["error"]
                    cases.append({"id": f"{verb}/{kind}/{context}", "verb": verb, "geometry": kind, "context": context,
                                  "epsg": epsg, "input": geojson, "schema": FIELDS, "boundary": boundary,
                                  "parameters": params, "actual": actual, "kind": "compute"})
        return cases, service.runtime.versions


def json_to_wkt(geometry):
    from shapely.geometry import shape
    return shape(geometry).wkt


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--qgis-python", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    cases, versions = collect()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    inputs = args.output.with_suffix(".inputs.json")
    inputs.write_text(json.dumps({"cases": cases, "runtime": versions}, ensure_ascii=False), encoding="utf-8")
    env = {k: v for k, v in os.environ.items() if k not in ("PROJ_DATA", "PROJ_LIB", "GDAL_DATA", "PYTHONPATH")}
    env["QT_QPA_PLATFORM"] = "offscreen"
    command = [str(args.qgis_python), str(ROOT / "scripts/qgis_matrix_reference.py"), str(inputs), str(args.output)]
    result = subprocess.run(command, cwd=ROOT, env=env, capture_output=True, timeout=180)
    args.output.with_suffix(".reference.stdout.txt").write_bytes(result.stdout)
    args.output.with_suffix(".reference.stderr.txt").write_bytes(result.stderr)
    if result.returncode != 0:
        print(result.stderr.decode("utf-8", errors="replace")[-3000:])
        raise SystemExit(result.returncode)
    report = json.loads(args.output.read_text(encoding="utf-8"))
    print(json.dumps({k: v for k, v in report.items() if k != "cases"}, ensure_ascii=False, indent=2))
    raise SystemExit(1 if any(c["status"] == "FAIL" for c in report["cases"]) else 0)


if __name__ == "__main__":
    main()
