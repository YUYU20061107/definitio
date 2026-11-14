# T4 性能基线与优化决策（v05）

日期：2026-11-14。机器：本机 Windows / CPython 3.12.9（隔离 venv）。
原始数据：`evidence/perf-baseline-v05.json`（由 `scripts/perf_baseline.py` 生成，可复跑）。

**口径声明**：全部数字是真实 `AgentService` 调用；未绑定端口、未调用模型 API、未访问外部业务 API、未付费。
无数字处不写「更快」。

## 1. 黄金链六步冷/热延迟（中位数，毫秒，3 次采样）

| 步骤 | 冷 | 热 |
|---|---|---|
| 1 dataset_import | 52.4 | 34.8 |
| 2 gis_copy_dataset | 351.8 | 295.8 |
| 3 gis_buffer | 352.6 | 316.1 |
| 4 gis_dissolve | 356.0 | 319.4 |
| 5 gis_measure | 201.0 | 181.3 |
| 6 gis_validate_geometry | 263.3 | 197.3 |

冷 = 全新 Runtime 首次执行该步（含冷启动）；热 = 同一 Runtime 预热一条链后重复执行。

## 2. 全量测试墙钟

JUnit `testsuite time`：**148.3 s**（372 项，基线 XML）。同一台机的干净 venv 复跑为 236.2 s，
差异来自冷文件缓存与 `PYTHONPATH` 注入差异，不作为优化前后对比基准。

## 3. 发现层输出体积

| 口径 | 字节 |
|---|---|
| full（60 工具） | 134,037 |
| compact（21 工具） | 36,223 |
| compact/full | 0.270 |
| 60 工具 inputSchema 合计 | 68,872（估 17,218 token，按 UTF-8 字节/4 粗估） |

token 数为**粗估**：本机无 tokenizer，只可用于同口径前后对比，不是真实 BPE 计数。

## 4. 头号热点（实测）

对单次 `gis_copy_dataset` 做 `cProfile`（0.252 s）：

| 项 | 累计 | 占比 |
|---|---|---|
| `Store.connect()`（38 次调用链） | 0.152 s | 60.3% |
| `sqlite3.Connection.commit`（18 次） | 0.048 s | 19.0% |
| `_sqlite3.connect`（19 次） | 0.031 s | 12.3% |
| 其余（JSON 编解码、shapely WKB、指纹） | 合计 < 0.02 s | — |

结论：热点不是几何计算，也不是 JSON 序列化，而是**每步 19 次 SQLite 连接建立与 18 次提交**。

## 5. 决策：本轮不交付优化（shipped: false）

唯一能实质改善热点的做法是**连接复用/池化**。实测证明它在 Windows 上不安全：

```text
常驻连接不关闭 → 删除同一 sqlite 文件：PermissionError [WinError 32] 另一个程序正在使用此文件
连接关闭后     → 删除成功
```

本工程每个测试用独立临时工作区，会话结束时删除；连接常驻会让临时目录清理失败。
这与本轮早前实际踩到的 328 个 `setup ERROR`（pytest 清理 basetemp 被拦）是同一失效模式，不是理论风险。

其余可行微优化（把 `save_task` 的 2 次独立连接并入已开事务）只影响 19 次连接中的 2 次，
低于本机采样噪声，**不构成可测收益，因此不做**，避免为名义优化引入事务边界变化。

保持现状的代价是明确的：写操作每步约 300 ms 中约 60% 花在连接生命周期上。
若后续要真正解决，方向是「每请求一个连接 + 显式 `Store.close()`」，并配套改造测试清理逻辑，
属独立工程，不在本单范围。

## 6. 复跑方式

```bash
python scripts/perf_baseline.py --baseline-xml evidence/baseline-v05-full-suite.xml \
  --out evidence/perf-baseline-v05.json --repeats 3
```
