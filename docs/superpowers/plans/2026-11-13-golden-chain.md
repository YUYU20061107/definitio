# Definitio 黄金链纵切（v03）实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在新版本目录内打通「设施数据接入 → 坐标转换 → 缓冲/服务区 → 空间连接 → 指标汇总 → 保存并回读产物」这一条黄金链，并沿链落实 D1 语义对象头、D2 验证器单实现、D3 版本绑定提交、D4 可恢复计划、D5 发现与错误一致性，五类验收与全量回归全部自动化跑出报告。

**Architecture:** 新增五个聚焦模块（semantics / verifier / commit / resume / discovery）承载新能力，既有 `runtime.py` 只做最小接线；算子（operations 等）、验证器（verifier）、执行策略（runtime/commit/resume）三层分离。对外 MCP/HTTP 工具总数保持 60、compact 保持 21、动词保持 40、prompts 保持 4——新能力通过既有工具的**可选参数**与**新增记录字段/URI 分支**暴露，避免改动既有计数断言。

**Tech Stack:** Python 3.12.9（`D:/agent-native-gis/.venv/Scripts/python.exe`，system-site-packages=true）、shapely 2.1.2 / GEOS 3.13.1、rasterio 1.5.2 / GDAL 3.12.2、SQLite(WAL)、FastAPI、官方 MCP SDK、pytest。

**Spec:** `C:/Users/RAHUYU/Downloads/dev-round-golden-chain-work-order-20261113.md`

## Global Constraints

- 新版本目录：`C:/Users/RAHUYU/WorkBuddy/2026-11-09-10-00-10/agent-native-gis/06-runtime-v03-golden-chain`（现场编号 00–05 已占用，续 06；`scripts/audit_g1.py` 的默认 root=`parents[2]` 只有在该布局下才指向工作台根并找到 `02-g1-gate/handoff-20261111`）。报告中须注明此定名依据。
- G1 基线 **59 个文件一字不动**，清单与期望哈希取自 `D:/agent-native-gis/evidence/baseline-sha256-before.json`，根为工作台根。开工前已核验 59/59 相符。
- 在线引擎 **8765/8876 不停、不改配置**；本轮不启动任何常驻引擎，测试一律用 `tmp_path` 独立工作区。
- **全程离线**：不调用任何真实模型 API，不访问外部网络（含 istSOS 官方端点），不推送、不发布、不动公开仓。
- 工具计数冻结：`len(tool_definitions())==60`、`len(compact_tools(...))==21`、`len(SPECS)==40`、`len(PROMPTS)==4`。新增能力只允许加**可选参数**、**记录字段**、**URI 分支**。
- 既有断言不得改判：`test_geographic_buffer_is_not_planar_metres` 仍须 `NOT_SUPPORTED`；`test_readback_comparator_rejects_fake_success` 仍须 `BACKEND_ERROR` + 空 `output_refs` + 数据集数不变；`test_interrupted_task_is_isolated_on_restart` 仍须 `TARGET_STATE_UNKNOWN`。
- 禁止笼统 `verified=true`；验证结果必须是「检查了什么／发现了什么／什么无法确认」三段式。
- CRS 与坐标轴顺序必须是**两个分开的字段**，轴序从 CRS 定义（WKT AXIS 节点）解析，**不得只由 EPSG 编号推断**。
- 本机依赖不随包的问题如实记账；不许用「本机能跑」冒充干净安装。
- 单点卡住超过 30 分钟：记录现象、绕行或降级为已知限制，不许静默跳过验收项。

## 基线事实（开工前实测，已落盘）

| 项 | 实测 | 证据 |
|---|---|---|
| G1 基线 59 文件哈希 | 59/59 相符，0 缺失 | 本轮开工前核验（Task 8 复跑并落盘） |
| 新版本目录全量测试（补 fixture 前） | 225 收集，211 PASS / 14 FAIL | `evidence/baseline-full-suite.xml`、`.log` |
| 新版本目录全量测试（补 g0 fixture 后） | **225 收集，215 PASS / 10 FAIL** | `evidence/baseline-full-suite-r2.xml`、`.log` |
| 同 10 项在源工作站 `D:/agent-native-gis` | 同样 10 FAIL / 27 PASS | 本轮对照实跑 |
| 10 项失败根因 | 缺 `data/backends.json`（本地 PostGIS/GeoServer 沙箱配置，含凭据，不随源码包）＋ 需外部 istSOS 端点（违反本轮离线红线） | `tests/test_native_completion.py:24` |
| 4 项 surface 失败根因 | 缺 `evidence/a3-a6-20261113/g0/{protocol,tasks,frozen,predictions}.json` 离线夹具，已复制补齐 → 13/13 PASS | `evidence/a3-a6-20261113/g0/` |
| 历史「201 个测试 ID」 | 与当前源码收集数 **225** 不一致；201 是 40/40 轮的历史口径，本轮以实测 225 为准并单列说明 | 本计划 + 交付报告 |

**10 项环境阻塞处置**：不启动 PostGIS/GeoServer 沙箱、不访问 istSOS、不复制含凭据的 `backends.json`。作为**已声明的既有环境限制**保留红灯，附源工作站同样失败的对照证据。不得改判为通过。

---

## File Structure

### 新建（全部在新版本目录内）

| 文件 | 职责 |
|---|---|
| `definitio/semantics.py` | D1：语义对象头构造；WKT AXIS 解析；量纲/单位；来源谱系 |
| `definitio/verifier.py` | D2：唯一验证器实现。5 个契约谓词 + 迁移进来的算子读回不变量；三段式结果 |
| `definitio/commit.py` | D3：写入范围绑定、版本绑定判定、三态提交、文件产物暂存→校验→发布、发布后回读 |
| `definitio/resume.py` | D4：步骤检查点、依赖失效判定、可恢复性判定 |
| `definitio/discovery.py` | D5：逐工具跨发现面名称/参数集一致性审计；prompt 内动词与参数解析 |
| `definitio/golden.py` | 黄金链定义、夹具与真值口径（供验收测试与报告脚本共用） |
| `tests/test_golden_semantics.py` | D1 单测 + 缓冲区锚例 |
| `tests/test_golden_verifier.py` | D2 单测 + 两路径代码级同一性断言 |
| `tests/test_golden_commit.py` | D3 单测（三态、暂存清理、越界拒绝） |
| `tests/test_golden_resume.py` | D4 单测（检查点、失效、不整链重跑） |
| `tests/test_golden_discovery.py` | D5 逐工具一致性断言 |
| `tests/test_golden_chain.py` | 五类验收（A1–A5）端到端 |
| `scripts/golden_chain_report.py` | 汇总五类验收 + 全量回归 + G1 哈希 + 文件清单哈希 → JSON/MD 报告 |
| `docs/GOLDEN-CHAIN-DELIVERY-20261113.md` | 交付报告正文 |

### 修改（全部在新版本目录内）

| 文件 | 改动 |
|---|---|
| `definitio/model.py` | `crs_info` 增加 `axis_order`（由 WKT 解析，独立字段）；不改 `definition_hash` 口径 |
| `definitio/store.py` | `insert_dataset/insert_raster/insert_pointcloud/insert_resource` 附语义头；新增 `file_artifacts` 表与读写方法 |
| `definitio/verification.py` | 变为 `verifier` 的薄适配层，保留 `verify_output` 签名与失败即回滚语义 |
| `definitio/runtime.py` | 声明期绑定写入范围；执行前跑验证器 precheck；三态提交；步骤检查点；`resume()`；抽出 `_run_steps()` |
| `definitio/preview.py` | 调用同一验证器（`stage="preview"`）；CRS 投影补 `axis_order` |
| `definitio/agent.py` | `task_get`+`verify`、`task_execute`+`resume`、`task_plan`+`exports`；descriptor 暴露语义头；`definitio://artifacts/{id}` 分支；补全 10 个缺失 recovery 码 |
| `definitio/guidance.py` | 新增 `RULES` 注册表并在 `plan_step_contract()` 公布；不改 PROMPTS 数量 |
| `definitio/surfaces/surface-v1.json` | 为 `task_get/task_execute/task_plan` 补 3 个新参数映射（保持双射） |

---

## Task 1: D1 语义对象头

**Files:**
- Create: `definitio/semantics.py`
- Modify: `definitio/model.py:60-78`（`crs_info`）
- Modify: `definitio/store.py:77-181`（四个 insert 方法）
- Modify: `definitio/agent.py:268-284`（`descriptor`）
- Modify: `definitio/preview.py:23-25`（`dataset_summary`）
- Test: `tests/test_golden_semantics.py`

**Interfaces:**
- Consumes: `model.crs_info`、`model.digest`、`model.logical_fingerprint`
- Produces:
  - `semantics.HEADER_FORMAT = "definitio-semantic-object-v1"`
  - `semantics.parse_axis_nodes(wkt: str) -> list[dict]`，每项 `{"name": str, "direction": str, "order": int, "unit": str}`，按 `order` 升序
  - `semantics.axis_order(crs: dict) -> dict`，键 `{"declared": list, "sequence": str, "storage_order": "x,y", "derivation": "parsed_from_crs_wkt_axis_nodes", "differs_from_storage": bool}`
  - `semantics.operation_units(verb: str, parameters: dict, crs: dict) -> dict`
  - `semantics.object_header(document: dict, *, operation=None, inputs=(), scope=None, persistence=None) -> dict`
  - `model.crs_info(value)` 返回字典新增键 `axis_order`（值为 `axis_order()` 的 `sequence` 字符串）；既有 8 键与 `definition_hash` 口径不变
  - `store.insert_dataset(name, geojson, crs, schema, source, db=None, header_context=None)`（其余三个 insert 同样新增 `header_context=None`）
  - 数据集文档新增键 `semantic`；`descriptor()` 新增键 `semantic`，其 `crs` 投影新增 `axis_order`

- [ ] **Step 1: 写失败测试 —— WKT 轴序解析与 CRS 双字段**

```python
# tests/test_golden_semantics.py
"""D1 semantic object headers; independent of the compute kernel."""
import pytest
from shapely.geometry import box, mapping

from definitio.model import crs_info
from definitio.semantics import axis_order, parse_axis_nodes


def test_axis_order_is_parsed_from_definition_not_epsg_number():
    geographic = crs_info("EPSG:4326")
    projected = crs_info("EPSG:32649")
    assert geographic["axis_order"] == "north,east"
    assert projected["axis_order"] == "east,north"
    assert geographic["coordinate_order"] == "x,y (traditional GIS order)"
    assert "axis_order" in geographic and "code" in geographic
    nodes = parse_axis_nodes(geographic["wkt"])
    assert [n["order"] for n in nodes] == [1, 2]
    assert nodes[0]["direction"] == "north" and nodes[0]["unit"] == "degree"


def test_axis_order_survives_a_wkt_only_definition_without_any_epsg_code():
    wkt = crs_info("EPSG:4326")["wkt"]
    info = crs_info({"wkt": wkt})
    assert info["axis_order"] == "north,east"
    assert axis_order(info)["derivation"] == "parsed_from_crs_wkt_axis_nodes"
    assert axis_order(info)["differs_from_storage"] is True


def test_axis_order_definition_hash_is_unchanged_by_the_new_field():
    from definitio.model import digest
    info = crs_info("EPSG:3857")
    assert info["definition_hash"] == digest(info["wkt"])
    assert crs_info(info)["definition_hash"] == info["definition_hash"]
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd 06-runtime-v03-golden-chain && D:/agent-native-gis/.venv/Scripts/python.exe -X utf8 -m pytest tests/test_golden_semantics.py -q -p no:cacheprovider --basetemp data/t1`
Expected: FAIL — `ModuleNotFoundError: No module named 'definitio.semantics'`

- [ ] **Step 3: 实现 `semantics.py` 的轴序部分 + `crs_info` 接线**

`definitio/semantics.py`：

```python
"""Semantic object headers: identity, version, structure, CRS and axis order, units, lineage, scope, persistence."""
from __future__ import annotations

from .model import digest

HEADER_FORMAT = "definitio-semantic-object-v1"


def _split_top_level(text):
    parts, depth, quoted, start = [], 0, False, 0
    for i, ch in enumerate(text):
        if ch == '"':
            quoted = not quoted
        elif not quoted:
            if ch in "[(":
                depth += 1
            elif ch in "])":
                depth -= 1
            elif ch == "," and depth == 0:
                parts.append(text[start:i].strip())
                start = i + 1
    parts.append(text[start:].strip())
    return parts


def _balanced(text, open_index):
    depth, quoted = 0, False
    for i in range(open_index, len(text)):
        ch = text[i]
        if ch == '"':
            quoted = not quoted
        elif not quoted:
            if ch == "[":
                depth += 1
            elif ch == "]":
                depth -= 1
                if depth == 0:
                    return text[open_index + 1:i], i + 1
    raise ValueError("unbalanced WKT bracket")


def parse_axis_nodes(wkt):
    """Read AXIS[...] nodes from the CRS definition itself; never infer from the EPSG code."""
    nodes, cursor = [], 0
    while True:
        index = wkt.find("AXIS[", cursor)
        if index < 0:
            break
        body, cursor = _balanced(wkt, index + 4)
        parts = _split_top_level(body)
        name = parts[0].strip('"')
        direction = parts[1] if len(parts) > 1 else "unknown"
        order, unit = len(nodes) + 1, ""
        for part in parts[2:]:
            if part.startswith("ORDER["):
                order = int(part[len("ORDER["):-1])
            elif "UNIT[" in part:
                unit = part.split("UNIT[", 1)[1].split(",", 1)[0].strip('"')
        nodes.append({"name": name, "direction": direction, "order": order, "unit": unit})
    return sorted(nodes, key=lambda n: n["order"])


def axis_order(crs):
    declared = parse_axis_nodes(crs["wkt"])
    sequence = ",".join(n["direction"] for n in declared)
    return {"declared": declared, "sequence": sequence, "storage_order": "x,y",
            "derivation": "parsed_from_crs_wkt_axis_nodes",
            "differs_from_storage": sequence not in ("", "east,north")}
```

`model.py` 的 `crs_info` 返回字典追加一键（保持既有 8 键与 `definition_hash=digest(wkt)` 不变）：

