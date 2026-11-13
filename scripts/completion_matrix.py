"""Freeze real W1/W2 Agent outputs and invoke the separately installed reference."""
from __future__ import annotations

import argparse
import base64
import copy
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from shapely.geometry import box, mapping
from definitio.agent import AgentService
from definitio.runtime import Runtime


def collect(terrain_only=False, selected=None):
    records=[]
    with tempfile.TemporaryDirectory(prefix="definitio-completion-") as directory:
        service=AgentService(Runtime(Path(directory)/"matrix.sqlite3"))
        def vector(name, rows=None, crs=32649):
            rows=rows if rows is not None else [(box(0,0,3,3),{"key":"A","value":2.0}),(box(10,10,11,11),{"key":"B","value":None})]
            empty_schema = [{"name":"key","type":"string","nullable":False},{"name":"value","type":"number","nullable":True}] if not rows else None
            return service.runtime.import_dataset(name,{"type":"FeatureCollection","features":[{"type":"Feature","geometry":mapping(g),"properties":p} for g,p in rows]},crs,empty_schema)
        def raster(name,data=None,crs=32649,**changes):
            r={"data":data if data is not None else [[float(2*c+3*r+(r*c)%3) for c in range(7)] for r in range(7)],
               "transform":[2,0,500000,0,-3,2500000],"dtype":"float32","nodata":None,"band_name":"elevation",**changes}
            return service.runtime.import_raster(name,r,crs)
        serial=0
        def record(verb,label,inputs,params,expected=None):
            nonlocal serial
            if selected is not None and verb+"/"+label not in selected:
                return
            serial+=1
            target="completion_"+str(serial)
            params={**params}
            if verb not in ("gis_rename_dataset","gis_append_dataset","gis_import_data"):params["target"]=target
            if verb=="gis_import_data":params.setdefault("target_name",target)
            if verb=="gis_rename_dataset":params.setdefault("new_name",target)
            if verb=="gis_delete_field":
                params["target_echo"]=target
                pre=service.call("destructive_preflight",{"dataset":params["dataset"],"fields":params["fields"],"target":target})
                params["preflight_id"]=pre["data"]["preflight"]["preflight_id"] if pre["ok"] else "unavailable-preflight"
                if label=="wrong_echo":params["target_echo"]="different"
                if label=="unknown_preflight":params["preflight_id"]="missing"
            reply=pre if verb=="gis_delete_field" and not pre["ok"] else service.call(verb,{**params,"confirm":label!="unconfirmed","request_id":"matrix-"+str(serial)})
            actual={"ok":reply["ok"]}
            if reply["ok"]:
                actual["output"]=service.store.dataset(reply["data"]["outputs"][0]["id"])
            else:actual["error"]=reply["error"]
            wave="W2" if verb in ("gis_raster_calc","gis_slope","gis_aspect","gis_hillshade","gis_reclassify","gis_zonal_statistics") else "W1"
            records.append({"id":verb+"/"+label,"verb":verb,"wave":wave,"parameters":params,"inputs":inputs,"expected_error":expected,"actual":actual})
        if not terrain_only:
            src=vector("source")
            for label,fields,error in [("ordinary_field",["value"],None),("missing_field",["absent"],["SCHEMA_MISMATCH"]),("wrong_echo",["value"],["CONFIRM_REQUIRED"]),("unknown_preflight",["value"],["PREFLIGHT_REQUIRED"]),("unconfirmed",["value"],["CONFIRM_REQUIRED"])]:
                record("gis_delete_field",label,[src],{"dataset":src["id"],"fields":fields},error)
            record("gis_rename_dataset","logical_rename",[src],{"dataset":src["id"]})
            occupied=vector("occupied")
            record("gis_rename_dataset","occupied_name",[occupied],{"dataset":occupied["id"],"new_name":"occupied"},["ALREADY_EXISTS"])
            record("gis_rename_dataset","unconfirmed",[occupied],{"dataset":occupied["id"]},["CONFIRM_REQUIRED"])
            for label,rows,crs,error in [("append",None,32649,None),("empty_source",[],32649,None),("schema",[(box(0,0,1,1),{"other":1})],32649,["SCHEMA_MISMATCH"]),("crs",None,4326,["CRS_MISMATCH"])]:
                source=vector("append_source_"+label,rows,crs);target=vector("append_target_"+label)
                record("gis_append_dataset",label,[source,target],{"source":source["id"],"target":target["name"]},error)
            record("gis_append_dataset","self",[occupied,occupied],{"source":occupied["id"],"target":"occupied"},["INVALID_ARGUMENT"])
            for label,license_ok,error in [("fixed_uri",True,None),("license_missing",False,["LICENSE_BLOCKED"])]:
                document={**src["geojson"],"definitio":{"crs":src["crs"],"schema":src["fields"],"source_license":{"allow_import":license_ok,"evidence_ref":"self-authored-synthetic-fixture"}}}
                uri="data:application/geo+json;base64,"+base64.b64encode(json.dumps(document).encode()).decode()
                record("gis_import_data",label,[src],{"source_uri":uri,"format":"geojson","kind":"vector"},error)
            record("gis_import_data","remote_uri",[src],{"source_uri":"https://example.invalid/data.geojson","format":"geojson","kind":"vector"},["NOT_SUPPORTED"])
            join=vector("join",[(box(0,0,2,2),{"key":"large"}),(box(0,0,1,1),{"key":"small"})])
            for predicate in ["intersects","contains","within","nearest"]:
                for summary in ["first","all"]:
                    error=["SCHEMA_MISMATCH"] if predicate=="nearest" and summary=="first" else None
                    record("gis_spatial_join",predicate+"_"+summary,[src,join],{"input":src["id"],"join":join["id"],"predicate":predicate,"summary":summary},error)
            tie=vector("tie",[(box(0,0,1,1),{"key":"x"}),(box(0,0,1,1),{"key":"y"})])
            record("gis_spatial_join","unstable_tie",[src,tie],{"input":src["id"],"join":tie["id"],"predicate":"intersects","summary":"first"},["SCHEMA_MISMATCH"])
            other=vector("other_crs",crs=4326)
            record("gis_spatial_join","crs_mismatch",[src,other],{"input":src["id"],"join":other["id"],"predicate":"intersects","summary":"all"},["CRS_MISMATCH"])
        dem=raster("dem")
        flat=raster("flat",[[0.0]*7 for _ in range(7)])
        masked_data=copy.deepcopy(dem["raster"]["data"]);masked_data[3][3]=None
        masked=raster("masked",masked_data)
        empty=raster("all_nodata",[[None]*7 for _ in range(7)])
        foot=raster("foot",crs=2263)
        for verb,extra in [("gis_slope",{"unit":"degree"}),("gis_aspect",{"convention":"north_zero_clockwise","flat_policy":"nodata"}),("gis_hillshade",{"azimuth":315,"altitude":45})]:
            for label,d in [("nonlinear",dem),("flat",flat),("nodata_hole",masked),("all_nodata",empty),("projected_foot",foot)]:
                if verb == "gis_aspect":
                    d = raster("square_aspect_"+label, d["raster"]["data"], crs=2263 if label=="projected_foot" else 32649,
                               transform=[2,0,500000,0,-2,2500000])
                record(verb,label,[d],{"input":d["id"],"z_factor":2 if label=="projected_foot" else 1,"nodata_policy":"propagate",**extra})
            if verb == "gis_aspect":
                record(verb,"non_square_pixels",[dem],{"input":dem["id"],"z_factor":1,"nodata_policy":"propagate",**extra},["NOT_SUPPORTED"])
            geographic=raster("geographic_"+verb,crs=4326)
            record(verb,"geographic",[geographic],{"input":geographic["id"],"z_factor":1,"nodata_policy":"propagate",**extra},["NOT_SUPPORTED"])
            record(verb,"zero_z_factor",[dem],{"input":dem["id"],"z_factor":0,"nodata_policy":"propagate",**extra},["INVALID_ARGUMENT"])
        if not terrain_only:
            for label,ds,expr,error in [("one_input",[dem],"A*2+1",None),("two_inputs",[dem,masked],"A+B",None),("all_nodata",[empty],"A+1",None),("constant",[dem],"3",None),("divide_zero",[dem],"A/0",["INVALID_ARGUMENT"]),("code",[dem],"__import__('os')",["INVALID_ARGUMENT"])]:
                record("gis_raster_calc",label,ds,{"inputs":[d["id"] for d in ds],"expression":expr,"nodata_policy":"propagate"},error)
            shifted=raster("shifted",transform=[2,0,500001,0,-3,2500000])
            record("gis_raster_calc","grid_mismatch",[dem,shifted],{"inputs":[dem["id"],shifted["id"]],"expression":"A+B","nodata_policy":"propagate"},["SCHEMA_MISMATCH"])
            rules=[{"min":0,"max":10,"min_inclusive":True,"max_inclusive":False,"value":1},{"min":10,"max":100,"min_inclusive":True,"max_inclusive":False,"value":2}]
            for label,d,rs,unmapped,error in [("half_open",dem,rules,"fail",None),("masked",masked,rules,"fail",None),("all_nodata",empty,rules,"fail",None),("unmapped_nodata",dem,rules[:1],"nodata",None),("unmapped_fail",dem,rules[:1],"fail",["INVALID_ARGUMENT"]),("overlap",dem,[rules[0],{**rules[1],"min":9}],"fail",["INVALID_ARGUMENT"])]:
                record("gis_reclassify",label,[d],{"input":d["id"],"rules":rs,"unmapped":unmapped,"nodata_policy":"propagate"},error)
            zones=vector("zones",[(box(500000,2499979,500014,2500000),{"zone":"full"}),(box(500001,2499990,500007,2499999),{"zone":"partial"}),(box(0,0,1,1),{"zone":"outside"})])
            for label,d in [("pixel_centers",dem),("masked",masked),("no_valid_values",empty)]:
                record("gis_zonal_statistics",label,[zones,d],{"zones":zones["id"],"values":d["id"],"statistics":["count","sum","mean","min","max"],"output":"table","zone_field":"zone","nodata_policy":"exclude","pixel_rule":"center","weighting":"none"})
            record("gis_zonal_statistics","missing_zone_key",[zones,dem],{"zones":zones["id"],"values":dem["id"],"statistics":["count"],"output":"table","zone_field":"absent","nodata_policy":"exclude","pixel_rule":"center","weighting":"none"},["SCHEMA_MISMATCH"])
        return {"cases":records,"runtime":service.runtime.versions}


