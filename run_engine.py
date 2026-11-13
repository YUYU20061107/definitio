"""Absolute engine entry, also used by the independent Windows demand-only task."""
from pathlib import Path
import sys

if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--data-dir", type=Path, default=Path(__file__).resolve().parent / "data")
    args = parser.parse_args()
    args.data_dir.mkdir(parents=True, exist_ok=True)
    # pythonw has no inherited console. Logs belong to the engine, never MCP stdout.
    sys.stdout = (args.data_dir / "engine.stdout.log").open("a", encoding="utf-8", buffering=1)
    sys.stderr = (args.data_dir / "engine.stderr.log").open("a", encoding="utf-8", buffering=1)
    from definitio.server import main
    sys.argv = [sys.argv[0], "--port", str(args.port), "--data-dir", str(args.data_dir), "--headless"]
    main()
