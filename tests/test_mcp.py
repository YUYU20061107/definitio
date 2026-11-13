"""Official MCP client -> stdio adapter -> actual no-UI engine process."""
import asyncio
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import time

import httpx
import pytest

from scripts.agent_mcp_smoke import exercise
from definitio.mcp_server import local_url

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(scope='module')
def engine(tmp_path_factory):
    root = tmp_path_factory.mktemp('agent-headless-engine')
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0));port = sock.getsockname()[1]
    env = dict(os.environ, PYTHONIOENCODING='utf-8')
    with (root/'engine.log').open('w', encoding='utf-8') as log:
        process = subprocess.Popen([sys.executable,'-m','definitio.server','--port',str(port),'--data-dir',str(root/'data'),'--headless'],
                    cwd=ROOT,env=env,stdout=log,stderr=log,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
        url=f'http://127.0.0.1:{port}'
        try:
            ready=False
            with httpx.Client(trust_env=False,timeout=1) as client:
                for _ in range(60):
                    if process.poll() is not None: break
                    try:
                        response=client.get(url+'/api/agent/manifest')
                        if response.status_code==200:ready=True;break
                    except httpx.HTTPError:pass
                    time.sleep(.1)
            assert ready,(root/'engine.log').read_text(encoding='utf-8')
            yield url,root
        finally:
            process.terminate()
            try:process.wait(timeout=10)
            except subprocess.TimeoutExpired:process.kill();process.wait(timeout=5)


def test_full_agent_workflow_over_real_mcp_stdio(engine):
    url,root=engine
    report=asyncio.run(exercise(url,client_cwd=root))
    assert report['status']=='PASS' and report['tools_discovered']==60
    assert report['tool_calls']>20 and report['model_api_calls']==0
    assert report['replay_deduplicated'] and report['no_browser']


def test_engine_has_no_web_surface_in_headless_mode(engine):
    url,_=engine
    with httpx.Client(trust_env=False) as client:
        assert client.get(url+'/').json()['ui'] is False
        assert client.get(url+'/assets/app.js').status_code==404
        assert client.get(url+'/api/health').json()['ui_enabled'] is False


def test_second_engine_cannot_take_ownership_or_recover_an_active_workspace(engine):
    url,root=engine
    result=subprocess.run([sys.executable,'-m','definitio.server','--port','0','--data-dir',str(root/'data'),'--headless'],
                          cwd=ROOT,capture_output=True,text=True,timeout=20,
                          creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
    assert result.returncode!=0 and 'already owned by another engine' in result.stderr
    with httpx.Client(trust_env=False) as client:
        assert client.get(url+'/api/health').status_code==200


@pytest.mark.parametrize('url',['https://127.0.0.1:8765','http://example.com:8765','http://127.0.0.1:8765/private','http://user:secret@127.0.0.1:8765','file:///etc/passwd'])
def test_mcp_adapter_cannot_be_pointed_at_an_external_service(url):
    with pytest.raises(ValueError):local_url(url)


def test_mcp_adapter_never_spawns_or_takes_ownership_of_shared_engine(monkeypatch):
    from definitio import mcp_server
    class Client:
        def __init__(self,*a,**kw):pass
        def __enter__(self):return self
        def __exit__(self,*a):pass
        def get(self,path):
            raise httpx.ConnectError('offline')
    def forbidden(*a,**kw):raise AssertionError('MCP adapter must not spawn a shared engine')
    monkeypatch.setattr(mcp_server.httpx,'Client',Client)
    monkeypatch.setattr(subprocess,'run',forbidden)
    with pytest.raises(RuntimeError,match='independently'):
        mcp_server.load_manifest('http://127.0.0.1:8765')


def test_compact_prompts_and_scoped_chain_over_stdio(engine):
    from datetime import timedelta
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client
    import uuid

    async def exercise_compact():
        url, root = engine
        prefix = "compact_" + uuid.uuid4().hex[:8] + "_"
        params = StdioServerParameters(command=sys.executable, args=[str(ROOT / "run_mcp.py"), "--engine-url", url],
                                       cwd=str(root), env={"PYTHONIOENCODING": "utf-8"})
        from test_agent import fixture_geojson
        async with stdio_client(params) as (read, write):
            async with ClientSession(read, write, read_timeout_seconds=timedelta(seconds=60)) as session:
                initialized = await session.initialize()
                assert "calling Agent owns planning" in initialized.instructions
                assert len(initialized.instructions) < 2200
                tools = (await session.list_tools()).tools
                assert len(tools) == 21 and "gis_call" in {t.name for t in tools}
                assert "gis_buffer" not in {t.name for t in tools}
                prompts = (await session.list_prompts()).prompts
                assert len(prompts) == 4
                recipe = await session.get_prompt("buffer_validate", {"input": "actual_handle", "distance_m": "5", "target_prefix": prefix})
                assert "actual_handle" in recipe.messages[0].content.text

                async def call(name, a, error=None):
                    r = (await session.call_tool(name, a)).structuredContent
                    assert r["error"]["code"] == error if error else r["ok"], r
                    return r["data"]

                grant = await call("authorization_grant", {"target_prefix": prefix, "verbs": ["dataset_import", "gis_add_field", "gis_dissolve"],
                    "max_writes": 3, "ttl_seconds": 600, "confirm": True, "request_id": prefix + "grant"})
                g = grant["authorization"]["id"]
                source = (await call("dataset_import", {"name": prefix + "input", "geojson": fixture_geojson(), "crs": "EPSG:32649",
                          "source_kind": "synthetic_fixture", "authorization_id": g}))["dataset"]["id"]
                await call("workflow_route", {"intent": "dissolve"})
                description = await call("gis_describe_algorithm", {"algorithm": "gis_dissolve"})
                assert description["capability"]["prerequisites"]
                steps = [
                    {"verb": "gis_add_field", "parameters": {"dataset": source, "target": prefix + "added",
                     "field": {"name": "new_group", "type": "integer", "nullable": False, "default": 1}}},
                    {"verb": "gis_dissolve", "parameters": {"input": "$previous", "target": prefix + "groups", "fields": ["new_group"]}},
                ]
                preview = (await call("task_dry_run", {"goal": "schema chain", "steps": steps}))["preview"]
                assert preview["steps"][1]["output"]["feature_count"] == 1
                planned = await call("task_plan", {"goal": "schema chain", "steps": steps, "request_id": prefix + "plan"})
                task = planned["task"]
                done = await call("task_execute", {"task_id": task["id"], "plan_digest": task["plan_digest"], "authorization_id": g})
                assert done["task"]["task_state"] == "COMMITTED"
                output = done["outputs"][-1]
                summary = await call("artifact_view", {"uri": output["geojson_uri"]})
                assert summary["summary"]["collections"]["features"]["count"] == 1
                await call("authorization_revoke", {"authorization_id": g})
                # Recovering a prior accepted plan after revocation spends no new quota.
                await call("task_execute", {"task_id": task["id"], "plan_digest": task["plan_digest"], "authorization_id": g})
        # A fresh MCP process has a distinct session token.
        async with stdio_client(params) as (read, write):
            async with ClientSession(read, write) as second:
                await second.initialize()
                r = (await second.call_tool("gis_call", {"verb": "gis_dissolve", "arguments":
                    {"input": source, "target": prefix + "foreign", "fields": [], "authorization_id": g}})).structuredContent
                assert r["error"]["code"] == "AUTHORIZATION_REQUIRED"

    asyncio.run(exercise_compact())


@pytest.mark.skipif(os.name != "nt", reason="Windows Job Object lifecycle proof")
def test_cold_start_survives_mcp_host_disconnect(tmp_path):
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client
    from definitio.lifecycle import task_identity
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    url = f"http://127.0.0.1:{port}"
    data = tmp_path / "cold-workspace"
    task = task_identity(port, data)
    async def connect():
        params = StdioServerParameters(command=sys.executable,
            args=[str(ROOT / "run_mcp.py"), "--engine-url", url, "--data-dir", str(data)],
            cwd=str(tmp_path), env={"PYTHONIOENCODING": "utf-8"})
        async with stdio_client(params) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                assert len((await session.list_tools()).tools) == 21
    try:
        asyncio.run(connect())
        time.sleep(.5)
        with httpx.Client(trust_env=False) as client:
            manifest = client.get(url + "/api/agent/manifest").json()
            assert manifest["workspace_path"] == str(data.resolve() / "workspace.sqlite3")
            assert manifest["engine_root"] == str(ROOT)
            assert client.get(url + "/api/health").status_code == 200
    finally:
        # Only the exact demand task this test created is stopped/removed.
        subprocess.run(["schtasks.exe", "/End", "/TN", task], capture_output=True, timeout=20, creationflags=subprocess.CREATE_NO_WINDOW)
        subprocess.run(["schtasks.exe", "/Delete", "/TN", task, "/F"], capture_output=True, timeout=20, creationflags=subprocess.CREATE_NO_WINDOW)