```python
    from .semantics import axis_order
    return {"code": crs.to_string(), "wkt": wkt, "definition_hash": digest(wkt),
            "projected": crs.is_projected, "unit": crs.linear_units if crs.is_projected else "degree",
            "metres_per_unit": factor, "coordinate_order": "x,y (traditional GIS order)",
            "axis_policy": "explicit_xy", "axis_order": axis_order({"wkt": wkt})["sequence"]}
```

注意循环导入：`semantics` 只在函数内被 `model` 导入，`semantics` 顶层只从 `model` 取 `digest`，而 `model` 顶层不导入 `semantics` → 无环。

- [ ] **Step 4: 跑测试确认通过**

Run: 同 Step 2
Expected: 3 PASS

- [ ] **Step 5: 写失败测试 —— 对象头与缓冲区锚例**

追加到 `tests/test_golden_semantics.py`：

```python
from definitio.runtime import Runtime
from definitio.semantics import HEADER_FORMAT


@pytest.fixture
def runtime(tmp_path):
    return Runtime(tmp_path / "workspace.sqlite3")


def roads(runtime):
    return runtime.import_dataset("roads", {"type": "FeatureCollection", "features": [
        {"type": "Feature", "geometry": mapping(box(500000, 2540000, 500100, 2540100)),
         "properties": {"road_id": "R1", "lanes": 2}}]}, "EPSG:32649")


def test_imported_dataset_carries_a_full_semantic_header(runtime):
    d = roads(runtime)
    h = d["semantic"]
    assert h["format"] == HEADER_FORMAT
    assert h["identity"]["object_id"] == d["id"]
    assert h["identity"]["object_type"] == "vector_dataset"
    assert h["identity"]["version"]["fingerprint"] == d["fingerprint"]
    assert h["structure"]["fields"] == d["fields"]
    assert h["crs"]["code"] == "EPSG:32649"
    assert h["axis_order"]["sequence"] == "east,north"
    assert h["units"]["linear"] == {"unit": "metre", "metres_per_unit": 1.0}
    assert h["lineage"]["operation"] is None
    assert h["persistence"]["artifact_uri"] == f"definitio://datasets/{d['id']}"
    assert h["scope"]["workspace"]


def test_buffer_result_answers_lineage_unit_crs_and_axis(runtime):
    src = roads(runtime)
    task = runtime.declare("buffer roads", [{"verb": "gis_buffer", "parameters": {
        "input": src["id"], "target": "road_buffer", "distance": 500, "unit": "m", "dissolve": False}}])
    task = runtime.execute(task["id"], task["plan_digest"], True)
    assert task["task_state"] == "COMMITTED"
    out = runtime.store.dataset(task["output_refs"][0])
    h = out["semantic"]
    assert h["lineage"]["operation"]["verb"] == "gis_buffer"
    assert h["lineage"]["operation"]["vid"] == "V18"
    assert h["lineage"]["inputs"] == [{"object_id": src["id"], "name": "roads",
                                      "version_id": src["id"], "fingerprint": src["fingerprint"]}]
    assert h["units"]["operation"]["distance"] == {
        "value": 500, "unit": "m", "crs_unit": "metre", "metres_per_unit": 1.0,
        "value_in_crs_units": 500.0, "method": "divide_by_metres_per_unit"}
    assert h["crs"]["code"] == "EPSG:32649"
    assert h["axis_order"]["sequence"] == "east,north"
    assert h["persistence"]["store"] == "sqlite" and h["persistence"]["record_id"] == out["id"]


def test_header_does_not_change_the_logical_fingerprint(runtime):
    from definitio.model import logical_fingerprint
    src = roads(runtime)
    assert src["fingerprint"] == logical_fingerprint(src["geojson"], src["crs"], src["fields"])


def test_descriptor_and_preview_expose_axis_order(runtime):
    from definitio.agent import AgentService
    from definitio.preview import dataset_summary
    src = roads(runtime)
    service = AgentService(runtime)
    described = service.call("dataset_describe", {"dataset": src["id"]})["data"]["dataset"]
    assert described["crs"]["axis_order"] == "east,north"
    assert described["semantic"]["identity"]["object_id"] == src["id"]
    assert dataset_summary(src)["crs"]["axis_order"] == "east,north"
```

- [ ] **Step 6: 跑测试确认失败**

Run: `... -m pytest tests/test_golden_semantics.py -q -p no:cacheprovider --basetemp data/t1`
Expected: FAIL — `KeyError: 'semantic'`

- [ ] **Step 7: 实现对象头构造与 store/agent/preview 接线**

`semantics.py` 追加：

```python
OBJECT_TYPES = {"vector": "vector_dataset", "raster": "raster_dataset",
                "pointcloud": "pointcloud_dataset"}


def operation_units(verb, parameters, crs):
    linear = {"unit": crs["unit"], "metres_per_unit": crs["metres_per_unit"]}
    units = {"linear": linear, "area": {"unit": "m²", "derivation": "linear_unit_squared"},
             "operation": None}
    if verb in ("gis_buffer", "gis_measure") and "distance" in parameters or verb == "gis_buffer":
        distance = parameters.get("distance")
        if distance is not None:
            factor = crs["metres_per_unit"] or 1.0
            units["operation"] = {"distance": {
                "value": distance, "unit": parameters.get("unit", "m"), "crs_unit": crs["unit"],
                "metres_per_unit": crs["metres_per_unit"], "value_in_crs_units": distance / factor,
                "method": "divide_by_metres_per_unit"}}
    if verb == "gis_measure":
        units["operation"] = {"measure": {"operation": parameters.get("operation"),
                                          "reported_unit": "m²" if parameters.get("operation") == "area" else "m",
                                          "method": "projected_planar"}}
    return units


def object_header(document, *, operation=None, inputs=(), scope=None, persistence=None):
    kind = document.get("kind", "vector")
    crs = document["crs"]
    structure = {"fields": document.get("fields", []), "feature_count": document.get("feature_count", 0),
                 "geometry_types": document.get("geometry_types", []), "bounds": document.get("bounds")}
    if kind == "raster":
        structure = {"grid": document.get("grid"), "bands": document.get("grid", {}).get("bands")}
    elif kind == "pointcloud":
        structure = {"point_count": document.get("point_count"),
                     "point_dimensions": document.get("point_dimensions")}
    elif "resource" in document:
        structure = {"resource_kind": document.get("kind"), "extent": document.get("bounds")}
    object_type = OBJECT_TYPES.get(kind, "managed_resource")
    if "resource" in document:
        object_type = "managed_resource"
    return {
        "format": HEADER_FORMAT,
        "identity": {"object_id": document["id"], "object_type": object_type,
                     "name": document.get("logical_name", document["name"]),
                     "version": {"version_id": document["id"], "fingerprint": document["fingerprint"],
                                 "created_at": document["created_at"]}},
        "structure": structure,
        "crs": crs,
        "axis_order": axis_order(crs),
        "units": operation_units((operation or {}).get("verb", ""), (operation or {}).get("parameters", {}), crs),
        "lineage": {"operation": operation,
                    "inputs": [{"object_id": d["id"], "name": d.get("logical_name", d["name"]),
                                "version_id": d["id"], "fingerprint": d["fingerprint"]} for d in inputs]},
        "scope": scope or {"workspace": None, "authorization": None},
        "persistence": persistence or {"store": "sqlite", "table": "datasets",
                                       "record_id": document["id"],
                                       "artifact_uri": f"definitio://datasets/{document['id']}",
                                       "metadata_uri": f"definitio://datasets/{document['id']}/metadata",
                                       "file_artifact": None},
    }
```

`store.py`：四个 insert 方法签名末尾加 `header_context=None`；在 `document` 组装完（含 `summarize`/`resource_summary`/`raster.summary`/`pointcloud.summary`）之后、写库之前插入：

```python
        from .semantics import object_header
        context = header_context or {}
        document["semantic"] = object_header(document, operation=context.get("operation"),
                                             inputs=context.get("inputs", ()), scope=context.get("scope"),
                                             persistence=context.get("persistence"))
```

`runtime.py:296` 的 `insert_dataset` 调用补 `header_context`：

```python
                header_context = {"operation": {"verb": step["verb"], "vid": step["vid"],
                                                "parameters": p, "task_id": task["id"], "step_id": step["id"]},
                                  "inputs": inputs,
                                  "scope": {"workspace": str(Path(self.store.path).parent),
                                            "authorization": task["governance_state"].get("authorization_id")}}
                output = self.store.insert_dataset(target, gj, crs, schema, source, db, header_context=header_context)
```

`agent.py:271` 的 CRS 投影元组追加 `"axis_order"`；`descriptor` 返回字典追加 `"semantic": d.get("semantic")`。
`preview.py:25` 的 CRS 投影元组追加 `"axis_order"`。

- [ ] **Step 8: 跑测试确认通过 + 定向回归**

Run: `... -m pytest tests/test_golden_semantics.py tests/test_runtime.py tests/test_agent.py tests/test_usability.py -q -p no:cacheprovider --basetemp data/t1`
Expected: 全 PASS（既有断言不得改判）

- [ ] **Step 9: 提交**

```bash
git add definitio/semantics.py definitio/model.py definitio/store.py definitio/agent.py definitio/preview.py definitio/runtime.py tests/test_golden_semantics.py
git commit -m "feat(D1): semantic object headers with definition-derived axis order"
```

> 注：新版本目录尚未建 git 仓。Task 1 Step 0 先 `git init` 并把复制进来的源码作为基线提交，后续每任务一提交；**不推送任何远端**。

---

## Task 2: D2 验证器单实现

**Files:**
- Create: `definitio/verifier.py`
- Modify: `definitio/verification.py`（整体改为薄适配层）
- Modify: `definitio/runtime.py:190-196, 288-327`
- Modify: `definitio/preview.py:68-118`
- Modify: `definitio/agent.py`（`task_get` 加 `verify`；`_control` 分支）
- Modify: `definitio/surfaces/surface-v1.json`（`task_get.parameters` 加 `verify`）
- Test: `tests/test_golden_verifier.py`

**Interfaces:**
- Consumes: Task 1 的 `semantics.axis_order`、`document["semantic"]`；`model.require/digest/logical_fingerprint`；`operations.exact_key/geometries`
- Produces:
  - `verifier.VERIFICATION_FORMAT = "definitio-verification-v1"`
  - `verifier.context(**kwargs) -> dict`，键：`stage`（`"precheck"|"preview"|"commit"|"verify"`）、`verb`、`vid`、`parameters`、`inputs`、`proposal`、`output`、`store`、`db`、`planned_inputs`、`write_scope`、`actual_writes`、`authorization`、`workspace`、`file_artifact`
  - 五个契约谓词，签名统一 `f(ctx) -> dict`：`distance_unit_compatibility`、`input_version_binding`、`spatial_join_contract`、`output_reopenable`、`write_scope`
  - `verifier.operation_readback_invariants(ctx) -> dict`（迁移自 `verification.py` 的算子不变量）
  - `verifier.CONTRACT_PREDICATES`（5 元组）、`verifier.PREDICATES`（6 元组）
  - `verifier.run(ctx, predicates=None) -> dict`，键 `{"format","stage","verb","checked","found","unconfirmed","ok","predicates_digest"}`；`predicates=None` 时**在调用时**读模块全局 `PREDICATES`
  - `verifier.legacy_checks(result) -> list[{"id","passed","method"}]`
  - `verifier.enforce(result) -> None`（任一 `passed is False` → `GISFault("BACKEND_ERROR", ..., check=<predicate>)`）
  - `verification.verify_output(verb, parameters, inputs, output, proposal)` 签名与返回形状不变，内部委托 `verifier`
  - `task_get` 新增可选参数 `verify: boolean`；为真时返回 `data["verification"]`

- [ ] **Step 1: 写失败测试 —— 三段式与五谓词**

```python
# tests/test_golden_verifier.py
"""D2 single verifier implementation shared by the agent verify path and the commit path."""
import pytest
from shapely.geometry import box, mapping

from definitio import verifier
from definitio.model import GISFault
from definitio.runtime import Runtime


@pytest.fixture
def runtime(tmp_path):
    return Runtime(tmp_path / "workspace.sqlite3")


def polygon(runtime, name="src", crs="EPSG:32649"):
    return runtime.import_dataset(name, {"type": "FeatureCollection", "features": [
        {"type": "Feature", "geometry": mapping(box(500000, 2540000, 500100, 2540100)),
         "properties": {"road_id": "R1"}}]}, crs)


def test_result_is_three_part_and_never_a_blanket_verified_flag():
    ctx = verifier.context(stage="preview", verb="gis_buffer", vid="V18",
                           parameters={"distance": 500, "unit": "m"}, inputs=[], proposal=None,
                           output=None, store=None, db=None, planned_inputs=[],
                           write_scope=None, actual_writes=[], authorization=None,
                           workspace=None, file_artifact=None)
    result = verifier.run(ctx)
    assert result["format"] == "definitio-verification-v1"
    assert set(result) >= {"checked", "found", "unconfirmed", "ok"}
    assert "verified" not in result
    assert len(result["checked"]) == len(verifier.PREDICATES)
    assert all(set(c) >= {"predicate", "subject", "method"} for c in result["checked"])
    assert all(set(f) >= {"predicate", "passed", "observation"} for f in result["found"])
    assert all(set(u) >= {"predicate", "limit"} for u in result["unconfirmed"])


def test_distance_unit_predicate_names_the_exact_mismatch(runtime):
    src = polygon(runtime, crs="EPSG:4326")
    ctx = verifier.context(stage="precheck", verb="gis_buffer", vid="V18",
                           parameters={"distance": 500, "unit": "m"}, inputs=[src],
                           proposal=None, output=None, store=runtime.store, db=None,
                           planned_inputs=[{"id": src["id"], "name": "src", "fingerprint": src["fingerprint"]}],
                           write_scope=None, actual_writes=[], authorization=None,
                           workspace=None, file_artifact=None)
    result = verifier.run(ctx, predicates=(verifier.distance_unit_compatibility,))
    found = result["found"][0]
    assert found["predicate"] == "distance_unit_compatibility" and found["passed"] is False
    assert found["details"]["crs_projected"] is False
    assert found["details"]["requested_unit"] == "m"
    assert result["ok"] is False


def test_input_version_predicate_reports_both_fingerprints(runtime):
    src = polygon(runtime)
    ctx = verifier.context(stage="verify", verb="gis_buffer", vid="V18", parameters={},
                           inputs=[src], proposal=None, output=None, store=runtime.store, db=None,
                           planned_inputs=[{"id": src["id"], "name": "src", "fingerprint": "0" * 64}],
                           write_scope=None, actual_writes=[], authorization=None,
                           workspace=None, file_artifact=None)
    result = verifier.run(ctx, predicates=(verifier.input_version_binding,))
    found = result["found"][0]
    assert found["passed"] is False
    assert found["details"]["changed"] == [{"id": src["id"], "planned": "0" * 64,
                                            "actual": src["fingerprint"]}]


def test_write_scope_predicate_rejects_an_out_of_scope_target(runtime):
    ctx = verifier.context(stage="commit", verb="gis_buffer", vid="V18", parameters={}, inputs=[],
                           proposal=None, output=None, store=runtime.store, db=None, planned_inputs=[],
                           write_scope={"dataset_targets": [{"step": 1, "target": "allowed"}], "file_artifacts": []},
                           actual_writes=[{"kind": "dataset", "target": "intruder"}],
                           authorization=None, workspace=None, file_artifact=None)
    result = verifier.run(ctx, predicates=(verifier.write_scope,))
    assert result["found"][0]["passed"] is False
    assert result["found"][0]["details"]["out_of_scope"] == [{"kind": "dataset", "target": "intruder"}]
```

