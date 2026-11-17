"""Compact operating handbook, deterministic routing and MCP task recipes."""
from __future__ import annotations

from .model import require

INSTRUCTIONS = """Definitio: the calling Agent owns planning.
1. workflow_route(intent) selects a verb family. workspace_list filters by name,
fields and CRS. dataset_describe gives the selected inputs' exact schema.
2. Compact MCP: gis_describe_algorithm gives the exact schema, gis_call executes it.
The full profile exposes direct gis_* tools. Use raster_import for bounded single-band
grids; explicit NoData/units/light/convention are required, with no implicit resampling.
3. Use known CRS and x,y coordinates. reproject moves coordinates; assign_projection
changes metadata. Planar analysis uses projected CRS, metres and m². Binary polygon
operations use identical CRS. gis_validate_geometry reports the valid flag.
4. task_dry_run computes each step in memory and shows actual fields, CRS, counts
and effects. task_plan stores the reviewed chain; task_execute uses its exact digest.
$previous is the immediately preceding dataset output; never reference its new
target name before execution. Step parameters contain operation arguments only:
put request_id on task_plan and authorization_id on task_execute, never in steps.
Dissolve keeps only its
explicit fields ([] means all); clip keeps input fields; spatial_filter selects
whole records; merge concatenates matching schemas into a new dataset.
5. Use existing authorization: confirm=true + stable request_id for one write,
or authorization_grant once for a session prefix/verbs/count/TTL, then pass
 authorization_id. Scoped writes derive stable request IDs automatically.
authorization_get reports use; authorization_revoke ends new writes.
6. Read summaries first; artifact_view provides JSON-pointer drill-down and cursors.
artifact_read provides exact JSON chunks. On a lost response reuse identical arguments
and request_id, or task_get. Check ok, valid and task state; partial chains retain
committed outputs. FAILED/ISOLATED receipts are inspected before creating a new task."""

FAMILIES = {
    "discovery": ("Capability discovery", ["gis_list_algorithms", "gis_describe_algorithm"], "discover capabilities 能力 工具"),
    "data": ("Create/copy/concatenate immutable datasets", ["gis_create_dataset", "gis_copy_dataset", "gis_merge_datasets", "gis_convert_format"], "merge append copy import export 合并 追加 复制 导入 导出"),
    "attributes": ("Add/calculate fields on a new copy", ["gis_add_field", "gis_calculate_field"], "field calculate attribute 字段 计算 属性"),
    "crs": ("CRS metadata versus coordinate transformation", ["gis_assign_projection", "gis_reproject"], "crs projection reproject 坐标 投影"),
    "geometry": ("Construct polygon analysis outputs", ["gis_buffer", "gis_clip", "gis_overlay", "gis_dissolve"], "buffer clip overlay dissolve 缓冲 裁剪 叠加 融合"),
    "query": ("Read-only selection/statistics/measurement/validation", ["gis_field_statistics", "gis_spatial_filter", "gis_measure", "gis_validate_geometry"], "statistics filter measure validate 统计 筛选 量测 检查 有效"),
}


