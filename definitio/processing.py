"""A deliberately small registered QGIS Processing channel, not a named-verb bypass."""
from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import tempfile

from .model import GISFault, require

SPECS = {"gis_run_processing": ("V02", "QGIS 注册算法通道", "R1", ["algorithm", "params"], [])}
SEEDS = {"native:centroids"}
NAMED = {"native:buffer", "native:clip", "native:dissolve", "native:intersection", "native:difference", "native:joinattributesbylocation"}


def validate_channel(p):
    algorithm = p["algorithm"]
    require(not any(word in algorithm.casefold() for word in ("executesql", "atlas", "inplace", "delete", "drop")), "DEFER_BLOCKED", "破坏/SQL/批量图集/原地类算法不开放总通道")
    require(algorithm not in NAMED, "NOT_SUPPORTED", "该算法须走已有具名 GIS 动词，不能借总通道绕过治理")
    require(algorithm in SEEDS, "NOT_SUPPORTED", "算法不在已公开的本地种子白名单", seeds=sorted(SEEDS))


def compute(p, dataset):
    from .operations import geometries
    require(dataset.get("kind", "vector") == "vector", "NOT_SUPPORTED", "当前种子只接受二维矢量")
    geometries(dataset)
    python = Path(os.environ.get("DEFINITIO_QGIS_PYTHON", r"C:\Program Files\QGIS 3.44.15\bin\python-qgis-ltr.bat"))
    require(python.is_file(), "NOT_SUPPORTED", "未找到已配置的 QGIS Python")
    with tempfile.TemporaryDirectory(prefix="definitio-qgis-processing-") as directory:
        input_path, output_path = Path(directory)/"input.json", Path(directory)/"output.json"
        input_path.write_text(json.dumps({"dataset": dataset, "algorithm": p["algorithm"], "all_parts": p["params"]["ALL_PARTS"]}), encoding="utf-8")
        env = {k:v for k,v in os.environ.items() if k not in ("PROJ_DATA", "PROJ_LIB", "GDAL_DATA", "PYTHONPATH")}
        env["QT_QPA_PLATFORM"]="offscreen"; env["PYTHONUTF8"]="1"
        try:
            r = subprocess.run([str(python), str(Path(__file__).parent.parent/"scripts/qgis_processing_worker.py"), str(input_path), str(output_path)],
                               env=env, capture_output=True, timeout=90, creationflags=getattr(subprocess,"CREATE_NO_WINDOW",0))
        except subprocess.TimeoutExpired as exc:
            raise GISFault("BACKEND_ERROR", "QGIS 超出 90 秒预算，未提交成果") from exc
        require(r.returncode == 0 and output_path.exists(), "BACKEND_ERROR", "QGIS Processing 失败，未提交成果", returncode=r.returncode, stderr=r.stderr.decode("utf-8",errors="replace")[-2000:])
        output = json.loads(output_path.read_text(encoding="utf-8"))
    return {"geojson": output["geojson"], "schema": dataset["fields"], "crs": dataset["crs"],
            "algorithm": p["algorithm"], "effective_algorithm_parameters": p["params"],
            "implementation_route": "installed_qgis_processing_seed", "backend_versions": output["versions"],
            "result_empty": not output["geojson"]["features"], "warnings": ["Only the published local seed is implemented; no general arbitrary Processing execution"]}
