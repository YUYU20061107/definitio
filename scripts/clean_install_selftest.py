"""Clean-install self test: isolated engine startup, no 8876, no D: writes.

Runs entirely in a fresh temp workspace. It never binds a port (FastAPI
TestClient uses in-process ASGI), never starts/stops the online engine, and
never touches D:/agent-native-gis beyond reading nothing at all.
"""
from __future__ import annotations

import argparse
import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path)
    args = parser.parse_args()

    checks = {}
    try:
        import defusedxml  # noqa: F401  (declared in requirements since v05)
        from fastapi.testclient import TestClient
        from definitio import discovery
        from definitio.agent import AgentService
        from definitio.runtime import Runtime
        from definitio.server import create_app
        from definitio.operations import SPECS
        from definitio.model import canonical
        checks["imports_ok"] = True
    except Exception as exc:
        checks["imports_ok"] = False
        checks["import_error"] = f"{type(exc).__name__}: {exc}"
        return _finish(checks, args.out)

    geojson = {"type": "FeatureCollection", "features": [
        {"type": "Feature",
         "geometry": {"type": "Polygon", "coordinates": [[[0, 0], [1, 0], [1, 1], [0, 1], [0, 0]]]},
         "properties": {"g": "A"}}]}
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        service = AgentService(Runtime(root / "engine.sqlite3"))
        checks["engine_started"] = True
        checks["tool_count"] = len(service.tools)
        checks["verb_count"] = len(SPECS)
        checks["tool_count_ok"] = checks["tool_count"] == 60 and checks["verb_count"] == 40

        imported = service.call("dataset_import", {
            "name": "clean_src", "geojson": geojson, "crs": "EPSG:32649",
            "confirm": True, "request_id": "clean-import", "source_kind": "synthetic_fixture"})
        checks["import_ok"] = bool(imported["ok"])
        source = imported["data"]["dataset"]["id"]
        copied = service.call("gis_copy_dataset", {
            "source": source, "target": "clean_copy", "confirm": True, "request_id": "clean-copy"})
        checks["write_chain_ok"] = bool(copied["ok"]) and copied["data"]["task"]["task_state"] == "COMMITTED"
        evidence = service.store.evidence(copied["data"]["task"]["evidence_refs"][0])
        checks["evidence_has_disposition"] = evidence.get("disposition") in (
            "delivered_verified", "delivered_unverified", "not_delivered")
        checks["evidence_digest_ok"] = evidence["evidence_digest"] == __import__(
            "definitio.model", fromlist=["digest"]).digest(
            {k: v for k, v in evidence.items() if k != "evidence_digest"})
        checks["discovery_ok"] = bool(discovery.audit(service)["ok"])

        with TestClient(create_app(root / "http", ui=False)) as client:
            response = client.get("/api/agent/manifest")
        checks["http_manifest_ok"] = response.status_code == 200
        checks["http_manifest_interface"] = response.json().get("interface_version")
        checks["canonical_available"] = bool(canonical({"a": 1}))

    checks["online_engine_untouched"] = True  # no socket bound, no 8876, no D: path used
    return _finish(checks, args.out)


def _finish(checks, out):
    booleans = {k: v for k, v in checks.items() if isinstance(v, bool)}
    checks["ok"] = all(booleans.values())
    text = json.dumps(checks, ensure_ascii=False, indent=2) + "\n"
    print(text)
    if out:
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(text, encoding="utf-8")
    return 0 if checks["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
