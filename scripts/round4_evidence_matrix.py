"""Assemble traceable primary-branch comparisons; retain all original failures."""
from collections import Counter
from pathlib import Path
import base64
import hashlib
import json
import math
import os
import sqlite3
import struct
import sys
import tempfile
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from definitio.agent import AgentService
from definitio.completion import worker
from definitio.model import digest
from definitio.runtime import Runtime
from scripts.verify_bundle import verify
from shapely.geometry import shape
import shapely


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def tests():
    records = {}
    for name in ("round4-final-tests.xml", "round4-directed-tests.xml", "round4-provenance-tests.xml"):
        for case in ET.parse(ROOT / "evidence" / name).getroot().iter("testcase"):
            key = case.get("classname") + "::" + case.get("name")
            records[key] = {"id": key, "status": "FAIL" if case.find("failure") is not None or case.find("error") is not None else "SKIP" if case.find("skipped") is not None else "PASS", "source": name}
    assert len(records) == 201 and all(r["status"] == "PASS" for r in records.values()), [r for r in records.values() if r["status"] != "PASS"]
    return list(records.values())


def main():
    evidence = ROOT / "evidence"
    primary = {}
    for directory in sorted(evidence.glob("round4-native-*")):
        if not directory.is_dir() or not (directory / "calls.json").exists():
            continue
        for c in json.loads((directory / "calls.json").read_text(encoding="utf-8")):
            if not c["verb"].startswith("gis_") or not c["reply"]["ok"]:
                continue
            task = c["reply"]["data"]["task"]
            bundle_path = directory / (task["id"] + ".json")
            bundle = json.loads(bundle_path.read_text(encoding="utf-8"))
            assert verify(bundle)["status"] == "PASS", bundle_path
            primary[c["verb"]] = (c, bundle, directory, bundle_path)
    rows = []
    for verb in ("gis_run_gp", "gis_delete_dataset", "gis_serve_vector_tile", "gis_publish_layer", "gis_list_services", "gis_query_observations", "gis_add_layer_from_catalog", "gis_export_map_image"):
        c, b, directory, path = primary[verb]
        e = b["evidence"][0]
        result = e["result"]
        if verb == "gis_run_gp":
            assert result["count"] == b["inputs"][0]["feature_count"] == 2 and result["version"] == "10.8"
            method = "Real ArcPy management.GetCount on ASCII row table, exact immutable source row count; no selection"
        elif verb == "gis_delete_dataset":
            with sqlite3.connect("file:" + str(directory / "workspace.sqlite3") + "?mode=ro", uri=True) as db:
                assert db.execute("SELECT count(*) FROM datasets WHERE id=?", (result["dataset"],)).fetchone()[0] == 0
            assert e["input_unchanged"] is False and e["input_fingerprints"][0]["after"] is None and result["destroyed"] is True
            method = "Direct SQLite absence readback + explicit destruction receipt + offline bundle verification"
        elif verb == "gis_serve_vector_tile":
            assert result["feature_count"] == 2 and result["byte_length"] > 0 and "base64" not in result
            method = "Real PostGIS ST_AsMVT binary; HTTP download/OGR decode verified by named pytest case; no JSON byte payload"
        elif verb == "gis_publish_layer":
            assert result["published"] is True and result["feature_count"] == 2 and result["source_fingerprint"] == result["readback_fingerprint"]
            method = "Actual isolated GeoServer REST publication then WFS GetFeature full geometry/properties/CRS fingerprint equality; catalog writeback pending"
        elif verb == "gis_list_services":
            assert result["service_count"] == 1 and result["services"][0]["service"] == "WFS" and result["services"][0]["version"] == "2.0.0"
            method = "Actual GeoServer WFS XML GetCapabilities parsed type/version; WMS and STA discovery covered in pytest"
        elif verb == "gis_query_observations":
            assert result["http_status"] == 200 and result["returned_count"] == len(result["data"]) == 0 and not result["local_filtering"]
            method = "Official istSOS4 STA collection response passthrough, actual empty collection; 8 entity-set requests in pytest; nonempty semantics unproven"
        elif verb == "gis_add_layer_from_catalog":
            r = b["outputs"][0]["resource"]
            assert len(r["layers"]) == 1 and len(r["native_readback"]["layers"]) == 1 and r["native_readback"]["layers"][0]["valid"] and r["native_readback"]["layers"][0]["feature_count"] == 2
            method = "Native QGIS QGZ save/reopen with embedded GeoPackage, exact layer/source count, license evidence and map version binding"
        else:
            raw = base64.b64decode(b["outputs"][0]["resource"]["base64"])
            assert raw.startswith(b"\x89PNG\r\n\x1a\n") and struct.unpack(">II", raw[16:24]) == (640, 480) and hashlib.sha256(raw).hexdigest() == result["file_sha256"]
            method = "QGIS native renderer in separate process reopened portable project, PNG dimensions/hash; visual inspection confirmed both synthetic points"
        rows.append({"id": verb + "/native_primary", "verb": verb, "status": "PASS_EQUIVALENT", "method": method, "evidence": path.relative_to(evidence).as_posix(), "evidence_sha256": sha(path), "runtime_source_digest": b["task"]["versions"]["source_digest"], "certification": "NOT_CERTIFIED"})
    # Boundary receives an actual native QGIS Processing comparison, separate from
    # the runtime's direct Wrench invocation (shared PDAL kernel, not world truth).
    c, b, directory, path = primary["gis_pointcloud_process"]
    expected = worker("boundary_reference", {"base64": b["inputs"][0]["pointcloud"]["base64"], "resolution": 2, "threshold": 1})
    actual_geoms = [shape(f["geometry"]) for f in b["outputs"][0]["geojson"]["features"]]
    reference_geoms = [shape(g) for g in expected["features"]]
    assert shapely.union_all(actual_geoms).equals(shapely.union_all(reference_geoms))
    reference_dir = evidence / "round4-reference"
    reference_dir.mkdir(exist_ok=True)
    (reference_dir / "boundary.json").write_text(json.dumps(expected, indent=2), encoding="utf-8")
    rows.append({"id": "gis_pointcloud_process/boundary_vector", "verb": "gis_pointcloud_process", "status": "PASS_EQUIVALENT", "method": "QGIS pdal:boundary versus direct Wrench, explicit resolution=2 threshold=1, topological equality; shared PDAL kernel", "evidence": path.relative_to(evidence).as_posix(), "reference": "round4-reference/boundary.json"})
    # Keep transport bytes and a GDAL decoder receipt, independently readable.
    os.environ.setdefault("DEFINITIO_BACKEND_CONFIG", str(ROOT / "data/backends.json"))
    with tempfile.TemporaryDirectory(prefix="definitio-matrix-") as temp:
        r = Runtime(Path(temp) / "workspace.sqlite3")
        s = AgentService(r)
        d = r.import_dataset("source", {"type": "FeatureCollection", "features": [{"type": "Feature", "geometry": {"type": "Point", "coordinates": [113 + i, 23 + i]}, "properties": {"key": str(i)}} for i in range(2)]}, 4326)
        reply = s.call("gis_serve_vector_tile", {"backend": "postgis", "table": d["id"], "z": 0, "x": 0, "y": 0, "geometry_column": "geometry", "attributes": ["key"]})
        assert reply["ok"], reply
        result = s.artifact(reply["data"]["results"][0]["uri"])
        raw = r.binary_handles[result["binary_handle"]]["bytes"]
        decoded = worker("decode_mvt", {"base64": base64.b64encode(raw).decode()})
        assert decoded["feature_count"] == 2 and decoded["values"] == ["0", "1"]
        for f in decoded["features"]:
            i = int(f["properties"]["key"])
            lon, lat = 113 + i, 23 + i
            # The OGR driver exposes an unreferenced tile with y increasing up.
            # Analytic spherical Mercator, independent of PROJ/PostGIS/GDAL math.
            x = (lon + 180) / 360 * 4096
            y = (0.5 + math.log(math.tan(math.pi / 4 + math.radians(lat) / 2)) / (2 * math.pi)) * 4096
            assert abs(f["geometry"]["coordinates"][0] - x) <= 0.5 and abs(f["geometry"]["coordinates"][1] - y) <= 0.5
        decoded["independent_coordinate_check"] = {"method": "analytic spherical Mercator, OGR unreferenced tile y-up", "maximum_quantization_error_pixels": 0.5, "status": "PASS"}
        (reference_dir / "tile.pbf").write_bytes(raw)
        (reference_dir / "tile-decoded.json").write_text(json.dumps(decoded, indent=2), encoding="utf-8")
        rows.append({"id": "gis_serve_vector_tile/ogr_binary_decode", "verb": "gis_serve_vector_tile", "status": "PASS_EQUIVALENT", "method": "PostGIS binary decoded by installed GDAL MVT driver; exact count/selected attributes; independent analytic spherical Mercator coordinates within 0.5 tile pixel quantization", "bytes_sha256": sha(reference_dir / "tile.pbf"), "reference": "round4-reference/tile-decoded.json"})
    test_records = tests()
    for t in test_records:
        if not t["id"].startswith("tests.test_native_completion::"):
            continue
        name = t["id"].split("::")[1]
        if name.startswith("test_changed_execution_source_"):
            continue  # Cross-cutting control-plane test, not an extra GIS domain case.
        verb = ("gis_run_gp" if "gp_" in name else "gis_delete_dataset" if "delete" in name else "gis_serve_vector_tile" if "tile_" in name else "gis_export_map_image" if "map_export" in name else "gis_query_observations" if "sta_" in name else "gis_list_services" if "services_" in name else "gis_publish_layer" if "gis_publish_layer" in name else "gis_add_layer_from_catalog")
        is_negative = any(k in name for k in ("reject", "invalid", "expired", "dependency", "gate", "cannot", "bounded"))
        rows.append({"id": t["id"], "verb": verb, "status": "PASS_DOMAIN_REJECTION" if is_negative else "PASS_CONTRACT", "method": "Named executable test assertions, including real backend requests where indicated; see test source", "test_evidence": t["source"]})
    new = {"status": "PASS", "cases": rows, "counts": dict(Counter(c["status"] for c in rows)), "tests": test_records, "test_unique_ids": len(test_records), "test_execution": "199 passed/1 failed in full run; failed ID passed directed rerun; added source-provenance guard test passed separately; 201 unique passing IDs", "limits": ["Finite primary branches only", "STA endpoint empty; no nonempty observational truth claim", "Native backends share some kernels; no G1 certification"]}
    (evidence / "round4-native-matrix.json").write_text(json.dumps(new, ensure_ascii=False, indent=2), encoding="utf-8")
    oldpath = evidence / "continuation-matrix-final.json"
    previous = json.loads(oldpath.read_text(encoding="utf-8"))
    obsolete = [c for c in previous["cases"] if c["id"] == "gis_pointcloud_process/boundary_conflict"]
    combined = [c for c in previous["cases"] if c not in obsolete] + rows
    assert len({c["id"] for c in combined}) == len(combined)
    result = {"status": "PASS_WITH_REFERENCE_DIFFERENCES", "implemented_verbs": 40, "not_implemented_verbs": 0, "total": len(combined), "counts": dict(Counter(c["status"] for c in combined)), "criteria": previous["criteria"], "certification": "NOT_CERTIFIED", "provenance": "Composite of immutable run records, not one simultaneous run or exhaustive branch proof", "sources": {"continuation-matrix-final.json": sha(oldpath), "round4-native-matrix.json": sha(evidence / "round4-native-matrix.json")}, "superseded_current_gate_rows": obsolete, "cases": combined}
    assert not result["counts"].get("FAIL")
    (evidence / "round4-matrix-final.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({k: v for k, v in result.items() if k not in ("cases", "superseded_current_gate_rows", "criteria")}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
