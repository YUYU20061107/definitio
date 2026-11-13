"""Independent OGR computations and PyQGIS comparisons; no GISAgent imports."""
from __future__ import annotations

from collections import Counter
import copy
import hashlib
import json
from pathlib import Path
import sys

from osgeo import gdal, ogr, osr
from qgis.core import Qgis, QgsApplication, QgsCoordinateReferenceSystem, QgsCoordinateTransform, QgsGeometry, QgsProject

gdal.UseExceptions()
ogr.UseExceptions()
osr.UseExceptions()

# Fixed before execution; map-unit coordinate tolerance and numeric relative tolerance.
COORD_ABS = 1e-7
NUMBER_REL = 1e-9
NUMBER_ABS = 1e-8


def geom(value):
    return ogr.CreateGeometryFromJson(json.dumps(value)) if value is not None else None


def qgeom(g):
    return QgsGeometry.fromWkt(g.ExportToWkt()) if g is not None else QgsGeometry()


def srs(epsg):
    value = osr.SpatialReference()
    value.ImportFromEPSG(epsg)
    value.SetAxisMappingStrategy(osr.OAMS_TRADITIONAL_GIS_ORDER)
    return value


def same_geometry(actual, expected):
    if actual is None or expected is None:
        return actual is None and expected is None
    a, b = qgeom(geom(actual)), qgeom(expected)
    if a.isEmpty() and b.isEmpty():
        return True
    if a.isGeosEqual(b):
        return True
    distance = a.hausdorffDistance(b)
    return 0 <= distance <= COORD_ABS


def numeric(a, b):
    if a is None or b is None:
        return a is None and b is None
    return abs(a - b) <= NUMBER_ABS + NUMBER_REL * max(abs(a), abs(b))


def rejected_domain(c):
    v, kind, context = c["verb"], c["geometry"], c["context"]
    if context == "unknown":
        return {"UNKNOWN_CRS"}
    if v == "gis_create_dataset":
        return None
    if v == "gis_measure":
        if context == "geographic":
            return {"NOT_SUPPORTED"}
        return {"GEOMETRY_INVALID"} if kind in ("PointZ", "null_geometry", "invalid_polygon", "empty_polygon") else None
    if kind == "PointZ":
        return {"DIMENSION_MISMATCH"}
    if v in ("gis_buffer", "gis_clip", "gis_overlay", "gis_dissolve") and context == "geographic":
        return {"NOT_SUPPORTED"}
    if v in ("gis_field_statistics", "gis_validate_geometry", "gis_assign_projection"):
        return None
    if kind in ("null_geometry", "invalid_polygon"):
        return {"GEOMETRY_INVALID"}
    if v in ("gis_clip", "gis_overlay", "gis_dissolve") and kind not in ("Polygon", "MultiPolygon", "empty_polygon"):
        return {"NOT_SUPPORTED"}
    return None


def make_layer(c):
    ds = ogr.GetDriverByName("Memory").CreateDataSource("")
    layer = ds.CreateLayer("src", srs(c["epsg"]), ogr.wkbUnknown)
    for f in c["schema"]:
        field = ogr.FieldDefn(f["name"], {"string": ogr.OFTString, "number": ogr.OFTReal, "integer": ogr.OFTInteger64}[f["type"]])
        field.SetNullable(f["nullable"])
        layer.CreateField(field)
    for row in c["input"]["features"]:
        feature = ogr.Feature(layer.GetLayerDefn())
        for k, value in row["properties"].items():
            if value is not None:
                feature.SetField(k, value)
        g = geom(row["geometry"])
        if g is not None:
            feature.SetGeometry(g)
        layer.CreateFeature(feature)
    return ds, layer


def read_fields(layer):
    definition = layer.GetLayerDefn()
    return [{"name": definition.GetFieldDefn(i).GetName(),
             "type": {ogr.OFTString: "string", ogr.OFTReal: "number", ogr.OFTInteger64: "integer", ogr.OFTInteger: "integer"}[definition.GetFieldDefn(i).GetType()],
             "nullable": bool(definition.GetFieldDefn(i).IsNullable())} for i in range(definition.GetFieldCount())]


def compare_rows(actual, rows, qrows=None):
    features = sorted(actual["features"], key=lambda r: json.dumps(r["properties"], sort_keys=True))
    rows = sorted(rows, key=lambda r: json.dumps(r[0], sort_keys=True))
    assert len(features) == len(rows), ("row count", len(features), len(rows))
    for f, (props, g) in zip(features, rows):
        assert f["properties"] == props, ("properties", f["properties"], props)
        assert same_geometry(f["geometry"], g), ("geometry", f["geometry"], g.ExportToWkt() if g is not None else None)
    if qrows is not None:
        qrows = sorted(qrows, key=lambda r: json.dumps(r[0], sort_keys=True))
        for f, (props, g) in zip(features, qrows):
            if g.isNull():
                # QgsGeometry.buffer(POLYGON EMPTY) returns a null wrapper.
                # OGR retains a typed empty polygon. Keep this API difference explicit.
                assert geom(f["geometry"]).IsEmpty() and props == f["properties"]
                continue
            assert f["properties"] == props and same_geometry(f["geometry"], ogr.CreateGeometryFromWkt(g.asWkt())), "QGIS geometry"


