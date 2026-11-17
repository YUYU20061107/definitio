# REDTEAM-REGRESSION — 本轮真实运行结果（08 发布树）

- 日期：2026-11-17
- 代码：08 发布树，分支 `repair/redteam-closure-20261117`，commit `78f0452e`，引擎摘要 `10492781…`
- 原则：只记本轮实际运行。SKIP / BLOCKED / UNKNOWN 不计 PASS；未跑的明说未跑。

## 1. 历史 13 反例（D 盘原版探针 × 当前 08 树）

- 探针：`D:\agent-native-gis\evidence\redteam-20261116\probes.py` 原件复制（SHA-256 `56ffb951…`，运行器内自校验），隔离目录运行，`PYTHONPATH` 指向 08 树。
- 结果：**DEFECTS_STILL_REPRODUCING 0 / 13，case 异常 0**。逐条布尔见 `logs/phase0-dprobes-result.json`。
- 对照基准（历史）：同一探针在未修复代码上为 13 / 13（红队原报告与两轮修复的基线记录一致）。

## 2. 针对性回归与 G1 守卫（focused 独立日志）

命令：`pytest tests/test_golden_g1_guard.py evidence/redteam-fix-20261116/test_redteam_regression.py -q -p no:cacheprovider`（TMP/TEMP 与 basetemp 分离，树外目录）

- **20 passed in 4.68s**（G1 守卫 6 + 红队正确行为回归 14），日志 `logs/focused-guard-regression-20261117.log`。
- 红队原报告中的 2 项 G1 守卫失败（104/106 的余项）在此树上不存在：守卫已改为随包基线 + 显式根参数，6 项全过，含缺证据负例（守卫不橡皮图章）。

## 3. 全量回归（本轮独立整包复跑，补 2026-11-17 验收的限制项）

命令：`pytest tests/ evidence/redteam-fix-20261116/test_redteam_regression.py -q -p no:cacheprovider --basetemp <树外独立目录>`，无任何 `-k`/`-m` 过滤。

- **403 passed, 0 failed, 0 skipped, 0 deselected, 1 warning，799.31s，退出码 0**。原始日志 `logs/full-suite-20261117.log`（SHA-256 `e6129363f001289e548d8e90cb91b1fc538729ebedeac12a174833d444cf3989`）。
- 计数与修复方自报的最终全量（403 passed）一致，但这是本轮在当前 commit 上的独立复跑结果，不是引用。
- 耗时长于修复方记录（311s）：本机同时有其他负载（8876 服务、桥接进程等在运行），仅作环境说明，不影响计数口径。
- D 盘平行修复中出现的 13 项环境阻塞（G1×2、schtasks×1、离线端点×10）在本树本轮**均未出现**：同类用例包含在 403 项内并全部通过。此差异源于树与环境不同（08 守卫不依赖外部工作台根；本机端点类用例在本环境可达），不据此评价对方定性。

## 4. 定点核查（本轮新构造，非既有套件）

| 用例 | 结果 | 判定 |
|---|---|---|
| N-01 task_get（输出数据集被删后） | `ok=false / NOT_FOUND`，无状态信息 | **缺陷存在（X-01）** |
| N-02 导出计数（2 导出 vs max_file_exports=1，授权层直建 grant） | 写前 AUTHORIZATION_SCOPE 拒绝，零文件、零写入 | 缺陷不存在 |
| N-03 经工具面授予 file_export 能力 | INVALID_ARGUMENT（schema 无此字段） | **缺陷存在（X-02，变体）** |
| N-04 规则登记（静态核查） | TARGET_STATE_UNKNOWN 有已发布指引条目；RULES 为计划契约注册表 | 同类问题不存在 |

脚本与原始结果：`logs/n_checks.py`、`logs/n-checks-result.json`、`logs/n02b.py`、`logs/n02b-result.json`。

## 5. 本轮未跑（如实留账）

- 变异集（六类 18 变异 + 12 正例）：未独立复跑。修复方记录 FA 0/18、FR 0/12 在案，不冒充本轮结果。
- RT-07 相位故障注入：未独立复跑。修复方记录 19/19 在案。
- 相同输入重复执行/状态泄漏：包含在全量套件既有用例内（通过），未另行构造专项。
- 保留反例过拟合检查：本轮探针即红队原件，未参与任何调参；08 树代码本轮未改，不存在为本轮结果调参的可能。


