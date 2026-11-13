"""Bounded native GIS routes; server-owned backends, immutable local resources.

These routes do not alter the frozen G1 mapping registry. See the local contract
addendum for the native backend/version and supported primary branch of each verb.
"""
from __future__ import annotations

import base64
import copy
import hashlib
import io
import json
import os
from pathlib import Path
import re
import subprocess
import tempfile
import time
from urllib.parse import quote, urlparse
import uuid
import xml.etree.ElementTree as ET

import httpx

from .model import GISFault, canonical, crs_info, digest, require, name_ok

SPECS = {
    "gis_run_gp": ("V01", "ArcPy 白名单 GP 通道", "R1", ["backend", "tool", "args"], []),
    "gis_delete_dataset": ("V11", "销毁已预检的独立数据集", "R3", ["dataset", "target_echo", "preflight_id"], ["dataset"]),
    "gis_serve_vector_tile": ("V33", "PostGIS MVT 二进制瓦片", "R0", ["table", "z", "x", "y", "geometry_column"], ["table"]),
    "gis_publish_layer": ("V34", "发布到隔离 GeoServer", "R1", ["dataset_ref", "workspace", "style", "purpose"], ["dataset_ref"]),
    "gis_list_services": ("V35", "OGC 服务能力发现", "R0", ["backend"], []),
    "gis_query_observations": ("V36", "SensorThings 八实体只读查询", "R0", ["backend", "entity"], []),
    "gis_add_layer_from_catalog": ("V39", "许可核验后加入 QGIS 地图", "R1", ["catalog_id", "map", "purpose"], ["catalog_id", "map"]),
    "gis_export_map_image": ("V40", "QGIS 当前地图视图出图", "R1", ["map", "target_path", "format", "dpi"], ["map"]),
}
ROUTES = {"gis_run_gp": "local_arcpy_10_8_seed", "gis_delete_dataset": "local_sqlite_destroy",
          "gis_serve_vector_tile": "installed_postgis_mvt", "gis_publish_layer": "isolated_geoserver_rest",
          "gis_list_services": "ogc_capabilities_http", "gis_query_observations": "istsos4_sta_http",
          "gis_add_layer_from_catalog": "native_qgis_project", "gis_export_map_image": "native_qgis_render"}
BACKENDS = {"gis_run_gp": ["local-arcpy-10.8"], "gis_serve_vector_tile": ["postgis"],
            "gis_publish_layer": ["geoserver"], "gis_list_services": ["geoserver", "istsos"],
            "gis_query_observations": ["istsos"], "gis_add_layer_from_catalog": ["qgis"],
            "gis_export_map_image": ["qgis"], "gis_delete_dataset": ["local-geos"]}
ENTITIES = ["Things", "Locations", "HistoricalLocations", "Datastreams", "Sensors", "ObservedProperties", "Observations", "FeaturesOfInterest"]
EXPLICIT_ONLY = {"gis_run_gp", "gis_delete_dataset", "gis_publish_layer", "gis_add_layer_from_catalog"}
KINDS = {"map", "catalog", "license", "image"}


def schema(verb, text, choice, CRS):
    """Complete public schemas; never accept arbitrary native paths or SQL."""
    props = {"backend": choice(BACKENDS[verb], "Explicit native route; frozen G1 leaves unchanged")}
    for key in SPECS[verb][3]:
        if key != "backend":
            props[key] = text(key)
    if SPECS[verb][2] != "R0":
        props["confirm"] = {"type": "boolean"}
    if verb == "gis_run_gp":
        props["tool"] = text("Whitelist: management.GetCount. Destructive, defer and named-verb bypasses rejected")
        props["args"] = {"type": "object", "properties": {"in_rows": text("Immutable vector dataset handle; counts all rows without a selection")}, "required": ["in_rows"], "additionalProperties": False}
    elif verb == "gis_serve_vector_tile":
        props.update(z={"type": "integer", "minimum": 0, "maximum": 22}, x={"type": "integer", "minimum": 0}, y={"type": "integer", "minimum": 0},
                     geometry_column={"const": "geometry", "description": "Local vector geometry column"},
                     attributes={"type": "array", "items": text("Exact scalar attribute"), "maxItems": 64, "uniqueItems": True})
    elif verb == "gis_list_services":
        props["service"] = choice(["WMS", "WFS", "SensorThings"], "Filter actual capabilities")
    elif verb == "gis_query_observations":
        props["entity"] = choice(ENTITIES, "Closed SensorThings 1.1 entity set")
        for k in ("datastream_id", "thing_id"):
            props[k] = {"type": "integer", "minimum": 0}
        for k in ("filter", "expand", "select", "orderby"):
            props[k] = text("OData parameter passed unchanged to server", maxLength=2048)
        props.update(top={"type": "integer", "minimum": 1, "maximum": 100}, skip={"type": "integer", "minimum": 0}, count={"type": "boolean"})
    elif verb == "gis_export_map_image":
        props.update(format={"const": "png"}, dpi={"type": "number", "exclusiveMinimum": 0, "maximum": 600},
                     layout=text("Only current view supported; named layouts explicitly rejected"),
                     target_path=text("New managed PNG filename, no absolute or relative filesystem path", maxLength=64))
    return {"type": "object", "properties": props, "required": SPECS[verb][3], "additionalProperties": False}


