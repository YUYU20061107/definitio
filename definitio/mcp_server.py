"""Standard MCP stdio adapter for the one shared headless GIS engine.

This module deliberately imports no GIS runtime/SQLite implementation: multiple
Agent clients must not independently recover or execute the same database.
"""
from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path
import secrets
import sys
import time
from urllib.parse import urlparse

import httpx
from jsonschema import ValidationError, validate as validate_json
from mcp import types
from mcp.server.lowlevel import Server
from mcp.server.lowlevel.helper_types import ReadResourceContents
from mcp.server.stdio import stdio_server

from . import __version__
from .lifecycle import ROOT, ensure_engine
from .surface import Surface, SurfaceError, surface_failure

INTERFACE = "definitio-agent-v1"


def local_url(value):
    parsed = urlparse(value)
    if parsed.scheme != "http" or parsed.hostname not in ("127.0.0.1", "localhost", "::1") or parsed.username or parsed.password or parsed.path not in ("", "/") or parsed.query or parsed.fragment:
        raise ValueError("MCP engine URL must be a plain loopback HTTP origin")
    if not parsed.port or not 1 <= parsed.port <= 65535:
        raise ValueError("MCP engine URL must include an explicit port")
    return value.rstrip("/")


def load_manifest(engine_url, autostart=False, data_dir=None):
    with httpx.Client(base_url=engine_url, trust_env=False, timeout=10) as client:
        try:
            response = client.get("/api/agent/manifest")
        except httpx.ConnectError:
            if not autostart:
                raise RuntimeError("Shared GIS engine is not running. Start launch.ps1 -Headless -NoBrowser independently, or enable automatic independent startup.")
            ensure_engine(urlparse(engine_url).port, data_dir or ROOT / "data")
            deadline = time.monotonic() + 25
            while True:
                try:
                    response = client.get("/api/agent/manifest")
                    break
                except httpx.ConnectError:
                    if time.monotonic() >= deadline:
                        raise RuntimeError("Independent GIS engine startup timed out; inspect engine.stderr.log in the configured data directory")
                    time.sleep(.2)
        response.raise_for_status()
        manifest = response.json()
        if manifest.get("interface_version") != INTERFACE or not manifest.get("tools"):
            raise RuntimeError("Engine does not expose definitio-agent-v1. Restart the owned engine with the current source.")
        if manifest.get("engine_root") and Path(manifest["engine_root"]).resolve() != ROOT:
            raise RuntimeError("Port belongs to a different GIS checkout; existing service left unchanged")
        if data_dir is not None and manifest.get("workspace_path") != str(Path(data_dir).resolve() / "workspace.sqlite3"):
            raise RuntimeError("Engine uses a different workspace; existing service left unchanged")
        return manifest


def transport_error(message):
    return {"ok": False, "status": "error", "interface_version": INTERFACE, "data": {},
            "error": {"code": "TARGET_STATE_UNKNOWN", "message": message,
                      "retry_safe_after_correction": False, "recovery": "reconcile_transport",
                      "recovery_message": "Response was not received. Query the task if known, or retry the identical direct write with the SAME request_id. Do not create a new key or assume rollback.",
                      "execution_started": None}, "next_actions": [], "certification": "NOT_CERTIFIED"}


