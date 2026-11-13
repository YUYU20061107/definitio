"""Separate QGIS/GDAL reference process. Never imports the Definitio engine."""
from __future__ import annotations

from collections import Counter
import copy
import hashlib
import json
import math
from pathlib import Path
import sys
import tempfile

import numpy as np
from osgeo import gdal, ogr, osr
from qgis.core import Qgis, QgsApplication, QgsGeometry

gdal.UseExceptions(); ogr.UseExceptions(); osr.UseExceptions()
ABS, REL, COORD = 1e-8, 1e-9, 1e-7


def srs(crs):
    s = osr.SpatialReference()
    s.SetFromUserInput(crs["wkt"])
    s.SetAxisMappingStrategy(osr.OAMS_TRADITIONAL_GIS_ORDER)
    return s


def layer(d, name="source", driver="Memory", path=""):
    ds = ogr.GetDriverByName(driver).CreateDataSource(path)
    lyr = ds.CreateLayer(name, srs(d["crs"]), ogr.wkbUnknown)
    for f in d["fields"]:
        field = ogr.FieldDefn(f["name"], {"string": ogr.OFTString, "integer": ogr.OFTInteger64, "number": ogr.OFTReal, "boolean": ogr.OFTInteger}[f["type"]])
        field.SetNullable(f["nullable"]); lyr.CreateField(field)
    for f in d["geojson"]["features"]:
        row = ogr.Feature(lyr.GetLayerDefn())
        if f["geometry"] is not None: row.SetGeometry(ogr.CreateGeometryFromJson(json.dumps(f["geometry"])))
        for key, value in f["properties"].items():
            if value is None: row.SetFieldNull(key)
            else: row.SetField(key, value)
        lyr.CreateFeature(row)
    return ds, lyr


def rows(lyr):
    lyr.ResetReading()
    result = []
    for f in lyr:
        g = f.GetGeometryRef()
        result.append({"geometry": json.loads(g.ExportToJson()) if g else None,
                       "properties": {f.GetFieldDefnRef(i).GetName(): f.GetField(i) for i in range(f.GetFieldCount())}})
    return result


def same_geometry(a, b):
    if a is None or b is None: return a is None and b is None
    ga = QgsGeometry.fromWkt(ogr.CreateGeometryFromJson(json.dumps(a)).ExportToWkt())
    gb = QgsGeometry.fromWkt(ogr.CreateGeometryFromJson(json.dumps(b)).ExportToWkt())
    return ga.isGeosEqual(gb) or ga.isEmpty() and gb.isEmpty() or 0 <= ga.hausdorffDistance(gb) <= COORD


def same_value(a, b):
    if a is None or b is None: return a is None and b is None
    if type(a) in (int, float) and type(b) in (int, float):
        return math.isfinite(a) and math.isfinite(b) and abs(a-b) <= ABS+REL*max(abs(a), abs(b))
    return type(a) == type(b) and a == b


def vector_compare(actual, expected):
    unmatched = list(expected)
    for f in actual["geojson"]["features"]:
        match = next((i for i, e in enumerate(unmatched) if set(f["properties"]) == set(e["properties"]) and
                      all(same_value(f["properties"][k], e["properties"][k]) for k in f["properties"]) and
                      same_geometry(f["geometry"], e["geometry"])), None)
        if match is None: return False
        unmatched.pop(match)
    return not unmatched


