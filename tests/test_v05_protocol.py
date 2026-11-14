"""T1 protocol hardening: disposition tri-value, handle codes, read tri-state.

Every case below is machine-verified against real runtime evidence or a real
store round-trip; no simulated verdicts are reported as execution results.
"""
import pytest

from definitio import discovery
from definitio.agent import AgentService, RECOVERY
from definitio.model import GISFault, digest
from definitio.protocol import (
    DISPOSITION_VALUES, READ_STATES, classify_disposition, disposition_basis,
    non_delivery_envelope, read_state, resolve_handle, validate_disposition,
)
from definitio.runtime import Runtime


@pytest.fixture
def service(tmp_path):
    return AgentService(Runtime(tmp_path / "protocol.sqlite3"))


def fixture_geojson():
    rows = [(0, 0, 1, 1, "A"), (1, 0, 2, 1, "A"), (0, 0, 1, 1, "B")]
    return {"type": "FeatureCollection", "features": [
        {"type": "Feature",
         "geometry": {"type": "Polygon", "coordinates": [[[x, y], [a, y], [a, b], [x, b], [x, y]]]},
         "properties": {"group_code": group}} for x, y, a, b, group in rows]}


def imported(service):
    reply = service.call("dataset_import", {
        "name": "source", "geojson": fixture_geojson(), "crs": "EPSG:32649",
        "confirm": True, "request_id": "protocol-import", "source_kind": "synthetic_fixture"})
    assert reply["ok"], reply
    return reply["data"]["dataset"]["id"]


def evidence_of(service, reply):
    task = reply["data"].get("task") or service.store.tasks()[0]
    return service.store.evidence(task["evidence_refs"][0])


# --- A. disposition: three values, one real case each -------------------------

def test_disposition_delivered_verified_on_a_real_write(service):
    source = imported(service)
    reply = service.call("gis_dissolve", {
        "input": source, "target": "dissolved", "fields": ["group_code"],
        "confirm": True, "request_id": "protocol-dissolve"})
    assert reply["ok"], reply
    evidence = evidence_of(service, reply)
    assert evidence["disposition"] == "delivered_verified"
    assert validate_disposition(evidence)["ok"]
    assert evidence["output_ref"]
    basis = evidence["disposition_basis"]
    assert basis["has_persisted_output"] and basis["verification_ok"]
    assert basis["checks_total"] == basis["checks_passed"] > 0
    assert basis["unconfirmed_segments"] > 0, "同核自洽限制必须如实保留，不因 verified 而抹掉"
    assert "oracle" in basis["oracle_note"]
    assert evidence["certification"] == "NOT_CERTIFIED"


def test_disposition_delivered_unverified_on_a_readonly_result(service):
    reply = service.call("gis_measure", {
        "operation": "area", "a_wkt": "POLYGON ((0 0,2 0,2 3,0 3,0 0))", "crs": "EPSG:32649"})
    assert reply["ok"], reply
    evidence = evidence_of(service, reply)
    assert evidence["disposition"] in DISPOSITION_VALUES
    assert evidence["disposition"] == "delivered_unverified", evidence.get("verification")
    assert validate_disposition(evidence)["ok"]


def test_disposition_not_delivered_is_persisted_and_read_back(service):
    envelope = non_delivery_envelope(
        evidence_id="ev_protocol_not_delivered", task_id="task_protocol", step_id="step_1",
        verb="gis_buffer", reason="declared output was never committed")
    with service.store.connect() as db:
        service.store.save_evidence(envelope, db)
    read_back = service.store.evidence("ev_protocol_not_delivered")
    assert read_back["disposition"] == "not_delivered"
    assert read_back["disposition_reason"]
    assert validate_disposition(read_back)["ok"]


def test_disposition_is_additive_and_digest_still_matches_envelope(service):
    source = imported(service)
    reply = service.call("gis_copy_dataset", {
        "source": source, "target": "copy_v05", "confirm": True, "request_id": "protocol-copy"})
    assert reply["ok"], reply
    evidence = evidence_of(service, reply)
    for existing in ("evidence_digest", "input_unchanged", "output_ref", "postcondition_checks",
                     "verification", "certification", "requested_parameters"):
        assert existing in evidence, existing
    stored = evidence["evidence_digest"]
    assert stored == digest({k: v for k, v in evidence.items() if k != "evidence_digest"})
    assert evidence["certification"] == "NOT_CERTIFIED"


