"""Run the single published seed on private copied inputs; no engine imports."""
import json
from pathlib import Path
import sys

from qgis.core import Qgis, QgsApplication, QgsCoordinateReferenceSystem, QgsVectorLayer
from qgis.PyQt.QtCore import QMetaType

app=QgsApplication([],False);app.initQgis()
sys.path.append(str(Path(QgsApplication.prefixPath())/"python/plugins"))
import processing
from processing.core.Processing import Processing
Processing.initialize()
source_path,output_path=map(Path,sys.argv[1:3])
request=json.loads(source_path.read_text(encoding="utf-8"))
assert request["algorithm"]=="native:centroids"
d=request["dataset"];geojson={**d["geojson"],"crs":{"type":"name","properties":{"name":d["crs"]["code"]}}}
path=source_path.parent/"source.geojson";path.write_text(json.dumps(geojson),encoding="utf-8")
layer=QgsVectorLayer(str(path),"source","ogr");assert layer.isValid()
layer.setCrs(QgsCoordinateReferenceSystem.fromWkt(d["crs"]["wkt"]))
out=processing.run(request["algorithm"],{"INPUT":layer,"ALL_PARTS":request["all_parts"],"OUTPUT":"memory:"})["OUTPUT"]
features=[]
from qgis.core import QgsVariantUtils
for f in out.getFeatures():
    properties={spec["name"]:None if QgsVariantUtils.isNull(f[spec["name"]]) else f[spec["name"]] for spec in d["fields"]}
    geometry=json.loads(f.geometry().asJson()) if not f.geometry().isNull() else None
    features.append({"type":"Feature","geometry":geometry,"properties":properties})
output_path.write_text(json.dumps({"geojson":{"type":"FeatureCollection","features":features},"versions":{"qgis":Qgis.QGIS_VERSION}},allow_nan=False),encoding="utf-8")
out=None;layer=None
app.exitQgis()