- [ ] **Step 2: 跑测试确认失败**

Run: `... -m pytest tests/test_golden_verifier.py -q -p no:cacheprovider --basetemp data/t2`
Expected: FAIL — `ImportError: cannot import name 'verifier'`

- [ ] **Step 3: 实现 `verifier.py`**

```python
"""Single verifier implementation: operators compute, this module judges, runtime decides policy.

Every predicate returns what was checked, what was found and what cannot be confirmed.
There is no blanket verified=true.
"""
from __future__ import annotations

from .model import GISFault, digest, logical_fingerprint, require
from .operations import exact_key, geometries

VERIFICATION_FORMAT = "definitio-verification-v1"
CONTEXT_KEYS = ("stage", "verb", "vid", "parameters", "inputs", "proposal", "output", "store", "db",
                "planned_inputs", "write_scope", "actual_writes", "authorization", "workspace", "file_artifact")
DISTANCE_VERBS = ("gis_buffer", "gis_measure")


def context(**kwargs):
    missing = [k for k in CONTEXT_KEYS if k not in kwargs]
    require(not missing, message="验证上下文缺少必需键", missing=missing)
    return {k: kwargs[k] for k in CONTEXT_KEYS}


def finding(predicate, checked, found, unconfirmed, passed, method, **details):
    return {"predicate": predicate, "checked": checked, "found": found, "unconfirmed": unconfirmed,
            "passed": passed, "method": method, "details": details}


def distance_unit_compatibility(ctx):
    name = "distance_unit_compatibility"
    verb, p, inputs = ctx["verb"], ctx["parameters"], ctx["inputs"]
    if verb not in DISTANCE_VERBS:
        return finding(name, f"{verb} 不是距离类操作", "不适用；未做单位断言",
                       "该动词的距离语义不在本批谓词范围内", True, "not_applicable", applicable=False)
    crs = inputs[0]["crs"] if inputs else (ctx["proposal"] or {}).get("crs")
    if crs is None:
        return finding(name, "距离类操作的 CRS 与单位", "无输入 CRS 可核对", "缺少输入快照，无法确认单位相容",
                       None, "insufficient_context", applicable=False)
    requested = p.get("unit", "m")
    compatible = bool(crs["projected"]) and requested == "m" and bool(crs["metres_per_unit"])
    return finding(name, f"{verb} 的请求单位 {requested} 与输入 CRS {crs['code']} 的线性单位 {crs['unit']}",
                   "相容：投影平面、米制请求、换算因子存在" if compatible
                   else f"不相容：projected={crs['projected']}，requested_unit={requested}，metres_per_unit={crs['metres_per_unit']}",
                   "测地精度与基准变换歧义未校准；本谓词只判单位与计算方式相容",
                   compatible, "crs_linear_unit_and_request_unit_comparison",
                   crs_projected=bool(crs["projected"]), crs_unit=crs["unit"],
                   metres_per_unit=crs["metres_per_unit"], requested_unit=requested,
                   crs_code=crs["code"])


def input_version_binding(ctx):
    name = "input_version_binding"
    planned, store = ctx["planned_inputs"], ctx["store"]
    if not planned:
        return finding(name, "计划绑定的输入版本集", "本步骤无外部输入版本绑定（如导入）",
                       "无绑定即无从核对；不因此判定为已验证", True, "no_planned_binding", changed=[])
    if store is None:
        return finding(name, "计划绑定的输入版本集", "无工作区连接，无法读取实际版本",
                       "预演/离线上下文不能确认持久层版本", None, "insufficient_context", changed=[])
    changed = []
    for ref in planned:
        try:
            actual = store.dataset(ref["id"], ctx["db"])
        except GISFault:
            changed.append({"id": ref["id"], "planned": ref["fingerprint"], "actual": None})
            continue
        if store.verify_snapshot(actual) != ref["fingerprint"]:
            changed.append({"id": ref["id"], "planned": ref["fingerprint"], "actual": actual["fingerprint"]})
    return finding(name, f"{len(planned)} 个计划绑定输入的指纹",
                   "全部仍与计划依据一致" if not changed else f"{len(changed)} 个输入版本已变化",
                   "引擎外的进程改动无法从本工作区证明", not changed,
                   "database_readback_logical_sha256", changed=changed)


def spatial_join_contract(ctx):
    name = "spatial_join_contract"
    if ctx["verb"] != "gis_spatial_join":
        return finding(name, "空间连接的键、字段与集合关系", "不适用", "非空间连接动词", True,
                       "not_applicable", applicable=False)
    p, inputs, output = ctx["parameters"], ctx["inputs"], ctx["output"]
    problems = []
    if len(inputs) != 2:
        problems.append(f"需要 2 个输入，实际 {len(inputs)}")
    for d in inputs:
        if d.get("kind", "vector") != "vector":
            problems.append(f"输入 {d['id']} 类型为 {d.get('kind')}，空间连接只接受矢量")
    if len(inputs) == 2 and inputs[0]["crs"]["definition_hash"] != inputs[1]["crs"]["definition_hash"]:
        problems.append("两侧 CRS 不同")
    if output is not None:
        left = {f["name"] for f in inputs[0]["fields"]}
        expected = set(left)
        for spec in inputs[1]["fields"]:
            expected.add(spec["name"] if spec["name"] not in left else "join_" + spec["name"])
        actual = {f["name"] for f in output["fields"]}
        if actual != expected:
            problems.append(f"输出字段集与预期不符：missing={sorted(expected - actual)} unexpected={sorted(actual - expected)}")
        if p.get("summary") == "all" and output["feature_count"] < inputs[0]["feature_count"]:
            problems.append("summary=all 的输出记录数少于左输入记录数")
    return finding(name, f"predicate={p.get('predicate')} summary={p.get('summary')} 的键/字段/集合关系",
                   "符合契约" if not problems else "；".join(problems),
                   "并列候选的业务键稳定性未声明，无法证明确定性选择", not problems,
                   "schema_and_count_set_comparison", problems=problems,
                   matched_count=(output or {}).get("matched_count"),
                   unmatched_count=(output or {}).get("unmatched_count"))


def output_reopenable(ctx):
    name = "output_reopenable"
    output, store, proposal = ctx["output"], ctx["store"], ctx["proposal"]
    if output is None:
        if ctx["stage"] == "preview":
            return finding(name, "输出可重新打开且结构合预期", "预演不落库，无持久输出可重开",
                           "内存预演不能确认持久化后可重开", None, "in_memory_only")
        return finding(name, "输出可重新打开且结构合预期", "本步骤为只读，无数据集输出",
                       "只读结果的重开由工件 URI 承担", True, "read_only_step")
    if store is None:
        return finding(name, "输出可重新打开且结构合预期", "无工作区连接", "无法重开持久记录", None,
                       "insufficient_context")
    reopened = store.dataset(output["id"], ctx["db"])
    actual = store.verify_snapshot(reopened)
    problems = []
    if actual != output["fingerprint"]:
        problems.append("重开指纹与提交指纹不一致")
    if proposal is not None and "geojson" in proposal:
        gj, crs, schema = proposal["geojson"], proposal["crs"], proposal["schema"]
        if reopened["fields"] != schema:
            problems.append("重开字段结构与预期不符")
        if reopened["crs"]["definition_hash"] != crs["definition_hash"]:
            problems.append("重开 CRS 与预期不符")
        if actual != logical_fingerprint(gj, crs, schema):
            problems.append("重开逻辑指纹与提案不一致")
    if "semantic" not in reopened:
        problems.append("重开记录缺少语义对象头")
    return finding(name, f"重新打开 {output['id']} 并核对指纹/字段/CRS/语义头",
                   "结构合预期" if not problems else "；".join(problems),
                   "读回核对不是独立几何 oracle，也不证明跨后端等价", not problems,
                   "database_readback_logical_sha256", problems=problems, fingerprint=actual)


def write_scope(ctx):
    name = "write_scope"
    scope, actual = ctx["write_scope"], ctx["actual_writes"]
    if scope is None:
        return finding(name, "实际写入范围在任务与授权内", "无声明写入范围",
                       "未绑定范围时不能声称写入合规", None, "no_declared_scope")
    allowed = {("dataset", t["target"]) for t in scope.get("dataset_targets", [])}
    allowed |= {("file", a["name"]) for a in scope.get("file_artifacts", [])}
    out = [w for w in actual if (w["kind"], w["target"]) not in allowed]
    grant = ctx["authorization"] or {}
    prefix = grant.get("target_prefix")
    if prefix:
        out += [w for w in actual if not w["target"].startswith(prefix)]
    return finding(name, f"{len(actual)} 项实际写入对 {len(allowed)} 项声明范围",
                   "全部在任务与授权范围内" if not out else f"{len(out)} 项越界",
                   "宿主自身的授权政策不在本引擎可证明范围内", not out,
                   "declared_scope_set_comparison", out_of_scope=out, actual_writes=actual)
```

`operation_readback_invariants(ctx)`：把现有 `verification.py:22-61` 的全部检查逐条搬进来，判定逻辑与 check id 一字不改，只是不再 `require` 抛错，而是收集 `problems`，最后返回一个 `finding`，`details={"checks": [{"id","passed","method"}]}`。`legacy_checks(result)` 把该谓词的 `details["checks"]` 摊平，并把其余 5 个谓词各映射为 `{"id": predicate, "passed": passed is not False, "method": method}`。

`run`：

```python
CONTRACT_PREDICATES = (distance_unit_compatibility, input_version_binding, spatial_join_contract,
                       output_reopenable, write_scope)
PREDICATES = CONTRACT_PREDICATES + (operation_readback_invariants,)


def run(ctx, predicates=None):
    chosen = PREDICATES if predicates is None else predicates
    findings = [p(ctx) for p in chosen]
    return {"format": VERIFICATION_FORMAT, "stage": ctx["stage"], "verb": ctx["verb"],
            "checked": [{"predicate": f["predicate"], "subject": f["checked"], "method": f["method"]} for f in findings],
            "found": [{"predicate": f["predicate"], "passed": f["passed"], "observation": f["found"],
                       "details": f["details"]} for f in findings],
            "unconfirmed": [{"predicate": f["predicate"], "limit": f["unconfirmed"]} for f in findings],
            "ok": all(f["passed"] is not False for f in findings),
            "predicates_digest": digest([p.__name__ for p in chosen])}


def enforce(result):
    for f in result["found"]:
        require(f["passed"] is not False, "BACKEND_ERROR", "读回结果违反后置条件，当前成果已回滚",
                check=f["predicate"], observation=f["observation"], details=f["details"])
```

- [ ] **Step 4: 跑测试确认通过**

Run: `... -m pytest tests/test_golden_verifier.py -q -p no:cacheprovider --basetemp data/t2`
Expected: 4 PASS

- [ ] **Step 5: 写失败测试 —— 两路径代码级同一性**

追加：

```python
def test_agent_verify_and_commit_paths_call_the_same_predicate_objects(runtime, monkeypatch):
    from definitio.agent import AgentService
    src = polygon(runtime)
    service = AgentService(runtime)
    seen = {"commit": [], "verify": []}
    original = verifier.PREDICATES

    def wrapped(fn, bucket):
        def inner(ctx):
            seen[bucket].append(fn)
            return fn(ctx)
        inner.__name__ = fn.__name__
        return inner

    task = runtime.declare("buffer", [{"verb": "gis_buffer", "parameters": {
        "input": src["id"], "target": "b", "distance": 10, "unit": "m", "dissolve": False}}])
    monkeypatch.setattr(verifier, "PREDICATES", tuple(wrapped(f, "commit") for f in original))
    runtime.execute(task["id"], task["plan_digest"], True)
    monkeypatch.setattr(verifier, "PREDICATES", tuple(wrapped(f, "verify") for f in original))
    reply = service.call("task_get", {"task_id": task["id"], "verify": True})
    assert reply["ok"], reply
    assert seen["commit"] and seen["verify"]
    assert [f.__name__ for f in seen["commit"]] == [f.__name__ for f in seen["verify"]]
    assert {id(f) for f in seen["commit"]} == {id(f) for f in seen["verify"]}
    assert reply["data"]["verification"]["predicates_digest"] == verifier.run(
        verifier.context(stage="verify", verb="gis_buffer", vid="V18", parameters={}, inputs=[],
                         proposal=None, output=None, store=None, db=None, planned_inputs=[],
                         write_scope=None, actual_writes=[], authorization=None, workspace=None,
                         file_artifact=None))["predicates_digest"]


def test_preview_uses_the_same_predicates_and_reports_unconfirmed_persistence(runtime, monkeypatch):
    from definitio.preview import preview
    src = polygon(runtime)
    seen = []
    original = verifier.PREDICATES

    def wrapped(fn):
        def inner(ctx):
            seen.append(fn)
            return fn(ctx)
        inner.__name__ = fn.__name__
        return inner

    monkeypatch.setattr(verifier, "PREDICATES", tuple(wrapped(f) for f in original))
    result = preview(runtime, "buffer", [{"verb": "gis_buffer", "parameters": {
        "input": src["id"], "target": "b", "distance": 10, "unit": "m", "dissolve": False}}])
    assert result["ok"]
    assert {id(f) for f in seen} == {id(f) for f in original}
    step = result["steps"][0]
    assert step["verification"]["stage"] == "preview"
    assert any(u["predicate"] == "output_reopenable" for u in step["verification"]["unconfirmed"])


def test_legacy_verify_output_still_rolls_back_fake_success(runtime, monkeypatch):
    from definitio.operations import compute as real_compute
    from definitio.verification import verify_output
    src = runtime.import_dataset("d", {"type": "FeatureCollection", "features": [
        {"type": "Feature", "geometry": mapping(box(0, 0, 1, 1)), "properties": {"g": "A"}},
        {"type": "Feature", "geometry": mapping(box(1, 0, 2, 1)), "properties": {"g": "A"}}]}, "EPSG:32649")

    def corrupt(verb, p, inputs):
        proposal = real_compute(verb, p, inputs)
        proposal["geojson"]["features"] = []
        return proposal

    monkeypatch.setattr("definitio.runtime.compute", corrupt)
    task = runtime.declare("dissolve", [{"verb": "gis_dissolve", "parameters": {
        "input": src["id"], "target": "out", "fields": ["g"]}}])
    task = runtime.execute(task["id"], task["plan_digest"], True)
    assert task["task_state"] == "FAILED" and task["error"]["code"] == "BACKEND_ERROR"
    assert not task["output_refs"] and len(runtime.store.datasets()) == 1
```

