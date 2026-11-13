# Definitio 开发轮交付 · 黄金链纵切（v03）

日期：2026-11-13 晚。执行：Muse 侧执行代理（本机施工）。验收：待 Muse 独立回读核验、鱼鱼终判。
依据工作单：`dev-round-golden-chain-work-order-20261113.md`（本单为唯一口径）。
实施计划：[docs/superpowers/plans/2026-11-13-golden-chain.md](superpowers/plans/2026-11-13-golden-chain.md)
自动化报告：[evidence/golden-chain-report.md](../evidence/golden-chain-report.md) · [evidence/golden-chain-report.json](../evidence/golden-chain-report.json)

**结论先行**：黄金链切片已在新版本目录内实现完毕；五类验收全部自动化跑出并逐项 PASS；全量回归 273 项，263 PASS / 10 FAIL，10 项全部为**既有环境阻塞**（非本轮引入，附源工作站对照），**非预期失败 0**；G1 基线 59 文件哈希 59/59 不变。**不含**引擎切版、真模型评测、40 动词全量推广。认证维持 NOT_CERTIFIED。

---

## 一、目录定名依据

新版本目录：`C:/Users/RAHUYU/WorkBuddy/2026-11-09-10-00-10/agent-native-gis/06-runtime-v03-golden-chain`

工作单举例为 `07-runtime-v03-golden-chain`，并授权「执行代理按现场布局定名并在报告中注明」。现场实测：

- 项目工作台现有编号为 `00-baseline`、`01-evaluation`、`02-g1-gate`、`03-g2-launch`、`04-roadmap`、`05-reference-runtime`，**06 尚未占用**，故续 06 而非 07。
- 硬约束佐证：`scripts/audit_g1.py:91` 的默认 root 为 `Path(__file__).resolve().parents[2]`，只有在该编号布局下才指向工作台根并找到 `02-g1-gate/handoff-20261111`。放在别处会使 G1 只读审计失效。
- 起点源码取自**当前最新工作站** `D:/agent-native-gis`（含 A3–A6 轮成果），不是 `05-reference-runtime` 的旧快照。复制范围：`definitio/`、`tests/`、`scripts/`、`examples/`、`web/`、`run_engine.py`、`run_mcp.py`、`requirements*.txt`、`AGENT.md`、`README.md`。**未复制** `data/`（私人工作区）、`evidence/`（历史证据）、`release/`、`backup/`、`.venv`。
- 另补齐 4 个离线评测夹具 `evidence/a3-a6-20261113/g0/{protocol,tasks,frozen,predictions}.json`（`tests/test_surface.py` 依赖，纯离线、用 httpx MockTransport）。补齐前 4 项 surface 测试因缺夹具失败，补齐后 13/13 PASS。
- 新目录内建本地 git 仓与分支 `golden-chain-v03`，逐任务提交，**无任何远端、未推送**。

## 二、黄金链与落实位置

链路（`definitio/golden.py:16` STAGES）：

| 阶段 | 动词/工具 | 说明 |
|---|---|---|
| 设施数据接入 | `gis_import_data` (V17) | 内联 data URI，显式 CRS/schema/source_license |
| 坐标转换 | `gis_reproject` (V15) | EPSG:4326 → EPSG:32649，真实变换坐标 |
| 缓冲/服务区分析 | `gis_buffer` (V18) | 500 m，投影平面，quad_segs=8 |
| 空间连接 | `gis_spatial_join` (V22) | intersects / summary=all |
| 指标汇总 | `gis_field_statistics` (V29) | count/sum，null_policy=exclude |
| 保存并回读产物 | `task_plan.exports` + `artifact_view` | 暂存→校验→发布文件产物，独立回读 |

服务区分析本轮以**缓冲**实现；工作单写的是「缓冲/服务区分析」，网络型等时圈不在本地数据域内，未新建动词（见已知限制）。

### D1 语义对象头

