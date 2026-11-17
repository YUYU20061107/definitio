# REPAIR-AUDIT — Definitio 红队闭环审计（08 发布树）

- 日期：2026-11-17
- 执行依据：`ORDER.md`（本目录存档的行动令原文）
- 审计对象：08 发布树 `08-runtime-v05-native-push`，分支 `repair/redteam-closure-20261117`，基线 commit `78f0452e`（引擎源码摘要 `10492781ee3c51d523ff9d292b043933fbb6094f7314ba3f240429283522b288`，本轮独立复算一致，34 个文件）
- 本轮性质：**独立复验 + 双树对账**。按上级指令，本轮未改动 08 树任何生产代码；新发现只登记、附最小复现、不擅自修复。

---

## 1. 树身份对照（Phase 0 先决事实）

| 树 | 位置 | 状态 | 源码摘要 | 说明 |
|---|---|---|---|---|
| D 盘审查副本 | `D:\agent-native-gis` | 红队审查的冻结副本；设计上从未接收 RT-FIX 修复 | `87d73c5b…`（红队报告与其实测一致） | 2026-11-17 小企鹅侧又在此树上做了一轮**独立的平行修复**（见 §5），修复后摘要 `22ed0b86…`；该平行修复不改变它「非正本」的身份 |
| 08 发布树（正本） | 笔记本 `…\agent-native-gis\08-runtime-v05-native-push` | 唯一正本；RT-FIX-20261116 落点；已发布基线 `a0f52107` + 门面修复 `78f0452e` | 修复前 `05aa24b4…` → 修复后 `10492781…` | 本轮全部硬验证在此树上进行 |
| 8876 运行实例 | 笔记本端口 8876，PID 58636（父进程 55484） | **只读观察，未触碰** | 不适用（进程内存未查） | 2026-11-14 01:22:45 启动，早于红队（11-16）与修复（11-17）；启动解释器为 `D:\agent-native-gis\.venv` 的 python，命令 `python -m definitio.server --port 8876 --headless`——全部旁证指向它跑的是 D 盘环境下的修复前代码；切版与树归属确认仍是未决项（公开 issue 素材第 5 条） |

**关于「外部复查认为没修好」的定性**：鱼鱼已确认外部复查对象是 D 盘。D 盘按设计未接收修复，其失败结果不构成对 08 树修复的否定。本轮用 D 盘原版探针在 08 树上取得 0/13（§2），使该结论有可复核证据，而非仅靠口径。

## 2. Phase 0 缺陷追踪矩阵（13 反例 / 11 组）

复验方法：D 盘原版探针 `D:\agent-native-gis\evidence\redteam-20261116\probes.py`（SHA-256 `56ffb951b27f46f302d5245d5e66e05ed2da0a2ccad510d3b8d4a9377592ceab`，与修复方证据包副本逐字节一致）复制到隔离目录，由本轮执行者亲自对当前 08 树（78f0452e）运行，13 条原缺陷断言容错重算。结果：**DEFECTS_STILL_REPRODUCING 0 / 13，case 异常 0**（`logs/phase0-dprobes-result.json`）。这与 2026-11-17 验收的 0/13 一致，但为本轮独立复跑，不引用旧结论充数。

| 探针 | 组 | 级别 | 原始证据（红队报告） | 本轮状态 | 回归入口 |
|---|---|---|---|---|---|
| buffer_wrong_geometry | RT-01 | P1 | 偏移约 1400 m 的缓冲被 COMMITTED 且 verify=true | 已修复且本轮复验通过 | regression test_01 |
| clip_wrong_geometry | RT-02 | P1 | 与 mask 不相交的裁剪结果被提交 | 已修复且本轮复验通过 | test_02 |
| join_wrong_attributes | RT-03 | P1 | zone 改为 WRONG_ZONE 仍提交并复验通过 | 已修复且本轮复验通过 | test_11 |
| grant_export | RT-04 | P1 | 仅数据集授权仍导出 outside_scope.geojson | 已修复且本轮复验通过 | test_03 |
| replay_missing_file | RT-05 | P1 | 删文件后重放/复验仍报 confirmed | 已修复且本轮复验通过 | test_04 |
| artifact_readback_failure | RT-05/RT-07 | P1/P2 | 读回失败留毒登记、resume 失败 | 已修复且本轮复验通过 | test_05 |
| external_publish_invalid_json | RT-06 | P1 | 副作用后坏 JSON 被报「已回滚」、execution_started=false | 已修复且本轮复验通过 | test_13 |
| failed_task_verify | RT-08 | P1 | 全失败任务 verify 仍 ok=true、steps=[] | 已修复且本轮复验通过 | test_06 |
| readonly_verify | RT-08 | P1 | 只读结果 sum 被篡改仍 ok=true | 已修复且本轮复验通过 | test_07 |
| multipoint_buffer | RT-09 | P2 | 合法 MultiPoint 缓冲被面积上界误拒 | 已修复且本轮复验通过 | test_08 |
| clip_duplicate_properties | RT-02（误拒侧） | P1 | 重复属性被错配到另一源面而拒绝 | 已修复且本轮复验通过 | test_09 |
| hillshade_dark_slope | RT-10 | P2 | 背光坡被 raster_pixel_spot 误拒 | 已修复且本轮复验通过 | test_12 |
| empty_reproject | RT-11 | P2 | 空 Polygon 重投影验证器抛 ValueError | 已修复且本轮复验通过 | test_10 |

