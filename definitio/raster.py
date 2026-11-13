"""Bounded, explicit raster snapshots and deterministic single-band analysis."""
from __future__ import annotations

import ast
import copy
import math
import operator

import numpy as np
from affine import Affine
from rasterio.features import geometry_mask

from .model import GISFault, crs_info, digest, require

SPECS = {
    "gis_raster_calc": ("V23", "显式网格栅格代数", "R1", ["inputs", "expression", "target", "nodata_policy"], ["inputs"]),
    "gis_slope": ("V24", "Horn 坡度", "R1", ["input", "target", "z_factor", "unit", "nodata_policy"], ["input"]),
    "gis_aspect": ("V25", "Horn 坡向", "R1", ["input", "target", "z_factor", "convention", "flat_policy", "nodata_policy"], ["input"]),
    "gis_hillshade": ("V26", "Horn 山体阴影", "R1", ["input", "target", "z_factor", "azimuth", "altitude", "nodata_policy"], ["input"]),
    "gis_reclassify": ("V27", "显式区间重分类", "R1", ["input", "target", "rules", "unmapped", "nodata_policy"], ["input"]),
    "gis_zonal_statistics": ("V28", "像元中心分区统计表", "R1", ["zones", "values", "target", "statistics", "output", "zone_field", "nodata_policy", "pixel_rule", "weighting"], ["zones", "values"]),
}


def validate_raster(raster, crs):
    require(isinstance(raster, dict) and set(raster) == {"data", "transform", "nodata", "dtype", "band_name"}, message="栅格须声明 data/transform/nodata/dtype/band_name")
    require(raster["dtype"] in ("float32", "float64", "int16", "int32", "uint8"), "NOT_SUPPORTED", "未支持该像元类型")
    rows = raster["data"]
    require(isinstance(rows, list) and 1 <= len(rows) <= 512 and all(isinstance(row, list) for row in rows), message="栅格须为最多 512 行的二维数组")
    width = len(rows[0])
    require(1 <= width <= 512 and all(len(row) == width for row in rows), message="栅格须为最多 512 列的矩形数组")
    require(isinstance(raster["band_name"], str) and 0 < len(raster["band_name"]) <= 128, message="须显式给出单波段名称")
    transform = raster["transform"]
    require(isinstance(transform, list) and len(transform) == 6 and all(type(n) in (int, float) and math.isfinite(n) for n in transform), message="transform 须为六个有限的 Affine 系数 [a,b,c,d,e,f]")
    require(transform[0] > 0 and transform[4] < 0 and transform[1] == transform[3] == 0, "NOT_SUPPORTED", "本轮支持无旋转、正北向上的网格")
    nodata = raster["nodata"]
    require(nodata is None or type(nodata) in (int, float) and math.isfinite(nodata), message="NoData 须为有限数值或显式 null；data 中 null 始终表示掩膜")
    dtype = np.dtype(raster["dtype"])
    normalized = []
    for row in rows:
        output = []
        for value in row:
            require(value is None or type(value) in (int, float) and math.isfinite(value), message="像元须为有限数值或显式 null")
            if value is None or nodata is not None and value == nodata:
                output.append(None)
                continue
            if dtype.kind in "iu":
                limits = np.iinfo(dtype)
                require(int(value) == value and limits.min <= value <= limits.max, "SCHEMA_MISMATCH", "整数像元溢出或含小数")
            cast = dtype.type(value).item()
            require(math.isfinite(cast), "SCHEMA_MISMATCH", "像元无法表示为声明类型")
            require(nodata is None or cast != nodata, "SCHEMA_MISMATCH", "像元转换后与 NoData 标记冲突")
            output.append(cast)
        normalized.append(output)
    return {**copy.deepcopy(raster), "data": normalized}, crs_info(crs)


def fingerprint(raster, crs):
    return digest({"normalization": "definitio-raster-v1", "crs": crs["definition_hash"], "raster": raster})


def summary(raster, crs):
    height, width = len(raster["data"]), len(raster["data"][0])
    a, _, x, _, e, y = raster["transform"]
    valid = sum(v is not None for row in raster["data"] for v in row)
    return {"kind": "raster", "feature_count": 0, "fields": [], "geometry_types": [],
            "bounds": [x, y + e * height, x + a * width, y], "crs": crs,
            "empty_geometry_count": 0, "invalid_geometry_count": 0,
            "grid": {"width": width, "height": height, "bands": 1, "transform": raster["transform"],
                     "dtype": raster["dtype"], "nodata": raster["nodata"], "band_name": raster["band_name"],
                     "valid_pixel_count": valid, "nodata_pixel_count": height * width - valid},
            "fingerprint": fingerprint(raster, crs)}


