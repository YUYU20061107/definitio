"""Read-only dependency checks for this runtime's declared backend routes."""
from __future__ import annotations

import base64
import importlib
from importlib.metadata import version
import json
import os
from pathlib import Path
import subprocess
import sys
from urllib.parse import urlparse
from urllib.request import Request, build_opener, ProxyHandler

EXTENSIONS = {
    "gis_run_gp": ["arcpy"], "gis_run_processing": ["qgis"],
    "gis_pointcloud_info": ["pdal"], "gis_pointcloud_process": ["pdal", "pdal_wrench"],
    "gis_serve_vector_tile": ["qgis", "postgis"], "gis_publish_layer": ["qgis", "geoserver"],
    "gis_list_services": ["geoserver OR sensorthings"], "gis_query_observations": ["sensorthings"],
    "gis_add_layer_from_catalog": ["qgis"], "gis_export_map_image": ["qgis"],
}


def dependency_info(verb):
    return {"dependency_tier": "extension" if verb in EXTENSIONS else "core",
            "dependencies": ["core"] + EXTENSIONS.get(verb, [])}


def command_check(path, arguments, extra_path=None):
    if not path.is_file():
        return {"status": "MISSING", "path": str(path)}
    env = {k: v for k, v in os.environ.items() if k not in ("PROJ_LIB", "PROJ_DATA", "GDAL_DATA", "PYTHONPATH")}
    env.update(QT_QPA_PLATFORM="offscreen", PYTHONUTF8="1")
    if extra_path is not None:
        env["PATH"] = str(extra_path) + os.pathsep + env.get("PATH", "")
    try:
        result = subprocess.run([str(path), *arguments], capture_output=True, timeout=25, env=env,
                                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except (OSError, subprocess.TimeoutExpired) as exc:
        return {"status": "UNAVAILABLE", "path": str(path), "reason": type(exc).__name__}
    # Do not echo native stderr: it may contain private backend configuration.
    return {"status": "AVAILABLE" if result.returncode == 0 else "UNAVAILABLE", "path": str(path),
            "version": result.stdout.decode("utf-8", errors="replace").strip()[:500] if result.returncode == 0 else None,
            "returncode": result.returncode}


def inspect_environment(data_dir, profile="core"):
    checks = {}
    for module, package in [(n, n) for n in ("fastapi", "uvicorn", "shapely", "rasterio", "httpx", "mcp", "jsonschema", "numpy", "affine")]:
        try:
            importlib.import_module("definitio.model" if module == "rasterio" else module)
            checks[package] = {"status": "AVAILABLE", "version": version(package)}
        except (ImportError, OSError) as exc:
            checks[package] = {"status": "MISSING", "reason": type(exc).__name__}
    core_ok = all(item["status"] == "AVAILABLE" for item in checks.values())
    if core_ok:
        try:
            # Import through the owner so its bundled PROJ database is selected.
            from .model import GISFault, crs_info
            from .runtime import Runtime
            crs_info("EPSG:32649")
            import shapely, rasterio
            checks["geos_gdal_proj"] = {"status": "AVAILABLE", "geos": shapely.geos_version_string, "gdal": rasterio.__gdal_version__}
        except (ImportError, OSError, RuntimeError, ValueError, GISFault) as exc:
            core_ok = False
            checks["geos_gdal_proj"] = {"status": "UNAVAILABLE", "reason": type(exc).__name__}
    qgis = Path(os.environ.get("DEFINITIO_QGIS_PYTHON", r"C:\Program Files\QGIS 3.44.15\bin\python-qgis-ltr.bat"))
    arcpy = Path(os.environ.get("DEFINITIO_ARCPY_PYTHON", r"C:\Python27\ArcGIS10.8\python.exe"))
    pdal = Path(os.environ.get("DEFINITIO_PDAL", r"C:\Program Files\QGIS 3.44.15\bin\pdal.exe"))
    wrench = Path(os.environ.get("DEFINITIO_PDAL_WRENCH", str(pdal.parent.parent / "apps/qgis-ltr/pdal_wrench.exe")))
    probes = {
        "qgis": (qgis, ["-c", "from qgis.core import Qgis; print(Qgis.QGIS_VERSION)"]),
        "arcpy": (arcpy, ["-c", "import arcpy; p=arcpy.ProductInfo(); print(arcpy.GetInstallInfo()['Version']); print(p); assert p not in ('NotInitialized','Unavailable')"]),
        "pdal": (pdal, ["--version"]), "pdal_wrench": (wrench, ["--help"]),
    }
    for name, (path, args) in probes.items():
        checks[name] = command_check(path, args, pdal.parent if name == "pdal_wrench" else None) if profile == "full" else {
            "status": "PRESENT_UNVERIFIED" if path.is_file() else "MISSING", "path": str(path)}
        if name == "pdal_wrench" and profile == "full":
            checks[name]["availability_probe"] = "--help (this binary has no --version option)"
            checks[name]["help_header"] = checks[name].pop("version", None)
            checks[name]["version"] = None
    config_path = Path(os.environ.get("DEFINITIO_BACKEND_CONFIG", str(Path(data_dir) / "backends.json")))
    config = {}
    if config_path.is_file():
        try:
            config = json.loads(config_path.read_text(encoding="utf-8"))
        except (ValueError, OSError):
            checks["backend_config"] = {"status": "INVALID", "path": str(config_path)}
    for name in ("postgis", "geoserver", "sensorthings"):
        checks[name] = {"status": "CONFIGURED_UNVERIFIED" if name in config else "NOT_CONFIGURED"}
    if profile == "full" and "postgis" in config:
        c = config["postgis"]
        if c.get("host") not in ("127.0.0.1", "localhost", "::1"):
            checks["postgis"] = {"status": "NOT_CHECKED", "reason": "Only loopback services are probed"}
        else:
            # QGIS is also the owning runtime for the MVT worker's psycopg2.
            code = "import json,psycopg2; c=json.loads(__import__('sys').stdin.read()); c['connect_timeout']=3; db=psycopg2.connect(**c); q=db.cursor(); q.execute('SELECT PostGIS_Full_Version()'); print(q.fetchone()[0]); db.close()"
            if not qgis.is_file():
                checks["postgis"] = {"status": "UNAVAILABLE", "reason": "Configured MVT worker requires QGIS Python"}
            else:
                try:
                    r = subprocess.run([str(qgis), "-c", code], input=json.dumps(c).encode(), capture_output=True,
                                       timeout=25, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
                    checks["postgis"] = {"status": "AVAILABLE" if r.returncode == 0 else "UNAVAILABLE",
                                         "version": r.stdout.decode(errors="replace").strip()[:500] if r.returncode == 0 else None}
                except (OSError, subprocess.TimeoutExpired) as exc:
                    checks["postgis"] = {"status": "UNAVAILABLE", "reason": type(exc).__name__}
    if profile == "full" and "geoserver" in config:
        c = config["geoserver"]
        parsed = urlparse(c.get("url", ""))
        if parsed.hostname not in ("localhost", "127.0.0.1", "::1") or parsed.scheme not in ("http", "https"):
            checks["geoserver"] = {"status": "NOT_CHECKED", "reason": "Only loopback services are probed"}
        else:
            credential = base64.b64encode((c.get("user", "") + ":" + c.get("password", "")).encode()).decode()
            request = Request(c["url"].rstrip("/") + "/rest/about/version.json", headers={"Authorization": "Basic " + credential})
            try:
                with build_opener(ProxyHandler({})).open(request, timeout=3) as response:
                    doc = json.load(response)
                checks["geoserver"] = {"status": "AVAILABLE", "version": doc.get("about", {})}
            except (OSError, ValueError) as exc:
                checks["geoserver"] = {"status": "UNAVAILABLE", "reason": type(exc).__name__}
    required_extensions = ("qgis", "arcpy", "pdal", "pdal_wrench", "postgis", "geoserver")
    ok = core_ok and (profile == "core" or all(checks[n]["status"] == "AVAILABLE" for n in required_extensions))
    catalog = json.loads((Path(__file__).parent / "catalog.json").read_text(encoding="utf-8"))
    return {"status": "PASS" if ok else "FAIL", "profile": profile, "python": sys.executable, "checks": checks,
            "verbs": [{"name": v["name"], **dependency_info(v["name"])} for v in catalog["verbs"]],
            "scope": "Read-only local checks; core profile does not launch native backends; no remote service probes or installation",
            "certification": "NOT_CERTIFIED"}
