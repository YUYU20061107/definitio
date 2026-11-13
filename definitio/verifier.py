"""Single verifier implementation.

Operators compute, this module judges, runtime/commit/resume decide policy.
Every predicate reports what was checked, what was found and what cannot be
confirmed. There is no blanket verified=true.
"""
from __future__ import annotations

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
    prefix = (ctx["authorization"] or {}).get("target_prefix")
    if prefix:
        out += [write for write in actual if not write["target"].startswith(prefix)]
    return finding(name, f"{len(actual)} 项实际写入对 {len(allowed)} 项声明范围",
                   "全部在任务与授权范围内" if not out else f"{len(out)} 项越界",
                   "宿主自身的授权政策不在本引擎可证明范围内", not out,
                   "declared_scope_set_comparison", out_of_scope=out, actual_writes=actual)


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

    def check(label, passed, method="readback_exact_structure"):
        checks.append({"id": label, "passed": None if passed is None else bool(passed), "method": method})
        if passed is False:
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
    """Grid alignment, nodata accounting and pixel spot checks; filled by the R2 native task."""


def _pointcloud_semantic_checks(ctx, output, proposal, check, unconfirm):
    """Point count/dimension accounting; filled by the R2 native task."""


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


def _vector_semantic_checks(ctx, output, proposal, check, unconfirm):
    """Per-verb vector postconditions. Same-kernel recomputations are labelled as
    self-consistency checks, never as an independent geometry oracle."""
    import math
    from shapely.geometry import shape as _shape
    verb, parameters, inputs = ctx["verb"], ctx["parameters"], ctx["inputs"]
    feats = output["geojson"]["features"]
    geoms = [_shape(f["geometry"]) if f["geometry"] is not None else None for f in feats]

    if verb == "gis_buffer":
        check("buffer_geometry_valid", all(g is not None and g.is_valid for g in geoms), "same_kernel_geos_is_valid")
        crs = inputs[0]["crs"]
        r = parameters["distance"] / crs["metres_per_unit"]
        source_geoms = geometries(inputs[0])
        if parameters["dissolve"]:
            check("buffer_count_semantics",
                  output["feature_count"] == (1 if source_geoms else 0) and output["fields"] == [],
                  "count_and_schema_set_comparison")
            if source_geoms and r > 0:
                upper = sum(g.area + 2 * g.length * r + math.pi * r * r for g in source_geoms)
                total = sum(g.area for g in geoms if g is not None)
                check("buffer_area_upper_bound", total <= upper * (1 + 1e-9), "analytic_parallel_body_upper_bound",
                      )
                check("buffer_area_lower_bound", total >= max(g.area for g in source_geoms), "containment_exact")
        else:
            check("buffer_count_semantics",
                  output["feature_count"] == inputs[0]["feature_count"]
                  and all(f["properties"] == s["properties"] for f, s in zip(feats, inputs[0]["geojson"]["features"])),
                  "count_and_properties_readback")
            for g, src in zip(geoms, source_geoms):
                if r > 0:
                    upper = src.area + 2 * src.length * r + math.pi * r * r
                    check("buffer_area_upper_bound", g.area <= upper * (1 + 1e-9), "analytic_parallel_body_upper_bound")
                    check("buffer_area_lower_bound", g.area >= src.area, "containment_exact")
                else:
                    check("buffer_area_lower_bound", 0 <= g.area <= src.area, "erosion_containment_exact")

    elif verb == "gis_spatial_join":
        left, right = geometries(inputs[0]), geometries(inputs[1])
        predicate, summary = parameters["predicate"], parameters["summary"]
        join_names = [f["name"] for f in output["fields"] if f["name"] not in {s["name"] for s in inputs[0]["fields"]}]
        if len(left) * len(right) > 20000:
            unconfirm("join_count_accounting", "输入对数超过 20000 的复算预算，无法逐对核对")
        else:
            counts = []
            for g in left:
                if predicate == "nearest":
                    ds = [g.distance(h) for h in right]
                    counts.append(sum(d == min(ds) for d in ds) if ds else 0)
                else:
                    counts.append(sum(bool(getattr(g, predicate)(h)) for h in right))
            matched = sum(c > 0 for c in counts)
            expected_rows = sum(max(1, c) if summary == "all" else 1 for c in counts)
            check("join_count_accounting",
                  matched + (len(left) - matched) == len(left) and output["feature_count"] == expected_rows,
                  "same_kernel_predicate_recount")
            if proposal is not None:
                check("join_proposal_counts", proposal.get("matched_count") == matched
                      and proposal.get("unmatched_count") == len(left) - matched, "same_kernel_predicate_recount")
            if summary == "first":
                nulls_ok = all(all(f["properties"][n] is None for n in join_names)
                               for f, c in zip(feats, counts) if c == 0)
                check("join_left_outer_nulls", nulls_ok, "left_outer_null_semantics_readback")

    elif verb in ("gis_clip", "gis_overlay"):
        source = inputs[0]["geojson"]["features"]
        cursor, aligned = 0, []
        for f in feats:
            while cursor < len(source) and source[cursor]["properties"] != f["properties"]:
                cursor += 1
            if cursor >= len(source):
                aligned = None
                break
            aligned.append((f, source[cursor]))
            cursor += 1
        check("attributes_retained", aligned is not None, "subsequence_properties_readback")
        check("count_bound", output["feature_count"] <= inputs[0]["feature_count"], "count_set_comparison")
        if aligned is not None:
            area_ok, subset_ok = True, True
            for f, s in aligned:
                out_g, in_g = _shape(f["geometry"]), _shape(s["geometry"])
                area_ok &= out_g.area <= in_g.area * (1 + 1e-9)
                subset_ok &= bool(in_g.covers(out_g))
            check("subset_area_bound", area_ok, "containment_area_exact")
            check("subset_within_input", subset_ok, "same_kernel_geos_covers")

    elif verb == "gis_reproject":
        from rasterio.warp import transform_geom
        check("reproject_count_unchanged", output["feature_count"] == inputs[0]["feature_count"], "count_readback")
        check("reproject_attributes_unchanged",
              all(f["properties"] == s["properties"] for f, s in zip(feats, inputs[0]["geojson"]["features"])),
              "properties_readback")
        src_crs, dst_crs = inputs[0]["crs"]["wkt"], output["crs"]["wkt"]
        sample = feats[::max(1, len(feats) // 8)][:8]
        src_sample = inputs[0]["geojson"]["features"][::max(1, len(feats) // 8)][:8]
        worst = 0.0
        for f, s in zip(sample, src_sample):
            if f["geometry"] is None or s["geometry"] is None:
                continue
            back = transform_geom(dst_crs, src_crs, f["geometry"], precision=-1)
            a, b = _shape(back), _shape(s["geometry"])
            ca, cb = shapely.get_coordinates(a), shapely.get_coordinates(b)
            if ca.shape != cb.shape:
                worst = float("inf")
                break
            scale = max(1.0, float(abs(cb).max()) if cb.size else 1.0)
            worst = max(worst, float(abs(ca - cb).max()) / scale)
        check("reproject_roundtrip_spot", worst <= 1e-6, "same_kernel_proj_roundtrip",
              )
        coords = shapely.get_coordinates([_shape(f["geometry"]) for f in inputs[0]["geojson"]["features"] if f["geometry"] is not None])
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