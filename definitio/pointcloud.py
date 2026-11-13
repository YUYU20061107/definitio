"""Approved, bounded LAS/LAZ snapshots through the installed PDAL engine."""
from __future__ import annotations

import base64
import hashlib
import json
import os
from pathlib import Path
import subprocess
import tempfile

import numpy as np
import rasterio

from .model import GISFault, crs_info, digest, require

SPECS = {
    "gis_pointcloud_info": ("V37", "PDAL 点云信息", "R0", ["input", "backend"], ["input"]),
    "gis_pointcloud_process": ("V38", "PDAL 白名单点云处理", "R1", ["input", "target", "action", "params"], ["input"]),
}
ACTIONS = ["clip", "reproject", "thin_radius", "thin_decimate", "merge", "convert", "export_raster", "export_raster_tin", "density", "boundary"]
MAX_BYTES = 6 * 1024 * 1024
MAX_POINTS = 100000


def executables():
    pdal = Path(os.environ.get("DEFINITIO_PDAL", r"C:\Program Files\QGIS 3.44.15\bin\pdal.exe"))
    wrench = Path(os.environ.get("DEFINITIO_PDAL_WRENCH", str(pdal.parent.parent / "apps/qgis-ltr/pdal_wrench.exe")))
    require(pdal.is_file(), "NOT_SUPPORTED", "未找到已配置 PDAL；没有自动安装或替代引擎")
    env = {k: v for k, v in os.environ.items() if k not in ("PROJ_DATA", "PROJ_LIB", "GDAL_DATA", "PYTHONPATH")}
    env["PATH"] = str(pdal.parent) + os.pathsep + env.get("PATH", "")
    proj = pdal.parent.parent / "share/proj"
    if proj.is_dir(): env["PROJ_DATA"] = str(proj)
    return pdal, wrench, env


