"""Black-box Agent workflow using the official MCP client and actual stdio.

No GUI, local intent planner, GIS runtime import, Shapely or paid model API is
used by this client. Rectangle area checks use independent shoelace arithmetic.
"""
from __future__ import annotations

import argparse
import asyncio
from datetime import timedelta
import hashlib
import json
from pathlib import Path
import sys
import tempfile
import uuid

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

ROOT = Path(__file__).resolve().parents[1]


def json_hash(value):
    raw = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def ring_area(ring):
    return abs(sum(a[0] * b[1] - b[0] * a[1] for a, b in zip(ring, ring[1:]))) / 2


def polygon_area(polygon):
    return ring_area(polygon[0]) - sum(ring_area(hole) for hole in polygon[1:])


async def exercise(engine_url, prefix=None, client_cwd=None):
    prefix = prefix or "mcp-" + uuid.uuid4().hex[:10]
    calls = []
    params = StdioServerParameters(command=sys.executable,
             args=[str(ROOT / "run_mcp.py"), "--engine-url", engine_url, "--tool-profile", "full"],
             cwd=str(client_cwd or ROOT), env={"PYTHONIOENCODING": "utf-8"})
    with tempfile.TemporaryFile(mode="w+", encoding="utf-8") as error_log:
        async with stdio_client(params, errlog=error_log) as (read, write):
            async with ClientSession(read, write, read_timeout_seconds=timedelta(seconds=60)) as session:
                initialized = await session.initialize()
                discovered = await session.list_tools()
                tools = {t.name: t for t in discovered.tools}
                assert len(tools) == 60 and "gis_dissolve" in tools
                assert tools["gis_dissolve"].inputSchema["properties"]["fields"]["type"] == "array"
                assert initialized.instructions and "calling Agent owns planning" in initialized.instructions

                async def call(name, arguments, error=None):
                    result = await session.call_tool(name, arguments)
                    payload = result.structuredContent
                    assert isinstance(payload, dict), result
                    assert result.isError is (not payload["ok"])
                    if error:
                        assert not payload["ok"] and payload["error"]["code"] == error, payload
                    else:
                        assert payload["ok"], payload
                    calls.append({"tool": name, "isError": result.isError, "response": payload})
                    return payload["data"] if not error else payload

                before = await call("workspace_list", {})
                raw = json.loads((ROOT / "examples/analytic-dissolve.geojson").read_text(encoding="utf-8"))
                imported = await call("dataset_import", {"name": prefix + "_input", "geojson": raw, "crs": "EPSG:32649",
                    "source_kind": "synthetic_fixture", "request_id": prefix + "_import", "confirm": True})
                source = imported["dataset"]
                args = {"input": source["id"], "target": prefix + "_groups", "fields": ["group_code"], "request_id": prefix + "_dissolve"}
                await call("gis_dissolve", {**args, "fields": "group_code"}, "INVALID_ARGUMENT")
                await call("gis_dissolve", args, "CONFIRM_REQUIRED")
                done = await call("gis_dissolve", {**args, "confirm": True})
                again = await call("gis_dissolve", {**args, "confirm": True})
                assert again["replayed"] is True
                assert done["task"]["id"] == again["task"]["id"]
                assert done["outputs"] == again["outputs"]
                await call("gis_dissolve", {**args, "confirm": True, "target": prefix + "_different"}, "INVALID_ARGUMENT")
                output = done["outputs"][0]
                assert output["feature_count"] == 3

                async def artifact(uri):
                    offset, content = 0, ""
                    while offset is not None:
                        page = await call("artifact_read", {"uri": uri, "offset": offset, "max_chars": 128})
                        content += page["content"]
                        offset = page["next_offset"]
                    value = json.loads(content)
                    assert json_hash(value) == page["sha256_json_utf8"]
                    return value

                first = await call("dataset_features", {"dataset": output["id"], "limit": 2})
                second = await call("dataset_features", {"dataset": output["id"], "offset": first["next_offset"], "limit": 2})
                rows = first["items"] + second["items"]
                assert second["next_offset"] is None and len(rows) == 3
                truth = {}
                for record in rows:
                    geom = await artifact(record["geometry_uri"])
                    assert geom["type"] == "MultiPolygon"
                    truth[record["properties"]["group_code"]] = {"area": sum(polygon_area(p) for p in geom["coordinates"]), "parts": len(geom["coordinates"])}
                assert truth == {"A": {"area": 3.0, "parts": 2}, "B": {"area": 1.0, "parts": 1}, None: {"area": 1.0, "parts": 1}}

                evidence_id = done["task"]["evidence_refs"][0]
                summary = await call("evidence_get", {"evidence_id": evidence_id})
                assert summary["evidence"]["input_unchanged"] is True
                groups_a = await call("evidence_get", {"evidence_id": evidence_id, "section": "groups", "limit": 2})
                groups_b = await call("evidence_get", {"evidence_id": evidence_id, "section": "groups", "offset": groups_a["next_offset"], "limit": 2})
                assert sorted(n for group in groups_a["items"] + groups_b["items"] for n in group["source_records"]) == [1, 2, 3, 4, 5]
                checked = await call("gis_validate_geometry", {"dataset": output["id"], "repair": False})
                assert checked["results"][0]["value"]["valid"] is True
                source_after = await call("dataset_describe", {"dataset": source["id"]})
                assert source_after["dataset"]["fingerprint"] == source["fingerprint"]
                await call("gis_delete_dataset", {}, "INVALID_ARGUMENT")

                planned = await call("task_plan", {"goal": "Agent-authored buffer then validation", "request_id": prefix + "_plan", "steps": [
                    {"verb": "gis_buffer", "parameters": {"input": source["id"], "target": prefix + "_buffer", "distance": .5, "unit": "m", "dissolve": False}},
                    {"verb": "gis_validate_geometry", "parameters": {"dataset": "$previous", "repair": False}}]})
                task = planned["task"]
                await call("task_execute", {"task_id": task["id"], "plan_digest": task["plan_digest"]}, "CONFIRM_REQUIRED")
                chain = await call("task_execute", {"task_id": task["id"], "plan_digest": task["plan_digest"], "confirm": True})
                assert chain["task"]["task_state"] == "COMMITTED" and len(chain["task"]["evidence_refs"]) == 2
                reread = await call("task_get", {"task_id": task["id"]})
                assert reread["task"]["output_refs"] == chain["task"]["output_refs"]
                resources = await session.list_resources()
                templates = await session.list_resource_templates()
                resource = await session.read_resource(f"definitio://datasets/{output['id']}")
                assert resources.resources and len(templates.resourceTemplates) == 3
                assert json.loads(resource.contents[0].text)["ok"] is True
                after = await call("workspace_list", {})
                assert after["total"] == before["total"] + 3  # input, dissolve, buffer; retries add none
    return {"status": "PASS", "transport": "official MCP SDK / actual stdio / actual headless HTTP engine",
            "tools_discovered": len(tools), "tool_calls": len(calls), "calls": calls,
            "independent_rectangle_oracle": {"method": "shoelace arithmetic; no shared GIS kernel in client", "A_area": 3, "A_parts": 2, "B_area": 1, "null_area": 1},
            "source_unchanged": True, "replay_deduplicated": True,
            "no_browser": True, "no_builtin_language_planner": True, "model_api_calls": 0,
            "certification": "NOT_CERTIFIED"}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--engine-url", default="http://127.0.0.1:8765")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    report = asyncio.run(exercise(args.engine_url))
    if args.output:
        args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({k: v for k, v in report.items() if k != "calls"}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