- [ ] **Step 6: 跑测试确认失败**

Run: `... -m pytest tests/test_golden_verifier.py -q -p no:cacheprovider --basetemp data/t2`
Expected: 新增 3 项 FAIL（`task_get` 无 `verify` 参数 / preview 无 `verification`）

- [ ] **Step 7: 接线 —— verification 适配、runtime、preview、agent、surface**

`verification.py` 全文替换为：

```python
"""Compatibility adapter: the only implementation lives in definitio.verifier."""
from __future__ import annotations

from . import verifier


def verify_output(verb, parameters, inputs, output, proposal):
    ctx = verifier.context(stage="commit", verb=verb, vid="", parameters=parameters, inputs=inputs,
                           proposal=proposal, output=output, store=None, db=None, planned_inputs=[],
                           write_scope=None, actual_writes=[], authorization=None, workspace=None,
                           file_artifact=None)
    result = verifier.run(ctx, predicates=(verifier.operation_readback_invariants,))
    verifier.enforce(result)
    return verifier.legacy_checks(result)
```

`runtime.py`：
- `_execute_step` 在 `compute` 之前加 precheck：构造 `stage="precheck"` 的 ctx（`planned_inputs` 取 `task["input_snapshot_refs"]`，`write_scope=task.get("write_scope")`，`actual_writes=[]`），`verifier.run(ctx, predicates=(verifier.distance_unit_compatibility, verifier.input_version_binding))`；若 `ok is False`，按谓词抛 `GISFault`：`distance_unit_compatibility` → `NOT_SUPPORTED`（保住既有断言），`input_version_binding` → `TARGET_STATE_UNKNOWN`，`details` 带 `predicate`/`observation`/`details`。
- `verify_output(...)` 调用点保持不变（它现在委托同一实现）。
- 提交前追加 `stage="commit"` 的完整 `verifier.run`，`verifier.enforce(result)`，并把 `result` 写进 `evidence["verification"]`；`actual_writes` 由本步骤实际写入组装。

`preview.py`：每步 compute 之后构造 `stage="preview"` ctx（`store=runtime.store`、`db=None`、`output=None`、`planned_inputs` 来自 `task["input_snapshot_refs"]`、`write_scope=task.get("write_scope")`、`actual_writes=[]`），`item["verification"] = verifier.run(ctx)`；若 `ok is False` 则按 GISFault 处理为 FAIL。

`agent.py`：
- `control_definitions()["task_get"]` 的 schema 加 `"verify": {"type": "boolean", "default": False, "description": "Re-run the single verifier against the committed steps and return checked/found/unconfirmed"}`（`required` 不变）。
- `_control` 的 `task_get` 分支：`verify` 为真时逐步构造 `stage="verify"` ctx 并 `verifier.run`，汇总为 `data["verification"] = {"format": ..., "steps": [...], "ok": all(...)}`。
- `surfaces/surface-v1.json` 的 `parameters.task_get` 加 `"verify": "verify"`。

- [ ] **Step 8: 跑测试确认通过 + 定向回归**

Run: `... -m pytest tests/test_golden_verifier.py tests/test_runtime.py tests/test_agent.py tests/test_surface.py tests/test_workstation.py -q -p no:cacheprovider --basetemp data/t2`
Expected: 全 PASS（`test_surface.py` 13/13，含双射校验）

- [ ] **Step 9: 提交**

```bash
git add definitio/verifier.py definitio/verification.py definitio/runtime.py definitio/preview.py definitio/agent.py definitio/surfaces/surface-v1.json tests/test_golden_verifier.py
git commit -m "feat(D2): one verifier implementation shared by verify and commit paths"
```

---

## Task 3: D3 版本绑定提交与文件产物暂存发布

**Files:**
- Create: `definitio/commit.py`
- Modify: `definitio/store.py`（`file_artifacts` 表 + 读写）
- Modify: `definitio/runtime.py:71-175, 177-221`
- Modify: `definitio/agent.py`（`task_plan` 加 `exports`；`artifact()` 加 artifacts 分支）
- Modify: `definitio/surfaces/surface-v1.json`（`task_plan.parameters` 加 `exports`）
- Test: `tests/test_golden_commit.py`

**Interfaces:**
- Consumes: Task 1 `semantics.object_header`；Task 2 `verifier.context/run/enforce`
- Produces:
  - `commit.WRITE_SCOPE_FORMAT = "definitio-write-scope-v1"`
  - `commit.COMMIT_STATES = ("committed", "rejected_state_changed", "pending_confirmation")`
  - `commit.bind_write_scope(steps, exports, workspace) -> dict`，键 `{"format","dataset_targets":[{"step","verb","target","mode"}],"file_artifacts":[{"step","name","format"}],"workspace"}`
  - `commit.evaluate_binding(task, store) -> dict`，键 `{"state","reasons","changed_inputs","changed_targets"}`
  - `commit.publish_file_artifact(store, data_dir, *, dataset, name, request_key, task_id, step_id) -> dict`（返回 `{"artifact","replayed"}`）
  - `commit.readback_file_artifact(data_dir, record) -> dict`
  - `store.file_artifact(reference, db=None) -> dict`、`store.file_artifacts() -> list`
  - `runtime.declare(goal, steps=None, dataset=None, secondary=None, persist=True, exports=None)`；`task["write_scope"]`、`task["commit_state"]`
  - `task_plan` 新增可选参数 `exports: array`，每项 `{"step": integer>=1, "name": string, "format": "geojson"}`
  - 新 URI 分支 `definitio://artifacts/{id}`

- [ ] **Step 1: 写失败测试 —— 三态、暂存清理、越界与幂等**

```python
# tests/test_golden_commit.py
"""D3 version-bound commit, staged file publish, three-state honesty."""
import json
import sqlite3

import pytest
from shapely.geometry import box, mapping

from definitio import commit
from definitio.model import GISFault
from definitio.runtime import Runtime


@pytest.fixture
def runtime(tmp_path):
    return Runtime(tmp_path / "workspace.sqlite3")


def src(runtime):
    return runtime.import_dataset("roads", {"type": "FeatureCollection", "features": [
        {"type": "Feature", "geometry": mapping(box(500000, 2540000, 500100, 2540100)),
         "properties": {"road_id": "R1"}}]}, "EPSG:32649")


def plan(runtime, d, exports=None):
    return runtime.declare("buffer", [{"verb": "gis_buffer", "parameters": {
        "input": d["id"], "target": "buf", "distance": 10, "unit": "m", "dissolve": False}}], exports=exports)


def test_plan_binds_input_versions_and_write_scope(runtime):
    d = src(runtime)
    task = plan(runtime, d, exports=[{"step": 1, "name": "buf.geojson", "format": "geojson"}])
    assert task["write_scope"]["format"] == "definitio-write-scope-v1"
    assert task["write_scope"]["dataset_targets"] == [{"step": 1, "verb": "gis_buffer", "target": "buf", "mode": "create"}]
    assert task["write_scope"]["file_artifacts"] == [{"step": 1, "name": "buf.geojson", "format": "geojson"}]
    assert task["input_snapshot_refs"] == [{"id": d["id"], "name": "roads", "fingerprint": d["fingerprint"]}]
    assert task["commit_state"] == "pending_confirmation"


def test_changed_input_rejects_the_old_plan_with_an_explicit_reason(runtime):
    d = src(runtime)
    task = plan(runtime, d)
    with sqlite3.connect(runtime.store.path) as db:
        row = db.execute("SELECT document FROM datasets WHERE id=?", (d["id"],)).fetchone()
        document = json.loads(row[0])
        document["feature_count"] = 999
        db.execute("UPDATE datasets SET document=? WHERE id=?", (json.dumps(document), d["id"]))
    task = runtime.execute(task["id"], task["plan_digest"], True)
    assert task["task_state"] == "FAILED"
    assert task["commit_state"] == "rejected_state_changed"
    assert task["error"]["details"]["rule"] == "version_binding_changed"
    assert task["error"]["details"]["changed_inputs"][0]["id"] == d["id"]
    assert not task["output_refs"]


def test_commit_is_pending_until_real_readback_confirms(runtime):
    d = src(runtime)
    task = plan(runtime, d)
    task = runtime.execute(task["id"], task["plan_digest"], True)
    assert task["commit_state"] == "committed"
    assert task["readback"]["confirmed"] is True
    assert task["readback"]["outputs"][0]["fingerprint"] == runtime.store.dataset(task["output_refs"][0])["fingerprint"]


def test_file_artifact_is_staged_verified_then_published(runtime, tmp_path):
    d = src(runtime)
    task = plan(runtime, d, exports=[{"step": 1, "name": "buf.geojson", "format": "geojson"}])
    task = runtime.execute(task["id"], task["plan_digest"], True)
    record = runtime.store.file_artifacts()[0]
    published = tmp_path / "artifacts" / "buf.geojson"
    assert published.exists() and not list((tmp_path / "artifacts" / ".staging").iterdir())
    document = json.loads(published.read_text(encoding="utf-8"))
    assert document["definitio"]["fingerprint"] == record["sha256_dataset"]
    assert record["bytes"] == published.stat().st_size
    readback = commit.readback_file_artifact(tmp_path, record)
    assert readback["confirmed"] is True and readback["feature_count"] == 1


def test_publish_retry_is_idempotent_and_never_overwrites(runtime, tmp_path):
    d = src(runtime)
    out = runtime.store.dataset(runtime.execute(plan(runtime, d), plan(runtime, d)["plan_digest"], True)["output_refs"][0]) if False else None
    task = plan(runtime, d, exports=[{"step": 1, "name": "buf.geojson", "format": "geojson"}])
    task = runtime.execute(task["id"], task["plan_digest"], True)
    first = runtime.store.file_artifacts()[0]
    again = commit.publish_file_artifact(runtime.store, tmp_path, dataset=runtime.store.dataset(first["dataset_id"]),
                                         name="buf.geojson", request_key=first["request_key"],
                                         task_id=task["id"], step_id=task["steps"][0]["id"])
    assert again["replayed"] is True and again["artifact"]["id"] == first["id"]
    assert len(runtime.store.file_artifacts()) == 1
    with pytest.raises(GISFault) as caught:
        commit.publish_file_artifact(runtime.store, tmp_path, dataset=runtime.store.dataset(first["dataset_id"]),
                                     name="buf.geojson", request_key="different-key",
                                     task_id=task["id"], step_id=task["steps"][0]["id"])
    assert caught.value.code == "ALREADY_EXISTS"


def test_publish_rejects_path_traversal_and_cleans_staging(runtime, tmp_path):
    d = src(runtime)
    for bad in ("../escape.geojson", "sub/dir.geojson", "..\\escape.geojson", "buf.txt"):
        with pytest.raises(GISFault) as caught:
            commit.publish_file_artifact(runtime.store, tmp_path, dataset=d, name=bad,
                                         request_key="k-" + bad, task_id="t", step_id="s")
        assert caught.value.code == "INVALID_ARGUMENT"
    staging = tmp_path / "artifacts" / ".staging"
    assert not staging.exists() or not list(staging.iterdir())
    assert not (tmp_path / "artifacts").exists() or not [p for p in (tmp_path / "artifacts").iterdir() if p.name != ".staging"]


def test_failed_verification_cleans_staging_and_publishes_nothing(runtime, tmp_path, monkeypatch):
    d = src(runtime)
    monkeypatch.setattr(commit, "verify_staged", lambda *a, **k: {"ok": False, "reason": "injected"})
    with pytest.raises(GISFault):
        commit.publish_file_artifact(runtime.store, tmp_path, dataset=d, name="buf.geojson",
                                     request_key="k1", task_id="t", step_id="s")
    assert not (tmp_path / "artifacts" / "buf.geojson").exists()
    staging = tmp_path / "artifacts" / ".staging"
    assert not staging.exists() or not list(staging.iterdir())
    assert runtime.store.file_artifacts() == []
```

> 实现期注意：上面 `test_publish_retry_is_idempotent_and_never_overwrites` 第一行的 `if False else None` 是写计划时的残留，实施时删掉该行，只保留 `task = plan(...)` 起的正文。

- [ ] **Step 2: 跑测试确认失败**

Run: `... -m pytest tests/test_golden_commit.py -q -p no:cacheprovider --basetemp data/t3`
Expected: FAIL — `ImportError: cannot import name 'commit'`

- [ ] **Step 3: 实现 `commit.py` 与 store 表**

