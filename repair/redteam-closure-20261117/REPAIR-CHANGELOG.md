# REPAIR-CHANGELOG — 本轮（红队闭环复验）变更记录

- 日期：2026-11-17
- 分支：`repair/redteam-closure-20261117`（基线 `78f0452e`）
- 基线回退点：commit `78f0452e27fde42e77c5f6e3471b9b5eb38bafd1`（已发布门面修复）

## 1. 生产代码变更

**无。** 按上级指令，本轮（Phase 0 双树对账 + 独立复验）不许动 08 树代码。引擎源码摘要在本轮始终为 `10492781ee3c51d523ff9d292b043933fbb6094f7314ba3f240429283522b288`（开工与收工两次独立复算一致），与 RT-FIX 交付摘要相同。

本轮登记但**未实施**的修复（待逐项批准后另行施工）：

| 项 | 拟改文件 | 内容 | 兼容性 |
|---|---|---|---|
| X-01 | `definitio/agent.py` `_task_reply` | 输出数据集缺失时返回 TARGET_STATE_UNKNOWN + unavailable_outputs，不再整体 NOT_FOUND | 返回结构增量（新增字段），`ok` 语义不变 |
| X-02 | `definitio/agent.py` `authorization_grant` 工具 schema | 增补 `capabilities`、`max_file_exports` 两个可选字段，与授权层对齐 | 增量字段；旧调用不受影响 |

## 2. 本轮新增文件（全部在本目录 `repair/redteam-closure-20261117/`）

| 文件 | 说明 |
|---|---|
| `ORDER.md` | 行动令原文存档 |
| `REPAIR-AUDIT.md` | Phase 0 矩阵、树身份对照、双树对账、新发现登记 |
| `REPAIR-CHANGELOG.md` | 本文件 |
| `REDTEAM-REGRESSION.md` | 本轮真实运行结果 |
| `VERIFICATION-EVIDENCE.md` | 命令、环境、哈希、日志位置 |
| `RELEASE-DECISION.md` | 本轮裁决 |
| `logs/full-suite-20261117.log` | 全量套件原始日志（403 passed，EXITCODE 0） |
| `logs/focused-guard-regression-20261117.log` | G1 守卫 + 红队回归 focused 日志（20 passed） |
| `logs/phase0-dprobes.log` / `phase0-dprobes-result.json` | D 盘原版探针 × 08 树复跑输出与逐条布尔 |
| `logs/runner_dprobes.py` | 上项的运行器（探针哈希自校验） |
| `logs/n_checks.py` / `n-checks-result.json` | X-01 / X-02 / N-02 计数三项核查脚本与结果 |
| `logs/n02b.py` / `n02b-result.json` | 授权层直建 grant 的导出计数核查 |

## 3. 测试影响

- 未修改、未删除任何既有测试；未调整任何断言或容差。
- 本轮运行：全量 403 项、focused 20 项、探针 13 例、定点核查 4 例，均为只读执行（隔离临时目录），未向仓库写入测试产物（pytest basetemp 与 TMP/TEMP 均指向树外目录）。

## 4. 回退方法

本轮全部变更都在分支 `repair/redteam-closure-20261117` 的一个提交内（仅新增 `repair/` 目录文件）。回退 = 删除该分支或对该提交做 revert；基线 `78f0452e` 与已发布内容不受影响。未 push、未动 main、未动已发布提交。


---

# 增补（2026-11-17 晚，修复轮：X-01 / X-02 实施 + D 侧 47 项回归移植）

- 批准依据：鱼鱼 2026-11-17 21:42 批准打包方案——即上文 §1 表格登记的 X-01、X-02 两项（此前「只登记不修」），本轮实施；另含 D 侧 47 项回归移植（仅测试）。
- 分支不变：`repair/redteam-closure-20261117`。本轮改动单独成一个提交，叠在上一提交（复验交付）之上。

## A1. 生产代码变更（3 个文件，+31 / −3）

