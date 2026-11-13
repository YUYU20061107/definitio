"""Inspect the approved installed engine with a tiny synthetic LAS fixture."""
from pathlib import Path
import json
import os
import subprocess

ROOT=Path(__file__).resolve().parents[1]
QGIS=Path(r"C:\Program Files\QGIS 3.44.15")
env={k:v for k,v in os.environ.items() if k not in ("PROJ_DATA","PROJ_LIB","GDAL_DATA","PYTHONPATH")}
env["PATH"]=str(QGIS/"bin")+os.pathsep+env["PATH"]
env["PROJ_DATA"]=str(QGIS/"share/proj")
directory=ROOT/"evidence/w3-probe"
directory.mkdir(exist_ok=False)
csv=directory/"points.csv"
csv.write_text("X,Y,Z,Intensity,Classification\n"+"\n".join(f"{500000+c},{2500000+r},{10+c+r},{100+c},{2 if r%2 else 1}" for r in range(7) for c in range(7)),encoding="utf-8")
pipeline={"pipeline":[{"type":"readers.text","filename":str(csv)}, {"type":"writers.las","filename":str(directory/"points.las"),"a_srs":"EPSG:32649","minor_version":4,"dataformat_id":3,"scale_x":0.001,"scale_y":0.001,"scale_z":0.001,"offset_x":500000,"offset_y":2500000,"offset_z":0}]}
results={}
for label,command,input_text in [
    ("create",[str(QGIS/"bin/pdal.exe"),"pipeline","--stdin"],json.dumps(pipeline)),
    ("info",[str(QGIS/"bin/pdal.exe"),"info","--metadata","--schema","--stats",str(directory/"points.las")],None),
    ("wrench",[str(QGIS/"apps/qgis-ltr/pdal_wrench.exe"),"--help"],None)]:
    p=subprocess.run(command,input=input_text,env=env,text=True,capture_output=True,timeout=30)
    results[label]={"command":command,"returncode":p.returncode,"stdout":p.stdout,"stderr":p.stderr}
(ROOT/"evidence/w3-probe-results.json").write_text(json.dumps(results,ensure_ascii=False,indent=2),encoding="utf-8")
print({k:v["returncode"] for k,v in results.items()})
if results["info"]["returncode"]==0:
    info=json.loads(results["info"]["stdout"])
    print('info keys',list(info));print('metadata keys',list(info.get('metadata',{})));print('schema',info.get('schema'))
