"""T4 performance baseline: four groups of measured numbers, no claims without data.

All timings are real AgentService calls in isolated temporary workspaces.
No port is bound, no online engine is touched, no model/API call is made.
"""
from __future__ import annotations

import argparse
import json
import statistics
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

GEOJSON = {"type": "FeatureCollection", "features": [
    {"type": "Feature",
     "geometry": {"type": "Polygon", "coordinates": [[[0, 0], [2, 0], [2, 2], [0, 2], [0, 0]]]},
     "properties": {"g": "A"}},
    {"type": "Feature",
     "geometry": {"type": "Polygon", "coordinates": [[[3, 0], [5, 0], [5, 2], [3, 2], [3, 0]]]},
     "properties": {"g": "B"}}]}


def timed(call, name, arguments):
    started = time.perf_counter()
    reply = call(name, arguments)
    elapsed = time.perf_counter() - started
    return elapsed, reply


def run_chain(call, tag):
    """Six real golden-chain operations; returns per-step seconds."""
    result = {}
    elapsed, reply = timed(call, "dataset_import", {
        "name": f"src_{tag}", "geojson": GEOJSON, "crs": "EPSG:32649",
        "confirm": True, "request_id": f"import_{tag}", "source_kind": "synthetic_fixture"})
    assert reply["ok"], reply
    source = reply["data"]["dataset"]["id"]
    result["1_dataset_import"] = elapsed

    elapsed, reply = timed(call, "gis_copy_dataset", {
        "source": source, "target": f"copy_{tag}", "confirm": True, "request_id": f"copy_{tag}"})
    assert reply["ok"], reply
    result["2_gis_copy_dataset"] = elapsed

    elapsed, reply = timed(call, "gis_buffer", {
        "input": source, "target": f"buf_{tag}", "distance": 1, "unit": "m",
        "dissolve": False, "confirm": True, "request_id": f"buf_{tag}"})
    assert reply["ok"], reply
    buffered = reply["data"]["outputs"][0]["id"]
    result["3_gis_buffer"] = elapsed

    elapsed, reply = timed(call, "gis_dissolve", {
        "input": source, "target": f"dis_{tag}", "fields": ["g"],
        "confirm": True, "request_id": f"dis_{tag}"})
    assert reply["ok"], reply
    result["4_gis_dissolve"] = elapsed

    elapsed, reply = timed(call, "gis_measure", {
        "operation": "area", "a_wkt": "POLYGON ((0 0,2 0,2 3,0 3,0 0))", "crs": "EPSG:32649"})
    assert reply["ok"], reply
    result["5_gis_measure"] = elapsed

    elapsed, reply = timed(call, "gis_validate_geometry", {"dataset": buffered, "repair": False})
    assert reply["ok"], reply
    result["6_gis_validate_geometry"] = elapsed
    return result


def cold_hot_table(repeats=3):
    from definitio.agent import AgentService
    from definitio.runtime import Runtime
    cold, hot = [], []
    for index in range(repeats):
        with tempfile.TemporaryDirectory() as tmp:
            service = AgentService(Runtime(Path(tmp) / f"cold{index}.sqlite3"))
            cold.append(run_chain(service.call, f"cold{index}"))
    with tempfile.TemporaryDirectory() as tmp:
        service = AgentService(Runtime(Path(tmp) / "hot.sqlite3"))
        run_chain(service.call, "warmup")  # warm-up chain, excluded from hot numbers
        for index in range(repeats):
            hot.append(run_chain(service.call, f"hot{index}"))
    table = {}
    for step in cold[0]:
        cold_values = [run[step] for run in cold]
        hot_values = [run[step] for run in hot]
        table[step] = {
            "cold_median_ms": round(statistics.median(cold_values) * 1000, 3),
            "hot_median_ms": round(statistics.median(hot_values) * 1000, 3),
            "cold_mean_ms": round(statistics.mean(cold_values) * 1000, 3),
            "hot_mean_ms": round(statistics.mean(hot_values) * 1000, 3),
            "cold_samples_ms": [round(v * 1000, 3) for v in cold_values],
            "hot_samples_ms": [round(v * 1000, 3) for v in hot_values],
        }
    return {"repeats": repeats, "unit": "milliseconds",
            "cold_definition": "每个样本在全新 Runtime 上首次执行该步（含冷启动）",
            "hot_definition": "同一 Runtime 内预热一条链后重复执行，取后续样本",
            "steps": table}


