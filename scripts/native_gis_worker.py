"""Private subprocess boundary for installed QGIS/OGR and psycopg2.

Only named operations are accepted; input/output paths are created by the engine.
No arbitrary expressions, provider URIs, SQL or filesystem paths from agents.
"""
from pathlib import Path
import base64
import hashlib
import io
import json
import sys
import zipfile


def main(p, work):
    action = p["action"]
    if action == "decode_mvt":
        from osgeo import ogr
        tile = work / "tile.pbf"
        tile.write_bytes(base64.b64decode(p["base64"]))
        ds = ogr.Open("MVT:" + str(tile))
        assert ds is not None, "OGR MVT decoder rejected binary"
        layer = ds.GetLayer(0)
        keys = [f.GetName() for f in layer.schema]
        features = [{"geometry": json.loads(f.GetGeometryRef().ExportToJson()), "properties": {key: f.GetField(key) for key in keys}} for f in layer]
        return {"feature_count": layer.GetFeatureCount(), "keys": keys, "values": sorted(f["properties"]["key"] for f in features), "features": features}
    if action == "mvt":
        import psycopg2
        from psycopg2.extras import Json
        d, a = p["dataset"], p["parameters"]
        srid = int(d["crs"]["code"].removeprefix("EPSG:"))
        rows = [{"id": f["id"], "geometry": f["geometry"], "attrs": {k: f["properties"][k] for k in a.get("attributes", [])}} for f in d["geojson"]["features"]]
        with psycopg2.connect(**p["config"], connect_timeout=8, options="-c statement_timeout=20000 -c default_transaction_read_only=on") as conn:
            with conn.cursor() as c:
                c.execute("SELECT postgis_full_version()")
                version = c.fetchone()[0]
                c.execute("""WITH src AS (
                    SELECT (v->>'id')::bigint AS fid, v->'attrs' AS attrs,
                      ST_Transform(ST_SetSRID(ST_GeomFromGeoJSON((v->'geometry')::text), %s),3857) AS geom
                    FROM jsonb_array_elements(%s::jsonb) AS v),
                    tile AS (SELECT fid,attrs,ST_AsMVTGeom(geom,ST_TileEnvelope(%s,%s,%s),4096,0,true) AS geom FROM src),
                    valid AS (SELECT * FROM tile WHERE geom IS NOT NULL AND NOT ST_IsEmpty(geom) ORDER BY fid)
                    SELECT ST_AsMVT(valid.*, 'definitio',4096,'geom','fid'), count(*) FROM valid""",
                          (srid, Json(rows), a["z"], a["x"], a["y"]))
                raw, count = c.fetchone()
        return {"base64": base64.b64encode(bytes(raw)).decode(), "feature_count": count, "postgis_version": version,
                "query_mode": "read-only CTE, bound JSONB parameters; no table or cache writes"}
    from qgis.core import (Qgis, QgsApplication, QgsProject, QgsCoordinateReferenceSystem,
                           QgsVectorLayer, QgsVectorFileWriter, QgsRectangle, QgsReferencedRectangle,
                           QgsMapSettings, QgsMapRendererSequentialJob)
    from qgis.PyQt.QtCore import QSize
    from qgis.PyQt.QtGui import QColor
    # QApplication must outlive every QgsProject/provider Python wrapper.
    global app
    app = QgsApplication([], False)
    app.initQgis()

    if action == "boundary_reference":
        sys.path.append(str(Path(QgsApplication.prefixPath()) / "python/plugins"))
        import processing
        from processing.core.Processing import Processing
        Processing.initialize()
        source = work / "input.las"
        source.write_bytes(base64.b64decode(p["base64"]))
        target = work / "boundary.gpkg"
        processing.run("pdal:boundary", {"INPUT": str(source), "OUTPUT": str(target), "RESOLUTION": p["resolution"], "THRESHOLD": p["threshold"]})
        from osgeo import ogr
        ds = ogr.Open(str(target))
        layer = ds.GetLayer(0)
        return {"features": [json.loads(f.GetGeometryRef().ExportToJson()) for f in layer], "qgis": Qgis.QGIS_VERSION, "algorithm": "pdal:boundary"}

    def vector(d):
        source = work / "source.geojson"
        source.write_text(json.dumps(d["geojson"]), encoding="utf-8")
        layer = QgsVectorLayer(str(source), d["name"], "ogr")
        layer.setCrs(QgsCoordinateReferenceSystem(d["crs"]["wkt"]))
        assert layer.isValid(), "Native OGR layer invalid"
        return layer

    def project_readback(project):
        return {"qgis_version": Qgis.QGIS_VERSION, "crs": project.crs().authid(),
                "layers": [{"name": l.name(), "valid": l.isValid(), "feature_count": l.featureCount(), "fields": [f.name() for f in l.fields()]} for l in project.layerTreeRoot().layerOrder()]}

    if action == "shapefile":
        d = p["dataset"]
        # DBF cannot faithfully carry arbitrary schemas; reject before publishing.
        assert all(f["name"].isascii() and len(f["name"]) <= 10 and f["type"] in ("integer", "number", "string") for f in d["fields"]), "Shapefile domain: ASCII <=10-char scalar names, no boolean"
        assert all(not isinstance(v, str) or len(v.encode("utf-8")) <= 254 for f in d["geojson"]["features"] for v in f["properties"].values()), "DBF string too long"
        layer = vector(d)
        target = work / (p["layer_name"] + ".shp")
        options = QgsVectorFileWriter.SaveVectorOptions()
        options.driverName = "ESRI Shapefile"
        options.fileEncoding = "UTF-8"
        result = QgsVectorFileWriter.writeAsVectorFormatV3(layer, str(target), QgsProject.instance().transformContext(), options)
        assert result[0] == QgsVectorFileWriter.NoError, str(result)
        native = QgsVectorLayer(str(target), "verify", "ogr")
        assert native.isValid() and native.featureCount() == d["feature_count"], "Shapefile readback failed"
        assert [f.name() for f in native.fields()] == [f["name"] for f in d["fields"]], "Shapefile field names changed"
        from qgis.core import QgsVariantUtils
        for actual, expected in zip(native.getFeatures(), d["geojson"]["features"]):
            props = {f["name"]: None if QgsVariantUtils.isNull(actual[f["name"]]) else actual[f["name"]] for f in d["fields"]}
            assert props == expected["properties"], "DBF values not lossless; publication blocked before REST"
            from osgeo import ogr
            reference = ogr.CreateGeometryFromJson(json.dumps(expected["geometry"]))
            assert ogr.CreateGeometryFromJson(actual.geometry().asJson()).Equals(reference), "Shapefile geometry changed"
        archive = io.BytesIO()
        with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as z:
            for f in work.glob(p["layer_name"] + ".*"):
                z.write(f, f.name)
        return {"base64": base64.b64encode(archive.getvalue()).decode(), "qgis_version": Qgis.QGIS_VERSION}
    if action == "read_vector":
        from osgeo import ogr
        source = work / "native.gpkg"
        source.write_bytes(base64.b64decode(p["base64"]))
        ds = ogr.Open(str(source))
        layer = ds.GetLayer(0)
        features = []
        for f in layer:
            g = f.GetGeometryRef()
            g.FlattenTo2D()
            features.append({"type": "Feature", "geometry": json.loads(g.ExportToJson()), "properties": {}})
        return {"geojson": {"type": "FeatureCollection", "features": features}, "crs": layer.GetSpatialRef().ExportToWkt(), "qgis_version": Qgis.QGIS_VERSION}
    project = QgsProject()
    target = work / "project.qgz"
    if action == "create_map":
        properties = p["properties"]
        project.setCrs(QgsCoordinateReferenceSystem(properties["crs"]))
        project.setBackgroundColor(QColor("white"))
        project.viewSettings().setDefaultViewExtent(QgsReferencedRectangle(QgsRectangle(*properties["extent"]), project.crs()))
    else:
        target.write_bytes(base64.b64decode(p["map"]["qgz_base64"]))
        assert project.read(str(target)), "Cannot reopen immutable QGIS project"
        assert all(l.isValid() for l in project.mapLayers().values()), "Project attachment not portable"
    if action == "add_layer":
        layer = vector(p["dataset"])
        # QGIS embeds attached GeoPackages inside the QGZ archive. Reopening the
        # project after this temporary directory is gone still resolves the data.
        attachment = project.createAttachedFile(p["dataset"]["id"] + ".gpkg")
        options = QgsVectorFileWriter.SaveVectorOptions()
        options.driverName = "GPKG"
        options.layerName = "features"
        result = QgsVectorFileWriter.writeAsVectorFormatV3(layer, attachment, project.transformContext(), options)
        assert result[0] == QgsVectorFileWriter.NoError, str(result)
        layer = QgsVectorLayer(attachment + "|layername=features", p["dataset"]["name"], "ogr")
        assert layer.isValid(), "Invalid attached data"
        project.addMapLayer(layer)
    if action in ("create_map", "add_layer"):
        assert project.write(str(target)), "Cannot save QGIS project"
        reopened = QgsProject()
        assert reopened.read(str(target)), "Cannot read back QGIS project"
        readback = project_readback(reopened)
        assert all(l["valid"] for l in readback["layers"]), "Invalid QGIS project layer readback"
        return {"base64": base64.b64encode(target.read_bytes()).decode(), "readback": readback}
    assert action == "render", "Unknown worker action"
    m = p["map"]
    settings = QgsMapSettings()
    settings.setLayers(project.layerTreeRoot().layerOrder())
    settings.setDestinationCrs(project.crs())
    settings.setTransformContext(project.transformContext())
    settings.setExtent(QgsRectangle(*m["extent"]))
    settings.setOutputSize(QSize(m["width"], m["height"]))
    settings.setOutputDpi(p["dpi"])
    settings.setBackgroundColor(project.backgroundColor())
    job = QgsMapRendererSequentialJob(settings)
    job.start()
    job.waitForFinished()
    assert not job.errors(), str(job.errors())
    image = job.renderedImage()
    assert image.width() == m["width"] and image.height() == m["height"], "Wrong render size"
    output = work / "map.png"
    assert image.save(str(output), "PNG"), "Cannot encode PNG"
    raw = output.read_bytes()
    return {"base64": base64.b64encode(raw).decode(), "sha256": hashlib.sha256(raw).hexdigest(), "readback": project_readback(project)}


if __name__ == "__main__":
    source, target = Path(sys.argv[1]), Path(sys.argv[2])
    try:
        result = main(json.loads(source.read_text(encoding="utf-8")), source.parent)
    except Exception as exc:
        # Deliberately avoid dumping parameters, connection strings or credentials.
        result = {"error": type(exc).__name__ + ": " + str(exc)[:800]}
    if "app" in globals():
        app.exitQgis()
    target.write_text(json.dumps(result, ensure_ascii=False), encoding="utf-8")
