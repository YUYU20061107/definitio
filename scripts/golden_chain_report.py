"""Automated golden-chain delivery report: acceptance, regression, G1 hashes, file manifest.

Read-only with respect to the G1 baseline. Runs the suite in a private temporary workspace.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
from pathlib import Path

from defusedxml.ElementTree import parse as parse_xml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

SKIP_DIRS = {".git", "__pycache__", ".pytest_cache", "data", ".venv", "node_modules"}
ACCEPTANCE = {
    "A1_normal_end_to_end_and_independent_readback": "test_a1_end_to_end_and_independent_readback_matches_truth",
    "A2_unit_object_version_errors_named": "test_a2_unit_object_and_version_errors_are_each_named",
    "A3_state_change_does_not_authorize_old_plan": "test_a3_state_change_after_checks_does_not_authorize_the_old_plan",
    "A4_midchain_failure_recovers": "test_a4_midchain_failure_recovers_from_valid_state",
    "A5_retry_has_no_duplicate_or_out_of_scope_effects": "test_a5_commit_and_export_retries_have_no_duplicate_or_out_of_scope_effects",
}
NEW_TEST_FILES = ("test_rollout_projection.py", "test_rollout_predicates.py", "test_rollout_native_predicates.py",
                  "test_rollout_commit_preview.py", "test_rollout_discovery.py", "test_rollout_planner.py",
                  "test_rollout_inventory.py")
V05_TEST_FILES = ("test_v05_protocol.py", "test_v05_object_header.py",
                  "test_v05_handbook.py", "test_v05_selfcheck.py")
ALL_NEW_TEST_FILES = NEW_TEST_FILES + V05_TEST_FILES
ROLLOUT_ACCEPTANCE = {
    "R1_three_surface_projection_consistency": "test_three_surfaces_emit_the_identical_projection_for_vector",
    "R1_geographic_axis_order_survives": "test_geographic_axis_order_survives_all_three_surfaces",
    "R3_dry_run_execute_same_checks": "test_preview_and_execute_run_the_same_invariant_checks",
    "R3_preview_commit_verify_same_predicate_objects": "test_preview_execute_verify_share_predicate_objects",
    "R4_full_60_tool_zero_inconsistency": "test_audit_covers_all_60_tools_with_zero_inconsistency",
    "R5_spatial_join_intent": "test_spatial_join_intent_branch",
    "R5_import_intent": "test_import_intent_branch_first_step",
    "inventory_40_verbs_no_blanks": "test_registry_covers_exactly_the_40_frozen_verbs",
}


def sha256(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def tree_files(root):
    for path in sorted(root.rglob("*")):
        if path.is_file() and not (set(path.relative_to(root).parts) & SKIP_DIRS):
            yield path


def manifest(root):
    return {path.relative_to(root).as_posix(): {"sha256": sha256(path), "bytes": path.stat().st_size}
            for path in tree_files(root)}


def git(root, *args):
    return subprocess.run(["git", "-C", str(root), *args], capture_output=True, text=True, check=True).stdout


def change_set(root, baseline):
    changed = git(root, "diff", "--name-status", baseline, "HEAD").splitlines()
    added, modified = [], []
    for line in changed:
        status, _, name = line.partition("\t")
        (added if status.startswith("A") else modified).append(name)
    tracked = set(git(root, "ls-tree", "-r", "--name-only", "HEAD").splitlines())
    untracked = sorted(rel for rel in manifest(root) if rel not in tracked)
    return {"baseline_commit": baseline, "added": sorted(added), "modified": sorted(modified),
            "untracked": untracked}


def collect_suite(xml, log, exit_code):
    cases = []
    for case in parse_xml(xml).getroot().iter("testcase"):
        failure = case.find("failure")
        error = case.find("error")
        skipped = case.find("skipped")
        cases.append({"id": f"{case.get('classname')}::{case.get('name')}", "file": case.get("classname"),
                      "name": case.get("name"), "time": float(case.get("time") or 0),
                      "status": "FAIL" if failure is not None else "ERROR" if error is not None
                      else "SKIP" if skipped is not None else "PASS",
                      "message": (failure if failure is not None else error).get("message")[:400]
                      if (failure is not None or error is not None) else None})
    return {"exit_code": exit_code, "total": len(cases),
            "passed": sum(c["status"] == "PASS" for c in cases),
            "failed": sum(c["status"] == "FAIL" for c in cases),
            "errors": sum(c["status"] == "ERROR" for c in cases),
            "skipped": sum(c["status"] == "SKIP" for c in cases),
            "duration_seconds": round(sum(c["time"] for c in cases), 3),
            "junitxml": str(xml), "log": str(log), "cases": cases}


def run_suite(root, out_dir, label):
    xml = out_dir / f"{label}.xml"
    log = out_dir / f"{label}.log"
    command = [sys.executable, "-X", "utf8", "-m", "pytest", "tests/", "-q", "-p", "no:cacheprovider",
               "--basetemp", str(root / "data" / f"{label}-tmp"), f"--junitxml={xml}"]
    with log.open("w", encoding="utf-8") as handle:
        result = subprocess.run(command, cwd=root, stdout=handle, stderr=subprocess.STDOUT, text=True)
    return collect_suite(xml, log, result.returncode)


def g1_guard(workbench, baseline_json):
    expected = json.loads(Path(baseline_json).read_text(encoding="utf-8"))
    files, changed, missing = [], [], []
    for rel, want in expected.items():
        path = workbench / rel
        if not path.exists():
            missing.append(rel)
            continue
        actual = sha256(path)
        files.append({"file": rel, "sha256": actual, "expected": want, "unchanged": actual == want})
        if actual != want:
            changed.append(rel)
    return {"total": len(expected), "unchanged": len(files) - len(changed), "changed": changed,
            "missing": missing, "status": "PASS" if not changed and not missing else "FAIL", "files": files}


def acceptance_evidence(suite):
    by_name = {case["name"]: case for case in suite["cases"]}
    return {key: {"test_name": name, "present": name in by_name,
                  "status": by_name.get(name, {}).get("status", "NOT_RUN"),
                  "id": by_name.get(name, {}).get("id")} for key, name in ACCEPTANCE.items()}


def rollout_acceptance_evidence(suite):
    by_name = {case["name"]: case for case in suite["cases"]}
    return {key: {"test_name": name, "present": name in by_name,
                  "status": by_name.get(name, {}).get("status", "NOT_RUN"),
                  "id": by_name.get(name, {}).get("id")} for key, name in ROLLOUT_ACCEPTANCE.items()}


def predicate_inventory():
    from definitio.verifier import CHECK_REGISTRY, SHARED_CHECKS
    verbs = []
    for verb in sorted(CHECK_REGISTRY):
        entry = CHECK_REGISTRY[verb]
        verbs.append({"verb": verb,
                      "checks": entry["checks"],
                      "basic_only_reason": entry.get("basic_only_reason")})
    return {"shared": SHARED_CHECKS, "verbs": verbs,
            "verb_count": len(verbs),
            "basic_only_count": sum(1 for v in verbs if not v["checks"]),
            "check_count": sum(len(v["checks"]) for v in verbs)}


ENVIRONMENT_BLOCKED = {
    "test_tile_binary_http_download_decode_and_expiration",
    "test_actual_sta_all_entity_sets_and_exact_odata_downpush",
    "test_actual_services_filter_and_unknown_service_rejection",
}
ENVIRONMENT_BLOCKED_REASON = (
    "缺 data/backends.json（本机 PostGIS/GeoServer 沙箱配置，含凭据，不随源码包）；"
    "STA 用例还需外部 istSOS 端点，违反本轮离线红线。completion.py:90 在读取配置处即返回 "
    "BACKEND_UNAVAILABLE，未发起任何网络请求。同 10 项在源工作站 D:/agent-native-gis 同样失败（10 FAIL / 27 PASS）。")


def classify_failures(suite):
    blocked, unexpected = [], []
    for case in suite["cases"]:
        if case["status"] == "PASS":
            continue
        base = case["name"].split("[")[0]
        (blocked if base in ENVIRONMENT_BLOCKED else unexpected).append(case["id"])
    return {"environment_blocked": blocked, "environment_blocked_count": len(blocked),
            "environment_blocked_reason": ENVIRONMENT_BLOCKED_REASON,
            "unexpected_failures": unexpected, "unexpected_count": len(unexpected)}


def reconcile_baseline(baseline_xml, suite):
    """Per-ID reconciliation against THIS tree's own pre-change baseline.

    v05 change of口径: new test IDs are listed separately instead of being
    filtered by a hard-coded file list. An unchanged existing verdict can never
    be turned into a failure by adding tests, and new tests can never hide a
    changed or missing existing ID. The 357 existing IDs remain the sole gate.
    """
    if not Path(baseline_xml).exists():
        return {"status": "NOT_RUN", "reason": f"missing baseline {baseline_xml}"}
    before = {case["id"]: case["status"] for case in collect_suite(baseline_xml, baseline_xml, 0)["cases"]}
    after_all = {case["id"]: case["status"] for case in suite["cases"]}
    after = {key: value for key, value in after_all.items() if key in before}
    changes = [{"id": key, "before": before[key], "after": after[key]} for key in before
               if key in after and before[key] != after[key]]
    missing, added = sorted(set(before) - set(after)), sorted(set(after_all) - set(before))
    return {"status": "PASS" if not changes and not missing else "FAIL",
            "baseline_ids": len(before), "final_existing_ids": len(after),
            "baseline_passed": sum(v == "PASS" for v in before.values()),
            "final_existing_passed": sum(v == "PASS" for v in after.values()),
            "verdict_changes": changes, "missing_ids": missing, "added_ids": added,
            "new_test_count": len(added),
            "reconciliation_rule": "既有 ID 逐项对账；新增 ID 单列，不参与零变化判定",
            "baseline_xml": str(baseline_xml)}


def render_markdown(report):
    lines = ["# Definitio 第三轮长线工程（v05 native-push）自动化报告", "",
             f"- 版本目录：`{report['version_directory']}`",
             f"- 定名依据：{report['naming_rationale']}",
             f"- 源码摘要 source_digest：`{report['source_digest']}`",
             f"- 运行时：{report['versions']['runtime']}｜shapely {report['versions']['shapely']}｜GEOS {report['versions']['geos']}｜GDAL {report['versions']['gdal']}",
             f"- 文件总数：{report['file_count']}", "", "## G1 基线", "",
             f"- 状态：**{report['g1_baseline']['status']}**；{report['g1_baseline']['unchanged']}/{report['g1_baseline']['total']} 文件哈希不变",
             f"- 变化：{report['g1_baseline']['changed'] or '无'}；缺失：{report['g1_baseline']['missing'] or '无'}", "",
             "## 发现与错误一致性（D5）", "",
             f"- 总体：{'PASS' if report['discovery_consistency']['ok'] else 'FAIL'}",
             f"- 不一致工具：{[t['name'] for t in report['discovery_tools_inconsistent']] or '无'}",
             f"- 未覆盖 recovery 码：{report['discovery_consistency']['recovery_codes']['missing'] or '无'}",
             f"- 未公布 rule：{report['discovery_consistency']['error_rules']['unpublished'] or '无'}",
             f"- prompt 不一致项：{[p for p in report['discovery_consistency']['prompts'] if not p['consistent']] or '无'}", ""]
    suite = report.get("regression")
    if suite:
        lines += ["## 全量回归", "",
                  f"- 总数 {suite['total']}｜PASS {suite['passed']}｜FAIL {suite['failed']}｜ERROR {suite['errors']}｜SKIP {suite['skipped']}｜耗时 {suite['duration_seconds']}s",
                  f"- 新增测试：{report['new_tests']['total']}（PASS {report['new_tests']['passed']} / FAIL {report['new_tests']['failed']}）",
                  f"- 既有测试：{report['existing_tests']['total']}（PASS {report['existing_tests']['passed']} / FAIL {report['existing_tests']['failed']}）",
                  f"- 证据：`{suite['junitxml']}`、`{suite['log']}`", "",
                  "| 文件 | 总数 | PASS | FAIL |", "|---|---:|---:|---:|"]
        per_file = {}
        for case in suite["cases"]:
            entry = per_file.setdefault(case["file"], [0, 0, 0])
            entry[0] += 1
            entry[1 if case["status"] == "PASS" else 2] += 1
        for name, (total, passed, failed) in sorted(per_file.items()):
            lines.append(f"| {name} | {total} | {passed} | {failed} |")
        rec = report["baseline_reconciliation"]
        lines += ["", "### 既有测试逐 ID 对账", "",
                  f"- 状态：**{rec['status']}**",
                  f"- 基线 {rec['baseline_ids']} ID（PASS {rec['baseline_passed']}） → 终局既有 {rec['final_existing_ids']} ID（PASS {rec['final_existing_passed']}）",
                  f"- 判定变化：{rec['verdict_changes'] or '无'}",
                  f"- 缺失 ID：{rec['missing_ids'] or '无'}；多出 ID：{rec['added_ids'] or '无'}"]
        lines += ["", "### 失败分类", "",
                  f"- 环境阻塞（既有，非本轮引入）：{report['failure_classification']['environment_blocked_count']}",
                  f"- 非预期失败：{report['failure_classification']['unexpected_count']}"
                  + (f" — {report['failure_classification']['unexpected_failures']}" if report["failure_classification"]["unexpected_failures"] else ""),
                  f"- 环境阻塞根因：{report['failure_classification']['environment_blocked_reason']}", "",
                  "### 失败逐项", ""]
        for case in suite["cases"]:
            if case["status"] != "PASS":
                lines.append(f"- `{case['id']}` — {case['status']} — {(case['message'] or '')[:200]}")
        lines += ["", "## 五类验收", "", "| 类别 | 测试 | 状态 |", "|---|---|---|"]
        for key, item in report["acceptance"].items():
            lines.append(f"| {key} | `{item['test_name']}` | {item['status']} |")
        if report.get("rollout_acceptance"):
            lines += ["", "## 本轮推广验收（R1/R3/R4/R5/清单）", "", "| 类别 | 测试 | 状态 |", "|---|---|---|"]
            for key, item in report["rollout_acceptance"].items():
                lines.append(f"| {key} | `{item['test_name']}` | {item['status']} |")
    inventory = report.get("predicate_inventory")
    if inventory:
        lines += ["", "## 逐动词谓词清单（40 动词，无空白）", "",
                  f"- 共享基础检查（矢量提案）：{', '.join(inventory['shared']['vector_proposal'])}",
                  f"- 契约谓词（全动词）：{', '.join(inventory['shared']['contract_predicates'])}",
                  f"- 执行策略检查：{', '.join(inventory['shared']['execution_policy'])}",
                  f"- 专属检查项合计：{inventory['check_count']}；仅基础检查动词：{inventory['basic_only_count']}", "",
                  "| 动词 | 专属检查项（方法｜依据｜层） |", "|---|---|"]
        for entry in inventory["verbs"]:
            if entry["checks"]:
                cell = "<br>".join(f"`{c['id']}`（{c['method']}｜{c['basis']}｜{c['layer']}）" for c in entry["checks"])
            else:
                cell = f"仅基础检查＋{entry['basic_only_reason']}"
            lines.append(f"| {entry['verb']} | {cell} |")
    lines += ["", "## 变更清单", "",
              f"- 基线提交：`{report['changes']['baseline_commit']}`",
              f"- 新增（{len(report['changes']['added'])}）：" + ", ".join(report["changes"]["added"]),
              f"- 修改（{len(report['changes']['modified'])}）：" + ", ".join(report["changes"]["modified"]),
              f"- 未跟踪（{len(report['changes']['untracked'])}）：" + ", ".join(report["changes"]["untracked"][:40]),
              "", "## 已知限制", ""]
    lines += [f"- {item}" for item in report["known_limits"]]
    lines += ["", "## 文件清单与哈希", "", "| 文件 | 字节 | sha256 |", "|---|---:|---|"]
    for name, item in sorted(report["file_manifest"].items()):
        lines.append(f"| {name} | {item['bytes']} | `{item['sha256']}` |")
    return "\n".join(lines) + "\n"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--workbench", type=Path, default=ROOT.parent)
    parser.add_argument("--g1-baseline", type=Path, default=Path("D:/agent-native-gis/evidence/baseline-sha256-before.json"))
    parser.add_argument("--baseline-commit", default="4040567")
    parser.add_argument("--baseline-xml", type=Path, default=None,
                        help="pre-change full-suite junitxml for per-ID reconciliation")
    parser.add_argument("--out", type=Path, default=ROOT / "evidence")
    parser.add_argument("--skip-tests", action="store_true")
    parser.add_argument("--reuse", action="store_true", help="reuse the existing junitxml instead of rerunning pytest")
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)

    from definitio.agent import AgentService
    from definitio.discovery import audit
    from definitio.runtime import Runtime
    import tempfile
    with tempfile.TemporaryDirectory() as tmp:
        service = AgentService(Runtime(Path(tmp) / "report.sqlite3"))
        consistency = audit(service)
        versions = service.runtime.versions

    if args.skip_tests:
        suite = None
    elif args.reuse:
        suite = collect_suite(args.out / "regression-full-suite.xml", args.out / "regression-full-suite.log", 0)
    else:
        suite = run_suite(args.root, args.out, "regression-full-suite")
    files = manifest(args.root)
    baseline_xml = args.baseline_xml or (args.out / "baseline-full-suite.xml")
    report = {
        "format": "definitio-golden-chain-report-v1",
        "version_directory": str(args.root),
        "naming_rationale": "以 07-runtime-v04-rollout 终局（提交 4040567）为起点续 08-runtime-v05-native-push；本地分支 native-push-v05、无远端；06/07 两树只读、G1 基线 59 文件不动",
        "source_digest": versions["source_digest"], "versions": versions,
        "file_manifest": files, "file_count": len(files),
        "changes": change_set(args.root, args.baseline_commit),
        "new_test_files": sorted(name for name in ALL_NEW_TEST_FILES if (args.root / "tests" / name).exists()),
        "g1_baseline": g1_guard(args.workbench, args.g1_baseline),
        "discovery_consistency": {k: v for k, v in consistency.items() if k != "tools"},
        "discovery_tools_inconsistent": [t for t in consistency["tools"] if not t["consistent"]],
        "predicate_inventory": predicate_inventory(),
        "regression": suite,
        "acceptance": acceptance_evidence(suite) if suite else None,
        "rollout_acceptance": rollout_acceptance_evidence(suite) if suite else None,
        "known_limits": [
            "10 项 test_native_completion 依赖本机 PostGIS/GeoServer 沙箱配置与外部 istSOS 端点；本轮离线红线不启动服务、不访问外网，保留红灯并附开工前基线同样失败的对照",
            "干净 venv 安装未验证；本轮显式复用 D:/agent-native-gis/.venv（system-site-packages=true）",
            "真实模型评测未做；引擎未切版，8765/8876 未动",
            "同核复算与 GEOS/PROJ/GDAL/PDAL 同源，是自洽性检查，不是独立几何 oracle，不证明跨后端等价或校准容差",
            "buffer 面积上界用平行体解析界 A+2Lr+πr²（相对松弛 1e-9）；下界用包含关系；点缓冲精确真值仍为正 32 边形（A1）",
            "reproject 往返容差 1e-6×max(1,|坐标|) 为同核 PROJ 浮点噪声界；坐标超 4096 时边界一致性降级无法确认",
            "地形像元抽查为独立 numpy Horn 实现复算，float32 相对容差 1e-6、hillshade uint8 ±1、aspect 环差 1e-4 度",
            "服务动词（publish/serve_tile/list_services/query_observations/run_gp）后端环境阻塞：谓词以合成回执做谓词级验证，未声称为后端实测证据",
            "tile 字节级重算不在验证上下文（二进制句柄属引擎进程），如实列入无法确认段",
            "文件产物只开放 geojson；轴序解析只覆盖 WKT2 AXIS 节点；planner 目标句仍限 1–1000 字（导入长 URI 须走 task_plan 显式步骤）",
            "HTTP 直传导入路径保持第一轮冻结语义（lineage.operation=None，溯源在 source.kind）；控制面导入已补谱系",
        ],
    }
    if suite:
        baseline_ids = ({c["id"] for c in collect_suite(baseline_xml, baseline_xml, 0)["cases"]}
                        if Path(baseline_xml).exists() else set())
        new_ids = [c for c in suite["cases"] if c["id"] not in baseline_ids]
        report["new_tests"] = {"total": len(new_ids), "passed": sum(c["status"] == "PASS" for c in new_ids),
                               "failed": sum(c["status"] != "PASS" for c in new_ids),
                               "ids": [c["id"] for c in new_ids]}
        report["existing_tests"] = {"total": suite["total"] - len(new_ids),
                                    "passed": suite["passed"] - report["new_tests"]["passed"],
                                    "failed": suite["failed"] - report["new_tests"]["failed"]}
        report["failure_classification"] = classify_failures(suite)
        report["baseline_reconciliation"] = reconcile_baseline(baseline_xml, suite)
    (args.out / "golden-chain-report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    (args.out / "golden-chain-report.md").write_text(render_markdown(report), encoding="utf-8")
    print(json.dumps({k: report[k] for k in ("file_count", "g1_baseline", "discovery_consistency", "acceptance")
                      if k in report}, ensure_ascii=False, indent=2)[:4000])
    if suite:
        print(json.dumps({k: suite[k] for k in ("total", "passed", "failed", "errors", "skipped")}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())