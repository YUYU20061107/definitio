"""Rollout predicate inventory: 40 verbs, no blanks, registered checks only."""
from pathlib import Path

import pytest
from shapely.geometry import box, mapping

import definitio.verifier as verifier
from definitio.operations import SPECS
from definitio.runtime import Runtime


@pytest.fixture
def runtime(tmp_path):
    return Runtime(tmp_path / "workspace.sqlite3")


def test_registry_covers_exactly_the_40_frozen_verbs():
    assert set(verifier.CHECK_REGISTRY) == set(SPECS)
    assert len(verifier.CHECK_REGISTRY) == 40


def test_every_registered_check_id_exists_in_the_verifier_source():
    source = Path(verifier.__file__).read_text(encoding="utf-8")
    for verb, entry in verifier.CHECK_REGISTRY.items():
        for check in entry["checks"]:
            assert f'"{check["id"]}"' in source, (verb, check["id"])
            assert check["basis"] and check["method"] and check["layer"]


def test_basic_only_verbs_state_a_reason():
    for verb in ("gis_list_algorithms", "gis_describe_algorithm"):
        entry = verifier.CHECK_REGISTRY[verb]
        assert entry["checks"] == []
        assert len(entry["basic_only_reason"]) > 10
    for verb, entry in verifier.CHECK_REGISTRY.items():
        if not entry["checks"]:
            assert entry.get("basic_only_reason"), verb


def test_predicate_count_stays_frozen_at_six():
    assert len(verifier.PREDICATES) == 6
    assert verifier.POLICY["commit"] == verifier.PREDICATES
    assert verifier.POLICY["verify"] == verifier.PREDICATES
    assert verifier.POLICY["preview"] == verifier.PREDICATES


def emitted_ids(runtime, verb, parameters, confirm=True):
    task = runtime.declare(verb, [{"verb": verb, "parameters": parameters}])
    task = runtime.execute(task["id"], task["plan_digest"], confirm)
    assert task["task_state"] == "COMMITTED", task.get("error")
    ev = runtime.store.evidence(task["steps"][0]["evidence_ref"])
    found = next(f for f in ev["verification"]["found"] if f["predicate"] == "operation_readback_invariants")
    return {c["id"] for c in found["details"]["checks"]}


def test_emitted_checks_are_all_registered(runtime):
    d = runtime.import_dataset("src", {"type": "FeatureCollection", "features": [
        {"type": "Feature", "geometry": mapping(box(500000, 2540000, 500100, 2540100)),
         "properties": {"g": "A"}}]}, "EPSG:32649")
    shared = set(verifier.SHARED_CHECKS["vector_proposal"])
    cases = [
        ("gis_buffer", {"input": d["id"], "target": "b", "distance": 10, "unit": "m", "dissolve": False}),
        ("gis_dissolve", {"input": d["id"], "target": "d", "fields": ["g"]}),
        ("gis_copy_dataset", {"source": d["id"], "target": "c"}),
        ("gis_rename_dataset", {"dataset": d["id"], "new_name": "renamed"}),
    ]
    for verb, parameters in cases:
        ids = emitted_ids(runtime, verb, parameters)
        registered = {c["id"] for c in verifier.CHECK_REGISTRY[verb]["checks"]} | shared
        assert ids <= registered, (verb, ids - registered)
        assert ids, verb


def test_inventory_table_renders_for_all_verbs():
    lines = []
    for verb in sorted(verifier.CHECK_REGISTRY):
        entry = verifier.CHECK_REGISTRY[verb]
        if entry["checks"]:
            lines.append(f"{verb}: " + ", ".join(c["id"] for c in entry["checks"]))
        else:
            lines.append(f"{verb}: 仅基础检查＋{entry['basic_only_reason']}")
    assert len(lines) == 40
    assert all(line.strip() for line in lines)