"""Agent-facing GIS service: discover, call, inspect and recover without a UI.

The HTTP engine is the sole owner of execution. MCP clients share this service
rather than opening independent runtimes against the same task database.
"""
from __future__ import annotations

import copy
import base64
import json
from urllib.parse import unquote, urlparse

from .model import GISFault, canonical, digest, require, validate_geojson
from .operations import SPECS
from .schemas import CRS, FIELD, FIELD_NAMES, choice, operation_schema, text, validate
from .authorization import Authorizations
from .guidance import FAMILIES, family_for, prerequisites, route
from .preview import preview, value_summary
from .vector_extra import write_target

INTERFACE = "definitio-agent-v1"
MAX_REPLY_CHARS = 48000
PAGE = {"offset": {"type": "integer", "minimum": 0, "default": 0},
        "limit": {"type": "integer", "minimum": 1, "maximum": 100, "default": 20}}
REQUEST_ID = text("Stable caller-generated idempotency key. Retry the SAME request with the SAME key after transport loss; do not generate a new key blindly.", maxLength=96)
OUTPUT_SCHEMA = {"type": "object", "properties": {
    "ok": {"type": "boolean"}, "status": choice(["success", "error"], "Tool execution outcome; an honestly reported invalid geometry is a successful validation result"),
    "interface_version": {"const": INTERFACE}, "data": {"type": "object"},
    "error": {"type": ["object", "null"]}, "next_actions": {"type": "array", "items": {"type": "object"}},
    "certification": {"const": "NOT_CERTIFIED"}},
    "required": ["ok", "status", "interface_version", "data", "error", "next_actions", "certification"],
    "additionalProperties": False}


def obj(properties=None, required=()):
    return {"type": "object", "properties": properties or {}, "required": list(required), "additionalProperties": False}


