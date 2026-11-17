"""Read-only guard: the 59 G1 baseline files must stay byte-identical.

G1 guard root fix: the workbench root comes from an EXPLICIT parameter (or the
DEFINITIO_WORKBENCH_ROOT environment variable) and the 59-file baseline ships
WITH the package. Missing evidence is enumerated and fails loudly; it is never
skipped, never inferred from another drive, never silently graded PASS.
No historical G1 file is modified.
"""
import json
from pathlib import Path

import pytest

from scripts.audit_g1 import (BUNDLED_BASELINE, check_baseline,
                              resolve_workbench_root, review)

WORKBENCH, ROOT_SOURCE = resolve_workbench_root()
BASELINE = BUNDLED_BASELINE


def test_g1_baseline_59_files_are_byte_identical():
    report = check_baseline(WORKBENCH, BASELINE)
    assert report["status"] == "PASS", report
    assert report["expected_count"] == 59
    assert report["checked"] == 59
    assert not report["missing_files"] and not report["changed_files"]
    assert not report["handoff_missing"]


def test_g1_registry_readonly_audit_still_runs():
    result = review(WORKBENCH, BASELINE)
    assert result["evidence"]["status"] == "PASS"
    assert result["integrity"]["status"] == "PASS"
    assert result["registry"]["status"] == "PASS"
    assert result["G1_acceptance"] == "NOT_ISSUED"


def test_g1_missing_evidence_is_reported_not_skipped(tmp_path):
    """An explicitly-passed empty root must report every missing item."""
    report = check_baseline(tmp_path, BASELINE)
    assert report["status"] == "MISSING"
    assert len(report["missing_files"]) == 59
    assert report["handoff_missing"]
    result = review(tmp_path, BASELINE)
    assert result["status"] == "MISSING"
    assert result["integrity"]["status"] == "MISSING"
    assert result["G1_acceptance"] == "NOT_ISSUED"
    assert result["missing_evidence"]["files"]


def test_g1_missing_baseline_manifest_is_reported(tmp_path):
    report = check_baseline(WORKBENCH, tmp_path / "absent.json")
    assert report["status"] == "MISSING"
    assert report["baseline_present"] is False
    assert report["reason"] == "bundled_baseline_manifest_missing"


def test_g1_explicit_root_parameter_is_honoured(monkeypatch, tmp_path):
    monkeypatch.setenv("DEFINITIO_WORKBENCH_ROOT", str(tmp_path))
    root, source = resolve_workbench_root()
    assert root == tmp_path.resolve()
    assert source == "environment_DEFINITIO_WORKBENCH_ROOT"
    explicit, source2 = resolve_workbench_root(WORKBENCH)
    assert explicit == Path(WORKBENCH).resolve()
    assert source2 == "explicit_parameter"


def test_g1_bundled_baseline_is_complete_and_self_contained():
    assert BASELINE.exists()
    expected = json.loads(BASELINE.read_text(encoding="utf-8"))
    assert len(expected) == 59
    assert all(len(v) == 64 and all(c in "0123456789abcdef" for c in v) for v in expected.values())
    assert "D:" not in str(BASELINE) and "D:/" not in BASELINE.read_text(encoding="utf-8")
