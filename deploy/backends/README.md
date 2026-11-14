# 本机原生后端配置包（PostGIS / GeoServer / SensorThings）

状态：**模板，未在本机验证**。2026-11-14 T7 实测：

- Docker CLI 29.6.2 已安装，但守护进程未运行（`npipe:////./pipe/dockerDesktopLinuxEngine` 不存在）。
- 本机 `PostgreSQL 16` 正在 5432 监听，`pg_hba.conf` 全为 `scram-sha-256`，无可用凭据；不得猜测口令。
- 未安装 QGIS / PyQGIS，而 `environment.py` 探测 PostGIS 与 MVT worker 都要求 QGIS Python。

因此本轮**没有**写入 `data/backends.json`，10 项环境阻塞项保持红灯（见交付报告 T7 节）。

## 1. 键名契约（来自代码，不是发明）

`definitio/completion.py::config()` 读 `data/backends.json`，顶层键 `postgis` / `geoserver` / `sensorthings`
（`istsos` 是 `sensorthings` 的别名）。`DEFINITIO_BACKEND_CONFIG` 可覆盖路径。

| 后端 | 必需键 | 消费点 |
|---|---|---|
| `postgis` | `host` `port` `dbname` `user` `password` | 原样传给 `psycopg2.connect(**cfg)`（`scripts/native_gis_worker.py:33`） |
| `geoserver` | `url` `user` `password` `sandbox=true` `allow_publish=true`（可选 `workspace_prefix`） | `completion.py:284-300` |
| `sensorthings` | `url`（可选 `user` `password`） | `completion.py:174-177,235` |

`environment.py:94` 只探测回环地址（`127.0.0.1` / `localhost` / `::1`）。

## 2. 起服务

```bash
# 仅在本机需要时执行；本轮未执行
docker compose -f deploy/backends/docker-compose.template.yml up -d
```

镜像 tag 请按官方当前版本核对后再固定；模板里的 tag 是占位，不是已验证组合。

## 3. 写配置

```bash
cp deploy/backends/backends.example.json data/backends.json
# 用真实口令替换 <set-me>；data/ 已在 .gitignore 内，不得提交
```

## 4. 验证顺序

```bash
python scripts/environment_doctor.py --profile full --output evidence/backend-doctor.json
python -m pytest tests/test_native_completion.py -q
```

期望：doctor 的 `postgis` / `geoserver` / `sensorthings` 三项为 `AVAILABLE`，10 项阻塞用例转 PASS。
若 `postgis` 仍为 `UNAVAILABLE`，先确认 QGIS Python（`DEFINITIO_QGIS_PYTHON`）可用——它同时承担 MVT worker 的 psycopg2。

## 5. 配置安全

- 口令只写 `data/backends.json`，不写进源码、文档、证据或提交。
- 本轮交付报告中只记录「文件不存在 / 未配置」，不记录任何凭据。
