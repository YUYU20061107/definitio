"""Single verifier implementation.

Operators compute, this module judges, runtime/commit/resume decide policy.
Every predicate reports what was checked, what was found and what cannot be
confirmed. There is no blanket verified=true.
"""
from __future__ import annotations

import math
from collections import defaultdict

import shapely
from shapely.geometry import shape
from shapely.validation import explain_validity

from .model import GISFault, digest, logical_fingerprint, require
from .operations import exact_key, geometries

VERIFICATION_FORMAT = "definitio-verification-v1"
CONTEXT_KEYS = ("stage", "verb", "vid", "parameters", "inputs", "proposal", "output", "store", "db",
                "planned_inputs", "write_scope", "actual_writes", "authorization", "workspace", "file_artifact")
DISTANCE_VERBS = ("gis_buffer", "gis_measure")


def context(**kwargs):
    missing = [key for key in CONTEXT_KEYS if key not in kwargs]
    require(not missing, message="验证上下文缺少必需键", missing=missing)
    return {key: kwargs[key] for key in CONTEXT_KEYS}


def finding(predicate, checked, found, unconfirmed, passed, method, **details):
    return {"predicate": predicate, "checked": checked, "found": found, "unconfirmed": unconfirmed,
            "passed": passed, "method": method, "details": details}


def distance_unit_compatibility(ctx):
    name = "distance_unit_compatibility"
    verb, parameters, inputs = ctx["verb"], ctx["parameters"], ctx["inputs"]
    if verb not in DISTANCE_VERBS:
        return finding(name, f"{verb} 不是距离类操作", "不适用；未做单位断言",
                       "该动词的距离语义不在本批谓词范围内", True, "not_applicable", applicable=False)
    crs = inputs[0]["crs"] if inputs else (ctx["proposal"] or {}).get("crs")
    if crs is None:
        return finding(name, "距离类操作的 CRS 与请求单位", "无输入 CRS 可核对",
                       "缺少输入快照，无法确认单位相容", None, "insufficient_context", applicable=False)
    requested = parameters.get("unit", "m")
    if verb == "gis_measure":
        compatible = bool(crs["projected"]) and bool(crs["metres_per_unit"])
        subject = f"gis_measure 的投影平面量测与 CRS {crs['code']} 的线性单位 {crs['unit']}"
    else:
        compatible = bool(crs["projected"]) and requested == "m" and bool(crs["metres_per_unit"])
        subject = f"{verb} 的请求单位 {requested} 与输入 CRS {crs['code']} 的线性单位 {crs['unit']}"
    return finding(name, subject,
                   "相容：投影平面、米制请求、换算因子存在" if compatible
                   else f"不相容：projected={crs['projected']}，requested_unit={requested}，metres_per_unit={crs['metres_per_unit']}",
                   "测地精度与基准变换歧义未校准；本谓词只判单位与计算方式相容",
                   compatible, "crs_linear_unit_and_request_unit_comparison",
                   crs_projected=bool(crs["projected"]), crs_unit=crs["unit"],
                   metres_per_unit=crs["metres_per_unit"], requested_unit=requested, crs_code=crs["code"])


def version_status(store, ref, db=None):
    """Compare a planned input binding against the record actually stored right now."""
    from .model import snapshot_fingerprint
    try:
        document = store.dataset(ref["id"], db)
    except GISFault:
        return {"id": ref["id"], "planned": ref["fingerprint"], "actual": None, "changed": True,
                "reason": "input_object_missing"}
    try:
        actual = snapshot_fingerprint(document)
    except GISFault:
        actual = None
    record = digest({key: value for key, value in document.items() if key != "logical_name"})
    reasons = []
    if actual != ref["fingerprint"]:
        reasons.append("logical_fingerprint_changed")
    if ref.get("record_digest") is not None and record != ref["record_digest"]:
        reasons.append("record_digest_changed")
    return {"id": ref["id"], "planned": ref["fingerprint"], "actual": actual, "changed": bool(reasons),
            "reason": ",".join(reasons) or None, "planned_record_digest": ref.get("record_digest"),
            "actual_record_digest": record}


def input_version_binding(ctx):
    name = "input_version_binding"
    planned, store = ctx["planned_inputs"], ctx["store"]
    if not planned:
        return finding(name, "计划绑定的输入版本集", "本步骤无外部输入版本绑定（如导入）",
                       "无绑定即无从核对；不因此判定为已验证", True, "no_planned_binding", changed=[])
    if store is None:
        return finding(name, "计划绑定的输入版本集", "无工作区连接，无法读取实际版本",
                       "预演/离线上下文不能确认持久层版本", None, "insufficient_context", changed=[])
    statuses = [version_status(store, ref, ctx["db"]) for ref in planned]
    changed = [{"id": s["id"], "planned": s["planned"], "actual": s["actual"]} for s in statuses if s["changed"]]
    return finding(name, f"{len(planned)} 个计划绑定输入的指纹与记录摘要",
                   "全部仍与计划依据一致" if not changed else f"{len(changed)} 个输入版本已变化",
                   "引擎外的进程改动无法从本工作区证明", not changed,
                   "database_readback_logical_sha256", changed=changed,
                   reasons=[{"id": s["id"], "reason": s["reason"]} for s in statuses if s["changed"]])


def spatial_join_contract(ctx):
    name = "spatial_join_contract"
    if ctx["verb"] != "gis_spatial_join":
        return finding(name, "空间连接的键、字段与集合关系", "不适用", "非空间连接动词", True,
                       "not_applicable", applicable=False)
    parameters, inputs, output = ctx["parameters"], ctx["inputs"], ctx["output"]
    proposal = ctx["proposal"] or {}
    problems, kinds = [], []

    def problem(kind, text):
        kinds.append(kind)
        problems.append(text)

    if len(inputs) != 2:
        problem("object_type", f"需要 2 个输入，实际 {len(inputs)}")
    for d in inputs:
        if d.get("kind", "vector") != "vector":
            problem("object_type", f"输入 {d['id']} 类型为 {d.get('kind')}，空间连接只接受矢量")
    if len(inputs) == 2:
        if inputs[0]["crs"]["definition_hash"] != inputs[1]["crs"]["definition_hash"]:
            problem("crs_mismatch", "两侧 CRS 不同")
        if parameters.get("predicate") == "nearest" and not inputs[0]["crs"]["projected"]:
            problem("not_supported", "nearest 只支持投影平面距离")
    if output is not None and len(inputs) == 2 and not problems:
        left = {f["name"] for f in inputs[0]["fields"]}
        expected = set(left)
        for spec in inputs[1]["fields"]:
            expected.add(spec["name"] if spec["name"] not in left else "join_" + spec["name"])
        actual = {f["name"] for f in output["fields"]}
        if actual != expected:
            problem("schema", f"输出字段集与预期不符：missing={sorted(expected - actual)} unexpected={sorted(actual - expected)}")
        if parameters.get("summary") == "all" and output["feature_count"] < inputs[0]["feature_count"]:
            problem("count", "summary=all 的输出记录数少于左输入记录数")
        if parameters.get("summary") == "first" and "matched_count" in proposal:
            if output["feature_count"] != proposal["matched_count"] + proposal["unmatched_count"]:
                problem("count", "summary=first 的记录数与 matched/unmatched 计数不一致")
    return finding(name, f"predicate={parameters.get('predicate')} summary={parameters.get('summary')} 的键/字段/集合关系",
                   "符合契约" if not problems else "；".join(problems),
                   "并列候选的业务键稳定性未声明，无法证明确定性选择", not problems,
                   "schema_and_count_set_comparison", problems=problems, problem_kinds=kinds,
                   matched_count=proposal.get("matched_count"), unmatched_count=proposal.get("unmatched_count"))


def output_reopenable(ctx):
    name = "output_reopenable"
    output, store, proposal = ctx["output"], ctx["store"], ctx["proposal"]
    if output is None:
        if ctx["stage"] == "preview":
            return finding(name, "输出可重新打开且结构合预期", "预演不落库，无持久输出可重开",
                           "内存预演不能确认持久化后可重开", None, "in_memory_only")
        return finding(name, "输出可重新打开且结构合预期", "本步骤为只读，无数据集输出",
                       "只读结果的重开由工件 URI 承担", True, "read_only_step")
    if ctx["stage"] == "preview":
        problems = []
        if proposal is not None and "geojson" in proposal:
            if output["fields"] != proposal["schema"]:
                problems.append("预演输出字段结构与提案不符")
            if output["crs"]["definition_hash"] != proposal["crs"]["definition_hash"]:
                problems.append("预演输出 CRS 与提案不符")
            if output["fingerprint"] != logical_fingerprint(proposal["geojson"], proposal["crs"], proposal["schema"]):
                problems.append("预演输出逻辑指纹与提案不一致")
        return finding(name, "内存预演输出的结构核对", "结构合预期" if not problems else "；".join(problems),
                       "内存预演不能确认持久化后可重开", not problems, "in_memory_structural_readback",
                       problems=problems)
    if store is None:
        return finding(name, "输出可重新打开且结构合预期", "无工作区连接", "无法重开持久记录", None,
                       "insufficient_context")
    reopened = store.dataset(output["id"], ctx["db"])
    actual = store.verify_snapshot(reopened)
    problems = []
    if actual != output["fingerprint"]:
        problems.append("重开指纹与提交指纹不一致")
    if proposal is not None and "geojson" in proposal:
        if reopened["fields"] != proposal["schema"]:
            problems.append("重开字段结构与预期不符")
        if reopened["crs"]["definition_hash"] != proposal["crs"]["definition_hash"]:
            problems.append("重开 CRS 与预期不符")
        if actual != logical_fingerprint(proposal["geojson"], proposal["crs"], proposal["schema"]):
            problems.append("重开逻辑指纹与提案不一致")
    if "semantic" not in reopened:
        problems.append("重开记录缺少语义对象头")
    return finding(name, f"重新打开 {output['id']} 并核对指纹/字段/CRS/语义头",
                   "结构合预期" if not problems else "；".join(problems),
                   "读回核对不是独立几何 oracle，也不证明跨后端等价", not problems,
                   "database_readback_logical_sha256", problems=problems, fingerprint=actual)


def write_scope(ctx):
    name = "write_scope"
    scope, actual = ctx["write_scope"], ctx["actual_writes"]
    if scope is None:
        return finding(name, "实际写入范围在任务与授权内", "无声明写入范围",
                       "未绑定范围时不能声称写入合规", None, "no_declared_scope")
    allowed = {(target.get("kind", "dataset"), target["target"]) for target in scope.get("dataset_targets", [])}
    allowed |= {("file", artifact["name"]) for artifact in scope.get("file_artifacts", [])}
    out = [write for write in actual if (write["kind"], write["target"]) not in allowed]
    auth = ctx["authorization"] or {}
    # RT-04: the real grant prefix/capabilities are carried in governance_state,
    # not inferred from the plan's own exports or plan_digest.
    prefix = auth.get("grant_prefix") or auth.get("target_prefix")
    if prefix:
        out += [write for write in actual if not write["target"].startswith(prefix)]
    caps = auth.get("grant_capabilities")
    if caps is not None:
        for write in actual:
            required = {"file": "file_export", "external": "external_publish"}.get(write["kind"], "dataset")
            if required not in caps:
                out.append({**write, "reason": "capability_not_granted", "required_capability": required})
        declared = sum(1 for _ in scope.get("file_artifacts", []))
        if declared and "file_export" in caps and auth.get("grant_max_file_exports") is not None:
            if declared > auth["grant_max_file_exports"]:
                out.append({"kind": "file", "target": "*", "reason": "file_export_budget_exceeded",
                            "required_capability": "file_export", "declared": declared,
                            "granted": auth["grant_max_file_exports"]})
    return finding(name, f"{len(actual)} 项实际写入对 {len(allowed)} 项声明范围与授权能力",
                   "全部在任务与授权范围内" if not out else f"{len(out)} 项越界",
                   "宿主自身的授权政策不在本引擎可证明范围内；声明范围不等于用户授权", not out,
                   "declared_scope_plus_grant_capability_comparison", out_of_scope=out, actual_writes=actual,
                   capabilities=caps)


