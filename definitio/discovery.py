"""Cross-surface discovery consistency: one canonical name, one parameter set.

Audits list / describe / route / prompts / error echo for every published tool and
reports mismatches per tool instead of a single blanket pass flag.
"""
from __future__ import annotations

import re
from pathlib import Path

from .guidance import FAMILIES, PROMPTS, RULES, family_for

CONSISTENCY_FORMAT = "definitio-discovery-consistency-v1"
CALL = re.compile(r"\b([a-z][a-z_0-9]*)\(([^)]*)\)")
PARAM = re.compile(r"\b([a-z][a-z_0-9]*)\s*=")
CONTROL_FIELDS = {"request_id", "authorization_id"}
RAISED = (re.compile(r'GISFault\(\s*"([A-Z][A-Z_]{3,})"'),
          re.compile(r'require\((?:[^(),]|\([^()]*\))+,\s*"([A-Z][A-Z_]{3,})"'))


def prompt_tool_calls(template, known):
    calls = []
    for name, args in CALL.findall(template):
        if name in known:
            calls.append({"verb": name, "parameters": sorted(set(PARAM.findall(args)))})
    return calls


def raised_codes(package_dir):
    """Every error code the package can raise, found statically plus the verifier's policy maps."""
    from .verifier import PRECHECK_CODES, PROBLEM_KIND_CODES
    codes = set(PRECHECK_CODES.values()) | set(PROBLEM_KIND_CODES.values())
    for path in sorted(Path(package_dir).glob("*.py")):
        text = path.read_text(encoding="utf-8")
        for pattern in RAISED:
            codes.update(pattern.findall(text))
    return codes


def _properties(schema):
    return set((schema or {}).get("properties", {}))


def audit(service):
    from .agent import RECOVERY, compact_tools, tool_definitions
    definitions = {tool["name"]: tool for tool in tool_definitions()}
    compact = {tool["name"]: tool for tool in compact_tools(list(definitions.values()))}
    capabilities = {verb["name"]: verb for verb in service.runtime.capabilities()["verbs"]}
    routed = {tool for _, tools, _ in FAMILIES.values() for tool in tools}
    entries = []
    for name, definition in definitions.items():
        canonical_params = _properties(definition["inputSchema"])
        surfaces = {"list_full": sorted(canonical_params)}
        mismatches = []
        if name in compact:
            surfaces["list_compact"] = sorted(_properties(compact[name]["inputSchema"]))
            if surfaces["list_compact"] != surfaces["list_full"]:
                mismatches.append({"surface": "list_compact", "reason": "compact schema differs from full schema"})
        else:
            surfaces["list_compact"] = None
        if name in capabilities:
            described = service.call("gis_describe_algorithm", {"algorithm": name})
            if not described["ok"]:
                mismatches.append({"surface": "describe", "reason": described["error"]["code"]})
                surfaces["describe"] = None
            else:
                capability = described["data"]["capability"]
                tool = described["data"]["tool"]
                surfaces["describe"] = sorted(_properties(capability.get("input_schema")))
                if set(surfaces["describe"]) != canonical_params - CONTROL_FIELDS:
                    mismatches.append({"surface": "describe", "reason": "capability parameter set differs from list"})
                if tool is None or sorted(_properties(tool["inputSchema"])) != surfaces["list_full"]:
                    mismatches.append({"surface": "describe", "reason": "describe.tool schema differs from list"})
                if capability["name"] != name:
                    mismatches.append({"surface": "describe", "reason": "capability name is not canonical"})
            surfaces["route"] = [family_for(name)] if family_for(name) else []
            if name not in routed:
                mismatches.append({"surface": "route", "reason": "verb absent from every family"})
            if len([key for key, (_, tools, _) in FAMILIES.items() if name in tools]) > 1:
                mismatches.append({"surface": "route", "reason": "verb listed in more than one family"})
        else:
            surfaces["describe"] = None
            surfaces["route"] = None
        entries.append({"name": name, "surfaces": surfaces, "consistent": not mismatches, "mismatches": mismatches})

    known = set(definitions)
    prompt_findings = []
    for prompt_name, (_, arguments, template) in PROMPTS.items():
        for call in prompt_tool_calls(template, known):
            published = _properties(definitions[call["verb"]]["inputSchema"])
            unknown = sorted(set(call["parameters"]) - published)
            prompt_findings.append({"prompt": prompt_name, "verb": call["verb"], "parameters": call["parameters"],
                                    "unknown_parameters": unknown, "consistent": not unknown})
    from .guidance import INSTRUCTIONS
    for call in prompt_tool_calls(INSTRUCTIONS, known):
        published = _properties(definitions[call["verb"]]["inputSchema"])
        unknown = sorted(set(call["parameters"]) - published)
        prompt_findings.append({"prompt": "gis_handbook", "verb": call["verb"], "parameters": call["parameters"],
                                "unknown_parameters": unknown, "consistent": not unknown})

    codes = raised_codes(Path(__file__).parent)
    missing = sorted(code for code in codes if code not in RECOVERY)
    rules = {"published": sorted(RULES),
             "unpublished": sorted({rule for rule in _echoed_rules(Path(__file__).parent) if rule not in RULES})}
    ok = (all(entry["consistent"] for entry in entries) and all(item["consistent"] for item in prompt_findings)
          and not missing and not rules["unpublished"])
    return {"format": CONSISTENCY_FORMAT, "tools": entries, "prompts": prompt_findings,
            "error_rules": rules, "recovery_codes": {"raised": sorted(codes), "missing": missing,
                                                     "covered": sorted(codes & set(RECOVERY))},
            "ok": ok}


def _echoed_rules(package_dir):
    pattern = re.compile(r'rule="([a-z_]+)"')
    found = set()
    for path in sorted(Path(package_dir).glob("*.py")):
        found.update(pattern.findall(path.read_text(encoding="utf-8")))
    return found