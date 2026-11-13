"""Run the same GIS compute path on private in-memory proposals; SQLite is read-only here."""
from __future__ import annotations

import copy

from . import verifier
from .model import GISFault, summarize, validate_geojson
from .operations import SPECS, compute
from .vector_extra import write_target


def value_summary(value):
    if isinstance(value, dict):
        return {"kind": "object", "keys": list(value)[:64],
                "scalars": {k: v for k, v in value.items()
                            if v is None or type(v) in (bool, int, float) or isinstance(v, str) and len(v) <= 256},
                "collections": {k: {"kind": "array" if isinstance(v, list) else "object", "count": len(v)}
                                for k, v in value.items() if isinstance(v, (list, dict))}}
    if isinstance(value, list):
        return {"kind": "array", "count": len(value)}
    return {"kind": "scalar", "value": value if not isinstance(value, str) else value[:256]}


def dataset_summary(d):
    from .semantics import crs_projection
    return {k: d[k] for k in ("fields", "feature_count", "geometry_types", "bounds", "kind", "grid", "point_count", "point_dimensions") if k in d} | {
        "crs": crs_projection(d["crs"])}


def preview(runtime, goal, steps):
    task = runtime.declare(goal, steps, persist=False)
    outputs, results, previous, failed = {}, [], None, False
    for step in task["steps"]:
        if failed:
            results.append({"step": step["index"], "verb": step["verb"], "state": "BLOCKED"})
            continue
        p = copy.deepcopy(step["parameters"])
        spec = SPECS[step["verb"]]
        inputs, input_sources = [], []
        for key in spec[4]:
            refs = p[key] if key == "inputs" else [p[key]]
            resolved = [previous if ref == "$previous" else ref for ref in refs]
            input_sources.extend({"parameter": key, "reference": ref, "resolved_id": resolved_ref,
                                  "source": "previous_step" if ref == "$previous" else "workspace_snapshot",
                                  **({"step": step["index"] - 1} if ref == "$previous" else {})}
                                 for ref, resolved_ref in zip(refs, resolved))
            p[key] = resolved if key == "inputs" else resolved[0]
            inputs.extend(outputs[ref] if ref in outputs else runtime.store.dataset(ref) for ref in resolved)
        from .pointcloud import extra_references
        for ref in extra_references(step["verb"], p):
            inputs.append(runtime.store.dataset(ref))
            input_sources.append({"parameter": "params.inputs", "reference": ref, "resolved_id": ref, "source": "workspace_snapshot"})
        if step["verb"] == "gis_run_processing":
            inputs.append(runtime.store.dataset(p["params"]["INPUT"]))
            input_sources.append({"parameter": "params.INPUT", "reference": p["params"]["INPUT"],
                                  "resolved_id": inputs[-1]["id"], "source": "workspace_snapshot"})
        if step["verb"] == "gis_run_gp":
            inputs.append(runtime.store.dataset(p["args"]["in_rows"]))
            input_sources.append({"parameter": "args.in_rows", "reference": p["args"]["in_rows"],
                                  "resolved_id": inputs[-1]["id"], "source": "workspace_snapshot"})
        for key, name in step.get("head_bindings", {}).items():
            for d in inputs:
                if d["id"] == p[key]:
                    d["logical_name"] = name
        target = step.get("head_bindings", {}).get("target", write_target(step["verb"], p))
        item = {"step": step["index"], "verb": step["verb"], "target": target,
                "input_sources": input_sources,
                "inputs": [dataset_summary(d) for d in inputs]}
        try:
            precheck = verifier.run(verifier.context(
                stage="precheck", verb=step["verb"], vid=step["vid"], parameters=p, inputs=inputs,
                proposal=None, output=None, store=runtime.store, db=None,
                planned_inputs=task["input_snapshot_refs"], write_scope=task.get("write_scope"),
                actual_writes=[], authorization=None, workspace=str(runtime.store.path.parent),
                file_artifact=None),
                predicates=verifier.POLICY["precheck"])
            item["precheck"] = precheck
            verifier.enforce_precheck(precheck)
            from .completion import SPECS as COMPLETION_SPECS, compute as completion_compute
            if step["verb"] == "gis_delete_dataset":
                impact = runtime.store.deletion_impact(inputs[0]["id"])
                proposal = {"result": {"would_destroy": inputs[0]["id"], "impact": impact, "external_writes": 0}}
            elif step["verb"] in COMPLETION_SPECS:
                proposal = completion_compute(runtime, step["verb"], p, inputs, dry_run=True)
            else:
                proposal = compute(step["verb"], p, inputs)
            if "geojson" in proposal:
                gj, crs, fields = validate_geojson(proposal["geojson"], proposal["crs"], proposal["schema"])
                previous = "preview_" + str(step["index"])
                output = {"id": previous, "name": target, "geojson": gj, **summarize(gj, crs, fields)}
                outputs[previous] = output
                item["output"] = dataset_summary(output)
                before = [f["name"] for f in inputs[0]["fields"]] if inputs else []
                after = [f["name"] for f in fields]
                item["effects"] = {"added_fields": [f for f in after if f not in before],
                                   "dropped_fields": [f for f in before if f not in after],
                                   "crs_changed": bool(inputs and crs["definition_hash"] != inputs[0]["crs"]["definition_hash"])}
            elif proposal.get("lifecycle") == "rename":
                previous = "preview_" + str(step["index"])
                outputs[previous] = {**inputs[0], "id": previous, "name": target}
                item["output"] = dataset_summary(outputs[previous])
                item["result_summary"] = value_summary(proposal["result"])
            elif "raster" in proposal:
                from .raster import validate_raster, summary
                raster, crs = validate_raster(proposal["raster"], proposal["crs"])
                previous = "preview_" + str(step["index"])
                outputs[previous] = {"id": previous, "name": target, "raster": raster, **summary(raster, crs)}
                item["output"] = dataset_summary(outputs[previous])
                item["effects"] = {"raster_grid": outputs[previous]["grid"], "input_snapshots_unchanged": True}
            elif "pointcloud" in proposal:
                from .pointcloud import summary
                previous = "preview_" + str(step["index"])
                outputs[previous] = {"id": previous, "name": target, "pointcloud": proposal["pointcloud"], **summary(proposal["pointcloud"], proposal["crs"])}
                item["output"] = dataset_summary(outputs[previous])
                item["effects"] = {"input_snapshots_unchanged": True, "temporary_files": "private automatically removed PDAL workspace"}
            elif "resource" in proposal:
                from .completion import resource_summary
                previous = "preview_" + str(step["index"])
                outputs[previous] = {"id": previous, "name": target, "resource": proposal["resource"], **resource_summary(proposal["resource_kind"], proposal["resource"], proposal["crs"])}
                item["output"] = dataset_summary(outputs[previous])
                item["result_summary"] = value_summary(proposal.get("result"))
            else:
                previous = None
                item["result_summary"] = value_summary(proposal.get("result"))
            verification = verifier.run(verifier.context(
                stage="preview", verb=step["verb"], vid=step["vid"], parameters=p, inputs=inputs,
                proposal=proposal, output=outputs.get(previous) if previous else None, store=runtime.store, db=None,
                planned_inputs=task["input_snapshot_refs"], write_scope=task.get("write_scope"),
                actual_writes=[], authorization=None, workspace=str(runtime.store.path.parent),
                file_artifact=None))
            item["verification"] = verification
            verifier.enforce_precheck(verification)
            item["state"] = "PASS"
        except GISFault as fault:
            item.update(state="FAIL", error=fault.payload())
            failed = True
        results.append(item)
    return {"ok": not failed, "mode": "isolated_temporary_compute" if any(s["verb"].startswith("gis_pointcloud_") for s in task["steps"]) else "in_memory_compute", "workspace_writes": 0,
            "steps": results, "versions": runtime.versions,
            "limits": ["Preview is a point-in-time computation, not a reservation or authorization",
                       "Execution rechecks immutable inputs and target availability"]}
