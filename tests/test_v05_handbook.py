"""T5 handbook drift tests: the document must follow the live catalog.

If any verb, error code, rule or disposition changes without regenerating
docs/HANDBOOK.md, these tests fail. Nothing here re-states the catalog by hand.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

from definitio.agent import RECOVERY, AgentService
from definitio.guidance import RULES
from definitio.handbook import build, render_json, render_markdown, source_digest
from definitio.operations import SPECS
from definitio.protocol import DISPOSITION_VALUES, READ_STATES
from definitio.runtime import Runtime

ROOT = Path(__file__).resolve().parents[1]
HANDBOOK = ROOT / "docs" / "HANDBOOK.md"


@pytest.fixture
def service(tmp_path):
    return AgentService(Runtime(tmp_path / "handbook.sqlite3"))


def test_checked_in_handbook_matches_the_live_catalog(service):
    """The only assertion that makes the manual '随行': byte equality with a fresh render."""
    assert HANDBOOK.exists(), "docs/HANDBOOK.md 缺失；用 scripts/generate_handbook.py 生成"
    assert HANDBOOK.read_text(encoding="utf-8") == render_markdown(build(service))


def test_handbook_mirrors_every_live_registry(service):
    handbook = build(service)
    assert {v["name"] for v in handbook["verbs"]} == set(SPECS)
    assert len(handbook["verbs"]) == len(SPECS) == 40
    assert handbook["counts"]["tools"] == 60
    assert {c["code"] for c in handbook["error_codes"]} == set(RECOVERY)
    assert {r["rule"] for r in handbook["rules"]} == set(RULES)
    assert [d["value"] for d in handbook["dispositions"]] == list(DISPOSITION_VALUES)
    assert handbook["read_states"] == list(READ_STATES)
    assert {"STALE", "AMBIGUOUS"} <= set(RECOVERY)


def test_handbook_carries_the_v05_protocol_sections(service):
    text = render_markdown(build(service))
    for token in ("delivered_verified", "delivered_unverified", "not_delivered",
                  "STALE", "AMBIGUOUS", "Unknown", "捕获证明按"):
        assert token in text, f"手册缺少协议条款：{token}"


def test_drift_is_actually_detected(service):
    """A catalog change must move both the digest and the rendered bytes."""
    before_digest = source_digest(service)
    before_text = render_markdown(build(service))
    service.tools["gis_not_a_real_verb"] = {"name": "gis_not_a_real_verb", "inputSchema": {}}
    assert source_digest(service) != before_digest
    assert render_markdown(build(service)) != before_text
    assert "gis_not_a_real_verb" not in before_text


def test_cli_check_reads_out_from_the_live_catalog():
    result = subprocess.run([sys.executable, "-X", "utf8", "scripts/generate_handbook.py", "--check"],
                            cwd=ROOT, capture_output=True, text=True, encoding="utf-8")
    assert result.returncode == 0, result.stdout + result.stderr
    assert "OK" in result.stdout


def test_json_render_is_stable_and_complete(service):
    first, second = render_json(build(service)), render_json(build(service))
    assert first == second
    assert '"source_digest"' in first and '"handle_resolution"' in first
