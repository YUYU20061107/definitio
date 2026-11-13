"""Task-list regressions: actionable plans and dependency preflight."""
import copy
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import threading
from urllib.parse import parse_qs, urlparse

import pytest

from definitio.agent import AgentService
from definitio.environment import inspect_environment
from definitio.runtime import Runtime
from test_agent import imported


@pytest.fixture
def service(tmp_path):
    return AgentService(Runtime(tmp_path / "workspace.sqlite3"))


@pytest.mark.parametrize("tool", ["task_plan", "task_dry_run"])
@pytest.mark.parametrize("field", ["request_id", "authorization_id"])
def test_step_control_fields_explain_the_rule_without_writes(service, tool, field):
    source = imported(service)
    args = {"goal": "bad control", "steps": [{"verb": "gis_copy_dataset", "parameters": {
        "source": source, "target": "job_copy", field: "misplaced"}}]}
    if tool == "task_plan":
        args["request_id"] = "plan-bad-control"
    r = service.call(tool, args)
    assert not r["ok"] and r["error"]["code"] == "INVALID_ARGUMENT", r
    details = r["error"]["details"]
    assert details["rule"] == "operation_parameters_only" and details["step"] == 1
    assert details["fields"] == [field] and "task_execute" in details["hint"]
    with service.store.connect() as db:
        assert db.execute("SELECT COUNT(*) FROM tasks").fetchone()[0] == 0
    assert len(service.store.datasets()) == 1


@pytest.mark.parametrize("tool", ["task_plan", "task_dry_run"])
def test_uncommitted_target_reference_explains_previous(service, tool):
    source = imported(service)
    args = {"goal": "bad reference", "steps": [
        {"verb": "gis_copy_dataset", "parameters": {"source": source, "target": "job_copy"}},
        {"verb": "gis_validate_geometry", "parameters": {"dataset": "job_copy", "repair": False}}]}
    if tool == "task_plan":
        args["request_id"] = "plan-bad-reference"
    r = service.call(tool, args)
    assert not r["ok"] and r["error"]["code"] == "INVALID_ARGUMENT", r
    details = r["error"]["details"]
    assert details["rule"] == "previous_dataset_output" and details["step"] == 2
    assert details["reference"] == "job_copy" and "$previous" in details["hint"]
    assert len(service.store.datasets()) == 1


def test_preview_sources_and_plan_match_execution(service):
    source = imported(service)
    steps = [
        {"verb": "gis_copy_dataset", "parameters": {"source": source, "target": "job_copy"}},
        {"verb": "gis_dissolve", "parameters": {"input": "$previous", "target": "job_groups", "fields": ["group_code"]}},
        {"verb": "gis_validate_geometry", "parameters": {"dataset": "$previous", "repair": False}}]
    original = copy.deepcopy(steps)
    r = service.call("task_dry_run", {"goal": "sources", "steps": steps})
    assert r["ok"], r
    preview = r["data"]["preview"]
    assert preview["workspace_writes"] == 0 and steps == original
    sources = [s["input_sources"][0] for s in preview["steps"]]
    assert [s["source"] for s in sources] == ["workspace_snapshot", "previous_step", "previous_step"]
    assert sources[0]["resolved_id"] == source and sources[1]["resolved_id"] == "preview_1"
    assert sources[2]["step"] == 2
    plan = service.call("task_plan", {"goal": "sources", "steps": steps, "request_id": "p"})["data"]["task"]
    done = service.call("task_execute", {"task_id": plan["id"], "plan_digest": plan["plan_digest"], "confirm": True})
    assert done["ok"], done
    assert [x["feature_count"] for x in done["data"]["outputs"]] == [s["output"]["feature_count"] for s in preview["steps"][:2]]


def test_algorithm_discloses_plan_rules_and_dependency_tier(service):
    r = service.call("gis_describe_algorithm", {"algorithm": "gis_copy_dataset"})
    assert r["ok"] and r["data"]["plan_step"]["reference_rule"] == "previous_dataset_output"
    assert r["data"]["capability"]["dependency_tier"] == "core"
    r = service.call("gis_describe_algorithm", {"algorithm": "gis_run_gp"})
    assert r["data"]["capability"]["dependencies"] == ["core", "arcpy"]


def test_full_doctor_reports_deliberately_missing_backends(tmp_path, monkeypatch):
    for key in ("QGIS_PYTHON", "ARCPY_PYTHON", "PDAL", "PDAL_WRENCH"):
        monkeypatch.setenv("DEFINITIO_" + key, str(tmp_path / "missing.exe"))
    monkeypatch.setenv("DEFINITIO_BACKEND_CONFIG", str(tmp_path / "absent.json"))
    report = inspect_environment(tmp_path, "full")
    assert report["status"] == "FAIL"
    assert all(report["checks"][name]["status"] == "MISSING" for name in ("qgis", "arcpy", "pdal", "pdal_wrench"))
    assert report["checks"]["postgis"]["status"] == "NOT_CONFIGURED"
    assert not list(tmp_path.iterdir())


def test_core_doctor_does_not_launch_backends(tmp_path, monkeypatch):
    def no_launch(*args):
        raise AssertionError("Core doctor must not launch native processes")
    monkeypatch.setattr("definitio.environment.command_check", no_launch)
    report = inspect_environment(tmp_path, "core")
    assert report["status"] == "PASS"
    assert len(report["verbs"]) == 40 and not list(tmp_path.iterdir())


def test_nonempty_synthetic_sta_over_real_http(service, tmp_path, monkeypatch):
    received = []
    records = [{"@iot.id": 7, "result": 12.5, "phenomenonTime": "2026-11-13T00:00:00Z"}]

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            received.append(urlparse(self.path))
            body = json.dumps({"value": records, "@iot.count": 2, "@iot.nextLink": "Observations?$skip=1"}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    config = tmp_path / "sta.json"
    config.write_text(json.dumps({"sensorthings": {"url": f"http://127.0.0.1:{server.server_port}/v1.1/"}}))
    monkeypatch.setenv("DEFINITIO_BACKEND_CONFIG", str(config))
    thread.start()
    try:
        r = service.call("gis_query_observations", {"backend": "istsos", "entity": "Observations",
            "datastream_id": 3, "top": 1, "count": True, "filter": "result gt 10"})
        assert r["ok"], r
        result = r["data"]["results"][0]["value"]
        assert result["data"] == records and result["returned_count"] == 1 and result["count"] == 2
        assert result["next_link"] == "Observations?$skip=1" and result["empty_reason"] is None
        assert not result["local_filtering"] and not result["local_page_concatenation"]
        assert received[0].path == "/v1.1/Datastreams(3)/Observations"
        assert parse_qs(received[0].query)["$filter"] == ["result gt 10"]
        assert len(received) == 1
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)
