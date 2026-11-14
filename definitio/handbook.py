"""Handbook that follows the live catalog instead of being hand-written again.

Single source of truth:
  verbs      -> Runtime.capabilities()["verbs"] (catalog.json x SPECS)
  tools      -> AgentService.tools (tool_definitions())
  error codes-> agent.RECOVERY
  rules      -> guidance.RULES
  disposition-> protocol.DISPOSITION_VALUES / DISPOSITION_MEANING
  tri-state  -> protocol.READ_STATES

`build()` renders only from those objects. If the catalog moves and the handbook
is not regenerated, `docs/HANDBOOK.md` no longer equals `render_markdown(build())`
and tests/test_v05_handbook.py fails. There is deliberately no second copy of any
verb, code or rule text in this module.
"""
from __future__ import annotations

import json

from .guidance import FAMILIES, INSTRUCTIONS, RULES
from .model import digest
from .operations import SPECS
from .protocol import (CAPTURE_PROOF_COUNTING_RULE, DISPOSITION_MEANING,
                       DISPOSITION_VALUES, HANDLE_RESOLUTION_CODES, READ_STATES)

HANDBOOK_FORMAT = "definitio-handbook-v1"


def build(service):
    """Assemble the handbook from live objects. No literal verb/code/rule text."""
    from .agent import RECOVERY

    capabilities = service.runtime.capabilities()
    verbs = []
    family_of = {name: key for key, (_, names, _) in FAMILIES.items() for name in names}
    for item in capabilities["verbs"]:
        schema = item.get("input_schema") or {}
        verbs.append({
            "name": item["name"],
            "vid": item.get("vid"),
            "level": item.get("g1_level"),
            "family": item.get("family") or family_of.get(item["name"]),
            "implemented": bool(item.get("implemented")),
            "backend": (item.get("backend") or {}).get("id") if isinstance(item.get("backend"), dict) else item.get("backend"),
            "required_parameters": list(schema.get("required") or []),
            "parameters": sorted((schema.get("properties") or {})),
        })
    verbs.sort(key=lambda v: (v["vid"] or "", v["name"]))

    return {
        "format": HANDBOOK_FORMAT,
        "counts": {"verbs": len(verbs), "tools": len(service.tools), "specs": len(SPECS),
                   "error_codes": len(RECOVERY), "rules": len(RULES),
                   "dispositions": len(DISPOSITION_VALUES)},
        "instructions": INSTRUCTIONS,
        "verbs": verbs,
        "error_codes": [{"code": code, "action": value[0], "retry": bool(value[1]), "message": value[2]}
                        for code, value in sorted(RECOVERY.items())],
        "rules": [{"rule": name, **detail} for name, detail in sorted(RULES.items())],
        "dispositions": [{"value": value, "meaning": DISPOSITION_MEANING[value]}
                         for value in DISPOSITION_VALUES],
        "read_states": list(READ_STATES),
        "handle_resolution": {"codes": list(HANDLE_RESOLUTION_CODES),
                              "counting_rule": CAPTURE_PROOF_COUNTING_RULE},
        "source_digest": source_digest(service),
    }


def source_digest(service):
    """Digest of everything the handbook claims to mirror, for drift detection."""
    from .agent import RECOVERY
    payload = {
        "verbs": [[v["name"], v.get("vid"), v.get("g1_level"),
                   sorted((v.get("input_schema") or {}).get("properties") or {})]
                  for v in service.runtime.capabilities()["verbs"]],
        "tools": sorted(service.tools),
        "error_codes": sorted(RECOVERY),
        "rules": sorted(RULES),
        "dispositions": list(DISPOSITION_VALUES),
        "read_states": list(READ_STATES),
        "specs": sorted(SPECS),
    }
    return digest(payload)


def render_markdown(handbook):
    """Deterministic markdown. Same handbook dict always renders identical bytes."""
    lines = ["# Definitio 使用手册（自动生成，勿手改）", "",
             f"格式：`{handbook['format']}`　来源摘要：`{handbook['source_digest']}`", "",
             "> 本文件由 `scripts/generate_handbook.py` 从活目录生成。"
             "动词、错误码、规则、disposition 口径变动后必须重新生成，"
             "否则 `tests/test_v05_handbook.py` 的漂移测试会失败。", "",
             "## 1. 操作顺序", "", "```text", handbook["instructions"].strip(), "```", "",
             "## 2. 计数", ""]
    for key, value in handbook["counts"].items():
        lines.append(f"- {key}: **{value}**")
    lines += ["", "## 3. 动词（40）", "",
              "| 动词 | VID | 层级 | 家族 | 必需参数 | 已实现 |", "|---|---|---|---|---|---|"]
    for verb in handbook["verbs"]:
        lines.append("| `{name}` | {vid} | {level} | {family} | {req} | {impl} |".format(
            name=verb["name"], vid=verb["vid"], level=verb["level"], family=verb["family"] or "-",
            req=", ".join(f"`{p}`" for p in verb["required_parameters"]) or "-",
            impl="是" if verb["implemented"] else "否"))
    lines += ["", "## 4. 错误码（复苏动作）", "", "| 错误码 | 建议动作 | 可重试 | 说明 |", "|---|---|---|---|"]
    for code in handbook["error_codes"]:
        lines.append("| `{code}` | `{action}` | {retry} | {message} |".format(
            code=code["code"], action=code["action"], retry="是" if code["retry"] else "否",
            message=code["message"].replace("|", "\\|")))
    lines += ["", "## 5. 交付三态（disposition）", ""]
    for item in handbook["dispositions"]:
        lines.append(f"- `{item['value']}`：{item['meaning']}")
    lines += ["", "## 6. 单次读取三态", "",
              "`" + "` / `".join(handbook["read_states"]) + "`"
              "：读失败或文档畸形一律 `Unknown`，不得降级为 `Absent`。", "",
              "## 7. 句柄解析", "",
              f"错误码：`{'` / `'.join(handbook['handle_resolution']['codes'])}`　"
              "（0 命中 = `STALE`；>=2 命中 = `AMBIGUOUS`）", "",
              handbook["handle_resolution"]["counting_rule"], "",
              "## 8. 规则", "", "| 规则 | 位置 | 含义 | 修复 |", "|---|---|---|---|"]
    for rule in handbook["rules"]:
        lines.append("| `{rule}` | {where} | {meaning} | {fix} |".format(
            rule=rule["rule"], where=rule["where"], meaning=rule["meaning"], fix=rule["fix"]))
    return "\n".join(lines) + "\n"


def render_json(handbook):
    return json.dumps(handbook, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