def check(c):
    if c["kind"] == "discovery":
        assert c["actual"]["ok"]
        if c["verb"] == "gis_list_algorithms":
            assert c["actual"]["data"]["implemented"] == 32
            assert len(c["actual"]["data"]["verbs"]) == 40
        else:
            assert c["actual"]["data"]["capability"]["implemented"]
        return "PASS_CONTRACT", "Capability metadata; geometry/CRS dimensions not applicable"
    allowed = rejected_domain(c)
    if allowed:
        assert not c["actual"]["ok"] and c["actual"]["error"]["code"] in allowed, c["actual"]
        return "PASS_DOMAIN_REJECTION", "Declared unsupported/input boundary; not a geometry equivalence claim"
    assert c["actual"]["ok"], c["actual"]
    v, a = c["verb"], c["actual"]
    ds, layer = make_layer(c)
    schema = read_fields(layer)
    source = [(copy.deepcopy(f["properties"]), geom(f["geometry"])) for f in c["input"]["features"]]
    rows, qrows = source, None
    code = "EPSG:" + str(c["epsg"])
    unit = srs(c["epsg"]).GetLinearUnits()
    if v == "gis_create_dataset":
        rows = []
    elif v == "gis_add_field":
        field = ogr.FieldDefn("extra", ogr.OFTInteger64)
        field.SetNullable(False)
        layer.CreateField(field)
        schema = read_fields(layer)
        rows = [(p | {"extra": 7}, g) for p, g in source]
    elif v == "gis_calculate_field":
        result = ds.ExecuteSQL('SELECT seq * 2 AS calculated FROM src', dialect="SQLite")
        values = [f.GetField("calculated") for f in result]
        ds.ReleaseResultSet(result)
        rows = [(p | {"seq": n}, g) for (p, g), n in zip(source, values)]
    elif v == "gis_merge_datasets":
        rows = source + source
    elif v == "gis_assign_projection":
        code = "EPSG:4326"
    elif v == "gis_reproject":
        transform = osr.CoordinateTransformation(srs(c["epsg"]), srs(3857))
        qt = QgsCoordinateTransform(QgsCoordinateReferenceSystem(code), QgsCoordinateReferenceSystem("EPSG:3857"), QgsProject.instance())
        rows, qrows = [], []
        for p, g in source:
            out, qout = g.Clone(), qgeom(g)
            if not out.IsEmpty():
                assert out.Transform(transform) == 0
                qout.transform(qt)
            rows.append((p, out))
            qrows.append((p, qout))
        code = "EPSG:3857"
    elif v == "gis_buffer":
        distance = 1 / unit
        rows = [(p, g.Buffer(distance, 8)) for p, g in source]
        qrows = [(p, qgeom(g).buffer(distance, 8)) for p, g in source]
    elif v in ("gis_clip", "gis_overlay"):
        cut = geom(c["boundary"])
        rows, qrows = [], []
        for p, g in source:
            out = g.Intersection(cut) if v == "gis_clip" else g.Difference(cut)
            qout = qgeom(g).intersection(qgeom(cut)) if v == "gis_clip" else qgeom(g).difference(qgeom(cut))
            if not out.IsEmpty() and out.GetDimension() == 2:
                rows.append((p, out))
                qrows.append((p, qout))
    elif v == "gis_dissolve":
        schema = [f for f in schema if f["name"] == "group_code"]
        groups = {}
        for p, g in source:
            groups.setdefault(p["group_code"], []).append(g)
        rows, qrows = [], []
        for key, gs in groups.items():
            out = gs[0].Clone()
            for g in gs[1:]:
                out = out.Union(g)
            rows.append(({"group_code": key}, out))
            qrows.append(({"group_code": key}, QgsGeometry.unaryUnion([qgeom(g) for g in gs])))
    elif v == "gis_field_statistics":
        result = ds.ExecuteSQL('SELECT group_code, COUNT(value) AS n, COUNT(*)-COUNT(value) AS nc, SUM(value) AS s, AVG(value) AS a, MIN(value) AS lo, MAX(value) AS hi FROM src GROUP BY group_code', dialect="SQLite")
        expected = []
        for f in result:
            expected.append({"group": {"group_code": f.GetField("group_code")},
                             "fields": {"value": {k: f.GetField(alias) for k, alias in {"count": "n", "null_count": "nc", "sum": "s", "mean": "a", "min": "lo", "max": "hi"}.items()}}})
        ds.ReleaseResultSet(result)
        key = lambda r: json.dumps(r["group"], sort_keys=True)
        assert sorted(a["result"]["groups"], key=key) == sorted(expected, key=key), (a["result"], expected)
        return "PASS_EQUIVALENT", "OGR SQLite grouped aggregates; explicit null exclusion"
    elif v == "gis_spatial_filter":
        cut = geom(c["boundary"])
        rows = [(p, g) for p, g in source if g.Intersects(cut)]
        qrows = [(p, qgeom(g)) for p, g in source if qgeom(g).intersects(qgeom(cut))]
        assert a["result"]["feature_count"] == len(rows)
        compare_rows(a["result"]["geojson"], rows, qrows)
        return "PASS_EQUIVALENT", "OGR and QGIS intersects"
    elif v == "gis_measure":
        g = geom(c["input"]["features"][0]["geometry"])
        expected = g.GetArea() * unit ** 2 if g.GetDimension() == 2 else 0
        qexpected = qgeom(g).area() * unit ** 2
        assert numeric(a["result"]["value"], expected) and numeric(a["result"]["value"], qexpected), (a["result"], expected, qexpected)
        assert a["result"]["unit"] == "m²"
        return "PASS_EQUIVALENT", "OGR and QGIS planar area with CRS unit conversion"
    elif v == "gis_validate_geometry":
        valid = all(g is not None and g.IsValid() for _, g in source)
        qvalid = all(g is not None and (not qgeom(g).validateGeometry() if g.IsEmpty() else qgeom(g).isGeosValid()) for _, g in source)
        assert a["result"]["valid"] == valid == qvalid
        assert a["result"]["checked"] == len(source)
        if c["geometry"] == "empty_polygon":
            return "PASS_REFERENCE_DIFFERENCE", "OGR IsValid=true; QGIS validateGeometry=[] but isGeosValid=false on typed empty. Raw API difference preserved"
        return "PASS_EQUIVALENT", "OGR and QGIS validity flag; reason strings are version-specific"
    assert a["output"]["fields"] == schema, ("schema", a["output"]["fields"], schema)
    assert a["output"]["crs"]["code"] == code, ("CRS", a["output"]["crs"]["code"], code)
    compare_rows(a["output"]["geojson"], rows, qrows)
    if c["geometry"] == "empty_polygon" and v == "gis_buffer":
        return "PASS_REFERENCE_DIFFERENCE", "Matches OGR typed empty polygons; QGIS buffer returns null wrappers. Raw API difference preserved"
    return "PASS_EQUIVALENT", "OGR schema/properties/geometry" + (" plus QGIS geometry" if qrows is not None else "")


