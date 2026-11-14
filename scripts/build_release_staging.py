"""Assemble the December open-source release staging tree. Local only.

    python scripts/build_release_staging.py --out release-staging-v05

This script NEVER publishes, tags, pushes, uploads or contacts any network
service. It only copies already-reviewed files into one directory and writes a
hash manifest so a reviewer can diff the staging tree against this repository.

Refuses to delete: if the target already exists, files are overwritten in place
and any leftover file is reported as stale instead of being removed silently.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

FILES = {
    "contract/CONTRACT-ADDENDUM-v0.5.md": "docs/CONTRACT-ADDENDUM-v0.5.md",
    "contract/OBJECT-HEADER-v0.1.md": "docs/OBJECT-HEADER-v0.1.md",
    "handbook/HANDBOOK.md": "docs/HANDBOOK.md",
    "install/CLEAN-INSTALL.md": "docs/CLEAN-INSTALL.md",
    "consistency-suite-v0.1/test_v05_protocol.py": "tests/test_v05_protocol.py",
    "consistency-suite-v0.1/test_v05_object_header.py": "tests/test_v05_object_header.py",
    "consistency-suite-v0.1/test_v05_handbook.py": "tests/test_v05_handbook.py",
    "consistency-suite-v0.1/test_v05_selfcheck.py": "tests/test_v05_selfcheck.py",
    "consistency-suite-v0.1/selfcheck_runner.py": "scripts/consistency_selfcheck.py",
    "evidence/consistency-selfcheck.json": "evidence/consistency-selfcheck.json",
    "evidence/clean-install-selftest.json": "evidence/clean-install-selftest.json",
}

SUITE_README = """# 一致性测试集 v0.1

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
"""


def sha256(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, default=ROOT / "release-staging-v05")
    args = parser.parse_args()
    out = args.out

    missing = [src for src in FILES.values() if not (ROOT / src).exists()]
    if missing:
        print("缺少待暂存文件：" + ", ".join(missing), file=sys.stderr)
        return 1

    out.mkdir(parents=True, exist_ok=True)
    manifest = {"format": "definitio-release-staging-v1", "published": False,
                "pushed": False, "certification": "NOT_CERTIFIED",
                "files": [], "source_root": ROOT.name}
    for target, source in sorted(FILES.items()):
        destination = out / target
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(ROOT / source, destination)
        manifest["files"].append({"path": target, "source": source,
                                  "sha256": sha256(destination),
                                  "bytes": destination.stat().st_size})

    (out / "consistency-suite-v0.1" / "README.md").write_text(SUITE_README, encoding="utf-8")
    manifest["files"].append({"path": "consistency-suite-v0.1/README.md", "source": None,
                              "sha256": sha256(out / "consistency-suite-v0.1" / "README.md"),
                              "bytes": (out / "consistency-suite-v0.1" / "README.md").stat().st_size})

    tree_payload = "\n".join(f"{item['path']}:{item['sha256']}" for item in
                             sorted(manifest["files"], key=lambda i: i["path"]))
    manifest["tree_sha256"] = hashlib.sha256(tree_payload.encode("utf-8")).hexdigest()
    manifest["file_count"] = len(manifest["files"])

    expected = {item["path"] for item in manifest["files"]} | {"MANIFEST.json"}
    stale = sorted(str(p.relative_to(out)) for p in out.rglob("*") if p.is_file()
                   and str(p.relative_to(out)).replace("\\", "/") not in expected)
    manifest["stale_files"] = stale

    (out / "MANIFEST.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
                                       encoding="utf-8")
    print(json.dumps({"out": str(out), "file_count": manifest["file_count"],
                      "tree_sha256": manifest["tree_sha256"], "stale_files": stale,
                      "published": False}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
