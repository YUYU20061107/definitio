# 从零干净安装说明（Definitio v05 native-push）

日期：2026-11-14。适用：`08-runtime-v05-native-push`。
本说明是**从零写**的：不引用 07 树的 `.venv`、不复用 `D:/agent-native-gis/.venv`、
不假设任何隐含的工作台状态。目标机器只需 Python 与本项目源码。

认证维持 **NOT_CERTIFIED**。本说明不含任何模型调用、外部业务 API 调用或付费动作。

## 0. 红线约束（先读）

- 干净验证**不启动、不停止、不重配** 8876 在线引擎，也不写 `D:/agent-native-gis`。
  自检用全新临时目录 + 进程内 ASGI（FastAPI TestClient），不绑定端口。
- G1 基线 JSON 只**只读引用**；59 个冻结文件一字不改。
- 06 / 07 两树只读；所有新文件只在 08 树内（venv 与证据目录除外，见第 2 节）。

## 1. 前置

| 项 | 要求 |
|---|---|
| Python | 3.12（引擎在 3.12.9 验证）；Windows 下 `py -3.12` 可选 |
| 系统级 GIS | **核心不需要** GDAL/QGIS/PDAL 系统安装（wheel 自带 GEOS/PROJ/GDAL） |
| 网络 | 仅 `pip install` 访问公共包索引；无模型 API、无业务 API、无付费动作 |
| 目录 | 必须保持 `<workbench>/08-runtime-v05-native-push/` 布局（见第 4 节） |

## 2. 全新 venv（在 08 树外）

```bash
py -3.12 -m venv C:/Users/RAHUYU/WorkBuddy/2026-11-14-03-15-42/clean-venv-v05
CLEAN=C:/Users/RAHUYU/WorkBuddy/2026-11-14-03-15-42/clean-venv-v05/Scripts/python.exe
"$CLEAN" -m pip install -r requirements.txt
```

`requirements.txt` → `requirements-core.txt`（fastapi / uvicorn / shapely / rasterio /
httpx / mcp / jsonschema / numpy / affine）＋ `pytest==9.1.1` ＋ `defusedxml==0.7.1`。

**缺口 1（已修）**：`scripts/golden_chain_report.py` import `defusedxml`，
但 07 的 requirements 未声明。07 复用 `--system-site-packages` 的 venv 时被掩盖，
全新隔离 venv 会直接 ImportError。本轮静态扫描全部 `.py` 的第三方 import 后补上声明。

## 3. 启动与自检（干净环境）

```bash
cd <workbench>/08-runtime-v05-native-push
"$CLEAN" -X utf8 scripts/clean_install_selftest.py --out evidence/clean-install-selftest.json
"$CLEAN" -X utf8 scripts/environment_doctor.py --profile core --output evidence/clean-install-environment.json
```

自检覆盖：核心模块 import、隔离 Runtime 启动、60 工具 / 40 动词计数、真实写入链
（导入＋复制，断言 COMMITTED）、证据信封 disposition 与摘要重算、发现层审计、
进程内 HTTP `/api/agent/manifest`。**不占 8876、不写 D 盘。**

## 4. 隐含的工作台依赖（逐条显式化）

| # | 依赖 | 为什么必须 | 处理 |
|---|---|---|---|
| 2 | `data/` 目录存在 | `golden_chain_report.py::run_suite` 用 `--basetemp <root>/data/<label>-tmp`；07 首跑因缺父目录出现 249 ERROR | 新建树后 `mkdir data`；08 树已建 |
| 3 | `<workbench>/08-.../` 布局 | `scripts/audit_g1.py` 默认 `root=parents[2]`、`golden_chain_report.py --workbench` 默认 `ROOT.parent` | 保持编号目录布局；或显式传 `--workbench` |
| 4 | G1 基线 JSON | 报告脚本默认 `--g1-baseline D:/agent-native-gis/evidence/baseline-sha256-before.json` | 干净机器自备同格式 JSON 并显式 `--g1-baseline`；本机只读引用，不修改 |
| 5 | 4 个离线夹具 `evidence/a3-a6-20261113/g0/*.json` | `test_surface.py` 依赖 | 随源码保留；复制新树时必须带上 |
| 6 | `python -m pytest` 而非裸 `pytest` | `tests/` 内存在跨文件 import（`from test_agent import ...`），需 rootdir 在 sys.path | 所有命令统一用 `python -m pytest` |
| 7 | 可选原生后端 `osgeo` / `qgis` / `processing` / `psycopg2` | 仅 `scripts/*worker*.py` 与 `*_reference.py` 使用，经子进程隔离；核心包与全量测试不 import | **不列为干净安装必需**；本机未装，属既有 10 项环境阻塞，保留红灯 |
| 8 | `data/backends.json`（含凭据） | 10 项 `test_native_completion` 用例需要本地 PostGIS/GeoServer 与外部 istSOS | 按设计不随源码包；缺配置时在读取配置处即返回 `BACKEND_UNAVAILABLE`，**不发网络请求**，保留既有红灯 |

## 5. 黄金链报告在干净环境复跑

```bash
"$CLEAN" -X utf8 -m pytest tests/ -q -p no:cacheprovider \
  --basetemp data/clean-install-tmp --junitxml=evidence/clean-install-full-suite.xml
"$CLEAN" -X utf8 scripts/golden_chain_report.py --root . --workbench .. \
  --out evidence/clean-install --baseline-xml evidence/baseline-v05-full-suite.xml \
  --baseline-commit 4040567 --skip-tests
```

`--skip-tests` 只用于「报告生成链」的干净环境验证；**测试本身由上一行的全量 pytest 真实跑出**，
两份证据同列，不拿报告生成冒充测试执行。

## 6. 干净环境复跑证据

见 `evidence/clean-install-*.json` / `.xml` 与交付报告 T3 节；命令与输出摘要逐条登记。
装不起来的缺口若无法修复，按工作单红线 5 写清证据与卡点，**不许假报完成**。

## 7. 依赖分层（发布用）

- **core（pip 可装）**：`requirements.txt` 全部 11 项。
- **optional native backends（系统级，非 pip）**：GDAL/OGR Python bindings、PyQGIS、
  PDAL、本地 PostGIS/GeoServer/istSOS。仅扩展后端动词需要；核心与全量确定性测试不依赖。
- **never required**：真模型 API、任何外部业务 API、付费服务。