def main():
    p=argparse.ArgumentParser();p.add_argument("--output",type=Path,required=True);p.add_argument("--terrain-only",action="store_true");p.add_argument("--only-failed-from",type=Path);p.add_argument("--continuous-raster-contract-tolerance",action="store_true");p.add_argument("--qgis-python",type=Path,default=Path(r"C:\Program Files\QGIS 3.44.15\bin\python-qgis-ltr.bat"));a=p.parse_args()
    selected = {c["id"] for c in json.loads(a.only_failed_from.read_text(encoding="utf-8"))["cases"] if c["status"]=="FAIL"} if a.only_failed_from else None
    payload=collect(a.terrain_only, selected);payload["continuous_raster_contract_tolerance"]=a.continuous_raster_contract_tolerance
    inputs=a.output.with_suffix(".inputs.json");inputs.parent.mkdir(parents=True,exist_ok=True)
    if inputs.exists() or a.output.exists():raise FileExistsError("Evidence paths are append-only; choose a fresh run name")
    inputs.write_text(json.dumps(payload,ensure_ascii=False,indent=2),encoding="utf-8")
    env={k:v for k,v in os.environ.items() if k not in ("PROJ_DATA","PROJ_LIB","GDAL_DATA","PYTHONPATH")};env["QT_QPA_PLATFORM"]="offscreen";env["PYTHONUTF8"]="1"
    r=subprocess.run([str(a.qgis_python),str(ROOT/"scripts/completion_reference.py"),str(inputs.resolve()),str(a.output.resolve())],env=env,capture_output=True,timeout=180)
    a.output.with_suffix(".stdout.txt").write_bytes(r.stdout);a.output.with_suffix(".stderr.txt").write_bytes(r.stderr)
    if a.output.exists():
        report=json.loads(a.output.read_text(encoding="utf-8"));print(json.dumps({k:v for k,v in report.items() if k!="cases"},indent=2))
        print(json.dumps([x for x in report["cases"] if x["status"]=="FAIL"],ensure_ascii=False)[:7000])
    else:print(r.stderr.decode("utf-8",errors="replace")[-3000:])
    return r.returncode


if __name__=="__main__":raise SystemExit(main())
