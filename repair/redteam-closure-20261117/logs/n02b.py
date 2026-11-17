"""N-02b: counting logic below the tool schema. Grant created via Authorizations.grant()
directly (capabilities + max_file_exports=1), plan asks for TWO file exports on one step.
If exports are counted, execution must refuse BEFORE creating any file."""
import json
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

ROOT = Path(tempfile.mkdtemp(prefix="n02b-", dir=HERE))
rt = Runtime(ROOT / "workspace.sqlite3")
service = AgentService(rt)
d = rt.import_dataset("src", {"type": "FeatureCollection", "features": [
    {"type": "Feature", "geometry": mapping(box(0, 0, 10, 10)), "properties": {"value": 7}}]}, "EPSG:32649")
plan = rt.declare("n02b buffer", [{"verb": "gis_buffer", "parameters": {
    "input": d["id"], "target": "safe_buf", "distance": 10, "unit": "m", "dissolve": False}}],
    exports=[{"step": 1, "name": "a.geojson", "format": "geojson"},
             {"step": 1, "name": "b.geojson", "format": "geojson"}])
session = "n02b-session-" + "w" * 32
try:
    grant = service.authorizations.grant({
        "target_prefix": "safe_", "verbs": ["gis_buffer"], "max_writes": 1,
        "capabilities": ["dataset", "file_export"], "max_file_exports": 1,
        "ttl_seconds": 600, "confirm": True, "request_id": "n02b-grant"}, session)
    res = {"grant_created": True, "grant_capabilities": grant.get("capabilities"),
           "grant_max_file_exports": grant.get("max_file_exports")}
    reply = service.call("task_execute", {"task_id": plan["id"], "plan_digest": plan["plan_digest"],
                                          "authorization_id": grant["id"]}, session=session)
    artifacts = rt.store.path.parent / "artifacts"
    res.update({"execute_ok": reply.get("ok"), "execute_error": (reply.get("error") or {}).get("code"),
                "files_created": sorted(p.name for p in artifacts.glob("*.geojson")) if artifacts.exists() else [],
                "task_state": rt.store.task(plan["id"])["task_state"],
                "actual_writes": rt.store.task(plan["id"]).get("actual_writes")})
except Exception:
    res = {"probe_error": traceback.format_exc()[-600:]}
print(json.dumps(res, ensure_ascii=False, indent=2, default=str), flush=True)
(HERE / "n02b-result.json").write_text(json.dumps(res, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