class _InvariantStop(Exception):
    def __init__(self, name):
        super().__init__(name)
        self.name = name


def operation_readback_invariants(ctx):
    """Operator-specific readback invariants for all four data kinds.

    The vector proposal-layer checks are migrated verbatim from verification.py.
    Raster/pointcloud/resource/lifecycle readback lists that used to be inline in
    runtime are judged here, by the single verifier. A check may be True, False
    (hard stop) or None (honestly unconfirmed; never a fake pass).
    """
    name = "operation_readback_invariants"
    verb, parameters, inputs = ctx["verb"], ctx["parameters"], ctx["inputs"]
    output, proposal, store = ctx["output"], ctx["proposal"], ctx["store"]
    if output is None and proposal is None:
        return finding(name, "算子级读回不变量", "无输出也无提案可比对（只读复核路径）",
                       "原始提案不保留在工件中；提交时的判定见 evidence.postcondition_checks",
                       True, "not_applicable", checks=[], failed_check=None, unconfirmed_checks=[])
    checks, unconfirmed = [], []

    def check(label, passed, method="readback_exact_structure", **details):
        entry = {"id": label, "passed": None if passed is None else bool(passed), "method": method}
        if details:
            entry["details"] = details
        checks.append(entry)
        if passed is not None and not passed:
            raise _InvariantStop(label)

    def unconfirm(label, reason, method="insufficient_context"):
        checks.append({"id": label, "passed": None, "method": method})
        unconfirmed.append({"id": label, "reason": reason})

    persisted = store is not None and ctx["stage"] != "preview"
    stopped = None
    try:
        if proposal is not None and "geojson" in proposal and output is not None:
            check("schema_readback", output["fields"] == proposal["schema"])
            check("crs_readback", output["crs"]["definition_hash"] == proposal["crs"]["definition_hash"])
            if verb in ("gis_copy_dataset", "gis_convert_format"):
                check("copy_logical_content", output["fingerprint"] == inputs[0]["fingerprint"])
            if verb == "gis_merge_datasets":
                check("merge_count_readback", output["feature_count"] == sum(d["feature_count"] for d in inputs))
            if verb == "gis_append_dataset":
                check("append_count_readback", output["feature_count"] == sum(d["feature_count"] for d in inputs))
                expected = inputs[1]["geojson"]["features"] + inputs[0]["geojson"]["features"]
                check("append_multiset_readback", output["fingerprint"] == logical_fingerprint({"features": expected}, output["crs"], output["fields"]))
            if verb == "gis_delete_field":
                check("removed_fields_absent", not set(parameters["fields"]) & {f["name"] for f in output["fields"]})
                check("delete_field_count", output["feature_count"] == inputs[0]["feature_count"])
            if verb == "gis_dissolve":
                source = inputs[0]
                fields = parameters["fields"]
                expected_schema = [next(f for f in source["fields"] if f["name"] == key) for key in fields]
                check("dissolve_fields_only", output["fields"] == expected_schema)
                groups, records = defaultdict(list), defaultdict(list)
                for f, geom in zip(source["geojson"]["features"], geometries(source)):
                    key = exact_key([f["properties"][k] for k in fields])
                    groups[key].append(geom)
                    records[key].append(f["id"])
                actual = {}
                for f in output["geojson"]["features"]:
                    key = exact_key([f["properties"][k] for k in fields])
                    check("unique_output_group", key not in actual)
                    actual[key] = shape(f["geometry"])
                check("group_key_set_readback", set(actual) == set(groups))
                check("group_count_readback", len(actual) == output["feature_count"] == len(groups))
                for key, source_geometries in groups.items():
                    geom = actual[key]
                    check("multipolygon_per_group", geom.geom_type == "MultiPolygon")
                    check("group_geometry_readback", geom.equals(shapely.union_all(source_geometries)), "same_kernel_geos_topological_equals")
                manifest = proposal.get("group_manifest", [])
                manifest_keys = [exact_key([entry["group"][k] for k in fields]) for entry in manifest]
                check("manifest_group_keys", len(manifest_keys) == len(set(manifest_keys)) and set(manifest_keys) == set(groups))
                check("manifest_membership", all(sorted(entry["source_records"]) == sorted(records[key]) for key, entry in zip(manifest_keys, manifest)))
        if proposal is not None and proposal.get("lifecycle") == "destroy":
            if not persisted:
                unconfirm("dataset_absent_readback", "预演不销毁，持久层缺席无法核对")
            else:
                try:
                    store.dataset(inputs[0]["id"], ctx["db"])
                    absent = False
                except GISFault:
                    absent = True
                check("dataset_absent_readback", absent, "database_readback_absence")
        if output is not None and proposal is not None and proposal.get("lifecycle") == "rename":
            check("rename_content_unchanged", output["fingerprint"] == inputs[0]["fingerprint"])
            if not persisted:
                unconfirm("rename_head_resolves", "预演不落库，逻辑头解析无法核对")
                unconfirm("rename_old_name_gone", "预演不落库，旧名注销无法核对")
            else:
                head = store.dataset(parameters["new_name"], ctx["db"])
                check("rename_head_resolves", head["id"] == inputs[0]["id"])
                old_name = inputs[0].get("logical_name") or inputs[0]["name"]
                try:
                    store.dataset(old_name, ctx["db"])
                    gone = False
                except GISFault:
                    gone = True
                check("rename_old_name_gone", gone, "database_readback_absence")
        if output is not None:
            if "resource" in output:
                _resource_readback(ctx, output, proposal, persisted, check, unconfirm)
            elif output.get("kind") == "raster":
                _raster_readback(ctx, output, proposal, persisted, check, unconfirm)
            elif output.get("kind") == "pointcloud":
                _pointcloud_readback(ctx, output, proposal, persisted, check, unconfirm)
            elif "geojson" in output:
                _vector_semantic_checks(ctx, output, proposal, check, unconfirm)
        if output is None and proposal is not None and "result" in proposal:
            _result_checks(ctx, proposal, check, unconfirm)
    except _InvariantStop as exc:
        stopped = exc.name
    except GISFault as exc:
        stopped = exc.code
        checks.append({"id": exc.code, "passed": False, "method": "geometry_readback"})
    limit = "GEOS 谓词与计算内核同源，不是独立几何 oracle"
    if unconfirmed:
        limit += "；" + "；".join(f"{item['id']}：{item['reason']}" for item in unconfirmed)
    return finding(name, f"{verb} 的字段/CRS/计数/分组/四类数据读回不变量",
                   "全部不变量成立" if stopped is None else f"不变量 {stopped} 不成立",
                   limit, stopped is None,
                   "readback_exact_structure", checks=checks, failed_check=stopped,
                   unconfirmed_checks=unconfirmed)


def _resource_readback(ctx, output, proposal, persisted, check, unconfirm):
    store, inputs, parameters = ctx["store"], ctx["inputs"], ctx["parameters"]
    if not persisted:
        unconfirm("native_resource_readback", "预演不落库，资源记录无法重开")
    else:
        readback = store.dataset(output["id"], ctx["db"])
        check("native_resource_readback", store.verify_snapshot(readback) == output["fingerprint"],
              "native_backend_and_sqlite_sha256")
    if proposal is not None and proposal.get("lifecycle") == "map_update":
        before = inputs[1]["resource"]["layers"]
        after = output["resource"]["layers"]
        added = after[-1] if after else {}
        expected_ref = inputs[0]["resource"]["storage"]["uri"]
        check("resource_map_layers_accounting",
              len(after) == len(before) + 1 and after[:-1] == before
              and added.get("dataset_ref") == expected_ref
              and added.get("catalog_id") == inputs[0]["id"]
              and added.get("license_ref") == inputs[0]["resource"]["evidence_ref"],
              "resource_layer_binding_readback")
    if output.get("kind") == "image":
        import base64
        import hashlib
        raw = base64.b64decode(output["resource"]["base64"], validate=True)
        check("resource_image_integrity",
              hashlib.sha256(raw).hexdigest() == output["resource"]["file_sha256"]
              and output["resource"]["width"] == inputs[0]["resource"]["width"]
              and output["resource"]["height"] == inputs[0]["resource"]["height"]
              and output["resource"]["dpi"] == parameters["dpi"],
              "sha256_bytes_and_map_property_readback")


def _raster_readback(ctx, output, proposal, persisted, check, unconfirm):
    from .raster import fingerprint
    if persisted:
        readback = ctx["store"].dataset(output["id"], ctx["db"])
        check("grid_and_pixels_readback", readback["raster"] == output["raster"], "sqlite_exact_raster_snapshot")
    elif proposal is not None and "raster" in proposal:
        check("grid_and_pixels_readback", output["raster"] == proposal["raster"], "sqlite_exact_raster_snapshot")
    else:
        check("grid_and_pixels_readback", fingerprint(output["raster"], output["crs"]) == output["fingerprint"],
              "sqlite_exact_raster_snapshot")
    _raster_semantic_checks(ctx, output, proposal, check, unconfirm)