- 构造：`definitio/semantics.py:124` `object_header()`；只读分析结果头 `semantics.py:98` `result_header()`。
- 字段：`identity`（object_id / object_type / name / version{version_id, fingerprint, created_at}）、`structure`（按对象类型：矢量 fields/feature_count/geometry_types/bounds；栅格 grid；点云 point_count/point_dimensions；资源 resource_kind/extent）、`crs`、`axis_order`、`units`、`lineage`（operation{verb,vid,parameters,task_id,step_id} + inputs[{object_id,name,version_id,fingerprint}]）、`scope`（workspace / authorization）、`persistence`（store/table/record_id/artifact_uri/metadata_uri/file_artifact）。
- **CRS 与轴序分列两个字段**：`model.py:79` 新增 `crs["axis_order"]`，由 `semantics.py:45` `parse_axis_nodes()` 从 **WKT 的 AXIS 节点**解析（name/direction/order/unit），`semantics.py:65` `axis_order()` 另给 `declared / sequence / storage_order / derivation / differs_from_storage`。**不由 EPSG 编号推断**：`tests/test_golden_semantics.py::test_axis_order_survives_a_wkt_only_definition_without_any_epsg_code` 用纯 WKT（无 EPSG 号）断言仍得 `north,east`。引擎存储序 `coordinate_order="x,y"` 与权威轴序分开保存，EPSG:4326 的 `differs_from_storage=True` 被显式标出。
- 落库：`store.py:82` `_attach_header()`，五个写入路径（insert_dataset / insert_raster / insert_pointcloud / insert_resource / advance_head）全部附头。`definition_hash` 与 `logical_fingerprint` 口径不变（`test_header_does_not_change_the_logical_fingerprint`）。
- 暴露：`agent.py:289` descriptor 的 CRS 投影补 `axis_order` 并新增 `semantic`；`preview.py` dataset_summary 同步；`agent.py` 与 `server.py` 的 GeoJSON 导出信封补 `semantic`；`evidence_get` 摘要补 `semantic`。
- **锚例**：`test_buffer_result_answers_lineage_unit_crs_and_axis` 断言缓冲区结果能回答「来自哪一版道路数据（lineage.inputs[0].version_id/fingerprint）、什么距离单位（units.operation.distance.unit="m"，value_in_crs_units=500.0）、哪个 CRS（EPSG:32649）、轴序为何（east,north）」。

### D2 验证器单实现

- 独立模块 `definitio/verifier.py`。三层分离：**算子** = `operations.py`/`vector_extra.py`/`raster.py`/`pointcloud.py`/`completion.py`；**验证器** = `verifier.py`；**执行策略** = `runtime.py`/`commit.py`/`resume.py`（决定何时调用、失败如何处置、错误码映射）。
- 第一批五谓词：`distance_unit_compatibility`(:34)、`input_version_binding`(:83)、`spatial_join_contract`(:101)、`output_reopenable`(:144)、`write_scope`(:176)。原 `verification.py` 的算子读回不变量整体迁入 `operation_readback_invariants`(:200)，判定逻辑与 check id 一字未改；`verification.py` 现为 19 行薄适配层，全仓**只有一份实现**。
- 三段式：`run()`(:278) 返回 `checked`（检查了什么）/`found`（发现了什么）/`unconfirmed`（什么无法确认）+ `ok` + `predicates_digest`。**无 `verified` 字段**，`test_result_is_three_part_and_never_a_blanket_verified_flag` 显式断言 `"verified" not in result`。不适用/上下文不足的谓词返回 `passed=None` 并写明无法确认的理由，不冒充通过。
- **两条路径同一实现**：提交路径 `runtime.py:_execute_step` 与 Agent 主动路径 `agent.py:338` `verify_task()`（经 `task_get` 的新可选参数 `verify=true`）都调用 `verifier.run()`。代码级同一性断言：`test_agent_verify_and_commit_paths_call_the_same_predicate_objects` 把谓词包成计数闭包后分别装到两条路径，断言 `{id(f)}` 集合相等且调用顺序同名；`test_preview_uses_the_same_predicates_and_reports_unconfirmed_persistence` 对预演路径做同样断言。
- 预演不再「零后置检查」：`preview.py` 在 compute 前后各跑一次同一验证器，`stage="preview"` 时 `output_reopenable` 如实报「内存预演不能确认持久化后可重开」。
- **配置层接缝**：`verifier.py:274` `POLICY = {precheck, preview, commit, verify, commit_task}` 是检查策略的唯一拨动点；产品默认走可靠全集，**未做成用户开关**（`test_check_strategy_is_a_configuration_seam_not_a_user_switch` 断言 60 个工具的 schema 里不存在 `predicates/check_policy/verification_mode` 参数），未建注入层。

