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
NEW_TEST_FILES = ("test_golden_semantics.py", "test_golden_verifier.py", "test_golden_commit.py",
                  "test_golden_resume.py", "test_golden_discovery.py", "test_golden_chain.py",
                  "test_golden_g1_guard.py")


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
    """Per-ID reconciliation: no existing test may silently change verdict."""
    if not Path(baseline_xml).exists():
        return {"status": "NOT_RUN", "reason": f"missing baseline {baseline_xml}"}
    before = {case["id"]: case["status"] for case in collect_suite(baseline_xml, baseline_xml, 0)["cases"]}
    new_classes = {"tests." + name[:-3] for name in NEW_TEST_FILES}
    after = {case["id"]: case["status"] for case in suite["cases"] if case["file"] not in new_classes}
    changes = [{"id": key, "before": before[key], "after": after[key]} for key in before
               if key in after and before[key] != after[key]]
    missing, added = sorted(set(before) - set(after)), sorted(set(after) - set(before))
    return {"status": "PASS" if not changes and not missing and not added else "FAIL",
            "baseline_ids": len(before), "final_existing_ids": len(after),
            "baseline_passed": sum(v == "PASS" for v in before.values()),
            "final_existing_passed": sum(v == "PASS" for v in after.values()),
            "verdict_changes": changes, "missing_ids": missing, "added_ids": added,
            "baseline_xml": str(baseline_xml)}


def render_markdown(report):
    lines = ["# Definitio 黄金链纵切（v03）自动化报告", "",
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
    parser.add_argument("--baseline-commit", default="491687c")
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
    report = {
        "format": "definitio-golden-chain-report-v1",
        "version_directory": str(args.root),
        "naming_rationale": "现场编号 00-baseline…05-reference-runtime 已占用，续 06；scripts/audit_g1.py 的默认 root=parents[2] 只有在该布局下才指向工作台根",
        "source_digest": versions["source_digest"], "versions": versions,
        "file_manifest": files, "file_count": len(files),
        "changes": change_set(args.root, args.baseline_commit),
        "new_test_files": sorted(name for name in NEW_TEST_FILES if (args.root / "tests" / name).exists()),
        "g1_baseline": g1_guard(args.workbench, args.g1_baseline),
        "discovery_consistency": {k: v for k, v in consistency.items() if k != "tools"},
        "discovery_tools_inconsistent": [t for t in consistency["tools"] if not t["consistent"]],
        "regression": suite,
        "acceptance": acceptance_evidence(suite) if suite else None,
        "known_limits": [
            "10 项 test_native_completion 依赖本机 PostGIS/GeoServer 沙箱配置与外部 istSOS 端点；本轮离线红线不启动服务、不访问外网，保留红灯并附源工作站同样失败的对照",
            "干净 venv 安装未验证；本轮显式复用 D:/agent-native-gis/.venv（system-site-packages=true）",
            "真实模型评测未做；引擎未切版，8765/8876 未动",
            "D1–D5 只沿黄金链落实，40 动词未全量推广",
            "文件产物首版只开放 geojson；轴序解析只覆盖 WKT2 AXIS 节点",
            "读回核对与 GEOS 同源，不是独立几何 oracle；缓冲真值用正 32 边形解析面积，非 πr²",
        ],
    }
    if suite:
        new_ids = [c for c in suite["cases"] if c["file"].split(".")[-1] + ".py" in NEW_TEST_FILES]
        report["new_tests"] = {"total": len(new_ids), "passed": sum(c["status"] == "PASS" for c in new_ids),
                               "failed": sum(c["status"] != "PASS" for c in new_ids),
                               "ids": [c["id"] for c in new_ids]}
        report["existing_tests"] = {"total": suite["total"] - len(new_ids),
                                    "passed": suite["passed"] - report["new_tests"]["passed"],
                                    "failed": suite["failed"] - report["new_tests"]["failed"]}
        report["failure_classification"] = classify_failures(suite)
        report["baseline_reconciliation"] = reconcile_baseline(args.out / "baseline-full-suite-r2.xml", suite)
    (args.out / "golden-chain-report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    (args.out / "golden-chain-report.md").write_text(render_markdown(report), encoding="utf-8")
    print(json.dumps({k: report[k] for k in ("file_count", "g1_baseline", "discovery_consistency", "acceptance")
                      if k in report}, ensure_ascii=False, indent=2)[:4000])
    if suite:
        print(json.dumps({k: suite[k] for k in ("total", "passed", "failed", "errors", "skipped")}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())