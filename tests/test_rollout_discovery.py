"""R4: full 60-tool discovery audit and complete recovery semantics."""
from pathlib import Path

import pytest

import definitio
from definitio import discovery
from definitio.agent import AgentService, RECOVERY, tool_definitions
from definitio.guidance import RULES, plan_step_contract
from definitio.runtime import Runtime


@pytest.fixture
def service(tmp_path):
    return AgentService(Runtime(tmp_path / "workspace.sqlite3"))


def test_audit_covers_all_60_tools_with_zero_inconsistency(service):
    report = discovery.audit(service)
    assert len(report["tools"]) == 60
    inconsistent = [t for t in report["tools"] if not t["consistent"]]
    assert not inconsistent, inconsistent
    assert report["ok"]
    assert report["recovery_codes"]["missing"] == []
    assert report["error_rules"]["unpublished"] == []
    assert all(p["consistent"] for p in report["prompts"])
    names = {t["name"] for t in report["tools"]}
    assert names == {t["name"] for t in tool_definitions()}
    for entry in report["tools"]:
        assert set(entry["surfaces"]) >= {"list_full", "list_compact", "describe", "route"}


def test_raised_codes_scan_includes_worker_scripts():
    assert discovery.SCANNED_SCRIPTS == ("native_gis_worker.py", "qgis_processing_worker.py")
    codes = discovery.raised_codes(Path(definitio.__file__).parent)
    assert "BACKEND_ERROR" in codes
    missing = sorted(code for code in codes if code not in RECOVERY)
    assert not missing, missing


def test_b7_recovery_codes_have_explicit_semantics():
    for code in ("AUTHORIZATION_REQUIRED", "AUTHORIZATION_SCOPE", "AUTHORIZATION_EXHAUSTED",
                 "LICENSE_BLOCKED", "DEPENDENCY_BLOCKED", "PREFLIGHT_REQUIRED",
                 "BACKEND_UNAVAILABLE", "DESTRUCTIVE_BLOCKED", "DEFER_BLOCKED", "DIMENSION_MISMATCH"):
        action, retry, message = RECOVERY[code]
        assert isinstance(retry, bool)
        assert action and len(message) > 20, code


def test_published_surface_names_rule_is_on_the_discovery_surface(service):
    assert "published_surface_names" in RULES
    assert "published_surface_names" in plan_step_contract()["rules"]
    described = service.call("gis_describe_algorithm", {"algorithm": "gis_buffer"})
    assert described["ok"]
    assert "published_surface_names" in described["data"]["plan_step"]["rules"]


def test_error_echo_rules_are_all_published(service):
    bad = service.call("task_plan", {"goal": "g", "request_id": "r", "steps": [
        {"verb": "gis_copy_dataset", "parameters": {"source": "x", "target": "y", "request_id": "oops"}}]})
    assert not bad["ok"]
    assert bad["error"]["details"]["rule"] in RULES
    report = discovery.audit(service)
    assert report["error_rules"]["unpublished"] == []