---

# 增补（2026-11-17 晚，修复轮真实运行结果）

- 代码：同分支，含 X-01 / X-02 修复（`definitio/agent.py`、`definitio/guidance.py`、`definitio/surfaces/surface-v1.json`），引擎摘要 `7b767759…`。
- 原则不变：只记本轮实际运行；失败如实记；未改弱任何断言。

## A1. 历史 13 反例（修复后复跑）

- 同一运行器（`logs/runner_dprobes.py`），探针从 **D 盘原件重新复制**到隔离目录 `C:\Users\RAHUYU\xfix-verify-20261117\` 并校验 SHA-256 `56ffb951…`。
- 结果：**DEFECTS_STILL_REPRODUCING 0 / 13，case 异常 0**。结果文件 `logs/xfix-dprobes-result-20261117.json`。

## A2. X-01 / X-02 专项回归（`tests/test_repair_x01_x02.py`，新文件）

**8 passed。** 其中一条如实记录：X-01 的 verify 路断言按 08 既有语义落账——输出数据集缺失时该步无法重判被 skip，verify 状态为 `incomplete`，同时 `live_status.state=corrupted`、`datasets[0].state=missing_or_corrupt`、`ok=false`。这与 D 侧「output_state_binding finding + failed 状态」形态不同、事实层一致（两边都不报已验证）。作者首版测试曾误断言 `corrupted` 状态标签，实测后按 08 真实语义改正并在此留痕，非放宽：核心断言（ok=false、live_status 标缺失、回复 TARGET_STATE_UNKNOWN）全部保留且通过。

## A3. D 侧 47 项回归移植（`tests/test_repair_redteam.py`）逐项结论

移植方式：逐字移植 + 唯一夹具适配（`grant()` 将 file_export 由 verb 译为 capabilities/max_file_exports，文件头 PORTING NOTE 已声明）。结果：**28 passed / 19 failed / 0 skipped**，原始日志 `logs/ported47-detail-20261117.log`。

通过的 28 项包括全部合法正例（buffer 几何族 3 参数化、MultiPoint buffer、clip 重复属性、join 一对多/左外空值、overlay 交集/差集正例、hillshade 背光、空 reproject、空 clip、无效几何拒绝、栅格 NoData、轴顺序记录、规划后变更/占用/摘要拒绝）与关键负例（授权前缀越界、撤销后不可执行、产物名穿越 3 参数化、发布副作用后隔离 ISOLATED）。

19 项失败逐项归因（均为两树实现差异；无一项显示 08 存在「错误结果被认证」或「越权写入已发生」的路径）：

| # | 用例 | 08 实际结果 | 初步归因 |
|---|---|---|---|
| 1 | test_buffer_rejects_a_result_at_the_wrong_position | 任务 FAILED，check=`buffer_positive_containment` | 谓词命名差异（D 名单为 buffer_offset_distance 等）；拒绝成立 |
| 2 | test_buffer_rejects_an_unbuffered_identity_result | FAILED，check=`buffer_positive_expansion` | 同上 |
| 3 | test_buffer_rejects_a_wrong_distance_multiple | FAILED，check=`buffer_same_kernel_recompute` | 同上 |
| 4 | test_clip_rejects_a_result_outside_the_mask | FAILED，check=`clip_completeness` | 同上（D 名单 clip_geometry_index_aligned 等） |
| 5 | test_clip_rejects_a_result_that_lost_area | FAILED，check=`clip_completeness` | 同上 |
| 6 | test_overlay_difference_that_keeps_the_subtracted_region_is_rejected | FAILED，check=`clip_completeness` | 同上 |
| 7 | test_join_rejects_a_wrong_joined_attribute | FAILED，check=`join_right_attributes_binding` | 同上（D 名 join_record_correspondence） |
| 8 | test_join_rejects_a_swapped_right_record | FAILED，check=`join_right_attributes_binding` | 同上 |
| 9 | test_the_reservation_counts_the_file_write_too | max_writes=1 + max_file_exports=1 下执行成功 | 授权计数模型差异：D 为数据集与文件共用同一预留计数；08 为分类别独立上限，各自不超限即放行。08 的超上限拒绝已由上轮 n02b 与本轮 X-02 专项证明（写前拒绝、零落盘），非检查缺失 |
| 10 | test_replay_reports_a_missing_file_instead_of_the_old_receipt | 抛 TARGET_STATE_UNKNOWN，但 08 文案为「已提交任务的承诺产物实时读回未确认…」 | 错误文案差异；代码与行为一致 |
| 11 | test_verify_reports_a_missing_file_as_not_ok | verify `ok=false` 成立；08 无 `artifact_state_binding` 逐项 finding，事实由 live_status 承载 | verify 报告契约差异 |
| 12 | test_verify_reports_a_deleted_output_dataset_as_not_ok | verify `ok=false` 成立；08 无 `output_state_binding` finding，live_status.datasets 标 missing_or_corrupt | 同上 |
| 13 | test_verify_of_a_failed_task_is_not_ok | status=`incomplete`（D 期望 `task_not_committed`），coverage 键名不同 | 同上 |
| 14 | test_verify_detects_a_tampered_read_only_result | `ok=false` 成立；08 的 finding 名为 `persisted_result_binding`（D 名 readonly_result_binding） | 同上（含命名） |
| 15 | test_verify_of_a_committed_task_reports_verified_with_coverage | status=`complete`（D 期望 `verified`），coverage 结构不同（expected/checked/skipped + live_status） | 同上 |
| 16 | test_publish_transport_failure_before_any_write_is_not_isolated | 任务 **ISOLATED**（D 期望 FAILED） | 发布故障分级差异：08 对 publish 路径的 BACKEND_ERROR 一律隔离（无法证明外部未动作的保守口径）；D 按「写前传输失败」判 FAILED。**留 Muse 裁决** |
| 17 | test_a_publication_that_vanishes_is_rolled_back_and_can_be_retried | task FAILED 成立；回滚后 `file_artifacts()` 仍保留登记记录（D 期望清空登记并以 events 表记 rolled_back；08 无 file_artifact_events API） | RT-07 记账模型差异：08 保留登记作状态记录 |
| 18 | test_a_corrupted_publication_is_not_silently_rolled_back | FAILED + TARGET_STATE_UNKNOWN + 登记保留 1 条均成立；但 08 删除了字节不一致的已发布文件（D 期望保留坏字节作证据） | 证据保留策略差异。**留 Muse 裁决** |
| 19 | test_file_artifact_confinement_rejects_escapes_by_resolved_path | AttributeError：08 verifier 无 `file_artifact_confinement` 函数 | 实现形态差异：08 的文件产物禁锢在 commit.readback / artifact_paths 与计划期名称校验一侧（名称穿越用例在 08 通过），无同名谓词可调 |

## A4. 全量套件（修复后整包复跑）

命令：`python -m pytest tests/ -q`（树根，无过滤）。

- **34 failed, 410 passed, 3 warnings in 216.31s（444 项）**
- 原始日志 `logs/full-suite-xfix-20261117.log`（SHA-256 `368f9683fa23ef0c526e54eb92a48c09cb06f837a227aa1d1dce39d400cdb4d1`）。
- 与基线对照：上一轮（修复前）同口径为 403 passed / 0 failed。本轮集合变化 = +8（X 专项）+47（移植）+ 既有 389 = 444 项；34 项失败的构成为：移植文件 19 项（已逐项归因，见 A3，含 2 项待 PI 裁决的语义分歧）＋手册摘要漂移 5 项（X 修复改动引擎源码后 HANDBOOK.md 未同步再生成所致，后续更正提交已再生成治本）＋native_completion 10 项（执行时笔记本刚重启、Docker 托管的三个后端未起，同代码在重启前同一套件中全过，属环境性失败）；既有套件与新增专项除上述手册漂移 5 项外无其他失败，34 项无一显示错误认证或越权写入。

## A5. 本轮仍未跑（如实留账）

变异集独立复跑、RT-07 崩溃窗口故障注入、授权撤销竞态：状态与上文 §5 相同，仍未独立复跑（C4 未全关）。移植文件中与发布故障注入相关的用例（A3 第 16–18 项）是 D 侧口径的注入实测，可作 C4 的部分参考，但不冒充 08 口径的 RT-07 注入复验。
