"""Single verifier implementation.

Operators compute, this module judges, runtime/commit/resume decide policy.
Every predicate reports what was checked, what was found and what cannot be
confirmed. There is no blanket verified=true.
"""
from __future__ import annotations

from collections import defaultdict

import shapely
from shapely.geometry import shape

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
    """Operator-specific readback invariants, migrated verbatim from verification.py."""
    name = "operation_readback_invariants"
    verb, parameters, inputs = ctx["verb"], ctx["parameters"], ctx["inputs"]
    output, proposal = ctx["output"], ctx["proposal"]
    if output is None or proposal is None or "geojson" not in proposal:
        return finding(name, "算子级读回不变量", "无矢量输出提案可比对（只读步骤或复核路径）",
                       "原始提案不保留在工件中；提交时的判定见 evidence.postcondition_checks",
                       True, "not_applicable", checks=[], failed_check=None)
    checks = []

    def check(label, passed, method="readback_exact_structure"):
        checks.append({"id": label, "passed": bool(passed), "method": method})
        if not passed:
            raise _InvariantStop(label)

    stopped = None
    try:
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
    except _InvariantStop as exc:
        stopped = exc.name
    except GISFault as exc:
        stopped = exc.code
        checks.append({"id": exc.code, "passed": False, "method": "geometry_readback"})
    return finding(name, f"{verb} 的字段/CRS/计数/分组读回不变量",
                   "全部不变量成立" if stopped is None else f"不变量 {stopped} 不成立",
                   "GEOS 谓词与计算内核同源，不是独立几何 oracle", stopped is None,
                   "readback_exact_structure", checks=checks, failed_check=stopped)


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
            checks.extend(found["details"]["checks"])
        else:
            checks.append({"id": found["predicate"], "passed": found["passed"] is not False,
                           "method": found["method"]})
    return checks