# Definitio 使用手册（自动生成，勿手改）

格式：`definitio-handbook-v1`　来源摘要：`5a305bb91d1a66e450477e4adaed9ddc473a31c33e74af5c96e046aa02cfe62d`

> 本文件由 `scripts/generate_handbook.py` 从活目录生成。动词、错误码、规则、disposition 口径变动后必须重新生成，否则 `tests/test_v05_handbook.py` 的漂移测试会失败。

## 1. 操作顺序

```text
Definitio: the calling Agent owns planning.
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
committed outputs. FAILED/ISOLATED receipts are inspected before creating a new task.
```

## 2. 计数

- verbs: **40**
- tools: **60**
- specs: **40**
- error_codes: **23**
- rules: **8**
- dispositions: **3**

## 3. 动词（40）

| 动词 | VID | 层级 | 家族 | 必需参数 | 已实现 |
|---|---|---|---|---|---|
| `gis_run_gp` | V01 | R1 | discovery | `backend`, `tool`, `args` | 是 |
| `gis_run_processing` | V02 | R1 | geometry | `algorithm`, `params` | 是 |
| `gis_list_algorithms` | V03 | R0 | discovery | - | 是 |
| `gis_describe_algorithm` | V04 | R0 | discovery | `algorithm` | 是 |
| `gis_create_dataset` | V05 | R1 | data | `target`, `crs`, `fields` | 是 |
| `gis_add_field` | V06 | R2 | attributes | `dataset`, `target`, `field` | 是 |
| `gis_delete_field` | V07 | R3 | attributes | `dataset`, `target`, `fields`, `target_echo`, `preflight_id` | 是 |
| `gis_calculate_field` | V08 | R2 | attributes | `dataset`, `target`, `field`, `expression` | 是 |
| `gis_copy_dataset` | V09 | R1 | data | `source`, `target` | 是 |
| `gis_rename_dataset` | V10 | R2 | data | `dataset`, `new_name` | 是 |
| `gis_delete_dataset` | V11 | R3 | data | `dataset`, `target_echo`, `preflight_id` | 是 |
| `gis_merge_datasets` | V12 | R1 | data | `inputs`, `target` | 是 |
| `gis_append_dataset` | V13 | R2 | data | `source`, `target` | 是 |
| `gis_assign_projection` | V14 | R2 | crs | `dataset`, `target`, `crs` | 是 |
| `gis_reproject` | V15 | R1 | crs | `input`, `target`, `target_crs` | 是 |
| `gis_convert_format` | V16 | R1 | data | `input`, `target`, `format` | 是 |
| `gis_import_data` | V17 | R1 | data | `source_uri`, `target_name`, `format`, `kind` | 是 |
| `gis_buffer` | V18 | R1 | geometry | `input`, `target`, `distance`, `unit`, `dissolve` | 是 |
| `gis_clip` | V19 | R1 | geometry | `input`, `clip_by`, `target` | 是 |
| `gis_overlay` | V20 | R1 | geometry | `input`, `overlay`, `target`, `operation` | 是 |
| `gis_dissolve` | V21 | R1 | geometry | `input`, `target`, `fields` | 是 |
| `gis_spatial_join` | V22 | R1 | geometry | `input`, `join`, `target`, `predicate`, `summary` | 是 |
| `gis_raster_calc` | V23 | R1 | raster | `inputs`, `expression`, `target`, `nodata_policy` | 是 |
| `gis_slope` | V24 | R1 | raster | `input`, `target`, `z_factor`, `unit`, `nodata_policy` | 是 |
| `gis_aspect` | V25 | R1 | raster | `input`, `target`, `z_factor`, `convention`, `flat_policy`, `nodata_policy` | 是 |
| `gis_hillshade` | V26 | R1 | raster | `input`, `target`, `z_factor`, `azimuth`, `altitude`, `nodata_policy` | 是 |
| `gis_reclassify` | V27 | R1 | raster | `input`, `target`, `rules`, `unmapped`, `nodata_policy` | 是 |
| `gis_zonal_statistics` | V28 | R1 | raster | `zones`, `values`, `target`, `statistics`, `output`, `zone_field`, `nodata_policy`, `pixel_rule`, `weighting` | 是 |
| `gis_field_statistics` | V29 | R0 | query | `dataset`, `fields`, `group_by`, `statistics`, `output`, `null_policy` | 是 |
| `gis_spatial_filter` | V30 | R0 | query | `table`, `predicate`, `reference_wkt` | 是 |
| `gis_measure` | V31 | R0 | query | `operation`, `a_wkt`, `crs` | 是 |
| `gis_validate_geometry` | V32 | R0 | query | `dataset`, `repair` | 是 |
| `gis_serve_vector_tile` | V33 | R0 | services | `table`, `z`, `x`, `y`, `geometry_column` | 是 |
| `gis_publish_layer` | V34 | R1 | services | `dataset_ref`, `workspace`, `style`, `purpose` | 是 |
| `gis_list_services` | V35 | R0 | services | `backend` | 是 |
| `gis_query_observations` | V36 | R0 | services | `backend`, `entity` | 是 |
| `gis_pointcloud_info` | V37 | R0 | pointcloud | `input`, `backend` | 是 |
| `gis_pointcloud_process` | V38 | R1 | pointcloud | `input`, `target`, `action`, `params` | 是 |
| `gis_add_layer_from_catalog` | V39 | R1 | map | `catalog_id`, `map`, `purpose` | 是 |
| `gis_export_map_image` | V40 | R1 | map | `map`, `target_path`, `format`, `dpi` | 是 |

