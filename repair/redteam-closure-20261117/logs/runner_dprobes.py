"""Phase 0/5 independent re-run: D-drive ORIGINAL red-team probes x current 08 tree.

Probe source: byte-identical copy of D:\\agent-native-gis\\evidence\\redteam-20261116\\probes.py
(sha256 must equal 56ffb951b27f46f302d5245d5e66e05ed2da0a2ccad510d3b8d4a9377592ceab).
Defect booleans: same 13 checks as the red team's own assertions, evaluated
tolerantly (the original runner tail asserts and crashes on None after fix).
"""
import hashlib
import json
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
TREE = Path(r"C:\Users\RAHUYU\WorkBuddy\2026-11-09-10-00-10\agent-native-gis\08-runtime-v05-native-push")
PROBE = HERE / "probes.py"
digest = hashlib.sha256(PROBE.read_bytes()).hexdigest()
print("PROBE_SHA256", digest, flush=True)
assert digest == "56ffb951b27f46f302d5245d5e66e05ed2da0a2ccad510d3b8d4a9377592ceab", digest
sys.path.insert(0, str(TREE))

source = PROBE.read_text(encoding="utf-8").split("for name, fn in [", 1)[0]
ns = {"__file__": str(PROBE), "__name__": "redteam_probe_cases_phase0"}
exec(compile(source, str(PROBE), "exec"), ns)
ns["ROOT"] = Path(tempfile.mkdtemp(prefix="phase0-", dir=HERE))

NAMES = ("buffer_wrong_geometry", "clip_wrong_geometry", "grant_export",
         "replay_missing_file", "artifact_readback_failure", "failed_task_verify",
         "readonly_verify", "multipoint_buffer", "clip_duplicate_properties",
         "empty_reproject", "join_wrong_attributes", "hillshade_dark_slope",
         "external_publish_invalid_json")
R = {}
errors = {}
for name in NAMES:
    try:
        R[name] = ns[name]()
    except Exception as exc:  # a crashing case is recorded, never silently passed
        errors[name] = repr(exc)
        R[name] = {}


def g(value, *keys):
    for key in keys:
        if not isinstance(value, dict):
            return None
        value = value.get(key)
    return value


defects = {
    "buffer_wrong_geometry": (R["buffer_wrong_geometry"].get("task_state") == "COMMITTED"
                              and R["buffer_wrong_geometry"].get("verify") is True
                              and R["buffer_wrong_geometry"].get("distance_from_input", 0) > 1000),
    "clip_wrong_geometry": (R["clip_wrong_geometry"].get("task_state") == "COMMITTED"
                            and R["clip_wrong_geometry"].get("intersects_clip_mask") is False),
    "grant_export": (R["grant_export"].get("ok") is True and R["grant_export"].get("file_created") is True),
    "replay_missing_file": (R["replay_missing_file"].get("replay_ok") is True
                            and g(R["replay_missing_file"], "verify_reply", "data", "verification", "ok") is True),
    "artifact_readback_failure": (R["artifact_readback_failure"].get("registered_artifacts") == 1
                                  and R["artifact_readback_failure"].get("file_exists") is False
                                  and g(R["artifact_readback_failure"], "resume", "task_state") == "FAILED"),
    "failed_task_verify": (g(R["failed_task_verify"], "verify", "ok") is True
                           and g(R["failed_task_verify"], "verify", "steps") == []),
    "readonly_verify": (g(R["readonly_verify"], "reply", "data", "verification", "ok") is True
                        and g(R["readonly_verify"], "reply", "data", "results", 0, "value",
                              "groups", 0, "fields", "value", "sum") == 999999),
    "multipoint_buffer": g(R["multipoint_buffer"], "error", "details", "check") == "buffer_area_upper_bound",
    "clip_duplicate_properties": g(R["clip_duplicate_properties"], "error", "details", "check") == "subset_within_input",
    "empty_reproject": R["empty_reproject"].get("task_state") == "FAILED",
    "join_wrong_attributes": (R["join_wrong_attributes"].get("task_state") == "COMMITTED"
                              and g(R["join_wrong_attributes"], "actual_properties", "zone") == "WRONG_ZONE"),
    "hillshade_dark_slope": (R["hillshade_dark_slope"].get("native_center_pixel") == 1
                             and g(R["hillshade_dark_slope"], "task", "error", "details", "check") == "raster_pixel_spot"),
    "external_publish_invalid_json": (R["external_publish_invalid_json"].get("step_state") == "FAILED"
                                      and len(R["external_publish_invalid_json"].get("mock_side_effect_requests", [])) == 3
                                      and R["external_publish_invalid_json"].get("execution_started") is False),
}
still = [name for name, value in defects.items() if value]
out = {"probe_sha256": digest, "tree": str(TREE), "defect_assertions": defects,
       "still_reproducing": still, "count": len(still), "total": len(defects),
       "case_errors": errors}
(HERE / "phase0-dprobes-result.json").write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
print("DEFECTS_STILL_REPRODUCING", len(still), "/", len(defects), flush=True)
print("STILL_REPRODUCING", json.dumps(still), flush=True)
print("CASE_ERRORS", json.dumps(errors), flush=True)
