# Definitio 开发轮交付 · D1–D5 横向推广（v04 rollout）

日期：2026-11-14。执行：小Q（鱼鱼派单，本机施工）。验收：待 Muse 独立回读核验、鱼鱼终判。
依据工作单：`dev-round-rollout-work-order-20261114.md`（本单为唯一口径）。
实施计划：[docs/superpowers/plans/2026-11-14-rollout.md](superpowers/plans/2026-11-14-rollout.md)
自动化报告：[evidence/golden-chain-report.md](../evidence/golden-chain-report.md) · [evidence/golden-chain-report.json](../evidence/golden-chain-report.json)

**结论先行**：D1–D5 已在新版本目录内推广到全部 40 动词与四类数据（vector/raster/pointcloud/resource）。全量回归 **357 项＝既有 273＋新增 84**；既有 273 个测试 ID **逐 ID 对账判定零变化**（263 PASS＋10 环境阻塞保留原判）；新增 84 项全 PASS；非预期失败 **0**；黄金链 A1–A5 全过；G1 基线 **59/59** 哈希不变；工具计数 **60/21/40/4** 复核不变；发现审计 60 工具零不一致。逐动词谓词清单 **40 动词无空白**（111 个专属检查项＋2 个显式「仅基础检查＋理由」）。认证维持 **NOT_CERTIFIED**。**不含**引擎切版、真模型评测、干净 venv 验证、推送发布。

---

## 一、目录定名依据

新版本目录：`C:/Users/RAHUYU/WorkBuddy/2026-11-09-10-00-10/agent-native-gis/07-runtime-v04-rollout`

- 工作台现有编号 00-baseline…06-runtime-v03-golden-chain 已占用，续 **07**；`scripts/audit_g1.py` 默认 root=parents[2] 只有在该编号布局下才指向工作台根并找到 `02-g1-gate/handoff-20261111`。
- 起点＝06 终局源码（第一轮交付 `2f02ee9`，source_digest `e5f8cd64…`）原样导入，基线提交 `c6d41ba`。复制范围沿第一轮成例；**未复制** 06 的 `data/`（私人工作区）与历史 `evidence/`，仅保留 `test_surface.py` 依赖的 4 个离线夹具 `evidence/a3-a6-20261113/g0/*.json`。
- 开工前在 07 实跑全量基线：**273 项，263 PASS／10 FAIL**，与 06 终局完全一致（`evidence/baseline-full-suite.xml/.log`）。首跑因 07 缺 `data/` 父目录致 pytest basetemp 建立失败（249 ERROR），建目录后重跑取得干净基线——属环境搭建问题，非源码问题，留痕于此。
- 07 内建本地 git 仓与分支 `rollout-v04`，逐任务提交，**无任何远端、未推送**。

## 二、推广内容与落实位置

### R1 语义对象头全量＋三处 CRS 投影统一