## 4. 错误码（复苏动作）

| 错误码 | 建议动作 | 可重试 | 说明 |
|---|---|---|---|
| `ALREADY_EXISTS` | `inspect_existing_target` | 否 | Inspect the existing target. Check prior request/task status; choose a new explicit target only for a genuinely new operation. |
| `AMBIGUOUS` | `disambiguate_handle` | 否 | Handle resolution found >=2 distinct identities. Supply stable fields that distinguish them; values and ranges are not identity. Never pick one silently. |
| `AUTHORIZATION_EXHAUSTED` | `inspect_authorization_scope` | 否 | The grant's write reservation is used up. Inspect authorization_get and request a new explicitly approved grant. |
| `AUTHORIZATION_REQUIRED` | `obtain_authorization` | 否 | No session grant covers this write. Obtain host/user approval, then authorization_grant or per-call confirm=true with a stable request_id. |
| `AUTHORIZATION_SCOPE` | `inspect_authorization_scope` | 否 | The write left the granted prefix/verb scope. Inspect authorization_get; never widen a grant implicitly. |
| `BACKEND_ERROR` | `inspect_failure` | 否 | Read task_get and its evidence. Current-step rollback does not erase previously committed outputs in a partial workflow. |
| `BACKEND_UNAVAILABLE` | `inspect_environment` | 否 | Run scripts/environment_doctor.py; the declared native backend is not configured here. No silent fallback. |
| `CONFIRM_REQUIRED` | `obtain_authorization` | 否 | Obtain host/user authorization for the exact target and field effects; only then resubmit with confirm=true. |
| `CRS_MISMATCH` | `explicit_reprojection` | 是 | Inspect both CRS definitions and explicitly reproject a new copy to a common CRS before analysis. |
| `DEFER_BLOCKED` | `inspect_capabilities` | 否 | The deferred route is not opened on this engine. Inspect gis_describe_algorithm prerequisites. |
| `DEPENDENCY_BLOCKED` | `inspect_dependencies` | 否 | Other objects still reference this dataset. Inspect the reported impact; destruction never cascades. |
| `DESTRUCTIVE_BLOCKED` | `inspect_capabilities` | 否 | The named destructive route is not opened on this engine. Inspect gis_describe_algorithm prerequisites. |
| `DIMENSION_MISMATCH` | `inspect_geometry` | 是 | Inputs disagree on dimensionality or geometry type. Inspect dataset_describe geometry_types; only 2D is accepted. |
| `GEOMETRY_INVALID` | `inspect_geometry` | 是 | Run read-only gis_validate_geometry, identify offending records; do not silently repair or drop them. |
| `INVALID_ARGUMENT` | `correct_arguments` | 是 | Read the published schema, correct the reported JSON paths. A reused request_id with different arguments requires a NEW key after reviewing prior status. |
| `LICENSE_BLOCKED` | `inspect_source_license` | 否 | The source did not attest allow_import with an evidence_ref. Supply explicit rights evidence; no license is inferred. |
| `NOT_FOUND` | `discover_inputs` | 是 | Call workspace_list; use an actual dataset/task/evidence handle. |
| `NOT_SUPPORTED` | `inspect_capabilities` | 否 | Use gis_describe_algorithm to inspect the declared domain; no silent fallback to another backend. |
| `PREFLIGHT_REQUIRED` | `obtain_destructive_preflight` | 否 | Destructive steps need a fresh single-use destructive_preflight token bound to the exact target and fingerprint. |
| `SCHEMA_MISMATCH` | `inspect_schema` | 是 | Read dataset_describe and provide exact, compatible field names/types; do not silently coerce. |
| `STALE` | `discover_inputs` | 是 | Handle resolution found 0 capture proofs for the declared role and stable fields. Re-read workspace_list; never treat an absent handle as a successful delivery. |
| `TARGET_STATE_UNKNOWN` | `inspect_task_and_evidence` | 否 | STOP automatic retries. Inspect task_get and evidence; target state is not proven safe for replay. |
| `UNKNOWN_CRS` | `supply_crs` | 是 | Provide the known CRS definition; do not guess EPSG:4326. |

