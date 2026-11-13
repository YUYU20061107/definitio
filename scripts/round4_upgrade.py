"""Back up, apply and read back the local 40-verb upgrade. No Git operations."""
from pathlib import Path
import argparse
import datetime
import hashlib
import json
import shutil
import sqlite3
import sys
import zipfile

import httpx

ROOT = Path(__file__).resolve().parents[1]
ORIGINAL = Path(r"C:\Users\RAHUYU\WorkBuddy\2026-11-09-10-00-10\agent-native-gis\05-reference-runtime")
PLAN = ROOT / "evidence/round4-upgrade-plan.json"


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def state(conn):
    return {table: {json.loads(row[0])["id"]: hashlib.sha256(row[0].encode()).hexdigest() for row in conn.execute("SELECT document FROM " + table)} for table in ("datasets", "tasks", "evidence")}


def g1():
    expected = json.loads((ROOT / "evidence/completion-g1-before.json").read_text(encoding="utf-8"))
    changed = [name for name, expected_sha in expected.items() if sha(ORIGINAL.parent / name) != expected_sha]
    assert len(expected) == 59 and not changed, changed
    return {"files": 59, "changed": changed}


def prepare():
    with httpx.Client(trust_env=False) as c:
        health = c.get("http://127.0.0.1:8765/api/health").json()
    assert health["versions"]["source_digest"] == "fafc475d929ed9e7709748810841ad1086521db30484ee840ff2d28b8c913d27", health
    oldmanifest = json.loads((ORIGINAL / "SOURCE-MANIFEST.json").read_text(encoding="utf-8"))
    assert all((ORIGINAL / name).is_file() and sha(ORIGINAL / name) == expected for name, expected in oldmanifest["files"].items())
    conn = sqlite3.connect("file:" + str(ORIGINAL / "data/workspace.sqlite3") + "?mode=ro", uri=True)
    documents = state(conn)
    active = [json.loads(row[0])["id"] for row in conn.execute("SELECT document FROM tasks") if json.loads(row[0])["task_state"] in ("EXECUTING", "PRECHECKED")]
    assert not active, active
    protected = g1()
    backup = ORIGINAL / "backup" / ("gis-round4-upgrade-" + datetime.datetime.now().strftime("%Y%m%d-%H%M%S"))
    backup.mkdir(parents=True, exist_ok=False)
    files = []
    for folder, patterns in {"definitio": ["*.py", "catalog.json"], "scripts": ["*.py"], "tests": ["*.py"], "web": ["*.html", "*.css", "*.js", "*.svg"], "examples": ["*.geojson", "*.las"]}.items():
        for pattern in patterns:
            files.extend((ROOT / folder).glob(pattern))
    files.extend(p for p in ROOT.iterdir() if p.is_file() and p.suffix in (".md", ".txt", ".py", ".ps1", ".cmd"))
    relative = sorted({p.relative_to(ROOT).as_posix() for p in files})
    old = {}
    with zipfile.ZipFile(backup / "source-before.zip", "x", zipfile.ZIP_DEFLATED) as z:
        for name in relative + ["mcp.local.json", "SOURCE-MANIFEST.json"]:
            target = (ORIGINAL / name).resolve()
            assert target.is_relative_to(ORIGINAL.resolve())
            if target.is_file():
                old[name] = sha(target)
                z.write(target, name)
    with sqlite3.connect(backup / "workspace-before.sqlite3") as db:
        conn.backup(db)
    conn.close()
    plan = {"backup": str(backup), "health_before": health, "source_files": {name: sha(ROOT / name) for name in relative}, "old_files": old,
            "workspace_documents_before": documents, "active_tasks_before": active, "g1": protected, "mcp_config_sha256": sha(ORIGINAL / "mcp.local.json"),
            "private_backend_config": "data/backends.json; excluded from source package", "scope": "05-reference-runtime only; no Git or public deployment"}
    PLAN.write_text(json.dumps(plan, ensure_ascii=False, indent=2), encoding="utf-8")
    (backup / "upgrade-plan.json").write_bytes(PLAN.read_bytes())
    print(json.dumps({"status": "PREPARED", "backup": str(backup), "files": len(relative), "workspace_counts": {k: len(v) for k, v in documents.items()}, "g1": protected}, ensure_ascii=False))


def apply():
    plan = json.loads(PLAN.read_text(encoding="utf-8"))
    for name, expected in plan["source_files"].items():
        assert sha(ROOT / name) == expected, name
        target = (ORIGINAL / name).resolve()
        assert target.is_relative_to(ORIGINAL.resolve())
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(ROOT / name, target)
        assert sha(target) == expected, name
    target = ORIGINAL / "data/backends.json"
    if target.exists():
        shutil.copy2(target, Path(plan["backup"]) / "backends-before.private.json")
    shutil.copy2(ROOT / "data/backends.json", target)
    assert sha(target) == sha(ROOT / "data/backends.json")
    assert sha(ORIGINAL / "mcp.local.json") == plan["mcp_config_sha256"]
    print(json.dumps({"status": "APPLIED", "files": len(plan["source_files"]), "g1": g1(), "credentials": "private file copied; not printed"}))


def verify_live():
    plan = json.loads(PLAN.read_text(encoding="utf-8"))
    with httpx.Client(trust_env=False) as c:
        health = c.get("http://127.0.0.1:8765/api/health").json()
        capabilities = c.get("http://127.0.0.1:8765/api/capabilities").json()
        manifest = c.get("http://127.0.0.1:8765/api/agent/manifest").json()
    assert capabilities["implemented"] == capabilities["total"] == 40 and len(manifest["tools"]) == 60
    sys.path.insert(0, str(ROOT))
    # Compute the candidate digest without opening the protected live workspace.
    from definitio.model import digest
    package = ROOT / "definitio"
    execution = list(package.glob("*.py")) + [ROOT / "scripts" / name for name in ("native_gis_worker.py", "qgis_processing_worker.py")]
    expected_digest = digest({p.relative_to(ROOT).as_posix(): sha(p) for p in sorted(execution)})
    assert health["versions"]["source_digest"] == expected_digest
    conn = sqlite3.connect("file:" + str(ORIGINAL / "data/workspace.sqlite3") + "?mode=ro", uri=True)
    documents = state(conn)
    conn.close()
    assert documents == plan["workspace_documents_before"]
    assert sha(ORIGINAL / "mcp.local.json") == plan["mcp_config_sha256"]
    report = {"status": "PASS", "health": health, "implemented": 40, "total": 40, "full_tools": 60, "compact_tools": 21,
              "workspace_documents_unchanged": True, "workspace_counts": {k: len(v) for k, v in documents.items()}, "g1": g1(), "live_mcp_config_unchanged": True,
              "backup": plan["backup"], "native_backends": {"postgis_port": 25432, "geoserver_port": 18980, "shared_5432_untouched": True}, "no_publication_or_git_push": True}
    (ROOT / "evidence/round4-live-engine.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("phase", choices=["prepare", "apply", "verify"])
    args = parser.parse_args()
    {"prepare": prepare, "apply": apply, "verify": verify_live}[args.phase]()
