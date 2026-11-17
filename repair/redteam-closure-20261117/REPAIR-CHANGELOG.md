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