def control_definitions():
    step_schema = obj({"verb": choice([v for v in SPECS if v not in ("gis_list_algorithms", "gis_describe_algorithm")], "Exact implemented verb"),
                       "parameters": {"type": "object", "description": "Operation arguments only: no request_id or authorization_id. Reference the immediately preceding dataset with $previous, never its new target name. See gis_describe_algorithm.plan_step."}},
                      ["verb", "parameters"])
    steps = {"type": "array", "items": step_schema, "minItems": 1, "maxItems": 6}
    geojson = {"type": "object", "properties": {"type": {"const": "FeatureCollection"},
               "features": {"type": "array", "maxItems": 10000, "items": {"type": "object"}}},
               "required": ["type", "features"]}
    return {
        "workspace_resource": ("Register an immutable map, license document or catalog record. Map creates a real portable QGIS project; license retains supplied document provenance (rights not independently authenticated); catalog binds that evidence to a source fingerprint.",
                               obj({"kind": choice(["map", "license", "catalog"], "Managed resource kind"), "name": text("New managed name", maxLength=64),
                                    "properties": {"type": "object", "description": "map: crs,extent[xmin,ymin,xmax,ymax],width,height,purpose; license: dataset,document,commercial_use,issuer; catalog: dataset,evidence_ref"},
                                    "confirm": {"type": "boolean"}, "request_id": REQUEST_ID}, ["kind", "name", "properties", "request_id"]), False),
        "pointcloud_import": ("Import fixed LAS/LAZ bytes (base64), validate with installed PDAL, and retain an immutable snapshot. No arbitrary file paths or URLs.",
                              obj({"name": text("New dataset name", maxLength=64),
                                   "base64": text("Fixed LAS/LAZ file bytes", maxLength=8*1024*1024),
                                   "format": choice(["las", "laz"], "Exact source encoding"),
                                   "confirm": {"type": "boolean"}, "request_id": REQUEST_ID}, ["name", "base64", "format", "request_id"]), False),
        "raster_import": ("Import a bounded single-band raster snapshot from inline JSON with explicit affine grid, pixel type, NoData and CRS. No paths, URLs or resampling.",
                          obj({"name": text("New dataset name", maxLength=64), "raster": {"type": "object"}, "crs": CRS,
                               "confirm": {"type": "boolean"}, "request_id": REQUEST_ID}, ["name", "raster", "crs", "request_id"]), False),
        "destructive_preflight": ("Inspect field-copy removal or deletion of an unreferenced dataset; returns a five-minute single-use fingerprint-bound token. No deletion occurs here.",
                                  obj({"dataset": text("Dataset ID"), "fields": FIELD_NAMES,
                                       "target": text("New output name", maxLength=64), "operation": choice(["delete_field", "delete_dataset"], "Default delete_field; deletion requires dataset only")}, ["dataset"]), False),
        "workspace_list": ("List immutable dataset handles and schema summaries. Bounded pagination; no GUI or filesystem discovery needed.",
                           obj({**PAGE, "name_contains": text("Case-insensitive substring of dataset name", maxLength=64),
                                "fields": FIELD_NAMES, "crs": CRS,
                                "geometry_type": choice(["Point", "MultiPoint", "LineString", "MultiLineString", "Polygon", "MultiPolygon", "GeometryCollection"], "Required geometry type")}), True),
        "dataset_import": ("Import explicit 2D GeoJSON with CRS into a new immutable dataset. Requires confirm=true and a stable request_id. Does not read arbitrary paths or fetch URLs.",
                           obj({"name": text("Exact new dataset name", maxLength=64), "geojson": geojson, "crs": CRS,
                                "schema": {"type": "array", "items": FIELD, "maxItems": 64},
                                "confirm": {"type": "boolean"}, "request_id": REQUEST_ID,
                                "source_kind": choice(["agent_import", "synthetic_fixture"], "Truthful source category")},
                               ["name", "geojson", "crs", "request_id"]), False),
        "dataset_describe": ("Get one dataset's immutable handle, fingerprint, CRS, geometry types and exact field schema before choosing an operation.",
                             obj({"dataset": text("Dataset ID or exact name")}, ["dataset"]), True),
        "dataset_features": ("Read a bounded page of records. Geometry is omitted by default; geometry_ref can be read in bounded chunks using artifact_read. No implicit truncation of records.",
                             obj({"dataset": text("Dataset ID or exact name"), **PAGE, "fields": FIELD_NAMES,
                                  "include_geometry": {"type": "boolean", "default": False}}, ["dataset"]), True),
        "task_plan": ("Declare an explicit ordered GIS verb chain, without executing it. Use $previous for the immediately preceding dataset output. Returns the exact plan, its digest and field-drop effects for authorization. exports binds staged file artifacts into the plan's declared write scope.",
                      obj({"goal": text("Task purpose", maxLength=1000), "steps": steps,
                           "exports": {"type": "array", "maxItems": 6, "description": "Staged file artifacts to publish on commit; each is bound into the plan's write scope",
                                       "items": obj({"step": {"type": "integer", "minimum": 1, "maximum": 6, "description": "1-based step whose dataset output is published"},
                                                     "name": text("New file name ending in .geojson; no path separators", maxLength=64),
                                                     "format": choice(["geojson"], "Published file encoding")}, ["step", "name", "format"])},
                           "request_id": REQUEST_ID}, ["goal", "steps", "request_id"]), False),
        "task_execute": ("Execute a stored plan only with its exact plan_digest and explicit write confirmation. Repeating a committed task returns existing outputs. FAILED/ISOLATED tasks are never blindly replayed. resume=true continues a FAILED plan from its still-valid committed results after re-checking every dependency version.",
                         obj({"task_id": text("Task ID from task_plan"), "plan_digest": text("Exact plan digest", maxLength=64),
                              "confirm": {"type": "boolean"}, "authorization_id": text("Session write grant ID"),
                              "resume": {"type": "boolean", "default": False, "description": "Continue a FAILED plan from still-valid committed steps; refused if any dependency version changed"}}, ["task_id", "plan_digest"]), False),
        "task_get": ("Recover task status, per-step outcome and immutable output/evidence handles after interruption or lost responses. Full records/results are available through artifact_read URIs. verify=true re-runs the same verifier the commit path uses and returns checked/found/unconfirmed.",
                     obj({"task_id": text("Task ID"),
                          "verify": {"type": "boolean", "default": False, "description": "Re-run the single verifier against committed steps; never a blanket verified flag"}}, ["task_id"]), True),
        "evidence_get": ("Read evidence summary or bounded pages of checks/group manifests. Hashes and readback checks are not independent geometry certification.",
                         obj({"evidence_id": text("Evidence ID"), "section": choice(["summary", "checks", "groups", "parameters"], "Section, default summary"), **PAGE}, ["evidence_id"]), True),
        "artifact_read": ("Read an immutable definitio:// artifact in exact character chunks. Concatenate content in offset order and verify sha256_json_utf8. Never reads a disk path or external URI.",
                          obj({"uri": text("definitio:// URI returned by this service", maxLength=512),
                               "offset": {"type": "integer", "minimum": 0, "default": 0},
                               "max_chars": {"type": "integer", "minimum": 1, "maximum": 8192, "default": 4096}}, ["uri"]), True),
        "workflow_route": ("Find verb families and distinctions for an intent. Deterministic keyword guide; unknown intents return all families. Use before scanning the full catalog.",
                           obj({"intent": text("GIS intent in English or Chinese", maxLength=1000)}, ["intent"]), True),
        "gis_call": ("Run an exact implemented GIS verb through compact discovery. First get its precise input schema/prerequisites with gis_describe_algorithm. Arguments are validated against that same schema; write authorization still enforced.",
                     obj({"verb": choice([v for v in SPECS if v not in ("gis_list_algorithms", "gis_describe_algorithm")], "Implemented GIS operation"),
                          "arguments": {"type": "object", "description": "Exact published verb arguments including explicit or scoped authorization"}}, ["verb", "arguments"]), False),
        "task_dry_run": ("Execute an explicit chain entirely in memory. No datasets, tasks, evidence, requests or quota are written. Returns every step's actual schema/CRS/count/effects or a failure with downstream BLOCKED.",
                         obj({"goal": text("Preview purpose", maxLength=1000), "steps": steps}, ["goal", "steps"]), True),
        "artifact_view": ("Default summary or structured JSON-pointer drill-down. Cursor handles pagination; server verifies dataset fingerprints. No JSON text concatenation needed. Full artifact_read remains available.",
                         obj({"uri": text("Immutable definitio:// URI", maxLength=512), "pointer": {"type": "string", "maxLength": 1024, "default": ""},
                              "cursor": text("Opaque continuation from prior artifact_view", maxLength=4096),
                              "limit": PAGE["limit"], "view": choice(["summary", "page"], "Default summary; page reads values")}, ["uri"]), True),
        "authorization_grant": ("After host/user approval, grant this session bounded writes to new names under a prefix, specific verbs, count and TTL. confirm=true is required once; never a global bypass.",
                                obj({"target_prefix": text("Literal nonempty NEW dataset name prefix", maxLength=64),
                                     "verbs": {"type": "array", "items": choice(["dataset_import", "raster_import"] + [v for v, s in SPECS.items() if s[2] != "R0"], "Allowed write verb"), "minItems": 1, "uniqueItems": True},
                                     "max_writes": {"type": "integer", "minimum": 1, "maximum": 100},
                                     "ttl_seconds": {"type": "integer", "minimum": 1, "maximum": 3600},
                                     "confirm": {"type": "boolean"}, "request_id": REQUEST_ID},
                                    ["target_prefix", "verbs", "max_writes", "ttl_seconds", "request_id"]), False),
        "authorization_get": ("Inspect this session's grant, remaining reservations and recent audit events.",
                              obj({"authorization_id": text("Session grant ID")}, ["authorization_id"]), True),
        "authorization_revoke": ("Immediately revoke this session's grant for new writes. Prior accepted receipts remain recoverable.",
                                 obj({"authorization_id": text("Session grant ID")}, ["authorization_id"]), False),
    }


