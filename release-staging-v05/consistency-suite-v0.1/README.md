# 一致性测试集 v0.1

四条测试文件与一份自检脚本，配套契约增补 v0.5 与对象头规范 v0.1。

运行方式（在引擎树根目录）：

```bash
python -m pytest consistency-suite-v0.1 -q
python consistency-suite-v0.1/selfcheck_runner.py
```

本套件**不是**独立可执行包：它 import 被测引擎的 `definitio` 包，必须与引擎同树运行。
覆盖范围：

| 文件 | 覆盖 |
|---|---|
| `test_v05_protocol.py` | disposition 三值、句柄 STALE/AMBIGUOUS、读取三态 |
| `test_v05_object_header.py` | 对象头四槽在 40 动词上的齐全性 |
| `test_v05_handbook.py` | 手册随活目录漂移 |
| `test_v05_selfcheck.py` | 检查器自检可失败性 |
| `selfcheck_runner.py` | 自植违规的端到端检出报告 |

认证状态：**NOT_CERTIFIED**。本套件证明「检查器能检出已知违规」，不证明引擎通过外部认证。