### 2.1 行动令点名数字的出处核实

- **104/106 针对性回归、2 项 G1 守卫失败**：出自红队原报告（2026-11-16）§3 第 134–136 行——那是**修复前**在 D 盘副本上的针对性回归：10 个测试文件 106 项，首轮 101 PASS / 5 FAIL；其中 3 项原生后端失败系临时目录 PermissionError，改 TMP/TEMP 后通过；按测试 ID 合并为 104/106，剩余 2 项即 G1 守卫（扁平副本路径解析到 `D:/`）。它不是修复后的复查数字。在 08 树上，G1 守卫已由 RT-FIX 改为随包基线 + 显式根参数，本轮实测 6/6 通过（`logs/focused-guard-regression-20261117.log`）。
- 历史参考数（40/40、G1 59/59、347/357）：按行动令口径仅作历史参考，本轮未引用为结论。

## 3. 本轮新发现（08 树，含最小复现）

### X-01（P1，新发现）task_get 在已提交输出数据集被删时只剩裸 NOT_FOUND

- 复现（`logs/n_checks.py` 用例 N-01）：在隔离工作区提交一个 buffer 任务（COMMITTED），直接删除其输出数据集行后，`task_get`（普通与 verify=true 均是）返回 `ok=false, error=NOT_FOUND`——调用方无法区分「任务不存在」与「输出被删」，拿不到任务状态、更没有 TARGET_STATE_UNKNOWN 或缺失清单。verify 路径同样死在此处：`_task_reply` 在 `verify_task` 之前先逐个 `store.dataset(ref)` 构建 outputs，缺失即整体失败，RT-FIX 已修好的 live_status/corrupted 语义根本到不了。
- 根因位置：`definitio/agent.py` `_task_reply`（约 319 行）无条件解引用全部 output_refs，无缺失输出处理分支。
- 与 D 侧对照：即对方 N-01 在 08 树的同源存在；对方已在其树上修复（返回 TARGET_STATE_UNKNOWN + unavailable_outputs）。
- 本轮处置：**只登记不修**（上级指令本轮不动 08 代码）。修复建议见总报告移植清单第 1 项。

### X-02（P2，新发现）file_export 能力无法经已发布工具面授予

- 事实：`definitio/authorization.py` 的授权层完整支持 `capabilities`（含 `file_export`）与 `max_file_exports`（EFFECT_KINDS 四类、check_effects 写前核对）；但 `definitio/agent.py` 第 109–115 行已发布的 `authorization_grant` 工具 schema 只声明 target_prefix/verbs/max_writes/ttl_seconds/confirm/request_id，**没有 capabilities 与 max_file_exports 字段**，传入即被参数校验以 INVALID_ARGUMENT 拒绝（本轮实测，`logs/n-checks-result.json` 用例 N-03）。
- 影响方向：fail-closed（更严，不构成越权）——但经会话授权的文件导出永远不可达，与授权层能力不一致，属发现层↔执行层不一致（对方 N-03 的同类问题，形态不同：对方是 verbs 枚举不收令牌，08 是 schema 缺字段）。
- 本轮处置：只登记不修。

### 已核查不存在的两项（对方 N-02 / N-04 的 08 对应面）