RULES = {
    "operation_parameters_only": {"where": "task_plan / task_dry_run step parameters",
                                  "meaning": "Step parameters carry operation arguments only",
                                  "fix": "Put request_id on task_plan and authorization_id on task_execute"},
    "previous_dataset_output": {"where": "task_plan / task_dry_run step references",
                                "meaning": "$previous references the immediately preceding dataset output",
                                "fix": "Use an existing handle for the first step; never a planned new target name"},
    "published_surface_names": {"where": "MCP surface adapter",
                                "meaning": "Only names published by the selected surface profile are accepted",
                                "fix": "Use the canonical name or the exact published surface name"},
    "verification_precheck": {"where": "before any side effect, in both dry-run and execute",
                              "meaning": "A contract predicate refused the step; details.predicate names it",
                              "fix": "Read details.observation and details.details, correct the unit, object or version"},
    "version_binding_changed": {"where": "task_execute commit gate",
                                "meaning": "An input version or target state changed after planning",
                                "fix": "Re-plan against the current versions; the old plan is refused"},
    "resume_dependency_invalidated": {"where": "task_execute resume gate",
                                      "meaning": "A committed step's dependency version is no longer valid",
                                      "fix": "Re-plan; recovery is only allowed from still-valid results"},
    "resume_state_not_recoverable": {"where": "task_execute resume gate",
                                     "meaning": "Only FAILED plans resume; ISOLATED means unknown external state",
                                     "fix": "Inspect task_get and evidence, then declare a new plan"},
    "commit_verification": {"where": "task commit, after real readback",
                            "meaning": "Actual writes left the declared scope or a binding changed",
                            "fix": "Inspect details.out_of_scope; nothing outside the plan may be written"},
    "committed_receipt_is_not_current_state": {"where": "task_get and committed-task replay, when a committed output can no longer be read back",
                                               "meaning": "A committed receipt is history; it does not prove the output still exists in its committed state",
                                               "fix": "Inspect task_get unavailable_outputs / live_status and the evidence; restore the output or re-plan, never trust the old receipt"},
}


def plan_step_contract():
    return {
        "parameter_rule": "operation_parameters_only",
        "control_fields_outside_parameters": {"request_id": "task_plan", "authorization_id": "task_execute"},
        "reference_rule": "previous_dataset_output",
        "reference_hint": "Use $previous for the immediately preceding dataset output, never its new target name. First-step inputs must already exist.",
        "rules": sorted(RULES),
        "example": {"goal": "Copy and validate", "request_id": "plan-copy-1", "steps": [
            {"verb": "gis_copy_dataset", "parameters": {"source": "EXISTING_DATASET_ID", "target": "job_copy"}},
            {"verb": "gis_validate_geometry", "parameters": {"dataset": "$previous", "repair": False}}]},
    }