def full_suite_wall_clock(xml_path):
    from defusedxml.ElementTree import parse
    node = parse(str(xml_path)).getroot().find("testsuite")
    return {"source": str(xml_path), "tests": int(node.get("tests")),
            "failures": int(node.get("failures")),
            "suite_time_seconds": float(node.get("time")),
            "wall_clock_definition": "JUnit testsuite time 属性（pytest 全量墙钟，非各用例耗时之和）"}


def discovery_size():
    from definitio.agent import compact_tools, tool_definitions
    from definitio.model import canonical
    full = tool_definitions()
    compact = compact_tools(full)
    full_bytes = len(canonical(full).encode("utf-8"))
    compact_bytes = len(canonical(compact).encode("utf-8"))
    schema_bytes = sum(len(canonical(tool["inputSchema"]).encode("utf-8")) for tool in full)
    return {
        "full_tool_count": len(full), "compact_tool_count": len(compact),
        "full_bytes": full_bytes, "compact_bytes": compact_bytes,
        "compact_over_full_ratio": round(compact_bytes / full_bytes, 4),
        "schema_bytes_total": schema_bytes,
        "schema_tokens_estimate": round(schema_bytes / 4, 1),
        "token_estimate_basis": "UTF-8 字节 / 4 的粗估；本机无 tokenizer，不是真实 BPE token 数，只用于同口径前后对比",
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline-xml", type=Path, default=ROOT / "evidence" / "baseline-v05-full-suite.xml")
    parser.add_argument("--out", type=Path, default=ROOT / "evidence" / "perf-baseline-v05.json")
    parser.add_argument("--repeats", type=int, default=3)
    args = parser.parse_args()
    import platform
    from definitio.agent import AgentService
    from definitio.runtime import Runtime
    with tempfile.TemporaryDirectory() as tmp:
        versions = Runtime(Path(tmp) / "v.sqlite3").versions
    report = {
        "format": "definitio-perf-baseline-v1",
        "captured_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "python": sys.version.split()[0], "platform": platform.platform(),
        "runtime_versions": versions,
        "groups": {
            "golden_chain_six_steps_cold_hot": cold_hot_table(args.repeats),
            "full_suite_wall_clock": full_suite_wall_clock(args.baseline_xml),
            "discovery_compact_full_size": discovery_size(),
            "tool_schema_token_estimate": None,
        },
        "constraints": {"model_api_calls": 0, "external_api_calls": 0,
                        "ports_bound": 0, "online_engine_touched": False},
    }
    report["groups"]["tool_schema_token_estimate"] = {
        "schema_bytes_total": report["groups"]["discovery_compact_full_size"]["schema_bytes_total"],
        "tokens_estimate": report["groups"]["discovery_compact_full_size"]["schema_tokens_estimate"],
        "basis": report["groups"]["discovery_compact_full_size"]["token_estimate_basis"]}
    text = json.dumps(report, ensure_ascii=False, indent=2) + "\n"
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(text, encoding="utf-8")
    summary = {"steps": {k: {"cold_ms": v["cold_median_ms"], "hot_ms": v["hot_median_ms"]}
                         for k, v in report["groups"]["golden_chain_six_steps_cold_hot"]["steps"].items()},
               "full_suite_seconds": report["groups"]["full_suite_wall_clock"]["suite_time_seconds"],
               "full_bytes": report["groups"]["discovery_compact_full_size"]["full_bytes"],
               "compact_bytes": report["groups"]["discovery_compact_full_size"]["compact_bytes"],
               "schema_tokens_estimate": report["groups"]["tool_schema_token_estimate"]["tokens_estimate"]}
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
