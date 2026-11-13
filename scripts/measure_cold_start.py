"""Time a local core engine and official MCP SDK workflow; retain the workspace."""
import asyncio
import json
from pathlib import Path
import socket
import subprocess
import sys
import time
import uuid
from datetime import timedelta

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import httpx
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


async def first_call(url, directory):
    params = StdioServerParameters(command=sys.executable, args=[str(ROOT / "run_mcp.py"),
        "--engine-url", url, "--no-autostart"], cwd=str(directory), env={"PYTHONIOENCODING": "utf-8"})
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write, read_timeout_seconds=timedelta(seconds=30)) as session:
            await session.initialize()
            tools = await session.list_tools()
            result = await session.call_tool("gis_call", {"verb": "gis_measure", "arguments": {
                "operation": "area", "a_wkt": "POLYGON ((0 0,2 0,2 3,0 3,0 0))", "crs": "EPSG:32649"}})
            assert result.structuredContent["ok"] and not result.isError, result
            return {"tool_profile": "compact", "tool_count": len(tools.tools), "call": result.structuredContent,
                    "model_api_calls": 0, "scripted_tool_calls": 1}


def main():
    started = time.perf_counter()
    directory = ROOT / "data" / ("cold-start-" + uuid.uuid4().hex[:8])
    directory.mkdir(parents=True)
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    url = f"http://127.0.0.1:{port}"
    with (directory / "engine.log").open("w", encoding="utf-8") as log:
        process = subprocess.Popen([sys.executable, str(ROOT / "run_engine.py"), "--port", str(port), "--data-dir", str(directory)],
                                   cwd=ROOT, stdout=log, stderr=log,
                                   creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        try:
            with httpx.Client(trust_env=False, timeout=1) as client:
                deadline = time.perf_counter() + 30
                while True:
                    if process.poll() is not None:
                        raise RuntimeError("Engine exited; inspect " + str(directory))
                    try:
                        if client.get(url + "/api/health").status_code == 200:
                            break
                    except httpx.HTTPError:
                        pass
                    if time.perf_counter() >= deadline:
                        raise RuntimeError("Engine readiness timeout")
                    time.sleep(.1)
            ready_seconds = time.perf_counter() - started
            workflow = asyncio.run(first_call(url, directory))
            report = {"status": "PASS", "scope": "existing workstation dependencies; first official MCP tool call, not a model evaluation",
                      "python": sys.executable, "engine_ready_seconds": ready_seconds,
                      "engine_and_workflow_seconds": time.perf_counter() - started,
                      "fresh_install_verified": False, "workspace": str(directory), "workflow": workflow}
            output = ROOT / "evidence/workstation-cold-start.json"
            output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            print(json.dumps({k: v for k, v in report.items() if k != "workflow"}, ensure_ascii=False, indent=2))
        finally:
            process.terminate()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)


if __name__ == "__main__":
    main()
