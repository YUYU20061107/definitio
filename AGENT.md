# Definitio · Agent 操作指南

接口：`definitio-agent-v1` · 运行时：`0.2.0-dev` · 更新：2026-11-13。

## 连接与发现

`python run_mcp.py` 默认连接本机 8765 端口；引擎未运行时独立启动。Windows 使用当前用户、最低权限、无触发器的按需任务；退出 MCP 客户端后共享引擎继续运行。任务名由源码目录、数据目录和端口计算，只复用匹配的执行动作。占用端口的其他服务不被替换。`--no-autostart` 切回只连接模式，`--data-dir` 指定可信的启动工作区。

本工作站使用 mcp.local.json 中显式的 8876 端口和本目录 data。工作站启动脚本默认8876；裸 run_mcp.py 的历史默认值8765仍保留以兼容旧调用者。

默认 compact 模式公布 21 个工具：控制面加 `gis_describe_algorithm`。调用 `workflow_route` 选动词家族，获取一个动词的精确 schema 后，通过 `gis_call` 执行：

```json
{"verb":"gis_measure","arguments":{"operation":"area","a_wkt":"POLYGON ((0 0,2 0,2 3,0 3,0 0))","crs":"EPSG:32649"}}
```

MCP 可显式增加 `--surface-profile surface-v1` 试用候选命名；省略时仍是 `canonical`。握手 instructions 声明 profile 名、版本和映射文件 SHA256。候选映射在 `definitio/surfaces/surface-v1.json`，仅在 MCP 呈现层转换工具名、操作参数名及声明的枚举。HTTP、规范名、VID、权限、计划摘要、不可变回执和工件内容保持规范形状；不要把用户字段名或工件内容当作参数别名转换。发现工具返回所选表面的 schema，调用时按该 schema 填写；回执中的规范动词用于溯源。删除动词和破坏性预检名称保持显式。候选表面是否采纳，以本轮冻结评测裁决为准。

`--tool-profile full` 公布 60 个工具（40 个 GIS 动词及 20 个管理工具）。`gis_list_algorithms` 支持 `family`、`implemented_only`、`query`；`gis_describe_algorithm` 返回先决条件、实现路线、依赖分层和完整参数定义。40 个动词均有实现，但扩展后端是否可用须以本机环境医生及实际调用为准；历史 40/40 不能直接当作本机验证。精确数据域见 LOCAL-CONTRACT-ADDENDUM.md。

MCP 初始化携带简短操作手册，并提供四个标准 prompts：`gis_handbook`、`clip_dissolve_statistics`、`buffer_validate`、`reproject_merge`。提示词提供操作顺序；校验和授权由代码执行。

HTTP 使用同一实现：`GET /api/agent/manifest`；`POST /api/agent/call` 接受 `{"name":"工具名","arguments":{...}}`。会话作用域调用需通过 `X-Definitio-Session` 传同一高熵令牌；MCP 适配器自动生成并保留此令牌，不把它放进模型参数或日志。这是本机调用方隔离，不是远程账户认证。

## 授权与重试

单次写入仍支持 `confirm=true` 和稳定 `request_id`。已获宿主或用户批准的链可以先调用一次 `authorization_grant`：

```json
{"target_prefix":"job_","verbs":["dataset_import","gis_add_field","gis_clip","gis_dissolve"],"max_writes":8,"ttl_seconds":900,"confirm":true,"request_id":"grant-job-1"}
```

此后导入或 GIS 写入传返回的 `authorization_id`，范围内不再逐步确认。省略 `request_id` 时，系统按会话、动词与完整请求生成稳定键；相同请求重试找回原成果。授权绑定会话、目标名称的字面前缀、具体写动词、最多 100 次写入预留和最多 3600 秒有效期。既有目标不会覆盖；跨会话、前缀/动词越界、过期、撤销或额度不足返回明确错误。

`task_execute` 会先验证计划摘要，再一次性为计划中所有写步骤预留次数。预留采用保守记账：尝试失败也不自动退还；提交过的同一请求重试不重复扣除。`authorization_get` 返回余量和最近 20 条审计事件及总条数，完整审计持久化在工作区 SQLite。`authorization_revoke` 立即阻止新写入，已有任务和回执仍可读取。计划治理记录保留授权 ID。宿主自己的授权政策继续适用。

