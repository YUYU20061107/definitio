"""Step checkpoints and dependency invalidation for resumable plans.

Recovery is only allowed from results that are still valid. A changed input version
invalidates the dependent result and refuses recovery instead of silently reusing it.
"""
from __future__ import annotations

from .model import GISFault, canonical, digest, now
from . import verifier

CHECKPOINT_FORMAT = "definitio-step-checkpoint-v1"
SIDE_EFFECTS = {"gis_delete_dataset": "destroy", "gis_rename_dataset": "rename",
                "gis_append_dataset": "advance_head", "gis_add_layer_from_catalog": "advance_head",
                "gis_publish_layer": "external_publish", "gis_export_map_image": "external_file"}
NON_RETRYABLE = {"destroy", "rename", "advance_head", "external_publish", "external_file"}


def version_of(document):
    return {"id": document["id"], "fingerprint": document["fingerprint"],
            "record_digest": digest({key: value for key, value in document.items() if key != "logical_name"})}


def checkpoint(step, *, inputs, output, evidence_ref, task):
    side = SIDE_EFFECTS.get(step["verb"], "new_dataset" if output else "none")
    uses_previous = "$previous" in canonical(step["parameters"])
    dependencies = [task["steps"][step["index"] - 2]["id"]] if uses_previous and step["index"] > 1 else []
    return {"format": CHECKPOINT_FORMAT, "dependencies": dependencies,
            "input_versions": [version_of(d) for d in inputs],
            "output_object": version_of(output) if output else None,
            "check_location": {"evidence_ref": evidence_ref, "uri": f"definitio://evidence/{evidence_ref}"},
            "side_effects": side, "retryable": side not in NON_RETRYABLE, "valid": True, "state_at": now()}


def validate_checkpoints(task, store):
    """Re-read every committed step's dependencies and output; report what is no longer valid."""
    valid, invalidated = [], []
    for step in task["steps"]:
        cp = step.get("checkpoint")
        if step["state"] != "COMMITTED" or not cp:
            continue
        reason = None
        for ref in cp["input_versions"]:
            status = verifier.version_status(store, ref)
            if status["changed"]:
                reason = f"输入 {ref['id']} 版本已变化（{status['reason']}）"
                break
        if reason is None and cp["output_object"]:
            status = verifier.version_status(store, cp["output_object"])
            if status["changed"]:
                reason = f"输出 {cp['output_object']['id']} 已失效（{status['reason']}）"
        if reason:
            cp["valid"] = False
            invalidated.append({"step": step["index"], "step_id": step["id"], "reason": reason})
        else:
            cp["valid"] = True
            valid.append(step["id"])
    return {"valid": valid, "invalidated": invalidated}


def next_resumable_index(task):
    return next((i for i, step in enumerate(task["steps"]) if step["state"] != "COMMITTED"), len(task["steps"]))


def require_resumable(task):
    from .model import require
    require(task["task_state"] == "FAILED", "TARGET_STATE_UNKNOWN",
            "只有 FAILED 任务可恢复；ISOLATED 表示外部状态未知，须人工核查后新建任务",
            task_state=task["task_state"], rule="resume_state_not_recoverable")