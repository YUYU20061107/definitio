# 契约正文增补 v0.5 · 协议加固（08 树活文档）

日期：2026-11-14。适用范围：`08-runtime-v05-native-push`（第三轮长线工程）。
状态：**待 Muse 异源验收**；认证维持 **NOT_CERTIFIED**。本文不预判验收结论。

## 0. 与 G1 冻结正文的关系（重要）

本增补是 08 树内的**活契约增补**，不修改 G1 冻结区的任何文件：

- 冻结正文：`02-g1-gate/handoff-20261111/gis-agent-semantic-contract-v0.1-full.md`
  与 `02-g1-gate/incoming/gis-agent-semantic-contract-v0.1-full.md`（G1 基线 59 文件之内）。
- **59/59 文件 SHA256 一字未改**；`scripts/golden_chain_report.py::g1_guard` 只读复核。
- 冻结正文第 6 章「证据字段规范」、1.2「对象与逻辑引用、句柄生命周期」、第 5 章「错误分类学」
  的既有条款语义不变；本增补只在其上作 **additive 加字段／加错误码／加读取语义**。
- 若 Muse 的 G1 验收意见（2026-11-14 白天出）与本增补任一条冲突，该项**暂停并在交付报告中标出，等裁决**，不自行解释过关（工作单红线 6）。

实现位置：`definitio/protocol.py`（新文件）、`definitio/runtime.py`（写路径一处 additive 字段）、
`definitio/agent.py`（RECOVERY 登记两码；evidence_get 读路径 additive 三态标注）。

---

## A. 证据信封 disposition 三值（additive 字段）

### A.1 定义

证据信封（`runtime._execute_step` 构造、`store.save_evidence` 落库的 evidence 文档）
新增字段 `disposition`，取值闭集三值：

| 值 | 含义 | 判定依据 |
|---|---|---|
| `delivered_verified` | 成果已落库且读回核验真跑并全通过 | 有落库输出；`verification.ok=true`；`postcondition_checks` 非空且全为真 |
| `delivered_unverified` | 成果已交付但无落库输出或核验不完整 | 只读结果（`output_ref=null`）、读回未通过／未执行、服务类合成回执 |
| `not_delivered` | 声明的成果未交付 | 写入前失败、预演不落库、交付中断；由 `protocol.non_delivery_envelope()` 显式记录，不得冒充已交付 |

判定事实另以 additive 字段 `disposition_basis` 落盘（`delivered` / `has_persisted_output` /
`verification_ok` / `checks_total` / `checks_passed` / `unconfirmed_segments` / `oracle_note`），
供验收方按同一口径重算，不依赖执行方自述。

### A.2 纪律

1. **写路径标，读路径不标**：`disposition` 只在 `_execute_step` 写路径按当时的实际交付与核验事实计算一次；
   `evidence_get` / `artifact` / `evidence_bundle` 等读路径只读出该字段，**不重新计算、不推断、不改写、不自升**。
2. **additive only**：不新增必需字段、不修改任何既有字段语义、不删字段。`evidence_digest` 按包含新字段后的完整信封重算，
   既有「摘要与内容一致」的验证逻辑不变。
3. `verification.unconfirmed` 段（同核自洽、非独立 oracle 等限制）**如实全数保留**在 `verification` 与
   `disposition_basis.unconfirmed_segments`，不因判为 `delivered_verified` 而抹掉。
   `delivered_verified` 只表示「读回核验真跑且全通过」，**不表示独立几何 oracle、不表示跨后端等价、
   不表示认证升级**；`certification` 恒为 `NOT_CERTIFIED`。认证等级与交付等级是两个正交轴，不得混用。
4. `not_delivered` 信封若同时带 `output_ref`，一致性检查器判为违规（见 T6 自检）。

### A.3 与冻结正文第 6 章的衔接

6.7「证据缺失、不可取得、未验证与脱敏规则」要求区分「未验证」与「确无」；本增补把该区分下沉到信封级：
`delivered_unverified` 对应「已有但未验证」，`not_delivered` 对应「没有交付」。冻结正文条款本身不改。

---

## B. 句柄解析失败分码与捕获证明计数规则

### B.1 协议级错误码

| 码 | 触发 | recovery 动作 |
|---|---|---|
| `STALE` | 句柄解析 **0 命中**（声明的对象已失效或从未被捕获） | `discover_inputs`，可重试 |
| `AMBIGUOUS` | 句柄解析 **>=2 个不同身份** | `disambiguate_handle`，不重试、不静默挑选 |

两码已登记进 `agent.RECOVERY`，并通过 `discovery.audit()` 的 `recovery_codes.missing == []` 检查。

### B.2 捕获证明计数规则（成文）

> 捕获证明按「**角色 + 稳定字段**」去重计数；值（`value`/`values`）、范围（`range`/`ranges`/`bounds`/`extent`）、
> 计数（`count`/`feature_count`）等易变量**不作身份**。同一身份的多次观测计 1；
> 0 个身份判 `STALE`，>=2 个身份判 `AMBIGUOUS`，恰好 1 个身份才解析为句柄。
> 解析结果必须可由调用方复核：`role`、稳定字段名、候选数、身份数、计数规则随错误一并返回。

理由：值与范围会随读回时点、量测口径、切片范围变化，若当作身份，同一对象会被误判为多个身份（`AMBIGUOUS` 假阳性），
或在不同时点被误判为对象消失（`STALE` 假阳性）。角色与稳定字段（如 object_id / version_id / schema_hash）才是身份。

### B.3 与控制面既有错误码的边界（守既有判定零变化）

`protocol.resolve_handle()` 是**新增协议面**。`store.Store.dataset()` 在 0 命中时仍返回既有 `NOT_FOUND`，
控制面既有 357 项测试的判定一律不变。本协议面**不替换、不重写、不改判**任何既有错误码；
两者在文档中并列，由调用方按所用表面选择。若 G1 裁决要求统一分码，须另行裁决后再改，不在本轮擅自合并。

---

## C. 单次读取证据三态

`protocol.read_state(loader, evidence_id, field)` 对「一次读取」的读回字段标注三态：

| 态 | 条件 | 禁止 |
|---|---|---|
| `Known` | 读成功且字段存在，返回 `value` | — |
| `Absent` | 读成功但字段不存在 | 不许据此推断「对象确无」 |
| `Unknown` | 读失败（含注入读失败）或证据结构不可解析 | **不许塌成 `Absent`**，不许当作「确无」 |

`Unknown` 携带 `reason=read_failed` 与 `error_type`，不吞异常、不伪造值。
`evidence_get` 的 summary 分支持有 `read_states`，只对 `output_ref` / `postcondition_checks` / `semantic`
三个读回字段作三态标注；**不对 disposition 作任何标注**。
控制面既有「证据不存在 → NOT_FOUND」的行为保持不变，以守既有判定零变化；协议层的 `Unknown` 在
`read_state` 中独立成立并有注入读失败测试覆盖。

---

## D. 验收入口

- 机器判定：`tests/test_v05_protocol.py`（三值各至少一例、0/>=2 命中分码、注入读失败 Unknown、发现层审计零缺失）。
- 复跑：`python scripts/golden_chain_report.py --root . --workbench .. --out evidence --baseline-xml evidence/baseline-v05-full-suite.xml --baseline-commit <08 起点提交>`。
- 冻结对照：`evidence/baseline-v05-full-suite.xml`（开工前 357 项）↔ 终局 `regression-full-suite.xml`，逐 ID 判定零变化。
