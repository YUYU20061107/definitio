"""T6: the consistency suite must be able to fail, and the staging tree must be honest."""
from __future__ import annotations

import hashlib
import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest

from definitio.agent import AgentService
from definitio.runtime import Runtime

ROOT = Path(__file__).resolve().parents[1]


def _load_selfcheck():
    spec = importlib.util.spec_from_file_location("consistency_selfcheck",
                                                  ROOT / "scripts" / "consistency_selfcheck.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def service(tmp_path):
    return AgentService(Runtime(tmp_path / "selfcheck.sqlite3"))


def test_no_seeded_violation_is_missed(service):
    cases, failures = _load_selfcheck().collect(service)
    assert failures == [], f"检查器漏检：{failures}"
    assert len(cases) >= 20


def test_every_seeded_violation_is_actually_caught(service):
    cases, _ = _load_selfcheck().collect(service)
    violations = [c for c in cases if c["expected_ok"] is False]
    assert len(violations) >= 8, "违规样本太少，自检说服力不足"
    for case in violations:
        assert case["caught"] is True, case
        assert case["actual_ok"] is False, case


def test_clean_samples_do_not_false_positive(service):
    cases, _ = _load_selfcheck().collect(service)
    clean = [c for c in cases if c["expected_ok"] is True]
    assert clean and all(c["actual_ok"] is True for c in clean)


def test_selfcheck_report_is_reproducible(service):
    first = _load_selfcheck().collect(service)[0]
    second = _load_selfcheck().collect(service)[0]
    assert [c["case"] for c in first] == [c["case"] for c in second]
    assert [(c["case"], c["caught"]) for c in first] == [(c["case"], c["caught"]) for c in second]


def test_cli_selfcheck_exit_code_is_real():
    result = subprocess.run([sys.executable, "-X", "utf8", "scripts/consistency_selfcheck.py"],
                            cwd=ROOT, capture_output=True, text=True, encoding="utf-8")
    assert result.returncode == 0, result.stdout[-2000:] + result.stderr[-2000:]
    assert '"ok": true' in result.stdout


def test_release_staging_manifest_matches_its_files(tmp_path):
    out = tmp_path / "staging"
    result = subprocess.run([sys.executable, "-X", "utf8", "scripts/build_release_staging.py",
                             "--out", str(out)], cwd=ROOT, capture_output=True, text=True,
                            encoding="utf-8")
    assert result.returncode == 0, result.stdout + result.stderr
    manifest = json.loads((out / "MANIFEST.json").read_text(encoding="utf-8"))

    assert manifest["published"] is False and manifest["pushed"] is False
    assert manifest["certification"] == "NOT_CERTIFIED"
    for item in manifest["files"]:
        digest = hashlib.sha256((out / item["path"]).read_bytes()).hexdigest()
        assert digest == item["sha256"], item["path"]
    payload = "\n".join(f"{i['path']}:{i['sha256']}"
                        for i in sorted(manifest["files"], key=lambda i: i["path"]))
    assert hashlib.sha256(payload.encode("utf-8")).hexdigest() == manifest["tree_sha256"]
    assert manifest["stale_files"] == []


def test_staging_contains_no_credentials_or_engine_data(tmp_path):
    out = tmp_path / "staging"
    subprocess.run([sys.executable, "-X", "utf8", "scripts/build_release_staging.py", "--out", str(out)],
                   cwd=ROOT, check=True, capture_output=True)
    names = {p.name for p in out.rglob("*") if p.is_file()}
    assert not any(name.endswith((".sqlite3", ".db", ".log", ".whl")) for name in names)
    text = "".join(p.read_text(encoding="utf-8", errors="ignore") for p in out.rglob("*")
                   if p.is_file() and p.suffix in {".md", ".py", ".json"})
    # 拼接字面量：本文件自身会被暂存进套件，不能把规则文本写成可被自己命中的整串
    for forbidden in ("BACKEND_" "PASSWORD", "postgres" "://", "aws_" "secret", "Bearer " "ey"):
        assert forbidden not in text, f"暂存树疑似含凭据：{forbidden[:8]}..."
