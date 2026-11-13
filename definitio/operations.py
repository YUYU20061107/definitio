"""Explicitly scoped local vector operations backed by GEOS and GDAL."""
from __future__ import annotations

import ast
import copy
import math
import operator
from collections import defaultdict

import shapely
from rasterio.warp import transform_geom
from shapely import wkt
from shapely.geometry import MultiPolygon, mapping, shape
from shapely.validation import explain_validity

from .model import GISFault, canonical, crs_info, digest, property_type, require


# This is a new implementation route, never an upgrade of a G1 mapping leaf.
SPECS = {
    "gis_list_algorithms": ("V03", "列出本地能力", "R0", [], []),
    "gis_describe_algorithm": ("V04", "查看能力定义", "R0", ["algorithm"], []),
    "gis_create_dataset": ("V05", "创建空矢量数据集", "R1", ["target", "crs", "fields"], []),
    "gis_add_field": ("V06", "添加字段到副本", "R2", ["dataset", "target", "field"], ["dataset"]),
    "gis_calculate_field": ("V08", "字段计算到副本", "R2", ["dataset", "target", "field", "expression"], ["dataset"]),
    "gis_copy_dataset": ("V09", "复制数据集", "R1", ["source", "target"], ["source"]),
    "gis_merge_datasets": ("V12", "严格同构合并", "R1", ["inputs", "target"], ["inputs"]),
    "gis_assign_projection": ("V14", "为副本指定 CRS", "R2", ["dataset", "target", "crs"], ["dataset"]),
    "gis_reproject": ("V15", "矢量重投影", "R1", ["input", "target", "target_crs"], ["input"]),
    "gis_convert_format": ("V16", "导出 GeoJSON 副本", "R1", ["input", "target", "format"], ["input"]),
    "gis_buffer": ("V18", "平面缓冲区", "R1", ["input", "target", "distance", "unit", "dissolve"], ["input"]),
    "gis_clip": ("V19", "面边界裁剪", "R1", ["input", "clip_by", "target"], ["input", "clip_by"]),
    "gis_overlay": ("V20", "面集合叠加", "R1", ["input", "overlay", "target", "operation"], ["input", "overlay"]),
    "gis_dissolve": ("V21", "按字段融合", "R1", ["input", "target", "fields"], ["input"]),
    "gis_field_statistics": ("V29", "字段统计", "R0", ["dataset", "fields", "group_by", "statistics", "output"], ["dataset"]),
    "gis_spatial_filter": ("V30", "空间筛选（只读）", "R0", ["table", "predicate", "reference_wkt"], ["table"]),
    "gis_measure": ("V31", "平面几何量测", "R0", ["operation", "a_wkt", "crs"], []),
    "gis_validate_geometry": ("V32", "几何有效性检查", "R0", ["dataset", "repair"], ["dataset"]),
}
from .vector_extra import SPECS as VECTOR_SPECS
SPECS.update(VECTOR_SPECS)
from .raster import SPECS as RASTER_SPECS
SPECS.update(RASTER_SPECS)
from .pointcloud import SPECS as POINTCLOUD_SPECS
SPECS.update(POINTCLOUD_SPECS)
from .processing import SPECS as PROCESSING_SPECS
SPECS.update(PROCESSING_SPECS)
from .completion import SPECS as COMPLETION_SPECS
SPECS.update(COMPLETION_SPECS)
OPTIONAL = {"backend", "confirm", "null_policy", "b_wkt"}


