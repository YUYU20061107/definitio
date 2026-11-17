"""Version-bound commit policy and staged file-artifact publication.

Policy only: the judging lives in definitio.verifier, the computing in the operators.
A file artifact is staged, verified, then published atomically; a failed verification
cleans the staging file and publishes nothing.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

from .model import GISFault, canonical, digest, name_ok, now, require
from .store import identifier
from . import verifier

WRITE_SCOPE_FORMAT = "definitio-write-scope-v1"
ARTIFACT_FORMAT = "definitio-file-artifact-v1"
COMMIT_STATES = ("committed", "rejected_state_changed", "pending_confirmation")


def artifact_name(name):
    require(isinstance(name, str) and name.endswith(".geojson"), message="文件产物名须以 .geojson 结尾")
    require("/" not in name and "\\" not in name and ".." not in name,
            message="文件产物名不得含路径分隔或上跳", target=name)
    name_ok(name[:-len(".geojson")])
    return name


def logical_target(step):
    """The name a step actually writes, resolved the same way at plan time and at commit time."""
    from .vector_extra import write_target
    if step["verb"] == "gis_append_dataset":
        return step.get("head_bindings", {}).get("target")
    if step["verb"] == "gis_add_layer_from_catalog":
        return step.get("head_bindings", {}).get("map")
    return write_target(step["verb"], step["parameters"])


def planned_destructions(task):
    """Inputs the plan itself declares it will destroy; their absence is an authorized effect."""
    return {target["target"] for target in task.get("write_scope", {}).get("dataset_targets", [])
            if target["mode"] == "destroy"}


def step_writes(task):
    """Actual dataset-side writes of a task, derived from its committed steps."""
    writes = []
    for step in task["steps"]:
        if step["verb"] == "gis_delete_dataset":
            if step["state"] == "COMMITTED":
                writes.append({"kind": "dataset_destroy", "target": step["parameters"]["dataset"]})
            continue
        if step.get("output_ref"):
            writes.append({"kind": "dataset", "target": logical_target(step)})
    return writes


# RT-04: side-effect classes that a bounded grant must cover explicitly.
HEAD_CHANGE_VERBS = ("gis_rename_dataset", "gis_append_dataset", "gis_add_layer_from_catalog", "gis_delete_dataset")
EXTERNAL_PUBLISH_VERBS = ("gis_publish_layer",)


def side_effect_inventory(task):
    """Count the four real side effects a task would cause, from the plan itself.

    Counts are derived from steps/exports, not from any self-declared plan_digest
    or from the task's own governance notes. Used both by the AgentService grant
    gate and by the direct Runtime gate, before any write is issued.
    """
    counts = {"dataset": 0, "file_export": 0, "logical_head": 0, "external_publish": 0}
    for step in task["steps"]:
        if step["level"] == "R0":
            continue
        if step["verb"] in EXTERNAL_PUBLISH_VERBS:
            counts["external_publish"] += 1
        else:
            counts["dataset"] += 1
        if step["verb"] in HEAD_CHANGE_VERBS:
            counts["logical_head"] += 1
    counts["file_export"] = len(task.get("exports") or [])
    return counts


def bind_write_scope(steps, exports, workspace):
    targets = []
    for step in steps:
        if step["verb"] == "gis_delete_dataset":
            targets.append({"step": step["index"], "verb": step["verb"],
                            "target": step["parameters"]["dataset"], "mode": "destroy", "kind": "dataset_destroy"})
            continue
        target = logical_target(step)
        if not target:
            continue
        mode = ("advance_head" if step["verb"] in ("gis_append_dataset", "gis_add_layer_from_catalog") else
                "rename" if step["verb"] == "gis_rename_dataset" else "create")
        targets.append({"step": step["index"], "verb": step["verb"], "target": target, "mode": mode, "kind": "dataset"})
    files = []
    for item in exports or []:
        require(isinstance(item, dict) and set(item) == {"step", "name", "format"},
                message="exports 每项须仅含 step/name/format")
        require(type(item["step"]) is int and 1 <= item["step"] <= len(steps), message="exports.step 越界")
        require(item["format"] == "geojson", "NOT_SUPPORTED", "文件产物首版只开放 geojson")
        artifact_name(item["name"])
        files.append({"step": item["step"], "name": item["name"], "format": item["format"]})
    require(len({f["name"] for f in files}) == len(files), message="exports 名称重复")
    return {"format": WRITE_SCOPE_FORMAT, "dataset_targets": targets, "file_artifacts": files,
            "workspace": str(workspace)}


def evaluate_binding(task, store, from_step=1):
    """Three-state gate: refuse a stale plan with reasons, or leave it pending confirmation.

    from_step lets a resume skip targets that its own already-committed steps occupy.
    """
    statuses = [verifier.version_status(store, ref) for ref in task["input_snapshot_refs"]]
    changed_inputs = [{"id": s["id"], "planned": s["planned"], "actual": s["actual"], "reason": s["reason"]}
                      for s in statuses if s["changed"]]
    changed_targets, reasons = [], []
    with store.connect() as db:
        for target in task.get("write_scope", {}).get("dataset_targets", []):
            if target["mode"] != "create" or target["step"] < from_step:
                continue
            row = db.execute("SELECT dataset_id FROM dataset_heads WHERE name=?", (target["target"],)).fetchone()
            if row:
                changed_targets.append({"target": target["target"], "occupied_by": row[0]})
    if changed_inputs:
        reasons.append(f"{len(changed_inputs)} 个输入版本在规划后已变化")
    if changed_targets:
        reasons.append(f"{len(changed_targets)} 个目标名称在规划后已被占用")
    return {"state": "rejected_state_changed" if reasons else "pending_confirmation", "reasons": reasons,
            "changed_inputs": changed_inputs, "changed_targets": changed_targets}


def artifact_paths(data_dir):
    root = Path(data_dir) / "artifacts"
    return root, root / ".staging"


def dataset_payload(dataset):
    from .semantics import crs_projection
    return {**dataset["geojson"], "definitio": {
        "crs": crs_projection(dataset["crs"]), "schema": dataset["fields"], "fingerprint": dataset["fingerprint"],
        "semantic": dataset.get("semantic"),
        "format": "GeoJSON-compatible snapshot; projected CRS retained; reproject to EPSG:4326 for RFC7946"}}


def verify_staged(path, expected_sha256, expected_fingerprint, expected_count):
    raw = path.read_bytes()
    actual = hashlib.sha256(raw).hexdigest()
    if actual != expected_sha256:
        return {"ok": False, "reason": "staged_bytes_sha256_mismatch"}
    try:
        document = json.loads(raw.decode("utf-8"))
    except (ValueError, UnicodeError) as exc:
        return {"ok": False, "reason": f"staged_not_reopenable: {exc}"}
    header = document.get("definitio", {})
    if header.get("fingerprint") != expected_fingerprint:
        return {"ok": False, "reason": "staged_fingerprint_mismatch"}
    if len(document.get("features", [])) != expected_count:
        return {"ok": False, "reason": "staged_feature_count_mismatch"}
    return {"ok": True, "sha256": actual, "feature_count": expected_count}


def _quarantine_published(data_dir, record):
    """Keep failed publication bytes as evidence instead of deleting them.

    The published filename is freed so a later retry is not permanently blocked,
    but the bytes survive under .staging/rolled-back-<id>-<name> for audit.
    """
    root, staging = artifact_paths(data_dir)
    path = root / record["name"]
    if not path.exists():
        return None
    staging.mkdir(parents=True, exist_ok=True)
    target = staging / f"rolled-back-{record['id']}-{record['name']}"
    os.replace(path, target)
    return str(target)


def recover_pending_publications(store, data_dir):
    """RT-07: reconcile disk / database / receipt for interrupted publishes.

    States: staging (registered, not yet on disk), published (bytes on disk,
    readback not yet confirmed), verified, published_unverified (readback failed,
    retained for review), rolled_back (bytes quarantined, registration retained
    but no longer occupying request_key/name). No state is silently skipped.
    """
    transitions = []
    for record in store.file_artifacts():
        state = record.get("state", "verified")
        if state == "staging":
            quarantined = _quarantine_published(data_dir, record)
            store.set_file_artifact_state(record["id"], "rolled_back", reason={
                "class": "not_published", "observed_phase": "staging",
                "bytes_visible_at_failure": False, "quarantine": quarantined,
                "error": "interrupted_before_publish"})
            store.release_file_artifact_keys(record["id"])
            transitions.append({"id": record["id"], "from": state, "to": "rolled_back",
                                "class": "not_published"})
        elif state == "published":
            readback = readback_file_artifact(data_dir, record)
            if readback["confirmed"]:
                store.set_file_artifact_state(record["id"], "verified")
                transitions.append({"id": record["id"], "from": state, "to": "verified",
                                    "class": "verified"})
            else:
                quarantined = _quarantine_published(data_dir, record)
                store.set_file_artifact_state(record["id"], "rolled_back", reason={
                    "class": "published_pending_review", "observed_phase": "published",
                    "bytes_visible_at_failure": quarantined is not None,
                    "quarantine": quarantined, "error": readback.get("reason")})
                store.release_file_artifact_keys(record["id"])
                transitions.append({"id": record["id"], "from": state, "to": "rolled_back",
                                    "class": "published_pending_review",
                                    "reason": readback.get("reason")})
    return transitions


def publication_evidence(store, data_dir):
    """RT-07: three-way reconciliation record (disk / registration / receipt).

    Every registration is retained. The evidence class distinguishes a publish
    that never became visible, one whose bytes were published but whose readback
    was never confirmed, and one that was rolled back after quarantine. Nothing
    is silently deleted and no failed registration keeps a UNIQUE key slot.
    """
    root, staging = artifact_paths(data_dir)
    rows = []
    for record in store.file_artifacts():
        state = record.get("state", "verified")
        reason = record.get("state_reason") or {}
        if not isinstance(reason, dict):
            reason = {"error": str(reason)}
        quarantined = (sorted(staging.glob(f"rolled-back-{record['id']}-*")) if staging.exists() else [])
        published = root / record["name"]
        if published.exists():
            disk_state = "present_at_published_name"
        elif quarantined:
            disk_state = "quarantined"
        else:
            disk_state = "absent"
        # A transient row has no state_reason yet; classify it from its phase so
        # no observation is left as "unclassified".
        derived = ({"verified": "verified", "staging": "not_published",
                    "published": "published_pending_review",
                    "rolled_back": "rolled_back_evidence"}).get(state, "unclassified")
        rows.append({"id": record["id"], "state": state,
                     "name": record["name"], "request_key": record["request_key"],
                     "original_name": record.get("original_name"),
                     "original_request_key": record.get("original_request_key"),
                     "keys_active": state in store.ACTIVE_ARTIFACT_STATES,
                     "evidence_class": reason.get("class") or derived,
                     "observed_phase": reason.get("observed_phase"),
                     "bytes_visible_at_failure": reason.get("bytes_visible_at_failure"),
                     "quarantine": [str(p) for p in quarantined],
                     "disk_state": disk_state,
                     "state_reason": reason})
    classes = {}
    for row in rows:
        classes[row["evidence_class"]] = classes.get(row["evidence_class"], 0) + 1
    return {"registrations": rows, "class_counts": classes,
            "active_registrations": sum(1 for r in rows if r["keys_active"]),
            "quarantined_bytes_preserved": sum(1 for r in rows if r["disk_state"] == "quarantined")}


def publish_file_artifact(store, data_dir, *, dataset, name, request_key, task_id, step_id):
    artifact_name(name)
    require(isinstance(request_key, str) and request_key, message="request_key 须为非空字符串")
    recover_pending_publications(store, data_dir)
    existing = store.file_artifact_by_key(request_key)
    if existing is not None:
        readback = readback_file_artifact(data_dir, existing)
        require(readback["confirmed"], "TARGET_STATE_UNKNOWN", "既有文件产物回读失败；不重复发布",
                artifact=existing["id"], artifact_state=existing.get("state"))
        if existing.get("state") == "published":
            existing = store.set_file_artifact_state(existing["id"], "verified")
        return {"artifact": existing, "replayed": True}
    require(store.file_artifact_by_name(name) is None, "ALREADY_EXISTS", "文件产物名已存在；不会覆盖", target=name)
    root, staging = artifact_paths(data_dir)
    staging.mkdir(parents=True, exist_ok=True)
    staged = staging / f"{identifier('stage')}.tmp"
    payload = canonical(dataset_payload(dataset)).encode("utf-8")
    expected = hashlib.sha256(payload).hexdigest()
    record = None
    try:
        staged.write_bytes(payload)
        checked = verify_staged(staged, expected, dataset["fingerprint"], dataset["feature_count"])
        require(checked["ok"], "BACKEND_ERROR", "暂存产物校验未通过；已清理暂存", reason=checked.get("reason"))
        root.mkdir(parents=True, exist_ok=True)
        published = root / name
        require(not published.exists(), "ALREADY_EXISTS", "文件产物名已存在；不会覆盖", target=name)
        # Registration is durable BEFORE the bytes become visible, so a crash
        # between the two is recoverable rather than an orphan file.
        record = {"id": identifier("fa"), "request_key": request_key, "name": name, "task_id": task_id,
                  "step_id": step_id, "dataset_id": dataset["id"], "sha256": expected,
                  "sha256_dataset": dataset["fingerprint"], "bytes": len(payload), "created_at": now(),
                  "format": ARTIFACT_FORMAT, "path": str(published), "state": "staging"}
        record["uri"] = f"definitio://artifacts/{record['id']}"
        store.save_file_artifact(record, state="staging")
        os.replace(staged, published)
        store.set_file_artifact_state(record["id"], "published")
        record["state"] = "published"
        readback = readback_file_artifact(data_dir, record)
        require(readback["confirmed"], "TARGET_STATE_UNKNOWN", "发布后回读未确认；不得报成功",
                artifact=record["id"], reason=readback.get("reason"))
        store.set_file_artifact_state(record["id"], "verified")
        record["state"] = "verified"
        return {"artifact": record, "replayed": False}
    except Exception as exc:
        if record is not None:
            root, _ = artifact_paths(data_dir)
            bytes_visible = (root / record["name"]).exists()
            quarantined = _quarantine_published(data_dir, record)
            # RT-07: keep a machine-readable evidence class so an auditor can
            # tell "never published" from "published, pending readback" from
            # "rolled back". Bytes are quarantined, never silently deleted.
            observed = record.get("state", "staging")
            evidence_class = ("published_pending_review" if observed == "published"
                              else "not_published" if observed == "staging" else "verification_incomplete")
            reason = {"class": evidence_class, "observed_phase": observed,
                      "bytes_visible_at_failure": bytes_visible,
                      "quarantine": quarantined,
                      "error": f"{type(exc).__name__}: {str(exc)[:200]}"}
            store.set_file_artifact_state(record["id"], "rolled_back", reason=reason)
            store.release_file_artifact_keys(record["id"])
        raise
    finally:
        if staged.exists():
            staged.unlink()


def readback_file_artifact(data_dir, record):
    root, _ = artifact_paths(data_dir)
    path = root / record["name"]
    if not path.exists():
        return {"confirmed": False, "reason": "published_file_missing"}
    raw = path.read_bytes()
    actual = hashlib.sha256(raw).hexdigest()
    if actual != record["sha256"] or len(raw) != record["bytes"]:
        return {"confirmed": False, "reason": "published_bytes_mismatch"}
    try:
        document = json.loads(raw.decode("utf-8"))
    except (ValueError, UnicodeError) as exc:
        return {"confirmed": False, "reason": f"published_not_reopenable: {exc}"}
    header = document.get("definitio", {})
    if header.get("fingerprint") != record["sha256_dataset"]:
        return {"confirmed": False, "reason": "published_fingerprint_mismatch"}
    return {"confirmed": True, "sha256": actual, "feature_count": len(document.get("features", [])),
            "fields": header.get("schema"), "crs": (header.get("crs") or {}).get("code"),
            "axis_order": (header.get("crs") or {}).get("axis_order")}


def live_readback(store, task):
    """RT-05: read the CURRENT state of every promised product.

    task["readback"] is an immutable historical execution receipt. This function
    never fills a live answer from it, never republishes a missing file, and
    verifies bytes, size and content binding for every file_artifact_ref.
    """
    datasets, files, problems = [], [], []
    for ref in task.get("output_refs", []):
        try:
            document = store.dataset(ref)
            fingerprint = store.verify_snapshot(document)
            datasets.append({"id": ref, "state": "present", "fingerprint": fingerprint})
        except Exception as exc:
            datasets.append({"id": ref, "state": "missing_or_corrupt"})
            problems.append({"kind": "dataset", "ref": ref, "reason": f"{type(exc).__name__}: {exc}"[:300]})
    data_dir = store.path.parent
    for ref in task.get("file_artifact_refs", []):
        try:
            record = store.file_artifact(ref)
        except Exception as exc:
            files.append({"id": ref, "state": "unknown", "confirmed": False})
            problems.append({"kind": "file_artifact", "ref": ref, "reason": f"registration_missing: {exc}"[:300]})
            continue
        name = record.get("original_name", record["name"])
        probe = {**record, "name": name}
        readback = readback_file_artifact(data_dir, probe)
        state = record.get("state", "verified")
        confirmed = bool(readback["confirmed"]) and state == "verified"
        files.append({"id": ref, "name": name, "state": state, "confirmed": confirmed,
                      "reason": readback.get("reason"), "sha256": readback.get("sha256"),
                      "bytes": len((data_dir / "artifacts" / name).read_bytes()) if (data_dir / "artifacts" / name).exists() else None})
        if not confirmed:
            problems.append({"kind": "file_artifact", "ref": ref, "name": name, "state": state,
                             "reason": readback.get("reason") or f"state_{state}"})
    return {"state": "confirmed" if not problems else "corrupted",
            "datasets": datasets, "file_artifacts": files, "problems": problems,
            "as_of": now(), "source": "live_disk_and_database_readback",
            "historical_receipt_confirmed": bool((task.get("readback") or {}).get("confirmed"))}