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
