"""Validated snapshots, explicit CRS handling, and logical fingerprints."""
from __future__ import annotations

import hashlib
import importlib.util
import json
import math
import os
import re
from datetime import datetime, timezone
from pathlib import Path

# Windows GIS installations often export an incompatible global PROJ_LIB.
# Scope the wheel's matching database to this process; never change OS settings.
_rasterio = importlib.util.find_spec("rasterio")
if _rasterio and _rasterio.origin:
    _data = Path(_rasterio.origin).parent / "proj_data"
    if (_data / "proj.db").is_file():
        os.environ["PROJ_DATA"] = str(_data)
        os.environ["PROJ_LIB"] = str(_data)

import shapely
from rasterio.crs import CRS
from shapely.geometry import mapping, shape


class GISFault(Exception):
    def __init__(self, code: str, message: str, **details):
        self.code, self.message, self.details = code, message, details
        super().__init__(message)

    def payload(self):
        return {"code": self.code, "message": self.message, "details": self.details,
                "execution_started": False}


def require(condition, code="INVALID_ARGUMENT", message="参数不合法", **details):
    if not condition:
        raise GISFault(code, message, **details)


def now():
    return datetime.now(timezone.utc).isoformat()


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def digest(value):
    return hashlib.sha256(canonical(value).encode("utf-8")).hexdigest()


def name_ok(name):
    require(isinstance(name, str) and re.fullmatch(r"[\w\u4e00-\u9fff][\w .（）()\-]{0,63}", name),
            message="名称须为 1–64 位字母、汉字、数字、空格、下划线或短横线")
    return name


def crs_info(value):
    require(value not in (None, "", 0), "UNKNOWN_CRS", "请显式指定可解析的 CRS；不会猜测坐标系")
    source_wkt = value.get("wkt") if isinstance(value, dict) else None
    try:
        if isinstance(value, dict):
            value = value.get("wkt") or value.get("definition") or value.get("code")
        if isinstance(value, bool):
            raise ValueError("boolean CRS")
        crs = CRS.from_epsg(value) if isinstance(value, int) else CRS.from_user_input(value)
        # GDAL may simplify a WKT ensemble when it reparses EPSG:3857. Keep
        # the already validated definition as the snapshot's identity source.
        wkt = source_wkt if isinstance(source_wkt, str) else crs.to_wkt(version="WKT2_2019")
        factor = crs.linear_units_factor[1] if crs.is_projected else None
    except Exception as exc:
        raise GISFault("UNKNOWN_CRS", "无法解析 CRS 定义", reason=str(exc)[:400]) from exc
    from .semantics import axis_order
    return {"code": crs.to_string(), "wkt": wkt, "definition_hash": digest(wkt),
            "projected": crs.is_projected, "unit": crs.linear_units if crs.is_projected else "degree",
            "metres_per_unit": factor, "coordinate_order": "x,y (traditional GIS order)",
            "axis_policy": "explicit_xy", "axis_order": axis_order({"wkt": wkt})["sequence"]}


def property_type(value):
    if value is None:
        return None
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, int):
        return "integer"
    if isinstance(value, float):
        require(math.isfinite(value), message="属性不允许 NaN 或无穷值")
        return "number"
    if isinstance(value, str):
        return "string"
    raise GISFault("SCHEMA_MISMATCH", "首版仅支持字符串、数值、布尔和 null 属性")


