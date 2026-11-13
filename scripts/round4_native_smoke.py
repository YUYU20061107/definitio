"""Real native end-to-end checks; each run retains its own success/failure record."""
from pathlib import Path
import base64
import json
import os
import sys
import time
import traceback

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from definitio.agent import AgentService
from definitio.runtime import Runtime
from definitio.model import GISFault
from scripts.verify_bundle import verify


def main():
    stamp = time.strftime("%Y%m%d-%H%M%S")
    directory = ROOT / "evidence" / ("round4-native-" + stamp)
    directory.mkdir(parents=True)
    os.environ.setdefault("DEFINITIO_BACKEND_CONFIG", str(ROOT / "data/backends.json"))
    service = AgentService(Runtime(directory / "workspace.sqlite3"))
    rows = []

    def call(verb, **arguments):
        if verb in service.tools and not service.tools[verb]["annotations"]["readOnlyHint"] and verb != "destructive_preflight":
            arguments.update(confirm=True, request_id=stamp + "-" + str(len(rows)))
        reply = service.call(verb, arguments)
        rows.append({"verb": verb, "arguments": arguments, "reply": reply})
        (directory / "calls.json").write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")
        assert reply["ok"], json.dumps(reply, ensure_ascii=False)
        if "task" in reply["data"]:
            bundle = service.runtime.evidence_bundle(reply["data"]["task"]["id"])
            (directory / (reply["data"]["task"]["id"] + ".json")).write_text(json.dumps(bundle, ensure_ascii=False, indent=2), encoding="utf-8")
            assert verify(bundle)["status"] == "PASS", verify(bundle)
        return reply["data"]

    def value(data):
        item = data["results"][0]
        return item.get("value", service.artifact(item["uri"]))

    source = call("dataset_import", name="native-points", crs=4326, source_kind="synthetic_fixture", geojson={"type": "FeatureCollection", "features": [
        {"type": "Feature", "geometry": {"type": "Point", "coordinates": [113 + i * .05, 23 + i * .05]}, "properties": {"key": str(i), "value": i}} for i in range(2)]})["dataset"]["id"]
    # Each branch runs even when another branch fails; failures are retained.
    def probe(label, fn):
        if len(sys.argv) > 1 and label not in sys.argv[1:]:
            return None
        try:
            result = fn()
            print(label + " PASS", flush=True)
            return result
        except Exception as exc:
            (directory / (label + "-failure.txt")).write_text(traceback.format_exc(), encoding="utf-8")
            print(label + " FAIL " + str(exc)[:900], flush=True)
            return None

    probe("V01", lambda: value(call("gis_run_gp", backend="local-arcpy-10.8", tool="management.GetCount", args={"in_rows": source})))
    probe("V33", lambda: value(call("gis_serve_vector_tile", backend="postgis", table=source, z=0, x=0, y=0, geometry_column="geometry", attributes=["key", "value"])))
    probe("V35", lambda: value(call("gis_list_services", backend="geoserver", service="WFS")))
    probe("V36", lambda: value(call("gis_query_observations", backend="istsos", entity="Observations", top=2, count=True)))
    lic = call("workspace_resource", kind="license", name="synthetic-license", properties={"dataset": source, "document": "CC0 1.0: self-authored synthetic fixture, dedicated to the public domain for this local validation.", "commercial_use": True, "issuer": "synthetic fixture generator"})["dataset"]["id"]
    cat = call("workspace_resource", kind="catalog", name="synthetic-catalog", properties={"dataset": source, "evidence_ref": lic})["dataset"]["id"]

    def map_chain():
        map_id = call("workspace_resource", kind="map", name="native-map", properties={"crs": "EPSG:4326", "extent": [112.8, 22.8, 113.3, 23.3], "width": 640, "height": 480, "purpose": "noncommercial"})["dataset"]["id"]
        newmap = call("gis_add_layer_from_catalog", backend="qgis", catalog_id=cat, map=map_id, purpose="noncommercial")["outputs"][0]["id"]
        image = call("gis_export_map_image", backend="qgis", map=newmap, target_path="native-map.png", format="png", dpi=96)["outputs"][0]["id"]
        (directory / "map.png").write_bytes(base64.b64decode(service.store.dataset(image)["resource"]["base64"]))
        (directory / "map.qgz").write_bytes(base64.b64decode(service.store.dataset(newmap)["resource"]["qgz_base64"]))
        return image
    probe("V39-V40", map_chain)
    probe("V34", lambda: value(call("gis_publish_layer", backend="geoserver", dataset_ref=cat, workspace="definitio_" + stamp.replace("-", ""), style="point", purpose="noncommercial")))

    def deletion():
        isolated = call("dataset_import", name="delete-only-synthetic", geojson={"type": "FeatureCollection", "features": []}, crs=4326, source_kind="synthetic_fixture")["dataset"]["id"]
        pf = call("destructive_preflight", operation="delete_dataset", dataset=isolated)["preflight"]
        reply = call("gis_delete_dataset", dataset=isolated, target_echo=pf["target_echo"], preflight_id=pf["preflight_id"])
        try:
            service.store.dataset(isolated)
        except GISFault as fault:
            assert fault.code == "NOT_FOUND"
        else:
            raise AssertionError("Deleted snapshot still readable")
        return reply
    probe("V11", deletion)

    def boundary():
        cloud = call("pointcloud_import", name="cloud", base64=base64.b64encode((ROOT / "examples/pdal-grid.las").read_bytes()).decode(), format="las")["dataset"]["id"]
        return call("gis_pointcloud_process", input=cloud, target="actual-boundary", action="boundary", params={"resolution": 2, "threshold": 1})
    probe("V38-boundary", boundary)
    failures = list(directory.glob("*-failure.txt"))
    report = {"directory": str(directory), "status": "FAIL" if failures else "PASS", "failures": [p.name for p in failures], "calls": len(rows), "implemented_catalog_count": service.runtime.capabilities()["implemented"]}
    (directory / "report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps(report), flush=True)
    return bool(failures)


if __name__ == "__main__":
    raise SystemExit(main())