### D3 版本绑定提交

- 计划绑定：`runtime.py:23` `snapshot_ref()` 给每个输入绑定 `{id, name, fingerprint, record_digest}`（**新增整记录摘要**，可检出逻辑指纹不变但记录被改的情况）；`commit.py:60` `bind_write_scope()` 绑定预期写入范围（数据集目标 + 文件产物 + 工作区），并纳入 `plan_digest`，篡改即摘要不符。
- 状态变化：`commit.py:86` `evaluate_binding()` → `rejected_state_changed` 并明示 `reasons / changed_inputs / changed_targets`；`rule="version_binding_changed"`。输入变化 → `TARGET_STATE_UNKNOWN`；仅目标被占 → `ALREADY_EXISTS`（保住既有判定）。
- 文件产物：`commit.py:139` `publish_file_artifact()` 走 **暂存（`data/artifacts/.staging/*.tmp`）→ 校验（字节 sha256 + 重新打开解析 + 指纹 + 记录数）→ 发布（`os.replace` 原子改名）**；任一环节失败清理暂存，发布后落库/回读失败则删除已发布文件，不留孤儿。名称校验拒绝路径分隔与上跳（`../escape.geojson`、`sub/dir.geojson`、`..\`、非 `.geojson` 全部 `INVALID_ARGUMENT`）。
- 三态：`commit_state ∈ {committed, rejected_state_changed, pending_confirmation}`（`commit.py:20`）。`runtime.py:251` `_finalize_commit()` 先置 `pending_confirmation`，**真实回读核对通过后**才置 `committed`；回读未确认即 `TARGET_STATE_UNKNOWN`，不报成功。
- 回读：`task["readback"] = {confirmed, outputs[], file_artifacts[], destroyed_inputs[]}`；`commit.py:180` `readback_file_artifact()` 重新打开已发布文件核对字节 sha256、字节数、内嵌指纹。
- 合法销毁是**计划内授权副作用**：`commit.py:41` `planned_destructions()` 使逐步核对与任务级核对不把已销毁输入误判为版本失效（本轮实测发现并修复的真实回归）。

### D4 可恢复计划

- 检查点：`resume.py:23` `checkpoint()` 记录 `dependencies / input_versions（含 record_digest）/ output_object / check_location{evidence_ref,uri} / side_effects / retryable / valid / state_at`。副作用标记覆盖 new_dataset / none / destroy / rename / advance_head / external_publish / external_file；非幂等类标 `retryable=False`。
- 恢复：`runtime.py:298` `resume()`，经 `task_execute` 的新可选参数 `resume=true` 暴露。只接受 `FAILED`；`ISOLATED`（外部状态未知）一律拒绝。恢复前逐个重验已提交步骤的依赖与输出（`resume.py:34`），任一失效 → `rule="resume_dependency_invalidated"` 拒绝，不复用旧结论。
- 不整链重跑：`runtime.py:222` `_run_steps(task, previous, start_index)` 从首个未提交步骤续跑，`$previous` 由上一有效成果接续。A4 断言 `steps[0]/[1].attempt_seq==1`、`steps[2].attempt_seq==2`、`gis_import_data`/`gis_reproject` 各只计算一次。
- 幂等：导出重试按 `request_key = digest({task, step, name})` 找回原记录并回读确认，不重复发布、不覆盖；`ALREADY_EXISTS` 拒绝同名不同键。`$previous` 语义与既有兼容（`test_previous_reference_still_works_after_resume` + 既有 225 项全过）。

### D5 发现与错误一致性

- 审计器 `definitio/discovery.py:44` `audit()`：逐工具比对 `list_full / list_compact / describe / route` 的名称与参数集，解析 4 个 prompts 与手册中的动词调用与参数名，静态扫描全包可抛出的错误码并核对 recovery 覆盖，扫描 `rule="..."` 回显并核对是否已公布。
- 结果：**60 个工具零不一致**；prompts 零不一致；`recovery_codes.missing = []`；`error_rules.unpublished = []`。
- 补齐 10 个此前落入通用兜底的错误码 recovery 条目（`agent.py` 模块级 `RECOVERY`）：AUTHORIZATION_REQUIRED / AUTHORIZATION_SCOPE / AUTHORIZATION_EXHAUSTED / LICENSE_BLOCKED / DEPENDENCY_BLOCKED / PREFLIGHT_REQUIRED / BACKEND_UNAVAILABLE / DESTRUCTIVE_BLOCKED / DEFER_BLOCKED / DIMENSION_MISMATCH。
- 新增 `guidance.py` `RULES` 注册表（8 条），经 `plan_step_contract()["rules"]` 在 `gis_describe_algorithm` 公布；此前 `published_surface_names` 只在错误里出现、发现面不公布的缺口已闭合。
- 计数冻结未破：full 60 / compact 21 / 动词 40 / prompts 4。新能力只通过**既有工具的可选参数**（`task_get.verify`、`task_execute.resume`、`task_plan.exports`）、**新增记录字段**与**新增 URI 分支** `definitio://artifacts/{id}` 暴露；`surface-v1.json` 同步补 3 个参数映射，双射校验通过。