def validate_geojson(geojson, crs, schema=None):
    require(isinstance(geojson, dict) and geojson.get("type") == "FeatureCollection",
            message="请提供 GeoJSON FeatureCollection")
    features = geojson.get("features")
    require(isinstance(features, list) and len(features) <= 10000, message="单数据集最多 10,000 条要素")
    crs = crs_info(crs)
    names, observed, nullable = [], {}, set()
    normalized = []
    coordinate_count = 0
    for index, feature in enumerate(features):
        require(isinstance(feature, dict) and feature.get("type") == "Feature", message="存在非 Feature 记录", record=index)
        props = feature.get("properties")
        require(isinstance(props, dict), "SCHEMA_MISMATCH", "每条要素须有 properties 对象", record=index)
        for key, value in props.items():
            require(isinstance(key, str) and 0 < len(key) <= 128, "SCHEMA_MISMATCH", "字段名不合法")
            if key not in names:
                names.append(key)
            kind = property_type(value)
            if kind:
                observed.setdefault(key, set()).add(kind)
            else:
                nullable.add(key)
        raw = feature.get("geometry")
        if raw is not None:
            try:
                geom = shape(raw)
                require(not shapely.has_z(geom) and not shapely.has_m(geom), "DIMENSION_MISMATCH", "首版仅接受二维几何", record=index)
                coordinates = shapely.get_coordinates(geom)
                coordinate_count += len(coordinates)
                require(coordinate_count <= 100000, message="单数据集最多 100,000 个坐标点；请先分块")
                require(all(math.isfinite(float(n)) for n in coordinates.flat),
                        "GEOMETRY_INVALID", "几何坐标须有限", record=index)
                raw = json.loads(canonical(mapping(geom)))
            except GISFault:
                raise
            except Exception as exc:
                raise GISFault("GEOMETRY_INVALID", "无法读取几何", record=index, reason=str(exc)[:300]) from exc
        normalized.append({"type": "Feature", "id": index + 1, "geometry": raw, "properties": dict(props)})
    inferred = []
    for key in names:
        types = observed.get(key, set())
        require(len(types) <= 1, "SCHEMA_MISMATCH", "同一字段出现不同类型；请先显式清洗", field=key, types=sorted(types))
        inferred.append({"name": key, "type": next(iter(types), "string"),
                         "nullable": key in nullable or any(key not in f["properties"] for f in normalized)})
    if schema is None:
        schema = inferred
    else:
        require(isinstance(schema, list) and all(isinstance(f, dict) for f in schema), "SCHEMA_MISMATCH", "schema 必须为字段数组")
        schema_names = [f.get("name") for f in schema]
        require(all(isinstance(n, str) and n for n in schema_names) and len(schema_names) == len(set(schema_names)), "SCHEMA_MISMATCH", "字段名无效或重复")
        require(set(names).issubset(set(schema_names)), "SCHEMA_MISMATCH", "属性字段不在声明 schema 中")
        for f in schema:
            require(f.get("type") in ("string", "integer", "number", "boolean") and isinstance(f.get("nullable"), bool),
                    "SCHEMA_MISMATCH", "字段须声明 type 与 nullable")
            for feature in normalized:
                value = feature["properties"].get(f["name"])
                require(value is not None or f["nullable"], "SCHEMA_MISMATCH", "非空字段出现 null", field=f["name"])
                kind = property_type(value)
                require(kind is None or kind == f["type"], "SCHEMA_MISMATCH", "字段值与声明类型不符", field=f["name"])
    for feature in normalized:
        feature["properties"] = {f["name"]: feature["properties"].get(f["name"]) for f in schema}
    return {"type": "FeatureCollection", "features": normalized}, crs, schema


def logical_fingerprint(geojson, crs, schema):
    records = [{"geometry": shapely.normalize(shape(f["geometry"])).wkb_hex if f["geometry"] is not None else None,
                "properties": f["properties"]} for f in geojson["features"]]
    return digest({"normalization": "definitio-logical-v1", "crs": crs["definition_hash"],
                   "schema": schema, "records": sorted(records, key=canonical)})


def snapshot_fingerprint(dataset):
    if "resource" in dataset:
        from .completion import resource_summary
        return resource_summary(dataset["kind"], dataset["resource"], dataset["crs"])["fingerprint"]
    if dataset.get("kind") == "pointcloud":
        from .pointcloud import fingerprint
        return fingerprint(dataset["pointcloud"], dataset["crs"])
    if dataset.get("kind") == "raster":
        from .raster import fingerprint
        return fingerprint(dataset["raster"], dataset["crs"])
    return logical_fingerprint(dataset["geojson"], dataset["crs"], dataset["fields"])


def summarize(geojson, crs, schema):
    geoms = [shape(f["geometry"]) for f in geojson["features"] if f["geometry"] is not None]
    nonempty = [g for g in geoms if not g.is_empty]
    bounds = [min(g.bounds[0] for g in nonempty), min(g.bounds[1] for g in nonempty),
              max(g.bounds[2] for g in nonempty), max(g.bounds[3] for g in nonempty)] if nonempty else None
    return {"feature_count": len(geojson["features"]), "fields": schema, "crs": crs,
            "geometry_types": sorted(set(g.geom_type for g in geoms)), "bounds": bounds,
            "empty_geometry_count": sum(g.is_empty for g in geoms),
            "invalid_geometry_count": sum(not g.is_valid for g in geoms) + len(geojson["features"]) - len(geoms),
            "fingerprint": logical_fingerprint(geojson, crs, schema)}
