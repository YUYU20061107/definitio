# Definitio D1–D5 横向推广（v04 rollout）自动化报告

- 版本目录：`.`
- 定名依据：现场编号 00-baseline…06-runtime-v03-golden-chain 已占用，续 07-runtime-v04-rollout；scripts/audit_g1.py 的默认 root=parents[2] 只有在该布局下才指向工作台根
- 源码摘要 source_digest：`87d73c5b1b39bca400eb6cb45111e05a8da23467fe040180a8cd0f09ef70230e`
- 运行时：0.2.0-dev｜shapely 2.1.2｜GEOS 3.13.1｜GDAL 3.12.2
- 文件总数：112

## G1 基线

- 状态：**PASS**；59/59 文件哈希不变
- 变化：无；缺失：无

## 发现与错误一致性（D5）

- 总体：PASS
- 不一致工具：无
- 未覆盖 recovery 码：无
- 未公布 rule：无
- prompt 不一致项：无

## 全量回归

- 总数 357｜PASS 347｜FAIL 10｜ERROR 0｜SKIP 0｜耗时 146.234s
- 新增测试：84（PASS 84 / FAIL 0）
- 既有测试：273（PASS 263 / FAIL 10）
- 证据：`evidence\regression-full-suite.xml`、`evidence\regression-full-suite.log`

| 文件 | 总数 | PASS | FAIL |
|---|---:|---:|---:|
| tests.test_agent | 33 | 33 | 0 |
| tests.test_completion | 23 | 23 | 0 |
| tests.test_golden_chain | 5 | 5 | 0 |
| tests.test_golden_commit | 11 | 11 | 0 |
| tests.test_golden_discovery | 6 | 6 | 0 |
| tests.test_golden_g1_guard | 2 | 2 | 0 |
| tests.test_golden_resume | 7 | 7 | 0 |
| tests.test_golden_semantics | 7 | 7 | 0 |
| tests.test_golden_verifier | 10 | 10 | 0 |
| tests.test_mcp | 11 | 11 | 0 |
| tests.test_native_completion | 37 | 27 | 10 |
| tests.test_pointcloud | 18 | 18 | 0 |
| tests.test_processing_channel | 4 | 4 | 0 |
| tests.test_rollout_commit_preview | 7 | 7 | 0 |
| tests.test_rollout_discovery | 5 | 5 | 0 |
| tests.test_rollout_inventory | 6 | 6 | 0 |
| tests.test_rollout_native_predicates | 18 | 18 | 0 |
| tests.test_rollout_planner | 11 | 11 | 0 |
| tests.test_rollout_predicates | 29 | 29 | 0 |
| tests.test_rollout_projection | 8 | 8 | 0 |
| tests.test_runtime | 64 | 64 | 0 |
| tests.test_surface | 13 | 13 | 0 |
| tests.test_usability | 11 | 11 | 0 |
| tests.test_workstation | 11 | 11 | 0 |

### 既有测试逐 ID 对账

- 状态：**PASS**
- 基线 273 ID（PASS 263） → 终局既有 273 ID（PASS 263）
- 判定变化：无
- 缺失 ID：无；多出 ID：无

### 失败分类

- 环境阻塞（既有，非本轮引入）：10
- 非预期失败：0
- 环境阻塞根因：缺 data/backends.json（本机 PostGIS/GeoServer 沙箱配置，含凭据，不随源码包）；STA 用例还需外部 istSOS 端点，违反本轮离线红线。completion.py:90 在读取配置处即返回 BACKEND_UNAVAILABLE，未发起任何网络请求。同 10 项在源工作站 D:/agent-native-gis 同样失败（10 FAIL / 27 PASS）。

### 失败逐项