def tool_definitions():
    tools = []
    descriptions = {
        "gis_dissolve": "Projected CRS + 2D Polygon/MultiPolygon only. fields is REQUIRED: [] dissolves all records, otherwise one MultiPolygon per exact typed group; null is a separate key. Empty groups remain. Output keeps ONLY fields: review dropped fields/business keys before confirm=true. Float grouping is unsupported pending D06.",
        "gis_buffer": "Projected planar buffer in explicit metres. round caps/joins and 8 segments per quadrant. Geographic CRS and zero-distance provisional cases are rejected. dissolve=true drops attributes; false retains input records.",
        "gis_clip": "Clip polygons by an explicit polygon dataset in the same CRS. No implicit reprojection or repair; a legitimate empty intersection is success with zero rows.",
        "gis_reproject": "Explicit 2D vector coordinate transformation using GDAL/PROJ. Not a CRS metadata relabel. Transformation ambiguity is not independently calibrated.",
        "gis_assign_projection": "Change CRS metadata on a NEW copy without moving coordinates. Never use as a substitute for reproject.",
        "gis_field_statistics": "Numeric statistics with explicit null_policy=exclude; null_count is always reported. Large grouped results are retained behind a paged artifact URI.",
        "gis_list_algorithms": "Discover all 40 known GIS verbs, with local implementation status, data domain and how to obtain each parameter schema. Unsupported verbs are explicit; no backend evidence status is upgraded.",
        "gis_describe_algorithm": "Return one known GIS verb's complete machine schema, examples, governance and local limitations. Use before calling if any parameter meaning is uncertain.",
    }
    for verb, spec in SPECS.items():
        schema = operation_schema(verb)
        schema["properties"]["request_id"] = REQUEST_ID
        if spec[2] != "R0":
            schema["properties"]["authorization_id"] = text("Session grant; permits automatic stable request_id when omitted")
            schema["anyOf"] = [{"required": ["request_id"]}, {"required": ["authorization_id"]}]
        description = descriptions.get(verb, spec[1] + ". Explicit local route. Inspect input schema and capability scope first.")
        if spec[2] != "R0":
            from .completion import EXPLICIT_ONLY
            description += (" Explicit dataset destruction: confirm=true + request_id + exact target_echo + single-use preflight; referenced objects rejected."
                            if verb == "gis_delete_dataset" else
                            " Explicit confirm=true + request_id required; scoped new-name grants do not apply. Inspect the native route and its declared effects."
                            if verb in EXPLICIT_ONLY else
                            " Logical lifecycle change: explicit confirm=true + request_id; old snapshots retained."
                            if verb in ("gis_rename_dataset", "gis_append_dataset") else
                            " Copy-on-write: confirm=true + request_id OR session authorization_id. Retries keep identical arguments; new targets never overwritten.")
        description += " Prerequisites: " + "; ".join(prerequisites(verb)) + "."
        tools.append({"name": verb, "title": spec[1], "description": description, "inputSchema": schema,
                      "outputSchema": OUTPUT_SCHEMA, "annotations": {"readOnlyHint": spec[2] == "R0", "destructiveHint": spec[2] == "R3",
                      "idempotentHint": True, "openWorldHint": False}})
    for name, (description, schema, read_only) in control_definitions().items():
        if name == "dataset_import":
            schema["properties"]["authorization_id"] = text("Session grant; permits automatic stable request_id")
            schema["required"].remove("request_id")
            schema["anyOf"] = [{"required": ["request_id"]}, {"required": ["authorization_id"]}]
        tools.append({"name": name, "title": name, "description": "[Control plane; not an additional GIS verb] " + description,
                      "inputSchema": schema, "outputSchema": OUTPUT_SCHEMA,
                      "annotations": {"readOnlyHint": read_only, "destructiveHint": False, "idempotentHint": True, "openWorldHint": False}})
    return tools


def compact_tools(tools):
    return [t for t in tools if not t["name"].startswith("gis_") or t["name"] in ("gis_call", "gis_describe_algorithm")]


def success(data, next_actions=None):
    return {"ok": True, "status": "success", "interface_version": INTERFACE, "data": data, "error": None,
            "next_actions": next_actions or [], "certification": "NOT_CERTIFIED"}


RECOVERY = {
    "CONFIRM_REQUIRED": ("obtain_authorization", False, "Obtain host/user authorization for the exact target and field effects; only then resubmit with confirm=true."),
    "INVALID_ARGUMENT": ("correct_arguments", True, "Read the published schema, correct the reported JSON paths. A reused request_id with different arguments requires a NEW key after reviewing prior status."),
    "NOT_FOUND": ("discover_inputs", True, "Call workspace_list; use an actual dataset/task/evidence handle."),
    "ALREADY_EXISTS": ("inspect_existing_target", False, "Inspect the existing target. Check prior request/task status; choose a new explicit target only for a genuinely new operation."),
    "SCHEMA_MISMATCH": ("inspect_schema", True, "Read dataset_describe and provide exact, compatible field names/types; do not silently coerce."),
    "CRS_MISMATCH": ("explicit_reprojection", True, "Inspect both CRS definitions and explicitly reproject a new copy to a common CRS before analysis."),
    "UNKNOWN_CRS": ("supply_crs", True, "Provide the known CRS definition; do not guess EPSG:4326."),
    "NOT_SUPPORTED": ("inspect_capabilities", False, "Use gis_describe_algorithm to inspect the declared domain; no silent fallback to another backend."),
    "GEOMETRY_INVALID": ("inspect_geometry", True, "Run read-only gis_validate_geometry, identify offending records; do not silently repair or drop them."),
    "TARGET_STATE_UNKNOWN": ("inspect_task_and_evidence", False, "STOP automatic retries. Inspect task_get and evidence; target state is not proven safe for replay."),
    "BACKEND_ERROR": ("inspect_failure", False, "Read task_get and its evidence. Current-step rollback does not erase previously committed outputs in a partial workflow."),
    "AUTHORIZATION_REQUIRED": ("obtain_authorization", False, "No session grant covers this write. Obtain host/user approval, then authorization_grant or per-call confirm=true with a stable request_id."),
    "AUTHORIZATION_SCOPE": ("inspect_authorization_scope", False, "The write left the granted prefix/verb scope. Inspect authorization_get; never widen a grant implicitly."),
    "AUTHORIZATION_EXHAUSTED": ("inspect_authorization_scope", False, "The grant's write reservation is used up. Inspect authorization_get and request a new explicitly approved grant."),
    "LICENSE_BLOCKED": ("inspect_source_license", False, "The source did not attest allow_import with an evidence_ref. Supply explicit rights evidence; no license is inferred."),
    "DEPENDENCY_BLOCKED": ("inspect_dependencies", False, "Other objects still reference this dataset. Inspect the reported impact; destruction never cascades."),
    "PREFLIGHT_REQUIRED": ("obtain_destructive_preflight", False, "Destructive steps need a fresh single-use destructive_preflight token bound to the exact target and fingerprint."),
    "BACKEND_UNAVAILABLE": ("inspect_environment", False, "Run scripts/environment_doctor.py; the declared native backend is not configured here. No silent fallback."),
    "DESTRUCTIVE_BLOCKED": ("inspect_capabilities", False, "The named destructive route is not opened on this engine. Inspect gis_describe_algorithm prerequisites."),
    "DEFER_BLOCKED": ("inspect_capabilities", False, "The deferred route is not opened on this engine. Inspect gis_describe_algorithm prerequisites."),
    "DIMENSION_MISMATCH": ("inspect_geometry", True, "Inputs disagree on dimensionality or geometry type. Inspect dataset_describe geometry_types; only 2D is accepted."),
}