def array(d):
    require(d.get("kind") == "raster", "NOT_SUPPORTED", "须提供 raster_import 返回的栅格引用")
    return np.array([[np.nan if v is None else v for v in row] for row in d["raster"]["data"]], dtype=np.float64)


def proposal(values, template, band, dtype="float64", **details):
    require(np.all(np.isfinite(values) | np.isnan(values)), message="计算产生无穷值")
    data = [[None if np.isnan(v) else float(v) for v in row] for row in values]
    raster, crs = validate_raster({**template["raster"], "data": data, "band_name": band,
                                   "dtype": dtype, "nodata": None}, template["crs"])
    valid = sum(v is not None for row in raster["data"] for v in row)
    return {"raster": raster, "crs": crs, "grid": summary(raster, crs)["grid"],
            "processed_pixel_count": values.size, "valid_pixel_count": valid,
            "result_empty": valid == 0, "empty_reason": "all_nodata" if valid == 0 else None,
            "nodata_propagation": "explicit null mask; no NoData-to-zero conversion", **details}


def calc(expression, arrays):
    require(len(expression) <= 256, message="表达式最多 256 字符")
    try:
        tree = ast.parse(expression, mode="eval")
    except SyntaxError as exc:
        raise GISFault("INVALID_ARGUMENT", "栅格表达式语法错误") from exc
    require(sum(1 for _ in ast.walk(tree)) <= 80, message="表达式过于复杂")
    ops = {ast.Add: operator.add, ast.Sub: operator.sub, ast.Mult: operator.mul, ast.Div: operator.truediv, ast.Mod: operator.mod}
    names = {chr(65+i): a for i, a in enumerate(arrays)}
    def visit(n):
        if isinstance(n, ast.Expression): return visit(n.body)
        if isinstance(n, ast.Constant) and type(n.value) in (int, float): return float(n.value)
        if isinstance(n, ast.Name) and n.id in names: return names[n.id]
        if isinstance(n, ast.UnaryOp) and isinstance(n.op, (ast.USub, ast.UAdd)): return (-1 if isinstance(n.op, ast.USub) else 1) * visit(n.operand)
        if isinstance(n, ast.BinOp) and type(n.op) in ops: return ops[type(n.op)](visit(n.left), visit(n.right))
        raise GISFault("INVALID_ARGUMENT", "仅支持输入 A..Z、有限常数与 + - * / %；不执行代码")
    mask = np.logical_or.reduce([np.isnan(a) for a in arrays])
    with np.errstate(all="ignore"):
        result = np.broadcast_to(visit(tree), arrays[0].shape).copy()
    require(np.isfinite(result[~mask]).all(), message="有效像元运算出现除零或非有限值")
    result[mask] = np.nan
    return result


