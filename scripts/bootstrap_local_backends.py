"""Create private loopback GIS backends; never attach to the installed PG service.

Secrets stay in data/backends.json (excluded from source distributions). Re-running
reuses the same cluster/configuration. Only the private cluster may be stopped.
"""
from pathlib import Path
import json
import os
import secrets
import shutil
import subprocess
import sys
import time
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
BASE = DATA / "backends"
CONFIG = DATA / "backends.json"
PG = Path(r"C:\Program Files\PostgreSQL\16\bin")
GS = Path(r"C:\Program Files\GeoServer")
JAVA = Path(r"C:\Program Files\Eclipse Adoptium\jre-17.0.20.101-hotspot\bin\java.exe")
FLAGS = getattr(subprocess, "CREATE_NO_WINDOW", 0)


def run(args, **kwargs):
    r = subprocess.run([str(a) for a in args], capture_output=True, creationflags=FLAGS, **kwargs)
    if r.returncode:
        raise RuntimeError(r.stderr.decode(errors="replace")[-2000:])
    return r


def main():
    BASE.mkdir(parents=True, exist_ok=True)
    if CONFIG.exists():
        cfg = json.loads(CONFIG.read_text(encoding="utf-8"))
    else:
        cfg = {"postgis": {"host": "127.0.0.1", "port": 25432, "dbname": "postgres", "user": "definitio", "password": secrets.token_urlsafe(24)},
               "geoserver": {"url": "http://127.0.0.1:18980/geoserver", "user": "admin", "password": secrets.token_urlsafe(24), "allow_publish": True, "workspace_prefix": "definitio_", "sandbox": True},
               "sensorthings": {"url": "https://istsos.org/v4/v1.1/", "origin": "official istSOS4 read-only demonstration"}}
        CONFIG.write_text(json.dumps(cfg, indent=2), encoding="utf-8")
    cluster = BASE / "postgres"
    if not (cluster / "PG_VERSION").exists():
        pw = BASE / "init-password.private"
        pw.write_text(cfg["postgis"]["password"], encoding="ascii")
        run([PG / "initdb.exe", "-D", cluster, "-U", "definitio", "--auth=scram-sha-256", "--encoding=UTF8", "--locale=C", "--pwfile", pw], timeout=90)
        # Keep the initial password file private alongside the private config.
        with (cluster / "postgresql.conf").open("a", encoding="utf-8") as f:
            f.write("\nlisten_addresses='127.0.0.1'\nport=25432\nshared_buffers='16MB'\nmax_connections=12\nwork_mem='2MB'\n")
    status = subprocess.run([str(PG / "pg_ctl.exe"), "-D", str(cluster), "status"], capture_output=True, creationflags=FLAGS)
    if status.returncode:
        # Windows grandchildren can retain PIPE handles after pg_ctl exits.
        with (BASE / "pg_ctl.log").open("ab") as log:
            subprocess.run([str(PG / "pg_ctl.exe"), "-D", str(cluster), "-l", str(BASE / "postgres.log"), "-w", "start"], stdout=log, stderr=log, check=True, timeout=60, creationflags=FLAGS)
    env = {**os.environ, "PGPASSWORD": cfg["postgis"]["password"]}
    r = run([PG / "psql.exe", "-h", "127.0.0.1", "-p", "25432", "-U", "definitio", "-d", "postgres", "-v", "ON_ERROR_STOP=1", "-Atc", "CREATE EXTENSION IF NOT EXISTS postgis; SELECT postgis_full_version();"], env=env, timeout=60)
    gsbase, gsdata = BASE / "geoserver-base", BASE / "geoserver-data"
    gsbase.mkdir(exist_ok=True)
    if not gsdata.exists():
        gsdata.mkdir()
        for xml in (GS / "data_dir").glob("*.xml"):
            shutil.copy2(xml, gsdata / xml.name)
        for name in ("security", "styles"):
            shutil.copytree(GS / "data_dir" / name, gsdata / name)
        (gsdata / "workspaces").mkdir()
        import xml.etree.ElementTree as ET
        users = gsdata / "security/usergroup/default/users.xml"
        tree = ET.parse(users)
        for user in tree.getroot().iter():
            if user.tag.split("}")[-1] != "user":
                continue
            if user.get("name") == "admin":
                user.set("password", "plain:" + cfg["geoserver"]["password"])
        tree.write(users, encoding="utf-8", xml_declaration=True)
    shutil.copy2(GS / "start.ini", gsbase / "start.ini")
    webapps = gsbase / "webapps"
    webapps.mkdir(exist_ok=True)
    (webapps / "geoserver.xml").write_text('''<?xml version="1.0"?>
<!DOCTYPE Configure PUBLIC "-//Jetty//Configure//EN" "https://jetty.org/configure_10_0.dtd">
<Configure class="org.eclipse.jetty.ee11.webapp.WebAppContext">
  <Set name="contextPath">/geoserver</Set>
  <Set name="war">C:/Program Files/GeoServer/webapps/geoserver</Set>
</Configure>''', encoding="utf-8")
    request = urllib.request.Request(cfg["geoserver"]["url"] + "/web/")
    try:
        urllib.request.urlopen(request, timeout=3).close()
        running = True
    except Exception:
        running = False
    if not running:
        tmp = BASE / "java-tmp"
        tmp.mkdir(exist_ok=True)
        args = [str(JAVA), "-Xms64m", "-Xmx256m", "-Djava.awt.headless=true", f"-Djetty.home={GS}", f"-Djetty.base={gsbase}", f"-DGEOSERVER_DATA_DIR={gsdata}", f"-Djava.io.tmpdir={tmp}", "-jar", str(GS / "start.jar"), "jetty.http.host=127.0.0.1", "jetty.http.port=18980", "jetty.threadPool.minThreads=4", "jetty.threadPool.maxThreads=32", "jetty.http.acceptors=1", "jetty.http.selectors=1"]
        with (BASE / "geoserver.log").open("ab") as log:
            process = subprocess.Popen(args, cwd=gsbase, stdout=log, stderr=log, creationflags=FLAGS)
        (BASE / "geoserver-pid.json").write_text(json.dumps({"pid": process.pid, "args": args}), encoding="utf-8")
    print(json.dumps({"config": str(CONFIG), "postgis": r.stdout.decode(errors="replace").strip(), "geoserver": "startup requested or already running", "ports": [25432, 18980], "credentials": "private file, not printed"}))


if __name__ == "__main__":
    main()
