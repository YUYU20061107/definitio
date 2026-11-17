"""Phase 0 dual-tree reconciliation: do the D-side N-01..N-03 defects exist on the 08 tree?
Runs against the CURRENT 08 published tree code, isolated temp workspaces only.
N-04 is checked statically (guidance registries) and reported from source, not here.
"""
import json
import sqlite3
import sys
import tempfile
import traceback
from pathlib import Path

TREE = Path(r"C:\Users\RAHUYU\WorkBuddy\2026-11-09-10-00-10\agent-native-gis\08-runtime-v05-native-push")
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(TREE))

from shapely.geometry import box, mapping  # noqa: E402
from definitio.runtime import Runtime  # noqa: E402
from definitio.agent import AgentService  # noqa: E402

ROOT = Path(tempfile.mkdtemp(prefix="nchecks-", dir=HERE))
out = {}


def env(case):
    rt = Runtime(ROOT / case / "workspace.sqlite3")
    return rt, AgentService(rt)


def src(rt, name="src"):
    return rt.import_dataset(name, {"type": "FeatureCollection", "features": [
        {"type": "Feature", "geometry": mapping(box(0, 0, 10, 10)),
         "properties": {"value": 7}}]}, "EPSG:32649")


def buffer_plan(rt, d, exports=None):
    return rt.declare("ncheck buffer", [{"verb": "gis_buffer", "parameters": {
        "input": d["id"], "target": "safe_buf", "distance": 10, "unit": "m",
        "dissolve": False}}], exports=exports)


# ---- N-01: task_get after the committed output DATASET is deleted ----
try:
    rt, service = env("n01")
    t = buffer_plan(rt, src(rt))
    t = rt.execute(t["id"], t["plan_digest"], True)
    assert t["task_state"] == "COMMITTED", t.get("error")
    out_ref = t["output_refs"][0]
    # simulate external deletion of the output dataset row (state, not engine op)
    con = sqlite3.connect(str(rt.store.path))
    tables = [r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")]
    deleted = None
    for tbl in ("datasets", "dataset", "workspace_datasets"):
        if tbl in tables:
            cur = con.execute(f"DELETE FROM {tbl} WHERE id=?", (out_ref,))
            deleted = (tbl, cur.rowcount)
            break
    con.commit(); con.close()
    res = {"committed_output": out_ref, "tables": tables, "deleted_row": deleted}
    try:
        reply = service.call("task_get", {"task_id": t["id"]})
        res["task_get_plain"] = {"ok": reply.get("ok"), "error": (reply.get("error") or {}).get("code")}
    except Exception as exc:
        res["task_get_plain"] = {"exception": type(exc).__name__, "message": str(exc)[:300]}
    try:
        reply = service.call("task_get", {"task_id": t["id"], "verify": True})
        ver = (reply.get("data") or {}).get("verification") or {}
        res["task_get_verify"] = {"ok": reply.get("ok"), "error": (reply.get("error") or {}).get("code"),
                                  "verification_ok": ver.get("ok"), "verification_status": ver.get("status")}
    except Exception as exc:
        res["task_get_verify"] = {"exception": type(exc).__name__, "message": str(exc)[:300]}
    out["N-01"] = res
except Exception:
    out["N-01"] = {"probe_error": traceback.format_exc()[-800:]}

# ---- N-02: file exports counted against their own explicit cap ----
try:
    rt, service = env("n02")
    d = src(rt)
    plan = buffer_plan(rt, d, exports=[{"step": 1, "name": "a.geojson", "format": "geojson"},
                                       {"step": 1, "name": "b.geojson", "format": "geojson"}])
    session = "ncheck-session-" + "y" * 32
    g = service.call("authorization_grant", {
        "target_prefix": "safe_", "verbs": ["gis_buffer"], "max_writes": 1,
        "capabilities": ["dataset", "file_export"], "max_file_exports": 1,
        "ttl_seconds": 600, "confirm": True, "request_id": "n02-grant"}, session=session)
    gid = g["data"]["authorization"]["id"]
    reply = service.call("task_execute", {"task_id": plan["id"], "plan_digest": plan["plan_digest"],
                                          "authorization_id": gid}, session=session)
    artifacts = rt.store.path.parent / "artifacts"
    out["N-02"] = {
        "grant": service.authorizations.get(gid, session),
        "execute_ok": reply.get("ok"),
        "execute_error": (reply.get("error") or {}).get("code"),
        "files_created": sorted(p.name for p in artifacts.glob("*.geojson")) if artifacts.exists() else [],
        "task_state": rt.store.task(plan["id"])["task_state"],
        "actual_writes": rt.store.task(plan["id"]).get("actual_writes"),
    }
except Exception:
    out["N-02"] = {"probe_error": traceback.format_exc()[-800:]}

# ---- N-03: file_export capability grantable through the agent surface ----
try:
    rt, service = env("n03")
    session = "ncheck-session-" + "z" * 32
    g = service.call("authorization_grant", {
        "target_prefix": "safe_", "verbs": ["gis_buffer"], "max_writes": 1,
        "capabilities": ["dataset", "file_export"], "max_file_exports": 2,
        "ttl_seconds": 600, "confirm": True, "request_id": "n03-grant"}, session=session)
    auth = (g.get("data") or {}).get("authorization") or {}
    out["N-03"] = {"grant_ok": g.get("ok"), "capabilities": auth.get("capabilities"),
                   "effect_capabilities": auth.get("effect_capabilities"),
                   "max_file_exports": auth.get("max_file_exports"),
                   "error": (g.get("error") or {}).get("code")}
except Exception:
    out["N-03"] = {"probe_error": traceback.format_exc()[-800:]}

(HERE / "n-checks-result.json").write_text(json.dumps(out, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
print(json.dumps(out, ensure_ascii=False, indent=2, default=str), flush=True)
