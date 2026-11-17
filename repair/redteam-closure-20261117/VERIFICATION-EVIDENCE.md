# VERIFICATION-EVIDENCE — 执行证据与环境记录

- 采集日期：2026-11-17（北京时间）
- 执行者：本轮复验执行方（经 Tailscale SSH 在鱼鱼笔记本上操作，隔离目录 `C:\Users\RAHUYU\repair-verify-20261117\`）

## 1. 环境（实测）

| 项 | 值 |
|---|---|
| 解释器 | `C:\Users\RAHUYU\WorkBuddy\2026-11-14-03-15-42\clean-venv-v05\Scripts\python.exe` |
| Python | 3.12.9 |
| pytest | 9.1.1 |
| shapely / rasterio | 2.1.2 / 1.5.2（GDAL 3.12.2 据修复方环境记录，本轮未单独复测 GDAL 版本） |
| 仓库 | 08 树，分支 `repair/redteam-closure-20261117`，基线 commit `78f0452e27fde42e77c5f6e3471b9b5eb38bafd1` |
| 引擎源码摘要 | `10492781ee3c51d523ff9d292b043933fbb6094f7314ba3f240429283522b288`（本轮以仅依赖 hashlib/json 的自写脚本独立复算，34 个文件，与交付清单一致） |

## 2. 关键哈希

| 对象 | SHA-256 |
|---|---|
| D 盘原版探针 `probes.py`（原件与本轮隔离副本相同） | `56ffb951b27f46f302d5245d5e66e05ed2da0a2ccad510d3b8d4a9377592ceab` |
| 本轮全量日志 `logs/full-suite-20261117.log` | `e6129363f001289e548d8e90cb91b1fc538729ebedeac12a174833d444cf3989` |

## 3. 实际执行命令（按序）

```text
# 基线与分支
git status / git log            # HEAD=78f0452e，工作区仅 3 个未跟踪 evidence 项（未动）
git switch -c repair/redteam-closure-20261117

# 探针哈希三方核对（D 原件 / 证据包副本 / 隔离副本）
certutil -hashfile D:\agent-native-gis\evidence\redteam-20261116\probes.py SHA256
certutil -hashfile evidence\redteam-fix-20261116\probes_original.py SHA256   # 三者同为 56ffb951…

# D 原版探针 × 08 树（隔离目录运行）
<venv>\python.exe runner_dprobes.py        # → DEFECTS_STILL_REPRODUCING 0 / 13, CASE_ERRORS {}

# 源码摘要独立复算（不 import definitio）
<venv>\python.exe recompute_digest.py      # → FILES 34, SOURCE_DIGEST 10492781…

# 全量套件（计划任务托管，TMP/TEMP 与 --basetemp 分离，均在树外）
<venv>\python.exe -m pytest tests/ evidence/redteam-fix-20261116/test_redteam_regression.py \
    -q -p no:cacheprovider --basetemp <树外目录>   # → 403 passed, EXITCODE 0

# focused 集
<venv>\python.exe -m pytest tests\test_golden_g1_guard.py \
    evidence\redteam-fix-20261116\test_redteam_regression.py -q -p no:cacheprovider
                                                  # → 20 passed in 4.68s

# 定点核查（隔离工作区）
<venv>\python.exe n_checks.py              # N-01 / N-02(schema 路径) / N-03
<venv>\python.exe n02b.py                  # N-02 授权层直建 grant 的计数核查

