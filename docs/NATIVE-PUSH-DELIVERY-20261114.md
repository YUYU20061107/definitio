# 第三轮长线工程交付报告（v05 native-push）

日期：2026-11-14　执行：小元　工作树：`08-runtime-v05-native-push`（本地分支 `native-push-v05`，无远端）
起点：`07-runtime-v04-rollout` 终局提交 `4040567`
认证状态：**NOT_CERTIFIED**（全文无任何升级表述）

## 0. 结论摘要

| 轨道 | 状态 | 关键数字 |
|---|---|---|
| T1 协议加固 | **完成** | 新增 12 项测试；disposition 三值、STALE/AMBIGUOUS、读取三态全部落地 |
| T2 对象头规范 | **完成** | 新增 3 项测试；40/40 动词过四槽校验，零空缺 |
| T3 干净安装 | **完成** | 全新 venv；修复 1 处真实声明缺口；干净环境全量 372 项 / 361 通过 / 11 失败 |
| T4 性能基线 | **基线完成，优化未交付** | 四组数字落盘；头号热点定位到 SQLite 连接生命周期（60%），池化被实测证明不安全 |
| T5 手册随行 | **完成** | 新增 6 项测试；手册由活目录生成，含 40 动词 / 60 工具 / 23 错误码 |
| T6 自检＋暂存 | **完成** | 自检 21 案、8 类违规全数检出；暂存树 12 文件、树哈希已入清单 |
| T7 环境阻塞 | **阻塞（留痕）** | Docker 守护进程未运行、本地 PG 需口令、无 QGIS；交配置包＋卡点说明 |

全量回归：**385 项 / 374 通过 / 11 失败 / 0 错误 / 294.5 s**；G1 基线 **59/59 PASS**；发现层审计零不一致。
既有 357 项中 **356 项判定不变，1 项变化**，归因为本会话安全策略漂移，已在基线代码上复现（见 §5）。

## 1. 边界与红线执行情况

- 08 树由 07 树复制新建（`data/` 113 MB 测试工作区不复制，`evidence/` 只保留 4 个离线夹具）。
- 06 / 07 两树全程只读；`D:\agent-native-gis` 部署与 8876 在线引擎未启动、未停止、未重配。
- G1 基线 59 个冻结文件一字未改（`g1_baseline.status=PASS`，59/59 unchanged）。
- 全程零真模型调用、零外部业务 API、零付费动作；未 push、未 publish、未动公开仓。

## 2. T1 协议加固

新增 `definitio/protocol.py`（additive，不改既有字段语义）：

- **disposition 三值**：`delivered_verified` / `delivered_unverified` / `not_delivered`。写路径在 `Runtime._execute_step` 构造证据信封时按实际交付与核验事实标注；读路径（`evidence_get`、`artifact_view`）只透出、不重标。
- **`disposition_basis`**：把判定依据（是否落库、核验是否通过、检查通过数、unconfirmed 段数、oracle 说明）一并写入信封，验收方可重算。
- **口径校正（重要）**：真实写路径恒有 6 段「同核自洽、无独立 oracle」的 unconfirmed 记录。若按「unconfirmed 必须为空」严判，将永远得不到 `delivered_verified`，三值退化为两值。故 verified 判据定为「成果已落库 + 读回核验真跑且全通过」，unconfirmed 段如实计入 basis，且**不升级认证**。此改动已同步写入契约增补并在测试中断言。
- **STALE / AMBIGUOUS**：`protocol.resolve_handle()` 按「角色 + 稳定字段」去重计数，0 命中抛 `STALE`，≥2 命中抛 `AMBIGUOUS`，值与范围（value/bounds/count 等）不作身份、误用抛 `INVALID_ARGUMENT`。两码已登记进 `agent.RECOVERY`，发现层自动拾取（`raised_codes`）。
- **既有句柄路径不动**：`Store.dataset()` 的 `NOT_FOUND` 语义保持原样，新协议面独立存在——这是 357 项判定零变化的必要前提。
- **读取三态**：`protocol.read_state()` 返回 `Known` / `Absent` / `Unknown`；读失败或文档畸形一律 `Unknown`，**不塌成 `Absent`**。
- **契约同步**：G1 契约正文位于 `02-g1-gate` 冻结区、不可改，故在 08 树内写活文档 `docs/CONTRACT-ADDENDUM-v0.5.md`，明确「增补不覆盖冻结正文」。
- 测试：`tests/test_v05_protocol.py` 12 项（三值各一例、0/≥2 命中分码、重复观测计 1、注入读失败、枚举顺序、信封 basis 断言）。