## 三、验收与回归（全部自动化）

复现命令（在新版本目录内）：

```bash
D:/agent-native-gis/.venv/Scripts/python.exe -X utf8 scripts/golden_chain_report.py \
  --root . --workbench .. --out evidence
```

### 五类验收逐项证据

| 类 | 测试 ID | 状态 | 关键断言 |
|---|---|---|---|
| 1 正常端到端 + 独立回读真值 | `tests/test_golden_chain.py::test_a1_end_to_end_and_independent_readback_matches_truth` | PASS | 5 步链 COMMITTED；已发布 `gc_join.geojson` 独立解析得 3 条记录、population 合计 7500；**鞋带公式**独立复算每条缓冲面积与解析真值相对偏差 < 1e-9；指标汇总 count=3/sum=7500；`artifact_view` 完整性哈希可读；缓冲语义头回答来源版本/单位/CRS/轴序；`task_get(verify=true)` 复核 ok 且每步都有 unconfirmed |
| 2 单位/对象/版本错误各一例且指明检查 | `...::test_a2_unit_object_and_version_errors_are_each_named` | PASS | 单位错：`details.predicate="distance_unit_compatibility"`、`crs_projected=False`、`requested_unit="m"`、码 NOT_SUPPORTED；对象错：`predicate="spatial_join_contract"`，problems 指明栅格非矢量；版本错：`rule="version_binding_changed"`、`changed_inputs[0].id` 与 `reason="record_digest_changed"` |
| 3 检查通过后改状态不得授权旧结论 | `...::test_a3_state_change_after_checks_does_not_authorize_the_old_plan` | PASS | dry_run 通过后篡改 zones 记录 → `commit_state="rejected_state_changed"`，`output_refs` 与 `file_artifact_refs` 均空，工作区无产物文件 |
| 4 中途失败可从有效状态恢复 | `...::test_a4_midchain_failure_recovers_from_valid_state` | PASS | 在 `gis_buffer` 首次计算注入失败 → partial；resume 后 COMMITTED、`resumed_from=3`、前两步 attempt_seq 仍为 1、import/reproject 各只算一次、buffer 算两次 |
| 5 提交/导出重试无重复或越界副作用 | `...::test_a5_commit_and_export_retries_have_no_duplicate_or_out_of_scope_effects` | PASS | 连续 3 次重试均 `replayed=True`；数据集数不变、产物文件名集合不变、字节完全一致、`file_artifacts` 仍 1 条、`.staging` 空、`replay_count=3`；`write_scope` 谓词 `out_of_scope=[]` |

