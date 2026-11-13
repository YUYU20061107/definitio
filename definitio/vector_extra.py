"""Additional vector paths; all proposals preserve the input snapshots."""
from __future__ import annotations

import base64
import copy
import json

from shapely.geometry import shape

from .model import GISFault, digest, require, validate_geojson


SPECS = {
    "gis_delete_field": ("V07", "预检后删除副本字段", "R3", ["dataset", "target", "fields", "target_echo", "preflight_id"], ["dataset"]),
    "gis_rename_dataset": ("V10", "变更逻辑名称", "R2", ["dataset", "new_name"], ["dataset"]),
    "gis_append_dataset": ("V13", "向既有逻辑目标追加版本", "R2", ["source", "target"], ["source", "target"]),
    "gis_import_data": ("V17", "导入显式数据 URI 快照", "R1", ["source_uri", "target_name", "format", "kind"], []),
    "gis_spatial_join": ("V22", "确定性空间连接", "R1", ["input", "join", "target", "predicate", "summary"], ["input", "join"]),
}


def write_target(verb, p):
    if verb == "gis_export_map_image":
        return p.get("target_path")
    if verb == "gis_run_processing":
        return p["params"]["OUTPUT"]
    if verb == "gis_rename_dataset":
        return p["new_name"]
    if verb == "gis_import_data":
        return p["target_name"]
    return p.get("target")