# 8876 只读观察（未重启、未改配置）
Get-NetTCPConnection -LocalPort 8876; Get-CimInstance Win32_Process
# → PID 58636（父 55484），2026-11-14 01:22:45 启动，解释器 D:\agent-native-gis\.venv
```

运行方式说明：全量套件首次以 Start-Process 启动后随 SSH 会话结束被回收（无日志），改用计划任务托管后正常跑完；该计划任务已在收工后删除。此过程如实记录，不影响结果计数（最终日志完整、退出码 0）。

## 4. 原始日志位置

全部在本提交的 `repair/redteam-closure-20261117/logs/` 内：`full-suite-20261117.log`、`focused-guard-regression-20261117.log`、`phase0-dprobes.log`、`phase0-dprobes-result.json`、`n-checks-result.json`、`n02b-result.json` 及三个运行脚本。隔离目录原件在笔记本 `C:\Users\RAHUYU\repair-verify-20261117\`（树外，未提交）。

## 5. 独立 Oracle 说明

- 本轮探针与回归均为红队/修复方既有断言的**复跑**，未新造判定标准；探针文件逐字节为红队原件。
- 空间验证的同核局限与红队报告、双方修复记录一致：同核重算只能证明自洽，不能替代独立几何真值。本轮未引入独立 oracle（属研究设计变更，不在本轮授权内）。
- 摘要复算脚本不 import 被测包，避免自证。

## 6. 边界遵守

未 push、未动 main、未动已发布提交；未触碰 8876 服务（仅只读查询）；未修改 D 盘任何文件（仅只读复制其探针与 repair-20261117 交付物到隔离区）；未修改原始红队证据与修复方证据包；未调用付费模型或第三方服务；未改动冻结的科研实验设计、任务集、评分 Oracle 与预注册内容。


---

# 增补（2026-11-17 晚，修复轮证据）

## A1. 环境与代码状态（修复轮实测）

| 项 | 值 |
|---|---|
| 解释器 | 笔记本系统 Python 3.12.9（`C:\Users\RAHUYU\AppData\Local\Programs\Python\Python312\python.exe`），pytest 9.1.1——与上轮 clean-venv 同版本线 |
| 分支 | `repair/redteam-closure-20261117`（上一提交为复验交付，本轮修复单独成提交） |
| 生产代码变更 | `definitio/agent.py`（+26/−3）、`definitio/guidance.py`（+3）、`definitio/surfaces/surface-v1.json`（+2），共 3 文件 +31/−3 |
| 引擎源码摘要（修复后） | `7b767759ea48dd18b957813ff29ee2a96960127651f2c30698fc5c6c8f7eea98`（34 文件；仅 hashlib/json 独立复算，不 import definitio；相对 `10492781…` 的差异文件与 git diff 一致：agent.py、guidance.py） |

## A2. 实际执行命令（修复轮，按序，节选关键）

```text
# D 原版探针（隔离目录 C:\Users\RAHUYU\xfix-verify-20261117；probes.py 自 D 原件重新复制）
Get-FileHash probes.py SHA256   # → 56ffb951…（与红队原件一致）
python runner_dprobes.py        # → DEFECTS_STILL_REPRODUCING 0 / 13, CASE_ERRORS {}

# 专项与移植
python -m pytest tests/test_repair_x01_x02.py -q    # → 8 passed
python -m pytest tests/test_repair_redteam.py -q    # → 28 passed, 19 failed（归因见 REDTEAM-REGRESSION 增补 A3）
python -m pytest tests/test_repair_redteam.py -q --tb=line   # 逐项日志 logs/ported47-detail-20261117.log

# 首轮全量（如实记录）：python -m pytest tests/ -q
# → 409 passed, 26 failed, 9 errors；其中 19 failed 为移植文件（已归因），
#   余 7 failed + 9 errors 全部由两处登记/同步缺失引起：
#   (a) guidance.RULES 未登记 committed_receipt_is_not_current_state
#       → test_error_echo_rules_are_all_published 等 3 项；
#   (b) surfaces/surface-v1.json 参数表未同步新字段
#       → Surface 构造期 SurfaceError，test_surface 9 errors + 1 failed 等。
# 补齐 (a)(b) 后受影响集复跑：tests/test_rollout_discovery.py + test_v05_protocol.py
#   + test_surface.py + test_repair_x01_x02.py + test_agent.py → 71 passed。

# 最终全量（补齐后整包复跑）
python -m pytest tests/ -q      # → 34 failed, 410 passed, 3 warnings in 216.31s（444 项）
                                # 日志 logs/full-suite-xfix-20261117.log，SHA-256 368f9683fa23ef0c526e54eb92a48c09cb06f837a227aa1d1dce39d400cdb4d1

# 摘要独立复算
python recompute_digest.py      # → FILES 34, SOURCE_DIGEST 7b767759…
```

## A3. 边界遵守（修复轮）

未 push、未动 main、未动已发布提交；8876 未触碰（未查询、未停、未重启）；D 盘仅只读复制 `probes.py` 与 `tests/test_repair_redteam.py` 两个文件；原始红队证据与修复方证据包未改（探针在隔离目录运行）；未调用付费模型或第三方服务；未改动冻结的科研实验设计、任务集、评分 Oracle 与预注册内容。本轮新增的隔离目录 `C:\Users\RAHUYU\xfix-verify-20261117\` 在树外，未提交。