- `tests.test_native_completion::test_tile_binary_http_download_decode_and_expiration` — FAIL — AssertionError: {'ok': False, 'status': 'error', 'interface_version': 'definitio-agent-v1', 'data': {'task': {'id': 'task_86fa360128ea...a795fad24cfa31e12cf257845fa942185862566d7a79f881128955bb', ...}
- `tests.test_native_completion::test_actual_sta_all_entity_sets_and_exact_odata_downpush[Things]` — FAIL — AssertionError: {'ok': False, 'status': 'error', 'interface_version': 'definitio-agent-v1', 'data': {'task': {'id': 'task_70982df1217e...643ad5fcd189913e8d461e612cd1ddd19399afd59ed720cdd201ea4d', ...}
- `tests.test_native_completion::test_actual_sta_all_entity_sets_and_exact_odata_downpush[Locations]` — FAIL — AssertionError: {'ok': False, 'status': 'error', 'interface_version': 'definitio-agent-v1', 'data': {'task': {'id': 'task_b3cb763470c3...7cff70a3f6bb3a55d907bc8ad106f1b3984ff75db46e8493508f9072', ...}
- `tests.test_native_completion::test_actual_sta_all_entity_sets_and_exact_odata_downpush[HistoricalLocations]` — FAIL — AssertionError: {'ok': False, 'status': 'error', 'interface_version': 'definitio-agent-v1', 'data': {'task': {'id': 'task_14c66f17c9cf...da987f7cf0aca19a67d6f527ddee30e91ea0c394f639893d73da0ac4', ...}
- `tests.test_native_completion::test_actual_sta_all_entity_sets_and_exact_odata_downpush[Datastreams]` — FAIL — AssertionError: {'ok': False, 'status': 'error', 'interface_version': 'definitio-agent-v1', 'data': {'task': {'id': 'task_6bcef6fc1859...254223d51ad631f3ff839a1516cf6c522d6326252773507a0303181d', ...}
- `tests.test_native_completion::test_actual_sta_all_entity_sets_and_exact_odata_downpush[Sensors]` — FAIL — AssertionError: {'ok': False, 'status': 'error', 'interface_version': 'definitio-agent-v1', 'data': {'task': {'id': 'task_6f8bf15468d7...b5bd2628460d8ae813d86450e17f85cf67b1e4da91ee22f1a7fec433', ...}
- `tests.test_native_completion::test_actual_sta_all_entity_sets_and_exact_odata_downpush[ObservedProperties]` — FAIL — AssertionError: {'ok': False, 'status': 'error', 'interface_version': 'definitio-agent-v1', 'data': {'task': {'id': 'task_1ac9f9babc97...86e97f1046ae904afd07b1bdaa8e8ae2d315564740d229bc285f9bd1', ...}
- `tests.test_native_completion::test_actual_sta_all_entity_sets_and_exact_odata_downpush[Observations]` — FAIL — AssertionError: {'ok': False, 'status': 'error', 'interface_version': 'definitio-agent-v1', 'data': {'task': {'id': 'task_6d7ec6af2d36...c3d10ff2c9859f9783da69dae809efd71e9d525c5e9ee2ee2e1e3d7b', ...}
- `tests.test_native_completion::test_actual_sta_all_entity_sets_and_exact_odata_downpush[FeaturesOfInterest]` — FAIL — AssertionError: {'ok': False, 'status': 'error', 'interface_version': 'definitio-agent-v1', 'data': {'task': {'id': 'task_48e40ba826d4...53910d301aebd8b9a40ae38ca6247bbd2e00f80ff9ab05dc550fa121', ...}
- `tests.test_native_completion::test_actual_services_filter_and_unknown_service_rejection` — FAIL — AssertionError: {'ok': False, 'status': 'error', 'interface_version': 'definitio-agent-v1', 'data': {'task': {'id': 'task_0bb6e39efe96...4be07d91fee72fd6482ba6cf1f41c2764675c07f3456db569aa33e25', ...}

## 五类验收

| 类别 | 测试 | 状态 |
|---|---|---|
| A1_normal_end_to_end_and_independent_readback | `test_a1_end_to_end_and_independent_readback_matches_truth` | PASS |
| A2_unit_object_version_errors_named | `test_a2_unit_object_and_version_errors_are_each_named` | PASS |
| A3_state_change_does_not_authorize_old_plan | `test_a3_state_change_after_checks_does_not_authorize_the_old_plan` | PASS |
| A4_midchain_failure_recovers | `test_a4_midchain_failure_recovers_from_valid_state` | PASS |
| A5_retry_has_no_duplicate_or_out_of_scope_effects | `test_a5_commit_and_export_retries_have_no_duplicate_or_out_of_scope_effects` | PASS |

## 本轮推广验收（R1/R3/R4/R5/清单）

| 类别 | 测试 | 状态 |
|---|---|---|
| R1_three_surface_projection_consistency | `test_three_surfaces_emit_the_identical_projection_for_vector` | PASS |
| R1_geographic_axis_order_survives | `test_geographic_axis_order_survives_all_three_surfaces` | PASS |
| R3_dry_run_execute_same_checks | `test_preview_and_execute_run_the_same_invariant_checks` | PASS |
| R3_preview_commit_verify_same_predicate_objects | `test_preview_execute_verify_share_predicate_objects` | PASS |
| R4_full_60_tool_zero_inconsistency | `test_audit_covers_all_60_tools_with_zero_inconsistency` | PASS |
| R5_spatial_join_intent | `test_spatial_join_intent_branch` | PASS |
| R5_import_intent | `test_import_intent_branch_first_step` | PASS |
| inventory_40_verbs_no_blanks | `test_registry_covers_exactly_the_40_frozen_verbs` | PASS |

## 逐动词谓词清单（40 动词，无空白）

- 共享基础检查（矢量提案）：schema_readback, crs_readback
- 契约谓词（全动词）：distance_unit_compatibility, input_version_binding, spatial_join_contract, output_reopenable, write_scope
- 执行策略检查：input_unchanged, explicit_destruction, output_readback
- 专属检查项合计：111；仅基础检查动词：2

| 动词 | 专属检查项（方法｜依据｜层） |
|---|---|
| gis_add_field | `add_field_schema_append`（schema_readback｜schema 追加精确比对｜commit+preview+verify）<br>`add_field_count_unchanged`（count_readback｜计数精确｜commit+preview+verify）<br>`add_field_default_values`（constructed_default_truth｜构造真值：每条新字段==声明 default｜commit+preview+verify）<br>`add_field_other_fields_unchanged`（properties_readback｜其余属性逐条精确｜commit+preview+verify） |
| gis_add_layer_from_catalog | `native_resource_readback`（native_backend_and_sqlite_sha256｜资源记录重开指纹一致｜commit+verify）<br>`resource_map_layers_accounting`（resource_layer_binding_readback｜layers 恰增 1 且 dataset_ref/catalog_id/license_ref 绑定目录证据｜commit+preview+verify） |
| gis_append_dataset | `append_count_readback`（count_sum｜计数求和精确｜commit+preview）<br>`append_multiset_readback`（sorted_multiset_logical_sha256｜目标+源拼接多重集指纹｜commit+preview）<br>`append_version_accounting`（proposal_count_readback｜before/added/after 计数对账；复核无提案降级无法确认｜commit+preview） |
| gis_aspect | `grid_and_pixels_readback`（sqlite_exact_raster_snapshot｜精确读回｜commit+preview+verify）<br>`raster_nodata_accounting`（pixel_count_accounting｜valid+nodata==w*h｜commit+preview+verify）<br>`raster_grid_alignment`（grid_property_exact_comparison｜网格==输入｜commit+preview+verify）<br>`raster_nodata_mask_semantics`（horn_3x3_mask_exact｜掩膜==边界＋3x3 含 NoData＋平坦像元（flat_policy=nodata）｜commit+preview+verify）<br>`raster_pixel_spot`（same_formula_independent_horn_recompute_float32_margin｜aspect=atan2(-dzdx,dzdy)%360，环差容差 1e-4 度；float32 舍入（同公式自洽）｜commit+preview+verify） |
| gis_assign_projection | `assign_projection_coordinates_unchanged`（normalized_wkb_multiset_exact｜normalize-WKB 多重集精确相等（坐标不动）｜commit+preview+verify） |
| gis_buffer | `buffer_geometry_valid`（same_kernel_geos_is_valid｜GEOS 有效性（同核自洽）｜commit+preview+verify）<br>`buffer_area_upper_bound`（analytic_parallel_body_upper_bound｜解析界 A+2Lr+πr²（Steiner 平行体上界；线要素精确主项 2Lr+πr²）；quad_segs=8 内接近似只更小；相对松弛 1e-9｜commit+preview+verify）<br>`buffer_area_lower_bound`（containment_exact｜r>0 包含关系 area≥A；r<0 侵蚀 0≤area≤A；精确｜commit+preview+verify）<br>`buffer_count_semantics`（count_and_schema_set_comparison｜dissolve=false 计数==输入且属性逐条保留；true 非空→1 条且 schema 空｜commit+preview+verify） |
| gis_calculate_field | `calculate_field_recompute`（same_kernel_safe_expression_recompute｜同核安全表达式逐条复算（自洽性）｜commit+preview+verify）<br>`calculate_field_other_unchanged`（count_and_schema_readback｜计数/schema 精确｜commit+preview+verify） |
| gis_clip | `attributes_retained`（subsequence_properties_readback｜输出属性为输入子序列（精确）｜commit+preview+verify）<br>`count_bound`（count_set_comparison｜输出计数≤输入计数｜commit+preview+verify）<br>`subset_area_bound`（containment_area_exact｜逐条面积≤对应输入面积（包含关系，相对松弛 1e-9）｜commit+preview+verify）<br>`subset_within_input`（same_kernel_geos_covers｜同核 covers 子集性（自洽）｜commit+preview+verify） |
| gis_convert_format | `copy_logical_content`（logical_fingerprint_equality｜逻辑指纹相等｜commit+preview） |
| gis_copy_dataset | `copy_logical_content`（logical_fingerprint_equality｜逻辑指纹相等（排序多重集）｜commit+preview） |
| gis_create_dataset | `create_empty_contract`（empty_and_schema_readback｜空数据集与声明 schema 的精确读回｜commit+preview+verify） |
| gis_delete_dataset | `dataset_absent_readback`（database_readback_absence｜销毁后按 ID 重开必须 NOT_FOUND（真实缺席读回）；预演降级无法确认｜commit+verify） |
| gis_delete_field | `removed_fields_absent`（schema_readback｜被删字段不在输出｜commit+preview）<br>`delete_field_count`（count_readback｜计数不变｜commit+preview）<br>`delete_field_values_unchanged`（properties_readback｜保留字段值逐条精确｜commit+preview+verify） |
| gis_describe_algorithm | 仅基础检查＋只读能力定义：结果即目录条目，无数据对象；契约谓词仍跑，算子读回不适用 |
| gis_dissolve | `dissolve_fields_only`（schema_readback｜仅保留分组字段｜commit+preview）<br>`unique_output_group`（group_key_uniqueness｜每组唯一｜commit+preview）<br>`group_key_set_readback`（group_key_set_comparison｜组键集==输入分组｜commit+preview）<br>`group_count_readback`（count_readback｜组计数三方一致｜commit+preview）<br>`multipolygon_per_group`（geometry_type_readback｜每组 MultiPolygon｜commit+preview）<br>`group_geometry_readback`（same_kernel_geos_topological_equals｜同核 union_all 拓扑相等（自洽）｜commit+preview）<br>`manifest_group_keys`（manifest_key_set｜清单组键唯一且完备｜commit+preview）<br>`manifest_membership`（manifest_membership_readback｜清单成员==源记录分组｜commit+preview） |
| gis_export_map_image | `native_resource_readback`（native_backend_and_sqlite_sha256｜资源记录重开指纹一致｜commit+verify）<br>`resource_image_integrity`（sha256_bytes_and_map_property_readback｜base64 解码 sha256==file_sha256；width/height==地图；dpi==请求｜commit+preview+verify） |
| gis_field_statistics | `stats_recompute`（same_kernel_pure_python_recompute｜纯 Python 逐组复算；count/sum/min/max 精确，mean 相对 1e-12（同核自洽）｜commit+preview(result)）<br>`stats_group_set`（group_key_set_comparison｜组数==输入分组数｜commit+preview(result)）<br>`stats_input_count`（count_readback｜input_feature_count 精确｜commit+preview(result)）<br>`stats_null_accounting`（null_count_recompute｜null_count 逐组复算精确｜commit+preview(result)） |
| gis_hillshade | `grid_and_pixels_readback`（sqlite_exact_raster_snapshot｜精确读回｜commit+preview+verify）<br>`raster_nodata_accounting`（pixel_count_accounting｜valid+nodata==w*h｜commit+preview+verify）<br>`raster_grid_alignment`（grid_property_exact_comparison｜网格==输入｜commit+preview+verify）<br>`raster_nodata_mask_semantics`（horn_3x3_mask_exact｜掩膜==边界＋3x3 含 NoData｜commit+preview+verify）<br>`raster_pixel_spot`（same_formula_independent_horn_recompute_float32_margin｜独立 Horn 光照公式复算；uint8 绝对容差±1（GDAL traditional 1..255 舍入，同公式自洽）｜commit+preview+verify） |
| gis_import_data | `import_license_attestation`（caller_attestation_readback｜allow_import=true 且 evidence_ref 非空（调用方声明，不推断外部许可）｜commit+preview+verify）<br>`import_source_fingerprint_binding`（source_document_sha256｜源文档 sha256==提案 source_fingerprint；复核无提案降级无法确认｜commit+preview）<br>`import_content_fingerprint`（source_to_output_logical_sha256｜输出指纹==源文档验证后的逻辑指纹（构造真值）｜commit+preview+verify） |
| gis_list_algorithms | 仅基础检查＋只读能力目录：结果即目录本身，无数据对象；契约谓词仍跑，算子读回不适用 |
| gis_list_services | `services_receipt_structure`（capability_receipt_readback｜service_count==len(services)、response_sha256 存在；外部能力文档无本地真值｜commit+preview(result)） |
| gis_measure | `measure_recompute`（same_kernel_shapely_recompute｜同核复算，相对 1e-12（自洽）｜commit+preview(result)）<br>`measure_unit_label`（declared_unit_readback｜area→m²、length/distance→m、projected_planar｜commit+preview(result)） |
| gis_merge_datasets | `merge_count_readback`（count_sum｜计数求和精确｜commit+preview）<br>`merge_fingerprint_multiset`（sorted_multiset_logical_sha256｜输入拼接的排序多重集逻辑指纹==输出指纹｜commit+preview+verify） |
| gis_overlay | `attributes_retained`（subsequence_properties_readback｜输出属性为输入子序列（精确）｜commit+preview+verify）<br>`count_bound`（count_set_comparison｜输出计数≤输入计数｜commit+preview+verify）<br>`subset_area_bound`（containment_area_exact｜intersection/difference 均⊆输入，面积界精确｜commit+preview+verify）<br>`subset_within_input`（same_kernel_geos_covers｜同核 covers 子集性（自洽）｜commit+preview+verify） |
| gis_pointcloud_info | `pc_info_count_matches`（snapshot_count_readback｜result.point_count==输入快照｜commit+preview(result)）<br>`pc_info_dimensions_match`（snapshot_dimensions_readback｜维度列表==输入快照｜commit+preview(result)）<br>`pc_info_fingerprint_binding`（snapshot_fingerprint_readback｜input_fingerprint==输入指纹｜commit+preview(result)） |
| gis_pointcloud_process | `pointcloud_bytes_readback`（sqlite_exact_bytes_and_metadata｜持久字节==输出字节（精确）｜commit+preview+verify）<br>`pointcloud_dimensions_preserved`（dimension_list_readback｜维度保真（算子已强制，验证器复核）｜commit+preview+verify）<br>`pointcloud_count_accounting`（constructed_action_truth｜merge=输入和；thin_decimate=len(range(offset,n,step)) 构造真值；reproject/convert 守恒；clip/thin_radius≤输入（PDAL 语义上界）｜commit+preview+verify）<br>`pointcloud_fingerprint_recomputed`（byte_sha256_binding｜字节 sha256 重算绑定指纹｜commit+preview+verify）<br>`pc_boundary_vector_contract`（declared_addendum_contract_readback｜boundary 动作：schema 空、CRS 保留（本地附录声明）｜commit+preview+verify） |
| gis_publish_layer | `publish_readback_receipt`（wfs_readback_receipt_reconciliation｜WFS 回读指纹==源指纹==输入指纹、计数对账、catalog_writeback=pending；后端环境阻塞时以合成回执做谓词级验证｜commit(result)） |
| gis_query_observations | `sta_receipt_structure`（odata_receipt_readback｜returned_count==len(data)、无本地过滤/拼页、response_sha256 存在｜commit+preview(result)） |
| gis_raster_calc | `grid_and_pixels_readback`（sqlite_exact_raster_snapshot｜持久栅格==输出栅格（精确）｜commit+preview+verify）<br>`raster_nodata_accounting`（pixel_count_accounting｜valid+nodata==w*h｜commit+preview+verify）<br>`raster_grid_alignment`（grid_property_exact_comparison｜transform/宽/高==全部输入（无重采样）｜commit+preview+verify）<br>`raster_nodata_mask_semantics`（nodata_union_exact｜输出掩膜==输入掩膜并集（精确）｜commit+preview+verify）<br>`raster_pixel_spot`（same_grammar_independent_arithmetic_recompute｜独立 AST 求值器按公布文法复算≤16 像元，相对 1e-9（同文法自洽）｜commit+preview+verify） |
| gis_reclassify | `grid_and_pixels_readback`（sqlite_exact_raster_snapshot｜精确读回｜commit+preview+verify）<br>`raster_nodata_accounting`（pixel_count_accounting｜valid+nodata==w*h｜commit+preview+verify）<br>`raster_grid_alignment`（grid_property_exact_comparison｜网格==输入｜commit+preview+verify）<br>`raster_nodata_mask_semantics`（nodata_propagation_exact｜输入 null 保持 null｜commit+preview+verify）<br>`raster_pixel_spot`（constructed_rule_mapping_truth｜按声明区间规则构造真值，精确映射（含 unmapped keep/nodata）｜commit+preview+verify） |
| gis_rename_dataset | `rename_content_unchanged`（fingerprint_equality｜内容指纹==输入（改名不动数据）｜commit+preview+verify）<br>`rename_head_resolves`（head_resolution_readback｜新名解析到同一 ID；预演降级无法确认｜commit+verify）<br>`rename_old_name_gone`（database_readback_absence｜旧名不再解析；预演降级无法确认｜commit+verify） |
| gis_reproject | `reproject_count_unchanged`（count_readback｜计数精确｜commit+preview+verify）<br>`reproject_attributes_unchanged`（properties_readback｜属性逐条精确｜commit+preview+verify）<br>`reproject_roundtrip_spot`（same_kernel_proj_roundtrip｜同核 PROJ 往返抽查≤8记录；绝对容差 1e-6×max(1,|坐标|)，同核浮点噪声，非独立 oracle｜commit+preview+verify）<br>`reproject_bounds_consistency`（same_kernel_transformed_bounds｜输入坐标≤4096 全量变换比对边界，容差 1e-6×跨度；超限降级无法确认｜commit+preview+verify） |
| gis_run_gp | `gp_count_readback`（native_count_readback｜ArcPy GetCount==输入 feature_count（精确）；后端环境阻塞时以合成回执做谓词级验证｜commit+preview(result)） |
| gis_run_processing | `processing_centroid_semantics`（declared_seed_semantics_readback｜native:centroids 种子：ALL_PARTS 时计数==部件数否则==要素数；几何全 Point；schema 保留｜commit+preview+verify） |
| gis_serve_vector_tile | `tile_handle_metadata`（handle_metadata_readback｜sha256 64hex、byte_length>0、expires=300；字节重算不在上下文→无法确认段｜commit+preview(result)） |
| gis_slope | `grid_and_pixels_readback`（sqlite_exact_raster_snapshot｜精确读回｜commit+preview+verify）<br>`raster_nodata_accounting`（pixel_count_accounting｜valid+nodata==w*h｜commit+preview+verify）<br>`raster_grid_alignment`（grid_property_exact_comparison｜网格==输入｜commit+preview+verify）<br>`raster_nodata_mask_semantics`（horn_3x3_mask_exact｜掩膜==边界＋3x3 含 NoData（Horn 声明语义）｜commit+preview+verify）<br>`raster_pixel_spot`（same_formula_independent_horn_recompute_float32_margin｜独立 numpy Horn 复算；slope=atan(z_factor·√(dzdx²+dzdy²))；float32 相对容差 1e-6（同公式自洽，非独立 oracle）｜commit+preview+verify） |
| gis_spatial_filter | `filter_count_matches`（same_kernel_predicate_recount｜同核谓词重数（自洽）｜commit+preview(result)）<br>`filter_subset_records`（whole_record_subset_readback｜整记录子集（属性+几何精确）｜commit+preview(result)）<br>`filter_predicate_recheck`（same_kernel_geos_predicate｜同核谓词复检（自洽）｜commit+preview(result)） |
| gis_spatial_join | `join_count_accounting`（same_kernel_predicate_recount｜同核逐对复算 matched/unmatched 与输出行数（≤20000 对，超限降级）｜commit+preview+verify）<br>`join_proposal_counts`（same_kernel_predicate_recount｜提案 matched/unmatched==复算值｜commit+preview）<br>`join_left_outer_nulls`（left_outer_null_semantics_readback｜summary=first 未匹配行连接字段全 null｜commit+preview+verify） |
| gis_validate_geometry | `validate_recheck`（same_kernel_geos_validity_rescan｜独立 is_valid 扫描比对 issues（同核自洽）｜commit+preview(result)）<br>`validate_count`（count_readback｜checked==feature_count｜commit+preview(result)）<br>`validate_flag_consistency`（flag_consistency｜valid==无 issues｜commit+preview(result)） |
| gis_zonal_statistics | `zonal_zone_count`（count_and_table_readback｜行数==zones 计数；几何全 null（表）｜commit+preview+verify）<br>`zonal_table_schema`（schema_readback｜zone_field+statistics 精确｜commit+preview+verify）<br>`zonal_pixel_accounting`（same_kernel_pixel_center_mask_recount｜同核 geometry_mask 中心规则复算 pixel/valid/nodata 计数（精确）｜commit+preview+verify）<br>`zonal_stats_recompute`（same_kernel_numpy_recompute｜numpy 复算 sum/mean/min/max，相对 1e-9（同核自洽）｜commit+preview+verify） |

## 变更清单

- 基线提交：`c6d41ba`
- 新增（16）：docs/ROLLOUT-DELIVERY-20261114.md, docs/superpowers/plans/2026-11-14-rollout.md, evidence/baseline-full-suite.log, evidence/baseline-full-suite.xml, evidence/golden-chain-report.json, evidence/golden-chain-report.md, evidence/regression-full-suite.log, evidence/regression-full-suite.xml, evidence/report-run.log, tests/test_rollout_commit_preview.py, tests/test_rollout_discovery.py, tests/test_rollout_inventory.py, tests/test_rollout_native_predicates.py, tests/test_rollout_planner.py, tests/test_rollout_predicates.py, tests/test_rollout_projection.py
- 修改（11）：definitio/agent.py, definitio/commit.py, definitio/completion.py, definitio/discovery.py, definitio/planner.py, definitio/preview.py, definitio/runtime.py, definitio/semantics.py, definitio/server.py, definitio/verifier.py, scripts/golden_chain_report.py
- 未跟踪（1）：evidence/report-reuse.log

## 已知限制

- 10 项 test_native_completion 依赖本机 PostGIS/GeoServer 沙箱配置与外部 istSOS 端点；本轮离线红线不启动服务、不访问外网，保留红灯并附开工前基线同样失败的对照
- 干净 venv 安装未验证；本轮显式复用 D:/agent-native-gis/.venv（system-site-packages=true）
- 真实模型评测未做；引擎未切版，8765/8876 未动
- 同核复算与 GEOS/PROJ/GDAL/PDAL 同源，是自洽性检查，不是独立几何 oracle，不证明跨后端等价或校准容差
- buffer 面积上界用平行体解析界 A+2Lr+πr²（相对松弛 1e-9）；下界用包含关系；点缓冲精确真值仍为正 32 边形（A1）
- reproject 往返容差 1e-6×max(1,|坐标|) 为同核 PROJ 浮点噪声界；坐标超 4096 时边界一致性降级无法确认
- 地形像元抽查为独立 numpy Horn 实现复算，float32 相对容差 1e-6、hillshade uint8 ±1、aspect 环差 1e-4 度
- 服务动词（publish/serve_tile/list_services/query_observations/run_gp）后端环境阻塞：谓词以合成回执做谓词级验证，未声称为后端实测证据
- tile 字节级重算不在验证上下文（二进制句柄属引擎进程），如实列入无法确认段
- 文件产物只开放 geojson；轴序解析只覆盖 WKT2 AXIS 节点；planner 目标句仍限 1–1000 字（导入长 URI 须走 task_plan 显式步骤）
- HTTP 直传导入路径保持第一轮冻结语义（lineage.operation=None，溯源在 source.kind）；控制面导入已补谱系

## 文件清单与哈希

| 文件 | 字节 | sha256 |
|---|---:|---|
| .gitattributes | 8 | `705fd4d6451a31d36b3df7de96f83f30ac976c9b4a6d1e51671d8e2f33e2d0da` |
| .gitignore | 47 | `d91b3ccfe667ac2722184e0d87c10fd070b32f53100e600b3b8f53bd79c62889` |
| AGENT.md | 8086 | `6b8864af312fc1027f8496cdba901bcd7b293b8732407b9892d252b7bbedccb2` |
| README.md | 3299 | `6946640fabe7f085f850c1b770bec05293c6341c99571812c802e830f4264ea4` |
| definitio/__init__.py | 88 | `38a30dbf11f3915341f759ad68bd9ee383a7f211c136b51c3994e954b805034b` |
| definitio/agent.py | 57659 | `2a97e6bc7f77f5837a49ee7b614ae8cc6d75af321de5e3b836fd3ca7b84a5332` |
| definitio/authorization.py | 5478 | `383d872b42e22b1eb7c2a325fd6c88d358793c5bb10dfb299a67cbcf1d8ac361` |
| definitio/catalog.json | 4090 | `e93d19ca12b88eb3173f93772fc26f775c379aa03f6c3eb1f66794d2ecb8a2da` |
| definitio/commit.py | 9913 | `94ba02baf08f74569b2034be82ecdedaf00405e838c02b598a0fb9bb96ea3234` |
| definitio/completion.py | 25386 | `b1a5954d7e14f05ade2e1c074b5ef717bc8421d1f552ee4118e55c07e84da4cb` |
| definitio/demo.py | 1273 | `8b0a77b6344eef49b93ea18f716d563e682fabecc3f881e80e6d69c026a5ff05` |
| definitio/discovery.py | 6539 | `59f26299ea7851ea35e8d9a9aa638145040cbfc76c688f8f44bc8290979be2f3` |
| definitio/environment.py | 8209 | `bf105ea2bc995bc7834d8918b8628edabfaf93c4c4e28497543b8fd0c5f9a16b` |
| definitio/gdal_dem.py | 3264 | `146861799ac237128e636c955ba1de6d4c56f9974670d37f6acaaf3b0f55ba0c` |
| definitio/golden.py | 5267 | `ec73b456695435e780ce64d00f6db70cfcb7315906c67d81e3204e6ec72ad0d1` |
| definitio/guidance.py | 16537 | `b6760609dff4e3a41d13d423d0a911892f6f63dc57bc693a44ed2ba227f2fbee` |
| definitio/lifecycle.py | 4423 | `56c61579169083dd28bbe41e9c2da92289817b4d90f92c7c5b18d52299b8afde` |
| definitio/mcp_server.py | 9445 | `6c618966469cdfd6a767c8a8016bf29ac17c20b3a6f9ef99178b14942c503fe9` |
| definitio/model.py | 9434 | `debb38b3dc1f07b05cb878b282b224fb01c038efbf3b8b7257f39132a7c085d2` |
| definitio/operations.py | 21392 | `4e811111aa801d298aa67a9db452e05e37641a2bceeb2f3596dd75d62f3cad59` |
| definitio/planner.py | 6048 | `cbcdadcfdad54ecfe8b47a393d362f6603993ce341d977d6d28d4f6078249223` |
| definitio/pointcloud.py | 14289 | `2a833cc1166006bb7e6a8768ed105893e07f9ea7a258b7ad365d7abaabc0eca5` |
| definitio/preview.py | 9036 | `46b94c15169fab51a0c7a27d490ec260108de829ce3b1b9d7a4bc6ab7910f018` |
| definitio/processing.py | 3101 | `1b3775f90f82c284b3a9c18cddf0b6910197db85de77e0d81a74a7cca3b7247e` |
| definitio/raster.py | 13044 | `c8a755c67288ed139f1d5fabf6312f77061912aa85969d676c7853235fd80985` |
| definitio/resume.py | 3308 | `f082e0d219f070af04c6f6f9d8245ee27206e5de85124b74ea72f84d7d26a09c` |
| definitio/runtime.py | 40600 | `05f1776a84849907334f2bd6073b203a4fb56a3b03dd2a8753ae1299aec15c04` |
| definitio/schemas.py | 12751 | `9dc60780d1fd856790984cd078b3a8fc236f2c7fc94970ecd6c54fd4c4ddf5aa` |
| definitio/semantics.py | 8576 | `523a1aef9a7ca939af3f2974cad4dcf001bac644debd05cd95a2e8c27ee62c9e` |
| definitio/server.py | 12332 | `ff96a1d6a0546abde9ef137dce6be043c496b16f846f93269adf7703eb7e684e` |
| definitio/store.py | 15088 | `437acb37f8ca23c8f8af88185f0ce96e2d7b1fbbb6b338a6bb7f9e119e222345` |
| definitio/surface.py | 10889 | `5d1a59f78b623997d496efb0e94ae37be479e4ddb0985de293dee0e7b7b28225` |
| definitio/surfaces/surface-v1.json | 17403 | `2d49a1df1d4d946cdc31e5e3a5968c71166320a2bda274752e4fd0ee90e7e35c` |
| definitio/vector_extra.py | 8383 | `8490ea6fb2e285cf637163201448fc9ee7b8c95fd41ca17b371a68452da66229` |
| definitio/verification.py | 950 | `2d88a72f251a2f516cd2519a0a1c550be2f091bc79f73bb128254fae539e59e9` |
| definitio/verifier.py | 79255 | `f5961e896995205f649d88673b95f7dd4b1975c10e44983cd0d15f97d49c69ff` |
| docs/GOLDEN-CHAIN-DELIVERY-20261113.md | 25441 | `740d2b5db39ca035cd3b73c0175b94696c196a828e996b92de31611fcb1efb8a` |
| docs/ROLLOUT-DELIVERY-20261114.md | 20540 | `d40ae9a0a565c24a77e8be5fc2bb5a15829798b9619cce91d0584dcb01d1e0ae` |
| docs/superpowers/plans/2026-11-13-golden-chain.md | 112583 | `667c7405768fb2dfe26a908245fd229a9854358fd1aa57486399ed3a7410540b` |
| docs/superpowers/plans/2026-11-14-rollout.md | 40908 | `2e4b4391af51f5184283ab3e73dc7d10124f3c869a3ed07f9fc66b24a1fc3992` |
| evidence/a3-a6-20261113/g0/frozen.json | 9430 | `21fac5a553897ce72d7ea8a6494c1c2d335e91e75c1bc7d11f662678a960746a` |
| evidence/a3-a6-20261113/g0/predictions.json | 938 | `f77ab76fd4cc0a6e5571cb3f468dd5cff5677047aa4eb88206e6b2f7025c0f94` |
| evidence/a3-a6-20261113/g0/protocol.json | 3076 | `525bd5413647bc3fc9574b77344e0c5b8aa0627a540ad7a9f339695486f01efc` |
| evidence/a3-a6-20261113/g0/tasks.json | 42594 | `5aedd8cc1bcb5492b4a958c28463440019feb41698cf33cbcf8410770855bb9b` |
| evidence/baseline-full-suite.log | 17012 | `6f4888da2d312d1e561c5afb8624eaeaa1d69fe21087e04aaa85a1daee666eae` |
| evidence/baseline-full-suite.xml | 51065 | `c2a2c8bab0009f2aa13c5c0ae30c418eb27f0827c843e118b340ca92e05f8cad` |
| evidence/golden-chain-report.json | 201308 | `3173261783092da9c9f7637482116c9a52f5e1d1bbe0ae919ef37658c266166c` |
| evidence/golden-chain-report.md | 38272 | `982e14f2ec6e765fd2b0dec3523448d5c961f53dcd0179ad9c16401ef65ff696` |
| evidence/regression-full-suite.log | 17106 | `697a5ca3f51c8edcecfa81eb01c23ba61bf5a76e7c538f93c816d7571dbded4e` |
| evidence/regression-full-suite.xml | 61128 | `5dc1d72128f49165ffc0b642612c05f1e35610dae5935030807cd37f1cbe7fe7` |
| evidence/report-reuse.log | 0 | `e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855` |
| evidence/report-run.log | 4169 | `1e52f155c2fbdaebff98929e253cefb96297a1974dcdf8eec2c1cf0257187b44` |
| examples/analytic-dissolve.geojson | 3041 | `2450e119e9959b8338ba31818cbe099048be2f8a1d53da678573e0629ebc6268` |
| examples/pdal-grid.las | 3347 | `c674843b5ffa87c1b6a5331dd951f7258303ac3172ba1ef1fbd3b0ed885d017b` |
| requirements-core.txt | 136 | `6411b9488f352fa56580cebef79975c518bd175e29fbcfe8d7085d798b882a26` |
| requirements.txt | 39 | `31c823f6bed872aff4e3d8980d8bfd95d5308d5ec73f9540f19e013620e912d4` |
| run_engine.py | 877 | `2423906e8a89155e6c129c3a974004abaa0aa3921318e05a97244647a000a874` |
| run_mcp.py | 252 | `bfbb849bef97803588fafeaedd887ca5af1e9848e7c14d4e7320b60928607b14` |
| scripts/agent_mcp_smoke.py | 9185 | `4c7d79756fdb139dcc5aa6f608a1e510d6cd7d3e88bb74821469754efd1eb4bc` |
| scripts/audit_g1.py | 6790 | `6d322fb357d7839117e64495c75a9d38d5105c42303b003bb7742d3d4f3cc628` |
| scripts/bootstrap_local_backends.py | 5630 | `5b4b3e8909f695c0f754a2e5319eab1fff5f22d36f9b47a34e4c1e2b067e900f` |
| scripts/completion_matrix.py | 11851 | `28e084ac4ad36e0c2f037fd47a0181a4a9cea0adabf4ec49338ad4b3fc818dc1` |
| scripts/completion_reference.py | 14217 | `294a77af04bf2a982382b6736983e6aeeba5f149d99cac4bf7412c7de72d7c52` |
| scripts/environment_doctor.py | 969 | `f417ae2477f64e0e79ee7a27d92e4834ba3c697e3fdbd3c29ecb2ea5562f67dd` |
| scripts/gis_equivalence_matrix.py | 10024 | `921dcb2c090c67278c37e534b22a0e021cc018b7c0c7de5a5f7669a6f4a0d846` |
| scripts/golden_chain_report.py | 21462 | `e580299d7fd947232e169e4c6ae8d5e68f32194dc4cdfd4617cc3e5dcc6a0b06` |
| scripts/measure_cold_start.py | 3779 | `fe192ab809bd077e804dbab0aa10351c4099bc7b71577c510d4699c7bd344feb` |
| scripts/model_evaluation.py | 28459 | `c3b652cee76c9dfc0f4be106670e7f9d32f02b44ceb5d8184a89d7adf2af43d7` |
| scripts/native_gis_worker.py | 10656 | `8b5b2b201066048103dff06b750c260a095569bf54a832926f355d27ac316fef` |
| scripts/package_release.py | 5049 | `1cafe1bf34483828d69d9e55db2723a2826f4047ff5ff26cfd53d3b7c22a9781` |
| scripts/prepare_a3_a6.py | 16003 | `7bbe401547bd1d18633237ffa1b43ae682eb6add2dc23a66c526f5656bda114e` |
| scripts/prepare_local_upgrade.py | 3425 | `fad1a960056289680ae67eef048875062cd5e65a0a3a5acec1cd76e12cf49b1b` |
| scripts/processing_matrix.py | 2512 | `12df54bc01c6b87cfd1f61e689399643ffa0a0ed41e38fd0674e44a05ee625d3` |
| scripts/qgis_matrix_reference.py | 13366 | `9686b6d02986df3f82f52ac745ce89e3cc7c338b2a96e5d72b88ac795670058a` |
| scripts/qgis_processing_worker.py | 1657 | `31f264bec80c4208ce4f50bff30c33c8f25f36a2d4e5f554caf073d2013315b6` |
| scripts/report_a3_a6.py | 6193 | `50b9df63ab9ab4721634aadf70b54261477ffffb0b740c5c291b865263499e79` |
| scripts/round4_evidence_matrix.py | 12084 | `dddd64b5ee99577fa79ceffd0c9159f264ff338ecf14bd1f5d7fbcc4f67b81c3` |
| scripts/round4_native_smoke.py | 6181 | `57ef0a90dbcd261434abc8bd5e2c18c5993e780b86f5415aaca71829379a9606` |
| scripts/round4_package_roundtrip.py | 8496 | `7fb2a4ba371389f63f691628f1ddaf5a0923b9ecb41fbb7837a317e242152a82` |
| scripts/round4_upgrade.py | 6833 | `f1f4fad76c23bb2aa2cf5d6a06208b42840672ffa2596c21b04303ed807c693f` |
| scripts/verify_bundle.py | 4420 | `5cc5c2d4ce69b742859413ca2acf2ca059c944f436f8108f79c020135dd1c0d1` |
| scripts/w3_matrix.py | 4764 | `1603981f4c0a1d08b011c5e2e5c4c5503d88fd210cbb96a62dba7eca768920db` |
| scripts/w3_probe.py | 1873 | `8c28a2614d940d6de07ae227bddd71bf23b18dce3fbed4c9fcdec5c2bed84e42` |
| scripts/w3_reference.py | 7759 | `561571214f0a37276cd98c853e03078671958eba3cb1c0e81533e70e8e8eb7d3` |
| tests/test_agent.py | 14086 | `53ed98a4bd2c3fabdf9dda53b9b4754f1d002d1545768ead8ef69fbc0620e149` |
| tests/test_completion.py | 15172 | `8370ad36a224705a0aa1926b6417590961b7e689e5860f171e8e0c3aaf045197` |
| tests/test_golden_chain.py | 10131 | `aa9c880485ce97580206eaee609d136767ddc10b9bbd1f5a2a0d65bd2e82f1ab` |
| tests/test_golden_commit.py | 8292 | `c71ab70b0877da964daf676be89ea3ee17f0778b8756f5ec9326e98265e0aaca` |
| tests/test_golden_discovery.py | 4794 | `e2d7421031b2def40aef5e67df950a3cc127bf72c7f406ae5d39ebe84be47098` |
| tests/test_golden_g1_guard.py | 1036 | `e72a99d3187cbb1d92959521577e5987e88efa7576f0bb951330a357db55866c` |
| tests/test_golden_resume.py | 6389 | `7924f3fa365c47821e999623e394ce4e6fcb859e42b80f3112f85a809c891a24` |
| tests/test_golden_semantics.py | 4445 | `a4d33e0f331f30386287793a11306896a4c128a684f7a6dcf6dcfc2d7a79dfd9` |
| tests/test_golden_verifier.py | 9385 | `5b03642ce414590675ff0f7e4f46aa257dc3bc1c456fa0d5448393553842cf65` |
| tests/test_mcp.py | 10369 | `8d0f1b47b73fdc862fde4c6ca9e804abf3c89b3c8c10724c371dc00c6d3ada1a` |
| tests/test_native_completion.py | 12706 | `77ff09c4dacda67b422e0442eb0d816b4a00f23c1dc73f607146c98310b2d649` |
| tests/test_pointcloud.py | 5751 | `c2afe72472442160d04c8630beebc5717ec856cb83ab8c59f12bef02c8968fcd` |
| tests/test_processing_channel.py | 1828 | `6f303e45f9b56d24dfc8a69f261987a649118d9268ed659d6867da8738104ea4` |
| tests/test_rollout_commit_preview.py | 6562 | `91e563c599aabaf0ad8e8a8ce00dc7f43fcc147a0410dde983d4888238fd1b17` |
| tests/test_rollout_discovery.py | 2738 | `b315ea7674df47597fc797e4c473f779f1be6b03f0d8909504db01d7f5e0f045` |
| tests/test_rollout_inventory.py | 3458 | `5b03633f9f1f3ae1b4f406a22126c239b339ec3817074de0476f9935952b1d13` |
| tests/test_rollout_native_predicates.py | 14376 | `e7f8de9d3a9e64bac68b919b14930c6dd5e851c501b6cdd2dc3bf70118b24816` |
| tests/test_rollout_planner.py | 4626 | `a33134be353d23036b819626db0fb6c03b307c4b2b59f26e27b3f1f1f374f74a` |
| tests/test_rollout_predicates.py | 20929 | `c97866154965f74396b0e2a095324e85e27ccd99f378090e6641840522e4b302` |
| tests/test_rollout_projection.py | 5656 | `f52383aea53d09ba50c539516628c00fc7e86338b12826d44bf02a79134a491a` |
| tests/test_runtime.py | 21238 | `0255751b17197bd7a61e31115f8ad9ede974f1a6b557a53ddeb4f49d6469103e` |
| tests/test_surface.py | 8399 | `4a9cf82c7359b3c7e0def8944108f30c1b21e698b99d5a1990896fbc71e50706` |
| tests/test_usability.py | 10102 | `62ae55c9ce3422abf0d0f7508ba9903531938b860096177a53a951916fc9294f` |
| tests/test_workstation.py | 6968 | `17fd30d86ef7e4d3f9fe476dba00a667d050a1eec371695e6249f6f28813bc8d` |
| web/app.js | 33827 | `56df0f48c48d6ef4de312fd122f6c38ce8f239f6918b7655dfdddafd866f9479` |
| web/index.html | 8192 | `0d0d741daa89e5d477e58d3beaaf29e2c09945a51b64cb42fd7e5d0be00b20a6` |
| web/mark.svg | 203 | `7009b9994ed70b9f1b54aee45eaca671316a6d361d393270063ed7c712cb32a0` |
| web/style.css | 24032 | `f78c51e7c36df7f3bec94b89c9b1138d5e8cc71b0417cf5b5b8c50b427f49b17` |
