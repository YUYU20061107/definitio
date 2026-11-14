# 对象头规范 v0.1（Object Header v0.1）

日期：2026-11-14。适用范围：`08-runtime-v05-native-push`。
实现：`definitio/semantics.py::validate_object_header`（一致性断言，**非运行时新门**）。
状态：待 Muse 异源验收；认证维持 **NOT_CERTIFIED**。

## 1. 定位

引擎所有数据成果（数据集、栅格、点云、托管资源、只读分析结果）都带一个 `semantic` 对象头。
本规范把既有实现里的事实约定收成成文的**四个逻辑槽位**，并给出机器可判的缺字段检查。
它是 additive 的：不新增运行时必需字段、不改 `object_header()` / `result_header()` 的既有字段语义、
不改任何既有写路径，因此既有 357 项判定不受影响。

- 对象头格式常量：`HEADER_FORMAT = "definitio-semantic-object-v1"`（既有，未改）。
- 规范版本：`OBJECT_HEADER_SPEC = "object-header-v0.1"`（本轮新增）。
- 唯一构造器：`semantics.object_header()`（有落库对象）与 `semantics.result_header()`（只读分析结果），
  经 `store.Store._attach_header()` 与 `runtime._execute_step` 接入；**没有第二份手写头**。

## 2. 四个逻辑槽位

| 槽位 | 语义 | 标准承载键 | 判定 |
|---|---|---|---|
| `crs` | 规范 CRS 身份 | `crs`（含 `code`、WKT2 摘要、轴序、单位） | `crs` 为对象且 `code` 非空 |
| `extent` | 空间范围／规模 | `structure.bounds`（矢量）、`structure.grid`（栅格）、`structure.point_count`（点云）、`structure.resource_kind`（托管资源） | 至少一个承载键存在且非空 |
| `field_summary` | 字段／维度摘要 | `structure.fields`（矢量）、`structure.grid`（栅格）、`structure.point_dimensions`（点云）、`structure.resource_kind`（资源） | 至少一个承载键存在且非空 |
| `version` | 不可变版本身份 | `identity.version`（`version_id` + `fingerprint` + `created_at`） | `version_id` 与 `fingerprint` 均非空 |

槽位名是**逻辑**的，不是新的物理字段：规范不要求引擎改名或搬迁键，只要求每个对象头都能被
`validate_object_header()` 判定为四槽位齐备。

### 2.1 只读分析结果的降级承载（如实声明）

`analysis_result` 不是空间数据集，**没有空间范围**。其 `structure.result_keys` 同时承载
「结果规模」与「字段摘要」两个槽位。这是显式的降级承载，**不冒充空间范围**，
验收时不得据此声称该结果具备 CRS 范围语义。除此之外 CRS 与 version 两槽位照常成立
（CRS 来自输入数据集或参数声明）。

## 3. 一致性断言（缺字段即测试失败）

`validate_object_header(header)` 返回
`{"ok", "spec", "object_type", "missing", "present"}`；`missing` 点名缺失槽位。
一致性测试 `tests/test_v05_object_header.py` 断言：

1. **40 动词无空白**：`operations.SPECS` 的 40 个动词按登记的数据域映射到唯一构造器，
   每个动词产出的对象头都 `ok=true`，且 40 条记录逐动词留痕（不靠循环里跳过）。
2. **逐槽位反例**：分别抽掉 `crs` / `extent` / `field_summary` / `version` 的承载键，
   断言 `missing` 精确点名该槽位。
3. **真实运行时抽样**：真实执行导入、复制、融合、量测，取落库数据集的 `semantic`
   与证据信封里的 `semantic`，断言同样四槽位齐备。

### 覆盖口径（不夸大）

第 1 项是「**40 动词 × 引擎唯一对象头构造器**」的机器覆盖，不是 40 次真实后端执行；
服务类动词在本机属既有环境阻塞（10 项红灯），不得拿合成回执冒充后端实测。
第 3 项才是真实执行证据。两者在交付报告中分开列示。

## 4. 不改的东西

- G1 冻结契约正文、59 文件哈希：一字不动。
- 命名表面（surface）：本轮绕行，工具计数 60/21/40/4 不变，`surface-v1.json` 未改。
- 既有错误码、既有证据字段语义、既有测试判定：不变。
- 运行时**不强制**调用校验器；它只在测试与 T6 一致性套件中作为断言使用。

## 5. 验收入口

- `python -m pytest tests/test_v05_object_header.py -q`
- 全量：`python scripts/golden_chain_report.py --root . --workbench .. --out evidence --baseline-xml evidence/baseline-v05-full-suite.xml`