def mem_raster(d):
    r = d["raster"]
    dtype = {"float32": gdal.GDT_Float32, "float64": gdal.GDT_Float64, "int32": gdal.GDT_Int32, "uint8": gdal.GDT_Byte}[r["dtype"]]
    data = np.array([[np.nan if v is None else v for v in row] for row in r["data"]], dtype="float64")
    ds = gdal.GetDriverByName("MEM").Create("", data.shape[1], data.shape[0], 1, dtype)
    a,b,c,d0,e,f = r["transform"]
    ds.SetGeoTransform([c,a,b,f,d0,e]); ds.SetProjection(d["crs"]["wkt"])
    # Use GDAL's mask, not a valid-value sentinel, so a valid zero stays valid.
    band = ds.GetRasterBand(1)
    if np.isnan(data).any():
        if r["dtype"] in ("float32", "float64"):
            band.SetNoDataValue(float("nan"))
        else:
            band.CreateMaskBand(0); band.GetMaskBand().WriteArray(np.where(np.isnan(data),0,255).astype("uint8"))
    band.WriteArray(data if r["dtype"].startswith("float") else np.nan_to_num(data))
    return ds


def read_masked(ds):
    band = ds.GetRasterBand(1); data = band.ReadAsArray().astype("float64")
    mask = band.GetMaskBand().ReadAsArray() == 0
    no = band.GetNoDataValue()
    if no is not None: mask |= np.isnan(data) if math.isnan(no) else data == no
    data[mask] = np.nan
    return data


def terrain(c):
    p, d = c["parameters"], c["inputs"][0]
    src = mem_raster(d); v = c["verb"].removeprefix("gis_")
    options = {"format": "MEM", "alg": "Horn", "computeEdges": False}
    if v == "slope": options.update(scale=1/p["z_factor"], slopeFormat="degree")
    if v == "hillshade": options.update(zFactor=p["z_factor"], scale=1, azimuth=p["azimuth"], altitude=p["altitude"])
    result = gdal.DEMProcessing("", src, v, **options)
    return read_masked(result), "GDAL DEMProcessing Horn, explicit scales and masked edges"


def expected_raster(c):
    v, p, ds = c["verb"], c["parameters"], c["inputs"]
    if v in ("gis_slope", "gis_aspect", "gis_hillshade"): return terrain(c)
    if v == "gis_raster_calc":
        from osgeo_utils import gdal_calc
        bindings = {chr(65+i): mem_raster(d) for i,d in enumerate(ds)}
        with tempfile.TemporaryDirectory(prefix="gdal-calc-reference-") as directory:
            result = gdal_calc.Calc(calc=p["expression"], outfile=str(Path(directory)/"calc.tif"), type="Float64", NoDataValue=-1.7e308, quiet=True, **bindings)
            pixels = read_masked(result)
            result.Close(); result = None
            return pixels, "osgeo_utils.gdal_calc.Calc Float64 with NoData propagation"
    sys.path.append(str(Path(QgsApplication.prefixPath())/"python/plugins"))
    import processing
    from processing.core.Processing import Processing
    Processing.initialize()
    with tempfile.TemporaryDirectory(prefix="qgis-reclass-reference-") as directory:
        source = str(Path(directory)/"source.tif"); dest = str(Path(directory)/"result.tif")
        src = mem_raster(ds[0]); gdal.GetDriverByName("GTiff").CreateCopy(source, src)
        # Main equivalence branch: min <= value < max. Other interval policies are regression-only.
        assert all(r["min_inclusive"] and not r["max_inclusive"] for r in p["rules"])
        table = [n for r in p["rules"] for n in (r["min"], r["max"], r["value"])]
        processing.run("native:reclassifybytable", {"INPUT_RASTER": source, "RASTER_BAND": 1, "TABLE": table,
                       "NO_DATA": -9999, "RANGE_BOUNDARIES": 1, "NODATA_FOR_MISSING": p["unmapped"] == "nodata", "DATA_TYPE": 6, "OUTPUT": dest})
        result = gdal.Open(dest)
        pixels = read_masked(result)
        result.Close(); result = None
        return pixels, "QGIS native:reclassifybytable min<=v<max"


