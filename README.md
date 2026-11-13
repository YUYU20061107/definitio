# Definitio Agent GIS · 当前工作站

项目已从同日交付目录迁入 `D:/agent-native-gis`。当前使用独立工作区 `data/`，引擎仅监听 `127.0.0.1:8876`。原目录和原始包保持不变；原包、基线清单及随包历史证据保留。

## 开始使用

本机已配置 `.venv` 并复用现有 Python 3.12 依赖。双击 `启动Agent引擎.cmd`，或运行：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File ./launch.ps1 -Headless -NoBrowser
```

MCP 宿主导入根目录 `mcp.local.json`，入口、解释器和数据路径均已迁至本目录；移动到其他路径后须相应修改绝对路径。也可以显式运行：

```powershell
./.venv/Scripts/python.exe run_mcp.py --engine-url http://127.0.0.1:8876 --data-dir ./data
```

[Agent用法](AGENT.md) · [当前交付与验证](docs/WORKSTATION-DELIVERY-20261113.md) · [任务逐项状态](docs/NEXT-PLAN-STATUS-20261113.md) · [摩擦登记册](docs/FRICTION-REGISTER.md)

## 安装与环境检查

```powershell
# 新环境安装核心依赖；需要索引能取得锁定版本
powershell -NoProfile -ExecutionPolicy Bypass -File ./setup.ps1
# 本机已有相同依赖时，显式选择复用
powershell -NoProfile -ExecutionPolicy Bypass -File ./setup.ps1 -ReuseInstalled
# 仅检查，不安装或启动外部后端
./.venv/Scripts/python.exe scripts/environment_doctor.py --profile core
./.venv/Scripts/python.exe scripts/environment_doctor.py --profile full
```

核心层包含30个无需重型GIS后端的动词；另10个动词需要ArcPy/QGIS/PDAL或服务后端。精确依赖由能力说明的 dependency_tier/dependencies 返回。`implemented` 表示已有实现，不表示本机所有后端都已就绪。

本机核心调用通过。QGIS 3.44.15、ArcPy 10.8、PDAL 2.10.0及Wrench探活通过；PostGIS/GeoServer/SensorThings尚未在新工作区配置。核心包使用GEOS/GDAL依赖wheel，不包含重型GIS软件、许可证、后端集群、密码或旧私人工作区。完整源码包同样不打包这些第三方后端。

干净venv从当前镜像安装锁定版本失败，离线缓存也不完整；本轮成功使用的是明确复用已有依赖的环境。不能据此声称干净机器安装通过。失败记录见 evidence/workstation-core-install.log。

## 已验证与历史边界

本轮定向回归120 PASS，含官方MCP SDK完整流程及合成非空STA的真实HTTP往返。已有依赖下新进程就绪2.06秒，首次MCP调用完成4.51秒；不包含解压/安装耗时，不是真实模型评测。

A1错误自解释、A2摩擦日志、A4预演输入来源已落实。A3真实模型评测、Muse独立复验、最终同源800条矩阵、G1裁决和公开发布均未完成；认证保持NOT_CERTIFIED。

历史40/40、201个测试ID、800条合成矩阵的原始限定见 [迁入README](docs/README-IMPORTED-20261113.md) 和 ROUND4-DELIVERY.md；这些是原环境记录。6条严格栅格红灯、真实非空STA验证和GeoServer目录回写仍保留原状态。

根目录 SOURCE-MANIFEST.json 是迁入基线，不是修改后源码清单。当前交付清单为 WORKSTATION-MANIFEST.json；每个新ZIP内部也带独立源码清单与校验回执。全程未公开发布或推送。
