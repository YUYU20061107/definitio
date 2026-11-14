"""T6 consistency self-check: seed known violations, prove the checkers catch them.

A checker that passes everything is worthless. Every case below feeds a fixture
whose verdict is known in advance, then asserts the checker returns exactly that
verdict. A missed violation fails this script (exit 1), and so does a false
positive on the clean fixtures.
"""
from __future__ import annotations

import argparse
import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from definitio.agent import RECOVERY, AgentService                     # noqa: E402
from definitio.handbook import build, render_markdown                 # noqa: E402
from definitio.model import GISFault                                  # noqa: E402
from definitio.protocol import (DISPOSITION_VALUES, read_state,       # noqa: E402
                                resolve_handle, validate_disposition)
from definitio.runtime import Runtime                                 # noqa: E402
from definitio.semantics import validate_object_header                # noqa: E402

GEOJSON = {"type": "FeatureCollection", "features": [
    {"type": "Feature", "geometry": {"type": "Polygon",
                                     "coordinates": [[[0, 0], [1, 0], [1, 1], [0, 1], [0, 0]]]},
     "properties": {"g": "A"}}]}


def _raise(exc):
    def loader(_evidence_id):
        raise exc
    return loader


def collect(service):
    """Run every seeded case. Returns (cases, failures)."""
    cases = []

    def expect(name, ok_expected, fn):
        try:
            actual_ok, detail = fn()
        except Exception as exc:  # a checker that explodes has not caught anything
            actual_ok, detail = None, f"{type(exc).__name__}: {exc}"
        cases.append({"case": name, "expected_ok": ok_expected, "actual_ok": actual_ok,
                      "caught": actual_ok is ok_expected, "detail": detail})

    # ---- disposition: three seeded defects + one clean envelope ----
    expect("disposition_missing_is_caught", False,
           lambda: (validate_disposition({"id": "e1"})["ok"], "no disposition field"))
    expect("disposition_unknown_value_is_caught", False,
           lambda: (validate_disposition({"disposition": "verified"})["ok"], "value outside三值"))
    expect("stale_disguised_as_success_is_caught", False,
           lambda: (validate_disposition({"disposition": "not_delivered", "output_ref": "ds_1"})["ok"],
                    "not_delivered 却带 output_ref"))
    expect("clean_envelope_passes", True,
           lambda: (validate_disposition({"disposition": "delivered_verified", "output_ref": "ds_1"})["ok"],
                    "clean"))

    # ---- read tri-state: a failed read must be Unknown, never Absent ----
    expect("injected_read_failure_is_unknown", True,
           lambda: (read_state(_raise(RuntimeError("db gone")), "e1", "disposition")["state"] == "Unknown",
                    "read failure"))
    expect("read_failure_never_becomes_absent", True,
           lambda: (read_state(_raise(RuntimeError("db gone")), "e1", "disposition")["state"] != "Absent",
                    "must not degrade to Absent"))
    expect("absent_field_is_absent", True,
           lambda: (read_state(lambda _i: {"id": "e1"}, "e1", "disposition")["state"] == "Absent",
                    "field not present"))
    expect("known_field_is_known", True,
           lambda: (read_state(lambda _i: {"disposition": "not_delivered"}, "e1", "disposition")["state"] == "Known",
                    "field present"))

    # ---- handle resolution: 0 hits STALE, >=2 hits AMBIGUOUS ----
    def _code(fn):
        try:
            fn()
        except GISFault as fault:
            return fault.code
        return "NO_FAULT"

    expect("zero_hits_is_stale", True,
           lambda: (_code(lambda: resolve_handle([], role="input", stable_fields=("dataset_id",))) == "STALE",
                    "0 命中"))
    expect("two_identities_is_ambiguous", True,
           lambda: (_code(lambda: resolve_handle(
               [{"dataset_id": "a"}, {"dataset_id": "b"}], role="input",
               stable_fields=("dataset_id",))) == "AMBIGUOUS", ">=2 命中"))
    expect("one_identity_repeated_is_not_ambiguous", True,
           lambda: (resolve_handle([{"dataset_id": "a", "count": 1}, {"dataset_id": "a", "count": 9}],
                                   role="input", stable_fields=("dataset_id",))["dataset_id"] == "a",
                    "同一身份两次观测计 1"))
    expect("value_as_identity_is_refused", True,
           lambda: (_code(lambda: resolve_handle([{"value": 1}], role="input", stable_fields=("value",)))
                    == "INVALID_ARGUMENT", "值与范围不作身份"))
    for code in ("STALE", "AMBIGUOUS"):
        expect(f"recovery_registers_{code}", True,
               lambda code=code: (code in RECOVERY, "RECOVERY 已登记"))

    # ---- object header: each missing slot is caught ----
    clean = _clean_header(service)
    expect("clean_object_header_passes", True,
           lambda: (validate_object_header(clean)["ok"], "四槽齐全"))
    for slot in ("crs", "version", "fields", "extent"):
        expect(f"missing_{slot}_is_caught", False,
               lambda slot=slot: (_strip(clean, slot)["ok"], f"缺 {slot}"))

    # ---- handbook drift ----
    doc = ROOT / "docs" / "HANDBOOK.md"
    expect("handbook_matches_live_catalog", True,
           lambda: (doc.exists() and doc.read_text(encoding="utf-8") == render_markdown(build(service)),
                    "手册与活目录一致"))
    stale = build(service)
    stale["verbs"] = stale["verbs"][:-1]
    expect("handbook_missing_a_verb_is_caught", False,
           lambda: (render_markdown(stale) == doc.read_text(encoding="utf-8"), "少一个动词"))

    failures = [c for c in cases if not c["caught"]]
    return cases, failures


def _clean_header(service):
    imported = service.call("dataset_import", {
        "name": "selfcheck_src", "geojson": GEOJSON, "crs": "EPSG:32649",
        "confirm": True, "request_id": "selfcheck-import", "source_kind": "synthetic_fixture"})
    assert imported["ok"], imported
    dataset = service.store.dataset(imported["data"]["dataset"]["id"])
    return dataset["semantic"]


def _strip(header, slot):
    """Remove one normative slot from a copy, leaving the rest intact."""
    import copy
    broken = copy.deepcopy(header)
    if slot == "crs":
        broken.pop("crs", None)
    elif slot == "version":
        broken.get("identity", {}).pop("version", None)
    elif slot == "fields":
        broken.get("structure", {}).pop("fields", None)
    elif slot == "extent":
        for key in ("bounds", "range", "extent"):
            broken.get("structure", {}).pop(key, None)
    return validate_object_header(broken)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path)
    args = parser.parse_args()
    with tempfile.TemporaryDirectory() as tmp:
        service = AgentService(Runtime(Path(tmp) / "selfcheck.sqlite3"))
        cases, failures = collect(service)
    report = {"format": "definitio-consistency-selfcheck-v1",
              "scope": "checker_can_fail_does_not_mean_engine_certified",
              "cases": cases, "case_count": len(cases),
              "violations_detected": sum(1 for c in cases if c["expected_ok"] is False and c["caught"]),
              "clean_passed": sum(1 for c in cases if c["expected_ok"] is True and c["caught"]),
              "failures": failures, "ok": not failures,
              "certification": "NOT_CERTIFIED",
              "dispositions_known": list(DISPOSITION_VALUES)}
    text = json.dumps(report, ensure_ascii=False, indent=2) + "\n"
    print(text)
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(text, encoding="utf-8")
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
