"""Absolute-path MCP entry point; works regardless of the Agent client's cwd."""
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))
from definitio.mcp_server import main

if __name__ == "__main__":
    main()