def validate_request(verb, params):
    require(isinstance(params, dict), message="parameters 必须是对象")
    if verb not in SPECS:
        raise GISFault("NOT_SUPPORTED", "本地参考后端尚未实现该动词", verb=verb, backend="local-geos")
    from .schemas import operation_schema, validate
    # The same contract is published to agents and enforced here, including
    # direct structured plans submitted without the browser or MCP adapter.
    validate(operation_schema(verb), params)
    if verb == "gis_run_processing":
        from .processing import validate_channel
        validate_channel(params)
    if verb == "gis_run_gp":
        from .completion import validate_channel
        validate_channel(params)
    spec = SPECS[verb]
    missing = [key for key in spec[3] if key not in params]
    require(not missing, message="缺少必填参数；不采用后端隐式默认值", missing=missing)
    unknown = sorted(set(params) - set(spec[3]) - OPTIONAL - ({"attributes", "service", "datastream_id", "thing_id", "filter", "top", "skip", "count", "expand", "select", "orderby", "layout"} if verb in COMPLETION_SPECS else set()) - ({"family", "implemented_only", "query"} if verb == "gis_list_algorithms" else set()))
    require(not unknown, message="存在未声明参数", unknown=unknown)
    if verb not in COMPLETION_SPECS:
        require(params.get("backend", "local-geos") == "local-geos", "NOT_SUPPORTED", "该动词使用 local-geos 本地路线")
    if "target" in params and verb != "gis_append_dataset":
        from .model import name_ok
        name_ok(params["target"])
    for key in spec[4]:
        value = params[key]
        require(isinstance(value, list) if key == "inputs" else isinstance(value, str), message=f"{key} 类型不合法")
        if key == "inputs":
            require(bool(value) and all(isinstance(x, str) for x in value), message="inputs 须为非空数据引用数组")
    if "fields" in params and verb != "gis_create_dataset":
        fields = params["fields"]
        require(isinstance(fields, list) and all(isinstance(x, str) for x in fields) and len(fields) == len(set(fields)),
                message="fields 须为无重复的字符串数组")
    return spec


def geometries(dataset, valid=True):
    result = []
    for feature in dataset["geojson"]["features"]:
        require(feature["geometry"] is not None, "GEOMETRY_INVALID", "存在 null 几何；不会跳过记录", record=feature["id"])
        geom = shape(feature["geometry"])
        if valid:
            require(geom.is_valid, "GEOMETRY_INVALID", "存在无效几何；不会隐式修复", record=feature["id"], reason=explain_validity(geom))
        result.append(geom)
    return result


def projected(dataset):
    require(dataset["crs"]["projected"], "NOT_SUPPORTED", "本地分析首版要求投影 CRS；请先重投影，不能将度当作米")


def polygonal(dataset):
    require(all(g.geom_type in ("Polygon", "MultiPolygon") for g in geometries(dataset)),
            "NOT_SUPPORTED", "该操作仅支持 Polygon / MultiPolygon")


def same_crs(a, b):
    require(a["crs"]["definition_hash"] == b["crs"]["definition_hash"], "CRS_MISMATCH", "两份数据 CRS 不同；不会隐式重投影")


def feature(geom, properties):
    return {"type": "Feature", "geometry": mapping(geom), "properties": copy.deepcopy(properties)}


def collection(features):
    for i, f in enumerate(features, 1):
        f["id"] = i
    return {"type": "FeatureCollection", "features": features}


def exact_key(values):
    return tuple((type(v).__name__, canonical(v)) for v in values)


def multipolygon(geom):
    if geom.is_empty:
        return MultiPolygon([])
    if geom.geom_type == "Polygon":
        return MultiPolygon([geom])
    require(geom.geom_type == "MultiPolygon", "GEOMETRY_INVALID", "集合并产生了非面结果")
    return geom


def safe_expression(expression, properties):
    """An arithmetic grammar, not eval/exec; no calls, attributes, or indexing."""
    require(isinstance(expression, str) and len(expression) <= 256, message="表达式最多 256 字符")
    try:
        tree = ast.parse(expression, mode="eval")
    except SyntaxError as exc:
        raise GISFault("INVALID_ARGUMENT", "字段表达式语法错误") from exc
    require(sum(1 for _ in ast.walk(tree)) <= 80, message="表达式过于复杂")
    ops = {ast.Add: operator.add, ast.Sub: operator.sub, ast.Mult: operator.mul,
           ast.Div: operator.truediv, ast.Mod: operator.mod}

    def visit(node):
        if isinstance(node, ast.Expression):
            return visit(node.body)
        if isinstance(node, ast.Constant) and type(node.value) in (int, float):
            return node.value
        if isinstance(node, ast.Name):
            require(node.id in properties, "SCHEMA_MISMATCH", "表达式引用不存在字段", field=node.id)
            value = properties[node.id]
            require(type(value) in (int, float), "SCHEMA_MISMATCH", "表达式字段须为非空数值", field=node.id)
            return value
        if isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.USub, ast.UAdd)):
            return (-1 if isinstance(node.op, ast.USub) else 1) * visit(node.operand)
        if isinstance(node, ast.BinOp) and type(node.op) in ops:
            return ops[type(node.op)](visit(node.left), visit(node.right))
        raise GISFault("INVALID_ARGUMENT", "仅允许字段、数值和 + - * / %；禁止函数调用与代码执行")
    try:
        value = float(visit(tree))
        require(math.isfinite(value), message="表达式结果不是有限数值")
        return value
    except (ArithmeticError, ValueError, OverflowError) as exc:
        raise GISFault("INVALID_ARGUMENT", "字段计算失败", reason=str(exc)) from exc