FAMILIES["data"][1].extend(["gis_rename_dataset", "gis_append_dataset", "gis_import_data"])
FAMILIES["attributes"][1].append("gis_delete_field")
FAMILIES["geometry"][1].append("gis_spatial_join")
FAMILIES["raster"] = ("Explicit single-band grid analysis", ["gis_raster_calc", "gis_slope", "gis_aspect", "gis_hillshade", "gis_reclassify", "gis_zonal_statistics"], "raster slope aspect hillshade zonal reclass 栅格 坡度 坡向 阴影 分区 重分类")
FAMILIES["pointcloud"] = ("Installed PDAL on immutable LAS/LAZ bytes", ["gis_pointcloud_info", "gis_pointcloud_process"], "pointcloud las laz pdal 点云 抽稀 密度")
FAMILIES["geometry"][1].append("gis_run_processing")
FAMILIES["data"][1].append("gis_delete_dataset")
FAMILIES["services"] = ("Native GIS services and SensorThings", ["gis_serve_vector_tile", "gis_publish_layer", "gis_list_services", "gis_query_observations"], "service tile mvt publish sensorthings observation 服务 瓦片 发布 观测")
FAMILIES["map"] = ("Licensed catalog layers and native QGIS map images", ["gis_add_layer_from_catalog", "gis_export_map_image"], "map catalog image 地图 目录 出图")
FAMILIES["discovery"][1].append("gis_run_gp")
PRECONDITIONS = {
    "gis_run_gp": ["Explicit local-arcpy-10.8 route; management.GetCount seed only", "No G1 arcmap/pro certification claimed; named/destructive/defer tools blocked"],
    "gis_delete_dataset": ["Explicit confirm + target_echo + single-use destructive_preflight(operation=delete_dataset)", "Only unreferenced standalone snapshots; physical removal; no cascade; preserves destruction receipt"],
    "gis_serve_vector_tile": ["Server-configured PostGIS, immutable vector; EPSG CRS", "XYZ bounds; extent=4096, buffer=0, clip=true; expiring binary handle, no tile cache"],
    "gis_publish_layer": ["Registered catalog with fingerprint-bound license evidence, checked before confirmation", "Own loopback GeoServer sandbox, definitio_ workspace, explicit style; limited lossless shapefile schema", "Catalog writeback pending; external partial state is isolated, never blindly replayed"],
    "gis_list_services": ["Server-configured GeoServer or SensorThings; actual GET capabilities"],
    "gis_query_observations": ["Server-configured SensorThings endpoint; GET-only 8 entity sets", "OData parameters passed unchanged; no local filtering or page concatenation; real empty collections retained"],
    "gis_add_layer_from_catalog": ["workspace_resource registers license/catalog/map with explicit purpose", "License evidence tied to immutable source fingerprint; commercial use gate before confirmation", "Native QGIS project with embedded data; active map version advances, previous IDs remain"],
    "gis_export_map_image": ["Managed native QGIS map resource; current view only, PNG, explicit dpi", "New managed filename only; width/height/extent/CRS are fixed map properties"],
    "gis_run_processing": ["Published local seed: native:centroids only, explicit INPUT/ALL_PARTS/OUTPUT", "Installed QGIS; named-verb bypass/defer/SQL/destructive algorithms rejected before persistence"],
    "gis_delete_field": ["R3 even on a copy; exact target_echo and single-use destructive_preflight token", "Only ordinary scalar fields; no declared business keys or dependencies"],
    "gis_rename_dataset": ["Changes the logical name only; frozen ID snapshots stay readable", "New name must be free; downstream name references may break"],
    "gis_append_dataset": ["Existing logical target; identical schema/CRS/geometry type; source distinct", "Target head advances atomically; old ID remains an immutable historical snapshot"],
    "gis_import_data": ["Fixed GeoJSON data URI with definitio CRS/schema/source_license", "Caller attests allow_import and evidence_ref; no network or filesystem fetch"],
    "gis_spatial_join": ["Identical CRS; exact input OP join predicates", "first ties rejected without declared stable business keys; all retains all pairs", "nearest only in projected CRS"],
    "gis_buffer": ["Projected CRS; valid non-null 2D geometry", "Nonzero metre distance; negative distance only for polygons", "dissolve=true drops all attributes"],
    "gis_clip": ["Both inputs valid Polygon/MultiPolygon", "Same projected CRS; input attributes retained"],
    "gis_overlay": ["Both inputs valid Polygon/MultiPolygon", "Same projected CRS; intersection/difference; input attributes retained"],
    "gis_dissolve": ["Valid Polygon/MultiPolygon; projected CRS", "Explicit existing fields; string/integer/boolean/null keys", "Only grouping fields survive; [] means dissolve all"],
    "gis_reproject": ["Known source/target CRS; valid 2D geometry", "Transforms coordinates; datum-operation ambiguity uncalibrated"],
    "gis_assign_projection": ["Known CRS definition; coordinates must already use that CRS", "Metadata only; does not transform coordinates"],
    "gis_merge_datasets": ["Schemas identical including order/types/nullability", "Same CRS; valid non-null geometry; new target only"],
    "gis_field_statistics": ["Existing numeric fields; explicit null_policy=exclude", "Read-only; group_by fields must exist"],
    "gis_calculate_field": ["Existing numeric target field", "Safe arithmetic on non-null numeric inputs; no code/calls"],
    "gis_add_field": ["New field name; explicit type/nullability/default", "Default must match schema"],
    "gis_spatial_filter": ["Valid non-null input and reference geometry", "Reference WKT in dataset CRS; returns whole records"],
    "gis_measure": ["Projected CRS; nonempty valid 2D WKT", "Distance requires b_wkt in same CRS"],
    "gis_validate_geometry": ["Read-only repair=false", "Check valid flag; invalid geometry is a successful diagnostic result"],
}


def family_for(verb):
    return next((key for key, (_, verbs, _) in FAMILIES.items() if verb in verbs), None)