## 3. T2 对象头规范

- `definitio/semantics.py` 新增 `OBJECT_HEADER_SPEC` / `OBJECT_HEADER_SLOTS` / `validate_object_header()`；四槽定位：`crs`、`structure.fields`（字段摘要）、`structure.bounds|range|extent`（范围）、`identity.version`（版本）。
- `docs/OBJECT-HEADER-v0.1.md` 成文，写明四槽位置、缺失判定、以及「分析结果头」的对应关系。
- 测试 `tests/test_v05_object_header.py` 3 项：**40/40 动词**逐一经引擎的两个头构造器（`object_header` / `result_header`）生成并校验，逐动词留 report，跳过一个即失败；四槽各自缺失时点名报错；真实运行时产物（复制数据集 + 量测结果）双路径校验。

## 4. T3 干净安装验证

- 全新隔离 venv：`C:/Users/RAHUYU/WorkBuddy/2026-11-14-03-15-42/clean-venv-v05`（Python 3.12.9，与 08 树无共享 site-packages）。
- **发现的真实缺口（已修）**：`scripts/golden_chain_report.py` import `defusedxml`，07 的 `requirements.txt` 未声明。07 复用带 `--system-site-packages` 的 venv 时被掩盖，干净 venv 会直接 ImportError。已补 `defusedxml==0.7.1` 声明，并在 `docs/CLEAN-INSTALL.md` 记为「缺口 1」。
- 安装结果：`pip install -r requirements.txt` 成功（`PIP_EXIT=0`），`pip freeze` 全量入 `evidence/clean-install-pip.log`。
- 干净环境自检 `scripts/clean_install_selftest.py`：14 项全绿——模块 import、隔离 Runtime 启动、60 工具/40 动词、真实写入链 COMMITTED、证据 disposition 与摘要重算、发现层审计、进程内 HTTP manifest。**不绑端口、不写 D 盘、不碰 8876。**
- 干净环境全量复跑：`372 项 / 361 通过 / 11 失败 / 0 错误 / 236.19 s`（`evidence-clean/`）。
  11 失败 = 既有 10 项环境阻塞 + §5 所述 `schtasks` 被策略拦截那 1 项。
- 隐含工作台依赖逐条显式化（8 条），见 `docs/CLEAN-INSTALL.md` 第 4 节。

## 5. 全量回归对账（红线 4）

| 口径 | 基线（08 树开工前） | 终局 |
|---|---|---|
| 用例总数 | 357 | 385 |
| 通过 | 347 | 374 |
| 失败 | 10 | 11 |
| 错误 | 0 | 0 |
| 墙钟 | 148.29 s | 294.52 s |
| 既有 ID 判定 | — | 356 不变 / **1 变化** |
| 新增 ID | — | 28（12+3+6+7） |
| G1 基线 | 59/59 | 59/59 PASS |
| 发现层审计 | ok | ok（60 工具零不一致） |

**唯一变化项**：`tests.test_mcp::test_cold_start_survives_mcp_host_disconnect`（PASS → FAIL）。

- 失败点不在断言，而在 `finally` 收尾：该用例调用 `schtasks.exe /End|/Delete` 清理自己创建的引擎任务，本会话安全策略把 `schtasks.exe` 列入程序黑名单，`CreateProcess` 直接 `PermissionError [WinError 5]`。
- 归因证据一：`git diff 4040567 -- tests/test_mcp.py` 为空——该测试文件本轮**一字未改**。
- 归因证据二：在基线提交 `4040567` 的原始代码上（独立 worktree）复跑同一用例，同样 `WinError 5` 失败。
- 归因证据三：同一用例在基线运行（策略生效前）为 PASS，说明这是**策略漂移**而非代码回归。
- 处理方式：不修改既有测试去吞掉异常（那等于篡改门禁），也不谎报零变化。**此项按严格口径计为红线 4 未完全达成，请验收方裁定是否接受归因。**

另：早前一次中期全量跑出现 1 项同类变化（同一用例），当时判为「间歇性」，本轮已查明真实原因，此处更正记录。

## 6. T4 性能基线

数据：`evidence/perf-baseline-v05.json`；分析：`docs/PERF-v05.md`。