def run(command, *, stdin=None):
    _, _, env = executables()
    try:
        p = subprocess.run([str(x) for x in command], input=stdin, text=True, capture_output=True,
                           env=env, timeout=90, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except subprocess.TimeoutExpired as exc:
        raise GISFault("BACKEND_ERROR", "PDAL 超出单次 90 秒预算，未提交输出") from exc
    require(p.returncode == 0, "BACKEND_ERROR", "PDAL 处理失败，未提交输出", returncode=p.returncode, stderr=p.stderr[-2000:])
    return p.stdout


def inspect_file(path):
    pdal, _, _ = executables()
    document = json.loads(run([pdal, "info", "--metadata", "--schema", "--stats", path]))
    meta = document["metadata"]
    stats = {s["name"]: s for s in document["stats"].get("statistic", [])}
    count = int(meta["count"])
    require(count <= MAX_POINTS, "NOT_SUPPORTED", "单点云最多 100,000 点；请先显式分块")
    require(all(int(s["count"]) == count for s in stats.values()), "SCHEMA_MISMATCH", "LAS 头点数与实际维度统计不一致")
    crs = crs_info(meta.get("comp_spatialreference") or meta.get("spatialreference"))
    bounds = [stats[n]["minimum"] for n in ("X", "Y", "Z")] + [stats[n]["maximum"] for n in ("X", "Y", "Z")] if count else None
    info = {"point_count": count, "bounds": bounds,
            "dimensions": sorted(document["schema"]["dimensions"], key=lambda s: s["name"]),
            "crs": crs, "driver": document["reader"], "file_version": f"{meta['major_version']}.{meta['minor_version']}",
            "pdal_version": document["pdal_version"], "scale": [meta["scale_"+a] for a in "xyz"],
            "offset": [meta["offset_"+a] for a in "xyz"], "dataformat_id": meta["dataformat_id"],
            "compressed": meta["compressed"], "pending": ["vertical_unit_separately_declared"] + (["bounds_for_empty_cloud"] if not count else []),
            "coordinate_dimensions": "XYZ point-cloud coordinates; not vector geometry Z or raster bands"}
    return info


def snapshot(encoded, format):
    require(format in ("las", "laz"), "NOT_SUPPORTED", "仅接受 LAS/LAZ 固定字节快照")
    require(isinstance(encoded, str) and len(encoded) <= MAX_BYTES * 4 // 3 + 4, message="点云超过 6 MiB 字节预算")
    try:
        raw = base64.b64decode(encoded, validate=True)
    except ValueError as exc:
        raise GISFault("INVALID_ARGUMENT", "点云须为有效 base64") from exc
    require(len(raw) <= MAX_BYTES and raw.startswith(b"LASF"), message="来源不是可接受的 LAS/LAZ 固定字节")
    with tempfile.TemporaryDirectory(prefix="definitio-pdal-info-") as directory:
        source = Path(directory)/("source."+format); source.write_bytes(raw)
        info = inspect_file(source)
    require(bool(info["compressed"]) == (format == "laz"), "SCHEMA_MISMATCH", "声明格式与 LAS 压缩头不一致")
    return {"base64": encoded, "format": format, "file_sha256": hashlib.sha256(raw).hexdigest(), "info": info}, info["crs"]


def fingerprint(payload, crs):
    raw = base64.b64decode(payload["base64"], validate=True)
    require(hashlib.sha256(raw).hexdigest() == payload["file_sha256"], "TARGET_STATE_UNKNOWN", "点云字节摘要与记录不一致")
    return digest({"normalization": "definitio-pointcloud-v1", "file_sha256": payload["file_sha256"], "format": payload["format"], "info": payload["info"], "crs": crs["definition_hash"]})


def summary(payload, crs):
    info = payload["info"]
    return {"kind": "pointcloud", "feature_count": 0, "fields": [], "geometry_types": [], "bounds": info["bounds"],
            "crs": crs, "empty_geometry_count": 0, "invalid_geometry_count": 0, "point_count": info["point_count"],
            "point_dimensions": info["dimensions"], "fingerprint": fingerprint(payload, crs)}


def extra_references(verb, p):
    return p["params"].get("inputs", []) if verb == "gis_pointcloud_process" and p["action"] == "merge" else []


def compute(verb, p, datasets):
    require(all(d.get("kind") == "pointcloud" for d in datasets), "NOT_SUPPORTED", "须提供 pointcloud_import 返回的点云引用")
    pdal, wrench, _ = executables()
    with tempfile.TemporaryDirectory(prefix="definitio-pdal-compute-") as directory:
        work = Path(directory); files = []
        for n, d in enumerate(datasets):
            path = work/f"source_{n}.{d['pointcloud']['format']}"
            path.write_bytes(base64.b64decode(d["pointcloud"]["base64"], validate=True)); files.append(path)
        if verb == "gis_pointcloud_info":
            return {"result": {**inspect_file(files[0]), "input_fingerprint": datasets[0]["fingerprint"], "implementation_route": "installed_pdal_info"}}
        action, params = p["action"], p["params"]
        info = datasets[0]["pointcloud"]["info"]
        details = {"implementation_route": "installed_pdal_pipeline", "actual_algorithm": action,
                   "input_point_count": sum(d["point_count"] for d in datasets), "effective_action_parameters": params,
                   "execution_receipt": {"returncode": 0, "timeout_seconds": 90, "shell": False}}
        if action == "boundary":
            require(datasets[0]["crs"]["projected"] and info["point_count"] > 0, "NOT_SUPPORTED", "边界主路径要求非空投影点云")
            require(wrench.is_file(), "BACKEND_UNAVAILABLE", "缺少 PDAL Wrench")
            output = work / "boundary.gpkg"
            run([wrench, "boundary", "--input=" + str(files[0]), "--output=" + str(output), "--resolution=" + str(params["resolution"]), "--threshold=" + str(params["threshold"])])
            require(output.is_file(), "BACKEND_ERROR", "原生边界未产出 GeoPackage")
            from .completion import worker
            result = worker("read_vector", {"base64": base64.b64encode(output.read_bytes()).decode()})
            return {"geojson": result["geojson"], "crs": datasets[0]["crs"], "schema": [],
                    **details, "implementation_route": "installed_pdal_wrench_boundary", "contract_addendum": "V38 boundary explicitly outputs 2D vector; frozen G1 unmodified"}
        if action in ("export_raster", "export_raster_tin", "density"):
            require(datasets[0]["crs"]["projected"], "NOT_SUPPORTED", "栅格转换/密度要求投影 CRS，分辨率为 CRS 单位")
            require(wrench.is_file(), "NOT_SUPPORTED", "缺少配置的 PDAL Wrench")
            bounds = info["bounds"]
            require(bounds is not None, "NOT_SUPPORTED", "空点云没有显式输出网格；不猜测栅格范围")
            resolution = params["resolution"]
            require((bounds[3]-bounds[0])/resolution + 3 <= 512 and (bounds[4]-bounds[1])/resolution + 3 <= 512,
                    "NOT_SUPPORTED", "输出栅格可能超过 512×512；请显式增大分辨率")
            command = {"export_raster": "to_raster", "export_raster_tin": "to_raster_tin", "density": "density"}[action]
            output = work/"output.tif"
            args = [wrench, command, "--input="+str(files[0]), "--output="+str(output), "--resolution="+str(resolution)]
            if action == "export_raster":
                require(params["attribute"] in {s["name"] for s in info["dimensions"]}, "SCHEMA_MISMATCH", "点云维度不存在")
                args.append("--attribute="+params["attribute"])
            if action == "export_raster_tin":
                args.append("--max-triangle-edge-length="+str(params["max_triangle_edge_length"]))
            run(args)
            with rasterio.open(output) as reader:
                values = reader.read(1, masked=True).astype("float64").filled(np.nan)
                raster = {"data": [[None if np.isnan(v) else float(v) for v in row] for row in values],
                          "transform": list(reader.transform)[:6], "dtype": reader.dtypes[0], "nodata": None,
                          "band_name": "point_count" if action == "density" else "elevation" if action == "export_raster_tin" else params["attribute"]}
                if raster["dtype"] == "uint32":
                    require(np.nanmax(values) <= 2147483647, "NOT_SUPPORTED", "密度整数超出已支持像元类型")
                    details["pixel_type_conversion"] = "GDAL uint32 to exact int32; values checked within range"
                    raster["dtype"] = "int32"
                crs = crs_info(reader.crs.to_wkt())
            require(crs["code"] == datasets[0]["crs"]["code"], "BACKEND_ERROR", "点云转栅格 CRS 读回不一致")
            crs = datasets[0]["crs"]
            from .raster import validate_raster, summary as raster_summary
            raster, crs = validate_raster(raster, crs)
            details.update(implementation_route="installed_pdal_wrench", actual_algorithm=command,
                           grid=raster_summary(raster, crs)["grid"], result_empty=not np.isfinite(values).any(),
                           deterministic_rule="fixed input snapshot, explicit resolution, PDAL Wrench grid rule")
            return {"raster": raster, "crs": crs, **details}
        stages = [{"type": "readers.las", "filename": str(f)} for f in files]
        if action == "merge":
            for other in datasets[1:]:
                require(other["crs"]["definition_hash"] == datasets[0]["crs"]["definition_hash"], "CRS_MISMATCH", "合并点云 CRS 不一致")
                require(other["point_dimensions"] == datasets[0]["point_dimensions"], "SCHEMA_MISMATCH", "合并点云维度不一致")
            require(sum(d["point_count"] for d in datasets) <= MAX_POINTS, "NOT_SUPPORTED", "合并后超过点数预算")
            stages.append({"type": "filters.merge"})
        if action == "clip":
            b = params["bounds"]
            require(all(b[n] <= b[n+3] for n in range(3)), message="裁剪 bounds 须为 minXYZ,maxXYZ")
            stages.append({"type": "filters.crop", "bounds": "("+",".join(f"[{b[n]},{b[n+3]}]" for n in range(3))+")"})
        if action == "thin_radius":
            require(datasets[0]["crs"]["projected"], "NOT_SUPPORTED", "采样半径要求投影 CRS，半径为 CRS 单位")
            stages.append({"type": "filters.sample", "radius": params["radius"]})
            details["deterministic_rule"] = "PDAL greedy sampling in immutable input point order"
        if action == "thin_decimate":
            stages.append({"type": "filters.decimation", "step": params["step"], "offset": params["offset"]})
            details["deterministic_rule"] = "keep point indices offset + k*step in immutable input order"
        target_crs = datasets[0]["crs"]
        if action == "reproject":
            target_crs = crs_info(params["target_crs"])
            # Ambiguous datum-grid operations are not advertised as calibrated.
            require(datasets[0]["crs"]["code"] in ("EPSG:32649", "EPSG:4326", "EPSG:3857") and target_crs["code"] in ("EPSG:32649", "EPSG:4326", "EPSG:3857"),
                    "NOT_SUPPORTED", "本地点云重投影仅开放已声明的 WGS84/UTM49N/WebMercator 路线")
            stages.append({"type": "filters.reprojection", "in_srs": datasets[0]["crs"]["wkt"], "out_srs": target_crs["wkt"]})
            details["coordinate_operation"] = {"source": datasets[0]["crs"]["code"], "target": target_crs["code"], "route": "PDAL filters.reprojection / PROJ explicit CRS pair", "grid_operation": "no datum shift; fixed WGS84 projection route"}
        format = params["format"] if action == "convert" else "las"
        output = work/("output."+format)
        writer = {"type": "writers.las", "filename": str(output), "forward": "all", "extra_dims": "all"}
        if action == "reproject":
            writer.update(a_srs=target_crs["wkt"], scale_x=params["scale"][0], scale_y=params["scale"][1], scale_z=params["scale"][2],
                          offset_x=params["offset"][0], offset_y=params["offset"][1], offset_z=params["offset"][2])
        stages.append(writer)
        run([pdal, "pipeline", "--stdin"], stdin=json.dumps({"pipeline": stages}))
        require(output.stat().st_size <= MAX_BYTES, "NOT_SUPPORTED", "输出超过 6 MiB 预算")
        payload, crs = snapshot(base64.b64encode(output.read_bytes()).decode("ascii"), format)
        require(payload["info"]["dimensions"] == info["dimensions"], "NOT_SUPPORTED", "该输入的维度无法完整保真，输出未提交")
        require(crs["code"] == target_crs["code"], "BACKEND_ERROR", "点云输出 CRS 读回不一致")
        # Keep the declared full CRS identity after equivalent PDAL WKT parsing.
        payload["info"]["crs"] = target_crs
        details.update(output_point_count=payload["info"]["point_count"], result_empty=payload["info"]["point_count"] == 0,
                       empty_reason="no_points_selected" if not payload["info"]["point_count"] else None)
        return {"pointcloud": payload, "crs": target_crs, **details}