**真值口径与容差理由**：缓冲真值用 `golden.py:48` `buffer_truth_area()` = 正 32 边形解析面积 `0.5·n·r²·sin(2π/n)`（GEOS `quad_segs=8` → 每象限 8 段），**不是 πr²**；面积用 `golden.py:31` `shoelace_area()` 对已发布文件的坐标独立复算，不经 GEOS、不经引擎计算路径。相对偏差阈值 1e-9，实测通过，未放宽容差。人口合计用普通 Python 求和独立核对。

### 全量回归

| 项 | 数 |
|---|---:|
| 收集总数 | **273** |
| PASS | **263** |
| FAIL | 10（全部环境阻塞） |
| ERROR / SKIP | 0 / 0 |
| 新增测试 | **48**（48 PASS / 0 FAIL） |
| 既有测试 | **225**（215 PASS / 10 环境阻塞） |
| 非预期失败 | **0** |
| 耗时 | 198.03 s |

逐文件结果见 [evidence/golden-chain-report.md](../evidence/golden-chain-report.md)；原始 junitxml 与日志：`evidence/regression-full-suite.xml`、`.log`。

**与历史「201 个测试 ID」口径的差异（如实记账）**：工作单给的基线口径是「去重 201 个测试 ID 终局全过」。当前源码实测收集数为 **225**（201 是 40/40 轮的历史口径，其后 A3–A6 轮新增了 workstation/surface 等测试）。本轮以实测 225 为既有基线，开工前在新版本目录实跑确认 **215 PASS / 10 FAIL**，收工后同样 **215 PASS / 10 FAIL**。

**逐 ID 对账（机器判定，已固化进报告脚本 `reconcile_baseline()`）**：基线 225 ID ↔ 终局既有 225 ID，缺失 0、多出 0、**判定变化 0**，PASS 数 215 ↔ 215。结果见 `golden-chain-report.json` 的 `baseline_reconciliation`（status=PASS）与报告 MD「既有测试逐 ID 对账」节。新增 48 项单列。

### 10 项环境阻塞（保留红灯，不改判）

- 用例：`test_native_completion.py` 的 `test_tile_binary_http_download_decode_and_expiration`、`test_actual_sta_all_entity_sets_and_exact_odata_downpush[8 个实体集]`、`test_actual_services_filter_and_unknown_service_rejection`。
- 根因：缺 `data/backends.json`（本机 PostGIS/GeoServer 沙箱配置，**含凭据，不随源码包**）；STA 用例还需外部 istSOS 端点。
- 处置：本轮**不启动** PostGIS/GeoServer 沙箱、**不访问**外网、**不复制**含凭据的配置。`completion.py:90` 在读取配置处即返回 `BACKEND_UNAVAILABLE`，**未发起任何网络请求**，离线红线未破。
- 对照证据：同 10 项在源工作站 `D:/agent-native-gis` 实跑同样失败（10 FAIL / 27 PASS），证明非本轮引入。开工前基线日志：`evidence/baseline-full-suite.xml`、`baseline-full-suite-r2.xml` 及对应 `.log`。

### G1 基线

`59/59` 文件 SHA256 与 `D:/agent-native-gis/evidence/baseline-sha256-before.json` 完全一致，0 变化 0 缺失。守卫用例 `tests/test_golden_g1_guard.py`（2 项 PASS）另跑 `scripts/audit_g1.py` 的只读审计：integrity PASS、registry PASS、`G1_acceptance=NOT_ISSUED`。

## 四、新增/修改文件清单与哈希

完整逐文件清单（110 个文件，含字节数与 SHA256）见 [evidence/golden-chain-report.md](../evidence/golden-chain-report.md) 末节与 `golden-chain-report.json` 的 `file_manifest`。

**新增 15**：`.gitattributes`、`definitio/{semantics,verifier,commit,resume,discovery,golden}.py`、`scripts/golden_chain_report.py`、`tests/test_golden_{semantics,verifier,commit,resume,discovery,chain,g1_guard}.py`