def main():
    app = QgsApplication([], False)
    app.initQgis()
    payload = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
    records = []
    for c in payload["cases"]:
        try:
            status, method = check(c)
            records.append({k: c[k] for k in ("id", "verb", "geometry", "context")} | {"status": status, "method": method})
        except Exception as exc:
            records.append({k: c[k] for k in ("id", "verb", "geometry", "context")} |
                           {"status": "FAIL", "error": str(exc)[:3000], "error_type": type(exc).__name__})
    summary = Counter(c["status"] for c in records)
    output = {"status": "FAIL" if summary["FAIL"] else "PASS_WITH_REFERENCE_DIFFERENCES" if summary["PASS_REFERENCE_DIFFERENCE"] else "PASS", "total": len(records), "counts": dict(summary),
              "verbs": len(set(c["verb"] for c in records)), "runtime": payload["runtime"],
              "reference": {"qgis": Qgis.QGIS_VERSION, "gdal": gdal.VersionInfo("RELEASE_NAME"),
                            "geos": f"{ogr.GetGEOSVersionMajor()}.{ogr.GetGEOSVersionMinor()}.{ogr.GetGEOSVersionMicro()}"},
              "tolerances": {"coordinate_map_units_abs": COORD_ABS, "numeric_abs": NUMBER_ABS, "numeric_rel": NUMBER_REL},
              "input_sha256": hashlib.sha256(Path(sys.argv[1]).read_bytes()).hexdigest(),
              "limits": ["Finite synthetic fixtures; not a proof over all GIS data",
                         "Both engines ultimately use GEOS/PROJ, with distinct installed builds",
                         "Domain rejection checks are counted separately from geometry equivalence",
                         "Discovery has no geometry/CRS dimension; create_dataset has no input geometry",
                         "Matrix covers area, intersects, difference and non-dissolving buffer variants; additional parameter variants have separate runtime tests"],
              "cases": records}
    Path(sys.argv[2]).write_text(json.dumps(output, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"status": output["status"], "counts": output["counts"]}))
    app.exitQgis()


if __name__ == "__main__":
    main()