def compute(verb, p, datasets):
    from .operations import collection, geometries, same_crs
    if verb == "gis_import_data":
        require(p["format"] == "geojson" and p["kind"] == "vector", "NOT_SUPPORTED", "仅支持携带完整 CRS/schema/来源许可声明的 GeoJSON 数据 URI")
        prefix = "data:application/geo+json;base64,"
        require(p["source_uri"].startswith(prefix), "NOT_SUPPORTED", "本地导入仅接受内联固定 data URI；不访问路径或网络")
        require(len(p["source_uri"]) <= 8 * 1024 * 1024, message="导入 URI 超过 8 MB")
        try:
            raw = base64.b64decode(p["source_uri"][len(prefix):], validate=True)
            document = json.loads(raw)
        except (ValueError, UnicodeError) as exc:
            raise GISFault("INVALID_ARGUMENT", "数据 URI 不是有效的 UTF-8 GeoJSON") from exc
        meta = document.get("definitio", {})
        license_info = meta.get("source_license", {})
        require(license_info.get("allow_import") is True and isinstance(license_info.get("evidence_ref"), str) and bool(license_info["evidence_ref"]),
                "LICENSE_BLOCKED", "须显式声明来源允许导入及其证据引用；不推断外部许可")
        require("schema" in meta, "SCHEMA_MISMATCH", "来源须显式携带 schema")
        gj, crs, schema = validate_geojson(document, meta.get("crs"), meta["schema"])
        return {"geojson": gj, "crs": crs, "schema": schema,
                "source_fingerprint": digest(document), "source_license": license_info,
                "license_verification": "caller_attested_local_source; no remote license verification",
                "external_source_unchanged": True, "effective_format": "geojson"}
    require(all(d.get("kind", "vector") == "vector" for d in datasets), "NOT_SUPPORTED", "此路径仅支持二维矢量")
    d = datasets[0]
    gj, schema, crs = copy.deepcopy(d["geojson"]), copy.deepcopy(d["fields"]), d["crs"]
    if verb == "gis_delete_field":
        require(p["target_echo"] == p["target"], "CONFIRM_REQUIRED", "target_echo 必须逐字回显新目标")
        names = {f["name"] for f in schema}
        require(bool(p["fields"]) and set(p["fields"]).issubset(names), "SCHEMA_MISMATCH", "待删字段不存在或为空")
        require(not set(p["fields"]) & {"id", "fid", "FID", "OBJECTID", "geometry"}, message="系统/几何字段不可删除")
        schema = [f for f in schema if f["name"] not in p["fields"]]
        for f in gj["features"]:
            f["properties"] = {s["name"]: f["properties"][s["name"]] for s in schema}
        return {"geojson": gj, "schema": schema, "crs": crs, "removed_fields": p["fields"],
                "dependency_precheck": "flat scalar snapshot; no indexes/domains/joins or declared business keys",
                "schema_hash_before": digest(d["fields"]), "schema_hash_after": digest(schema)}
    if verb == "gis_rename_dataset":
        require(p["new_name"] != d.get("logical_name", d["name"]), message="新名称与当前名称相同")
        return {"lifecycle": "rename", "result": {"logical_change": "rename", "name_before": d.get("logical_name", d["name"]),
                "name_after": p["new_name"], "content_unchanged": True,
                "warnings": ["Existing ID references stay valid; the old logical name no longer resolves"]}}
    if verb == "gis_append_dataset":
        target = datasets[1]
        require(d["id"] != target["id"], message="源与追加目标不能是同一对象")
        same_crs(d, target)
        require(d["fields"] == target["fields"], "SCHEMA_MISMATCH", "追加要求字段顺序/类型/可空性相同")
        geometries(d); geometries(target)
        require(d["geometry_types"] == target["geometry_types"] or not d["feature_count"] or not target["feature_count"], "DIMENSION_MISMATCH", "追加要求几何基类型相同")
        gj = collection(copy.deepcopy(target["geojson"]["features"] + d["geojson"]["features"]))
        return {"geojson": gj, "schema": schema, "crs": crs, "lifecycle": "append",
                "logical_change": "append", "before_count": target["feature_count"], "added_count": d["feature_count"],
                "after_count": len(gj["features"]), "previous_target_ref": target["id"],
                "target_version_changed": True, "source_unchanged": True,
                "version_policy": "logical head advances atomically; old ID remains an immutable historical snapshot"}
    same_crs(d, datasets[1])
    left, right = geometries(d), geometries(datasets[1])
    require(p["predicate"] != "nearest" or crs["projected"], "NOT_SUPPORTED", "nearest 仅支持投影平面距离；地理坐标测地路线未实现")
    fields, names = [], {s["name"] for s in schema}
    for spec in datasets[1]["fields"]:
        name = spec["name"]
        if name in names:
            name = "join_" + name
        require(name not in names, "SCHEMA_MISMATCH", "连接字段确定性前缀仍冲突，须先显式整理字段", field=name)
        names.add(name)
        fields.append((spec["name"], name))
        schema.append({**spec, "name": name, "nullable": True})
    output, matched, unmatched, multi = [], 0, 0, 0
    for f, g in zip(gj["features"], left):
        require(not g.is_empty or p["predicate"] != "nearest", "GEOMETRY_INVALID", "nearest 不接受空几何")
        candidates = []
        for j, h in enumerate(right):
            if p["predicate"] == "nearest":
                require(not h.is_empty, "GEOMETRY_INVALID", "nearest 不接受空几何")
                candidates.append((g.distance(h), j))
            elif getattr(g, p["predicate"])(h):
                intersection = g.intersection(h)
                candidates.append(((-intersection.area, -intersection.length), j))
        if p["predicate"] == "nearest" and candidates:
            minimum = min(x[0] for x in candidates)
            candidates = [x for x in candidates if x[0] == minimum]
        if candidates:
            matched += 1
            multi += len(candidates) > 1
            if p["summary"] == "first":
                candidates.sort(key=lambda x: x[0])
                require(len(candidates) == 1 or candidates[0][0] != candidates[1][0], "SCHEMA_MISMATCH", "连接候选并列且本地 schema 没有声明稳定业务键，无法确定性选择")
                candidates = candidates[:1]
        else:
            unmatched += 1
            candidates = [(None, None)]
        for _, index in candidates:
            props = copy.deepcopy(f["properties"])
            joined = datasets[1]["geojson"]["features"][index]["properties"] if index is not None else {}
            props.update({new: joined.get(old) for old, new in fields})
            output.append({**f, "properties": props})
    return {"geojson": collection(output), "schema": schema, "crs": crs,
            "matched_count": matched, "unmatched_count": unmatched, "multi_match_count": multi,
            "tie_break": "overlap area then length; tied first candidates rejected without declared business key",
            "distance_method": "projected_planar" if p["predicate"] == "nearest" else None}