def compute(verb, p, datasets):
    if verb == "gis_zonal_statistics":
        return zonal(p, datasets)
    arrays = [array(d) for d in datasets]
    d, z = datasets[0], arrays[0]
    if verb == "gis_raster_calc":
        for other in datasets[1:]:
            require(other["crs"]["definition_hash"] == d["crs"]["definition_hash"], "CRS_MISMATCH", "栅格 CRS 不一致")
            require(other["raster"]["transform"] == d["raster"]["transform"] and other["grid"]["width"] == d["grid"]["width"] and other["grid"]["height"] == d["grid"]["height"], "SCHEMA_MISMATCH", "栅格网格未对齐；不隐式重采样")
        return proposal(calc(p["expression"], arrays), d, "calculation", effective_expression=p["expression"], language="definitio-safe-raster-arithmetic-v1", input_binding=[chr(65+i) for i in range(len(arrays))])
    if verb == "gis_reclassify":
        rules = p["rules"]
        require(bool(rules), message="规则不能为空")
        ordered = sorted(rules, key=lambda r: (r["min"], r["max"]))
        for r in ordered:
            require(r["min"] < r["max"], message="区间须满足 min < max")
        for a, b in zip(ordered, ordered[1:]):
            require(a["max"] < b["min"] or a["max"] == b["min"] and not (a["max_inclusive"] and b["min_inclusive"]), message="重分类区间重叠")
        covered = np.zeros(z.shape, dtype=bool)
        result = z.copy() if p["unmapped"] == "keep" else np.full(z.shape, np.nan)
        for rule in ordered:
            match = ((z >= rule["min"]) if rule["min_inclusive"] else (z > rule["min"])) & ((z <= rule["max"]) if rule["max_inclusive"] else (z < rule["max"]))
            result[match] = rule["value"]
            covered |= match
        unmapped = int((~covered & ~np.isnan(z)).sum())
        require(p["unmapped"] != "fail" or not unmapped, message="规则未覆盖有效像元", unmapped_pixel_count=unmapped)
        return proposal(result, d, "reclassified", effective_rules=rules, unmapped_pixel_count=unmapped, unmapped_policy=p["unmapped"])
    require(d["crs"]["projected"], "NOT_SUPPORTED", "地形分析要求投影 CRS；不把度当作距离")
    require(z.shape[0] >= 3 and z.shape[1] >= 3, message="Horn 地形分析至少需要 3×3 网格")
    require(d["raster"]["dtype"] in ("float32", "float64"), "NOT_SUPPORTED", "地形分析仅开放浮点 DEM；不隐式改变整型 NoData 结构")
    if verb == "gis_aspect":
        require(d["raster"]["transform"][0] == -d["raster"]["transform"][4], "NOT_SUPPORTED", "当前 GDAL 坡向路线仅开放正方形像元；非方形网格参考语义差异保留在报告中")
    from .gdal_dem import terrain
    result = terrain(verb, p, d, z)
    details = {"algorithm": "GDAL_DEMProcessing_Horn_3x3", "edge_policy": "one-pixel NoData border; any NoData in 3x3 masks output", "z_factor": p["z_factor"], "implementation": "Rasterio-bundled GDAL public C API"}
    if verb == "gis_slope":
        details["unit"] = "degree"
    elif verb == "gis_aspect":
        window_valid = np.isfinite(z[1:-1,1:-1])
        for row in range(3):
            for col in range(3):
                window_valid &= np.isfinite(z[row:row+z.shape[0]-2,col:col+z.shape[1]-2])
        flat = window_valid & np.isnan(result[1:-1,1:-1])
        details.update(convention="north_zero_clockwise", unit="degree", flat_pixel_count=int(flat.sum()), flat_policy="nodata")
    else:
        details.update(azimuth=p["azimuth"], altitude=p["altitude"], intensity="GDAL traditional 1..255; null is NoData")
    return proposal(result, d, d["raster"]["band_name"], "float32" if verb != "gis_hillshade" else "uint8", effective_parameters=details)



def zonal(p, datasets):
    from .operations import collection, geometries, polygonal, same_crs
    zones, values = datasets
    require(zones.get("kind", "vector") == "vector", "NOT_SUPPORTED", "zones 须为面矢量")
    z = array(values)
    same_crs(zones, values); polygonal(zones)
    require(p["zone_field"] in {f["name"] for f in zones["fields"]}, "SCHEMA_MISMATCH", "分区键不存在")
    keys = [f["properties"][p["zone_field"]] for f in zones["geojson"]["features"]]
    require(all(k is not None for k in keys) and len({digest(k) for k in keys}) == len(keys), "SCHEMA_MISMATCH", "分区键须非空且唯一")
    require(not set(p["statistics"]) & {p["zone_field"]}, "SCHEMA_MISMATCH", "统计字段与分区键冲突")
    schema = [copy.deepcopy(next(f for f in zones["fields"] if f["name"] == p["zone_field"]))]
    schema.extend({"name": s, "type": "integer" if s == "count" else "number", "nullable": s != "count"} for s in p["statistics"])
    rows, counts = [], []
    for f in zones["geojson"]["features"]:
        inside = geometry_mask([f["geometry"]], z.shape, Affine(*values["raster"]["transform"]), invert=True, all_touched=False)
        samples = z[inside & np.isfinite(z)]
        props = {p["zone_field"]: f["properties"][p["zone_field"]]}
        for s in p["statistics"]:
            props[s] = int(samples.size) if s == "count" else float({"sum": np.sum, "mean": np.mean, "min": np.min, "max": np.max}[s](samples)) if samples.size else None
        rows.append({"type": "Feature", "geometry": None, "properties": props})
        counts.append({"zone": props[p["zone_field"]], "pixel_count": int(inside.sum()), "valid_pixel_count": int(samples.size), "nodata_pixel_count": int((inside & np.isnan(z)).sum())})
    return {"geojson": collection(rows), "schema": schema, "crs": zones["crs"], "output_kind": "table",
            "zone_count": len(rows), "zone_pixel_counts": counts, "result_empty": len(rows) == 0,
            "effective_parameters": {"pixel_rule": "center", "weighting": "none", "nodata_policy": "exclude", "statistics": p["statistics"]}}