**修改 9**：`definitio/{agent,guidance,model,preview,runtime,server,store,verification}.py`、`definitio/surfaces/surface-v1.json`

**新版本源码摘要** `source_digest`：`e5f8cd6496c089361f312189332d1ba43e274783ee6989d6ea63b890a2ca8c31`
运行时 0.2.0-dev｜shapely 2.1.2｜GEOS 3.13.1｜rasterio 1.5.2｜GDAL 3.12.2｜Python 3.12.9

本地 git 提交（分支 `golden-chain-v03`，无远端）：

```
77a1614 fix: planned destructions are authorized effects; add check-policy configuration seam and publish cleanup
13e0385 feat(D5): cross-surface discovery consistency; golden chain A1-A5 acceptance and G1 guard
bb745b6 feat(D3,D4): version-bound three-state commit, staged file publication and resumable plans
9cb6bdd feat(D2): one verifier implementation shared by verify and commit paths
65ef8fa feat(D1): semantic object headers with definition-derived axis order
b317f2d chore: keep working-tree bytes stable for hash reporting
491687c baseline: import current workstation source as v03 golden-chain starting point
```

## 五、本轮实测发现并修复的问题（不改判、留痕）

1. **语义头塞进只读结果导致回包降级**：首版把 `semantic` 直接并入 `result`，使 `gis_validate_geometry` 结果超过 3000 字符阈值，`_task_reply` 从 `value` 降级为 `summary`，`test_agent.py` 报 `KeyError: 'value'`。改为把头挂在 `evidence["semantic"]`，结果体保持原样。
2. **写入范围口径不一致**：`bind_write_scope` 用 `write_target` 取到的是解析后的数据集 ID（append）或新名（rename），而实际写入记录用文档 `name`，导致 rename/append 被误判越界。新增 `commit.logical_target()` 统一口径，4 项既有测试恢复。
3. **空间连接计数核对取错来源**：`matched_count/unmatched_count` 是提案附加字段、不在数据集记录上，首版从 output 取值得 0 而误判。改为从 proposal 取，并把 CRS 不符映射回 `CRS_MISMATCH`（保住既有判定）。
4. **合法销毁被误判为版本失效**：销毁后任务级/逐步 `input_version_binding` 把已销毁输入报为 missing。新增 `planned_destructions()` 排除计划内授权销毁。
5. **`$previous` 无法接在导入之后**：`runtime.declare` 原判据是前一步参数含 `"target"`，而 `gis_import_data` 用 `target_name`，使「接入→坐标转换」无法成链。改为 `write_target(...) is not None`，语义更准确且既有负例测试仍过。
6. **审计器自身解包错误**：`FAMILIES` 值形状是 `(purpose, tools, keywords)`，首版按 `(_, _, tools)` 解包把关键词串当成工具名，误报 17 个工具「不在任何家族」。已修正。
7. **`require` 的 details  eager 求值**：`reason=checked["reason"]` 在校验通过时也会取值而 KeyError。改为 `.get()`。
8. **canonical 表面的参数映射为空是设计而非缺陷**：`Surface.__init__` 对 canonical profile 构造 `parameters={}`（恒等、无重命名），审计断言据此区分 canonical 与 surface-v1，未把设计当 bug 改。

## 六、已知限制