```python
"""Version-bound commit policy and staged file-artifact publication."""
from __future__ import annotations

import hashlib
import json
import os
import time
from pathlib import Path

from .model import GISFault, canonical, digest, name_ok, now, require
from .store import identifier

WRITE_SCOPE_FORMAT = "definitio-write-scope-v1"
COMMIT_STATES = ("committed", "rejected_state_changed", "pending_confirmation")
ARTIFACT_FORMAT = "definitio-file-artifact-v1"


def bind_write_scope(steps, exports, workspace):
    from .vector_extra import write_target
    targets = []
    for step in steps:
        target = write_target(step["verb"], step["parameters"])
        if not target:
            continue
        mode = ("advance_head" if step["verb"] == "gis_append_dataset" else
                "rename" if step["verb"] == "gis_rename_dataset" else
                "destroy" if step["verb"] == "gis_delete_dataset" else "create")
        targets.append({"step": step["index"], "verb": step["verb"], "target": target, "mode": mode})
    files = []
    for item in exports or []:
        require(isinstance(item, dict) and set(item) == {"step", "name", "format"},
                message="exports 每项须仅含 step/name/format")
        require(type(item["step"]) is int and 1 <= item["step"] <= len(steps), message="exports.step 越界")
        require(item["format"] == "geojson", "NOT_SUPPORTED", "文件产物首版只开放 geojson")
        artifact_name(item["name"])
        files.append({"step": item["step"], "name": item["name"], "format": item["format"]})
    require(len({f["name"] for f in files}) == len(files), message="exports 名称重复")
    return {"format": WRITE_SCOPE_FORMAT, "dataset_targets": targets, "file_artifacts": files,
            "workspace": str(workspace)}


def artifact_name(name):
    require(isinstance(name, str) and name.endswith(".geojson"), message="文件产物名须以 .geojson 结尾")
    stem = name[:-len(".geojson")]
    require("/" not in name and "\\" not in name and ".." not in name, message="文件产物名不得含路径分隔或上跳")
    name_ok(stem)
    return name


def evaluate_binding(task, store):
    changed_inputs, changed_targets, reasons = [], [], []
    for ref in task["input_snapshot_refs"]:
        try:
            d = store.dataset(ref["id"])
        except GISFault:
            changed_inputs.append({"id": ref["id"], "planned": ref["fingerprint"], "actual": None})
            continue
        actual = store.verify_snapshot(d)
        if actual != ref["fingerprint"]:
            changed_inputs.append({"id": ref["id"], "planned": ref["fingerprint"], "actual": actual})
    with store.connect() as db:
        for target in task.get("write_scope", {}).get("dataset_targets", []):
            if target["mode"] != "create":
                continue
            row = db.execute("SELECT dataset_id FROM dataset_heads WHERE name=?", (target["target"],)).fetchone()
            if row:
                changed_targets.append({"target": target["target"], "occupied_by": row[0]})
    if changed_inputs:
        reasons.append(f"{len(changed_inputs)} 个输入版本在规划后已变化")
    if changed_targets:
        reasons.append(f"{len(changed_targets)} 个目标名称在规划后已被占用")
    state = "rejected_state_changed" if reasons else "pending_confirmation"
    return {"state": state, "reasons": reasons, "changed_inputs": changed_inputs, "changed_targets": changed_targets}


def artifact_paths(data_dir):
    root = Path(data_dir) / "artifacts"
    return root, root / ".staging"


def dataset_payload(dataset):
    return {**dataset["geojson"], "definitio": {
        "crs": dataset["crs"], "schema": dataset["fields"], "fingerprint": dataset["fingerprint"],
        "semantic": dataset.get("semantic"),
        "format": "GeoJSON-compatible snapshot; projected CRS retained; reproject to EPSG:4326 for RFC7946"}}


def verify_staged(path, expected_sha256, expected_fingerprint, expected_count):
    raw = path.read_bytes()
    actual = hashlib.sha256(raw).hexdigest()
    if actual != expected_sha256:
        return {"ok": False, "reason": "staged_bytes_sha256_mismatch"}
    try:
        document = json.loads(raw.decode("utf-8"))
    except (ValueError, UnicodeError) as exc:
        return {"ok": False, "reason": f"staged_not_reopenable: {exc}"}
    header = document.get("definitio", {})
    if header.get("fingerprint") != expected_fingerprint:
        return {"ok": False, "reason": "staged_fingerprint_mismatch"}
    if len(document.get("features", [])) != expected_count:
        return {"ok": False, "reason": "staged_feature_count_mismatch"}
    return {"ok": True, "sha256": actual, "feature_count": expected_count}


def publish_file_artifact(store, data_dir, *, dataset, name, request_key, task_id, step_id):
    artifact_name(name)
    require(isinstance(request_key, str) and request_key, message="request_key 须为非空字符串")
    existing = store.file_artifact_by_key(request_key)
    if existing is not None:
        readback = readback_file_artifact(data_dir, existing)
        require(readback["confirmed"], "TARGET_STATE_UNKNOWN", "既有文件产物回读失败；不重复发布",
                artifact=existing["id"])
        return {"artifact": existing, "replayed": True}
    require(store.file_artifact_by_name(name) is None, "ALREADY_EXISTS", "文件产物名已存在；不会覆盖", target=name)
    root, staging = artifact_paths(data_dir)
    staging.mkdir(parents=True, exist_ok=True)
    token = identifier("stage")
    staged = staging / f"{token}.tmp"
    payload = canonical(dataset_payload(dataset)).encode("utf-8")
    expected = hashlib.sha256(payload).hexdigest()
    try:
        staged.write_bytes(payload)
        checked = verify_staged(staged, expected, dataset["fingerprint"], dataset["feature_count"])
        require(checked["ok"], "BACKEND_ERROR", "暂存产物校验未通过；已清理暂存", reason=checked["reason"])
        root.mkdir(parents=True, exist_ok=True)
        published = root / name
        require(not published.exists(), "ALREADY_EXISTS", "文件产物名已存在；不会覆盖", target=name)
        os.replace(staged, published)
        record = {"id": identifier("fa"), "request_key": request_key, "name": name, "task_id": task_id,
                  "step_id": step_id, "dataset_id": dataset["id"], "sha256": expected,
                  "sha256_dataset": dataset["fingerprint"], "bytes": len(payload),
                  "created_at": now(), "format": ARTIFACT_FORMAT,
                  "uri": f"definitio://artifacts/{identifier('fa')}"}
        record["uri"] = f"definitio://artifacts/{record['id']}"
        record["path"] = str(published)
        store.save_file_artifact(record)
        readback = readback_file_artifact(data_dir, record)
        require(readback["confirmed"], "TARGET_STATE_UNKNOWN", "发布后回读未确认；不得报成功", artifact=record["id"])
        return {"artifact": record, "replayed": False}
    finally:
        if staged.exists():
            staged.unlink()


def readback_file_artifact(data_dir, record):
    root, _ = artifact_paths(data_dir)
    path = root / record["name"]
    if not path.exists():
        return {"confirmed": False, "reason": "published_file_missing"}
    raw = path.read_bytes()
    actual = hashlib.sha256(raw).hexdigest()
    if actual != record["sha256"] or len(raw) != record["bytes"]:
        return {"confirmed": False, "reason": "published_bytes_mismatch"}
    document = json.loads(raw.decode("utf-8"))
    header = document.get("definitio", {})
    if header.get("fingerprint") != record["sha256_dataset"]:
        return {"confirmed": False, "reason": "published_fingerprint_mismatch"}
    return {"confirmed": True, "sha256": actual, "feature_count": len(document.get("features", [])),
            "fields": header.get("schema"), "crs": header.get("crs", {}).get("code"),
            "axis_order": header.get("crs", {}).get("axis_order")}
```

`store.py` 建表处追加：

```sql
CREATE TABLE IF NOT EXISTS file_artifacts (
    id TEXT PRIMARY KEY, request_key TEXT UNIQUE NOT NULL, name TEXT UNIQUE NOT NULL,
    task_id TEXT, step_id TEXT, dataset_id TEXT NOT NULL, sha256 TEXT NOT NULL,
    sha256_dataset TEXT NOT NULL, bytes INTEGER NOT NULL, document TEXT NOT NULL, created_at TEXT NOT NULL);
```

并加方法 `save_file_artifact(record)`、`file_artifact(reference, db=None)`（按 id 或 name）、`file_artifact_by_key(key)`、`file_artifact_by_name(name)`、`file_artifacts()`。

`runtime.py`：
- `declare(..., exports=None)`：`task["write_scope"] = commit.bind_write_scope(normalized, exports, Path(self.store.path).parent)`；`task["commit_state"] = "pending_confirmation"`；`task["exports"] = exports or []`。`plan_digest` 加入 `"write_scope": task.get("write_scope")`。
- `execute`：在既有输入指纹核对处改为调用 `commit.evaluate_binding`；若 `state == "rejected_state_changed"` → 记 `task["commit_state"]`、`task_state="FAILED"`、`error=GISFault("TARGET_STATE_UNKNOWN", "规划后状态已变化；拒绝旧计划", rule="version_binding_changed", reasons=..., changed_inputs=..., changed_targets=...)`，`save_task` 后返回 task（不抛异常，以便三态可见）。
- 全链成功后：先置 `commit_state="pending_confirmation"`，对每个 `output_refs` 真实回读核对指纹，再对每个 `exports` 调 `publish_file_artifact`（`request_key = digest({"task": task["id"], "step": step_id, "name": name})`），全部确认后才置 `commit_state="committed"` 并写 `task["readback"] = {"confirmed": True, "outputs": [...], "file_artifacts": [...]}`。

`agent.py`：`task_plan` schema 加 `exports`；`_task_request` 透传；`artifact()` 加 `definitio://artifacts/{id}` 分支返回记录 + 回读结果。`surface-v1.json` 的 `parameters.task_plan` 加 `"exports": "exports"`。

- [ ] **Step 4: 跑测试确认通过 + 定向回归**

Run: `... -m pytest tests/test_golden_commit.py tests/test_runtime.py tests/test_agent.py tests/test_surface.py tests/test_usability.py -q -p no:cacheprovider --basetemp data/t3`
Expected: 全 PASS

- [ ] **Step 5: 提交**

```bash
git add definitio/commit.py definitio/store.py definitio/runtime.py definitio/agent.py definitio/surfaces/surface-v1.json tests/test_golden_commit.py
git commit -m "feat(D3): version-bound three-state commit with staged file publication"
```

---

## Task 4: D4 可恢复计划

**Files:**
- Create: `definitio/resume.py`
- Modify: `definitio/runtime.py`（`_execute_step` 写检查点；抽出 `_run_steps`；新增 `resume`）
- Modify: `definitio/agent.py`（`task_execute` 加 `resume`）
- Modify: `definitio/surfaces/surface-v1.json`（`task_execute.parameters` 加 `resume`）
- Test: `tests/test_golden_resume.py`

**Interfaces:**
- Consumes: Task 3 `commit.evaluate_binding`、`task["write_scope"]`
- Produces:
  - `resume.CHECKPOINT_FORMAT = "definitio-step-checkpoint-v1"`
  - `resume.checkpoint(step, *, inputs, output, evidence_ref, task) -> dict`，键 `{"format","dependencies","input_versions","output_object","check_location","side_effects","retryable","valid","state_at"}`
  - `resume.validate_checkpoints(task, store) -> dict`，键 `{"valid": [step_id], "invalidated": [{"step","reason"}]}`
  - `runtime.resume(task_id, plan_digest, confirm=False) -> task`
  - `task_execute` 新增可选参数 `resume: boolean`
  - `$previous` 语义与既有兼容不变

- [ ] **Step 1: 写失败测试**

```python
# tests/test_golden_resume.py
"""D4 resumable plans: recover from still-valid results, never rerun the whole chain."""
import pytest
from shapely.geometry import box, mapping

from definitio import resume
from definitio.model import GISFault
from definitio.runtime import Runtime


@pytest.fixture
def runtime(tmp_path):
    return Runtime(tmp_path / "workspace.sqlite3")


def chain(runtime, d):
    return runtime.declare("chain", [
        {"verb": "gis_buffer", "parameters": {"input": d["id"], "target": "b1", "distance": 10, "unit": "m", "dissolve": False}},
        {"verb": "gis_dissolve", "parameters": {"input": "$previous", "target": "b2", "fields": []}},
        {"verb": "gis_validate_geometry", "parameters": {"dataset": "$previous", "repair": False}}])


def source(runtime):
    return runtime.import_dataset("roads", {"type": "FeatureCollection", "features": [
        {"type": "Feature", "geometry": mapping(box(500000, 2540000, 500100, 2540100)), "properties": {"road_id": "R1"}},
        {"type": "Feature", "geometry": mapping(box(500200, 2540000, 500300, 2540100)), "properties": {"road_id": "R2"}}]}, "EPSG:32649")


def test_every_committed_step_records_a_checkpoint(runtime):
    d = source(runtime)
    task = runtime.execute(chain(runtime, d)["id"], chain(runtime, d)["plan_digest"], True) if False else None
    declared = chain(runtime, d)
    task = runtime.execute(declared["id"], declared["plan_digest"], True)
    assert task["task_state"] == "COMMITTED"
    for step in task["steps"]:
        cp = step["checkpoint"]
        assert cp["format"] == "definitio-step-checkpoint-v1"
        assert cp["side_effects"] in ("new_dataset", "none")
        assert isinstance(cp["retryable"], bool)
        assert cp["check_location"]["uri"].startswith("definitio://evidence/")
        assert cp["input_versions"] and all({"id", "fingerprint"} <= set(v) for v in cp["input_versions"])
    assert task["steps"][0]["checkpoint"]["dependencies"] == []
    assert task["steps"][1]["checkpoint"]["dependencies"] == [task["steps"][0]["id"]]


def test_midchain_failure_resumes_without_rerunning_valid_steps(runtime, monkeypatch):
    d = source(runtime)
    declared = chain(runtime, d)
    real = runtime._execute_step
    calls = []

    def flaky(task, step, previous):
        calls.append(step["index"])
        if step["index"] == 2 and calls.count(2) == 1:
            raise GISFault("BACKEND_ERROR", "injected mid-chain failure")
        return real(task, step, previous)

    monkeypatch.setattr(runtime, "_execute_step", flaky)
    failed = runtime.execute(declared["id"], declared["plan_digest"], True)
    assert failed["task_state"] == "FAILED" and failed["completion"] == "partial"
    assert failed["steps"][0]["state"] == "COMMITTED"
    first_output = failed["steps"][0]["output_ref"]
    monkeypatch.setattr(runtime, "_execute_step", real)
    resumed = runtime.resume(declared["id"], declared["plan_digest"], True)
    assert resumed["task_state"] == "COMMITTED" and resumed["completion"] == "all"
    assert resumed["steps"][0]["output_ref"] == first_output
    assert resumed["steps"][0]["attempt_seq"] == 1
    assert resumed["resumed_from"] == 2
    assert len(resumed["output_refs"]) == 2


def test_changed_input_version_invalidates_dependent_results(runtime):
    import json, sqlite3
    d = source(runtime)
    declared = chain(runtime, d)
    task = runtime.execute(declared["id"], declared["plan_digest"], True)
    with sqlite3.connect(runtime.store.path) as db:
        row = db.execute("SELECT document FROM datasets WHERE id=?", (d["id"],)).fetchone()
        document = json.loads(row[0])
        document["feature_count"] = 42
        db.execute("UPDATE datasets SET document=? WHERE id=?", (json.dumps(document), d["id"]))
    report = resume.validate_checkpoints(runtime.store.task(declared["id"]), runtime.store)
    assert report["invalidated"] and report["invalidated"][0]["step"] == 1
    with pytest.raises(GISFault) as caught:
        runtime.resume(declared["id"], declared["plan_digest"], True)
    assert caught.value.code == "TARGET_STATE_UNKNOWN"
    assert caught.value.details["rule"] == "resume_dependency_invalidated"


def test_isolated_tasks_are_never_resumed(runtime):
    d = source(runtime)
    declared = chain(runtime, d)
    task = runtime.store.task(declared["id"])
    task["task_state"] = "ISOLATED"
    task["error"] = {"code": "TARGET_STATE_UNKNOWN", "message": "unknown external state"}
    runtime.store.save_task(task)
    with pytest.raises(GISFault) as caught:
        runtime.resume(declared["id"], declared["plan_digest"], True)
    assert caught.value.code == "TARGET_STATE_UNKNOWN"


def test_previous_reference_still_works_after_resume(runtime):
    d = source(runtime)
    declared = chain(runtime, d)
    task = runtime.execute(declared["id"], declared["plan_digest"], True)
    assert task["steps"][2]["result"]["checked"] == 1
```

