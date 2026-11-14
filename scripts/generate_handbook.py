"""Generate the handbook from the live catalog.

    python scripts/generate_handbook.py --out docs/HANDBOOK.md
    python scripts/generate_handbook.py --check          # exit 1 on drift

--check never writes: it only tells you whether the checked-in handbook still
matches what the engine would render right now.
"""
from __future__ import annotations

import argparse
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from definitio.agent import AgentService          # noqa: E402
from definitio.handbook import build, render_json, render_markdown  # noqa: E402
from definitio.runtime import Runtime             # noqa: E402


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, default=ROOT / "docs" / "HANDBOOK.md")
    parser.add_argument("--json-out", type=Path)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()

    with tempfile.TemporaryDirectory() as tmp:
        service = AgentService(Runtime(Path(tmp) / "handbook.sqlite3"))
        handbook = build(service)
    markdown = render_markdown(handbook)

    if args.check:
        current = args.out.read_text(encoding="utf-8") if args.out.exists() else ""
        if current != markdown:
            print(f"DRIFT: {args.out} 与活目录不一致，请重新生成", file=sys.stderr)
            return 1
        print(f"OK: {args.out} 与活目录一致（source_digest={handbook['source_digest'][:16]}）")
        return 0

    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(markdown, encoding="utf-8")
    if args.json_out:
        args.json_out.parent.mkdir(parents=True, exist_ok=True)
        args.json_out.write_text(render_json(handbook), encoding="utf-8")
    print(f"wrote {args.out} ({len(markdown)} chars, digest={handbook['source_digest'][:16]})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