def validate_channel(p):
    tool = p["tool"].casefold()
    require(not any(t in tool for t in ("delete", "truncate", "drop", "remove", "overwrite")), "DESTRUCTIVE_BLOCKED", "销毁工具须走具名破坏动词")
    require(not any(t in tool for t in ("atlas", "batch", "sql", "script", "python")), "DEFER_BLOCKED", "批量/SQL/脚本通道未开放")
    require(p["tool"] == "management.GetCount", "NOT_SUPPORTED", "仅开放已验证的非具名种子 management.GetCount")


def config(runtime, backend):
    path = Path(os.environ.get("DEFINITIO_BACKEND_CONFIG", str(runtime.store.path.parent / "backends.json")))
    require(path.is_file(), "BACKEND_UNAVAILABLE", "缺少服务端后端配置；请运行独立后端配置脚本")
    doc = json.loads(path.read_text(encoding="utf-8"))
    key = "sensorthings" if backend == "istsos" else backend
    require(key in doc, "BACKEND_UNAVAILABLE", "后端未配置", backend=backend)
    return doc[key]


def worker(action, payload, timeout=90):
    python = Path(os.environ.get("DEFINITIO_QGIS_PYTHON", r"C:\Program Files\QGIS 3.44.15\bin\python-qgis-ltr.bat"))
    require(python.is_file(), "BACKEND_UNAVAILABLE", "缺少已配置 QGIS Python")
    with tempfile.TemporaryDirectory(prefix="definitio-native-") as directory:
        a, b = Path(directory) / "input.json", Path(directory) / "output.json"
        a.write_text(canonical({"action": action, **payload}), encoding="utf-8")
        env = {k: v for k, v in os.environ.items() if k not in ("PROJ_DATA", "PROJ_LIB", "GDAL_DATA", "PYTHONPATH")}
        env.update(QT_QPA_PLATFORM="offscreen", PYTHONUTF8="1")
        try:
            r = subprocess.run([str(python), str(Path(__file__).parent.parent / "scripts/native_gis_worker.py"), str(a), str(b)],
                               env=env, capture_output=True, timeout=timeout, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        except subprocess.TimeoutExpired as exc:
            raise GISFault("BACKEND_ERROR", "原生后端超出预算，未提交成果") from exc
        # Worker errors must never echo the input document (it may contain a DSN).
        require(r.returncode == 0 and b.exists(), "BACKEND_ERROR", "原生后端执行失败", returncode=r.returncode,
                stderr=r.stderr.decode("utf-8", errors="replace")[-1800:])
        out = json.loads(b.read_text(encoding="utf-8"))
        require(not out.get("error"), "BACKEND_ERROR", "原生后端执行失败", reason=out.get("error"))
        return out


def resource_summary(kind, payload, crs):
    return {"kind": kind, "feature_count": 0, "fields": [], "geometry_types": [], "bounds": payload.get("extent"), "crs": crs,
            "empty_geometry_count": 0, "invalid_geometry_count": 0, "fingerprint": digest({"kind": kind, "resource": payload, "crs": crs["definition_hash"]})}


def register_resource(runtime, a, db=None):
    kind, payload = a["kind"], copy.deepcopy(a["properties"])
    if kind == "map":
        require(set(payload) == {"crs", "extent", "width", "height", "purpose"}, message="map 需显式 crs/extent/width/height/purpose")
        crs = crs_info(payload["crs"])
        extent = payload["extent"]
        require(isinstance(extent, list) and len(extent) == 4 and all(type(v) in (int, float) for v in extent) and extent[0] < extent[2] and extent[1] < extent[3], message="地图范围不合法")
        require(all(type(payload[k]) is int and 32 <= payload[k] <= 2048 for k in ("width", "height")), message="地图像素尺寸须为 32..2048")
        require(payload["purpose"] in ("noncommercial", "commercial"), message="用途须显式")
        out = worker("create_map", {"properties": payload})
        payload = {**payload, "layers": [], "qgz_base64": out["base64"], "native_readback": out["readback"]}
    elif kind == "license":
        require(set(payload) == {"dataset", "document", "commercial_use", "issuer"}, message="license 需 dataset/document/commercial_use/issuer")
        d = runtime.store.dataset(payload["dataset"])
        runtime.store.verify_snapshot(d)
        require(isinstance(payload["document"], str) and 20 <= len(payload["document"]) <= 16000 and type(payload["commercial_use"]) is bool and isinstance(payload["issuer"], str) and bool(payload["issuer"]), message="许可必须保留来源与正文，不接受口头布尔授权")
        payload.update(dataset=d["id"], source_fingerprint=d["fingerprint"], document_sha256=hashlib.sha256(payload["document"].encode("utf-8")).hexdigest(), provenance="caller_supplied_document; rights not independently authenticated")
        crs = d["crs"]
    else:
        require(kind == "catalog" and set(payload) == {"dataset", "evidence_ref"}, message="catalog 需 dataset/evidence_ref")
        d, lic = runtime.store.dataset(payload["dataset"]), runtime.store.dataset(payload["evidence_ref"])
        runtime.store.verify_snapshot(d); runtime.store.verify_snapshot(lic)
        require(lic.get("kind") == "license" and lic["resource"]["dataset"] == d["id"] and lic["resource"]["source_fingerprint"] == d["fingerprint"], "LICENSE_BLOCKED", "许可证据与数据快照不一致")
        payload = {"storage": {"uri": d["id"]}, "source_fingerprint": d["fingerprint"], "evidence_ref": lic["id"], "commercial_use": lic["resource"]["commercial_use"]}
        crs = d["crs"]
    return runtime.store.insert_resource(a["name"], kind, payload, crs, {"kind": "managed_resource_registration"}, db)


def licensed_dataset(runtime, catalog, purpose, db=None):
    require(catalog.get("kind") == "catalog", "LICENSE_BLOCKED", "须使用已登记的目录许可证据")
    record = catalog["resource"]
    try:
        lic = runtime.store.dataset(record["evidence_ref"], db)
        d = runtime.store.dataset(record["storage"]["uri"], db)
    except GISFault as exc:
        raise GISFault("LICENSE_BLOCKED", "目录来源或许可证据已缺失") from exc
    runtime.store.verify_snapshot(lic); runtime.store.verify_snapshot(d)
    require(lic.get("kind") == "license" and lic["resource"]["dataset"] == d["id"] and lic["resource"]["source_fingerprint"] == record["source_fingerprint"] == d["fingerprint"], "LICENSE_BLOCKED", "许可绑定指纹不一致")
    require(purpose in ("commercial", "noncommercial") and (purpose != "commercial" or record["commercial_use"] is True), "LICENSE_BLOCKED", "许可不支持所请求用途")
    return d, lic


def precheck(runtime, verb, p, db=None):
    """License gate is read-only and precedes confirmation on every entry point."""
    if verb in ("gis_add_layer_from_catalog", "gis_publish_layer"):
        cat = runtime.store.dataset(p["catalog_id"] if verb == "gis_add_layer_from_catalog" else p["dataset_ref"], db)
        licensed_dataset(runtime, cat, p["purpose"], db)


def request(cfg, method, path, **kwargs):
    auth = (cfg["user"], cfg["password"]) if "user" in cfg else None
    try:
        with httpx.Client(timeout=45, trust_env=False, follow_redirects=False, auth=auth) as client:
            with client.stream(method, cfg["url"].rstrip("/") + "/" + path.lstrip("/"), **kwargs) as r:
                data = bytearray()
                for chunk in r.iter_bytes():
                    data.extend(chunk)
                    require(len(data) <= 8 * 1024 * 1024, "BACKEND_ERROR", "服务响应超过 8 MiB 预算")
                require(200 <= r.status_code < 300, "BACKEND_ERROR", "服务返回非成功状态", status=r.status_code, response_sha256=hashlib.sha256(data).hexdigest())
                return bytes(data), str(r.url), r.status_code
    except httpx.HTTPError as exc:
        raise GISFault("BACKEND_UNAVAILABLE", "服务请求失败", exception=type(exc).__name__) from exc


def put_binary(runtime, raw, mime):
    # No tile cache or dataset write. Expiring handles live only in this process.
    current = time.time()
    runtime.binary_handles = {k: v for k, v in runtime.binary_handles.items() if v["expires"] > current}
    require(len(runtime.binary_handles) < 64 and sum(len(v["bytes"]) for v in runtime.binary_handles.values()) + len(raw) <= 16 * 1024 * 1024, "BACKEND_ERROR", "短期二进制句柄预算已满")
    key = "bin_" + uuid.uuid4().hex
    runtime.binary_handles[key] = {"bytes": raw, "mime": mime, "expires": current + 300}
    return {"binary_handle": key, "download_uri": "/api/binary/" + key, "media_type": mime, "byte_length": len(raw), "sha256": hashlib.sha256(raw).hexdigest(), "expires_in_seconds": 300}


def compute(runtime, verb, p, inputs, db=None, dry_run=False):
    if verb == "gis_run_gp":
        validate_channel(p)
        d = inputs[0]
        require(d.get("kind", "vector") == "vector", "NOT_SUPPORTED", "GetCount 种子仅支持本地二维矢量行集")
        python = Path(os.environ.get("DEFINITIO_ARCPY_PYTHON", r"C:\Python27\ArcGIS10.8\python.exe"))
        require(python.is_file(), "BACKEND_UNAVAILABLE", "未配置 ArcPy Python")
        with tempfile.TemporaryDirectory(prefix="definitio-arcpy-") as directory:
            csv, output = Path(directory) / "rows.csv", Path(directory) / "result.json"
            csv.write_text("_record_id\n" + "".join(str(n) + "\n" for n in range(1, d["feature_count"] + 1)), encoding="ascii")
            script = "import arcpy,json,sys; n=int(arcpy.management.GetCount(sys.argv[1]).getOutput(0)); json.dump({'count':n,'version':arcpy.GetInstallInfo()['Version'],'product':arcpy.ProductInfo()},open(sys.argv[2],'w'))"
            r = subprocess.run([str(python), "-c", script, str(csv), str(output)], capture_output=True, timeout=90, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            require(r.returncode == 0 and output.exists(), "BACKEND_ERROR", "ArcPy GetCount 失败", returncode=r.returncode)
            result = json.loads(output.read_text())
        require(result["count"] == d["feature_count"], "BACKEND_ERROR", "ArcPy 行数读回不同")
        return {"result": {**result, "tool": p["tool"], "input_ref": d["id"], "implementation_route": ROUTES[verb], "materialization": "ASCII row-ID table, one immutable feature per row; no selection", "stderr": r.stderr.decode(errors="replace")[-1000:]}}
    if verb == "gis_serve_vector_tile":
        d = inputs[0]
        require(d.get("kind", "vector") == "vector", "NOT_SUPPORTED", "MVT 当前仅接受本地矢量快照")
        from .operations import geometries
        geometries(d)
        require(p["x"] < 2 ** p["z"] and p["y"] < 2 ** p["z"], message="XYZ 超出层级范围")
        require(set(p.get("attributes", [])).issubset({f["name"] for f in d["fields"]}), "SCHEMA_MISMATCH", "瓦片属性不存在")
        out = worker("mvt", {"dataset": d, "parameters": p, "config": config(runtime, "postgis")})
        raw = base64.b64decode(out.pop("base64"))
        handle = {"preview_binary_bytes": len(raw)} if dry_run else put_binary(runtime, raw, "application/vnd.mapbox-vector-tile")
        return {"result": {**handle, **out, "effective_parameters": {**p, "attributes": p.get("attributes", []), "extent": 4096, "buffer": 0, "clip_geom": True, "layer_name": "definitio"}, "implementation_route": ROUTES[verb]}}
    if verb == "gis_query_observations":
        path = p["entity"]
        require(not ("datastream_id" in p and "thing_id" in p), message="两个父实体限定不能同时提供")
        if "datastream_id" in p:
            require(path in ("Observations",), "NOT_SUPPORTED", "datastream_id 当前仅用于 Observations 导航")
            path = f"Datastreams({p['datastream_id']})/Observations"
        if "thing_id" in p:
            require(path in ("Datastreams", "Locations", "HistoricalLocations"), "NOT_SUPPORTED", "thing_id 不适用于该实体")
            path = f"Things({p['thing_id']})/{path}"
        params = {"$" + k: str(p[k]).lower() if type(p[k]) is bool else str(p[k]) for k in ("filter", "top", "skip", "count", "expand", "select", "orderby") if k in p}
        raw, url, status = request(config(runtime, "istsos"), "GET", path, params=params)
        result = json.loads(raw)
        require(isinstance(result, dict) and isinstance(result.get("value"), list), "BACKEND_ERROR", "STA 响应不是实体集合")
        return {"result": {"entity": p["entity"], "data": result["value"], "returned_count": len(result["value"]), "count": result.get("@iot.count"), "next_link": result.get("@iot.nextLink"), "effective_parameters": params, "request_uri": url, "response_sha256": hashlib.sha256(raw).hexdigest(), "http_status": status, "local_filtering": False, "local_page_concatenation": False, "empty_reason": "backend_returned_empty_collection" if not result["value"] else None}}
    if verb == "gis_list_services":
        cfg = config(runtime, p["backend"])
        if p["backend"] == "istsos":
            raw, url, _ = request(cfg, "GET", "")
            doc = json.loads(raw)
            require(isinstance(doc.get("value"), list), "BACKEND_ERROR", "STA 能力面无实体集")
            services = [{"service": "SensorThings", "version": "1.1", "entities": doc["value"], "capabilities_uri": url, "response_sha256": hashlib.sha256(raw).hexdigest()}]
        else:
            services = []
            for service, version in (("WMS", "1.3.0"), ("WFS", "2.0.0")):
                if p.get("service") and p["service"] != service:
                    continue
                raw, url, _ = request(cfg, "GET", "ows", params={"service": service, "version": version, "request": "GetCapabilities"})
                root = ET.fromstring(raw)
                require("Capabilities" in root.tag, "BACKEND_ERROR", "后端未返回 OGC Capabilities")
                names = [e.text for e in root.iter() if e.tag.split("}")[-1] in ("Name", "Title") and e.text]
                services.append({"service": service, "version": root.get("version"), "capabilities_uri": url, "advertised_names": names, "response_sha256": hashlib.sha256(raw).hexdigest()})
        services = [s for s in services if not p.get("service") or s["service"] == p["service"]]
        return {"result": {"services": services, "service_count": len(services), "backend": p["backend"]}}
    if verb == "gis_add_layer_from_catalog":
        cat, mapdoc = inputs
        source, lic = licensed_dataset(runtime, cat, p["purpose"], db)
        require(mapdoc.get("kind") == "map" and mapdoc["resource"]["purpose"] == p["purpose"], "LICENSE_BLOCKED", "地图用途与请求不一致")
        require(source.get("kind", "vector") == "vector", "NOT_SUPPORTED", "目录加图层当前只支持矢量")
        require(source["id"] not in [l["dataset_ref"] for l in mapdoc["resource"]["layers"]], "ALREADY_EXISTS", "地图已包含该快照")
        out = worker("add_layer", {"map": mapdoc["resource"], "dataset": source})
        payload = {**mapdoc["resource"], "qgz_base64": out["base64"], "native_readback": out["readback"],
                   "layers": mapdoc["resource"]["layers"] + [{"dataset_ref": source["id"], "fingerprint": source["fingerprint"], "catalog_id": cat["id"], "license_ref": lic["id"]}]}
        return {"resource": payload, "resource_kind": "map", "crs": mapdoc["crs"], "lifecycle": "map_update", "result": {"added_dataset_ref": source["id"], "native_readback": out["readback"], "license_evidence_ref": lic["id"], "data_imported": False}}
    if verb == "gis_export_map_image":
        d = inputs[0]
        require(d.get("kind") == "map", "NOT_SUPPORTED", "须提供实际 QGIS 地图资源")
        require("layout" not in p, "NOT_SUPPORTED", "首版仅导出当前地图视图；未实现命名布局")
        require(re.fullmatch(r"[\w\u4e00-\u9fff][\w .()-]{0,58}\.png", p["target_path"]), message="target_path 须为新的托管 PNG 文件名，不接受路径")
        out = worker("render", {"map": d["resource"], "dpi": p["dpi"]})
        payload = {"base64": out["base64"], "media_type": "image/png", "file_sha256": out["sha256"], "width": d["resource"]["width"], "height": d["resource"]["height"], "dpi": p["dpi"], "map_ref": d["id"], "native_readback": out["readback"]}
        return {"resource": payload, "resource_kind": "image", "crs": d["crs"], "result": {k: v for k, v in payload.items() if k != "base64"}}
    if verb == "gis_publish_layer":
        return publish(runtime, p, inputs[0], db, dry_run)
    raise GISFault("NOT_SUPPORTED", "销毁须经 Runtime 事务与预检执行")


def publish(runtime, p, catalog, db, dry_run):
    source, lic = licensed_dataset(runtime, catalog, p["purpose"], db)
    require(source.get("kind", "vector") == "vector" and source["feature_count"] > 0, "NOT_SUPPORTED", "发布主路径接受非空同构矢量")
    cfg = config(runtime, "geoserver")
    require(cfg.get("sandbox") is True and cfg.get("allow_publish") is True and urlparse(cfg["url"]).hostname in ("127.0.0.1", "localhost") and p["workspace"].startswith(cfg.get("workspace_prefix", "definitio_")) and re.fullmatch(r"[A-Za-z][A-Za-z0-9_]{0,50}", p["workspace"]), "NOT_SUPPORTED", "发布仅开放已配置隔离回环服务和命名范围")
    require(p["style"] in ("point", "line", "polygon"), "NOT_SUPPORTED", "首版仅使用明确的内置样式")
    style_raw, _, _ = request(cfg, "GET", "rest/styles/" + p["style"] + ".json")
    require("style" in json.loads(style_raw), "BACKEND_ERROR", "样式不可解析")
    if dry_run:
        return {"result": {"license_evidence_ref": lic["id"], "workspace": p["workspace"], "would_publish": True, "external_writes": 0}}
    workspace = p["workspace"]
    name = "layer_" + source["id"].removeprefix("ds_")
    out = worker("shapefile", {"dataset": source, "layer_name": name})
    # Existing names are never overwritten; durable task reservation forbids blind retry.
    with httpx.Client(timeout=15, trust_env=False, auth=(cfg["user"], cfg["password"])) as client:
        exists = client.get(cfg["url"] + "/rest/workspaces/" + workspace + ".json")
    workspace_missing = exists.status_code == 404
    require(exists.status_code in (200, 404), "BACKEND_ERROR", "工作空间状态不明", status=exists.status_code)
    with httpx.Client(timeout=15, trust_env=False, auth=(cfg["user"], cfg["password"])) as client:
        exists = client.get(cfg["url"] + "/rest/layers/" + workspace + ":" + name + ".json")
    require(exists.status_code == 404, "ALREADY_EXISTS", "服务目标已存在或状态不明；禁止覆盖")
    try:
        if workspace_missing:
            request(cfg, "POST", "rest/workspaces", json={"workspace": {"name": workspace}})
        request(cfg, "PUT", f"rest/workspaces/{workspace}/datastores/{name}/file.shp", params={"configure": "first", "charset": "UTF-8"}, content=base64.b64decode(out["base64"]), headers={"Content-Type": "application/zip"})
        # Archive basename is the stable native layer name, set by the worker.
        native_name = name
        request(cfg, "PUT", f"rest/layers/{workspace}:{native_name}.json", json={"layer": {"defaultStyle": {"name": p["style"]}}})
        raw, url, _ = request(cfg, "GET", f"{workspace}/ows", params={"service": "WFS", "version": "2.0.0", "request": "GetFeature", "typeNames": f"{workspace}:{native_name}", "outputFormat": "application/json", "srsName": source["crs"]["code"]})
        features = json.loads(raw)
        require(len(features.get("features", [])) == source["feature_count"], "TARGET_STATE_UNKNOWN", "发布后 WFS 要素数不同")
        from .model import logical_fingerprint, validate_geojson
        gj, crs, fields = validate_geojson(features, source["crs"], source["fields"])
        require(logical_fingerprint(gj, crs, fields) == source["fingerprint"], "TARGET_STATE_UNKNOWN", "发布后 WFS 几何/属性与来源不等价")
    except GISFault as exc:
        raise GISFault("TARGET_STATE_UNKNOWN", "GeoServer 可能已有部分发布，禁止盲目重试；核查服务目标", workspace=workspace, datastore=name, cause=exc.payload()) from exc
    return {"result": {"layer_ref": f"{workspace}:{native_name}", "workspace": workspace, "datastore": name, "wfs_uri": url, "published": True, "feature_count": len(gj["features"]), "source_fingerprint": source["fingerprint"], "readback_fingerprint": logical_fingerprint(gj, crs, fields), "response_sha256": hashlib.sha256(raw).hexdigest(), "license_evidence_ref": lic["id"], "catalog_writeback_status": "pending", "implementation_route": ROUTES["gis_publish_layer"]}}