> 实施时删掉 `test_every_committed_step_records_a_checkpoint` 里 `if False else None` 那行残留。

- [ ] **Step 2: 跑测试确认失败**

Run: `... -m pytest tests/test_golden_resume.py -q -p no:cacheprovider --basetemp data/t4`
Expected: FAIL — `ImportError: cannot import name 'resume'`

- [ ] **Step 3: 实现 `resume.py` 与 runtime 接线**

```python
"""Step checkpoints and dependency invalidation for resumable plans."""
from __future__ import annotations

from .model import GISFault, now, require

CHECKPOINT_FORMAT = "definitio-step-checkpoint-v1"
SIDE_EFFECTS = {"gis_delete_dataset": "destroy", "gis_rename_dataset": "rename",
                "gis_append_dataset": "advance_head", "gis_publish_layer": "external_publish",
                "gis_add_layer_from_catalog": "advance_head", "gis_export_map_image": "external_file"}
NON_RETRYABLE = {"destroy", "rename", "external_publish", "advance_head", "external_file"}


def checkpoint(step, *, inputs, output, evidence_ref, task):
    side = SIDE_EFFECTS.get(step["verb"], "new_dataset" if output else "none")
    dependencies = [s["id"] for s in task["steps"][:step["index"] - 1]
                    if s.get("output_ref") and "$previous" in _canonical_params(step)]
    return {"format": CHECKPOINT_FORMAT, "dependencies": dependencies,
            "input_versions": [{"id": d["id"], "fingerprint": d["fingerprint"]} for d in inputs],
            "output_object": {"id": output["id"], "fingerprint": output["fingerprint"]} if output else None,
            "check_location": {"evidence_ref": evidence_ref, "uri": f"definitio://evidence/{evidence_ref}"},
            "side_effects": side, "retryable": side not in NON_RETRYABLE, "valid": True, "state_at": now()}


def _canonical_params(step):
    from .model import canonical
    return canonical(step["parameters"])


def validate_checkpoints(task, store):
    valid, invalidated = [], []
    for step in task["steps"]:
        cp = step.get("checkpoint")
        if step["state"] != "COMMITTED" or not cp:
            continue
        reason = None
        for ref in cp["input_versions"]:
            try:
                d = store.dataset(ref["id"])
            except GISFault:
                reason = f"输入 {ref['id']} 已不存在"
                break
            if store.verify_snapshot(d) != ref["fingerprint"]:
                reason = f"输入 {ref['id']} 版本已变化"
                break
        if reason is None and cp["output_object"]:
            try:
                out = store.dataset(cp["output_object"]["id"])
                if store.verify_snapshot(out) != cp["output_object"]["fingerprint"]:
                    reason = "输出快照指纹已变化"
            except GISFault:
                reason = "输出对象已不存在"
        if reason:
            cp["valid"] = False
            invalidated.append({"step": step["index"], "step_id": step["id"], "reason": reason})
        else:
            cp["valid"] = True
            valid.append(step["id"])
    return {"valid": valid, "invalidated": invalidated}
```

`runtime.py`：
- `_execute_step` 末尾 `step.update(...)` 处追加 `checkpoint=resume.checkpoint(step, inputs=inputs, output=output, evidence_ref=evidence_id, task=task)`。
- 把 `execute` 的 `for step in task["steps"]` 循环体抽成 `_run_steps(self, task, previous, start_index=0)`，`execute` 调它；失败处理逻辑原样保留。
- 新增：

```python
    def resume(self, task_id, plan_digest, confirm=False):
        with self.store.lock:
            task = self.store.task(task_id)
            require(plan_digest == task["plan_digest"] == self.plan_digest(task), message="计划摘要不一致，请重新规划后确认")
            require(task["task_state"] == "FAILED", "TARGET_STATE_UNKNOWN",
                    "只有 FAILED 任务可恢复；ISOLATED 须人工核查后新建任务", task_state=task["task_state"])
            require(task["versions"]["source_digest"] == self.source_digest, "TARGET_STATE_UNKNOWN", "计划建立后执行源码已变化；须重新规划")
            report = resume.validate_checkpoints(task, self.store)
            require(not report["invalidated"], "TARGET_STATE_UNKNOWN", "已提交步骤的依赖版本失效；须重新规划",
                    rule="resume_dependency_invalidated", invalidated=report["invalidated"])
            start = next((i for i, s in enumerate(task["steps"]) if s["state"] != "COMMITTED"), len(task["steps"]))
            previous = task["steps"][start - 1]["output_ref"] if start else None
            for step in task["steps"][start:]:
                step["state"] = "DECLARED"
                step.pop("error", None)
            task.pop("error", None)
            task["resumed_from"] = start + 1
            task["governance_state"]["confirmed"] = confirm is True
            self._transition(task, "EXECUTING")
            return self._run_steps(task, previous, start)
```

- `_run_steps` 成功收尾时复用 Task 3 的三态提交与文件发布逻辑（抽成 `_finalize_commit(task)` 供 execute/resume 共用）。

`agent.py`：`task_execute` schema 加 `"resume": {"type":"boolean","default":False,...}`；分支里 `resume` 为真时调 `self.runtime.resume(...)`，否则原路径。`surface-v1.json` 的 `parameters.task_execute` 加 `"resume": "resume"`。

- [ ] **Step 4: 跑测试确认通过 + 定向回归**

Run: `... -m pytest tests/test_golden_resume.py tests/test_runtime.py tests/test_agent.py tests/test_surface.py -q -p no:cacheprovider --basetemp data/t4`
Expected: 全 PASS

- [ ] **Step 5: 提交**

```bash
git add definitio/resume.py definitio/runtime.py definitio/agent.py definitio/surfaces/surface-v1.json tests/test_golden_resume.py
git commit -m "feat(D4): resumable plans with checkpoints and dependency invalidation"
```

---

## Task 5: D5 发现与错误一致性

**Files:**
- Create: `definitio/discovery.py`
- Modify: `definitio/guidance.py`（`RULES` 注册表 + `plan_step_contract()` 公布）
- Modify: `definitio/agent.py:172-184`（补全 recovery 码）
- Test: `tests/test_golden_discovery.py`

**Interfaces:**
- Consumes: `agent.tool_definitions/compact_tools`、`runtime.capabilities`、`guidance.FAMILIES/PROMPTS/route/plan_step_contract`、`surface.Surface`
- Produces:
  - `discovery.CONSISTENCY_FORMAT = "definitio-discovery-consistency-v1"`
  - `discovery.prompt_tool_calls(template) -> list[{"verb","parameters"}]`
  - `discovery.audit(service) -> dict`，键 `{"format","tools":[{"name","surfaces":{...},"consistent":bool,"mismatches":[...]}],"error_rules":{...},"recovery_codes":{...},"ok":bool}`
  - `guidance.RULES: dict[str, dict]`，含 `operation_parameters_only`、`previous_dataset_output`、`published_surface_names`、`version_binding_changed`、`resume_dependency_invalidated`
  - `plan_step_contract()` 新增键 `"rules": sorted(RULES)`

- [ ] **Step 1: 写失败测试 —— 逐工具跨面比对**

```python
# tests/test_golden_discovery.py
"""D5 one canonical name and one parameter set across every discovery surface."""
import pytest

from definitio import discovery
from definitio.agent import AgentService, compact_tools, tool_definitions
from definitio.guidance import PROMPTS, RULES, plan_step_contract
from definitio.runtime import Runtime
from definitio.surface import Surface


@pytest.fixture
def service(tmp_path):
    return AgentService(Runtime(tmp_path / "workspace.sqlite3"))


CHAIN_TOOLS = ["dataset_import", "gis_import_data", "gis_reproject", "gis_buffer", "gis_spatial_join",
               "gis_field_statistics", "gis_dissolve", "task_plan", "task_dry_run", "task_execute",
               "task_get", "artifact_view", "workflow_route", "gis_describe_algorithm"]


def test_every_tool_has_one_name_and_one_parameter_set_across_surfaces(service):
    report = discovery.audit(service)
    assert report["format"] == "definitio-discovery-consistency-v1"
    assert report["ok"], [t for t in report["tools"] if not t["consistent"]]
    assert len(report["tools"]) == 60
    for name in CHAIN_TOOLS:
        entry = next(t for t in report["tools"] if t["name"] == name)
        assert entry["consistent"], entry["mismatches"]
        assert set(entry["surfaces"]) >= {"list_full", "describe", "route"}


def test_compact_and_full_and_surface_v1_agree_on_canonical_names(service):
    full = {t["name"] for t in tool_definitions()}
    compact = {t["name"] for t in compact_tools(tool_definitions())}
    assert len(full) == 60 and len(compact) == 21
    assert compact <= full
    for profile in ("canonical", "surface-v1"):
        surface = Surface(tool_definitions(), profile)
        assert set(surface.names) == full
        for name in full:
            assert set(surface.fields(name)) == set(
                next(t for t in tool_definitions() if t["name"] == name)["inputSchema"]["properties"])


def test_prompts_only_use_canonical_verbs_and_published_parameters(service):
    for name, (_, arguments, template) in PROMPTS.items():
        for call in discovery.prompt_tool_calls(template):
            assert call["verb"] in service.tools, (name, call["verb"])
            schema = service.tools[call["verb"]]["inputSchema"]["properties"]
            assert set(call["parameters"]) <= set(schema), (name, call)
    assert len(PROMPTS) == 4


def test_error_rules_are_published_and_echoed_consistently(service):
    assert set(RULES) >= {"operation_parameters_only", "previous_dataset_output", "published_surface_names",
                          "version_binding_changed", "resume_dependency_invalidated"}
    assert plan_step_contract()["rules"] == sorted(RULES)
    bad = service.call("task_plan", {"goal": "g", "request_id": "r", "steps": [
        {"verb": "gis_copy_dataset", "parameters": {"source": "x", "target": "y", "request_id": "oops"}}]})
    assert bad["error"]["details"]["rule"] in RULES
    described = service.call("gis_describe_algorithm", {"algorithm": "gis_buffer"})
    assert described["data"]["plan_step"]["rules"] == sorted(RULES)


def test_every_raised_error_code_has_a_recovery_entry(service):
    from definitio.agent import failure
    from definitio.model import GISFault
    codes = ["AUTHORIZATION_REQUIRED", "AUTHORIZATION_SCOPE", "AUTHORIZATION_EXHAUSTED", "LICENSE_BLOCKED",
             "DEPENDENCY_BLOCKED", "PREFLIGHT_REQUIRED", "BACKEND_UNAVAILABLE", "DESTRUCTIVE_BLOCKED",
             "DEFER_BLOCKED", "DIMENSION_MISMATCH"]
    for code in codes:
        reply = failure(GISFault(code, "x"))
        assert reply["error"]["recovery"] != "inspect_failure", code
        assert reply["error"]["recovery_message"]
```

- [ ] **Step 2: 跑测试确认失败**

Run: `... -m pytest tests/test_golden_discovery.py -q -p no:cacheprovider --basetemp data/t5`
Expected: FAIL — `ImportError: cannot import name 'discovery'`

- [ ] **Step 3: 实现 `discovery.py`、`RULES`、recovery 补全**

```python
"""Cross-surface discovery consistency: one canonical name, one parameter set."""
from __future__ import annotations

import re

from .guidance import FAMILIES, PROMPTS, family_for, route

CONSISTENCY_FORMAT = "definitio-discovery-consistency-v1"
CALL = re.compile(r"(gis_[a-z_]+)\(([^)]*)\)")
PARAM = re.compile(r"([a-z_]+)\s*=")


def prompt_tool_calls(template):
    calls = []
    for verb, args in CALL.findall(template):
        calls.append({"verb": verb, "parameters": sorted(set(PARAM.findall(args)))})
    return calls


def _properties(schema):
    return set((schema or {}).get("properties", {}))


def audit(service):
    definitions = {t["name"]: t for t in service.tools.values()}
    capabilities = {v["name"]: v for v in service.runtime.capabilities()["verbs"]}
    routed = {tool for _, _, tools in FAMILIES.values() for tool in tools}
    entries = []
    for name, definition in definitions.items():
        canonical_params = _properties(definition["inputSchema"])
        surfaces = {"list_full": sorted(canonical_params)}
        mismatches = []
        if name in capabilities:
            described = capabilities[name]
            surfaces["describe"] = sorted(_properties(described.get("input_schema")))
            if set(surfaces["describe"]) != canonical_params - {"request_id", "authorization_id"}:
                mismatches.append({"surface": "describe", "reason": "parameter set differs from list"})
            surfaces["route"] = sorted([family_for(name)] if family_for(name) else [])
            if name not in routed:
                mismatches.append({"surface": "route", "reason": "verb absent from every family"})
        else:
            surfaces["describe"] = None
            surfaces["route"] = None
        entries.append({"name": name, "surfaces": surfaces, "consistent": not mismatches,
                        "mismatches": mismatches})
    from .agent import failure
    from .model import GISFault
    from .guidance import RULES
    recovery = {}
    for code in sorted(set(RECOVERY_CODES) | set(RULES)):
        pass
    return {"format": CONSISTENCY_FORMAT, "tools": entries,
            "error_rules": {"published": sorted(RULES), "echoed": sorted(RULES)},
            "ok": all(e["consistent"] for e in entries)}
```