## 5. 交付三态（disposition）

- `delivered_verified`：成果已交付且经真实读回核验：有落库输出、校验通过、后置检查全为真且无无法确认段
- `delivered_unverified`：成果已交付但核验不完整：只读结果、无独立读回、存在 unconfirmed 段或检查不完备
- `not_delivered`：声明的成果未交付：写入前失败、预演不落库、或交付中断，不得冒充已交付

## 6. 单次读取三态

`Known` / `Absent` / `Unknown`：读失败或文档畸形一律 `Unknown`，不得降级为 `Absent`。

## 7. 句柄解析

错误码：`STALE` / `AMBIGUOUS`　（0 命中 = `STALE`；>=2 命中 = `AMBIGUOUS`）

捕获证明按「角色 + 稳定字段」去重计数；值(value/values)、范围(range/ranges/bounds/extent)、计数(count/feature_count)等易变量不作身份。同一身份的多次观测计 1；0 个身份判 STALE，>=2 个身份判 AMBIGUOUS，恰好 1 个身份才解析为句柄。

## 8. 规则

| 规则 | 位置 | 含义 | 修复 |
|---|---|---|---|
| `commit_verification` | task commit, after real readback | Actual writes left the declared scope or a binding changed | Inspect details.out_of_scope; nothing outside the plan may be written |
| `operation_parameters_only` | task_plan / task_dry_run step parameters | Step parameters carry operation arguments only | Put request_id on task_plan and authorization_id on task_execute |
| `previous_dataset_output` | task_plan / task_dry_run step references | $previous references the immediately preceding dataset output | Use an existing handle for the first step; never a planned new target name |
| `published_surface_names` | MCP surface adapter | Only names published by the selected surface profile are accepted | Use the canonical name or the exact published surface name |
| `resume_dependency_invalidated` | task_execute resume gate | A committed step's dependency version is no longer valid | Re-plan; recovery is only allowed from still-valid results |
| `resume_state_not_recoverable` | task_execute resume gate | Only FAILED plans resume; ISOLATED means unknown external state | Inspect task_get and evidence, then declare a new plan |
| `verification_precheck` | before any side effect, in both dry-run and execute | A contract predicate refused the step; details.predicate names it | Read details.observation and details.details, correct the unit, object or version |
| `version_binding_changed` | task_execute commit gate | An input version or target state changed after planning | Re-plan against the current versions; the old plan is refused |
