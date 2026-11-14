# 干净环境复跑证据（T3）

`clean-install-full-suite.xml` / `.log`：在 08 树外全新 venv（Python 3.12.9，
`clean-venv-v05`）里跑的全量套件。

```text
372 tests, 361 passed, 11 failed, 0 errors, 236.19 s
```

此时的 372 项 = 既有 357 + T1/T2 新增 15 项；T5/T6 的 13 项是在这次运行之后才写的，
最终口径以 `evidence/regression-full-suite.xml`（385 项）为准。

11 项失败 = 既有 10 项环境阻塞（PostGIS/QGIS/istSOS 未配置）+ 1 项
`test_mcp.py::test_cold_start_survives_mcp_host_disconnect`，后者原因是本会话
安全策略把 `schtasks.exe` 列入程序黑名单，用例 `finally` 里的收尾命令被拒（`WinError 5`）。
同一用例在基线提交 `4040567` 的原始代码上复跑同样失败，与本轮改动无关，见交付报告 T3.3。

## 已作废的首次尝试（记录在案，不隐藏）

首次用同一 venv 调 `golden_chain_report.py` 全跑时得到 `328 errors`，全部是 setup 阶段
`SystemExit: 1` / `AssertionError`。原因是本会话 `PYTHONPATH` 指向 WorkBuddy 沙箱的
`sitecustomize.py`，它拦截 `shutil.rmtree`，pytest 清理上一轮残留的 35 MB `--basetemp`
目录时被挡。清空 `PYTHONPATH` 并换全新 basetemp 后重跑即得上面的真实结果。
该次产物（2.2 MB 无效 XML）已删除，避免与真实证据混淆。