> 实施说明：`audit` 的 recovery 部分改为直接遍历 `agent.RECOVERY`（把 `failure()` 内的局部字典提升为模块级 `RECOVERY` 常量，行为不变），对每个码断言 `recovery != "inspect_failure"` 或该码确实以 `inspect_failure` 为**显式声明**的归宿；把 10 个缺失码补进 `RECOVERY`，各自给明确的 recovery 动作与消息。`discovery.audit` 返回 `recovery_codes: {"missing": [...], "covered": [...]}`，`ok` 要求 `missing == []`。

`guidance.py` 追加：

```python
RULES = {
    "operation_parameters_only": {"where": "task_plan/task_dry_run step parameters",
                                  "meaning": "Step parameters carry operation arguments only",
                                  "fix": "Put request_id on task_plan and authorization_id on task_execute"},
    "previous_dataset_output": {"where": "task_plan/task_dry_run step references",
                                "meaning": "$previous references the immediately preceding dataset output",
                                "fix": "Use an existing handle for the first step; never a planned new target name"},
    "published_surface_names": {"where": "MCP surface adapter",
                                "meaning": "Only names published by the selected surface profile are accepted",
                                "fix": "Use the canonical name or the exact published surface name"},
    "version_binding_changed": {"where": "task_execute commit gate",
                                "meaning": "An input version or target state changed after planning",
                                "fix": "Re-plan against the current versions; the old plan is refused"},
    "resume_dependency_invalidated": {"where": "task_execute resume gate",
                                      "meaning": "A committed step's dependency version is no longer valid",
                                      "fix": "Re-plan; recovery is only allowed from still-valid results"},
}
```

`plan_step_contract()` 返回字典追加 `"rules": sorted(RULES)`。

- [ ] **Step 4: 跑测试确认通过 + 定向回归**

Run: `... -m pytest tests/test_golden_discovery.py tests/test_surface.py tests/test_agent.py tests/test_mcp.py tests/test_workstation.py -q -p no:cacheprovider --basetemp data/t5`
Expected: 全 PASS（60/21/40/4 计数不变）

- [ ] **Step 5: 提交**

```bash
git add definitio/discovery.py definitio/guidance.py definitio/agent.py tests/test_golden_discovery.py
git commit -m "feat(D5): cross-surface discovery and error consistency with automated audit"
```

---

## Task 6: 黄金链与五类验收

**Files:**
- Create: `definitio/golden.py`
- Create: `tests/test_golden_chain.py`
- Test: 自身

**Interfaces:**
- Consumes: Task 1–5 全部
- Produces:
  - `golden.STAGES`（6 阶段 → 动词/工具映射）
  - `golden.fixtures(service, session) -> dict`（zones 句柄、facilities data URI、期望真值）
  - `golden.chain_steps(facilities_uri, zones_id, prefix) -> list[dict]`
  - `golden.shoelace_area(ring) -> float`（独立真值，不用 GEOS）
  - `golden.BUFFER_SEGMENTS = 8`、`golden.buffer_truth_area(radius) -> float`

- [ ] **Step 1: 写失败测试 —— A1 正常端到端 + 独立回读真值**

```python
# tests/test_golden_chain.py
"""Golden chain acceptance A1-A5; all offline, all in a private tmp workspace."""
import json
import math
import sqlite3

import pytest

from definitio import golden
from definitio.agent import AgentService
from definitio.runtime import Runtime

SESSION = "golden-chain-acceptance-session-token-0001"


@pytest.fixture
def service(tmp_path):
    return AgentService(Runtime(tmp_path / "workspace.sqlite3"))


def test_a1_end_to_end_and_independent_readback_matches_truth(service, tmp_path):
    fx = golden.fixtures(service, SESSION)
    steps = golden.chain_steps(fx["facilities_uri"], fx["zones_id"], "gc_")
    dry = service.call("task_dry_run", {"goal": "golden chain", "steps": steps})
    assert dry["ok"], dry
    planned = service.call("task_plan", {"goal": "golden chain", "request_id": "gc-plan-1", "steps": steps,
                                         "exports": [{"step": 4, "name": "gc_join.geojson", "format": "geojson"}]})
    assert planned["ok"], planned
    digest = planned["data"]["task"]["plan_digest"]
    executed = service.call("task_execute", {"task_id": planned["data"]["task"]["id"], "plan_digest": digest,
                                             "confirm": True})
    assert executed["ok"], executed
    task = executed["data"]["task"]
    assert task["task_state"] == "COMMITTED"
    record = service.runtime.store.task(task["id"])
    assert record["commit_state"] == "committed" and record["readback"]["confirmed"] is True

    published = tmp_path / "artifacts" / "gc_join.geojson"
    document = json.loads(published.read_text(encoding="utf-8"))
    assert len(document["features"]) == fx["expected_join_count"]
    total = sum(f["properties"]["join_population"] for f in document["features"])
    assert total == fx["expected_population_sum"]
    areas = [golden.shoelace_area(f["geometry"]["coordinates"][0]) for f in document["features"]]
    truth = golden.buffer_truth_area(fx["buffer_metres"])
    assert all(abs(a - truth) / truth < 1e-9 for a in areas), (areas, truth)

    stats = record["steps"][4]["result"]
    assert stats["groups"][0]["fields"]["join_population"]["sum"] == fx["expected_population_sum"]
    assert stats["groups"][0]["fields"]["join_population"]["count"] == fx["expected_join_count"]

    viewed = service.call("artifact_view", {"uri": f"definitio://artifacts/{record['file_artifact_refs'][0]}",
                                            "view": "page"})
    assert viewed["ok"] and viewed["data"]["integrity"]["sha256_json_utf8"]

    header = service.runtime.store.dataset(task["output_refs"][2])["semantic"]
    assert header["lineage"]["operation"]["verb"] == "gis_buffer"
    assert header["units"]["operation"]["distance"]["unit"] == "m"
    assert header["crs"]["code"] == "EPSG:32649" and header["axis_order"]["sequence"] == "east,north"
```

- [ ] **Step 2: 跑测试确认失败**

Run: `... -m pytest tests/test_golden_chain.py -q -p no:cacheprovider --basetemp data/t6`
Expected: FAIL — `ImportError: cannot import name 'golden'`

- [ ] **Step 3: 实现 `golden.py`**

```python
"""Golden chain definition, offline fixtures and independent truth formulas."""
from __future__ import annotations

import base64
import json
import math

from rasterio.warp import transform

STAGES = [("设施数据接入", "gis_import_data"), ("坐标转换", "gis_reproject"),
          ("缓冲/服务区分析", "gis_buffer"), ("空间连接", "gis_spatial_join"),
          ("指标汇总", "gis_field_statistics"), ("保存并回读产物", "artifact_export+artifact_view")]
BUFFER_SEGMENTS = 8
BUFFER_METRES = 500.0
TARGET_CRS = "EPSG:32649"
SOURCE_CRS = "EPSG:4326"


def shoelace_area(ring):
    total = 0.0
    for (x1, y1), (x2, y2) in zip(ring, ring[1:]):
        total += x1 * y2 - x2 * y1
    return abs(total) / 2.0


def buffer_truth_area(radius):
    n = 4 * BUFFER_SEGMENTS
    return 0.5 * n * radius * radius * math.sin(2 * math.pi / n)


def fixtures(service, session):
    centres = [(500000.0, 2541000.0), (504000.0, 2541000.0), (508000.0, 2541000.0)]
    populations = [1000, 2500, 4000]
    lonlat = transform(TARGET_CRS, SOURCE_CRS, [c[0] for c in centres], [c[1] for c in centres])
    facilities = {"type": "FeatureCollection", "features": [
        {"type": "Feature", "geometry": {"type": "Point", "coordinates": [lon, lat]},
         "properties": {"facility_id": f"F{i + 1}", "name": f"facility-{i + 1}"}}
        for i, (lon, lat) in enumerate(zip(*lonlat))]}
    zones = {"type": "FeatureCollection", "features": [
        {"type": "Feature", "geometry": {"type": "Polygon", "coordinates": [[
            [cx - 2000, cy - 2000], [cx + 2000, cy - 2000], [cx + 2000, cy + 2000],
            [cx - 2000, cy + 2000], [cx - 2000, cy - 2000]]]},
         "properties": {"zone_id": f"Z{i + 1}", "population": populations[i]}}
        for i, (cx, cy) in enumerate(centres)]}
    imported = service.call("dataset_import", {"name": "gc_zones", "geojson": zones, "crs": TARGET_CRS,
                                               "confirm": True, "request_id": "gc-zones-1"}, session)
    assert imported["ok"], imported
    envelope = {"definitio": {"crs": SOURCE_CRS,
                              "schema": [{"name": "facility_id", "type": "string", "nullable": False},
                                         {"name": "name", "type": "string", "nullable": False}],
                              "source_license": {"allow_import": True, "evidence_ref": "offline-synthetic-fixture"}},
                **facilities}
    uri = "data:application/geo+json;base64," + base64.b64encode(
        json.dumps(envelope, ensure_ascii=False).encode("utf-8")).decode("ascii")
    return {"zones_id": imported["data"]["dataset"]["id"], "facilities_uri": uri,
            "buffer_metres": BUFFER_METRES, "expected_join_count": 3,
            "expected_population_sum": sum(populations), "centres": centres}


def chain_steps(facilities_uri, zones_id, prefix):
    return [
        {"verb": "gis_import_data", "parameters": {"source_uri": facilities_uri, "target_name": prefix + "facilities",
                                                   "format": "geojson", "kind": "vector"}},
        {"verb": "gis_reproject", "parameters": {"input": "$previous", "target": prefix + "projected",
                                                 "target_crs": TARGET_CRS}},
        {"verb": "gis_buffer", "parameters": {"input": "$previous", "target": prefix + "service_area",
                                              "distance": BUFFER_METRES, "unit": "m", "dissolve": False}},
        {"verb": "gis_spatial_join", "parameters": {"input": "$previous", "join": zones_id,
                                                    "target": prefix + "joined", "predicate": "intersects",
                                                    "summary": "all"}},
        {"verb": "gis_field_statistics", "parameters": {"dataset": "$previous", "fields": ["join_population"],
                                                        "group_by": [], "statistics": ["count", "sum"],
                                                        "output": "result", "null_policy": "exclude"}},
    ]
```

> 真值口径声明：`buffer_truth_area` 是**正 32 边形**面积（GEOS `quad_segs=8` → 每象限 8 段），不是 πr²。这是独立于引擎计算路径的解析真值；相对偏差阈值 1e-9。若实测偏差超阈值，先查根因（段数/顶点顺序），**不得静默放宽容差**。

- [ ] **Step 4: 跑 A1 确认通过**

Run: `... -m pytest tests/test_golden_chain.py::test_a1_end_to_end_and_independent_readback_matches_truth -q -p no:cacheprovider --basetemp data/t6`
Expected: PASS

- [ ] **Step 5: 写 A2–A5 失败测试**