def prerequisites(verb):
    if verb in FAMILIES["pointcloud"][1]:
        return ["pointcloud_import: fixed LAS/LAZ bytes, <=6 MiB and <=100000 points, known CRS", "Installed configured PDAL; strict action-specific keys; no arbitrary pipeline/path/URL", "boundary produces a 2D vector under the explicit local addendum; other actions retain their declared output kinds"]
    if verb in FAMILIES["raster"][1]:
        return ["raster_import: single band, <=512x512, explicit grid/CRS/dtype/NoData", "No implicit alignment/resampling; explicit NoData policy", "Terrain: projected CRS, Horn 3x3, masked border; explicit units/light/convention"]
    return PRECONDITIONS.get(verb, ["Explicit inputs; immutable new targets for writes; 2D vector domain"])


def route(intent):
    value = intent.casefold()
    selected = [key for key, (_, _, terms) in FAMILIES.items() if any(t in value for t in terms.split())]
    return {"matched": bool(selected),
            "families": [{"family": key, "purpose": FAMILIES[key][0], "tools": FAMILIES[key][1]} for key in (selected or FAMILIES)],
            "distinctions": {"merge": "Concatenate identical schemas into a NEW dataset; append advances an EXISTING logical target version",
                             "copy": "Duplicate one dataset", "clip": "Cut polygon geometry; retain input fields",
                             "overlay": "Polygon intersection/difference with input-side fields",
                             "spatial_filter": "Select complete records; no geometry cutting",
                             "assign_projection": "Relabel CRS metadata", "reproject": "Transform coordinates"},
            "next": ["workspace_list", "dataset_describe", "gis_describe_algorithm", "task_dry_run"]}


PROMPTS = {
    "gis_handbook": ("GIS operating handbook", [], INSTRUCTIONS),
    "clip_dissolve_statistics": ("Clip, dissolve, then inspect group counts", ["input", "boundary", "group_field", "target_prefix"],
        """Describe {input} and {boundary}; validate geometry and identical projected CRS.
Dry-run gis_clip(input={input},clip_by={boundary},target={target_prefix}clip);
gis_dissolve(input=$previous,fields=[{group_field}],target={target_prefix}groups).
Dissolve keeps only {group_field}. For numeric grouping fields, append read-only
gis_field_statistics(dataset=$previous,fields=[{group_field}],group_by=[],statistics=[count],
output=result,null_policy=exclude). For strings inspect feature_count/dataset_features.
Statistics on original numeric fields must run BEFORE dissolve or on a separate branch.
Review dry-run fields/counts, task_plan, then execute the exact digest under authorization."""),
    "buffer_validate": ("Buffer in metres, then validate", ["input", "distance_m", "target_prefix"],
        """Describe {input}. For geographic input, explicitly reproject to the task's projected CRS. Validate input. Dry-run gis_buffer(input={input},
target={target_prefix}buffer,distance={distance_m},unit=m,dissolve=false),
then gis_validate_geometry(dataset=$previous,repair=false). Review effects, task_plan
and execute the exact digest under scoped or explicit authorization. Inspect valid flag."""),
    "reproject_merge": ("Align CRS and merge identical schemas", ["input", "other", "target_crs", "target_prefix"],
        """Describe {input} and {other}; verify identical field order/types/nullability.
Dry-run gis_reproject(input={input},target={target_prefix}projected,target_crs={target_crs}),
then gis_merge_datasets(inputs=[$previous,{other}],target={target_prefix}merged).
The other input must already use {target_crs}; otherwise reproject it in a separate
authorized branch. Do not use assign_projection to move coordinates.
Review preview; task_plan and execute only after authorization."""),
}


def render_prompt(name, arguments=None):
    require(name in PROMPTS, "NOT_FOUND", "提示词模板不存在")
    _, required, template = PROMPTS[name]
    arguments = arguments or {}
    require(set(arguments) == set(required), message="提示词参数须与模板声明一致", required=required)
    require(all(isinstance(v, str) and 0 < len(v) <= 256 for v in arguments.values()), message="模板参数须为 1–256 字符")
    return template.format(**arguments)
