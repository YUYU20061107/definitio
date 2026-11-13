"""D5 one canonical name and one parameter set across every discovery surface."""
import pytest

from definitio import discovery
from definitio.agent import AgentService, compact_tools, tool_definitions
from definitio.guidance import PROMPTS, RULES, plan_step_contract
from definitio.runtime import Runtime
from definitio.surface import Surface


@pytest.fixture
def service(tmp_path):
    return AgentService(Runtime(tmp_path / "workspace.sqlite3"))


CHAIN_TOOLS = ["dataset_import", "gis_import_data", "gis_reproject", "gis_buffer", "gis_spatial_join",
               "gis_field_statistics", "gis_dissolve", "task_plan", "task_dry_run", "task_execute",
               "task_get", "artifact_view", "workflow_route", "gis_describe_algorithm"]


def test_every_tool_has_one_name_and_one_parameter_set_across_surfaces(service):
    report = discovery.audit(service)
    assert report["format"] == "definitio-discovery-consistency-v1"
    assert report["ok"], {"tools": [t for t in report["tools"] if not t["consistent"]],
                          "prompts": [p for p in report["prompts"] if not p["consistent"]],
                          "recovery": report["recovery_codes"]["missing"],
                          "rules": report["error_rules"]["unpublished"]}
    assert len(report["tools"]) == 60
    for name in CHAIN_TOOLS:
        entry = next(t for t in report["tools"] if t["name"] == name)
        assert entry["consistent"], entry["mismatches"]
        assert set(entry["surfaces"]) >= {"list_full", "list_compact", "describe", "route"}


def test_compact_and_full_and_surface_v1_agree_on_canonical_names(service):
    full = {t["name"] for t in tool_definitions()}
    compact = {t["name"] for t in compact_tools(tool_definitions())}
    assert len(full) == 60 and len(compact) == 21
    assert compact <= full
    canonical = Surface(tool_definitions(), "canonical")
    assert set(canonical.names) == full
    assert all(canonical.names[name] == name for name in full)
    assert canonical.spec["parameters"] == {}
    renamed = Surface(tool_definitions(), "surface-v1")
    assert set(renamed.names) == full
    for definition in tool_definitions():
        name = definition["name"]
        assert set(renamed.fields(name)) == set(definition["inputSchema"]["properties"])
        assert renamed.reverse[renamed.names[name]] == name


def test_prompts_only_use_canonical_verbs_and_published_parameters(service):
    known = {t["name"] for t in tool_definitions()}
    for name, (_, arguments, template) in PROMPTS.items():
        for call in discovery.prompt_tool_calls(template, known):
            assert call["verb"] in service.tools, (name, call["verb"])
            schema = service.tools[call["verb"]]["inputSchema"]["properties"]
            assert set(call["parameters"]) <= set(schema), (name, call)
    assert len(PROMPTS) == 4


def test_error_rules_are_published_and_echoed_consistently(service):
    assert set(RULES) >= {"operation_parameters_only", "previous_dataset_output", "published_surface_names",
                          "version_binding_changed", "resume_dependency_invalidated"}
    assert plan_step_contract()["rules"] == sorted(RULES)
    bad = service.call("task_plan", {"goal": "g", "request_id": "r", "steps": [
        {"verb": "gis_copy_dataset", "parameters": {"source": "x", "target": "y", "request_id": "oops"}}]})
    assert bad["error"]["details"]["rule"] in RULES
    described = service.call("gis_describe_algorithm", {"algorithm": "gis_buffer"})
    assert described["data"]["plan_step"]["rules"] == sorted(RULES)


def test_every_raised_error_code_has_an_explicit_recovery_entry(service):
    from definitio.agent import RECOVERY
    from pathlib import Path
    import definitio
    codes = discovery.raised_codes(Path(definitio.__file__).parent)
    missing = sorted(code for code in codes if code not in RECOVERY)
    assert not missing, missing
    for code in ("AUTHORIZATION_REQUIRED", "AUTHORIZATION_SCOPE", "AUTHORIZATION_EXHAUSTED", "LICENSE_BLOCKED",
                 "DEPENDENCY_BLOCKED", "PREFLIGHT_REQUIRED", "BACKEND_UNAVAILABLE", "DESTRUCTIVE_BLOCKED",
                 "DEFER_BLOCKED", "DIMENSION_MISMATCH"):
        assert code in RECOVERY, code
        assert RECOVERY[code][0] and RECOVERY[code][2]


def test_route_names_are_canonical_and_chain_verbs_are_routable(service):
    routed = service.call("workflow_route", {"intent": "buffer 缓冲 reproject 投影 spatial join 空间连接 statistics 统计 import 导入"})
    assert routed["ok"]
    names = {tool for family in routed["data"]["families"] for tool in family["tools"]}
    assert names <= set(service.tools)
    for verb in ("gis_buffer", "gis_spatial_join", "gis_field_statistics", "gis_reproject", "gis_import_data"):
        assert verb in names, verb