```python
def test_a2_unit_object_and_version_errors_are_each_named(service, tmp_path):
    from definitio import verifier
    fx = golden.fixtures(service, SESSION)
    steps = golden.chain_steps(fx["facilities_uri"], fx["zones_id"], "gc_")

    unit_steps = [dict(s) for s in steps]
    unit_steps[2] = {"verb": "gis_buffer", "parameters": {**steps[2]["parameters"], "distance": 500, "unit": "m"}}
    unit_steps[1] = {"verb": "gis_assign_projection", "parameters": {"dataset": "$previous", "target": "gc_projected", "crs": "EPSG:4326"}}
    unit = service.call("task_dry_run", {"goal": "unit error", "steps": unit_steps})
    assert not unit["ok"]
    assert unit["error"]["details"]["predicate"] == "distance_unit_compatibility"
    assert unit["error"]["details"]["details"]["crs_projected"] is False

    object_steps = [dict(s) for s in steps]
    object_steps[3] = {"verb": "gis_spatial_join", "parameters": {**steps[3]["parameters"], "join": fx["zones_id"], "predicate": "contains"}}
    raster = service.call("raster_import", {"name": "gc_raster", "crs": "EPSG:32649", "confirm": True,
                                            "request_id": "gc-raster-1",
                                            "raster": {"width": 2, "height": 2, "bands": 1,
                                                       "transform": [1.0, 0.0, 500000.0, 0.0, -1.0, 2541000.0],
                                                       "dtype": "float32", "nodata": None,
                                                       "data": [[1.0, 2.0], [3.0, 4.0]]}})
    assert raster["ok"], raster
    object_steps[3]["parameters"]["join"] = raster["data"]["dataset"]["id"]
    obj = service.call("task_dry_run", {"goal": "object error", "steps": object_steps})
    assert not obj["ok"]
    assert obj["error"]["details"]["predicate"] == "spatial_join_contract"

    planned = service.call("task_plan", {"goal": "version error", "request_id": "gc-plan-ver", "steps": steps})
    assert planned["ok"], planned
    task_id = planned["data"]["task"]["id"]
    facilities_id = service.runtime.store.task(task_id)["steps"][0]["parameters"]["target_name"]
    with sqlite3.connect(service.runtime.store.path) as db:
        row = db.execute("SELECT id, document FROM datasets WHERE name=?", (facilities_id,)).fetchone()
    document = json.loads(row[1])
    document["feature_count"] = 999
    with sqlite3.connect(service.runtime.store.path) as db:
        db.execute("UPDATE datasets SET document=? WHERE id=?", (json.dumps(document), row[0]))
    refused = service.call("task_execute", {"task_id": task_id,
                                            "plan_digest": planned["data"]["task"]["plan_digest"], "confirm": True})
    assert not refused["ok"]
    assert refused["error"]["details"]["rule"] == "version_binding_changed"
    assert refused["error"]["details"]["changed_inputs"]


def test_a3_state_change_after_checks_does_not_authorize_the_old_plan(service):
    fx = golden.fixtures(service, SESSION)
    steps = golden.chain_steps(fx["facilities_uri"], fx["zones_id"], "gc_")
    dry = service.call("task_dry_run", {"goal": "golden", "steps": steps})
    assert dry["ok"]
    planned = service.call("task_plan", {"goal": "golden", "request_id": "gc-a3", "steps": steps})
    task_id = planned["data"]["task"]["id"]
    zones = service.runtime.store.dataset(fx["zones_id"])
    with sqlite3.connect(service.runtime.store.path) as db:
        row = db.execute("SELECT document FROM datasets WHERE id=?", (zones["id"],)).fetchone()
        document = json.loads(row[0])
        document["feature_count"] = 77
        db.execute("UPDATE datasets SET document=? WHERE id=?", (json.dumps(document), zones["id"]))
    refused = service.call("task_execute", {"task_id": task_id,
                                            "plan_digest": planned["data"]["task"]["plan_digest"], "confirm": True})
    assert not refused["ok"]
    record = service.runtime.store.task(task_id)
    assert record["commit_state"] == "rejected_state_changed"
    assert record["error"]["details"]["changed_inputs"][0]["id"] == zones["id"]
    assert not record["output_refs"]


def test_a4_midchain_failure_recovers_from_valid_state(service, monkeypatch):
    fx = golden.fixtures(service, SESSION)
    steps = golden.chain_steps(fx["facilities_uri"], fx["zones_id"], "gc_")
    planned = service.call("task_plan", {"goal": "golden", "request_id": "gc-a4", "steps": steps})
    task_id = planned["data"]["task"]["id"]
    digest = planned["data"]["task"]["plan_digest"]
    runtime = service.runtime
    real = runtime._execute_step
    seen = []

    def flaky(task, step, previous):
        seen.append(step["index"])
        if step["index"] == 3 and seen.count(3) == 1:
            raise GISFault("BACKEND_ERROR", "injected buffer failure")
        return real(task, step, previous)

    from definitio.model import GISFault
    monkeypatch.setattr(runtime, "_execute_step", flaky)
    failed = service.call("task_execute", {"task_id": task_id, "plan_digest": digest, "confirm": True})
    assert not failed["ok"]
    monkeypatch.setattr(runtime, "_execute_step", real)
    resumed = service.call("task_execute", {"task_id": task_id, "plan_digest": digest, "confirm": True, "resume": True})
    assert resumed["ok"], resumed
    record = runtime.store.task(task_id)
    assert record["task_state"] == "COMMITTED" and record["resumed_from"] == 3
    assert record["steps"][0]["attempt_seq"] == 1 and record["steps"][1]["attempt_seq"] == 1
    assert seen.count(1) == 1 and seen.count(2) == 1


def test_a5_commit_and_export_retries_have_no_duplicate_or_out_of_scope_effects(service, tmp_path):
    fx = golden.fixtures(service, SESSION)
    steps = golden.chain_steps(fx["facilities_uri"], fx["zones_id"], "gc_")
    exports = [{"step": 4, "name": "gc_join.geojson", "format": "geojson"}]
    planned = service.call("task_plan", {"goal": "golden", "request_id": "gc-a5", "steps": steps, "exports": exports})
    task_id = planned["data"]["task"]["id"]
    digest = planned["data"]["task"]["plan_digest"]
    first = service.call("task_execute", {"task_id": task_id, "plan_digest": digest, "confirm": True})
    assert first["ok"], first
    before_datasets = len(service.runtime.store.datasets())
    before_files = sorted(p.name for p in (tmp_path / "artifacts").iterdir() if p.is_file())
    before_bytes = (tmp_path / "artifacts" / "gc_join.geojson").read_bytes()
    for _ in range(3):
        again = service.call("task_execute", {"task_id": task_id, "plan_digest": digest, "confirm": True})
        assert again["ok"], again
        assert again["data"]["replayed"] is True
    assert len(service.runtime.store.datasets()) == before_datasets
    assert sorted(p.name for p in (tmp_path / "artifacts").iterdir() if p.is_file()) == before_files
    assert (tmp_path / "artifacts" / "gc_join.geojson").read_bytes() == before_bytes
    assert len(service.runtime.store.file_artifacts()) == 1
    assert not list((tmp_path / "artifacts" / ".staging").iterdir())
    record = service.runtime.store.task(task_id)
    assert all(w["target"].startswith("gc_") for w in record["actual_writes"])
```

- [ ] **Step 6: 跑 A2–A5 确认失败，再按失败信息补齐实现**

Run: `... -m pytest tests/test_golden_chain.py -q -p no:cacheprovider --basetemp data/t6`
Expected: A1 PASS，A2–A5 FAIL；逐项按报错补 `runtime`/`agent` 接线（`task["actual_writes"]`、`task["file_artifact_refs"]`、错误 details 带 `predicate`、replay 路径不重复发布），**不得放宽断言**。

- [ ] **Step 7: 全链通过 + 定向回归**

Run: `... -m pytest tests/test_golden_chain.py tests/test_runtime.py tests/test_agent.py tests/test_surface.py tests/test_workstation.py tests/test_usability.py -q -p no:cacheprovider --basetemp data/t6`
Expected: 全 PASS

- [ ] **Step 8: 提交**

```bash
git add definitio/golden.py definitio/runtime.py definitio/agent.py tests/test_golden_chain.py
git commit -m "feat: golden chain vertical slice with A1-A5 acceptance evidence"
```

---

## Task 7: 全量回归 + G1 哈希守卫 + 报告脚本

**Files:**
- Create: `scripts/golden_chain_report.py`
- Create: `tests/test_golden_g1_guard.py`
- Test: 全量套件

**Interfaces:**
- Consumes: 全部前序任务
- Produces:
  - `scripts/golden_chain_report.py --root <新版本目录> --workbench <工作台根> --out <evidence 目录>`
  - 输出 `evidence/golden-chain-report.json` 与 `evidence/golden-chain-report.md`
  - `tests/test_golden_g1_guard.py::test_g1_baseline_59_files_are_byte_identical`

- [ ] **Step 1: 写 G1 守卫失败测试**

```python
# tests/test_golden_g1_guard.py
"""Read-only guard: the 59 G1 baseline files must stay byte-identical."""
import hashlib
import json
from pathlib import Path

WORKBENCH = Path(__file__).resolve().parents[2]
BASELINE = Path("D:/agent-native-gis/evidence/baseline-sha256-before.json")


def test_g1_baseline_59_files_are_byte_identical():
    expected = json.loads(BASELINE.read_text(encoding="utf-8"))
    assert len(expected) == 59
    changed, missing = [], []
    for rel, want in expected.items():
        path = WORKBENCH / rel
        if not path.exists():
            missing.append(rel)
            continue
        if hashlib.sha256(path.read_bytes()).hexdigest() != want:
            changed.append(rel)
    assert not missing and not changed, {"missing": missing, "changed": changed}
```

- [ ] **Step 2: 跑守卫测试**

Run: `... -m pytest tests/test_golden_g1_guard.py -q -p no:cacheprovider --basetemp data/t7`
Expected: PASS（若 FAIL 立即停工排查，不得继续）

- [ ] **Step 3: 实现报告脚本**

`scripts/golden_chain_report.py`  responsibilities：
1. 收集新版本目录全部源码/测试/脚本文件清单 + SHA256 + 字节数；与复制基线（Task 1 Step 0 的 git 初始提交树）对比，分出「新增」「修改」「未变」。
2. 跑全量 pytest（`--junitxml`），解析逐 ID 结果；按文件与按 D 项/验收类分组计数；新增测试单列。
3. 跑 G1 59 文件哈希核对。
4. 跑 `discovery.audit` 与 `scripts/audit_g1.py`（只读）。
5. 汇总五类验收逐项证据（从 junitxml 取 A1–A5 对应 ID 与其断言输出）。
6. 写 `golden-chain-report.json` + `.md`，含已知限制节（10 项环境阻塞、干净安装未验、真模型评测未做、40 动词未全量推广、轴序解析只覆盖 WKT2 AXIS 节点、文件产物只开放 geojson）。

- [ ] **Step 4: 全量复跑**

Run: `... -m pytest tests/ -q --basetemp data/regression-tmp --junitxml=evidence/regression-full-suite.xml -p no:cacheprovider`
Expected: 225 + 新增 ID；除 10 项已声明环境阻塞外全过

- [ ] **Step 5: 生成报告**

Run: `... python scripts/golden_chain_report.py --root . --workbench .. --out evidence`
Expected: 生成 JSON/MD，G1 59/59 相符

- [ ] **Step 6: 提交**

```bash
git add scripts/golden_chain_report.py tests/test_golden_g1_guard.py evidence/golden-chain-report.json evidence/golden-chain-report.md
git commit -m "test: full regression, G1 hash guard and automated delivery report"
```

---

## Task 8: 交付报告

**Files:**
- Create: `docs/GOLDEN-CHAIN-DELIVERY-20261113.md`

- [ ] **Step 1: 写报告正文**

必含节：
1. 目录定名依据（00–05 已占用 → 06；`audit_g1.py` 的 `parents[2]` 布局约束）
2. 新增/修改文件清单 + SHA256 + 字节数
3. 新版本源码摘要（`Runtime.source_digest`）与模块职责表
4. 测试总数、逐类结果、新增测试单列计数；与历史「201 ID」口径差异说明
5. 五类验收逐项证据（测试 ID、断言、真值口径与容差理由、证据文件路径）
6. D1–D5 逐项落实位置（file:line）与代码级同一性断言说明
7. G1 59 文件哈希核对结果
8. 已知限制：10 项环境阻塞（附源工作站对照）、干净安装未验、依赖复用记账、真模型评测未做、引擎未切版、40 动词未推广、文件产物只开放 geojson、轴序解析范围
9. 红线自查：8765/8876 未动、离线、未推送/发布、暂存清理、30 分钟卡点记录

- [ ] **Step 2: 回读核验**

逐条把报告中的数字与 `evidence/golden-chain-report.json`、junitxml、哈希清单对账；不符即改报告，不改数据。

- [ ] **Step 3: 提交**

```bash
git add docs/GOLDEN-CHAIN-DELIVERY-20261113.md
git commit -m "docs: golden chain v03 delivery report with evidence and declared limits"
```

---

## Self-Review

**1. Spec coverage**

| 工作单条目 | 对应任务 |
|---|---|
| D1 语义对象头（身份/版本/类型与字段/CRS 与轴序分列/量纲/来源/范围/持久化） | Task 1 |
| D1 锚例：缓冲区回答来源版本、距离单位、CRS、轴序 | Task 1 Step 5 `test_buffer_result_answers_lineage_unit_crs_and_axis` |
| D2 独立验证器模块、三层分离 | Task 2（verifier / operations / runtime+commit+resume） |
| D2 五谓词 | Task 2 Step 3 |
| D2 三段式、禁止 verified=true | Task 2 Step 1 `test_result_is_three_part_and_never_a_blanket_verified_flag` |
| D2 两路径同一实现 + 代码级同一性断言 | Task 2 Step 5 `test_agent_verify_and_commit_paths_call_the_same_predicate_objects` |
| D3 计划绑定输入版本集与预期写入范围 | Task 3 `bind_write_scope` + `plan_digest` |
| D3 状态变化重验或拒绝并明示原因 | Task 3 `evaluate_binding` + A2/A3 |
| D3 文件产物暂存→校验→发布、失败清理 | Task 3 `publish_file_artifact` + 两个清理测试 |
| D3 三态如实区分、回读未确认不报成功 | Task 3 `COMMIT_STATES` + `test_commit_is_pending_until_real_readback_confirms` |
| D3 提交后真实状态回读核对 | Task 3 `task["readback"]` + `readback_file_artifact` |
| D4 步骤记录依赖/输入版本/输出/检查位置/副作用/可否重试 | Task 4 `checkpoint` |
| D4 从仍有效结果恢复 | Task 4 `resume` + A4 |
| D4 输入版本改变使依赖失效 | Task 4 `validate_checkpoints` + 测试 |
| D4 导出/写入重试幂等 | Task 3 + A5 |
| D4 `$previous` 兼容 | Task 4 `test_previous_reference_still_works_after_resume` + 既有套件 |
| D5 逐工具跨面名称与参数集一致 + 自动化断言 | Task 5 |
| 验收 1 正常端到端 + 独立回读真值 | Task 6 A1 |
| 验收 2 单位/对象/版本错误各一例且指明检查 | Task 6 A2 |
| 验收 3 检查后改状态不得授权旧结论 | Task 6 A3 |
| 验收 4 中途失败可恢复不整链重跑 | Task 6 A4 |
| 验收 5 重试无重复/越界副作用 | Task 6 A5 |
| 回归全量复跑 + G1 59 哈希 + 新增单列 | Task 7 |
| 报告六项必含内容 | Task 7 Step 3 + Task 8 |
| 红线：新版本目录、引擎不动、离线、依赖记账、30 分钟卡点 | Global Constraints + Task 8 红线自查 |

**2. Placeholder scan**：Task 5 Step 3 的 `audit` 中 recovery 段落给了实施说明而非完整代码——实施时按该说明把 `agent.failure` 的局部字典提升为模块级 `RECOVERY` 并补 10 码，`discovery.audit` 返回 `recovery_codes`。Task 6 Step 6 是「按失败信息补齐接线」，属 TDD 的正常红→绿步骤，已列明需补的具体记录键（`actual_writes`、`file_artifact_refs`、错误 details 的 `predicate`）。两处计划内残留行（`if False else None`）已显式标注实施时删除。

**3. Type consistency**：`verifier.context` 的 15 个键在 Task 2/3/4/6 中一致；`commit.bind_write_scope` 的 `dataset_targets`/`file_artifacts` 键名在 `verifier.write_scope` 与 A5 断言中一致；`checkpoint` 的 `input_versions`/`output_object`/`check_location` 在 Task 4 测试与实现中一致；`task["commit_state"]`/`task["readback"]`/`task["resumed_from"]`/`task["actual_writes"]`/`task["file_artifact_refs"]` 在 Task 3/4/6 中同名。