def _raster_semantic_checks(ctx, output, proposal, check, unconfirm):
    """Grid alignment, nodata accounting and deterministic pixel spot checks.

    Terrain spots use an independent numpy implementation of the published Horn
    3x3 formulas; GDAL computes in float32 (hillshade uint8), so tolerances are
    float32 rounding margins, not an independent oracle.

    RT-10 / GDAL 3.12.2 lock-in: hillshade is a uint8 traditional 1..255 image.
    shade = cos(zenith)*cos(slope) + sin(zenith)*sin(slope)*cos(azimuth-aspect),
    clamped to [0,1]; value = 1 + floor(254*shade + 0.5). Dark slopes truncate
    to 1 (they are NOT negative), NoData stays null. Pinned against 117 GDAL
    pixels in evidence/redteam-fix-20261116/gdal_hillshade_probe.json; no ±1
    tolerance widening is used."""
    import numpy as np
    verb, parameters, inputs = ctx["verb"], ctx["parameters"], ctx["inputs"]
    grid = output["grid"]
    data = output["raster"]["data"]
    valid = sum(v is not None for row in data for v in row)
    check("raster_nodata_accounting",
          grid["valid_pixel_count"] == valid and valid + grid["nodata_pixel_count"] == grid["width"] * grid["height"],
          "pixel_count_accounting")
    arr = np.array([[np.nan if v is None else v for v in row] for row in data], dtype=np.float64)
    if inputs[0].get("kind") == "pointcloud":
        unconfirm("raster_grid_alignment", "点云转栅格：网格由 PDAL Wrench 分辨率规则生成，无输入网格可对齐")
        unconfirm("raster_pixel_spot", "点云转栅格的像元真值依赖 PDAL 内部网格规则，本地无法独立复算")
        return
    source = inputs[0]
    src = np.array([[np.nan if v is None else v for v in row] for row in source["raster"]["data"]], dtype=np.float64)
    aligned = (grid["transform"] == source["raster"]["transform"]
               and grid["width"] == source["grid"]["width"] and grid["height"] == source["grid"]["height"])
    if verb == "gis_raster_calc":
        aligned = aligned and all(d["raster"]["transform"] == source["raster"]["transform"]
                                  and d["grid"]["width"] == grid["width"] and d["grid"]["height"] == grid["height"]
                                  for d in inputs[1:])
    check("raster_grid_alignment", aligned, "grid_property_exact_comparison")

    def spot_indices(mask, limit=16):
        rows, cols = np.where(mask)
        if len(rows) == 0:
            return []
        picks = np.linspace(0, len(rows) - 1, min(limit, len(rows))).astype(int)
        return [(int(rows[i]), int(cols[i])) for i in picks]

    if verb == "gis_raster_calc":
        arrays = [np.array([[np.nan if v is None else v for v in row] for row in d["raster"]["data"]], dtype=np.float64)
                  for d in inputs]
        mask = np.logical_or.reduce([np.isnan(a) for a in arrays])
        check("raster_nodata_mask_semantics", np.array_equal(np.isnan(arr), mask), "nodata_union_exact")
        ok = True
        for r, c in spot_indices(~mask):
            expected = _raster_expression(parameters["expression"], [a[r, c] for a in arrays])
            ok &= abs(arr[r, c] - expected) <= 1e-9 * max(1.0, abs(expected))
        check("raster_pixel_spot", ok, "same_grammar_independent_arithmetic_recompute")
    elif verb in ("gis_slope", "gis_aspect", "gis_hillshade"):
        cell_x, cell_y = grid["transform"][0], -grid["transform"][4]
        interior = np.isfinite(src[1:-1, 1:-1])
        for dr in range(3):
            for dc in range(3):
                interior &= np.isfinite(src[dr:dr + src.shape[0] - 2, dc:dc + src.shape[1] - 2])
        expected_mask = np.ones(src.shape, dtype=bool)
        expected_mask[1:-1, 1:-1] = ~interior
        if verb == "gis_aspect":
            dzdx = ((src[:-2, 2:] + 2 * src[1:-1, 2:] + src[2:, 2:])
                    - (src[:-2, :-2] + 2 * src[1:-1, :-2] + src[2:, :-2])) / (8 * cell_x)
            dzdy = ((src[2:, :-2] + 2 * src[2:, 1:-1] + src[2:, 2:])
                    - (src[:-2, :-2] + 2 * src[:-2, 1:-1] + src[:-2, 2:])) / (8 * cell_y)
            expected_mask[1:-1, 1:-1] |= (dzdx == 0) & (dzdy == 0)
        check("raster_nodata_mask_semantics", np.array_equal(np.isnan(arr), expected_mask), "horn_3x3_mask_exact")
        zf = parameters["z_factor"]
        ok = True
        interior_full = np.zeros(src.shape, dtype=bool)
        interior_full[1:-1, 1:-1] = interior
        for r, c in spot_indices(interior_full):
            w = src[r - 1:r + 2, c - 1:c + 2]
            dzdx = ((w[0, 2] + 2 * w[1, 2] + w[2, 2]) - (w[0, 0] + 2 * w[1, 0] + w[2, 0])) / (8 * cell_x)
            dzdy = ((w[2, 0] + 2 * w[2, 1] + w[2, 2]) - (w[0, 0] + 2 * w[0, 1] + w[0, 2])) / (8 * cell_y)
            slope = math.atan(zf * math.hypot(dzdx, dzdy))
            if verb == "gis_slope":
                expected = math.degrees(slope)
                ok &= abs(arr[r, c] - expected) <= 1e-6 * max(1.0, abs(expected))
            elif verb == "gis_aspect":
                if dzdx == 0 and dzdy == 0:
                    ok &= np.isnan(arr[r, c])
                else:
                    expected = math.degrees(math.atan2(-dzdx, dzdy)) % 360
                    diff = abs(arr[r, c] - expected)
                    ok &= min(diff, 360 - diff) <= 1e-4
            else:
                # Independent scalar formula for the pinned GDAL encoding.
                aspect = math.atan2(-dzdx, dzdy) % (2 * math.pi)
                zenith = math.radians(90 - parameters["altitude"])
                azimuth = math.radians(parameters["azimuth"])
                shade = (math.cos(zenith) * math.cos(slope)
                         + math.sin(zenith) * math.sin(slope) * math.cos(azimuth - aspect))
                expected = 1 + math.floor(254 * max(0.0, min(1.0, shade)) + 0.5)
                ok &= arr[r, c] == expected
        check("raster_pixel_spot", ok,
              "gdal_3_12_2_traditional_1_255_independent_scalar_exact" if verb == "gis_hillshade"
              else "same_formula_independent_horn_recompute_float32_margin")
    elif verb == "gis_reclassify":
        check("raster_nodata_mask_semantics", np.array_equal(np.isnan(arr), np.isnan(src)), "nodata_propagation_exact")
        rules = sorted(parameters["rules"], key=lambda r: (r["min"], r["max"]))
        ok = True
        for r, c in spot_indices(np.isfinite(src)):
            value = src[r, c]
            mapped = None
            for rule in rules:
                lo = value >= rule["min"] if rule["min_inclusive"] else value > rule["min"]
                hi = value <= rule["max"] if rule["max_inclusive"] else value < rule["max"]
                if lo and hi:
                    mapped = rule["value"]
                    break
            expected = mapped if mapped is not None else (value if parameters["unmapped"] == "keep" else np.nan)
            ok &= (np.isnan(arr[r, c]) if np.isnan(expected) else arr[r, c] == expected)
        check("raster_pixel_spot", ok, "constructed_rule_mapping_truth")


def _raster_expression(expression, values):
    """Independent evaluator for the published A..Z + - * / % raster grammar."""
    import ast
    import operator
    tree = ast.parse(expression, mode="eval")
    ops = {ast.Add: operator.add, ast.Sub: operator.sub, ast.Mult: operator.mul,
           ast.Div: operator.truediv, ast.Mod: operator.mod}
    names = {chr(65 + i): v for i, v in enumerate(values)}

    def visit(node):
        if isinstance(node, ast.Expression):
            return visit(node.body)
        if isinstance(node, ast.Constant) and type(node.value) in (int, float):
            return float(node.value)
        if isinstance(node, ast.Name) and node.id in names:
            return names[node.id]
        if isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.USub, ast.UAdd)):
            return (-1 if isinstance(node.op, ast.USub) else 1) * visit(node.operand)
        if isinstance(node, ast.BinOp) and type(node.op) in ops:
            return ops[type(node.op)](visit(node.left), visit(node.right))
        raise GISFault("INVALID_ARGUMENT", "栅格表达式语法超出已公布文法")
    return float(visit(tree))


def _pointcloud_semantic_checks(ctx, output, proposal, check, unconfirm):
    """Point count/dimension accounting against declared PDAL action semantics."""
    from .pointcloud import fingerprint
    parameters, inputs = ctx["parameters"], ctx["inputs"]
    action = parameters["action"]
    n_in = inputs[0]["point_count"]
    out_count = output["point_count"]
    check("pointcloud_dimensions_preserved", output["point_dimensions"] == inputs[0]["point_dimensions"],
          "dimension_list_readback")
    if action == "merge":
        check("pointcloud_count_accounting", out_count == sum(d["point_count"] for d in inputs),
              "constructed_sum_truth")
    elif action == "thin_decimate":
        step, offset = parameters["params"]["step"], parameters["params"]["offset"]
        check("pointcloud_count_accounting", out_count == len(range(offset, n_in, step)),
              "constructed_decimation_truth")
    elif action in ("reproject", "convert"):
        check("pointcloud_count_accounting", out_count == n_in, "count_preservation")
    elif action in ("clip", "thin_radius"):
        check("pointcloud_count_accounting", out_count <= n_in, "pdal_semantics_upper_bound")
    else:
        unconfirm("pointcloud_count_accounting", "该动作输出栅格/矢量，点数对账不适用")
    check("pointcloud_fingerprint_recomputed", fingerprint(output["pointcloud"], output["crs"]) == output["fingerprint"],
          "byte_sha256_binding")


def _pointcloud_readback(ctx, output, proposal, persisted, check, unconfirm):
    from .pointcloud import fingerprint
    if persisted:
        readback = ctx["store"].dataset(output["id"], ctx["db"])
        check("pointcloud_bytes_readback", readback["pointcloud"] == output["pointcloud"],
              "sqlite_exact_bytes_and_metadata")
    elif proposal is not None and "pointcloud" in proposal:
        check("pointcloud_bytes_readback", output["pointcloud"] == proposal["pointcloud"],
              "sqlite_exact_bytes_and_metadata")
    else:
        check("pointcloud_bytes_readback", fingerprint(output["pointcloud"], output["crs"]) == output["fingerprint"],
              "sqlite_exact_bytes_and_metadata")
    _pointcloud_semantic_checks(ctx, output, proposal, check, unconfirm)


def _parts(geom):
    """Non-empty constituent parts; empty parts contribute no buffer area."""
    if geom is None or geom.is_empty:
        return []
    if hasattr(geom, "geoms"):
        return [part for part in geom.geoms if not part.is_empty]
    return [geom]


def _flatten_parts(geoms):
    out = []
    for geom in geoms:
        out.extend(_parts(geom))
    return out


def _parallel_body_upper_bound(geom, radius):
    """RT-09: provable area upper bound SUMMED PER PART.

    For one part, area + 2*length*radius + pi*radius^2 bounds its round-cap /
    round-join buffer (exact for points and lines, conservative for polygons).
    Summing over parts covers MultiPoint, multi-part short lines, multi-part
    polygons and empty parts. Applicability: 2D projected geometry, radius > 0,
    quad_segs polygonal disc approximation (disc area <= pi*r^2). This is an
    analytic bound, not a fudge factor: tolerances are NOT enlarged to hide
    false rejections.
    """
    return sum(part.area + 2 * part.length * radius + math.pi * radius * radius
               for part in _parts(geom))


def _buffer_bounds_ok(actual, source, radius, tol):
    """Minkowski sum with a disc of radius r has an axis-aligned bounding box
    contained in source.bounds expanded by r (a provable spatial constraint)."""
    if source.is_empty:
        return actual[0] <= actual[2] and actual[1] <= actual[3]
    s = source.bounds
    return (actual[0] >= s[0] - radius - tol and actual[1] >= s[1] - radius - tol
            and actual[2] <= s[2] + radius + tol and actual[3] <= s[3] + radius + tol)


