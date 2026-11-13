"""Loopback-only API and same-origin static workspace."""
from __future__ import annotations

import argparse
import io
import json
import os
import zipfile
from contextlib import contextmanager
from pathlib import Path
from urllib.parse import urlparse

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles

from . import __version__
from .demo import demo_layers
from .model import GISFault, canonical, require
from .runtime import Runtime
from .agent import AgentService, INTERFACE, compact_tools
from .guidance import INSTRUCTIONS, PROMPTS

ROOT = Path(__file__).resolve().parent.parent


@contextmanager
def engine_lease(data_dir):
    """Acquire ownership BEFORE Runtime recovery can touch another process's tasks."""
    data_dir = Path(data_dir)
    data_dir.mkdir(parents=True, exist_ok=True)
    with (data_dir / ".engine-owner.lock").open("a+b") as handle:
        handle.seek(0, 2)
        if handle.tell() == 0:
            handle.write(b"1")
            handle.flush()
        handle.seek(0)
        try:
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            raise RuntimeError("GIS workspace is already owned by another engine; reuse its Agent endpoint instead of starting a second Runtime") from exc
        try:
            yield
        finally:
            handle.seek(0)
            if os.name == "nt":
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def create_app(data_dir=None, ui=True):
    runtime = Runtime(Path(data_dir or os.environ.get("DEFINITIO_DATA_DIR", ROOT / "data")) / "workspace.sqlite3")
    app = FastAPI(title="Definitio Local GISAgent", version=__version__)
    app.state.runtime = runtime
    agent_service = AgentService(runtime)
    app.state.agent = agent_service

    @app.middleware("http")
    async def local_only(request: Request, call_next):
        host = request.url.hostname
        if host not in ("127.0.0.1", "localhost", "testserver", "::1"):
            return JSONResponse({"error": {"code": "INVALID_ARGUMENT", "message": "只接受本机请求"}}, status_code=403)
        if request.method not in ("GET", "HEAD", "OPTIONS"):
            origin = request.headers.get("origin")
            if origin and origin != f"{request.url.scheme}://{request.url.netloc}":
                return JSONResponse({"error": {"code": "INVALID_ARGUMENT", "message": "拒绝跨站写入"}}, status_code=403)
            if "application/json" not in request.headers.get("content-type", ""):
                return JSONResponse({"error": {"code": "INVALID_ARGUMENT", "message": "写入请求须为 application/json"}}, status_code=415)
            body = await request.body()
            if len(body) > 8 * 1024 * 1024:
                return JSONResponse({"error": {"code": "INVALID_ARGUMENT", "message": "请求超过 8 MB"}}, status_code=413)
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Cache-Control"] = "no-store"
        response.headers["Content-Security-Policy"] = "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; object-src 'none'"
        return response

    @app.exception_handler(GISFault)
    async def domain_error(request, fault):
        status = {"NOT_FOUND": 404, "ALREADY_EXISTS": 409, "CONFIRM_REQUIRED": 409, "TARGET_STATE_UNKNOWN": 409}.get(fault.code, 400)
        return JSONResponse({"error": fault.payload()}, status_code=status)

    @app.get("/api/health")
    def health():
        return {"status": "ok", "product": "Definitio Studio", "versions": runtime.versions,
                "role": "agent_gis_engine", "ui_enabled": ui,
                "planner": "本地规则规划器（未接入大模型）", "certification": "NOT_CERTIFIED"}

    @app.get("/api/capabilities")
    def capabilities():
        return runtime.capabilities()

    @app.get("/api/agent/manifest")
    def agent_manifest():
        return {"interface_version": INTERFACE, "engine_source_digest": runtime.source_digest,
                "transport": "MCP stdio adapter or same-origin local HTTP",
                "instructions": INSTRUCTIONS, "engine_root": str(ROOT), "workspace_path": str(runtime.store.path.resolve()),
                "prompts": [{"name": k, "description": v[0], "arguments": v[1], "template": v[2]} for k, v in PROMPTS.items()],
                "compact_tool_names": [t["name"] for t in compact_tools(agent_service.tools.values())],
                "tools": list(agent_service.tools.values()), "certification": "NOT_CERTIFIED"}

    @app.post("/api/agent/call")
    def agent_call(body: dict, request: Request):
        from .schemas import validate, text
        from .agent import obj
        validate(obj({"name": text("Tool name"), "arguments": {"type": "object"}}, ["name", "arguments"]), body)
        return agent_service.call(body["name"], body["arguments"], session=request.headers.get("X-Definitio-Session"))

    @app.get("/api/datasets")
    def datasets():
        return runtime.store.datasets()

    @app.post("/api/datasets")
    def import_dataset(body: dict):
        require(body.get("confirm") is True, "CONFIRM_REQUIRED", "导入须显式确认")
        return runtime.import_dataset(body.get("name"), body.get("geojson"), body.get("crs"), body.get("schema"))

    @app.get("/api/datasets/{reference}")
    def dataset(reference: str):
        return runtime.store.dataset(reference)

    @app.get("/api/binary/{handle}")
    def binary(handle: str):
        import time
        item = runtime.binary_handles.get(handle)
        require(item is not None and item["expires"] > time.time(), "NOT_FOUND", "二进制句柄不存在、已过期或引擎已重启")
        return Response(item["bytes"], media_type=item["mime"], headers={"Cache-Control": "no-store"})

    @app.get("/api/datasets/{reference}/export")
    def export_dataset(reference: str):
        d = runtime.store.dataset(reference)
        runtime.store.verify_snapshot(d)
        if "resource" in d:
            import base64
            if d["kind"] == "image":
                return Response(base64.b64decode(d["resource"]["base64"]), media_type="image/png", headers={"Content-Disposition": f'attachment; filename="{d["id"]}.png"'})
            if d["kind"] == "map":
                return Response(base64.b64decode(d["resource"]["qgz_base64"]), media_type="application/zip", headers={"Content-Disposition": f'attachment; filename="{d["id"]}.qgz"'})
            return Response(canonical(d["resource"]), media_type="application/json")
        if d.get("kind") == "pointcloud":
            import base64
            payload = d["pointcloud"]
            return Response(base64.b64decode(payload["base64"], validate=True), media_type="application/octet-stream",
                            headers={"Content-Disposition": f'attachment; filename="{d["id"]}.{payload["format"]}"'})
        if d.get("kind") == "raster":
            import numpy as np
            from affine import Affine
            from rasterio.io import MemoryFile
            r = d["raster"]
            values = np.array([[0 if v is None else v for v in row] for row in r["data"]], dtype=r["dtype"])
            mask = np.array([[0 if v is None else 255 for v in row] for row in r["data"]], dtype="uint8")
            with MemoryFile() as memory:
                with memory.open(driver="GTiff", height=values.shape[0], width=values.shape[1], count=1,
                                 dtype=r["dtype"], transform=Affine(*r["transform"]), crs=d["crs"]["wkt"]) as writer:
                    writer.write(values, 1); writer.write_mask(mask); writer.set_band_description(1, r["band_name"])
                    writer.update_tags(DEFINITIO_FINGERPRINT=d["fingerprint"], DEFINITIO_NODATA_POLICY="internal mask; invalid payload zeros are not valid pixels")
                return Response(memory.read(), media_type="image/tiff", headers={"Content-Disposition": f'attachment; filename="{d["id"]}.tif"'})
        # GeoJSON RFC 7946 requires WGS84. Preserve projected data as a clearly
        # labelled extended document instead of silently mislabelling coordinates.
        payload = {**d["geojson"], "definitio": {"crs": d["crs"], "schema": d["fields"], "fingerprint": d["fingerprint"],
                   "semantic": d.get("semantic"),
                   "format": "GeoJSON-compatible snapshot; projected CRS retained; reproject to EPSG:4326 for RFC7946"}}
        if d["crs"]["code"] != "EPSG:4326":
            payload["crs"] = {"type": "name", "properties": {"name": d["crs"]["code"]}}
        return Response(canonical(payload), media_type="application/geo+json", headers={"Content-Disposition": f'attachment; filename="{d["id"]}.geojson"'})

    @app.post("/api/demo")
    def demo(body: dict):
        require(body.get("confirm") is True, "CONFIRM_REQUIRED", "载入合成示例须确认")
        refs = []
        for name, gj in demo_layers():
            try:
                d = runtime.store.dataset(name)
            except GISFault:
                d = runtime.import_dataset(name, gj, "EPSG:32649", source_kind="synthetic_demo")
            refs.append(d["id"])
        return {"dataset_refs": refs, "source_kind": "synthetic_demo", "message": "合成示例，不是真实城市观测"}

    @app.get("/api/tasks")
    def tasks():
        return runtime.store.tasks()

    @app.post("/api/tasks/plan")
    def plan(body: dict):
        return runtime.declare(body.get("goal"), body.get("steps"), body.get("dataset"), body.get("secondary_dataset"))

    @app.get("/api/tasks/{task_id}")
    def task(task_id: str):
        return runtime.store.task(task_id)

    @app.post("/api/tasks/{task_id}/execute")
    def execute(task_id: str, body: dict):
        return runtime.execute(task_id, body.get("plan_digest"), body.get("confirm", False))

    @app.get("/api/tasks/{task_id}/bundle")
    def bundle(task_id: str):
        bundle = runtime.evidence_bundle(task_id)
        stream = io.BytesIO()
        with zipfile.ZipFile(stream, "w", zipfile.ZIP_DEFLATED) as archive:
            archive.writestr("bundle.json", json.dumps(bundle, ensure_ascii=False, indent=2, allow_nan=False))
            archive.writestr("README.txt", "Definitio local execution evidence.\nVerify bundle_sha256 with scripts/verify_bundle.py.\nThis is not G1 acceptance, independent geometry verification, or backend certification.\n")
        return Response(stream.getvalue(), media_type="application/zip", headers={"Content-Disposition": f'attachment; filename="{task_id}-evidence.zip"'})

    @app.get("/api/evidence/{evidence_id}")
    def evidence(evidence_id: str):
        return runtime.store.evidence(evidence_id)

    if ui:
        @app.get("/")
        def index():
            return FileResponse(ROOT / "web" / "index.html")
        app.mount("/assets", StaticFiles(directory=ROOT / "web"), name="assets")
    else:
        @app.get("/")
        def engine_index():
            return {"service": "Definitio Agent GIS engine", "manifest": "/api/agent/manifest", "ui": False}
    return app


def main():
    parser = argparse.ArgumentParser(description="Definitio local GISAgent workspace")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--data-dir", type=Path)
    parser.add_argument("--headless", action="store_true", help="Run the Agent GIS engine without loading or serving web assets")
    args = parser.parse_args()
    import uvicorn
    data_dir = Path(args.data_dir or os.environ.get("DEFINITIO_DATA_DIR", ROOT / "data"))
    with engine_lease(data_dir):
        uvicorn.run(create_app(data_dir, ui=not args.headless), host="127.0.0.1", port=args.port, workers=1, log_level="info")


if __name__ == "__main__":
    main()