- **规范投影单实现**：`definitio/semantics.py` `CRS_PROJECTION_KEYS`＋`crs_projection()`，8 键＝code/definition_hash/projected/unit/metres_per_unit/**coordinate_order/axis_policy/axis_order**。三处消费同一函数：
  - preview `dataset_summary`（原 6 键，缺 coordinate_order/axis_policy）；
  - agent `descriptor`（原 7 键，缺 axis_policy）；
  - artifact/export：`commit.dataset_payload`、`agent.artifact` 的 geojson/raster 信封、`server.export_dataset`（原全量 9 键含 wkt）。完整 WKT 仍经 metadata URI 可达。
- **一致性断言**：`tests/test_rollout_projection.py` 对同一对象断言三处（＋artifact_view 信封＋已发布文件信封）输出**逐键相等**；EPSG:4326 的 `north,east` 与 `differs_from_storage=True` 在三处均成立；raster/pointcloud/resource 同投影。
- **对象头覆盖**：06 已在五条写入路径（insert_dataset/insert_raster/insert_pointcloud/insert_resource/advance_head）附头，本轮复核确认四类数据全部产出路径覆盖。控制面四个导入（dataset_import/raster_import/pointcloud_import/workspace_resource）**补谱系** `lineage.operation`（verb＋紧凑参数，不嵌 geojson/base64）与 scope。
- **冻结冲突如实处理**：`test_golden_semantics.py::test_imported_dataset_carries_a_full_semantic_header` 冻结了 HTTP 直传导入 `lineage.operation is None`。为守「既有判定零变化」，`runtime.import_dataset/import_raster` **保持原语义**（溯源在 source.kind＋完整对象头），未强行加谱系；控制面导入不受该断言约束，已补。

### R2 验证器谓词全量补齐（40 动词）

- **形态**：不新增顶层谓词（既有测试硬断言谓词数=6，且 `predicates_digest` 冻结）；全部逐动词后置检查以 check 项扩展进 `verifier.operation_readback_invariants`（沿 dissolve 既有形态），三段式（checked/found/unconfirmed）不变。`check()` 支持 True/False/**None（无法确认，不冒充通过）**；`legacy_checks()` 过滤 None，硬门不误伤。
- **矢量**（优先序沿工单）：buffer（GEOS 有效性＋**解析界 A+2Lr+πr² 上界**＋包含关系下界＋dissolve/非 dissolve 计数语义）→ spatial_join（同核逐对复算 matched/unmatched 与输出行数、左外空值语义）→ clip/overlay（面积≤输入、同核 covers 子集性、属性子序列保留）→ reproject（同核 PROJ 往返抽查＋全量坐标变换边界一致性）→ merge/append（计数＋排序多重集指纹对账）→ field_statistics（纯 Python 逐组复算）→ add/calculate_field（构造 default 真值／安全表达式逐条复算）→ import_data（**源文档 sha256 绑定**＋许可声明＋源→输出逻辑指纹）→ assign_projection（normalize-WKB 多重集精确）→ create/delete_field/spatial_filter/measure/validate_geometry/copy/convert/dissolve 各有专属项。
- **栅格六动词**：grid_and_pixels_readback（收编自 runtime）＋网格对齐（无重采样）＋nodata 计数对账＋掩膜语义（calc=输入并集；terrain=边界＋3x3 含 null；**aspect 另加平坦像元**；reclassify=null 保持）＋**像元抽查**（calc 用独立 AST 求值器；slope/aspect/hillshade 用 verifier 内**独立 numpy Horn 实现**复算；reclassify 按规则构造真值）。zonal 为矢量表输出：分区计数＋同核 geometry_mask 中心规则复算＋统计复算。
- **点云两动词**：字节读回（收编）＋维度保真＋**点数对账**（merge=输入和；thin_decimate=len(range(offset,n,step)) 构造真值；reproject/convert 守恒；clip/thin_radius≤输入上界）＋字节 sha256 指纹重算；boundary 动作的矢量附录契约。
- **processing/gp/completion**：centroids 种子语义（ALL_PARTS 部件计数、Point 类型、schema 保留）；GetCount==输入计数；资源读回（收编）＋map layers 恰增 1 绑定目录证据＋image base64 sha256/尺寸/dpi；服务动词回执结构对账（见已知限制 5）。
- **容差依据全部登记**：`verifier.CHECK_REGISTRY`（40 动词、111 专属项，每项 method/basis/layer）；报告末节渲染逐动词清单表，`tests/test_rollout_inventory.py` 断言覆盖==SPECS 40 动词、注册 ID 均存在于实现、实跑发射的 check 均已注册、无谓词动词显式理由。
- **同核口径**：所有同核复算 method 标 `same_kernel_*`/`same_formula_*`，unconfirmed 文案统一「不是独立几何 oracle」；沿 06 `verification_limits`。

### R3 提交与恢复全量＋dry-run 同验证器

- **结构事实**：06 的 commit/resume 本就经单一 `_execute_step` 覆盖全部写路径（含生命周期动词，preflight 衔接未绕过）；本轮复核确认并补齐真实缺口：
  - **三处内联 ad-hoc 清单收编**：runtime 的 resource/raster/pointcloud 读回清单移入验证器（check ID 与方法名保持兼容），postcondition_checks 经 `legacy_checks` 回填；
  - **rename 真实读回**（原无任何后置检查）：内容指纹不变＋新名解析同 ID＋旧名注销；预演对持久层两项如实降级无法确认；
  - **delete 缺席读回**：原 `dataset_absent_readback` 是恒真占位，改为验证器按 ID 重开必须 NOT_FOUND 的真实读回；
  - **B10 dry-run/execute 不对称消除**：preview 后置验证原传 `output=None` 致不变量空转；现传入内存输出，**同一 check 集在预演与执行真实运行**（`test_preview_and_execute_run_the_same_invariant_checks` 断言 ID 集相等）。
- **同一性断言（id() 级，沿 06 形态）**：`test_preview_execute_verify_share_predicate_objects` 把谓词包成计数闭包分装三路径，断言 `{id(f)}` 集合相等；`output_reopenable` 预演分支做内存结构核对并保留「内存预演不能确认持久化后可重开」无法确认段。
- 恢复语义未改：FAILED 才可恢复、ISOLATED 拒绝、依赖失效拒绝、不整链重跑（A4 与既有 resume 测试全过）。

### R4 发现与错误一致性全量

- **如实区分**：B7 十码 recovery 条目与 `published_surface_names` 入 RULES 在**第一轮已补齐**（06 交付文档 D5 节）；本轮独立复核确认存在且语义完整（`test_b7_recovery_codes_have_explicit_semantics`、`test_published_surface_names_rule_is_on_the_discovery_surface`），未重复声称为本轮新增。
- **本轮实际扩展**：`discovery.raised_codes` 扫描范围从 `definitio/*.py` 扩到执行面内的两个 worker 脚本（`SCANNED_SCRIPTS`，runtime.source_digest 本就含它们）；worker 错误经 `completion.worker()` 统一 BACKEND_ERROR，无新码面。
- **固化证据**：`test_audit_covers_all_60_tools_with_zero_inconsistency` 断言 60 工具逐个 list_full/list_compact/describe/route 参数集一致、prompts 零未知参数、recovery 零缺失、rule 零未公布；报告 `discovery_consistency.ok=True`。

### R5 计划器衔接（小项）

- `planner.plan_goal` 补 **gis_spatial_join**（predicate 关键词：最近/包含/位于，默认 intersects；summary：全部→all，默认 first；secondary 必选）与 **gis_import_data**（仅首步；内联 data URI 提取；format/kind 固定）两条分支；均有执行级测试（成链并 COMMITTED）。
- **URI 安全切分**：目标句按 `;` 切步会切断 `geo+json;base64` 中的分号——切分前以占位符保护 data URI，切分后还原（实测发现并修复）。
- 统计步约束报错写明原因：「只读步骤（检查/统计，治理级 **R0**）不产生 **target** 数据集，后续步骤无法用 $previous 引用；请把统计或检查放在任务最后一步」，details 带 `constraint="readonly_step_must_be_last"/level/step`；判据改用 `write_target`（import 的 target_name 亦正确识别）。**6 步上限与既有规则未改**。

## 三、验收与回归（全部自动化）

复现命令（在 07 目录内）：

```bash
D:/agent-native-gis/.venv/Scripts/python.exe -X utf8 scripts/golden_chain_report.py \
  --root . --workbench .. --out evidence
```

### 全量回归

| 项 | 数 |
|---|---:|
| 收集总数 | **357** |
| PASS | **347** |
| FAIL | 10（全部既有环境阻塞） |
| ERROR / SKIP | 0 / 0 |
| 新增测试 | **84**（84 PASS / 0 FAIL） |
| 既有测试 | **273**（263 PASS / 10 环境阻塞） |
| 非预期失败 | **0** |
| 耗时 | 150.25 s |

**逐 ID 对账（机器判定）**：开工前基线 273 ID ↔ 终局既有 273 ID，缺失 0、多出 0、**判定变化 0**，PASS 数 263 ↔ 263（`baseline_reconciliation.status=PASS`）。新增 84 项单列（7 个 test_rollout_*.py）。

### 10 项环境阻塞（保留红灯，不改判）

- 用例与第一轮完全相同：`test_tile_binary_http_download_decode_and_expiration`、`test_actual_sta_all_entity_sets_and_exact_odata_downpush[8 实体集]`、`test_actual_services_filter_and_unknown_service_rejection`。
- 根因：缺 `data/backends.json`（含凭据，不随源码包）；STA 还需外部 istSOS 端点。`completion.py` 在配置读取处即返回 BACKEND_UNAVAILABLE，**未发起任何网络请求**。
- 对照：同 10 项在 07 开工前基线（`evidence/baseline-full-suite.xml`）与 06 终局（`06/evidence/regression-full-suite.xml`）同样失败，证明非本轮引入。

### 五类验收 A1–A5 与本轮推广验收

A1–A5 全 PASS（独立回读真值、三类错误指名、改状态拒旧计划、中途恢复、重试无重复越界）。本轮推广验收全 PASS：R1 三处投影一致性（含地理轴序）、R3 dry-run/execute 同 check 集＋三路径 id() 同一性、R4 全 60 工具零不一致、R5 两条意图分支、清单 40 动词无空白。逐项测试 ID 见报告 JSON `acceptance`/`rollout_acceptance`。

### G1 基线与计数冻结

- `59/59` 文件 SHA256 与 `D:/agent-native-gis/evidence/baseline-sha256-before.json` 一致，0 变化 0 缺失；守卫用例＋`scripts/audit_g1.py` 只读审计 PASS。
- 工具计数实测复核：**full 60 / compact 21 / 动词 40/40 / prompts 4**；VID 与规范名未动；`surface-v1.json` 未改（本轮未新增工具参数）。

## 四、新增/修改文件清单与哈希

完整逐文件清单（108 个文件，含字节数与 SHA256）见 [evidence/golden-chain-report.md](../evidence/golden-chain-report.md) 末节与 JSON `file_manifest`。

**新增 15**：实施计划 1＋证据 7（基线/终局 xml+log、报告 json+md、运行日志）＋测试 7（test_rollout_{projection,predicates,native_predicates,commit_preview,discovery,planner,inventory}.py）

**修改 11**：`definitio/{semantics,preview,agent,commit,server,completion,runtime,verifier,discovery,planner}.py`、`scripts/golden_chain_report.py`

**新版本源码摘要** `source_digest`：`87d73c5b1b39bca400eb6cb45111e05a8da23467fe040180a8cd0f09ef70230e`
运行时 0.2.0-dev｜shapely 2.1.2｜GEOS 3.13.1｜rasterio 1.5.2｜GDAL 3.12.2｜Python 3.12.9

本地 git 提交（分支 `rollout-v04`，无远端）：

```
c1ee11e feat: per-verb predicate inventory (40 verbs, 111 checks) and rollout report sections
bfad79b feat(R5): planner spatial_join/import_data intents, URI-safe splitting, explicit R0 last-step constraint
e281930 feat(R4): discovery audit scans worker scripts; full 60-tool zero-inconsistency evidence
507ea41 feat(R2): raster/pointcloud/zonal/service predicates; fix numpy-bool enforcement and aspect flat mask
2898b4f feat(R2): per-verb postcondition predicates for vector verbs with analytic bounds and same-kernel recomputation
ec80aff feat(R3): single-verifier readback for raster/pointcloud/resource/rename/delete; preview judges real output (B10)
ec1f0fc feat(R1): one canonical CRS projection across preview/descriptor/artifact; control-plane import lineage
c6d41ba baseline: import 06-runtime-v03-golden-chain final source as v04 rollout starting point
```

## 五、本轮实测发现并修复的问题（不改判、留痕）

1. **numpy 布尔同一性陷阱（真实假成功缺陷）**：重构后的 `check()` 用 `passed is False` 判失败，对 `numpy.bool_(False)` 不成立（身份比较）——检查项记为 false 却不触发回滚，任务照样 COMMITTED。由栅格像元注入测试捕获，改为 `passed is not None and not passed`。这是本轮最重要的修复：若不捕获，谓词全量推广会带着假成功上线。
2. **aspect 平坦掩膜语义差异**：首版对 slope/aspect/hillshade 统一断言「边界＋3x3 含 null」掩膜，但 aspect 的平坦像元（dzdx=dzdy=0）按 flat_policy=nodata 额外置 null，致既有 `test_flat_nodata_and_all_masked_terrain` 回归。修正为 aspect 掩膜逐像元加平坦判定。
3. **terrain 抽查坐标偏移**：`np.where(interior)` 返回子阵坐标（0..n-3），首版直接当全图坐标取 3x3 窗致越界。修正为先映射回全图掩膜再抽样。
4. **planner 切分切断 data URI**：目标句按 `;` 切步，`geo+json;base64` 的分号被当步骤分隔符。修正为切分前占位保护 URI。
5. **HTTP 导入谱系与冻结断言冲突**：见 R1 节——回退 runtime.import_* 改动，守既有判定零变化，控制面导入补谱系。
6. **测试夹具两处自纠**：reproject 测试误把 UTM 坐标当经纬度（PROJ 合法拒绝）；calculate_field 夹具含 null 被算子合法拒绝。均改夹具，未动算子。
7. **z_factor 约定实测确认**：以本地 GDAL 探针确认 slope=atan(z_factor·√(dzdx²+dzdy²))（-s 1/z_factor 的实际效果）、hillshade 标准 Horn 公式 uint8 舍入，复算公式据此实现，未凭记忆猜测。

## 六、已知限制

1. 10 项原生后端用例环境阻塞（第三节），保留红灯，未启动服务、未访问外网、未复制凭据。
2. **干净安装未验证**：显式复用 `D:/agent-native-gis/.venv`（system-site-packages=true，Python 3.12.9）；下一轮单独排。
3. **真模型评测未做**：全部确定性自动化测试，0 次模型 API 调用。
4. **引擎未切版**：8765/8876 未启动/停止/改配置；测试一律 tmp_path 独立工作区与临时端口。
5. **服务动词谓词为谓词级验证**：publish/serve_tile/list_services/query_observations/run_gp 的后端在本环境阻塞，其检查项以**合成回执**在单元 context 验证判定逻辑（测试文件显式注明），**不构成后端实测证据**；tile 字节级重算不在验证上下文（二进制句柄属引擎进程），如实列入无法确认段。
6. **同核自洽口径**：GEOS/PROJ/GDAL/PDAL 同源复算不是独立几何 oracle，不证明跨后端等价或校准容差。buffer 上界为解析界（A+2Lr+πr²，相对松弛 1e-9），下界为包含关系；点缓冲精确真值仍为正 32 边形（A1）。reproject 往返容差 1e-6×max(1,|坐标|)；坐标超 4096 边界一致性降级无法确认。地形抽查 float32 相对 1e-6、hillshade uint8 ±1、aspect 环差 1e-4 度。spatial_join 逐对复算预算 20000 对，超限降级。
7. **机制仪器未建**：论文实验装置（机制开关全套/变异缺陷集）按 2026-11-13 裁决推迟，本轮未建。
8. **服务区分析仍以缓冲＋融合建模**，未新建动词（动词数仍 40）。
9. 文件产物只开放 geojson；轴序解析只覆盖 WKT2 AXIS 节点；planner 目标句仍限 1–1000 字（长 URI 导入须走 task_plan 显式步骤）；HTTP 直传导入保持第一轮冻结语义（lineage.operation=None）。
10. 认证维持 **NOT_CERTIFIED**；G1 正式裁决、Muse 独立复验、公开发布均未执行。

## 七、红线自查

| 红线 | 自查结果 |
|---|---|
| 只进新目录 07 | PASS，15 新增＋11 修改全部在 07 内 |
| 06 目录零改动 | PASS，06 git 干净，HEAD 仍 2f02ee9 |
| 在线工作站 D:/agent-native-gis 零改动 | PASS，只读使用其 .venv 与 G1 基线哈希；git 状态干净 |
| G1 基线 59 文件一字不动 | PASS，59/59 哈希相符 |
| 工具计数 60/21/40/4、VID/规范名冻结 | PASS，实测复核；surface-v1.json 未改 |
| 全程离线 | PASS，0 次模型 API；原生后端在配置读取处即拒；MCP/表面测试用本地子进程与 MockTransport |
| 不推送、不发布、不动公开仓 | PASS，本地 git 无远端 |
| 测试实例用备用端口 | PASS，tmp_path 工作区＋临时端口，未占 8876 |
| 容差凭空编 | PASS，全部容差/界写明依据并登记 CHECK_REGISTRY；拿不准的降级无法确认（tile 字节重算、超预算复算、预演持久层） |
| 既有判定变化先报因 | PASS，逐 ID 对账零变化；唯一冲突（HTTP 导入谱系）回退改动守冻结，第五节留痕 |
| 单点卡住超 30 分钟 | 本轮无；7 个实测问题均当轮定位修复并留痕 |
| 不静默跳过验收项 | PASS，全部验收项自动化跑出；10 项环境阻塞保留红灯附对照 |

## 八、待 Muse 核验的入口

1. 复跑：`python scripts/golden_chain_report.py --root . --workbench .. --out evidence`（约 3 分钟，全离线）。
2. 只读对账：`evidence/golden-chain-report.json` 的 `baseline_reconciliation`（273↔273 零变化）、`g1_baseline.files`、`file_manifest`、`predicate_inventory`、`regression.cases`、`acceptance`、`rollout_acceptance`、`failure_classification`。
3. 代码级同一性：`tests/test_rollout_commit_preview.py::test_preview_execute_verify_share_predicate_objects`（id() 级）与 `test_preview_and_execute_run_the_same_invariant_checks`（check 集相等）。
4. 三处投影一致性：`tests/test_rollout_projection.py::test_three_surfaces_emit_the_identical_projection_for_vector`。
5. 假成功防线：`tests/test_rollout_native_predicates.py::test_raster_calc_pixel_tamper_is_named`（numpy 布尔缺陷的回归守卫）。
6. 原始基线对照：`evidence/baseline-full-suite.log`（开工前 273/263/10）与 `evidence/regression-full-suite.log`（收工 357/347/10）。

—— 执行代理小Q登记，2026-11-14。本报告不预判验收结论。