- **预留/计数漏算 exports（对方 N-02）：不存在。** 实测（`logs/n02b.py`）：经授权层直建 grant（capabilities=[dataset, file_export]、max_file_exports=1），计划在同一步挂 2 个导出，执行在任何写入前以 AUTHORIZATION_SCOPE 拒绝（files_created=[]、task=DECLARED、actual_writes=[]）。08 的模型是按副作用类别分设上限（文件导出默认 0），不存在「共用计数漏算」。
- **新错误规则未登记（对方 N-04）：不存在同类问题。** 08 的 `guidance.RULES` 是计划契约规则注册表（8 条，RT-FIX 未新增计划规则）；RT-FIX 引入的状态语义（TARGET_STATE_UNKNOWN）在 `agent.py` 的错误指引表中有已发布条目（「STOP automatic retries. Inspect task_get and evidence…」）。机制不同，未发现执行层发出而发现层未登记的规则。

## 4. 未复验 / 证据不足项（如实留账）

| 项 | 状态 | 说明 |
|---|---|---|
| 变异集（六类 18 变异 + 12 正例） | 未独立复跑 | 修复方记录 FA 0/18、FR 0/12 在案（`evidence/redteam-fix-20261116/mutation-report.json`）；本轮未在隔离目录重跑该套件，不冒充本轮结果 |
| RT-07 发布崩溃窗口故障注入 | 未独立复跑 | 修复方 `rt07_phase_injection` 记录 19/19；本轮未重演「os.replace 后崩溃」窗口 |
| 授权撤销竞态（对方 R2 的 08 对应面） | 未复验 | 本轮未构造「执行中撤销」用例；08 的撤销语义以授权层快照 + 提交时 check_effects 为界，未做竞态实测 |
| 同核 Oracle 局限（对方 R3） | 两树共同存在 | 08 的空间验证含同核重算成分且已标注非独立 oracle；独立几何 oracle（GDAL/PostGIS 交叉）未引入，属研究设计变更，未在本轮范围 |

## 5. D 盘平行修复对账摘要（外部证据：`D:\agent-native-gis\evidence\repair-20261117\` 五份交付物已通读）

- 对方在 D 盘冻结副本上独立完成了同一行动令的一轮修复：基线 13/13 复现（digest 87d73c5b 与红队报告一致）→ 其修复后 13/13 失效；新增回归 47 项（修复前基线 26 失败、具区分力）；全量 404 项 391 通过 / 13 失败，13 失败（G1 守卫×2、schtasks 被宿主策略阻断×1、离线缺 OGC/STA 端点×10）在其修复前基线上同样失败，定性环境阻塞；裁决 CONDITIONAL GO（限定本地单工作区、离线/回环；明确不放行 8876 合入、不宣称认证）。
- 过程事故（对方已披露）：复现时直跑归档 `probes.py` 覆写了归档 `probes.json` 并新建 cases-* 目录；已从原 zip 恢复并按 MANIFEST 逐字节复核 23/23 一致。本轮核查：D 盘 `probes.py` 当前 SHA-256 仍为原值 `56ffb951…`，与该披露相符。
- 与 08 的关系：同一缺陷清单、两套独立实现（对方 verifier 重写路线 vs 08 的 RT-FIX 路线）。逐条处置差异与移植建议见总报告；本审计只确认一点：**对方的任何数字（包括 391/404 与 CONDITIONAL GO）都只对其 D 盘工作树成立，不迁移到 08 树**。08 树的本轮数字见 REDTEAM-REGRESSION.md（403/403）。
- 对方 CONDITIONAL GO 的条件 C1–C5 与 08 现状对照：C1 环境阻塞重跑——08 本轮全量 403/403 已包含同类用例且全过（本机环境下不构成阻塞）；C2 发布两阶段原子性——08 已有 staging→published→verified 状态机（RT-07 修复），余崩溃窗口未独立注入（见 §4）；C3 撤销语义文档化——08 同样未做，待决；C4 版本绑定——08 已在 Git 下（本条件在 08 天然满足）；C5 G1 基线可复现——08 已满足（随包基线 + 守卫 6/6）。

## 6. 残余风险

1. X-01 未修：输出数据集被外部删除后，任务状态不可见（fail-closed，不产生假成功，但诊断通道断裂）。
2. X-02 未修：会话授权路径下文件导出不可达（fail-closed，功能缺口）。
3. 8876 仍在运行修复前代码（D 盘环境启动）；其对外行为不代表 08 发布树，切版需单独批准。
4. D 盘现有第二套分叉实现（摘要 22ed0b86）；若未来误把 D 盘当正本，将与 08 的 RT-FIX 冲突。正本唯一性须维持：08 树。