- 黄金链六步冷/热中位数（ms）：import 52.4/34.8、copy 351.8/295.8、buffer 352.6/316.1、dissolve 356.0/319.4、measure 201.0/181.3、validate 263.3/197.3。
- 全量墙钟 148.3 s（基线 XML 口径）。
- 发现层体积：full 134,037 B / compact 36,223 B（27.0%）；60 工具 schema 合计 68,872 B，按字节/4 粗估 17,218 token（**粗估，非真实 BPE**）。
- 头号热点（cProfile，单次 copy 0.252 s）：`Store.connect()` 累计 0.152 s（60.3%），19 次新建连接 + 18 次提交；几何计算与 JSON 序列化合计 < 0.02 s。
- **本轮未交付优化（shipped: false）**：唯一实质解法是连接池化，但实测「常驻连接 → 删除同一 sqlite 文件」在 Windows 上 `PermissionError [WinError 32]`，会破坏每个测试的临时工作区清理（与本轮早前 328 个 setup ERROR 同一失效模式）。其余微优化只影响 19 次连接中的 2 次，低于采样噪声。故不做名义优化，只交基线与决策依据。

## 7. T5 手册随行

- 新增 `definitio/handbook.py`：只从活对象渲染（`Runtime.capabilities()`、`service.tools`、`RECOVERY`、`RULES`、`DISPOSITION_*`、`READ_STATES`），模块内不存任何动词/错误码/规则字面量。
- CLI：`scripts/generate_handbook.py --out docs/HANDBOOK.md`，`--check` 只比对不写。
- 产出 `docs/HANDBOOK.md`：40 动词（VID/层级/家族/必需参数/实现状态）、23 错误码（含建议动作与可重试）、3 disposition、3 读取态、句柄计数规则、8 条规则；`source_digest=5a305bb9…`。
- 漂移测试 `tests/test_v05_handbook.py` 6 项：手册与活目录逐字节相等、注册表全集相等、v05 条款在场、**篡改目录能被检出**、CLI `--check` 退出码真实、JSON 渲染稳定。

## 8. T6 一致性套件自检＋发布暂存

- `scripts/consistency_selfcheck.py`：自植 21 个案例，其中 **8 个已知违规**（缺 disposition、非法值、`not_delivered` 冒充带输出、缺 crs/version/fields/extent 四槽、手册少一个动词），13 个干净样本。结果：违规 8/8 全检出、干净 13/13 无误报、`ok=true`。检不出即本轨失败。
- 发布暂存树 `release-staging-v05/`（**只组装，未发布、未推送**）：
  - `contract/`（契约增补 v0.5、对象头 v0.1）、`handbook/`、`install/`、`consistency-suite-v0.1/`（4 个测试 + 自检脚本 + README）、`evidence/`（自检报告、干净安装自检）。
  - `MANIFEST.json`：12 文件逐条 sha256 + 字节 + 树哈希 `30f345fd2cf7149b42ccc493f0c10873d518a193277a495bab6f96d0f417977b`；`published=false`、`pushed=false`、`stale_files=[]`。
  - 脚本拒绝静默删除：目标已存在时只覆盖并报告残留，不清理。

## 9. T7 环境阻塞清账（阻塞）

实测三项硬卡点：

1. Docker CLI 29.6.2 已装，守护进程未运行（`npipe:////./pipe/dockerDesktopLinuxEngine` 不存在）。
2. 本机 `PostgreSQL 16` 正在 5432 监听，但 `pg_hba.conf` 全为 `scram-sha-256`，无可用口令；不猜口令。
3. 未安装 QGIS / PyQGIS，而 `environment.py` 探测 PostGIS 与 MVT worker 都要求 QGIS Python。

故**未写** `data/backends.json`（写假凭据等于假报完成）。交付 `deploy/backends/`：`README.md`（键名契约、验证顺序、凭据纪律）、`backends.example.json`（键名与 `completion.py` 完全一致）、`docker-compose.template.yml`（仅回环、口令走环境变量、镜像 tag 标注为待核对）。
10 项阻塞用例保持红灯：1 项瓦片下载 + 8 项 STA 实体集 + 1 项 services 过滤。

## 10. 新文件与修改文件（sha256 前 16 位）