def test_read_path_does_not_relabel_disposition(service):
    source = imported(service)
    reply = service.call("gis_dissolve", {
        "input": source, "target": "dissolved2", "fields": ["group_code"],
        "confirm": True, "request_id": "protocol-dissolve2"})
    assert reply["ok"], reply
    ref = reply["data"]["task"]["evidence_refs"][0]
    summary = service.call("evidence_get", {"evidence_id": ref})
    assert summary["ok"]
    assert summary["data"]["evidence"]["disposition"] == service.store.evidence(ref)["disposition"]
    states = summary["data"]["read_states"]
    assert "disposition" not in states, "read path must not re-label disposition"
    assert set(states) == {"output_ref", "postcondition_checks", "semantic"}
    assert all(item["state"] in READ_STATES and item["state"] != "Unknown" for item in states.values())


# --- B. handle resolution -----------------------------------------------------

def test_handle_resolution_stale_on_zero_hits():
    with pytest.raises(GISFault) as exc:
        resolve_handle([], role="input", stable_fields=["object_id", "version_id"])
    assert exc.value.code == "STALE"
    assert exc.value.details["identity_count"] == 0


def test_handle_resolution_ambiguous_on_two_identities():
    candidates = [
        {"object_id": "a", "version_id": "v1", "value": 1, "bounds": [0, 0, 1, 1]},
        {"object_id": "b", "version_id": "v1", "value": 999, "bounds": [9, 9, 9, 9]},
    ]
    with pytest.raises(GISFault) as exc:
        resolve_handle(candidates, role="input", stable_fields=["object_id", "version_id"])
    assert exc.value.code == "AMBIGUOUS"
    assert exc.value.details["identity_count"] == 2


def test_capture_proof_counting_ignores_values_and_ranges():
    same_object_observed_twice = [
        {"object_id": "a", "version_id": "v1", "value": 1, "bounds": [0, 0, 1, 1]},
        {"object_id": "a", "version_id": "v1", "value": 42, "bounds": [8, 8, 9, 9]},
    ]
    handle = resolve_handle(same_object_observed_twice, role="input",
                            stable_fields=["object_id", "version_id"])
    assert handle["object_id"] == "a"
    with pytest.raises(GISFault) as exc:
        resolve_handle([{"object_id": "a", "version_id": "v1"}], role="input",
                       stable_fields=["value", "bounds"])
    assert exc.value.code == "INVALID_ARGUMENT"


def test_protocol_codes_are_registered_on_the_discovery_surface(service):
    report = discovery.audit(service)
    assert report["ok"]
    assert report["recovery_codes"]["missing"] == []
    raised = set(report["recovery_codes"]["raised"])
    assert {"STALE", "AMBIGUOUS"} <= raised
    assert {"STALE", "AMBIGUOUS"} <= set(RECOVERY)
    for code in ("STALE", "AMBIGUOUS"):
        action, retry, message = RECOVERY[code]
        assert action and isinstance(retry, bool) and len(message) > 20


# --- C. read tri-state --------------------------------------------------------

def test_read_tristate_known_absent_and_unknown():
    document = {"id": "ev_1", "output_ref": "ds_1"}
    assert read_state(lambda _id: document, "ev_1", "output_ref") == {
        "state": "Known", "field": "output_ref", "value": "ds_1"}
    assert read_state(lambda _id: document, "ev_1", "semantic")["state"] == "Absent"

    def broken(_id):
        raise OSError("injected evidence read failure")

    failed = read_state(broken, "ev_1", "output_ref")
    assert failed["state"] == "Unknown"
    assert failed["reason"] == "read_failed"
    assert failed["error_type"] == "OSError"
    assert "value" not in failed

    malformed = read_state(lambda _id: None, "ev_1", "output_ref")
    assert malformed["state"] == "Unknown" and malformed["reason"] == "malformed_evidence"


def test_disposition_never_claims_an_independent_oracle():
    verification = {"ok": True, "unconfirmed": [{"predicate": "p", "limit": "no oracle"}]}
    checks = [{"id": "c1", "passed": True}]
    assert classify_disposition(delivered=True, verification=verification, checks=checks,
                                has_output=True) == "delivered_verified"
    basis = disposition_basis(delivered=True, verification=verification, checks=checks, has_output=True)
    assert basis["unconfirmed_segments"] == 1 and "oracle" in basis["oracle_note"]
    assert classify_disposition(delivered=True, verification=verification, checks=checks,
                                has_output=False) == "delivered_unverified"
    assert classify_disposition(delivered=False, verification=verification, checks=checks,
                                has_output=True) == "not_delivered"
    assert classify_disposition(delivered=True, verification={"ok": False, "unconfirmed": []},
                                checks=[{"passed": False}], has_output=True) == "delivered_unverified"


def test_not_delivered_claiming_an_output_is_a_detectable_violation():
    broken = {"disposition": "not_delivered", "output_ref": "ds_1"}
    assert validate_disposition(broken) == {"ok": False, "problem": "not_delivered_claims_output"}
    assert validate_disposition({"disposition": "made_up"})["problem"] == "disposition_unknown_value"
    assert validate_disposition({"id": "x"})["problem"] == "disposition_missing"