def expected_vector(c):
    v, p, ds = c["verb"], c["parameters"], c["inputs"]
    if v == "gis_run_processing":
        result=[]
        for feature in ds[0]["geojson"]["features"]:
            g=ogr.CreateGeometryFromJson(json.dumps(feature["geometry"]))
            parts=[g.GetGeometryRef(i).Clone() for i in range(g.GetGeometryCount())] if p["params"]["ALL_PARTS"] and g.GetGeometryType() in (ogr.wkbMultiPolygon,ogr.wkbMultiLineString,ogr.wkbMultiPoint) else [g]
            for part in parts:result.append({"geometry":json.loads(part.Centroid().ExportToJson()),"properties":feature["properties"]})
        return result,"OGR centroid on identical input snapshot; installed QGIS Processing output compared"
    if v == "gis_spatial_join":
        left, right = ds
        names = {f["name"] for f in left["fields"]}
        field_map = {f["name"]: "join_"+f["name"] if f["name"] in names else f["name"] for f in right["fields"]}
        result = []
        for f in left["geojson"]["features"]:
            g = QgsGeometry.fromWkt(ogr.CreateGeometryFromJson(json.dumps(f["geometry"])).ExportToWkt()); pairs=[]
            for row in right["geojson"]["features"]:
                h = QgsGeometry.fromWkt(ogr.CreateGeometryFromJson(json.dumps(row["geometry"])).ExportToWkt())
                if p["predicate"] == "nearest": pairs.append((g.distance(h), row))
                elif getattr(g,p["predicate"])(h):
                    cross = g.intersection(h); pairs.append(((-cross.area(),-cross.length()),row))
            if p["predicate"] == "nearest" and pairs: pairs = [x for x in pairs if x[0] == min(t[0] for t in pairs)]
            if p["summary"] == "first" and pairs: pairs=sorted(pairs,key=lambda x:x[0])[:1]
            for _,row in pairs or [(None,None)]:
                result.append({"geometry":f["geometry"],"properties":{**f["properties"],**{new:row["properties"][old] if row else None for old,new in field_map.items()}}})
        return result,"PyQGIS exact predicates/intersection/left join"
    if v == "gis_zonal_statistics":
        zones, values=ds; src=mem_raster(values); data=read_masked(src); result=[]
        for f in zones["geojson"]["features"]:
            one={**zones,"geojson":{"features":[f]}}
            vector_ds,lyr=layer(one)
            mask=gdal.GetDriverByName("MEM").Create("",src.RasterXSize,src.RasterYSize,1,gdal.GDT_Byte)
            mask.SetGeoTransform(src.GetGeoTransform());mask.SetProjection(src.GetProjection())
            gdal.RasterizeLayer(mask,[1],lyr,burn_values=[1],options=["ALL_TOUCHED=FALSE"])
            pixels=data[(mask.ReadAsArray()==1)&np.isfinite(data)]
            props={p["zone_field"]:f["properties"][p["zone_field"]]}
            for stat in p["statistics"]:
                props[stat]=int(pixels.size) if stat=="count" else float({"sum":np.sum,"mean":np.mean,"min":np.min,"max":np.max}[stat](pixels)) if pixels.size else None
            result.append({"geometry":None,"properties":props})
        return result,"GDAL RasterizeLayer pixel centers + explicit numeric reductions"
    d=ds[0]
    if v=="gis_import_data":
        raw=copy.deepcopy(d["geojson"]);raw["crs"]={"type":"name","properties":{"name":d["crs"]["code"]}}
        path="/vsimem/completion-import.geojson";gdal.FileFromMemBuffer(path,json.dumps(raw).encode())
        src=ogr.Open(path); result=rows(src.GetLayer(0));src=None;gdal.Unlink(path)
        return result,"OGR GeoJSON fixed-byte import"
    if v=="gis_rename_dataset":
        with tempfile.TemporaryDirectory(prefix="ogr-rename-reference-") as directory:
            src,lyr=layer(d,"original","GPKG",str(Path(directory)/"ref.gpkg"))
            src.ExecuteSQL('ALTER TABLE "original" RENAME TO "renamed"')
            lyr=src.GetLayerByName("renamed")
            assert lyr is not None
            result = rows(lyr)
            lyr = None; src = None
            return result,"OGR GeoPackage SQL logical table rename"
    src,lyr=layer(ds[1] if v=="gis_append_dataset" else d)
    if v=="gis_delete_field":
        for name in p["fields"]:lyr.DeleteField(lyr.GetLayerDefn().GetFieldIndex(name))
    elif v=="gis_append_dataset":
        additional,other=layer(d)
        for f in other:
            row=ogr.Feature(lyr.GetLayerDefn());row.SetFrom(f);lyr.CreateFeature(row)
    return rows(lyr),"OGR DeleteField" if v=="gis_delete_field" else "OGR feature append into existing layer"