网络丢失时用相同参数和键重试，或 `task_get` 查状态。FAILED/ISOLATED 不会隐式重做；步骤逐个事务提交，部分完成时保留已提交成果。

## 预演与执行

1. `workspace_list` 组合筛选 `name_contains`、`fields`（须全部包含）、`crs`、`geometry_type`，然后 `dataset_describe` 核对选中的数据。
2. `task_dry_run(goal,steps)` 在内存中执行同一套计算代码，逐步返回实际输入/输出字段、CRS、数量、增删字段或错误。后续依赖步标 BLOCKED。预演不写数据、任务、证据、请求或授权账目。
3. `task_plan` 保存最多六步的明确链，并附带预演结果。查看 `preview.ok` 和逐步效果后，`task_execute` 使用返回的原样 `plan_digest`，传单次确认或 `authorization_id`。
4. 预演不保留目标名称，执行时仍检查输入指纹、目标占用和计算结果；预演不能代替执行回执。

步骤的 `parameters` 采用对应 GIS 动词的计算参数，不含控制面的 `request_id/authorization_id`。`$previous` 仅引用紧邻前一步的数据成果。

`request_id` 放在 `task_plan` 顶层，`authorization_id` 放在 `task_execute` 顶层；`task_dry_run` 不需要授权。执行前不要用新目标名称引用中间成果。错误 `details.rule` 分别为 `operation_parameters_only` 和 `previous_dataset_output`，并指出步号、参数和修法。每步预演的 `input_sources` 返回工作区快照或前一步来源及解析后的句柄。

规范计划示例（先把输入替换成已有数据集句柄）：

```json
{"goal":"复制后检查几何","request_id":"plan-copy-1","steps":[
  {"verb":"gis_copy_dataset","parameters":{"source":"EXISTING_DATASET_ID","target":"job_copy"}},
  {"verb":"gis_validate_geometry","parameters":{"dataset":"$previous","repair":false}}
]}
```

## CRS、字段与几何

导入提供已知 CRS；坐标顺序为 x,y。`gis_reproject` 改变坐标，`gis_assign_projection` 只改变副本元数据。缓冲、裁剪、叠加、融合和量测采用投影平面；距离按 CRS 单位换算为米，面积 m²；双输入面分析要求相同 CRS。

`gis_validate_geometry(repair=false)` 的 `valid=false` 是诊断结果。计算入口检查几何，保持无效记录可定位。融合只保留明确列出的字段；`fields=[]` 表示全体融合；数值浮点分组尚未支持。裁剪保留输入属性；空间筛选返回完整记录；merge 要求字段顺序、类型、可空性严格同构，并创建新数据集。

## 成果读取

任务与证据默认返回摘要和不可变 URI。`artifact_view(uri)` 先读类型、键和数组数量；`pointer` 使用 JSON Pointer，例如 `/features`、`/groups`、`/features/0/properties`。`view=page` 返回结构化值；直接传回 `cursor` 继续，无须拼接 JSON 或人工维护 offset。服务器验证快照和规范 JSON 哈希，摘要显示完整工件哈希。单值过大时继续下钻，或使用精确读取接口。

`dataset_features`、`evidence_get` 与旧版 `offset/limit`、`artifact_read` 无损字符分块接口均保留。检查 `ok/isError`、任务状态和几何的 `valid`，空结果可为正常计算成果。

## 验证与边界

本次实现及命令见 [验证记录](AGENT-USABILITY-VALIDATION.md)。GIS 对照使用实际安装的 QGIS/OGR 独立进程，分别统计结果等价、能力契约、范围拒绝和空面参考接口差异。有限夹具不能推出全域完备、独立数学真值或 G1 认证；历史基线与研究结论维持原状态。

协议依据：[MCP prompts](https://modelcontextprotocol.io/specification/2025-11-25/server/prompts)、[MCP tools](https://modelcontextprotocol.io/specification/2025-11-25/server/tools)。计算参考：[GDAL vector API](https://gdal.org/en/stable/api/python/vector_api.html)、[PyQGIS 3.44 geometry API](https://qgis.org/pyqgis/3.44/core/QgsGeometry.html)。