1. 10 项原生后端用例环境阻塞（见第三节），保留红灯，未启动服务、未访问外网、未复制凭据。
2. **干净安装未验证**：本轮显式复用 `D:/agent-native-gis/.venv`（`system-site-packages=true`，Python 3.12.9）。不声称干净机器安装通过；历史失败记录见源工作站 `evidence/workstation-core-install.log`。
3. **真模型评测未做**：本轮全部为确定性自动化测试，0 次模型 API 调用。
4. **引擎未切版**：8765 仍在监听（PID 6928，pythonw.exe），本轮未启动/停止/改配置任何引擎；8876 的记录进程 PID 32012 在本轮开工前已不存在（`data/server-process.json` 记于 15:01），本轮同样未触碰。测试一律用 `tmp_path` 独立工作区，未占用 8899 等常驻端口。
5. **D1–D5 只沿黄金链落实**，40 动词未全量推广；机制仪器（E/V/T 开关全套、变异缺陷集、五原语域中立化）按鱼鱼决定推迟到论文实验装置阶段，本轮未建。
6. **服务区分析以缓冲实现**：网络型等时圈/服务区不在本地数据域内，未新建动词（动词数仍 40）。
7. **文件产物首版只开放 geojson**；轴序解析只覆盖 WKT2 的 `AXIS[...]` 节点，未处理 WKT1 或无 AXIS 节点的自定义定义（此时 `sequence=""`、`differs_from_storage=False`，并如实标为未确认）。
8. **读回核对与 GEOS 同源**，不是独立几何 oracle，不证明跨后端等价或校准容差。缓冲真值用正 32 边形解析面积而非 πr²，理由已写明。
9. **谱系成为真实依赖**：语义头记录输入对象 ID 后，`store.deletion_impact` 的子串扫描会把派生数据集识别为输入的依赖，销毁有派生成果的输入现在会被 `DEPENDENCY_BLOCKED` 拒绝。这是 D1 谱系的预期后果（更安全），既有测试未覆盖该组合，在此显式声明。
10. **恢复对已销毁输入保守拒绝**：若已提交步骤的依赖被销毁，`validate_checkpoints` 判失效并拒绝恢复，须重新规划。
11. 源工作站 `D:/agent-native-gis` 有 6 个文件与 `WORKSTATION-MANIFEST.json`（15:03）不符：`AGENT.md`、`definitio/mcp_server.py`、`docs/FRICTION-REGISTER.md`、`docs/MODEL-EVALUATION-PROTOCOL.md`、`docs/NEXT-PLAN-STATUS-20261113.md`、`scripts/package_release.py`。实测 mtime 均为 15:39–16:11，属 A3–A6 轮在清单写就之后的改动，**非本轮所为**；本轮在该目录只跑过一次只读对照测试，除 `__pycache__` 外未写入。
12. 认证维持 **NOT_CERTIFIED**；G1 正式裁决、Muse 独立复验、公开发布均未执行。

## 七、红线自查

| 红线 | 自查结果 |
|---|---|
| G1 基线 59 文件一字不动 | PASS，59/59 哈希相符，守卫用例固化 |
| 新开发只进新版本目录 | PASS，15 新增 + 9 修改全部在 `06-runtime-v03-golden-chain` 内 |
| 在线引擎 8765/8876 不停不改配置 | PASS，未启动/停止/改配置；8765 仍在监听，8876 开工前已停 |
| 全程离线 | PASS，0 次模型 API；原生后端用例在配置读取处即拒，未发起网络请求；MCP/表面测试用本地子进程与 MockTransport |
| 不推送、不发布、不动公开仓 | PASS，本地 git 无远端，未 push、未打包发布 |
| 本地依赖不随包如实记账 | PASS，见已知限制 2；未复制含凭据的 backends.json |
| 卡住超 30 分钟须记录 | 本轮无单点卡住超 30 分钟；8 个实测问题均在当轮定位修复并留痕（第五节） |
| 不许静默跳过验收项 | PASS，五类验收逐项自动化 PASS；10 项环境阻塞保留红灯并附对照证据 |

## 八、待 Muse 核验的入口

1. 复跑：`python scripts/golden_chain_report.py --root . --workbench .. --out evidence`（约 3.5 分钟，全离线）。
2. 只读对账：`evidence/golden-chain-report.json` 的 `g1_baseline.files`、`file_manifest`、`regression.cases`、`acceptance`、`failure_classification`。
3. 代码级同一性：`tests/test_golden_verifier.py::test_agent_verify_and_commit_paths_call_the_same_predicate_objects`。
4. 独立真值：`tests/test_golden_chain.py::test_a1_...` 中的鞋带公式与正 32 边形解析面积。
5. 原始基线对照：`evidence/baseline-full-suite-r2.log`（开工前 225/215/10）与 `evidence/regression-full-suite.log`（收工 273/263/10）。

—— 执行代理登记，2026-11-13。本报告不预判验收结论。