def create_server(manifest, client, tool_profile="compact", surface_profile="canonical"):
    surface = Surface(manifest["tools"], surface_profile)
    instructions = surface.text(manifest["instructions"]) + "\nSurface profile: " + json.dumps(surface.declaration, sort_keys=True)
    instructions += "\nTool names/arguments use this surface; immutable receipts and artifact contents retain canonical names and VIDs."
    server = Server("definitio-gis", version=__version__, instructions=instructions)
    definitions = {tool["name"]: tool for tool in manifest["tools"]}
    session_token = secrets.token_urlsafe(32)
    prompts = {p["name"]: p for p in manifest.get("prompts", [])}

    async def invoke(name, arguments, canonical=False):
        if not canonical:
            try:
                name, arguments = surface.decode(name, arguments)
            except SurfaceError as exc:
                return surface_failure(str(exc))
        try:
            response = await client.post("/api/agent/call", json={"name": name, "arguments": arguments},
                                         headers={"X-Definitio-Session": session_token})
            response.raise_for_status()
            payload = response.json()
            if name in definitions:
                validate_json(payload, definitions[name]["outputSchema"])
            return surface.result(payload)
        except (httpx.HTTPError, ValueError, ValidationError) as exc:
            return transport_error(f"GIS engine response unavailable or invalid: {type(exc).__name__}")

    @server.list_tools()
    async def list_tools():
        selected = manifest["tools"] if tool_profile == "full" else [t for t in manifest["tools"] if t["name"] in manifest.get("compact_tool_names", definitions)]
        return [types.Tool(**surface.tool(tool)) for tool in selected]

    @server.list_prompts()
    async def list_prompts():
        return [types.Prompt(name=p["name"], description=p["description"],
                            arguments=[types.PromptArgument(name=a, required=True) for a in p["arguments"]]) for p in prompts.values()]

    @server.get_prompt()
    async def get_prompt(name, arguments):
        p = prompts.get(name)
        values = arguments or {}
        if not p or set(values) != set(p["arguments"]) or any(not isinstance(v, str) or not 0 < len(v) <= 256 for v in values.values()):
            raise ValueError("Unknown prompt or invalid declared prompt arguments")
        return types.GetPromptResult(description=p["description"], messages=[
            types.PromptMessage(role="user", content=types.TextContent(type="text", text=surface.text(p["template"]).format(**values)))])

    @server.call_tool(validate_input=False)
    async def call_tool(name, arguments):
        # Engine validates the published schema and returns actionable domain
        # errors. SDK's default plain-text validation errors are avoided.
        payload = await invoke(name, arguments)
        return types.CallToolResult(content=[types.TextContent(type="text", text=json.dumps(payload, ensure_ascii=False))],
                                    structuredContent=payload, isError=not payload["ok"])

    @server.list_resources()
    async def list_resources():
        return [types.Resource(uri="definitio://workspace", name="GIS workspace", description="First page of immutable dataset handles and schemas", mimeType="application/json")]

    @server.list_resource_templates()
    async def list_resource_templates():
        return [types.ResourceTemplate(uriTemplate=f"definitio://{kind}/{{id}}", name=f"GIS {kind}",
                                       description="Bounded JSON artifact view. Use artifact_read for exact chunk continuation.", mimeType="application/json")
                for kind in ("datasets", "tasks", "evidence")]

    @server.read_resource()
    async def read_resource(uri):
        value = str(uri)
        payload = await invoke("workspace_list", {}, canonical=True) if value.rstrip("/") == "definitio://workspace" else await invoke("artifact_view", {"uri": value}, canonical=True)
        return [ReadResourceContents(json.dumps(payload, ensure_ascii=False), mime_type="application/json")]

    return server


async def serve(engine_url, manifest, tool_profile="compact", surface_profile="canonical"):
    async with httpx.AsyncClient(base_url=engine_url, trust_env=False, timeout=120) as client:
        server = create_server(manifest, client, tool_profile, surface_profile)
        async with stdio_server() as (read_stream, write_stream):
            await server.run(read_stream, write_stream, server.create_initialization_options())


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--engine-url", default="http://127.0.0.1:8765")
    parser.add_argument("--no-autostart", action="store_true", help="Connect only; never start an engine")
    parser.add_argument("--data-dir", type=Path, help="Trusted launcher workspace (not a tool argument)")
    parser.add_argument("--tool-profile", choices=["compact", "full"], default="compact", help="Compact router by default; full keeps direct GIS tools visible")
    parser.add_argument("--surface-profile", choices=["canonical", "surface-v1"], default="canonical", help="Reversible MCP naming; canonical remains the default")
    args = parser.parse_args()
    try:
        engine_url = local_url(args.engine_url)
        manifest = load_manifest(engine_url, autostart=not args.no_autostart, data_dir=args.data_dir)
        asyncio.run(serve(engine_url, manifest, args.tool_profile, args.surface_profile))
    except (ValueError, RuntimeError, httpx.HTTPError) as exc:
        print(f"Definitio MCP startup failed: {exc}", file=sys.stderr)
        raise SystemExit(1)


if __name__ == "__main__":
    main()
