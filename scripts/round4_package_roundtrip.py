"""Extract a frozen source ZIP and use its fresh engine through real MCP stdio."""
from pathlib import Path
import argparse
import asyncio
import hashlib
import json
import os
import shutil
import socket
import struct
import subprocess
import sys
import time
import zipfile

import httpx
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

ROOT = Path(__file__).resolve().parents[1]


async def exercise(extracted, url, directory):
    params = StdioServerParameters(command=sys.executable, args=[str(extracted / "run_mcp.py"), "--engine-url", url], cwd=str(directory), env={"PYTHONIOENCODING": "utf-8"})
    calls = []
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            tools = (await session.list_tools()).tools
            assert len(tools) == 21

            async def call(name, a, write=False):
                if write:
                    a = {**a, "confirm": True, "request_id": "cold-" + str(len(calls))}
                result = (await session.call_tool(name, a)).structuredContent
                calls.append({"name": name, "arguments": a, "reply": result})
                (directory / "mcp-calls.json").write_text(json.dumps(calls, ensure_ascii=False, indent=2), encoding="utf-8")
                assert result["ok"], result
                return result["data"]

            async def gis(verb, a, write=False):
                await call("gis_describe_algorithm", {"algorithm": verb})
                if write:
                    a = {**a, "confirm": True, "request_id": "cold-" + str(len(calls))}
                return await call("gis_call", {"verb": verb, "arguments": a})

            source = (await call("dataset_import", {"name": "cold-points", "geojson": {"type": "FeatureCollection", "features": [
                {"type": "Feature", "geometry": {"type": "Point", "coordinates": [113 + i * .05, 23 + i * .05]}, "properties": {"key": str(i)}} for i in range(2)]}, "crs": 4326, "source_kind": "synthetic_fixture"}, True))["dataset"]["id"]
            gp = await gis("gis_run_gp", {"backend": "local-arcpy-10.8", "tool": "management.GetCount", "args": {"in_rows": source}}, True)
            assert gp["results"][0]["value"]["count"] == 2
            tile = await gis("gis_serve_vector_tile", {"backend": "postgis", "table": source, "z": 0, "x": 0, "y": 0, "geometry_column": "geometry", "attributes": ["key"]})
            tile = tile["results"][0]["value"]
            with httpx.Client(trust_env=False) as client:
                raw = client.get(url + tile["download_uri"])
            assert raw.status_code == 200 and hashlib.sha256(raw.content).hexdigest() == tile["sha256"] and tile["feature_count"] == 2
            (directory / "tile.pbf").write_bytes(raw.content)
            lic = (await call("workspace_resource", {"kind": "license", "name": "cold-license", "properties": {"dataset": source, "document": "CC0 1.0 self-authored synthetic cold-start fixture for local GIS validation.", "commercial_use": True, "issuer": "cold fixture generator"}}, True))["dataset"]["id"]
            cat = (await call("workspace_resource", {"kind": "catalog", "name": "cold-catalog", "properties": {"dataset": source, "evidence_ref": lic}}, True))["dataset"]["id"]
            mapref = (await call("workspace_resource", {"kind": "map", "name": "cold-map", "properties": {"crs": "EPSG:4326", "extent": [112.8, 22.8, 113.3, 23.3], "width": 640, "height": 480, "purpose": "noncommercial"}}, True))["dataset"]["id"]
            added = await gis("gis_add_layer_from_catalog", {"backend": "qgis", "catalog_id": cat, "map": mapref, "purpose": "noncommercial"}, True)
            rendered = await gis("gis_export_map_image", {"backend": "qgis", "map": added["outputs"][0]["id"], "target_path": "cold-map.png", "format": "png", "dpi": 96}, True)
            with httpx.Client(trust_env=False) as client:
                image = client.get(url + rendered["outputs"][0]["export_uri"])
                qgz = client.get(url + added["outputs"][0]["export_uri"])
            assert image.status_code == qgz.status_code == 200 and image.content.startswith(b"\x89PNG\r\n\x1a\n") and struct.unpack(">II", image.content[16:24]) == (640, 480)
            (directory / "map.png").write_bytes(image.content)
            (directory / "map.qgz").write_bytes(qgz.content)
            # The final task bundle must be verifiable using the extracted code.
            with httpx.Client(trust_env=False) as client:
                bundle = client.get(url + "/api/tasks/" + rendered["task"]["id"] + "/bundle")
            bundle.raise_for_status()
            path = directory / "map-evidence.zip"
            path.write_bytes(bundle.content)
            check = subprocess.run([sys.executable, str(extracted / "scripts/verify_bundle.py"), str(path)], capture_output=True, timeout=30, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            assert check.returncode == 0, check.stdout.decode(errors="replace")
            return {"compact_tools": 21, "actual_mcp_calls": len(calls), "arcpy_count": 2, "postgis_tile": {"feature_count": 2, "bytes": len(raw.content), "sha256": tile["sha256"]}, "qgis_map": {"width": 640, "height": 480, "png_sha256": hashlib.sha256(image.content).hexdigest(), "portable_qgz_bytes": len(qgz.content)}, "bundle_verifier": json.loads(check.stdout)}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("archive", type=Path)
    args = parser.parse_args()
    archive = args.archive.resolve()
    directory = ROOT / "release" / ("round4-cold-" + time.strftime("%Y%m%d-%H%M%S"))
    directory.mkdir(parents=True, exist_ok=False)
    extracted, data = directory / "source", directory / "data"
    extracted.mkdir(); data.mkdir()
    with zipfile.ZipFile(archive) as z:
        assert z.testzip() is None
        for entry in z.infolist():
            assert (extracted / entry.filename).resolve().is_relative_to(extracted.resolve()), entry.filename
        z.extractall(extracted)
    manifest = json.loads((extracted / "SOURCE-MANIFEST.json").read_text(encoding="utf-8"))
    assert all(hashlib.sha256((extracted / name).read_bytes()).hexdigest() == expected for name, expected in manifest["files"].items())
    # Private local connection configuration is supplied for this validation;
    # it is neither in the frozen ZIP nor part of the public verification receipt.
    shutil.copy2(ROOT / "data/backends.json", data / "backends.json")
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0)); port = sock.getsockname()[1]
    url = f"http://127.0.0.1:{port}"
    process = subprocess.Popen([sys.executable, str(extracted / "run_engine.py"), "--port", str(port), "--data-dir", str(data)], cwd=extracted,
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    report = {"artifact": str(archive), "sha256": hashlib.sha256(archive.read_bytes()).hexdigest(), "extracted_source": str(extracted), "manifest_files_verified": len(manifest["files"]), "third_party_backends": "Existing installed software + private local connections; not bundled or portable by themselves", "original_workspace_touched": False}
    try:
        with httpx.Client(trust_env=False) as client:
            for attempt in range(30):
                try:
                    health = client.get(url + "/api/health").json()
                    capabilities = client.get(url + "/api/capabilities").json()
                    assert capabilities["implemented"] == 40
                    break
                except httpx.ConnectError:
                    time.sleep(.3)
            else:
                raise RuntimeError("Extracted engine did not start")
        report.update(asyncio.run(exercise(extracted, url, directory)), status="PASS", health=health)
    except Exception as exc:
        report.update(status="FAIL", error=repr(exc))
        raise
    finally:
        # Only the exact child process created by this script, never the live engine.
        process.terminate()
        process.wait(timeout=15)
        report["validation_directory"] = str(directory)
        (ROOT / "release/round4-roundtrip-verification.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
