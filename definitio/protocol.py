"""Protocol v0.5 additions: delivery disposition, handle resolution, read tri-state.

Additive by construction: no existing evidence field changes meaning, no existing
control-plane error code is replaced, and no read path invents a disposition.
The frozen G1 contract text is left untouched; the live addendum lives in
docs/CONTRACT-ADDENDUM-v0.5.md inside this tree.
"""
from __future__ import annotations

from .model import GISFault, require

DISPOSITION_VALUES = ("delivered_verified", "delivered_unverified", "not_delivered")
DISPOSITION_MEANING = {
    "delivered_verified": "成果已交付且经真实读回核验：有落库输出、校验通过、后置检查全为真且无无法确认段",
    "delivered_unverified": "成果已交付但核验不完整：只读结果、无独立读回、存在 unconfirmed 段或检查不完备",
    "not_delivered": "声明的成果未交付：写入前失败、预演不落库、或交付中断，不得冒充已交付",
}
READ_STATES = ("Known", "Absent", "Unknown")
HANDLE_RESOLUTION_CODES = ("STALE", "AMBIGUOUS")

# Capture proofs are counted by (role, stable field values). Volatile values and
# ranges are deliberately NOT part of identity: two observations of the same
# object with different measured values still count as ONE captured handle.
CAPTURE_PROOF_COUNTING_RULE = (
    "捕获证明按「角色 + 稳定字段」去重计数；值(value/values)、范围(range/ranges/bounds/extent)、"
    "计数(count/feature_count)等易变量不作身份。同一身份的多次观测计 1；"
    "0 个身份判 STALE，>=2 个身份判 AMBIGUOUS，恰好 1 个身份才解析为句柄。"
)
NON_IDENTITY_FIELDS = frozenset({
    "value", "values", "range", "ranges", "bounds", "extent", "min", "max",
    "count", "feature_count", "observed_at", "timestamp", "measured_value",
})


def capture_identity(candidate, role, stable_fields):
    """Identity of one capture proof. Never folds values or ranges into identity."""
    return (str(role),) + tuple(
        (str(field), _freeze(candidate.get(field))) for field in stable_fields
    )


def _freeze(value):
    if isinstance(value, (list, tuple)):
        return tuple(_freeze(item) for item in value)
    if isinstance(value, dict):
        return tuple(sorted((str(k), _freeze(v)) for k, v in value.items()))
    return value


def resolve_handle(candidates, *, role, stable_fields):
    """Resolve one handle from capture proofs, or fail with a protocol-level code.

    This is a NEW protocol surface. It deliberately does not replace the control
    plane's Store.dataset() NOT_FOUND behaviour, so all 357 existing verdicts
    stay unchanged. See docs/CONTRACT-ADDENDUM-v0.5.md section B.
    """
    require(isinstance(stable_fields, (tuple, list)) and len(stable_fields) > 0,
            "INVALID_ARGUMENT", "句柄解析须给出至少一个稳定字段", role=str(role))
    bad = [f for f in stable_fields if str(f) in NON_IDENTITY_FIELDS]
    require(not bad, "INVALID_ARGUMENT", "值与范围不作身份，不能当稳定字段", rejected=bad)
    by_identity = {}
    for candidate in candidates or ():
        key = capture_identity(candidate, role, stable_fields)
        by_identity.setdefault(key, candidate)
    identities = sorted(by_identity, key=repr)
    details = {"role": str(role), "stable_fields": [str(f) for f in stable_fields],
               "candidate_count": len(candidates or ()),
               "identity_count": len(identities),
               "counting": CAPTURE_PROOF_COUNTING_RULE}
    if not identities:
        raise GISFault("STALE", "句柄解析 0 命中：声明的对象已失效或从未被捕获", **details)
    if len(identities) > 1:
        raise GISFault("AMBIGUOUS", "句柄解析 >=2 命中：必须给出能区分身份的稳定字段", **details)
    return by_identity[identities[0]]


def classify_disposition(*, delivered, verification=None, checks=None, has_output=False):
    """Derive the disposition from actual delivery + verification facts.

    Write path only. Read paths must never call this to re-label an envelope.
    `verified` means a persisted deliverable whose readback checks really ran
    and all passed. It is NOT an independent geometry oracle and never upgrades
    certification; unconfirmed segments are recorded verbatim in the basis.
    """
    if not delivered:
        return "not_delivered"
    verification = verification or {}
    checks = checks or []
    complete = (bool(has_output) and verification.get("ok") is True and bool(checks)
                and all(bool(check.get("passed")) for check in checks))
    return "delivered_verified" if complete else "delivered_unverified"


def disposition_basis(*, delivered, verification=None, checks=None, has_output=False):
    """Auditable facts behind the disposition, so a reviewer can recompute it."""
    verification = verification or {}
    checks = checks or []
    return {"delivered": bool(delivered), "has_persisted_output": bool(has_output),
            "verification_ok": bool(verification.get("ok")),
            "checks_total": len(checks),
            "checks_passed": sum(1 for check in checks if bool(check.get("passed"))),
            "unconfirmed_segments": len(verification.get("unconfirmed") or []),
            "oracle_note": "读回核验为同核自洽；delivered_verified 不表示独立几何 oracle，也不升级 certification"}


def non_delivery_envelope(*, evidence_id, task_id, step_id, verb, reason):
    """An explicit, persistent not_delivered envelope for a step that produced no deliverable."""
    return {"id": evidence_id, "task_id": task_id, "step_id": step_id, "verb": verb,
            "disposition": "not_delivered", "disposition_reason": reason,
            "certification": "NOT_CERTIFIED"}


def read_state(loader, evidence_id, field):
    """Single-read tri-state. A failed or malformed read is Unknown, never Absent."""
    try:
        document = loader(evidence_id)
    except Exception as exc:  # injected read failure must not collapse into "definitely absent"
        return {"state": "Unknown", "field": field, "reason": "read_failed",
                "error_type": type(exc).__name__}
    if not isinstance(document, dict):
        return {"state": "Unknown", "field": field, "reason": "malformed_evidence"}
    if field not in document:
        return {"state": "Absent", "field": field, "reason": "field_not_present"}
    return {"state": "Known", "field": field, "value": document[field]}


def validate_disposition(evidence):
    """Consistency checker used by the T6 self-check suite."""
    if not isinstance(evidence, dict):
        return {"ok": False, "problem": "envelope_not_object"}
    value = evidence.get("disposition")
    if value is None:
        return {"ok": False, "problem": "disposition_missing"}
    if value not in DISPOSITION_VALUES:
        return {"ok": False, "problem": "disposition_unknown_value", "found": value}
    if value == "not_delivered" and evidence.get("output_ref"):
        return {"ok": False, "problem": "not_delivered_claims_output"}
    return {"ok": True, "disposition": value}