def failure(fault, data=None, tool=None):
    code = fault.code
    actions = []
    action, retry, message = RECOVERY.get(code, ("inspect_failure", False, "Inspect the explicit error and operation scope before taking another action."))
    error = {"code": code, "message": fault.message, "details": fault.details,
             "retry_safe_after_correction": retry, "recovery": action, "recovery_message": message,
             "execution_started": bool((data or {}).get("task", {}).get("execution_started", False))}
    if code == "NOT_FOUND":
        actions.append({"tool": "workspace_list", "arguments": {}, "reason": message})
    if code == "CONFIRM_REQUIRED" and tool:
        actions.append({"tool": tool, "arguments_patch": {"confirm": True}, "requires_authorization": True, "reason": message})
    return {"ok": False, "status": "error", "interface_version": INTERFACE, "data": data or {}, "error": error,
            "next_actions": actions, "certification": "NOT_CERTIFIED"}


class AgentService:
    def __init__(self, runtime):
        self.runtime, self.store = runtime, runtime.store
        self.authorizations = Authorizations(self.store)
        self.tools = {t["name"]: t for t in tool_definitions()}
        with self.store.connect() as db:
            db.execute("CREATE TABLE IF NOT EXISTS agent_requests (request_id TEXT PRIMARY KEY, request_digest TEXT NOT NULL, kind TEXT NOT NULL, reference TEXT NOT NULL)")

    def call(self, name, arguments, session=None):
        try:
            require(name in self.tools, "NOT_SUPPORTED", "未开放的 Agent 工具", tool=name)
            validate(self.tools[name]["inputSchema"], arguments)
            if name == "gis_call":
                return self.call(arguments["verb"], arguments["arguments"], session)
            arguments = copy.deepcopy(arguments)
            if arguments.get("authorization_id") and "request_id" not in arguments and (name == "dataset_import" or name in SPECS and SPECS[name][2] != "R0"):
                arguments["request_id"] = "auto_" + digest({"session": self.authorizations.session_hash(session),
                                                          "tool": name, "arguments": arguments})
            with self.store.lock:
                if name in SPECS:
                    result = self._operation(name, arguments, session)
                else:
                    result = self._control(name, arguments, session)
            if arguments.get("request_id"):
                result["data"]["request_id"] = arguments["request_id"]
            if len(canonical(result)) > MAX_REPLY_CHARS:
                # Never turn an already committed write into a fake argument
                # failure merely because its metadata exceeds the reply budget.
                data = result["data"]
                if "task" in data:
                    result["data"] = {"task": data["task"], "record_uri": data["task"]["record_uri"],
                                      "message": "Exact plan/results and input handles are retained in the task artifact; use artifact_read before authorization or further processing."}
                elif "dataset" in data:
                    d = data["dataset"]
                    result["data"] = {"dataset": {k: d[k] for k in ("id", "name", "fingerprint", "feature_count", "resource_uri", "metadata_uri", "geojson_uri")},
                                      "message": "Full schema metadata is retained at metadata_uri; use artifact_read."}
                else:
                    raise GISFault("INVALID_ARGUMENT", "请求的只读视图过大；请减小 limit 或使用 artifact_read 分块读取")
            validate(OUTPUT_SCHEMA, result)
            return result
        except GISFault as exc:
            return failure(exc, tool=name)
        except Exception as exc:
            return failure(GISFault("BACKEND_ERROR", "Agent 接口处理失败；请检查任务状态后再决定是否重试", reason=type(exc).__name__), tool=name)

    def _existing(self, request_id, request_digest):
        if not request_id:
            return None
        with self.store.connect() as db:
            row = db.execute("SELECT * FROM agent_requests WHERE request_id=?", (request_id,)).fetchone()
        if row:
            require(row["request_digest"] == request_digest, message="request_id 已绑定不同请求；禁止以旧键执行新操作", request_id=request_id, existing_reference=row["reference"])
            return dict(row)
        return None

    @staticmethod
    def _save_request(db, request_id, request_digest, kind, reference):
        if request_id:
            db.execute("INSERT INTO agent_requests VALUES (?,?,?,?)", (request_id, request_digest, kind, reference))

    def _task_request(self, request_id, request_digest, goal, steps, exports=None):
        old = self._existing(request_id, request_digest)
        if old:
            return self.store.task(old["reference"]), True
        task = self.runtime.declare(goal, steps, exports=exports)
        with self.store.connect() as db:
            self._save_request(db, request_id, request_digest, "task", task["id"])
        # Reservation is durable BEFORE execution. Lost responses cannot cause a
        # second committed output when the same caller key is submitted again.
        return task, False

    def descriptor(self, d):
        result = {"id": d["id"], "name": d.get("logical_name", d["name"]), "fingerprint": d["fingerprint"], "feature_count": d["feature_count"],
                "fields": d["fields"], "geometry_types": d["geometry_types"], "bounds": d["bounds"],
                "crs": {k: d["crs"][k] for k in ("code", "definition_hash", "projected", "unit", "metres_per_unit", "coordinate_order", "axis_order")},
                "source": d["source"], "semantic": d.get("semantic"), "resource_uri": f"definitio://datasets/{d['id']}",
                "geojson_uri": f"definitio://datasets/{d['id']}/geojson", "metadata_uri": f"definitio://datasets/{d['id']}/metadata"}
        if d.get("kind") == "raster":
            result.update(kind="raster", grid=d["grid"], raster_uri=f"definitio://datasets/{d['id']}/raster")
            result.pop("geojson_uri")
        if d.get("kind") == "pointcloud":
            result.update(kind="pointcloud", point_count=d["point_count"], point_dimensions=d["point_dimensions"],
                          pointcloud_uri=f"definitio://datasets/{d['id']}/pointcloud")
            result.pop("geojson_uri")
        if "resource" in d:
            result.update(kind=d["kind"], export_uri=f"/api/datasets/{d['id']}/export")
            result.pop("geojson_uri")
        return result

    def task_summary(self, t):
        return {"id": t["id"], "goal": t["goal"], "task_state": t["task_state"], "plan_digest": t["plan_digest"],
                "completion": t.get("completion"), "execution_started": t["execution_started"],
                "confirmation_required": t["governance_state"]["confirmation_required"],
                "output_refs": t["output_refs"], "evidence_refs": t["evidence_refs"],
                "commit_state": t.get("commit_state"), "file_artifact_refs": t.get("file_artifact_refs", []),
                "readback": t.get("readback"), "resumed_from": t.get("resumed_from"),
                "versions": t["versions"], "record_uri": f"definitio://tasks/{t['id']}/record",
                "steps": [{k: s.get(k) for k in ("id", "index", "verb", "vid", "level", "state", "output_ref", "evidence_ref")}
                          for s in t["steps"]]}

    def _task_reply(self, task, replayed=False):
        data = {"task": self.task_summary(task), "replayed": replayed,
                "outputs": [{k: d[k] for k in ("id", "name", "fingerprint", "feature_count", "crs", "resource_uri", "metadata_uri", "geojson_uri", "raster_uri", "pointcloud_uri", "export_uri", "point_count", "kind", "grid") if k in d}
                            for d in (self.descriptor(self.store.dataset(ref)) for ref in task["output_refs"])],
                "results": []}
        for step in task["steps"]:
            if step.get("result") is not None:
                result = step["result"]
                item = {"step_id": step["id"], "uri": f"definitio://tasks/{task['id']}/results/{step['index'] - 1}"}
                if len(canonical(result)) <= 3000:
                    item["value"] = result
                else:
                    item["message"] = "Result retained as an artifact; use artifact_read. No records were discarded."
                    item["summary"] = value_summary(result)
                    item["drill_down"] = {"tool": "artifact_view", "arguments": {"uri": item["uri"]}}
                    if isinstance(result, dict) and "feature_count" in result:
                        item["feature_count"] = result["feature_count"]
                data["results"].append(item)
        if task["task_state"] in ("FAILED", "ISOLATED"):
            error = task["error"]
            return failure(GISFault(error["code"], error["message"], **error.get("details", {})), data)
        return success(data, [{"tool": "task_get", "arguments": {"task_id": task["id"]}, "reason": "Persistent status and output handles"}])

    def verify_task(self, task):
        """Agent-initiated verify path: the very same predicates the commit path must call."""
        from . import verifier
        from .commit import planned_destructions
        from .pointcloud import extra_references
        absent = planned_destructions(task)
        planned = [ref for ref in task["input_snapshot_refs"] if ref["id"] not in absent]
        steps = []
        for step in task["steps"]:
            if step["state"] != "COMMITTED" or step["verb"] == "gis_delete_dataset":
                continue
            evidence = self.store.evidence(step["evidence_ref"])
            p = evidence.get("effective_parameters", step["parameters"])
            spec = SPECS[step["verb"]]
            inputs = []
            for key in spec[4]:
                inputs.extend(self.store.dataset(ref) for ref in (p[key] if key == "inputs" else [p[key]]))
            inputs.extend(self.store.dataset(ref) for ref in extra_references(step["verb"], p))
            if step["verb"] == "gis_run_processing":
                inputs.append(self.store.dataset(p["params"]["INPUT"]))
            if step["verb"] == "gis_run_gp":
                inputs.append(self.store.dataset(p["args"]["in_rows"]))
            output = self.store.dataset(step["output_ref"]) if step.get("output_ref") else None
            result = verifier.run(verifier.context(
                stage="verify", verb=step["verb"], vid=step["vid"], parameters=p, inputs=inputs,
                proposal=None, output=output, store=self.store, db=None,
                planned_inputs=planned, write_scope=task.get("write_scope"),
                actual_writes=task.get("actual_writes", []), authorization=task["governance_state"],
                workspace=str(self.store.path.parent), file_artifact=None))
            steps.append({"step": step["index"], "step_id": step["id"], "evidence_ref": step["evidence_ref"], **result})
        return {"format": verifier.VERIFICATION_FORMAT, "task_id": task["id"], "steps": steps,
                "ok": all(item["ok"] for item in steps),
                "predicates_digest": digest([predicate.__name__ for predicate in verifier.PREDICATES]),
                "limits": ["Re-verification reads current persisted state; it is not an independent geometry oracle",
                           "Operator-level invariants were judged at commit time; their receipts are in evidence.postcondition_checks"]}

    def _operation(self, name, arguments, session=None):
        from .completion import EXPLICIT_ONLY, precheck
        precheck(self.runtime, name, arguments)
        if name in EXPLICIT_ONLY | {"gis_rename_dataset", "gis_append_dataset"}:
            require(not arguments.get("authorization_id"), "CONFIRM_REQUIRED", "现有逻辑对象变更须逐次显式确认；新目标前缀授权不适用")
        if name == "gis_list_algorithms":
            verbs = [v for v in self.runtime.capabilities()["verbs"]
                     if (not arguments.get("implemented_only") or v["implemented"])
                     and (not arguments.get("family") or v["family"] == arguments["family"])
                     and (not arguments.get("query") or arguments["query"].casefold() in (v["name"] + v["label"]).casefold())]
            return success({"verbs": [{"name": v["name"], "vid": v["vid"], "implemented": v["implemented"], "implementation_status": v["implementation_status"], "level": v["local_level"], "family": v["family"]}
                                       for v in verbs],
                            "families": [{"family": k, "purpose": v[0], "tools": v[1]} for k, v in FAMILIES.items()],
                            "implemented": len(SPECS), "scope": "2D vectors and bounded single-band rasters; explicit local domain",
                            "describe_tool": "gis_describe_algorithm"})
        if name == "gis_describe_algorithm":
            from .guidance import plan_step_contract
            item = next((v for v in self.runtime.capabilities()["verbs"] if v["name"] == arguments["algorithm"]), None)
            require(item is not None, "NOT_FOUND", "动词名称不存在")
            return success({"capability": item, "tool": self.tools.get(item["name"]),
                            "plan_step": plan_step_contract(),
                            "interface_note": "definitio-local-v0.1 signature; G1 compatibility differences are declared in AGENT.md"})
        spec = SPECS[name]
        request_id = arguments.get("request_id")
        params = {k: v for k, v in arguments.items() if k not in ("request_id", "authorization_id")}
        request_digest = digest({"tool": name, "parameters": params, **({"authorization_id": arguments["authorization_id"]} if arguments.get("authorization_id") else {})})
        old = self._existing(request_id, request_digest)
        confirmed = self.authorizations.authorize(arguments, session, request_id,
                                                  [(name, write_target(name, arguments))] if spec[2] != "R0" else [], replay=bool(old))
        task, replayed = self._task_request(request_id, request_digest, f"Agent: {name}", [{"verb": name, "parameters": params}])
        if not replayed and arguments.get("authorization_id"):
            task["governance_state"].update(authorization_id=arguments["authorization_id"], request_id=request_id)
            self.store.save_task(task)
        if task["task_state"] in ("DECLARED", "COMMITTED"):
            task = self.runtime.execute(task["id"], task["plan_digest"], confirmed)
        return self._task_reply(task, replayed)

    def _control(self, name, a, session=None):
        if name == "workspace_resource":
            request_digest = digest({"tool": name, "arguments": a})
            old = self._existing(a["request_id"], request_digest)
            self.authorizations.authorize(a, session, a["request_id"], [(name, a["name"])], replay=bool(old))
            if old:
                return success({"dataset": self.descriptor(self.store.dataset(old["reference"])), "replayed": True})
            from .completion import register_resource
            # This control requires explicit confirmation; it accepts no scoped grant.
            with self.store.lock, self.store.connect() as db:
                d = register_resource(self.runtime, a, db)
                self._save_request(db, a["request_id"], request_digest, "dataset", d["id"])
            return success({"dataset": self.descriptor(d), "replayed": False})
        if name == "pointcloud_import":
            request_digest = digest({"tool": name, "arguments": a})
            old = self._existing(a["request_id"], request_digest)
            self.authorizations.authorize(a, session, a["request_id"], [(name, a["name"])], replay=bool(old))
            if old:
                return success({"dataset": self.descriptor(self.store.dataset(old["reference"])), "replayed": True})
            from .pointcloud import snapshot
            payload, crs = snapshot(a["base64"], a["format"])
            with self.store.connect() as db:
                d = self.store.insert_pointcloud(a["name"], payload, crs, {"kind": "agent_pointcloud_import"}, db)
                self._save_request(db, a["request_id"], request_digest, "dataset", d["id"])
            return success({"dataset": self.descriptor(d), "replayed": False})
        if name == "raster_import":
            request_digest = digest({"tool": name, "arguments": a})
            old = self._existing(a["request_id"], request_digest)
            self.authorizations.authorize(a, session, a["request_id"], [(name, a["name"])], replay=bool(old))
            if old:
                return success({"dataset": self.descriptor(self.store.dataset(old["reference"])), "replayed": True})
            from .raster import validate_raster
            raster, crs = validate_raster(a["raster"], a["crs"])
            with self.store.connect() as db:
                d = self.store.insert_raster(a["name"], raster, crs, {"kind": "agent_raster_import"}, db)
                self._save_request(db, a["request_id"], request_digest, "dataset", d["id"])
            return success({"dataset": self.descriptor(d), "replayed": False})
        if name == "destructive_preflight":
            if a.get("operation") == "delete_dataset":
                require(not (set(a) - {"dataset", "operation"}), message="数据销毁预检不接受字段或新目标参数")
                return success({"preflight": self.runtime.delete_preflight(a["dataset"])})
            require("fields" in a and "target" in a, message="字段删除预检须 fields/target")
            return success({"preflight": self.runtime.field_preflight(a["dataset"], a["fields"], a["target"])})
        if name == "workflow_route":
            return success(route(a["intent"]))
        if name == "task_dry_run":
            result = preview(self.runtime, a["goal"], a["steps"])
            if not result["ok"]:
                fault = next(s["error"] for s in result["steps"] if s["state"] == "FAIL")
                return failure(GISFault(fault["code"], fault["message"], **fault.get("details", {})), {"preview": result})
            return success({"preview": result})
        if name == "authorization_grant":
            return success({"authorization": self.authorizations.grant(a, session)})
        if name == "authorization_get":
            return success({"authorization": self.authorizations.get(a["authorization_id"], session)})
        if name == "authorization_revoke":
            return success({"authorization": self.authorizations.revoke(a["authorization_id"], session)})
        if name == "artifact_view":
            return success(self.artifact_view(a))
        if name == "workspace_list":
            items = self.store.datasets()
            if a.get("name_contains"):
                items = [d for d in items if a["name_contains"].casefold() in d["name"].casefold()]
            if a.get("fields"):
                items = [d for d in items if set(a["fields"]).issubset(f["name"] for f in d["fields"])]
            if "crs" in a:
                from .model import crs_info
                crs = crs_info(a["crs"])
                items = [d for d in items if d["crs"]["definition_hash"] == crs["definition_hash"]]
            if a.get("geometry_type"):
                items = [d for d in items if a["geometry_type"] in d["geometry_types"]]
            return success(self._page(items, a, lambda d: self.descriptor(d)))
        if name == "dataset_describe":
            d = self.store.dataset(a["dataset"])
            self.store.verify_snapshot(d)
            return success({"dataset": self.descriptor(d)})
        if name == "dataset_import":
            request_digest = digest({"tool": name, "arguments": a})
            old = self._existing(a["request_id"], request_digest)
            gj, crs, schema = validate_geojson(a["geojson"], a["crs"], a.get("schema"))
            self.authorizations.authorize(a, session, a["request_id"], [(name, a["name"])], replay=bool(old))
            if old:
                return success({"dataset": self.descriptor(self.store.dataset(old["reference"])), "replayed": True})
            with self.store.connect() as db:
                d = self.store.insert_dataset(a["name"], gj, crs, schema, {"kind": a.get("source_kind", "agent_import")}, db)
                self._save_request(db, a["request_id"], request_digest, "dataset", d["id"])
            return success({"dataset": self.descriptor(d), "replayed": False})
        if name == "dataset_features":
            d = self.store.dataset(a["dataset"])
            require(d.get("kind", "vector") == "vector", "NOT_SUPPORTED", "栅格请通过 raster_uri 与 artifact_view 读取")
            self.store.verify_snapshot(d)
            names = [f["name"] for f in d["fields"]]
            fields = a.get("fields", names)
            require(set(fields).issubset(names), "SCHEMA_MISMATCH", "读取字段不在 schema 中", available=names)
            def item(f):
                index = f["id"] - 1
                value = {"record_id": f["id"], "properties": {k: f["properties"][k] for k in fields},
                         "geometry_uri": f"definitio://datasets/{d['id']}/geometry/{index}"}
                if a.get("include_geometry"):
                    if len(canonical(f["geometry"])) <= 4000:
                        value["geometry"] = f["geometry"]
                    else:
                        value["geometry_inline"] = False
                return value
            page = self._page(d["geojson"]["features"], a, item)
            page.update(dataset=d["id"], fingerprint=d["fingerprint"])
            return success(page)
        if name == "task_plan":
            task, replayed = self._task_request(a["request_id"], digest({"tool": name, "arguments": a}), a["goal"], a["steps"], a.get("exports"))
            effects = []
            previous_fields = None
            for step in task["steps"]:
                p = step["parameters"]
                ref = p.get("input") or p.get("dataset") or p.get("source") or (p.get("inputs") or [None])[0]
                fields = previous_fields if ref == "$previous" else [f["name"] for f in self.store.dataset(ref)["fields"]] if ref else []
                dropped = p["fields"] if step["verb"] == "gis_delete_field" else [f for f in fields if f not in p["fields"]] if step["verb"] == "gis_dissolve" else fields if step["verb"] == "gis_buffer" and p["dissolve"] else []
                effects.append({"step": step["index"], "level": step["level"], "target": write_target(step["verb"], p), "dropped_fields": dropped,
                                "authorization": "explicit confirm required" if step["level"] != "R0" else "read-only"})
                if step["verb"] == "gis_create_dataset":
                    previous_fields = [f["name"] for f in p["fields"]]
                elif step["verb"] == "gis_add_field":
                    previous_fields = fields + [p["field"]["name"]]
                elif step["verb"] == "gis_dissolve":
                    previous_fields = p["fields"]
                elif step["verb"] == "gis_buffer" and p["dissolve"]:
                    previous_fields = []
                else:
                    previous_fields = fields
            plan = [{"verb": s["verb"], "parameters": s["parameters"]} for s in task["steps"]]
            data = {"task": self.task_summary(task), "effects": effects, "replayed": replayed}
            if task["task_state"] == "DECLARED":
                data["preview"] = preview(self.runtime, task["goal"], plan)
            if len(canonical(plan)) <= 20000:
                data["plan"] = plan
            else:
                data["plan_uri"] = f"definitio://tasks/{task['id']}/record"
                data["message"] = "Exact plan retained as an artifact; inspect it before authorization."
            return success(data)
        if name == "task_execute":
            task = self.store.task(a["task_id"])
            require(a["plan_digest"] == task["plan_digest"] == self.runtime.plan_digest(task), message="计划摘要不一致")
            from .completion import EXPLICIT_ONLY, precheck
            for s in task["steps"]:
                precheck(self.runtime, s["verb"], s["parameters"])
            require(not a.get("authorization_id") or not any(s["verb"] in EXPLICIT_ONLY | {"gis_rename_dataset", "gis_append_dataset"} for s in task["steps"]), "CONFIRM_REQUIRED", "该操作须逐次显式确认")
            from . import resume as resume_module
            resuming = bool(a.get("resume"))
            start = resume_module.next_resumable_index(task) if resuming else 0
            writes = [(s["verb"], s.get("head_bindings", {}).get("target", write_target(s["verb"], s["parameters"])))
                      for s in task["steps"][start:] if s["level"] != "R0"]
            confirmed = self.authorizations.authorize(a, session, task["id"], writes,
                                                      replay=task["task_state"] != "DECLARED" and not resuming)
            if task["task_state"] == "DECLARED" and a.get("authorization_id"):
                task["governance_state"]["authorization_id"] = a["authorization_id"]
                self.store.save_task(task)
            if resuming:
                return self._task_reply(self.runtime.resume(a["task_id"], a["plan_digest"], confirmed))
            before = task["replay_count"]
            executed = self.runtime.execute(a["task_id"], a["plan_digest"], confirmed)
            return self._task_reply(executed, replayed=executed["replay_count"] > before)
        if name == "task_get":
            task = self.store.task(a["task_id"])
            reply = self._task_reply(task)
            if a.get("verify"):
                reply["data"]["verification"] = self.verify_task(task)
            return reply
        if name == "evidence_get":
            e = self.store.evidence(a["evidence_id"])
            section = a.get("section", "summary")
            if section in ("checks", "groups"):
                key = "postcondition_checks" if section == "checks" else "group_manifest"
                return success({"evidence_id": e["id"], "section": section, **self._page(e.get(key, []), a)})
            if section == "parameters":
                return success({k: e[k] for k in ("requested_parameters", "effective_parameters", "parameter_rewrites")})
            keys = ("id", "task_id", "step_id", "verb", "vid", "backend", "implementation_route", "input_unchanged", "input_feature_count",
                    "output_feature_count", "processed_feature_count", "result_empty", "empty_reason", "output_ref", "output_fingerprint",
                    "evidence_digest", "atomicity", "versions", "verification_limits", "semantic")
            return success({"evidence": {k: e.get(k) for k in keys}, "resource_uri": f"definitio://evidence/{e['id']}"})
        if name == "artifact_read":
            value = self.artifact(a["uri"])
            content = canonical(value)
            offset, maximum = a.get("offset", 0), a.get("max_chars", 4096)
            require(offset <= len(content), message="offset 超出工件范围", total_chars=len(content))
            end = min(offset + maximum, len(content))
            return success({"uri": a["uri"], "encoding": "json-text; offsets are Unicode characters", "content": content[offset:end],
                            "offset": offset, "next_offset": end if end < len(content) else None,
                            "total_chars": len(content), "sha256_json_utf8": digest(value)})
        raise GISFault("NOT_SUPPORTED", "控制面工具未实现")

    @staticmethod
    def _page(items, a, convert=lambda v: v):
        offset, limit = a.get("offset", 0), a.get("limit", 20)
        page = []
        used = 0
        for value in items[offset:offset + limit]:
            item = convert(value)
            length = len(canonical(item))
            require(page or length <= 30000, message="单条记录过大，请用 artifact_read 读取完整工件")
            if used + length > 30000:
                break
            page.append(item)
            used += length
        end = offset + len(page)
        return {"items": page, "offset": offset, "returned": len(page), "total": len(items),
                "next_offset": end if end < len(items) else None}

    def artifact_view(self, a):
        value = self.artifact(a["uri"])
        fingerprint = digest(value)
        pointer, offset = a.get("pointer", ""), 0
        if a.get("cursor"):
            try:
                cursor = json.loads(base64.urlsafe_b64decode(a["cursor"].encode("ascii")))
            except (ValueError, UnicodeError) as exc:
                raise GISFault("INVALID_ARGUMENT", "游标格式不合法") from exc
            require(isinstance(cursor, dict) and cursor.get("uri") == a["uri"] and cursor.get("sha") == fingerprint,
                    message="游标不属于该不可变工件")
            pointer, offset = cursor.get("pointer"), cursor.get("offset")
            require(isinstance(pointer, str) and type(offset) is int and offset >= 0, message="游标内容不合法")
            require("pointer" not in a or a["pointer"] == pointer, message="游标与 pointer 冲突")
        require(pointer == "" or pointer.startswith("/"), message="pointer 必须是 JSON Pointer")
        for token in pointer.split("/")[1:] if pointer else []:
            key = token.replace("~1", "/").replace("~0", "~")
            if isinstance(value, dict):
                require(key in value, "NOT_FOUND", "JSON Pointer 键不存在", key=key)
                value = value[key]
            elif isinstance(value, list) and key.isdigit():
                require(int(key) < len(value), "NOT_FOUND", "JSON Pointer 数组越界")
                value = value[int(key)]
            else:
                raise GISFault("NOT_FOUND", "JSON Pointer 不能继续下钻")
        data = {"uri": a["uri"], "pointer": pointer, "summary": value_summary(value),
                "integrity": {"sha256_json_utf8": fingerprint, "checked_by": "server_snapshot_and_canonical_hash"}}
        if a.get("view", "summary") == "summary" and not a.get("cursor"):
            data["drill_down"] = {"tool": "artifact_view", "arguments": {"uri": a["uri"], "pointer": pointer, "view": "page"}}
            return data
        if isinstance(value, (list, dict)):
            items = value if isinstance(value, list) else [{"key": k, "value": v} for k, v in value.items()]
            page = self._page(items, {"offset": offset, "limit": a.get("limit", 20)})
            end = page.pop("next_offset")
            page.pop("offset")
            data.update(page)
            data["cursor"] = base64.urlsafe_b64encode(canonical({"uri": a["uri"], "sha": fingerprint, "pointer": pointer, "offset": end}).encode()).decode() if end is not None else None
        else:
            require(len(canonical(value)) <= 30000, message="单个值过大；请使用 artifact_read 精确分块")
            data.update(value=value, cursor=None)
        return data

    def artifact(self, uri):
        parsed = urlparse(uri)
        require(parsed.scheme == "definitio" and not parsed.query and not parsed.fragment, message="只接受本系统返回的 definitio:// 工件 URI")
        parts = [unquote(p) for p in parsed.path.strip("/").split("/")]
        require(parts and parts[0] and all(p not in (".", "..") for p in parts), message="工件 URI 不合法")
        if parsed.netloc == "datasets":
            d = self.store.dataset(parts[0])
            self.store.verify_snapshot(d)
            if len(parts) == 1:
                return self.descriptor(d)
            if parts[1:] == ["metadata"]:
                result = {k: v for k, v in d.items() if k not in ("geojson", "raster", "pointcloud", "resource")}
                if "resource" in d:
                    result["resource"] = {k: v for k, v in d["resource"].items() if k not in ("base64", "qgz_base64")}
                return result
            if parts[1:] == ["pointcloud"]:
                require(d.get("kind") == "pointcloud", "NOT_SUPPORTED", "该数据集不是点云")
                return d["pointcloud"]
            if parts[1:] == ["raster"]:
                require(d.get("kind") == "raster", "NOT_SUPPORTED", "该数据集不是栅格")
                return {**d["raster"], "crs": d["crs"], "fingerprint": d["fingerprint"]}
            if parts[1:] == ["geojson"]:
                require(d.get("kind", "vector") == "vector", "NOT_SUPPORTED", "该数据集不是矢量")
                return {**d["geojson"], "definitio": {"crs": d["crs"], "schema": d["fields"], "fingerprint": d["fingerprint"],
                                                       "semantic": d.get("semantic")}}
            if len(parts) == 3 and parts[1] == "geometry" and parts[2].isdigit():
                index = int(parts[2])
                require(index < len(d["geojson"]["features"]), "NOT_FOUND", "几何记录索引不存在")
                return d["geojson"]["features"][index]["geometry"]
        if parsed.netloc == "tasks":
            t = self.store.task(parts[0])
            if len(parts) == 1:
                return self.task_summary(t)
            if parts[1:] == ["record"]:
                return t
            if len(parts) == 3 and parts[1] == "results" and parts[2].isdigit():
                index = int(parts[2])
                require(index < len(t["steps"]), "NOT_FOUND", "步骤索引不存在")
                return t["steps"][index].get("result")
        if parsed.netloc == "evidence" and len(parts) == 1:
            return self.store.evidence(parts[0])
        if parsed.netloc == "artifacts" and len(parts) == 1:
            from . import commit
            record = self.store.file_artifact(parts[0])
            return {**record, "readback": commit.readback_file_artifact(self.store.path.parent, record)}
        raise GISFault("NOT_FOUND", "工件 URI 不存在", uri=uri)
