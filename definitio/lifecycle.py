"""Start an owned loopback engine outside the MCP host's process lifetime."""
from __future__ import annotations

import getpass
import hashlib
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[1]
NS = "http://schemas.microsoft.com/windows/2004/02/mit/task"


def task_identity(port, data_dir):
    value = str(ROOT).casefold() + "|" + str(Path(data_dir).resolve()).casefold() + "|" + str(port)
    return "DefinitioEngine-" + hashlib.sha256(value.encode()).hexdigest()[:16]


def ensure_engine(port, data_dir):
    """Demand-start only; never stop/replace an existing service or create a trigger."""
    data_dir = Path(data_dir).resolve()
    data_dir.mkdir(parents=True, exist_ok=True)
    entry = ROOT / "run_engine.py"
    args = [str(entry), "--port", str(port), "--data-dir", str(data_dir)]
    if os.name != "nt":
        with (data_dir / "engine-launch.log").open("ab") as log:
            subprocess.Popen([sys.executable, *args], cwd=ROOT, stdin=subprocess.DEVNULL,
                             stdout=log, stderr=log, close_fds=True, start_new_session=True)
        return {"strategy": "independent_session", "data_dir": str(data_dir)}
    executable = str(Path(sys.executable).with_name("pythonw.exe"))
    if not Path(executable).is_file():
        raise RuntimeError("Independent Windows startup requires pythonw.exe next to the configured Python")
    arguments = subprocess.list2cmdline(args)
    task = task_identity(port, data_dir)
    flags = subprocess.CREATE_NO_WINDOW

    def run(*parts):
        return subprocess.run(["schtasks.exe", *parts], capture_output=True, creationflags=flags, timeout=20)

    def matches(raw):
        document = ET.fromstring(raw)
        actions = document.findall(f"{{{NS}}}Actions/{{{NS}}}Exec")
        return len(actions) == 1 and actions[0].findtext(f"{{{NS}}}Command") == executable and actions[0].findtext(f"{{{NS}}}Arguments") == arguments

    existing = run("/Query", "/TN", task, "/XML")
    if existing.returncode == 0:
        if not matches(existing.stdout):
            raise RuntimeError("Owned engine task name is occupied by a different action; left unchanged")
    else:
        ET.register_namespace("", NS)
        document = ET.Element(f"{{{NS}}}Task", version="1.2")
        def add(parent, tag, value=None, **attrs):
            node = ET.SubElement(parent, f"{{{NS}}}{tag}", **attrs)
            node.text = value
            return node
        principals = add(document, "Principals")
        principal = add(principals, "Principal", id="Author")
        add(principal, "UserId", getpass.getuser())
        add(principal, "LogonType", "InteractiveToken")
        add(principal, "RunLevel", "LeastPrivilege")
        settings = add(document, "Settings")
        for tag, value in {"MultipleInstancesPolicy": "IgnoreNew", "DisallowStartIfOnBatteries": "false",
                           "StopIfGoingOnBatteries": "false", "AllowHardTerminate": "true",
                           "AllowStartOnDemand": "true", "Enabled": "true", "Hidden": "true",
                           "ExecutionTimeLimit": "PT0S"}.items():
            add(settings, tag, value)
        action = add(add(document, "Actions", Context="Author"), "Exec")
        add(action, "Command", executable)
        add(action, "Arguments", arguments)
        add(action, "WorkingDirectory", str(ROOT))
        with tempfile.NamedTemporaryFile(suffix=".xml", delete=False) as handle:
            xml_path = Path(handle.name)
            handle.write(ET.tostring(document, encoding="utf-16", xml_declaration=True))
        try:
            created = run("/Create", "/TN", task, "/XML", str(xml_path))
            if created.returncode != 0:
                # A concurrent MCP connection may have installed the same owned task.
                found = run("/Query", "/TN", task, "/XML")
                if found.returncode != 0 or not matches(found.stdout):
                    raise RuntimeError("Cannot register demand-only engine task; see Windows Task Scheduler permissions")
        finally:
            xml_path.unlink()
    started = run("/Run", "/TN", task)
    if started.returncode != 0:
        raise RuntimeError("Windows Task Scheduler could not start the owned GIS engine")
    return {"strategy": "windows_demand_task", "task_name": task, "data_dir": str(data_dir)}
