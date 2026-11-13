"""Verify an exported bundle's hashes and linked snapshots, without executing GIS tasks.

This verifies integrity and internal consistency, not scientific correctness.
"""
from __future__ import annotations

import argparse
import json
import sys
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from definitio.model import digest, logical_fingerprint, snapshot_fingerprint
from definitio.runtime import Runtime


def verify(bundle):
    errors = []
    payload = {k: v for k, v in bundle.items() if k != "bundle_sha256"}
    if digest(payload) != bundle.get("bundle_sha256"):
        errors.append("bundle_sha256 mismatch")
    task = bundle.get("task", {})
    if Runtime.plan_digest(task) != task.get("plan_digest"):
        errors.append("plan_digest mismatch")
    snapshots = {}
    destroyed = {d["id"]: d for d in bundle.get("destroyed_inputs", [])}
    if bundle.get("destroyed_inputs", []) != task.get("destroyed_inputs", []):
        errors.append("destroyed_inputs mismatch")
    for d in bundle.get("inputs", []) + bundle.get("outputs", []):
        fingerprint = snapshot_fingerprint(d)
        if fingerprint != d["fingerprint"]:
            errors.append(f"snapshot fingerprint mismatch: {d['id']}")
        snapshots[d["id"]] = d
    evidence = {e["id"]: e for e in bundle.get("evidence", [])}
    if set(evidence) != set(task.get("evidence_refs", [])):
        errors.append("evidence_refs mismatch")
    if {d["id"] for d in bundle.get("outputs", [])} != set(task.get("output_refs", [])):
        errors.append("output_refs mismatch")
    for e in evidence.values():
        if digest({k: v for k, v in e.items() if k != "evidence_digest"}) != e.get("evidence_digest"):
            errors.append(f"evidence_digest mismatch: {e['id']}")
        if e.get("task_id") != task.get("id"):
            errors.append(f"evidence task link mismatch: {e['id']}")
        if not all(c.get("passed") is True for c in e.get("postcondition_checks", [])):
            errors.append(f"failed postcondition in committed evidence: {e['id']}")
        for f in e.get("input_fingerprints", []):
            if e.get("verb") == "gis_delete_dataset" and e.get("lifecycle") == "destroy" and f["id"] in destroyed:
                receipt = destroyed[f["id"]]
                if f["after"] is not None or f["before"] != receipt["fingerprint"] or receipt["evidence_ref"] != e["id"] or e.get("input_unchanged") is not False or e.get("target_state") != "destroyed":
                    errors.append(f"destruction receipt mismatch: {e['id']}")
            elif f["id"] not in snapshots or snapshots[f["id"]]["fingerprint"] != f["before"] or f["before"] != f["after"]:
                errors.append(f"input fingerprint link mismatch: {e['id']}")
        if e.get("output_ref") and (e["output_ref"] not in snapshots or snapshots[e["output_ref"]]["fingerprint"] != e.get("output_fingerprint")):
            errors.append(f"output fingerprint link mismatch: {e['id']}")
    for s in task.get("steps", []):
        if s.get("state") == "COMMITTED" and (s.get("evidence_ref") not in evidence or evidence[s["evidence_ref"]]["step_id"] != s["id"]):
            errors.append(f"committed step evidence missing: {s['id']}")
    return {"status": "FAIL" if errors else "PASS", "scope": "integrity_and_links_only", "certification": "NOT_CERTIFIED",
            "snapshots": len(snapshots), "evidence_records": len(evidence), "errors": errors}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("bundle", type=Path)
    args = parser.parse_args()
    try:
        if args.bundle.suffix == ".zip":
            with zipfile.ZipFile(args.bundle) as archive:
                if sum(i.file_size for i in archive.infolist()) > 128 * 1024 * 1024:
                    raise ValueError("bundle exceeds 128 MB uncompressed")
                if archive.testzip():
                    raise ValueError("CRC mismatch")
                bundle = json.loads(archive.read("bundle.json"))
        else:
            bundle = json.loads(args.bundle.read_text(encoding="utf-8"))
        result = verify(bundle)
    except Exception as exc:
        result = {"status": "FAIL", "errors": [str(exc)]}
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