| 项 | 文件 | 内容 | 兼容性 |
|---|---|---|---|
| X-01 | `definitio/agent.py` `_task_reply` | 输出逐个核算：可读的照常描述；缺失的记入新增字段 `unavailable_outputs`（含 ref 与底层错误 payload）；存在缺失且任务非 FAILED/ISOLATED 时，回复改为 TARGET_STATE_UNKNOWN 失败（rule=`committed_receipt_is_not_current_state`），data 仍带完整 task summary。FAILED/ISOLATED 分支顺序不变 | 回复新增字段；原「整体裸 NOT_FOUND」的情况改为带状态的 TARGET_STATE_UNKNOWN（`ok` 仍为 false） |
| X-01 登记 | `definitio/guidance.py` `RULES` | 新增规则条目 `committed_receipt_is_not_current_state`（where/meaning/fix），使该 rule 名进入已发布规则表（discovery audit 要求所有被回显的 rule 必须已发布） | 增量条目 |
| X-02 | `definitio/agent.py` `authorization_grant` 工具 schema | 增补可选字段 `capabilities`（枚举与 `authorization.EFFECT_KINDS` 一致）与 `max_file_exports`（整数 0–100，缺省 0）；工具描述补一行说明。授权层逻辑未改 | 增量可选字段；旧调用不受影响 |
| X-02 同步 | `definitio/surfaces/surface-v1.json` | `authorization_grant` 的 parameters 增补同名恒等映射两行（Surface 层要求其参数注册表与引擎 schema 严格一致） | 表面参数表增量；surface profile 哈希随之变化 |

修复后引擎源码摘要：`7b767759ea48dd18b957813ff29ee2a96960127651f2c30698fc5c6c8f7eea98`（34 文件，同口径独立复算；相对 RT-FIX 的 `10492781…`，差异仅 agent.py 与 guidance.py 两文件，与 git diff 一致）。

过程记录（如实）：首轮全量复跑曾出现 7 失败 + 9 错误，全部源于本轮两处登记/同步未做（RULES 未登记、Surface 参数表未同步），补齐后受影响集 71 项全过、全量复跑见 REDTEAM-REGRESSION 增补。无任何断言被改弱。

## A2. 新增测试文件

| 文件 | 内容 | 本轮结果 |
|---|---|---|
| `tests/test_repair_x01_x02.py` | X-01 四项（输出数据集删除后 task_get 普通路、verify 路、文件侧对称例、健康正例）+ X-02 四项（经 schema 授予 capabilities/max_file_exports、未知能力仍被拒、上限内导出成功、超上限写前 AUTHORIZATION_SCOPE 拒绝且零落盘、任务保持 DECLARED） | **8 passed** |
| `tests/test_repair_redteam.py` | D 侧 47 项回归移植：逐字移植，仅一处夹具适配——`grant()` 把 D 的「file_export 作 verb」译为 08 的 capabilities + max_file_exports（见文件头 PORTING NOTE）；断言一字未改 | **28 passed / 19 failed**（逐项归因见 REDTEAM-REGRESSION 增补 A3；失败全部保留） |

## A3. 本轮新日志（logs/）

| 文件 | 说明 |
|---|---|
| `logs/xfix-dprobes-result-20261117.json` | 修复后 D 原版探针 × 08 树复跑结果（0/13，探针自 D 原件重新复制并校验哈希） |
| `logs/ported47-detail-20261117.log` | 移植 47 项逐项结果（`--tb=line`） |
| `logs/full-suite-xfix-20261117.log` | 修复后全量套件原始日志（最终轮） |

## A4. 回退方法（增补）

回退本轮 = revert 本轮提交：`definitio/agent.py`、`definitio/guidance.py`、`definitio/surfaces/surface-v1.json` 回到上一提交状态，两个新增测试文件删除；不影响上一提交（复验交付）与基线 `78f0452e`。未 push、未动 main、未动 8876。
