"""Read-only guard: the 59 G1 baseline files must stay byte-identical."""
import hashlib
import json
from pathlib import Path

WORKBENCH = Path(__file__).resolve().parents[2]
BASELINE = Path("D:/agent-native-gis/evidence/baseline-sha256-before.json")


def test_g1_baseline_59_files_are_byte_identical():
    expected = json.loads(BASELINE.read_text(encoding="utf-8"))
    assert len(expected) == 59
    changed, missing = [], []
    for rel, want in expected.items():
        path = WORKBENCH / rel
        if not path.exists():
            missing.append(rel)
            continue
        if hashlib.sha256(path.read_bytes()).hexdigest() != want:
            changed.append(rel)
    assert not missing and not changed, {"missing": missing, "changed": changed}


def test_g1_registry_readonly_audit_still_runs():
    from scripts.audit_g1 import review
    result = review(WORKBENCH)
    assert result["integrity"]["status"] == "PASS"
    assert result["registry"]["status"] == "PASS"
    assert result["G1_acceptance"] == "NOT_ISSUED"