def compute(verb, p, datasets):
    """Return either a new snapshot proposal or an explicitly read-only result."""
    if verb in POINTCLOUD_SPECS:
        from .pointcloud import compute as pointcloud_compute
        return pointcloud_compute(verb, p, datasets)
    if verb == "gis_run_processing":
        from .processing import compute as processing_compute
        return processing_compute(p, datasets[0])
    if verb in VECTOR_SPECS:
        from .vector_extra import compute as vector_compute
        return vector_compute(verb, p, datasets)
    if verb in RASTER_SPECS:
        from .raster import compute as raster_compute
        return raster_compute(verb, p, datasets)
    require(all(d.get("kind", "vector") == "vector" for d in datasets), "NOT_SUPPORTED", "此动词当前仅支持二维矢量；栅格请使用明确的栅格家族")
    if verb == "gis_create_dataset":
        from .model import validate_geojson
        gj, crs, schema = validate_geojson(collection([]), p["crs"], p["fields"])
        return {"geojson": gj, "crs": crs, "schema": schema, "checks": {"empty_dataset": True}}
    if verb == "gis_measure":
        crs = crs_info(p["crs"])
        require(crs["projected"], "NOT_SUPPORTED", "首版仅提供投影平面量测，不冒称测地量测")
        try:
            a = wkt.loads(p["a_wkt"])
            require(a.is_valid and not a.is_empty and not a.has_z and not shapely.has_m(a), "GEOMETRY_INVALID", "量测几何无效、为空或非二维")
            unit = crs["metres_per_unit"]
            if p["operation"] == "area":
                value, label = a.area * unit ** 2, "m²"
            elif p["operation"] == "length":
                value, label = a.length * unit, "m"
            elif p["operation"] == "distance":
                require("b_wkt" in p, message="distance 必须提供 b_wkt")
                b = wkt.loads(p["b_wkt"])
                require(b.is_valid and not b.is_empty, "GEOMETRY_INVALID", "b_wkt 无效或为空")
                value, label = a.distance(b) * unit, "m"
            else:
                raise GISFault("NOT_SUPPORTED", "仅支持 area / length / distance")
        except GISFault:
            raise
        except Exception as exc:
            raise GISFault("INVALID_ARGUMENT", "WKT 无法解析", reason=str(exc)[:200]) from exc
        return {"result": {"value": value, "unit": label, "method": "projected_planar"}}
    d = datasets[0]
    gj, schema, crs = copy.deepcopy(d["geojson"]), copy.deepcopy(d["fields"]), d["crs"]
    checks, extra = {}, {}
    if verb == "gis_validate_geometry":
        require(p["repair"] is False, "NOT_SUPPORTED", "几何修复尚未实现；不会隐式改变输入")
        issues = [{"record": f["id"], "reason": "null geometry" if f["geometry"] is None else explain_validity(shape(f["geometry"]))}
                  for f in gj["features"] if f["geometry"] is None or not shape(f["geometry"]).is_valid]
        return {"result": {"valid": not issues, "checked": len(gj["features"]), "issues": issues}}
    if verb == "gis_field_statistics":
        require(p["output"] == "result", "NOT_SUPPORTED", "统计当前仅支持只读 result 输出")
        require(p.get("null_policy") == "exclude", message="须显式指定 null_policy=exclude；同时报告 null_count")
        require(isinstance(p["group_by"], list) and all(isinstance(x, str) for x in p["group_by"]), message="group_by 须为字段数组")
        names = {f["name"] for f in schema}
        require(set(p["fields"] + p["group_by"]).issubset(names), "SCHEMA_MISMATCH", "统计字段不存在")
        require(bool(p["fields"]) and isinstance(p["statistics"], list) and bool(p["statistics"]) and
                set(p["statistics"]).issubset({"count", "sum", "min", "max", "mean"}), message="统计方法不合法")
        groups = defaultdict(list)
        for f in gj["features"]:
            groups[exact_key([f["properties"][k] for k in p["group_by"]])].append(f)
        results = []
        for _, fs in sorted(groups.items()):
            item = {"group": {k: fs[0]["properties"][k] for k in p["group_by"]}, "fields": {}}
            for field in p["fields"]:
                values = [f["properties"][field] for f in fs if f["properties"][field] is not None]
                require(all(type(v) in (int, float) for v in values), "SCHEMA_MISMATCH", "统计字段须为数值", field=field)
                stats = {"count": len(values), "null_count": len(fs) - len(values)}
                for stat in p["statistics"]:
                    if stat != "count":
                        stats[stat] = ({"sum": lambda: sum(values), "min": lambda: min(values),
                                        "max": lambda: max(values), "mean": lambda: sum(values) / len(values)}[stat]() if values else None)
                item["fields"][field] = stats
            results.append(item)
        return {"result": {"groups": results, "null_policy": "exclude", "input_feature_count": len(gj["features"])}}
    if verb == "gis_spatial_filter":
        require(p["predicate"] in ("intersects", "within", "contains", "disjoint"), message="空间谓词不受支持")
        try:
            reference = wkt.loads(p["reference_wkt"])
            require(reference.is_valid, "GEOMETRY_INVALID", "参考几何无效")
        except GISFault:
            raise
        except Exception as exc:
            raise GISFault("INVALID_ARGUMENT", "reference_wkt 无法解析") from exc
        matches = [f for f, g in zip(gj["features"], geometries(d)) if getattr(g, p["predicate"])(reference)]
        return {"result": {"feature_count": len(matches), "geojson": collection(matches), "crs": crs}}
    if verb in ("gis_copy_dataset", "gis_convert_format"):
        if verb == "gis_convert_format":
            require(p["format"].lower() == "geojson", "NOT_SUPPORTED", "首版转换格式仅支持 GeoJSON")
        checks["snapshot_equal"] = True
    elif verb == "gis_add_field":
        field = p["field"]
        require(isinstance(field, dict) and set(field) == {"name", "type", "nullable", "default"}, message="field 须含 name/type/nullable/default")
        require(field["name"] not in [f["name"] for f in schema], "SCHEMA_MISMATCH", "字段已存在")
        schema.append({k: field[k] for k in ("name", "type", "nullable")})
        for f in gj["features"]:
            f["properties"][field["name"]] = field["default"]
    elif verb == "gis_calculate_field":
        require(isinstance(p["field"], str) and p["field"] in [f["name"] for f in schema], "SCHEMA_MISMATCH", "计算目标字段不存在")
        target = next(f for f in schema if f["name"] == p["field"])
        require(target["type"] in ("integer", "number"), "SCHEMA_MISMATCH", "计算目标须为数值字段")
        for f in gj["features"]:
            value = safe_expression(p["expression"], f["properties"])
            require(target["type"] != "integer" or value.is_integer(), "SCHEMA_MISMATCH", "计算结果不是整数；不会截断")
            f["properties"][p["field"]] = int(value) if target["type"] == "integer" else value
    elif verb == "gis_merge_datasets":
        fs = []
        for item in datasets:
            same_crs(d, item)
            require(item["fields"] == schema, "SCHEMA_MISMATCH", "合并要求字段顺序、类型、可空性严格同构")
            geometries(item)
            fs.extend(copy.deepcopy(item["geojson"]["features"]))
        gj = collection(fs)
        checks["count_sum"] = len(fs) == sum(item["feature_count"] for item in datasets)
    elif verb == "gis_assign_projection":
        crs = crs_info(p["crs"])
        extra["metadata_only"] = True
    elif verb == "gis_reproject":
        crs = crs_info(p["target_crs"])
        for f, geom in zip(gj["features"], geometries(d)):
            f["geometry"] = mapping(geom) if geom.is_empty else transform_geom(d["crs"]["wkt"], crs["wkt"], f["geometry"], precision=-1)
        extra["coordinate_operation"] = {"engine": "GDAL/PROJ", "source": d["crs"]["code"], "target": crs["code"],
                                         "axis_policy": "traditional GIS x,y", "selection": "GDAL default; not calibrated for multi-operation ambiguity"}
    elif verb == "gis_buffer":
        projected(d)
        require(type(p["distance"]) in (int, float) and math.isfinite(p["distance"]), message="distance 须为有限数值")
        require(p["unit"] == "m" and isinstance(p["dissolve"], bool), message="unit 必须为 m；dissolve 须为布尔值")
        gs = geometries(d)
        require(p["distance"] != 0, "NOT_SUPPORTED", "零距离退化域仍为 C07 provisional，本地不作等价承诺")
        require(p["distance"] > 0 or all(g.geom_type in ("Polygon", "MultiPolygon") for g in gs), message="负距离只适用于面")
        buffered = [shapely.buffer(g, p["distance"] / crs["metres_per_unit"], quad_segs=8, cap_style="round", join_style="round") for g in gs]
        if p["dissolve"]:
            gj = collection([feature(multipolygon(shapely.union_all(buffered)), {})] if gs else [])
            extra["dropped_fields"] = [f["name"] for f in schema]
            schema = []
        else:
            gj = collection([feature(multipolygon(g), f["properties"]) for f, g in zip(gj["features"], buffered)])
        extra["buffer_parameters"] = {"quad_segs": 8, "cap_style": "round", "join_style": "round"}
    elif verb in ("gis_clip", "gis_overlay"):
        other = datasets[1]
        same_crs(d, other)
        projected(d)
        polygonal(d)
        polygonal(other)
        boundary = shapely.union_all(geometries(other))
        operation = "intersection" if verb == "gis_clip" else p["operation"]
        require(operation in ("intersection", "difference"), "NOT_SUPPORTED", "本地叠加仅支持 intersection / difference，保留输入侧属性")
        fs = []
        for f, geom in zip(gj["features"], geometries(d)):
            result = getattr(geom, operation)(boundary)
            # Touching boundaries can yield lower-dimensional geometries. Keep only
            # polygonal components, as explicitly declared by this local route.
            if result.geom_type == "GeometryCollection":
                result = shapely.union_all([g for g in result.geoms if g.geom_type in ("Polygon", "MultiPolygon")])
            if not result.is_empty and result.geom_type in ("Polygon", "MultiPolygon"):
                fs.append(feature(multipolygon(result), f["properties"]))
        gj = collection(fs)
        extra["polygon_only"] = True
    elif verb == "gis_dissolve":
        projected(d)
        polygonal(d)
        names = {f["name"] for f in schema}
        require(set(p["fields"]).issubset(names), "SCHEMA_MISMATCH", "分组字段不存在", missing=sorted(set(p["fields"]) - names))
        selected = [next(f for f in schema if f["name"] == key) for key in p["fields"]]
        require(all(f["type"] in ("string", "integer", "boolean") for f in selected), "NOT_SUPPORTED",
                "本地融合暂限字符串、整数、布尔和 null 分组；浮点键 D06 尚未裁决")
        groups = {}
        for f, geom in zip(gj["features"], geometries(d)):
            values = [f["properties"][key] for key in p["fields"]]
            key = exact_key(values)
            groups.setdefault(key, {"values": values, "geometries": [], "source_records": []})
            groups[key]["geometries"].append(geom)
            groups[key]["source_records"].append(f["id"])
        fs, manifest = [], []
        for _, group in sorted(groups.items()):
            result = multipolygon(shapely.union_all(group["geometries"]))
            props = dict(zip(p["fields"], group["values"]))
            fs.append(feature(result, props))
            manifest.append({"group": props, "source_records": group["source_records"],
                             "source_count": len(group["source_records"]), "output_record": len(fs)})
        extra.update(group_manifest=manifest, group_count=len(fs),
                     dropped_fields=[f["name"] for f in schema if f["name"] not in p["fields"]],
                     dropped_business_keys=[], business_key_policy="not declared in local schema; all dropped fields require review")
        gj, schema = collection(fs), selected
        checks["one_record_per_group"] = len(gj["features"]) == len(groups)
        checks["all_records_accounted"] = sum(x["source_count"] for x in manifest) == d["feature_count"]
    else:
        raise GISFault("NOT_SUPPORTED", "未实现的本地计算路线")
    if verb not in ("gis_assign_projection",):
        geometries(d)
    return {"geojson": gj, "schema": schema, "crs": crs, "checks": checks, **extra}