| 文件 | 字节 | sha256 |
|---|---|---|
| `definitio/protocol.py` | 7,138 | `e49483048c773dd8` |
| `definitio/handbook.py` | 6,442 | `469b02cd73eccd7d` |
| `definitio/runtime.py`（改） | 41,324 | `b5a66920a2489fcf` |
| `definitio/agent.py`（改） | 58,525 | `fe4dd6c3a6746e35` |
| `definitio/semantics.py`（改） | 10,717 | `925b1bf4bba62985` |
| `requirements.txt`（改） | 57 | `576a909a024621b5` |
| `scripts/golden_chain_report.py`（改） | 22,238 | `3c62b46f3612bc5a` |
| `scripts/generate_handbook.py` | 2,000 | `60605da3a6a618e5` |
| `scripts/consistency_selfcheck.py` | 8,026 | `5ebd9c6f79ff43ef` |
| `scripts/build_release_staging.py` | 5,029 | `fdc41042945ab468` |
| `scripts/perf_baseline.py` | 8,064 | `90fd793b875852ef` |
| `scripts/clean_install_selftest.py` | 3,873 | `1f3d0ea6b397fdab` |
| `tests/test_v05_protocol.py` | 9,515 | `9ad2e0590d9eb022` |
| `tests/test_v05_object_header.py` | 6,170 | `bcd56f1accafbdf0` |
| `tests/test_v05_handbook.py` | 3,138 | `df42a21e62973232` |
| `tests/test_v05_selfcheck.py` | 4,177 | `d2b2613d5612dcfa` |
| `docs/CONTRACT-ADDENDUM-v0.5.md` | 7,093 | `e076b9fe72f73bfa` |
| `docs/OBJECT-HEADER-v0.1.md` | 4,345 | `4d136775ea30b6a8` |
| `docs/HANDBOOK.md` | 12,820 | `ece9aac3ed1bfadd` |
| `docs/CLEAN-INSTALL.md` | 5,406 | `dddfdc0e435bc146` |
| `docs/PERF-v05.md` | 3,249 | `fc551a149a237199` |
| `deploy/backends/README.md` | 2,415 | `80e770eb5d3db2e7` |
| `deploy/backends/backends.example.json` | 595 | `d5ecd86c377d62e6` |
| `deploy/backends/docker-compose.template.yml` | 1,103 | `90ed4ffe78226302` |

证据：`evidence/baseline-v05-full-suite.xml`（357/347/10）、`evidence/regression-full-suite.xml`（385/374/11）、`evidence/golden-chain-report.json`、`evidence/consistency-selfcheck.json`、`evidence/clean-install-selftest.json`、`evidence/perf-baseline-v05.json`、`evidence/handbook.json`、`evidence/clean-install-pip.log`、`evidence-clean/`。

## 11. 红线自查

| # | 红线 | 结论 |
|---|---|---|
| 1 | 零模型/外部 API/付费；不推送、不发布 | 遵守。全流程本地；`published=false`；无 push |
| 2 | 不改命名表面与发现层呈现 | 遵守。60 工具 / 21 compact / 40 动词 / 8 规则 / 4 prompt 均未变；仅 `RECOVERY` 增 2 个**新**码，只被新协议面使用，既有动词不抛 |
| 3 | 认证保持 NOT_CERTIFIED | 遵守。代码、手册、暂存 MANIFEST、自检报告均写死 NOT_CERTIFIED |
| 4 | 每改动配测试；既有 357 项零变化；G1 59/59 | **部分达成**：新增 28 项测试全绿、G1 59/59、356/357 不变；1 项因 `schtasks.exe` 被策略拦截而 FAIL，已举证为外部原因（§5），**请裁定** |
| 5 | 阻塞留痕、不假报 | 遵守。T4 明写未交付优化；T7 明写阻塞；干净环境首次 328 ERROR 的作废原因已记录 |
| 6 | 与 G1 验收意见冲突则暂停待裁决 | 未收到 Muse 的 G1 验收意见文本，本轮无据以判定的冲突项；若验收意见涉及 disposition 语义或句柄编码，需复核 `docs/CONTRACT-ADDENDUM-v0.5.md` §C 的「verified 不等于独立 oracle」口径 |

## 12. 复核方式

```bash
cd 08-runtime-v05-native-push
python -m pytest tests/ -q                      # 385 项
python scripts/golden_chain_report.py --root . --workbench .. \
  --baseline-xml evidence/baseline-v05-full-suite.xml --baseline-commit 4040567
python scripts/consistency_selfcheck.py         # 21 案
python scripts/generate_handbook.py --check     # 手册漂移
python scripts/build_release_staging.py --out release-staging-v05
python scripts/perf_baseline.py                 # 四组基线
```