def _vector_semantic_checks(ctx, output, proposal, check, unconfirm):
    """Per-verb vector postconditions. Same-kernel recomputations are labelled as
    self-consistency checks, never as an independent geometry oracle."""
    import math
    from shapely.geometry import shape as _shape
    verb, parameters, inputs = ctx["verb"], ctx["parameters"], ctx["inputs"]
    feats = output["geojson"]["features"]
    geoms = [_shape(f["geometry"]) if f["geometry"] is not None else None for f in feats]

    if verb == "gis_buffer":
        # RT-01: area bounds alone never prove WHERE a buffer is, so add
        # provable containment, expansion, bounds, and same-kernel recomputation
        # checks. Type-specific branches keep point/line/polygon/multi-part honest.
        check("buffer_geometry_valid", all(g is not None for g in geoms), "same_kernel_geos_is_valid")
        crs = inputs[0]["crs"]
        r = parameters["distance"] / crs["metres_per_unit"]
        source_geoms = geometries(inputs[0])
        quad_segs = (proposal or {}).get("buffer_parameters", {}).get("quad_segs", 8)
        tol = max(1e-9, abs(r) * 1e-9)

        def same_kernel(expected, actual, label):
            delta = actual.symmetric_difference(expected).area if not actual.is_empty else math.inf
            check(label, delta <= 1e-6 * max(1.0, expected.area),
                  "same_kernel_geos_consistency_not_independent_oracle")

        if parameters["dissolve"]:
            check("buffer_count_semantics",
                  output["feature_count"] == (1 if source_geoms else 0) and output["fields"] == [],
                  "count_and_schema_set_comparison")
            if source_geoms and geoms and geoms[0] is not None:
                g = geoms[0]
                upper = sum(_parallel_body_upper_bound(src, r) for src in source_geoms) if r > 0 else None
                cover_area = shapely.union_all(source_geoms).area
                check("buffer_area_upper_bound", upper is None or g.area <= upper * (1 + 1e-9),
                      "analytic_parallel_body_upper_bound_per_part")
                # dissolve 输出是各源缓冲的并：正缓冲必覆盖源的并（面积下界），
                # 负缓冲必为其子集。此处与逐要素分支使用同一语义，不是凑绿别名。
                check("buffer_area_lower_bound",
                      (g.area >= cover_area * (1 - 1e-9)) if r > 0
                      else (0 <= g.area <= cover_area * (1 + 1e-9)),
                      "containment_exact" if r > 0 else "erosion_containment_exact")
                if r > 0:
                    check("buffer_positive_containment", all(g.covers(src) for src in _flatten_parts(source_geoms)),
                          "minkowski_containment_all_source_parts")
                    check("buffer_positive_expansion",
                          sum(g.difference(src).area for src in source_geoms) > 0,
                          "strict_outer_expansion")
                    expected = shapely.union_all([
                        shapely.buffer(src, r, quad_segs=quad_segs, cap_style="round", join_style="round")
                        for src in source_geoms])
                    same_kernel(expected, g, "buffer_same_kernel_recompute")
                else:
                    expected = shapely.union_all([
                        shapely.buffer(src, r, quad_segs=quad_segs, cap_style="round", join_style="round")
                        for src in source_geoms])
                    check("buffer_negative_erosion", g.area <= sum(src.area for src in source_geoms) * (1 + 1e-9),
                          "erosion_area_contraction")
                    same_kernel(expected, g, "buffer_same_kernel_recompute")
        else:
            check("buffer_count_semantics",
                  output["feature_count"] == inputs[0]["feature_count"]
                  and all(f["properties"] == s["properties"] for f, s in zip(feats, inputs[0]["geojson"]["features"])),
                  "count_and_properties_readback")
            for g, src in zip(geoms, source_geoms):
                if g is None:
                    continue
                family = ("point" if src.geom_type in ("Point", "MultiPoint") else
                          "line" if src.geom_type in ("LineString", "MultiLineString", "LinearRing") else "polygon")
                if r > 0:
                    upper = _parallel_body_upper_bound(src, r)
                    check("buffer_area_upper_bound", g.area <= upper * (1 + 1e-9),
                          "analytic_parallel_body_upper_bound_per_part", part_count=len(_parts(src)), family=family)
                    check("buffer_area_lower_bound", g.area >= src.area, "containment_exact")
                    check("buffer_positive_containment", bool(g.covers(src)), "minkowski_containment")
                    check("buffer_positive_expansion", g.difference(src).area > 0, "strict_outer_expansion")
                    check("buffer_expansion_bounds", _buffer_bounds_ok(g.bounds, src, r, tol),
                          "axis_aligned_minkowski_bound")
                    expected = shapely.buffer(src, r, quad_segs=quad_segs, cap_style="round", join_style="round")
                    same_kernel(expected, g, "buffer_same_kernel_recompute")
                else:
                    check("buffer_area_lower_bound", 0 <= g.area <= src.area, "erosion_containment_exact")
                    check("buffer_negative_containment", bool(src.covers(g)), "erosion_subset_of_input")
                    check("buffer_negative_contraction", g.area < src.area, "strict_inner_erosion")
                    expected = shapely.buffer(src, r, quad_segs=quad_segs, cap_style="round", join_style="round")
                    same_kernel(expected, g, "buffer_same_kernel_recompute")

    elif verb == "gis_spatial_join":
        # RT-03: build the complete match relation and bind EVERY output row to
        # left geometry, left attributes, right attributes and multiplicity.
        # An equal row count is not evidence of a correct join.
        left, right = geometries(inputs[0]), geometries(inputs[1])
        predicate, summary = parameters["predicate"], parameters["summary"]
        left_feats, right_feats = inputs[0]["geojson"]["features"], inputs[1]["geojson"]["features"]
        left_names = [s["name"] for s in inputs[0]["fields"]]
        seen = set(left_names)
        join_mapping = []
        for spec in inputs[1]["fields"]:
            new = spec["name"] if spec["name"] not in seen else "join_" + spec["name"]
            seen.add(new)
            join_mapping.append((spec["name"], new))
        if len(left) * len(right) > 20000:
            unconfirm("join_count_accounting", "输入对数超过 20000 的复算预算，无法逐对核对")
            unconfirm("join_row_binding", "输入对数超过复算预算，逐行绑定未核对")
        else:
            pairs = []
            for i, g in enumerate(left):
                candidates = []
                for j, h in enumerate(right):
                    if predicate == "nearest":
                        candidates.append((g.distance(h), j))
                    elif getattr(g, predicate)(h):
                        inter = g.intersection(h)
                        candidates.append(((-inter.area, -inter.length), j))
                if predicate == "nearest" and candidates:
                    minimum = min(x[0] for x in candidates)
                    candidates = [x for x in candidates if x[0] == minimum]
                if not candidates:
                    pairs.append((i, None))
                elif summary == "first":
                    ordered = sorted(candidates, key=lambda x: x[0])
                    if len(ordered) > 1 and ordered[0][0] == ordered[1][0]:
                        unconfirm("join_deterministic_first", "候选并列且 schema 未声明稳定业务键")
                    pairs.append((i, ordered[0][1]))
                else:
                    pairs.extend((i, j) for _, j in candidates)
            matched_left = len({i for i, j in pairs if j is not None})
            check("join_count_accounting", output["feature_count"] == len(pairs),
                  "complete_match_relation_row_multiplicity")
            if proposal is not None:
                check("join_proposal_counts",
                      proposal.get("matched_count") == matched_left
                      and proposal.get("unmatched_count") == len(left) - matched_left,
                      "same_kernel_predicate_recount")
            geometry_ok = left_attrs_ok = right_attrs_ok = True
            for row, (i, j) in enumerate(pairs[:len(feats)]):
                f = feats[row]
                geometry_ok &= geoms[row] is not None and bool(geoms[row].equals(left[i]))
                left_attrs_ok &= all(f["properties"].get(n) == left_feats[i]["properties"].get(n)
                                     for n in left_names)
                for old, new in join_mapping:
                    wanted = right_feats[j]["properties"].get(old) if j is not None else None
                    right_attrs_ok &= f["properties"].get(new) == wanted
            check("join_left_geometry_binding", geometry_ok, "row_to_left_geometry_identity")
            check("join_left_attributes_binding", left_attrs_ok, "row_to_left_attributes_identity")
            check("join_right_attributes_binding", right_attrs_ok,
                  "row_to_right_attributes_exact_values_not_merely_counts")
            check("join_summary_multiplicity",
                  all(sum(1 for p in pairs if p[0] == i) ==
                      (1 if summary == "first" else max(1, sum(1 for p in pairs if p[0] == i and p[1] is not None)))
                      for i in range(len(left))), "first_vs_all_row_multiplicity")
            if summary == "first":
                check("join_left_outer_nulls",
                      all(all(f["properties"].get(new) is None for _, new in join_mapping)
                          for row, (i, j) in enumerate(pairs[:len(feats)]) if j is None),
                      "left_outer_null_semantics_readback")

    elif verb in ("gis_clip", "gis_overlay"):
        # RT-02: bind rows by explicit source index / geometry identity, never by
        # an attribute subsequence (duplicate attribute values are legal). Both
        # input sides and result completeness are checked, including difference.
        operation = "intersection" if verb == "gis_clip" else parameters["operation"]
        source_feats, source_geoms = inputs[0]["geojson"]["features"], geometries(inputs[0])
        mask = shapely.union_all(geometries(inputs[1]))
        provenance = (proposal or {}).get("row_provenance") or []
        expected_parts, expected_indices = [], []
        for src_index, src in enumerate(source_geoms):
            piece = getattr(src, operation)(mask)
            if piece.geom_type == "GeometryCollection":
                piece = shapely.union_all([p for p in piece.geoms if p.geom_type in ("Polygon", "MultiPolygon")])
            if not piece.is_empty and piece.geom_type in ("Polygon", "MultiPolygon"):
                expected_parts.append(piece)
                expected_indices.append(src_index)
        expected_union = shapely.union_all(expected_parts) if expected_parts else None
        nonempty = [g for g in geoms if g is not None and not g.is_empty]
        out_union = shapely.union_all(nonempty) if nonempty else None
        complete = ((expected_union is None and out_union is None)
                    or (expected_union is not None and out_union is not None
                        and expected_union.symmetric_difference(out_union).area
                        <= 1e-6 * max(1.0, expected_union.area)))
        # Resolve every output row to ONE source row before judging semantics.
        # Explicit provenance wins; geometry identity is the fallback for
        # re-verification of older artifacts. Ambiguity is reported as
        # unconfirmed, never as a pass and never as an attribute-subsequence
        # guess (that guess is exactly the RT-02 false-rejection source).
        resolved = []
        for index in range(len(feats)):
            entry = provenance[index] if index < len(provenance) else None
            src_index = entry.get("source_index") if isinstance(entry, dict) else None
            if src_index is None or not (0 <= src_index < len(source_geoms)):
                out_g = geoms[index]
                covering = ([i for i, sg in enumerate(source_geoms)
                             if out_g is not None and not out_g.is_empty and sg.covers(out_g)]
                            if out_g is not None else [])
                src_index = covering[0] if len(covering) == 1 else None
            resolved.append(src_index)

        attrs_ok = attrs_known = True
        row_ok = subset_ok = mask_ok = area_ok = True
        for index, f in enumerate(feats):
            out_g, src_index = geoms[index], resolved[index]
            if out_g is None or out_g.is_empty:
                row_ok = subset_ok = mask_ok = False
                continue
            if src_index is None:
                attrs_known = False
                row_ok = subset_ok = mask_ok = area_ok = False
                continue
            src_g = source_geoms[src_index]
            props_ok = f["properties"] == source_feats[src_index]["properties"]
            covers_ok = bool(src_g.covers(out_g))
            attrs_ok &= props_ok
            row_ok &= covers_ok and props_ok
            subset_ok &= covers_ok
            area_ok &= out_g.area <= src_g.area * (1 + 1e-9)
            if operation == "intersection":
                mask_ok &= bool(mask.covers(out_g))
            else:
                mask_ok &= out_g.intersection(mask).area <= 1e-9 * max(1.0, out_g.area)
        count_ok = output["feature_count"] <= inputs[0]["feature_count"]

        # The four legacy check IDs remain genuine postconditions. They are now
        # computed from explicit row provenance rather than an attribute
        # subsequence, and they still name the classic "enlarged output" fault.
        check("attributes_retained", attrs_ok if attrs_known else None,
              "explicit_source_row_property_readback")
        check("count_bound", count_ok, "count_set_comparison")
        check("subset_area_bound", area_ok, "containment_area_exact")
        check("subset_within_input", subset_ok, "same_kernel_geos_covers")
        # RT-02 structural additions: completeness, row identity, mask side.
        check("clip_completeness", complete, "same_kernel_geos_recompute_not_independent_oracle")
        # The declared operator emits exactly one row per non-empty source row.
        # A duplicated row leaves the union/area/count bounds intact, so it needs
        # an explicit multiplicity check (found by the mutation suite).
        resolved_known = [i for i in resolved if i is not None]
        check("clip_row_multiplicity",
              len(resolved) == len(expected_indices)
              and len(resolved_known) == len(resolved)
              and sorted(resolved_known) == sorted(expected_indices),
              "one_output_row_per_nonempty_source_row")
        check("clip_source_row_binding", row_ok,
              "explicit_source_index_or_geometry_identity_not_attribute_subsequence")
        check("clip_mask_side_constraint", mask_ok, "mask_covers_intersection_or_difference_exclusion")

    elif verb == "gis_reproject":
        from rasterio.warp import transform_geom
        check("reproject_count_unchanged", output["feature_count"] == inputs[0]["feature_count"], "count_readback")
        check("reproject_attributes_unchanged",
              all(f["properties"] == s["properties"] for f, s in zip(feats, inputs[0]["geojson"]["features"])),
              "properties_readback")
        src_crs, dst_crs = inputs[0]["crs"]["wkt"], output["crs"]["wkt"]
        sample = feats[::max(1, len(feats) // 8)][:8]
        src_sample = inputs[0]["geojson"]["features"][::max(1, len(feats) // 8)][:8]
        # RT-11: one contract for empty collections / empty geometries / null
        # geometries, applied consistently on compute and verify sides. Empty
        # geometries keep their emptiness and attributes; they are never sent to
        # a coordinate round-trip or an extremum reduction over zero rows.
        worst, empty_ok, null_ok = 0.0, True, True
        for f, s in zip(sample, src_sample):
            if f["geometry"] is None or s["geometry"] is None:
                null_ok &= f["geometry"] is None and s["geometry"] is None
                continue
            f_geom, s_geom = _shape(f["geometry"]), _shape(s["geometry"])
            if f_geom.is_empty or s_geom.is_empty:
                empty_ok &= f_geom.is_empty and s_geom.is_empty
                continue
            back = transform_geom(dst_crs, src_crs, f["geometry"], precision=-1)
            a, b = _shape(back), s_geom
            ca, cb = shapely.get_coordinates(a), shapely.get_coordinates(b)
            if ca.shape != cb.shape:
                worst = float("inf")
                break
            scale = max(1.0, float(abs(cb).max()) if cb.size else 1.0)
            worst = max(worst, float(abs(ca - cb).max()) / scale)
        check("reproject_empty_geometry_contract", empty_ok,
              "empty_geometry_emptiness_and_attribute_preservation")
        check("reproject_null_geometry_contract", null_ok, "null_geometry_contract")
        check("reproject_roundtrip_spot", worst <= 1e-6, "same_kernel_proj_roundtrip")
        coords = shapely.get_coordinates([_shape(f["geometry"]) for f in inputs[0]["geojson"]["features"]
                                          if f["geometry"] is not None and not _shape(f["geometry"]).is_empty])
        if len(coords) == 0:
            check("reproject_bounds_consistency", output["bounds"] is None, "empty_bounds_readback")
        elif len(coords) <= 4096:
            moved = transform_geom(src_crs, dst_crs, {"type": "MultiPoint", "coordinates": coords.tolist()}, precision=-1)
            mc = shapely.get_coordinates(_shape(moved))
            expected = [mc[:, 0].min(), mc[:, 1].min(), mc[:, 0].max(), mc[:, 1].max()]
            span = max(1.0, expected[2] - expected[0], expected[3] - expected[1])
            actual = output["bounds"]
            check("reproject_bounds_consistency",
                  actual is not None and all(abs(a - e) <= 1e-6 * span for a, e in zip(actual, expected)),
                  "same_kernel_transformed_bounds")
        else:
            unconfirm("reproject_bounds_consistency", "输入坐标超过 4096 的抽样预算，无法证明极值边界")

    elif verb == "gis_merge_datasets":
        expected = [f for d in inputs for f in d["geojson"]["features"]]
        check("merge_fingerprint_multiset",
              output["fingerprint"] == logical_fingerprint({"features": expected}, output["crs"], output["fields"]),
              "sorted_multiset_logical_sha256")

    elif verb == "gis_append_dataset":
        if proposal is not None:
            check("append_version_accounting",
                  proposal.get("before_count") == inputs[1]["feature_count"]
                  and proposal.get("added_count") == inputs[0]["feature_count"]
                  and proposal.get("after_count") == output["feature_count"],
                  "proposal_count_readback")
        else:
            unconfirm("append_version_accounting", "复核路径无原始提案，版本计数以对账指纹为准")

    elif verb == "gis_assign_projection":
        def wkb_set(d):
            return sorted(shapely.normalize(_shape(f["geometry"])).wkb_hex for f in d["geojson"]["features"] if f["geometry"] is not None)
        check("assign_projection_coordinates_unchanged", wkb_set(output) == wkb_set(inputs[0]),
              "normalized_wkb_multiset_exact")

    elif verb == "gis_create_dataset":
        check("create_empty_contract", output["feature_count"] == 0 and output["fields"] == parameters["fields"],
              "empty_and_schema_readback")

    elif verb == "gis_import_data":
        import base64
        import json as _json
        prefix = "data:application/geo+json;base64,"
        raw = base64.b64decode(parameters["source_uri"][len(prefix):], validate=True)
        document = _json.loads(raw)
        meta = document.get("definitio", {})
        license_info = meta.get("source_license", {})
        check("import_license_attestation", license_info.get("allow_import") is True
              and isinstance(license_info.get("evidence_ref"), str) and bool(license_info["evidence_ref"]),
              "caller_attestation_readback")
        if proposal is not None:
            check("import_source_fingerprint_binding", digest(document) == proposal.get("source_fingerprint"),
                  "source_document_sha256")
        else:
            unconfirm("import_source_fingerprint_binding",
                      "复核路径无原始提案 source_fingerprint；输出与源文档的绑定见 import_content_fingerprint")
        from .model import validate_geojson
        gj, crs, schema = validate_geojson(document, meta.get("crs"), meta["schema"])
        check("import_content_fingerprint", output["fingerprint"] == logical_fingerprint(gj, crs, schema),
              "source_to_output_logical_sha256")

    elif verb == "gis_add_field":
        field = parameters["field"]
        check("add_field_schema_append", output["fields"] == inputs[0]["fields"] + [{k: field[k] for k in ("name", "type", "nullable")}],
              "schema_readback")
        check("add_field_count_unchanged", output["feature_count"] == inputs[0]["feature_count"], "count_readback")
        check("add_field_default_values", all(f["properties"][field["name"]] == field["default"] for f in feats),
              "constructed_default_truth")
        check("add_field_other_fields_unchanged",
              all({k: v for k, v in f["properties"].items() if k != field["name"]} == s["properties"]
                  for f, s in zip(feats, inputs[0]["geojson"]["features"])), "properties_readback")

    elif verb == "gis_calculate_field":
        from .operations import safe_expression
        name = parameters["field"]
        ok = True
        for f, s in zip(feats, inputs[0]["geojson"]["features"]):
            value = safe_expression(parameters["expression"], s["properties"])
            target_type = next(t["type"] for t in output["fields"] if t["name"] == name)
            expected = int(value) if target_type == "integer" else value
            ok &= f["properties"][name] == expected
            ok &= {k: v for k, v in f["properties"].items() if k != name} == {k: v for k, v in s["properties"].items() if k != name}
        check("calculate_field_recompute", ok, "same_kernel_safe_expression_recompute")
        check("calculate_field_other_unchanged", output["feature_count"] == inputs[0]["feature_count"]
              and output["fields"] == inputs[0]["fields"], "count_and_schema_readback")

    elif verb == "gis_delete_field":
        kept = [f["name"] for f in output["fields"]]
        check("delete_field_values_unchanged",
              all({k: f["properties"][k] for k in kept} == {k: s["properties"][k] for k in kept}
                  for f, s in zip(feats, inputs[0]["geojson"]["features"])), "properties_readback")

    elif verb == "gis_run_processing":
        all_parts = parameters["params"]["ALL_PARTS"]
        parts = sum(shapely.get_num_geometries(_shape(f["geometry"])) if all_parts else 1
                    for f in inputs[0]["geojson"]["features"] if f["geometry"] is not None)
        check("processing_centroid_semantics",
              output["feature_count"] == parts
              and all(f["geometry"] is not None and _shape(f["geometry"]).geom_type == "Point" for f in feats)
              and output["fields"] == inputs[0]["fields"],
              "declared_seed_semantics_readback")

    elif verb == "gis_zonal_statistics":
        import numpy as np
        from affine import Affine
        from rasterio.features import geometry_mask
        zones, values = inputs
        check("zonal_zone_count", output["feature_count"] == zones["feature_count"]
              and all(f["geometry"] is None for f in feats), "count_and_table_readback")
        check("zonal_table_schema", [f["name"] for f in output["fields"]] == [parameters["zone_field"]] + parameters["statistics"],
              "schema_readback")
        z = np.array([[np.nan if v is None else v for v in row] for row in values["raster"]["data"]], dtype=np.float64)
        transform = Affine(*values["raster"]["transform"])
        counts_ok, stats_ok = True, True
        for f, row in zip(zones["geojson"]["features"], feats):
            inside = geometry_mask([f["geometry"]], z.shape, transform, invert=True, all_touched=False)
            samples = z[inside & np.isfinite(z)]
            props = row["properties"]
            if proposal is not None:
                receipt = next((c for c in proposal.get("zone_pixel_counts", []) if c["zone"] == props[parameters["zone_field"]]), None)
                counts_ok &= receipt is not None and receipt["pixel_count"] == int(inside.sum()) \
                    and receipt["valid_pixel_count"] == int(samples.size) \
                    and receipt["nodata_pixel_count"] == int((inside & np.isnan(z)).sum())
            for s in parameters["statistics"]:
                if s == "count":
                    counts_ok &= props[s] == int(samples.size)
                    continue
                expected = float({"sum": np.sum, "mean": np.mean, "min": np.min, "max": np.max}[s](samples)) if samples.size else None
                got = props[s]
                if expected is None:
                    stats_ok &= got is None
                else:
                    stats_ok &= got is not None and abs(got - expected) <= 1e-9 * max(1.0, abs(expected))
        check("zonal_pixel_accounting", counts_ok, "same_kernel_pixel_center_mask_recount")
        check("zonal_stats_recompute", stats_ok, "same_kernel_numpy_recompute")

    elif verb == "gis_pointcloud_process" and parameters.get("action") == "boundary":
        check("pc_boundary_vector_contract",
              output["fields"] == [] and output["crs"]["definition_hash"] == inputs[0]["crs"]["definition_hash"],
              "declared_addendum_contract_readback")


def _result_checks(ctx, proposal, check, unconfirm):
    """Read-only result postconditions: same-kernel recomputation or receipt reconciliation."""
    import math
    from shapely import wkt as _wkt
    from shapely.geometry import shape as _shape
    verb, parameters, inputs = ctx["verb"], ctx["parameters"], ctx["inputs"]
    result = proposal["result"]

    if verb == "gis_field_statistics":
        from .operations import exact_key
        groups = defaultdict(list)
        for f in inputs[0]["geojson"]["features"]:
            groups[exact_key([f["properties"][k] for k in parameters["group_by"]])].append(f)
        expected = []
        for _, fs in sorted(groups.items()):
            item = {"group": {k: fs[0]["properties"][k] for k in parameters["group_by"]}, "fields": {}}
            for field in parameters["fields"]:
                values = [f["properties"][field] for f in fs if f["properties"][field] is not None]
                stats = {"count": len(values), "null_count": len(fs) - len(values)}
                for stat in parameters["statistics"]:
                    if stat != "count":
                        stats[stat] = ({"sum": lambda: sum(values), "min": lambda: min(values),
                                        "max": lambda: max(values), "mean": lambda: sum(values) / len(values)}[stat]() if values else None)
                item["fields"][field] = stats
            expected.append(item)
        actual = result["groups"]
        ok = len(actual) == len(expected)
        for a, e in zip(actual, expected):
            ok &= a["group"] == e["group"]
            for field, stats in e["fields"].items():
                got = a["fields"].get(field, {})
                for key, value in stats.items():
                    if key == "mean" and value is not None:
                        ok &= got.get(key) is not None and abs(got[key] - value) <= 1e-12 * max(1.0, abs(value))
                    else:
                        ok &= got.get(key) == value
        check("stats_recompute", ok, "same_kernel_pure_python_recompute")
        check("stats_group_set", len(actual) == len(groups), "group_key_set_comparison")
        check("stats_input_count", result["input_feature_count"] == inputs[0]["feature_count"], "count_readback")
        check("stats_null_accounting", all(a["fields"][f]["null_count"] == e["fields"][f]["null_count"]
                                           for a, e in zip(actual, expected) for f in e["fields"]), "null_count_recompute")

    elif verb == "gis_measure":
        from .model import crs_info
        crs = crs_info(parameters["crs"])
        a = _wkt.loads(parameters["a_wkt"])
        unit = crs["metres_per_unit"]
        if parameters["operation"] == "area":
            value, label = a.area * unit ** 2, "m²"
        elif parameters["operation"] == "length":
            value, label = a.length * unit, "m"
        else:
            value, label = a.distance(_wkt.loads(parameters["b_wkt"])) * unit, "m"
        check("measure_recompute", abs(result["value"] - value) <= 1e-12 * max(1.0, abs(value)),
              "same_kernel_shapely_recompute")
        check("measure_unit_label", result["unit"] == label and result["method"] == "projected_planar",
              "declared_unit_readback")

    elif verb == "gis_validate_geometry":
        issues = [{"record": f["id"], "reason": "null geometry" if f["geometry"] is None else explain_validity(_shape(f["geometry"]))}
                  for f in inputs[0]["geojson"]["features"] if f["geometry"] is None or not _shape(f["geometry"]).is_valid]
        check("validate_recheck", result["issues"] == issues, "same_kernel_geos_validity_rescan")
        check("validate_count", result["checked"] == inputs[0]["feature_count"], "count_readback")
        check("validate_flag_consistency", result["valid"] == (not issues), "flag_consistency")

    elif verb == "gis_spatial_filter":
        reference = _wkt.loads(parameters["reference_wkt"])
        matches = [f for f, g in zip(inputs[0]["geojson"]["features"], geometries(inputs[0]))
                   if getattr(g, parameters["predicate"])(reference)]
        got = result["geojson"]["features"]
        check("filter_count_matches", result["feature_count"] == len(got) == len(matches), "same_kernel_predicate_recount")
        check("filter_subset_records",
              all(any(f["properties"] == m["properties"] and f["geometry"] == m["geometry"] for m in matches) for f in got),
              "whole_record_subset_readback")
        check("filter_predicate_recheck",
              all(bool(getattr(_shape(f["geometry"]), parameters["predicate"])(reference)) for f in got),
              "same_kernel_geos_predicate")

    elif verb == "gis_run_gp":
        check("gp_count_readback", result["count"] == inputs[0]["feature_count"], "native_count_readback")

    elif verb == "gis_pointcloud_info":
        check("pc_info_count_matches", result["point_count"] == inputs[0]["point_count"], "snapshot_count_readback")
        check("pc_info_dimensions_match", result["dimensions"] == inputs[0]["point_dimensions"], "snapshot_dimensions_readback")
        check("pc_info_fingerprint_binding", result["input_fingerprint"] == inputs[0]["fingerprint"], "snapshot_fingerprint_readback")

    elif verb == "gis_serve_vector_tile":
        if "preview_binary_bytes" in result:
            check("tile_handle_metadata", result["preview_binary_bytes"] >= 0, "dry_run_metadata_readback")
            unconfirm("tile_bytes_rehash", "预演不保留二进制句柄，无法重算字节哈希")
        else:
            check("tile_handle_metadata",
                  isinstance(result.get("sha256"), str) and len(result["sha256"]) == 64
                  and result.get("byte_length", 0) > 0 and result.get("expires_in_seconds") == 300,
                  "handle_metadata_readback")
            unconfirm("tile_bytes_rehash", "二进制句柄不在验证上下文，字节级重算无法确认")

    elif verb == "gis_publish_layer":
        check("publish_readback_receipt",
              result.get("readback_fingerprint") == result.get("source_fingerprint") == inputs[0]["fingerprint"]
              and result.get("feature_count") == inputs[0]["feature_count"]
              and result.get("published") is True and result.get("catalog_writeback_status") == "pending",
              "wfs_readback_receipt_reconciliation")

    elif verb == "gis_list_services":
        check("services_receipt_structure",
              result["service_count"] == len(result["services"])
              and all(len(s.get("response_sha256", "")) == 64 for s in result["services"]),
              "capability_receipt_readback")

    elif verb == "gis_query_observations":
        check("sta_receipt_structure",
              result["returned_count"] == len(result["data"])
              and result["local_filtering"] is False and result["local_page_concatenation"] is False
              and len(result.get("response_sha256", "")) == 64,
              "odata_receipt_readback")


CONTRACT_PREDICATES = (distance_unit_compatibility, input_version_binding, spatial_join_contract,
                       output_reopenable, write_scope)
PREDICATES = CONTRACT_PREDICATES + (operation_readback_invariants,)
PRECHECK_PREDICATES = (distance_unit_compatibility, input_version_binding, spatial_join_contract)

# Configuration-layer seam: the check strategy is selected here, never by a user switch.
# The product default is the reliable full set; an experiment may narrow a stage here.
POLICY = {"precheck": PRECHECK_PREDICATES, "preview": PREDICATES, "commit": PREDICATES,
          "verify": PREDICATES, "commit_task": (input_version_binding, write_scope)}


def run(ctx, predicates=None):
    chosen = POLICY.get(ctx["stage"], PREDICATES) if predicates is None else predicates
    findings = [predicate(ctx) for predicate in chosen]
    return {"format": VERIFICATION_FORMAT, "stage": ctx["stage"], "verb": ctx["verb"],
            "checked": [{"predicate": f["predicate"], "subject": f["checked"], "method": f["method"]} for f in findings],
            "found": [{"predicate": f["predicate"], "passed": f["passed"], "observation": f["found"],
                       "method": f["method"], "details": f["details"]} for f in findings],
            "unconfirmed": [{"predicate": f["predicate"], "limit": f["unconfirmed"]} for f in findings],
            "ok": all(f["passed"] is not False for f in findings),
            "predicates_digest": digest([predicate.__name__ for predicate in chosen])}


def enforce(result):
    for found in result["found"]:
        require(found["passed"] is not False, "BACKEND_ERROR", "读回结果违反后置条件，当前成果已回滚",
                check=found["details"].get("failed_check") or found["predicate"],
                predicate=found["predicate"], observation=found["observation"])


PRECHECK_CODES = {"distance_unit_compatibility": "NOT_SUPPORTED",
                  "input_version_binding": "TARGET_STATE_UNKNOWN",
                  "spatial_join_contract": "SCHEMA_MISMATCH"}
PROBLEM_KIND_CODES = {"crs_mismatch": "CRS_MISMATCH", "object_type": "NOT_SUPPORTED",
                      "not_supported": "NOT_SUPPORTED", "schema": "SCHEMA_MISMATCH", "count": "SCHEMA_MISMATCH"}

# Per-verb predicate inventory (single source for the delivery report).
# layer: commit = 提交时（含提案）; preview = 预演同核; verify = task_get(verify=true) 复核;
# result = 只读结果层。所有同核检查均为自洽性检查，不是独立几何 oracle。
SHARED_CHECKS = {
    "vector_proposal": ["schema_readback", "crs_readback"],
    "contract_predicates": [f.__name__ for f in CONTRACT_PREDICATES],
    "execution_policy": ["input_unchanged", "explicit_destruction", "output_readback"],
}
CHECK_REGISTRY = {
    "gis_list_algorithms": {"checks": [], "basic_only_reason": "只读能力目录：结果即目录本身，无数据对象；契约谓词仍跑，算子读回不适用"},
    "gis_describe_algorithm": {"checks": [], "basic_only_reason": "只读能力定义：结果即目录条目，无数据对象；契约谓词仍跑，算子读回不适用"},
    "gis_create_dataset": {"checks": [
        {"id": "create_empty_contract", "method": "empty_and_schema_readback", "basis": "空数据集与声明 schema 的精确读回", "layer": "commit+preview+verify"}]},
    "gis_add_field": {"checks": [
        {"id": "add_field_schema_append", "method": "schema_readback", "basis": "schema 追加精确比对", "layer": "commit+preview+verify"},
        {"id": "add_field_count_unchanged", "method": "count_readback", "basis": "计数精确", "layer": "commit+preview+verify"},
        {"id": "add_field_default_values", "method": "constructed_default_truth", "basis": "构造真值：每条新字段==声明 default", "layer": "commit+preview+verify"},
        {"id": "add_field_other_fields_unchanged", "method": "properties_readback", "basis": "其余属性逐条精确", "layer": "commit+preview+verify"}]},
    "gis_calculate_field": {"checks": [
        {"id": "calculate_field_recompute", "method": "same_kernel_safe_expression_recompute", "basis": "同核安全表达式逐条复算（自洽性）", "layer": "commit+preview+verify"},
        {"id": "calculate_field_other_unchanged", "method": "count_and_schema_readback", "basis": "计数/schema 精确", "layer": "commit+preview+verify"}]},
    "gis_copy_dataset": {"checks": [
        {"id": "copy_logical_content", "method": "logical_fingerprint_equality", "basis": "逻辑指纹相等（排序多重集）", "layer": "commit+preview"}]},
    "gis_merge_datasets": {"checks": [
        {"id": "merge_count_readback", "method": "count_sum", "basis": "计数求和精确", "layer": "commit+preview"},
        {"id": "merge_fingerprint_multiset", "method": "sorted_multiset_logical_sha256", "basis": "输入拼接的排序多重集逻辑指纹==输出指纹", "layer": "commit+preview+verify"}]},
    "gis_assign_projection": {"checks": [
        {"id": "assign_projection_coordinates_unchanged", "method": "normalized_wkb_multiset_exact", "basis": "normalize-WKB 多重集精确相等（坐标不动）", "layer": "commit+preview+verify"}]},
    "gis_reproject": {"checks": [
        {"id": "reproject_count_unchanged", "method": "count_readback", "basis": "计数精确", "layer": "commit+preview+verify"},
        {"id": "reproject_attributes_unchanged", "method": "properties_readback", "basis": "属性逐条精确", "layer": "commit+preview+verify"},
        {"id": "reproject_empty_geometry_contract", "method": "empty_geometry_emptiness_and_attribute_preservation", "basis": "RT-11 空几何保持为空且属性保留；不对零坐标数组做变换/极值归约", "layer": "commit+preview+verify"},
        {"id": "reproject_null_geometry_contract", "method": "null_geometry_contract", "basis": "RT-11 null 几何保持 null，计算与验证两侧契约一致", "layer": "commit+preview+verify"},
        {"id": "reproject_roundtrip_spot", "method": "same_kernel_proj_roundtrip", "basis": "同核 PROJ 往返抽查≤8记录；绝对容差 1e-6×max(1,|坐标|)，同核浮点噪声，非独立 oracle", "layer": "commit+preview+verify"},
        {"id": "reproject_bounds_consistency", "method": "same_kernel_transformed_bounds", "basis": "输入坐标≤4096 全量变换比对边界，容差 1e-6×跨度；超限降级无法确认", "layer": "commit+preview+verify"}]},
    "gis_convert_format": {"checks": [
        {"id": "copy_logical_content", "method": "logical_fingerprint_equality", "basis": "逻辑指纹相等", "layer": "commit+preview"}]},
    "gis_buffer": {"checks": [
        {"id": "buffer_geometry_valid", "method": "same_kernel_geos_is_valid", "basis": "GEOS 有效性（同核自洽）", "layer": "commit+preview+verify"},
        {"id": "buffer_area_upper_bound", "method": "analytic_parallel_body_upper_bound_per_part", "basis": "RT-09 逐部件解析界 Σ(Ai+2Li·r+πr²)（Steiner 平行体上界，MultiPoint/多部件/空部件均覆盖）；quad_segs=8 内接近似只更小；相对松弛 1e-9，未放大容差", "layer": "commit+preview+verify"},
        {"id": "buffer_area_lower_bound", "method": "containment_exact", "basis": "r>0 面积≥源几何（或源的并）；r<0 侵蚀 0≤area≤源面积；精确；dissolve 与逐要素分支语义一致", "layer": "commit+preview+verify"},
        {"id": "buffer_count_semantics", "method": "count_and_schema_set_comparison", "basis": "dissolve=false 计数==输入且属性逐条保留；true 非空→1 条且 schema 空", "layer": "commit+preview+verify"},
        {"id": "buffer_positive_containment", "method": "minkowski_containment", "basis": "RT-01 r>0：输出必须覆盖每个非空源部件（面积界无法证明位置）", "layer": "commit+preview+verify"},
        {"id": "buffer_positive_expansion", "method": "strict_outer_expansion", "basis": "RT-01 r>0：输出减去源的面积>0，排除原地不动/缩小", "layer": "commit+preview+verify"},
        {"id": "buffer_expansion_bounds", "method": "axis_aligned_minkowski_bound", "basis": "RT-01 r>0：输出 bbox 必须落在源 bbox 外扩 r 之内（可证空间约束），捕捉平移/缩小", "layer": "commit+preview+verify"},
        {"id": "buffer_same_kernel_recompute", "method": "same_kernel_geos_consistency_not_independent_oracle", "basis": "RT-01 用声明的 quad_segs/cap/join 同核复算并比对对称差面积（自洽性，非独立 oracle）", "layer": "commit+preview+verify"},
        {"id": "buffer_negative_erosion", "method": "erosion_area_contraction", "basis": "r<0 dissolve：面积不超过源面积之和", "layer": "commit+preview+verify"},
        {"id": "buffer_negative_containment", "method": "erosion_subset_of_input", "basis": "r<0：输出必须被源包含", "layer": "commit+preview+verify"},
        {"id": "buffer_negative_contraction", "method": "strict_inner_erosion", "basis": "r<0：面积严格小于源面积", "layer": "commit+preview+verify"}]},
    "gis_clip": {"checks": [
        {"id": "attributes_retained", "method": "explicit_source_row_property_readback", "basis": "RT-02 输出属性按显式源行索引逐条精确比对；不再用属性子序列（重复属性合法，子序列会误拒）；源行不可判定则记为未确认", "layer": "commit+preview+verify"},
        {"id": "count_bound", "method": "count_set_comparison", "basis": "输出计数≤输入计数", "layer": "commit+preview+verify"},
        {"id": "subset_area_bound", "method": "containment_area_exact", "basis": "逐条面积≤其绑定源行面积（包含关系，相对松弛 1e-9）", "layer": "commit+preview+verify"},
        {"id": "subset_within_input", "method": "same_kernel_geos_covers", "basis": "同核 covers 子集性（自洽）", "layer": "commit+preview+verify"},
        {"id": "clip_completeness", "method": "same_kernel_geos_recompute_not_independent_oracle", "basis": "RT-02 输出几何并集必须等于逐源 intersection 的并集；捕捉漏行/错几何（自洽性，非独立 oracle）", "layer": "commit+preview+verify"},
        {"id": "clip_row_multiplicity", "method": "one_output_row_per_nonempty_source_row", "basis": "RT-02 算子对每个非空源行恰好输出一行；重复行不改变并集/面积/计数界，须单独核对源索引多重集", "layer": "commit+preview+verify"},
        {"id": "clip_source_row_binding", "method": "explicit_source_index_or_geometry_identity_not_attribute_subsequence", "basis": "RT-02 每行绑定到唯一源行的几何与属性；显式 row_provenance 优先，几何恒等兜底", "layer": "commit+preview+verify"},
        {"id": "clip_mask_side_constraint", "method": "mask_covers_intersection_or_difference_exclusion", "basis": "RT-02 intersection 每行被掩膜覆盖；difference 每行与掩膜交集面积≈0", "layer": "commit+preview+verify"}]},
    "gis_overlay": {"checks": [
        {"id": "attributes_retained", "method": "explicit_source_row_property_readback", "basis": "RT-02 输出属性按显式源行索引逐条精确比对；不再用属性子序列", "layer": "commit+preview+verify"},
        {"id": "count_bound", "method": "count_set_comparison", "basis": "输出计数≤输入计数", "layer": "commit+preview+verify"},
        {"id": "subset_area_bound", "method": "containment_area_exact", "basis": "intersection/difference 均⊆绑定源行，面积界精确", "layer": "commit+preview+verify"},
        {"id": "subset_within_input", "method": "same_kernel_geos_covers", "basis": "同核 covers 子集性（自洽）", "layer": "commit+preview+verify"},
        {"id": "clip_completeness", "method": "same_kernel_geos_recompute_not_independent_oracle", "basis": "RT-02 输出并集==逐源叠加结果并集（含 difference 排除区）", "layer": "commit+preview+verify"},
        {"id": "clip_row_multiplicity", "method": "one_output_row_per_nonempty_source_row", "basis": "RT-02 每个非空源行恰好一行（含 difference），重复行须拦截", "layer": "commit+preview+verify"},
        {"id": "clip_source_row_binding", "method": "explicit_source_index_or_geometry_identity_not_attribute_subsequence", "basis": "RT-02 每行绑定唯一源行的几何与属性", "layer": "commit+preview+verify"},
        {"id": "clip_mask_side_constraint", "method": "mask_covers_intersection_or_difference_exclusion", "basis": "RT-02 intersection 被掩膜覆盖；difference 排除掩膜区域", "layer": "commit+preview+verify"}]},
    "gis_dissolve": {"checks": [
        {"id": "dissolve_fields_only", "method": "schema_readback", "basis": "仅保留分组字段", "layer": "commit+preview"},
        {"id": "unique_output_group", "method": "group_key_uniqueness", "basis": "每组唯一", "layer": "commit+preview"},
        {"id": "group_key_set_readback", "method": "group_key_set_comparison", "basis": "组键集==输入分组", "layer": "commit+preview"},
        {"id": "group_count_readback", "method": "count_readback", "basis": "组计数三方一致", "layer": "commit+preview"},
        {"id": "multipolygon_per_group", "method": "geometry_type_readback", "basis": "每组 MultiPolygon", "layer": "commit+preview"},
        {"id": "group_geometry_readback", "method": "same_kernel_geos_topological_equals", "basis": "同核 union_all 拓扑相等（自洽）", "layer": "commit+preview"},
        {"id": "manifest_group_keys", "method": "manifest_key_set", "basis": "清单组键唯一且完备", "layer": "commit+preview"},
        {"id": "manifest_membership", "method": "manifest_membership_readback", "basis": "清单成员==源记录分组", "layer": "commit+preview"}]},
    "gis_field_statistics": {"checks": [
        {"id": "stats_recompute", "method": "same_kernel_pure_python_recompute", "basis": "纯 Python 逐组复算；count/sum/min/max 精确，mean 相对 1e-12（同核自洽）", "layer": "commit+preview(result)"},
        {"id": "stats_group_set", "method": "group_key_set_comparison", "basis": "组数==输入分组数", "layer": "commit+preview(result)"},
        {"id": "stats_input_count", "method": "count_readback", "basis": "input_feature_count 精确", "layer": "commit+preview(result)"},
        {"id": "stats_null_accounting", "method": "null_count_recompute", "basis": "null_count 逐组复算精确", "layer": "commit+preview(result)"}]},
    "gis_spatial_filter": {"checks": [
        {"id": "filter_count_matches", "method": "same_kernel_predicate_recount", "basis": "同核谓词重数（自洽）", "layer": "commit+preview(result)"},
        {"id": "filter_subset_records", "method": "whole_record_subset_readback", "basis": "整记录子集（属性+几何精确）", "layer": "commit+preview(result)"},
        {"id": "filter_predicate_recheck", "method": "same_kernel_geos_predicate", "basis": "同核谓词复检（自洽）", "layer": "commit+preview(result)"}]},
    "gis_measure": {"checks": [
        {"id": "measure_recompute", "method": "same_kernel_shapely_recompute", "basis": "同核复算，相对 1e-12（自洽）", "layer": "commit+preview(result)"},
        {"id": "measure_unit_label", "method": "declared_unit_readback", "basis": "area→m²、length/distance→m、projected_planar", "layer": "commit+preview(result)"}]},
    "gis_validate_geometry": {"checks": [
        {"id": "validate_recheck", "method": "same_kernel_geos_validity_rescan", "basis": "独立 is_valid 扫描比对 issues（同核自洽）", "layer": "commit+preview(result)"},
        {"id": "validate_count", "method": "count_readback", "basis": "checked==feature_count", "layer": "commit+preview(result)"},
        {"id": "validate_flag_consistency", "method": "flag_consistency", "basis": "valid==无 issues", "layer": "commit+preview(result)"}]},
    "gis_delete_field": {"checks": [
        {"id": "removed_fields_absent", "method": "schema_readback", "basis": "被删字段不在输出", "layer": "commit+preview"},
        {"id": "delete_field_count", "method": "count_readback", "basis": "计数不变", "layer": "commit+preview"},
        {"id": "delete_field_values_unchanged", "method": "properties_readback", "basis": "保留字段值逐条精确", "layer": "commit+preview+verify"}]},
    "gis_rename_dataset": {"checks": [
        {"id": "rename_content_unchanged", "method": "fingerprint_equality", "basis": "内容指纹==输入（改名不动数据）", "layer": "commit+preview+verify"},
        {"id": "rename_head_resolves", "method": "head_resolution_readback", "basis": "新名解析到同一 ID；预演降级无法确认", "layer": "commit+verify"},
        {"id": "rename_old_name_gone", "method": "database_readback_absence", "basis": "旧名不再解析；预演降级无法确认", "layer": "commit+verify"}]},
    "gis_append_dataset": {"checks": [
        {"id": "append_count_readback", "method": "count_sum", "basis": "计数求和精确", "layer": "commit+preview"},
        {"id": "append_multiset_readback", "method": "sorted_multiset_logical_sha256", "basis": "目标+源拼接多重集指纹", "layer": "commit+preview"},
        {"id": "append_version_accounting", "method": "proposal_count_readback", "basis": "before/added/after 计数对账；复核无提案降级无法确认", "layer": "commit+preview"}]},
    "gis_import_data": {"checks": [
        {"id": "import_license_attestation", "method": "caller_attestation_readback", "basis": "allow_import=true 且 evidence_ref 非空（调用方声明，不推断外部许可）", "layer": "commit+preview+verify"},
        {"id": "import_source_fingerprint_binding", "method": "source_document_sha256", "basis": "源文档 sha256==提案 source_fingerprint；复核无提案降级无法确认", "layer": "commit+preview"},
        {"id": "import_content_fingerprint", "method": "source_to_output_logical_sha256", "basis": "输出指纹==源文档验证后的逻辑指纹（构造真值）", "layer": "commit+preview+verify"}]},
    "gis_spatial_join": {"checks": [
        {"id": "join_count_accounting", "method": "complete_match_relation_row_multiplicity", "basis": "RT-03 构造完整匹配关系 pairs，输出行数==pairs 长度（≤20000 对，超限降级）", "layer": "commit+preview+verify"},
        {"id": "join_proposal_counts", "method": "same_kernel_predicate_recount", "basis": "提案 matched/unmatched==复算值", "layer": "commit+preview"},
        {"id": "join_left_outer_nulls", "method": "left_outer_null_semantics_readback", "basis": "summary=first 未匹配行连接字段全 null", "layer": "commit+preview+verify"},
        {"id": "join_left_geometry_binding", "method": "row_to_left_geometry_identity", "basis": "RT-03 每行几何==其左行几何（计数相等不足以证明连接正确）", "layer": "commit+preview+verify"},
        {"id": "join_left_attributes_binding", "method": "row_to_left_attributes_identity", "basis": "RT-03 每行左属性逐字段等于其左行", "layer": "commit+preview+verify"},
        {"id": "join_right_attributes_binding", "method": "row_to_right_attributes_exact_values_not_merely_counts", "basis": "RT-03 每行右属性==其匹配右行的精确值；未匹配为 null（捕捉属性错配）", "layer": "commit+preview+verify"},
        {"id": "join_summary_multiplicity", "method": "first_vs_all_row_multiplicity", "basis": "RT-03 summary=first 每左行恰 1 行；all 按候选数展开", "layer": "commit+preview+verify"},
        {"id": "join_deterministic_first", "method": "insufficient_context", "basis": "候选并列且无稳定业务键时如实记为未确认", "layer": "commit+preview+verify"},
        {"id": "join_row_binding", "method": "insufficient_context", "basis": "输入对数超预算时逐行绑定记为未确认", "layer": "commit+preview+verify"}]},
    "gis_raster_calc": {"checks": [
        {"id": "grid_and_pixels_readback", "method": "sqlite_exact_raster_snapshot", "basis": "持久栅格==输出栅格（精确）", "layer": "commit+preview+verify"},
        {"id": "raster_nodata_accounting", "method": "pixel_count_accounting", "basis": "valid+nodata==w*h", "layer": "commit+preview+verify"},
        {"id": "raster_grid_alignment", "method": "grid_property_exact_comparison", "basis": "transform/宽/高==全部输入（无重采样）", "layer": "commit+preview+verify"},
        {"id": "raster_nodata_mask_semantics", "method": "nodata_union_exact", "basis": "输出掩膜==输入掩膜并集（精确）", "layer": "commit+preview+verify"},
        {"id": "raster_pixel_spot", "method": "same_grammar_independent_arithmetic_recompute", "basis": "独立 AST 求值器按公布文法复算≤16 像元，相对 1e-9（同文法自洽）", "layer": "commit+preview+verify"}]},
    "gis_slope": {"checks": [
        {"id": "grid_and_pixels_readback", "method": "sqlite_exact_raster_snapshot", "basis": "精确读回", "layer": "commit+preview+verify"},
        {"id": "raster_nodata_accounting", "method": "pixel_count_accounting", "basis": "valid+nodata==w*h", "layer": "commit+preview+verify"},
        {"id": "raster_grid_alignment", "method": "grid_property_exact_comparison", "basis": "网格==输入", "layer": "commit+preview+verify"},
        {"id": "raster_nodata_mask_semantics", "method": "horn_3x3_mask_exact", "basis": "掩膜==边界＋3x3 含 NoData（Horn 声明语义）", "layer": "commit+preview+verify"},
        {"id": "raster_pixel_spot", "method": "same_formula_independent_horn_recompute_float32_margin", "basis": "独立 numpy Horn 复算；slope=atan(z_factor·√(dzdx²+dzdy²))；float32 相对容差 1e-6（同公式自洽，非独立 oracle）", "layer": "commit+preview+verify"}]},
    "gis_aspect": {"checks": [
        {"id": "grid_and_pixels_readback", "method": "sqlite_exact_raster_snapshot", "basis": "精确读回", "layer": "commit+preview+verify"},
        {"id": "raster_nodata_accounting", "method": "pixel_count_accounting", "basis": "valid+nodata==w*h", "layer": "commit+preview+verify"},
        {"id": "raster_grid_alignment", "method": "grid_property_exact_comparison", "basis": "网格==输入", "layer": "commit+preview+verify"},
        {"id": "raster_nodata_mask_semantics", "method": "horn_3x3_mask_exact", "basis": "掩膜==边界＋3x3 含 NoData＋平坦像元（flat_policy=nodata）", "layer": "commit+preview+verify"},
        {"id": "raster_pixel_spot", "method": "same_formula_independent_horn_recompute_float32_margin", "basis": "aspect=atan2(-dzdx,dzdy)%360，环差容差 1e-4 度；float32 舍入（同公式自洽）", "layer": "commit+preview+verify"}]},
    "gis_hillshade": {"checks": [
        {"id": "grid_and_pixels_readback", "method": "sqlite_exact_raster_snapshot", "basis": "精确读回", "layer": "commit+preview+verify"},
        {"id": "raster_nodata_accounting", "method": "pixel_count_accounting", "basis": "valid+nodata==w*h", "layer": "commit+preview+verify"},
        {"id": "raster_grid_alignment", "method": "grid_property_exact_comparison", "basis": "网格==输入", "layer": "commit+preview+verify"},
        {"id": "raster_nodata_mask_semantics", "method": "horn_3x3_mask_exact", "basis": "掩膜==边界＋3x3 含 NoData", "layer": "commit+preview+verify"},
        {"id": "raster_pixel_spot", "method": "gdal_3122_traditional_uint8_exact_encoding", "basis": "RT-10 锁定 Rasterio-bundled GDAL 3.12.2 traditional 编码：1+floor(254·clamp(shade,0,1)+0.5)，暗部钳到 1、NoData 保持 null；逐像元精确相等，不放大 ±1 容差", "layer": "commit+preview+verify"}]},
    "gis_reclassify": {"checks": [
        {"id": "grid_and_pixels_readback", "method": "sqlite_exact_raster_snapshot", "basis": "精确读回", "layer": "commit+preview+verify"},
        {"id": "raster_nodata_accounting", "method": "pixel_count_accounting", "basis": "valid+nodata==w*h", "layer": "commit+preview+verify"},
        {"id": "raster_grid_alignment", "method": "grid_property_exact_comparison", "basis": "网格==输入", "layer": "commit+preview+verify"},
        {"id": "raster_nodata_mask_semantics", "method": "nodata_propagation_exact", "basis": "输入 null 保持 null", "layer": "commit+preview+verify"},
        {"id": "raster_pixel_spot", "method": "constructed_rule_mapping_truth", "basis": "按声明区间规则构造真值，精确映射（含 unmapped keep/nodata）", "layer": "commit+preview+verify"}]},
    "gis_zonal_statistics": {"checks": [
        {"id": "zonal_zone_count", "method": "count_and_table_readback", "basis": "行数==zones 计数；几何全 null（表）", "layer": "commit+preview+verify"},
        {"id": "zonal_table_schema", "method": "schema_readback", "basis": "zone_field+statistics 精确", "layer": "commit+preview+verify"},
        {"id": "zonal_pixel_accounting", "method": "same_kernel_pixel_center_mask_recount", "basis": "同核 geometry_mask 中心规则复算 pixel/valid/nodata 计数（精确）", "layer": "commit+preview+verify"},
        {"id": "zonal_stats_recompute", "method": "same_kernel_numpy_recompute", "basis": "numpy 复算 sum/mean/min/max，相对 1e-9（同核自洽）", "layer": "commit+preview+verify"}]},
    "gis_pointcloud_info": {"checks": [
        {"id": "pc_info_count_matches", "method": "snapshot_count_readback", "basis": "result.point_count==输入快照", "layer": "commit+preview(result)"},
        {"id": "pc_info_dimensions_match", "method": "snapshot_dimensions_readback", "basis": "维度列表==输入快照", "layer": "commit+preview(result)"},
        {"id": "pc_info_fingerprint_binding", "method": "snapshot_fingerprint_readback", "basis": "input_fingerprint==输入指纹", "layer": "commit+preview(result)"}]},
    "gis_pointcloud_process": {"checks": [
        {"id": "pointcloud_bytes_readback", "method": "sqlite_exact_bytes_and_metadata", "basis": "持久字节==输出字节（精确）", "layer": "commit+preview+verify"},
        {"id": "pointcloud_dimensions_preserved", "method": "dimension_list_readback", "basis": "维度保真（算子已强制，验证器复核）", "layer": "commit+preview+verify"},
        {"id": "pointcloud_count_accounting", "method": "constructed_action_truth", "basis": "merge=输入和；thin_decimate=len(range(offset,n,step)) 构造真值；reproject/convert 守恒；clip/thin_radius≤输入（PDAL 语义上界）", "layer": "commit+preview+verify"},
        {"id": "pointcloud_fingerprint_recomputed", "method": "byte_sha256_binding", "basis": "字节 sha256 重算绑定指纹", "layer": "commit+preview+verify"},
        {"id": "pc_boundary_vector_contract", "method": "declared_addendum_contract_readback", "basis": "boundary 动作：schema 空、CRS 保留（本地附录声明）", "layer": "commit+preview+verify"}]},
    "gis_run_processing": {"checks": [
        {"id": "processing_centroid_semantics", "method": "declared_seed_semantics_readback", "basis": "native:centroids 种子：ALL_PARTS 时计数==部件数否则==要素数；几何全 Point；schema 保留", "layer": "commit+preview+verify"}]},
    "gis_run_gp": {"checks": [
        {"id": "gp_count_readback", "method": "native_count_readback", "basis": "ArcPy GetCount==输入 feature_count（精确）；后端环境阻塞时以合成回执做谓词级验证", "layer": "commit+preview(result)"}]},
    "gis_delete_dataset": {"checks": [
        {"id": "dataset_absent_readback", "method": "database_readback_absence", "basis": "销毁后按 ID 重开必须 NOT_FOUND（真实缺席读回）；预演降级无法确认", "layer": "commit+verify"}]},
    "gis_serve_vector_tile": {"checks": [
        {"id": "tile_handle_metadata", "method": "handle_metadata_readback", "basis": "sha256 64hex、byte_length>0、expires=300；字节重算不在上下文→无法确认段", "layer": "commit+preview(result)"}]},
    "gis_publish_layer": {"checks": [
        {"id": "publish_readback_receipt", "method": "wfs_readback_receipt_reconciliation", "basis": "WFS 回读指纹==源指纹==输入指纹、计数对账、catalog_writeback=pending；后端环境阻塞时以合成回执做谓词级验证", "layer": "commit(result)"}]},
    "gis_list_services": {"checks": [
        {"id": "services_receipt_structure", "method": "capability_receipt_readback", "basis": "service_count==len(services)、response_sha256 存在；外部能力文档无本地真值", "layer": "commit+preview(result)"}]},
    "gis_query_observations": {"checks": [
        {"id": "sta_receipt_structure", "method": "odata_receipt_readback", "basis": "returned_count==len(data)、无本地过滤/拼页、response_sha256 存在", "layer": "commit+preview(result)"}]},
    "gis_add_layer_from_catalog": {"checks": [
        {"id": "native_resource_readback", "method": "native_backend_and_sqlite_sha256", "basis": "资源记录重开指纹一致", "layer": "commit+verify"},
        {"id": "resource_map_layers_accounting", "method": "resource_layer_binding_readback", "basis": "layers 恰增 1 且 dataset_ref/catalog_id/license_ref 绑定目录证据", "layer": "commit+preview+verify"}]},
    "gis_export_map_image": {"checks": [
        {"id": "native_resource_readback", "method": "native_backend_and_sqlite_sha256", "basis": "资源记录重开指纹一致", "layer": "commit+verify"},
        {"id": "resource_image_integrity", "method": "sha256_bytes_and_map_property_readback", "basis": "base64 解码 sha256==file_sha256；width/height==地图；dpi==请求", "layer": "commit+preview+verify"}]},
}


def enforce_precheck(result):
    """Policy for the before-side-effect pass: name the predicate that refused."""
    for found in result["found"]:
        if found["passed"] is False:
            kinds = found["details"].get("problem_kinds") or []
            code = next((PROBLEM_KIND_CODES[kind] for kind in kinds if kind in PROBLEM_KIND_CODES),
                        PRECHECK_CODES.get(found["predicate"], "BACKEND_ERROR"))
            raise GISFault(code, found["observation"],
                           predicate=found["predicate"], rule="verification_precheck", **found["details"])


def legacy_checks(result):
    checks = []
    for found in result["found"]:
        if found["predicate"] == "operation_readback_invariants":
            checks.extend(check for check in found["details"]["checks"] if check["passed"] is not None)
        else:
            checks.append({"id": found["predicate"], "passed": found["passed"] is not False,
                           "method": found["method"]})
    return checks