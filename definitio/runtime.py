"""Persistent task control plane, copy-on-write execution, and evidence readback."""
from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path

from . import __version__
from .model import GISFault, canonical, digest, logical_fingerprint, now, require, validate_geojson

import rasterio
import shapely
from . import commit, resume, verifier
from .operations import SPECS, compute, validate_request
from .planner import plan_goal
from .store import Store, identifier
from .verification import verify_output
from .vector_extra import write_target
from .completion import SPECS as COMPLETION_SPECS


def snapshot_ref(d):
    """Plan-side binding of one input version: logical fingerprint plus whole-record digest."""
    return {"id": d["id"], "name": d["name"], "fingerprint": d["fingerprint"],
            "record_digest": digest({key: value for key, value in d.items() if key != "logical_name"})}


class Runtime:
    def __init__(self, db_path):
        package = Path(__file__).parent
        execution_files = list(package.glob("*.py")) + [package.parent / "scripts" / name for name in ("native_gis_worker.py", "qgis_processing_worker.py")]
        self.source_digest = digest({p.relative_to(package.parent).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
                                     for p in sorted(execution_files)})
        self.store = Store(db_path)
        self.store.recover_interrupted()
        self.binary_handles = {}

    @property
    def versions(self):
        return {"runtime": __version__, "contract": "G1-v0.1-draft (not certified)",
                "interface": "definitio-local-v0.1", "normalization": "definitio-logical-v1",
                "shapely": shapely.__version__, "geos": shapely.geos_version_string,
                "rasterio": rasterio.__version__, "gdal": rasterio.__gdal_version__,
                "planner": "local-intent-grammar-v1", "comparator": "readback-invariants-v2",
                "source_digest": self.source_digest,
                "independent_geometry_oracle": False}

    def capabilities(self):
        from .schemas import operation_schema
        from .guidance import family_for, prerequisites
        from .completion import ROUTES, BACKENDS
        from .environment import dependency_info
        catalog = json.loads((Path(__file__).parent / "catalog.json").read_text(encoding="utf-8"))
        items = []
        for item in catalog["verbs"]:
            spec = SPECS.get(item["name"])
            items.append({**item, **dependency_info(item["name"]), "implemented": spec is not None, "status": "local_implemented" if spec else "not_supported",
                          "implementation_status": "local_implemented" if spec else "not_supported",
                          "local_level": spec[2] if spec else None, "label": spec[1] if spec else item["name"],
                          "required_parameters": spec[3] if spec else [], "backend": BACKENDS.get(item["name"], ["local-geos"])[0],
                          "input_schema": operation_schema(item["name"]) if spec else None,
                          "family": family_for(item["name"]), "prerequisites": prerequisites(item["name"]) if spec else ["Not implemented; do not call on this engine"],
                          "certification": "NOT_CERTIFIED", "implementation_route": ROUTES.get(item["name"], "installed_pdal" if item["name"].startswith("gis_pointcloud_") else "installed_qgis_processing_seed" if item["name"] == "gis_run_processing" else "local_geos_gdal")})
        return {"verbs": items, "implemented": sum(v["implemented"] for v in items), "total": len(items),
                "versions": self.versions, "scope": "2D GeoJSON vectors and bounded single-band rasters; local-only",
                "g1_registry_unchanged": True, "provenance": catalog["provenance"]}

    def import_dataset(self, name, geojson, crs, schema=None, source_kind="user_import"):
        gj, crs, schema = validate_geojson(geojson, crs, schema)
        return self.store.insert_dataset(name, gj, crs, schema, {"kind": source_kind})

    def import_raster(self, name, raster, crs):
        from .raster import validate_raster
        raster, crs = validate_raster(raster, crs)
        return self.store.insert_raster(name, raster, crs, {"kind": "agent_raster_import"})

    def declare(self, goal, steps=None, dataset=None, secondary=None, persist=True, exports=None):
        require(isinstance(goal, str) and 0 < len(goal.strip()) <= 1000, message="任务名称须为 1–1000 字")
        planner = "structured-plan-v1"
        if steps is None:
            require(isinstance(dataset, str), message="请先选择输入数据")
            steps = plan_goal(goal, self.store.dataset(dataset), secondary)
            planner = self.versions["planner"]
        require(isinstance(steps, list) and 1 <= len(steps) <= 6, message="任务须包含 1–6 步")
        snapshots, normalized, targets = {}, [], set()
        for i, raw in enumerate(steps):
            require(isinstance(raw, dict) and set(raw) == {"verb", "parameters"}, message="每步须仅含 verb / parameters")
            verb, p = raw["verb"], copy.deepcopy(raw["parameters"])
            require(isinstance(verb, str), message="verb 须为字符串")
            controls = sorted(set(p) & {"request_id", "authorization_id"}) if isinstance(p, dict) else []
            require(not controls, message="步骤 parameters 只能包含操作参数；授权与请求字段应放在控制面调用中",
                    rule="operation_parameters_only", step=i + 1, verb=verb, fields=controls,
                    hint="request_id 放在 task_plan；authorization_id 放在 task_execute；task_dry_run 不需要授权")
            try:
                spec = validate_request(verb, p)
            except GISFault as fault:
                raise GISFault(fault.code, fault.message, **{**fault.details, "step": i + 1, "verb": verb,
                               "hint": "按 gis_describe_algorithm 的操作 schema 填写 parameters；控制字段放在 task_plan/task_execute"}) from fault
            from .completion import precheck
            precheck(self, verb, p)
            head_bindings = {}
            require(verb not in ("gis_list_algorithms", "gis_describe_algorithm") or len(steps) == 1,
                    message="能力查询仅能单独执行")
            for key in spec[4]:
                for ref in (p[key] if key == "inputs" else [p[key]]):
                    if ref == "$previous":
                        require(not (verb == "gis_rename_dataset" or verb == "gis_append_dataset" and key == "target"),
                                "NOT_SUPPORTED", "逻辑改名/追加目标须在规划时已存在，不能使用 $previous")
                        require(i > 0 and write_target(normalized[-1]["verb"], normalized[-1]["parameters"]) is not None,
                                message="$previous 必须引用前一步数据成果",
                                rule="previous_dataset_output", step=i + 1, parameter=key,
                                hint="第一步使用已存在的数据集句柄；$previous 只引用紧邻前一步的数据集成果")
                    else:
                        require(ref not in targets, message="计划中的新目标尚未落库，步间引用须使用 $previous",
                                rule="previous_dataset_output", step=i + 1, parameter=key, reference=ref,
                                hint="紧邻前一步的数据集成果请写 $previous；更早成果须拆成独立计划并使用执行后返回的句柄")
                        d = self.store.dataset(ref)
                        self.store.verify_snapshot(d)
                        snapshots[d["id"]] = snapshot_ref(d)
                        if key != "inputs":
                            p[key] = d["id"]
                        if verb == "gis_rename_dataset" or verb == "gis_append_dataset" and key == "target" or verb == "gis_add_layer_from_catalog" and key == "map":
                            with self.store.connect() as db:
                                row = db.execute("SELECT name FROM dataset_heads WHERE dataset_id=?", (d["id"],)).fetchone()
                            require(row is not None, "NOT_FOUND", "该历史快照已不是活动逻辑对象")
                            head_bindings[key] = row[0]
                if key == "inputs":
                    p[key] = [r if r == "$previous" else self.store.dataset(r)["id"] for r in p[key]]
            from .pointcloud import extra_references
            extras = []
            for ref in extra_references(verb, p):
                require(ref != "$previous", "NOT_SUPPORTED", "merge 附加输入须在规划时已有固定快照")
                d = self.store.dataset(ref)
                self.store.verify_snapshot(d)
                snapshots[d["id"]] = snapshot_ref(d)
                extras.append(d["id"])
            if extras:
                p["params"]["inputs"] = extras
            if verb == "gis_run_processing":
                d = self.store.dataset(p["params"]["INPUT"])
                self.store.verify_snapshot(d)
                snapshots[d["id"]] = snapshot_ref(d)
                p["params"]["INPUT"] = d["id"]
            if verb == "gis_run_gp":
                d = self.store.dataset(p["args"]["in_rows"])
                self.store.verify_snapshot(d)
                snapshots[d["id"]] = snapshot_ref(d)
                p["args"]["in_rows"] = d["id"]
            target = write_target(verb, p)
            if target and verb != "gis_append_dataset":
                from .model import name_ok
                name_ok(target)
                require(target not in targets, "ALREADY_EXISTS", "任务内目标名称重复")
                try:
                    self.store.dataset(target)
                except GISFault as exc:
                    if exc.code != "NOT_FOUND":
                        raise
                else:
                    raise GISFault("ALREADY_EXISTS", "目标已存在；请在规划时显式选择新名称", target=target)
                targets.add(target)
            normalized.append({"id": identifier("step"), "index": i + 1, "verb": verb, "vid": spec[0], "label": spec[1],
                               "level": spec[2], "parameters": p, "state": "DECLARED", "attempt_seq": 0,
                               "obligations": [f"SC-{spec[0]}-F09", f"SC-{spec[0]}-F10", f"SC-{spec[0]}-F13"]})
            if head_bindings:
                normalized[-1]["head_bindings"] = head_bindings
        task = {"id": identifier("task"), "task_version": "1", "goal": goal.strip(), "task_state": "DECLARED",
                "created_at": now(), "state_as_of": now(), "planner": planner, "steps": normalized,
                "input_snapshot_refs": list(snapshots.values()), "versions": self.versions,
                "governance_state": {"confirmation_required": any(s["level"] != "R0" for s in normalized), "confirmed": False},
                "execution_started": False, "output_refs": [], "evidence_refs": [], "replay_count": 0,
                "exports": list(exports or []), "actual_writes": [], "file_artifact_refs": [],
                "commit_state": "pending_confirmation",
                "certification": "NOT_CERTIFIED", "warnings": ["本地参考路线未取得 G1 验收或 L1–L3 认证；容差未校准"]}
        task["write_scope"] = commit.bind_write_scope(normalized, exports, self.store.path.parent)
        task["plan_digest"] = self.plan_digest(task)
        if persist:
            self.store.save_task(task)
        return task

    @staticmethod
    def plan_digest(task):
        return digest({"goal": task["goal"], "inputs": task["input_snapshot_refs"],
                       "steps": [{k: s[k] for k in ("verb", "parameters", "level", "head_bindings") if k in s} for s in task["steps"]],
                       "write_scope": task.get("write_scope"), "versions": task["versions"]})

    def execute(self, task_id, plan_digest, confirm=False):
        with self.store.lock:
            task = self.store.task(task_id)
            require(plan_digest == task["plan_digest"] == self.plan_digest(task), message="计划摘要不一致，请重新规划后确认")
            if task["task_state"] == "COMMITTED":
                for ref in task["output_refs"]:
                    self.store.verify_snapshot(self.store.dataset(ref))
                task["replay_count"] += 1
                self.store.save_task(task)
                return task
            require(task["task_state"] == "DECLARED", "TARGET_STATE_UNKNOWN", "该任务已经结束或被隔离；禁止盲目重放", task_state=task["task_state"])
            require(task["versions"]["source_digest"] == self.source_digest, "TARGET_STATE_UNKNOWN", "计划建立后执行源码已变化；须重新规划；已提交回执仍可读取")
            from .completion import precheck
            for step in task["steps"]:
                precheck(self, step["verb"], step["parameters"])
            require(not task["governance_state"]["confirmation_required"] or confirm is True,
                    "CONFIRM_REQUIRED", "执行前必须确认全部输出目标与字段变化", task_id=task_id)
            binding = commit.evaluate_binding(task, self.store)
            if binding["state"] == "rejected_state_changed":
                task["commit_state"] = "rejected_state_changed"
                code = "TARGET_STATE_UNKNOWN" if binding["changed_inputs"] else "ALREADY_EXISTS"
                fault = GISFault(code, "规划后状态已变化；拒绝旧计划：" + "；".join(binding["reasons"]),
                                 rule="version_binding_changed", reasons=binding["reasons"],
                                 changed_inputs=binding["changed_inputs"], changed_targets=binding["changed_targets"])
                task["error"] = fault.payload()
                task["completion"] = "none"
                self._transition(task, "FAILED")
                return task
            task["governance_state"]["confirmed"] = confirm is True
            task["actual_writes"] = []
            self._transition(task, "PRECHECKED")
            self._transition(task, "EXECUTING")
            return self._run_steps(task, None, 0)

    def _run_steps(self, task, previous, start_index):
        for step in task["steps"][start_index:]:
            try:
                output = self._execute_step(task, step, previous)
                if output:
                    previous = output["id"]
            except Exception as exc:
                fault = exc if isinstance(exc, GISFault) else GISFault("BACKEND_ERROR", "本地计算失败，当前步骤已回滚", reason=str(exc)[:500])
                if step["verb"] == "gis_publish_layer" and fault.code == "TARGET_STATE_UNKNOWN":
                    task["execution_started"] = True
                step["state"] = "ISOLATED" if fault.code == "TARGET_STATE_UNKNOWN" else "FAILED"
                step["error"] = fault.payload()
                task["error"] = fault.payload()
                for following in task["steps"][step["index"]:]:
                    following["state"] = "BLOCKED"
                self._transition(task, "ISOLATED" if step["state"] == "ISOLATED" else "FAILED")
                task["completion"] = "partial" if task["output_refs"] else "none"
                self.store.save_task(task)
                return task
        try:
            return self._finalize_commit(task)
        except Exception as exc:
            fault = exc if isinstance(exc, GISFault) else GISFault("BACKEND_ERROR", "提交阶段失败", reason=str(exc)[:500])
            task["error"] = fault.payload()
            task["commit_state"] = "pending_confirmation"
            task["completion"] = "partial" if task["output_refs"] else "none"
            self._transition(task, "FAILED")
            return task

    def _finalize_commit(self, task):
        """Real readback first; only a confirmed readback may report a committed state."""
        task["commit_state"] = "pending_confirmation"
        self.store.save_task(task)
        outputs = []
        for ref in task["output_refs"]:
            d = self.store.dataset(ref)
            outputs.append({"id": d["id"], "name": d["name"], "fingerprint": self.store.verify_snapshot(d)})
        files, actual = [], commit.step_writes(task)
        for item in task.get("exports", []):
            step = task["steps"][item["step"] - 1]
            require(step.get("output_ref"), "TARGET_STATE_UNKNOWN", "导出步骤没有已提交的数据集成果", step=item["step"])
            dataset = self.store.dataset(step["output_ref"])
            key = digest({"task": task["id"], "step": step["id"], "name": item["name"]})
            published = commit.publish_file_artifact(self.store, self.store.path.parent, dataset=dataset,
                                                     name=item["name"], request_key=key,
                                                     task_id=task["id"], step_id=step["id"])
            record = published["artifact"]
            if record["id"] not in task["file_artifact_refs"]:
                task["file_artifact_refs"].append(record["id"])
            actual.append({"kind": "file", "target": record["name"]})
            files.append({"id": record["id"], "name": record["name"], "replayed": published["replayed"],
                          **commit.readback_file_artifact(self.store.path.parent, record)})
        task["actual_writes"] = actual
        destroyed = {item["id"] for item in task.get("destroyed_inputs", [])}
        verification = verifier.run(verifier.context(
            stage="commit", verb="task_commit", vid="", parameters={}, inputs=[], proposal=None, output=None,
            store=self.store, db=None,
            planned_inputs=[ref for ref in task["input_snapshot_refs"] if ref["id"] not in destroyed],
            write_scope=task.get("write_scope"), actual_writes=actual,
            authorization=task["governance_state"], workspace=str(self.store.path.parent), file_artifact=None),
            predicates=verifier.POLICY["commit_task"])
        task["verification"] = verification
        for found in verification["found"]:
            require(found["passed"] is not False,
                    "AUTHORIZATION_SCOPE" if found["predicate"] == "write_scope" else "TARGET_STATE_UNKNOWN",
                    found["observation"], predicate=found["predicate"], rule="commit_verification",
                    **found["details"])
        confirmed = all(item["confirmed"] for item in files) and len(outputs) == len(task["output_refs"])
        task["readback"] = {"confirmed": bool(confirmed), "outputs": outputs, "file_artifacts": files,
                            "destroyed_inputs": sorted(destroyed)}
        require(confirmed, "TARGET_STATE_UNKNOWN", "提交后真实状态回读未确认；不得报成功")
        task["commit_state"] = "committed"
        task["completion"] = "all"
        self._transition(task, "COMMITTED")
        return task

    def resume(self, task_id, plan_digest, confirm=False):
        """Continue a FAILED plan from its still-valid committed results; never rerun the whole chain."""
        with self.store.lock:
            task = self.store.task(task_id)
            require(plan_digest == task["plan_digest"] == self.plan_digest(task), message="计划摘要不一致，请重新规划后确认")
            resume.require_resumable(task)
            require(task["versions"]["source_digest"] == self.source_digest, "TARGET_STATE_UNKNOWN",
                    "计划建立后执行源码已变化；须重新规划")
            report = resume.validate_checkpoints(task, self.store)
            require(not report["invalidated"], "TARGET_STATE_UNKNOWN",
                    "已提交步骤的依赖版本失效；须重新规划，不得复用旧结论",
                    rule="resume_dependency_invalidated", invalidated=report["invalidated"])
            from .completion import precheck
            start = resume.next_resumable_index(task)
            for step in task["steps"][start:]:
                precheck(self, step["verb"], step["parameters"])
            require(not task["governance_state"]["confirmation_required"] or confirm is True,
                    "CONFIRM_REQUIRED", "恢复前必须确认全部输出目标与字段变化", task_id=task_id)
            binding = commit.evaluate_binding(task, self.store, from_step=start + 1)
            require(binding["state"] != "rejected_state_changed", "TARGET_STATE_UNKNOWN",
                    "恢复前状态已变化；拒绝旧计划：" + "；".join(binding["reasons"]),
                    rule="version_binding_changed", changed_inputs=binding["changed_inputs"],
                    changed_targets=binding["changed_targets"])
            previous = task["steps"][start - 1]["output_ref"] if start else None
            for step in task["steps"][start:]:
                step["state"] = "DECLARED"
                step.pop("error", None)
            task.pop("error", None)
            task["resumed_from"] = start + 1
            task["resume_report"] = report
            task["governance_state"]["confirmed"] = confirm is True
            task["actual_writes"] = commit.step_writes(task)
            self._transition(task, "EXECUTING")
            return self._run_steps(task, previous, start)

    def _transition(self, task, state):
        task["task_state"], task["state_as_of"] = state, now()
        task.setdefault("events", []).append({"state": state, "at": task["state_as_of"]})
        self.store.save_task(task)

    def _precheck_verification(self, task, step, p, inputs):
        """Run the same contract predicates before any side effect, so a bad unit/object/version is named."""
        absent = commit.planned_destructions(task)
        result = verifier.run(verifier.context(
            stage="precheck", verb=step["verb"], vid=step["vid"], parameters=p, inputs=inputs,
            proposal=None, output=None, store=self.store, db=None,
            planned_inputs=[ref for ref in task["input_snapshot_refs"] if ref["id"] not in absent],
            write_scope=task.get("write_scope"),
            actual_writes=[], authorization=task["governance_state"],
            workspace=str(self.store.path.parent), file_artifact=None),
            predicates=verifier.POLICY["precheck"])
        verifier.enforce_precheck(result)
        return result

    def _execute_step(self, task, step, previous):
        p = copy.deepcopy(step["parameters"])
        spec = SPECS[step["verb"]]
        for key in spec[4]:
            if key == "inputs":
                p[key] = [previous if x == "$previous" else x for x in p[key]]
            elif p[key] == "$previous":
                p[key] = previous
        step.update(state="EXECUTING", attempt_id=identifier("attempt"), attempt_seq=step["attempt_seq"] + 1)
        self.store.save_task(task)
        with self.store.connect() as db:
            inputs = []
            for key in spec[4]:
                inputs.extend(self.store.dataset(ref, db) for ref in (p[key] if key == "inputs" else [p[key]]))
            from .pointcloud import extra_references
            inputs.extend(self.store.dataset(ref, db) for ref in extra_references(step["verb"], p))
            if step["verb"] == "gis_run_processing":
                inputs.append(self.store.dataset(p["params"]["INPUT"],db))
            if step["verb"] == "gis_run_gp":
                inputs.append(self.store.dataset(p["args"]["in_rows"], db))
            before = [self.store.verify_snapshot(d) for d in inputs]
            for key, name in step.get("head_bindings", {}).items():
                head = db.execute("SELECT dataset_id FROM dataset_heads WHERE name=?", (name,)).fetchone()
                require(head and head[0] == p[key], "TARGET_STATE_UNKNOWN", "规划后的逻辑目标已变化")
                for d in inputs:
                    if d["id"] == p[key]:
                        d["logical_name"] = name
            target = write_target(step["verb"], p)
            self._precheck_verification(task, step, p, inputs)
            if target and step["verb"] != "gis_append_dataset":
                occupied = db.execute("SELECT dataset_id FROM dataset_heads WHERE name=?", (target,)).fetchone()
                require(not occupied, "ALREADY_EXISTS", "目标在规划后已被占用；不会覆盖", target=target)
            if step["verb"] == "gis_delete_field":
                self.consume_preflight(p, inputs[0], db)
            if step["verb"] == "gis_list_algorithms":
                proposal = {"result": self.capabilities()}
            elif step["verb"] == "gis_describe_algorithm":
                candidates = [x for x in self.capabilities()["verbs"] if x["name"] == p["algorithm"]]
                require(bool(candidates), "NOT_FOUND", "能力名称不存在")
                proposal = {"result": candidates[0]}
            elif step["verb"] == "gis_delete_dataset":
                self.consume_delete_preflight(p, inputs[0], db)
                impact = self.store.destroy_dataset(inputs[0]["id"], db, task["id"])
                proposal = {"lifecycle": "destroy", "result": {"destroyed": True, "dataset": inputs[0]["id"], "before_fingerprint": before[0], "impact": impact}}
            elif step["verb"] in COMPLETION_SPECS:
                from .completion import compute as completion_compute
                proposal = completion_compute(self, step["verb"], p, inputs, db)
            else:
                proposal = compute(step["verb"], p, inputs)
            evidence_id = identifier("ev")
            output = None
            header_context = {"operation": {"verb": step["verb"], "vid": step["vid"], "parameters": p,
                                            "task_id": task["id"], "step_id": step["id"]},
                              "inputs": inputs,
                              "scope": {"workspace": str(self.store.path.parent),
                                        "authorization": task["governance_state"].get("authorization_id")}}
            readback_checks = []
            if "resource" in proposal:
                expected = None
                if proposal.get("lifecycle") == "map_update":
                    target = step["head_bindings"]["map"]
                    expected = inputs[1]["id"]
                output = self.store.insert_resource(target, proposal["resource_kind"], proposal["resource"], proposal["crs"],
                    {"kind": "derived", "task_id": task["id"], "evidence_ref": evidence_id}, db, expected=expected,
                    header_context=header_context)
                readback = self.store.dataset(output["id"], db)
                require(self.store.verify_snapshot(readback) == output["fingerprint"], "TARGET_STATE_UNKNOWN", "资源读回摘要不同")
                readback_checks = []
            elif "geojson" in proposal:
                gj, crs, schema = validate_geojson(proposal["geojson"], proposal["crs"], proposal["schema"])
                source = {"kind": "derived", "task_id": task["id"], "step_id": step["id"], "evidence_ref": evidence_id,
                          "runtime_source_digest": self.source_digest}
                if proposal.get("lifecycle") == "append":
                    output = self.store.advance_head(step["head_bindings"]["target"], inputs[1]["id"],
                                                     {"geojson": gj, "crs": crs, "schema": schema}, source, db,
                                                     header_context=header_context)
                else:
                    output = self.store.insert_dataset(target, gj, crs, schema, source, db, header_context=header_context)
                # Read the actual stored record, not the proposal, for postconditions.
                readback = self.store.dataset(output["id"], db)
                require(self.store.verify_snapshot(readback) == logical_fingerprint(gj, crs, schema),
                        "TARGET_STATE_UNKNOWN", "目标读回指纹不一致")
                readback_checks = verify_output(step["verb"], p, inputs, readback, proposal)
            elif proposal.get("lifecycle") == "rename":
                self.store.rename_head(step["head_bindings"]["dataset"], inputs[0]["id"], p["new_name"], db)
                output = self.store.dataset(p["new_name"], db)
            elif "raster" in proposal:
                from .raster import validate_raster, fingerprint
                raster, crs = validate_raster(proposal["raster"], proposal["crs"])
                output = self.store.insert_raster(target, raster, crs, {"kind": "derived", "task_id": task["id"], "evidence_ref": evidence_id}, db,
                                                  header_context=header_context)
                readback = self.store.dataset(output["id"], db)
                require(self.store.verify_snapshot(readback) == fingerprint(raster, crs), "TARGET_STATE_UNKNOWN", "栅格读回指纹不一致")
                readback_checks = []
            elif "pointcloud" in proposal:
                from .pointcloud import fingerprint
                output = self.store.insert_pointcloud(target, proposal["pointcloud"], proposal["crs"],
                                                     {"kind": "derived", "task_id": task["id"], "evidence_ref": evidence_id}, db,
                                                     header_context=header_context)
                readback = self.store.dataset(output["id"], db)
                require(self.store.verify_snapshot(readback) == fingerprint(proposal["pointcloud"], proposal["crs"]), "TARGET_STATE_UNKNOWN", "点云字节读回不一致")
                readback_checks = []
            destroyed = proposal.get("lifecycle") == "destroy"
            after = [None] if destroyed else [self.store.verify_snapshot(self.store.dataset(d["id"], db)) for d in inputs]
            require(destroyed or before == after, "TARGET_STATE_UNKNOWN", "输入在执行中发生变化")
            checks = [{"id": "explicit_destruction" if destroyed else "input_unchanged", "passed": destroyed or before == after, "method": "database_readback_absence" if destroyed else "database_readback_logical_sha256"}]
            checks.extend({"id": key, "passed": value, "method": "local_invariant"} for key, value in proposal.get("checks", {}).items())
            checks.extend(readback_checks)
            if output:
                checks.append({"id": "output_readback", "passed": True, "method": "database_readback_logical_sha256"})
            require(all(c["passed"] for c in checks), "BACKEND_ERROR", "后置条件核验未通过")
            actual_writes = ([{"kind": "dataset_destroy", "target": inputs[0]["id"]}] if destroyed
                             else [{"kind": "dataset", "target": commit.logical_target(step)}] if output else [])
            verification = verifier.run(verifier.context(
                stage="commit", verb=step["verb"], vid=step["vid"], parameters=p, inputs=inputs,
                proposal=proposal, output=output, store=self.store, db=db,
                planned_inputs=[ref for ref in task["input_snapshot_refs"]
                                if ref["id"] not in commit.planned_destructions(task)],
                write_scope=task.get("write_scope"),
                actual_writes=actual_writes, authorization=task["governance_state"],
                workspace=str(self.store.path.parent), file_artifact=None))
            verifier.enforce(verification)
            if any(k in proposal for k in ("raster", "pointcloud", "resource")) or proposal.get("lifecycle") in ("destroy", "rename"):
                checks.extend(verifier.legacy_checks(verification))
            task.setdefault("actual_writes", []).extend(actual_writes)
            evidence = {"id": evidence_id, "task_id": task["id"], "step_id": step["id"], "attempt_id": step["attempt_id"],
                        "created_at": now(), "verb": step["verb"], "vid": step["vid"], "level": step["level"],
                        "backend": "local-geos", "implementation_route": "local_geos_gdal", "versions": self.versions,
                        "requested_parameters": step["parameters"], "effective_parameters": p,
                        "parameter_rewrites": [{"from": "$previous", "to": previous, "reason": "declared_task_reference"}] if "$previous" in canonical(step["parameters"]) else [],
                        "input_fingerprints": [{"id": d["id"], "before": b, "after": a} for d, b, a in zip(inputs, before, after)],
                        "input_unchanged": before == after, "output_ref": output["id"] if output else None,
                        "output_fingerprint": output["fingerprint"] if output else None,
                        "input_feature_count": sum(d["feature_count"] for d in inputs),
                        "output_feature_count": output["feature_count"] if output else None,
                        "processed_feature_count": sum(d["feature_count"] for d in inputs),
                        "result_empty": (output["feature_count"] == 0 or output["empty_geometry_count"] == output["feature_count"]) if output else None,
                        "empty_reason": ("input_empty" if all(d["feature_count"] == 0 for d in inputs) else "operation_result_empty") if output and (output["feature_count"] == 0 or output["empty_geometry_count"] == output["feature_count"]) else None,
                        "postcondition_checks": checks, "verification": verification, "governance_state": task["governance_state"],
                        "atomicity": "external_service_nontransactional; isolate_on_unknown" if step["verb"] == "gis_publish_layer" else "sqlite_transaction_per_step", "target_state": "destroyed" if destroyed else "external_published" if step["verb"] == "gis_publish_layer" else "committed" if output else "read_only",
                        "certification": "NOT_CERTIFIED", "observation_ref": None,
                        "verification_limits": ["Readback checks are not an independent geometry oracle", "No cross-backend equivalence or calibrated tolerances claimed"],
                        **{k: v for k, v in proposal.items() if k not in ("geojson", "raster", "pointcloud", "resource", "schema", "crs", "checks")}}
            if output is not None:
                evidence["semantic"] = output.get("semantic")
            elif step["level"] == "R0" and isinstance(proposal.get("result"), dict) and step["verb"] not in ("gis_list_algorithms", "gis_describe_algorithm"):
                from .semantics import result_header
                evidence["semantic"] = result_header(
                    verb=step["verb"], vid=step["vid"], parameters=p, result=proposal["result"], inputs=inputs,
                    task_id=task["id"], step_id=step["id"], evidence_ref=evidence_id,
                    result_index=step["index"] - 1, workspace=str(self.store.path.parent),
                    authorization=task["governance_state"].get("authorization_id"))
            evidence["evidence_digest"] = digest(evidence)
            self.store.save_evidence(evidence, db)
            step.update(state="COMMITTED", output_ref=evidence["output_ref"], evidence_ref=evidence_id,
                        result=evidence.get("result"), execution_started=output is not None or destroyed or step["verb"] == "gis_publish_layer",
                        checkpoint=resume.checkpoint(step, inputs=inputs, output=output, evidence_ref=evidence_id, task=task))
            task["execution_started"] |= step["execution_started"]
            if destroyed:
                task.setdefault("destroyed_inputs", []).append({"id": inputs[0]["id"], "fingerprint": before[0], "evidence_ref": evidence_id})
            task["evidence_refs"].append(evidence_id)
            if output:
                task["output_refs"].append(output["id"])
            self.store.save_task(task, db)
            return output

    def field_preflight(self, dataset, fields, target):
        import time
        from .vector_extra import compute as vector_compute
        from .model import name_ok
        name_ok(target)
        d = self.store.dataset(dataset)
        self.store.verify_snapshot(d)
        # Exercise the actual schema projection without writing a dataset.
        vector_compute("gis_delete_field", {"fields": fields, "target": target, "target_echo": target}, [d])
        token = identifier("pf")
        binding = digest({"dataset": d["id"], "fingerprint": d["fingerprint"], "fields": fields, "target": target})
        with self.store.connect() as db:
            require(not db.execute("SELECT 1 FROM dataset_heads WHERE name=?", (target,)).fetchone(), "ALREADY_EXISTS", "目标已存在")
            db.execute("INSERT INTO local_preflights VALUES (?,?,?,0)", (token, binding, time.time() + 300))
        return {"preflight_id": token, "target_echo": target, "removed_fields": fields,
                "expires_in_seconds": 300, "single_use": True, "dependency_scope": "flat local scalar fields; no declared business keys"}

    def delete_preflight(self, dataset):
        import time
        with self.store.lock, self.store.connect() as db:
            d = self.store.dataset(dataset, db)
            self.store.verify_snapshot(d)
            require(d.get("kind", "vector") in ("vector", "raster", "pointcloud"), "NOT_SUPPORTED", "销毁首版只接受独立数据快照")
            impact = self.store.deletion_impact(d["id"], db)
            require(not impact["dependent_objects"], "DEPENDENCY_BLOCKED", "存在持久依赖；不会级联销毁", impact=impact)
            token = identifier("pf")
            binding = digest({"operation": "delete_dataset", "dataset": d["id"], "fingerprint": d["fingerprint"], "impact": impact})
            db.execute("INSERT INTO local_preflights VALUES (?,?,?,0)", (token, binding, time.time() + 300))
        return {"preflight_id": token, "target_echo": d["id"], "fingerprint": d["fingerprint"], "impact": impact, "expires_in_seconds": 300, "single_use": True, "destruction": "physical dataset row and active name removal; no hidden recoverable copy"}

    def consume_delete_preflight(self, p, d, db):
        import time
        impact = self.store.deletion_impact(d["id"], db, exclude_task=None)
        # The task that is about to delete the dataset is itself an expected
        # reference; other declared tasks still block deletion in destroy_dataset.
        impact["dependent_objects"] = []
        binding = digest({"operation": "delete_dataset", "dataset": d["id"], "fingerprint": d["fingerprint"], "impact": impact})
        row = db.execute("SELECT * FROM local_preflights WHERE id=?", (p["preflight_id"],)).fetchone()
        require(p["target_echo"] == d["id"] and row and row["binding"] == binding and not row["used"] and row["expires"] > time.time(), "PREFLIGHT_REQUIRED", "销毁目标回显或单次预检不匹配/已过期")
        db.execute("UPDATE local_preflights SET used=1 WHERE id=?", (p["preflight_id"],))

    @staticmethod
    def consume_preflight(p, d, db):
        import time
        row = db.execute("SELECT * FROM local_preflights WHERE id=?", (p["preflight_id"],)).fetchone()
        binding = digest({"dataset": d["id"], "fingerprint": d["fingerprint"], "fields": p["fields"], "target": p["target"]})
        require(row and row["binding"] == binding and row["used"] == 0 and row["expires"] > time.time(),
                "PREFLIGHT_REQUIRED", "预检不存在、已用、过期或与参数/输入不匹配")
        db.execute("UPDATE local_preflights SET used=1 WHERE id=?", (p["preflight_id"],))

    def evidence_bundle(self, task_id):
        task = self.store.task(task_id)
        evidence = [self.store.evidence(ref) for ref in task["evidence_refs"]]
        datasets = [self.store.dataset(ref) for ref in task["output_refs"]]
        payload = {"format": "definitio-evidence-bundle-v1", "task": task, "evidence": evidence, "outputs": datasets,
                   "inputs": [self.store.dataset(ref["id"]) for ref in task["input_snapshot_refs"] if ref["id"] not in {d["id"] for d in task.get("destroyed_inputs", [])}],
                   "destroyed_inputs": task.get("destroyed_inputs", []),
                   "verification_limits": "Self-contained local execution evidence; not G1 acceptance or backend certification"}
        return {**payload, "bundle_sha256": digest(payload)}