def main():
    app=QgsApplication([],False);app.initQgis()
    input_path, output_path=map(Path,sys.argv[1:3]);document=json.loads(input_path.read_text(encoding="utf-8"));records=[]
    for c in document["cases"]:
        record={"id":c["id"],"verb":c["verb"],"wave":c["wave"]}
        try:
            actual=c["actual"]
            if c.get("expected_error"):
                passed=not actual["ok"] and actual["error"]["code"] in c["expected_error"]
                record.update(status="PASS_DOMAIN_REJECTION" if passed else "FAIL",expected_errors=c["expected_error"],actual_error=actual.get("error"),method="declared local domain gate; not computational equivalence")
            elif not actual["ok"]:
                record.update(status="FAIL",actual_error=actual["error"])
            elif c["verb"] in ("gis_raster_calc","gis_slope","gis_aspect","gis_hillshade","gis_reclassify"):
                expected,method=expected_raster(c);observed=np.array([[np.nan if v is None else v for v in row] for row in actual["output"]["raster"]["data"]])
                mask=np.isnan(expected);mask_diff=np.isnan(observed)!=mask
                errors=np.abs(observed-expected)
                continuous = document.get("continuous_raster_contract_tolerance", False) and c["verb"] in ("gis_raster_calc","gis_slope","gis_aspect","gis_hillshade")
                allowed=np.maximum(1e-6,1e-6*np.maximum(np.abs(observed),np.abs(expected))) if continuous else ABS+REL*np.maximum(np.abs(observed),np.abs(expected))
                if c["verb"]=="gis_aspect":errors=np.minimum(errors,360-errors)
                bad=(errors>allowed)&~mask
                positions=np.argwhere(mask_diff|bad).tolist()
                record.update(status="PASS_EQUIVALENT" if not positions else "FAIL",method=method,differing_pixel_count=len(positions),differing_pixel_positions=positions,
                              max_abs_error=float(np.nanmax(errors)) if np.isfinite(errors).any() else 0,
                              expected_data=[[None if np.isnan(v) else float(v) for v in row] for row in expected])
            else:
                expected,method=expected_vector(c)
                passed=vector_compare(actual["output"],expected)
                record.update(status="PASS_EQUIVALENT" if passed else "FAIL",method=method,expected_records=expected)
        except Exception as exc:
            record.update(status="FAIL",reference_exception=repr(exc))
        records.append(record)
    counts=dict(Counter(r["status"] for r in records));report={"status":"FAIL" if counts.get("FAIL") else "PASS", "total":len(records),"counts":counts,
        "reference":{"qgis":Qgis.QGIS_VERSION,"gdal":gdal.VersionInfo("RELEASE_NAME")},"tolerances":{"coordinate_map_units_abs":COORD,"numeric_abs":ABS,"numeric_rel":REL},
        "input_sha256":hashlib.sha256(input_path.read_bytes()).hexdigest(),"continuous_raster_contract_tolerance": document.get("continuous_raster_contract_tolerance", False),"cases":records}
    output_path.write_text(json.dumps(report,ensure_ascii=False,indent=2,allow_nan=False),encoding="utf-8")
    print(json.dumps({k:v for k,v in report.items() if k!="cases"}));app.exitQgis()
    return 1 if counts.get("FAIL") else 0


if __name__=="__main__":raise SystemExit(main())
