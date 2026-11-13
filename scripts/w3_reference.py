"""QGIS/PDAL reference, deliberately independent of engine Python modules."""
from collections import Counter
import csv
import base64
import hashlib
import json
import math
import os
from pathlib import Path
import subprocess
import sys
import tempfile

import numpy as np
from osgeo import gdal,ogr,osr
from qgis.core import Qgis,QgsApplication,QgsFeature,QgsGeometry,QgsVectorLayer

gdal.UseExceptions();ogr.UseExceptions()
app=QgsApplication([],False);app.initQgis()
sys.path.append(str(Path(QgsApplication.prefixPath())/'python/plugins'))
import processing
from processing.core.Processing import Processing
Processing.initialize()
qgis=Path(QgsApplication.prefixPath()).parent.parent
pdal=qgis/'bin/pdal.exe'


def run(command,stdin=None):
    p=subprocess.run([str(v) for v in command],input=stdin,capture_output=True,text=True,timeout=60)
    if p.returncode:raise RuntimeError(p.stderr)
    return p.stdout


def info(path):return json.loads(run([pdal,'info','--metadata','--schema','--stats',path]))


def points(path,directory,name):
    output=directory/(name+'.csv')
    stages=[{'type':'readers.las','filename':str(path)},{'type':'writers.text','filename':str(output),'format':'csv','precision':12}]
    run([pdal,'pipeline','--stdin'],json.dumps({'pipeline':stages}))
    with output.open(encoding='utf-8',newline='') as handle:
        return [{k:float(v) for k,v in row.items()} for row in csv.DictReader(handle)]


def equivalent_points(a,b,quantize=None):
    if len(a)!=len(b):return False
    a=sorted(a,key=lambda p:tuple(p[k] for k in sorted(p)));b=sorted(b,key=lambda p:tuple(p[k] for k in sorted(p)))
    for left,right in zip(a,b):
        if set(left)!=set(right):return False
        for key,value in left.items():
            expected=right[key]
            if quantize and key in 'XYZ' and len(key)==1:
                index='XYZ'.index(key);expected=round((expected-quantize['offset'][index])/quantize['scale'][index])*quantize['scale'][index]+quantize['offset'][index]
            if abs(value-expected)>(1e-7 if key in ('X','Y','Z') else 0):return False
    return True


def main():
    input_path,output_path=map(Path,sys.argv[1:3]);document=json.loads(input_path.read_text(encoding='utf-8'));records=[]
    temporary=tempfile.TemporaryDirectory(prefix='w3-qgis-reference-');directory=Path(temporary.name)
    for n,c in enumerate(document['cases']):
        r={'id':c['id'],'verb':c['verb']}
        try:
            actual=c['actual']
            if c['expected_error']:
                assert not actual['ok'] and actual['error']['code'] in c['expected_error'],actual
                r.update(status='PASS_DOMAIN_REJECTION',method='declared local gate; not computational equivalence',actual_error=actual['error'])
            else:
                assert actual['ok'],actual
                source=directory/f'source_{n}.las';source.write_bytes(base64.b64decode(c['source']['pointcloud']['base64']))
                if c['verb']=='gis_pointcloud_info':
                    expected=info(source);meta=expected['metadata'];observed=actual['result']
                    assert observed['point_count']==meta['count']
                    assert observed['dimensions']==sorted(expected['schema']['dimensions'],key=lambda f:f['name'])
                    assert observed['bounds']==[meta[k] for k in ('minx','miny','minz','maxx','maxy','maxz')]
                    r.update(status='PASS_EQUIVALENT',method='independent PDAL info on identical bytes; shared PDAL kernel')
                else:
                    action,params=c['parameters']['action'],c['parameters']['params']
                    output=directory/f'reference_{n}.las';args={'INPUT':str(source),'OUTPUT':str(output)}
                    if action=='clip':
                        b=params['bounds'];overlay=QgsVectorLayer('Polygon?crs=EPSG:32649','clip','memory');f=QgsFeature();f.setGeometry(QgsGeometry.fromWkt(f'POLYGON (({b[0]} {b[1]}, {b[3]} {b[1]}, {b[3]} {b[4]}, {b[0]} {b[4]}, {b[0]} {b[1]}))'));overlay.dataProvider().addFeatures([f]);args['OVERLAY']=overlay;algorithm='pdal:clip'
                    elif action=='thin_decimate':args['POINTS_NUMBER']=params['step'];algorithm='pdal:thinbydecimate'
                    elif action=='thin_radius':args['SAMPLING_RADIUS']=params['radius'];algorithm='pdal:thinbyradius'
                    elif action=='merge':
                        other=directory/f'other_{n}.las';other.write_bytes(base64.b64decode(c['additional_inputs'][0]['pointcloud']['base64']));args={'LAYERS':[str(source),str(other)],'OUTPUT':str(output)};algorithm='pdal:merge'
                    elif action=='convert':output=output.with_suffix('.laz');args['OUTPUT']=str(output);algorithm='pdal:convertformat'
                    elif action=='reproject':args['CRS']='EPSG:3857';algorithm='pdal:reproject'
                    else:
                        output=output.with_suffix('.tif');args.update(OUTPUT=str(output),RESOLUTION=params['resolution'],TILE_SIZE=1000)
                        if action=='export_raster':args['ATTRIBUTE']=params['attribute']
                        algorithm={'export_raster':'pdal:exportraster','export_raster_tin':'pdal:exportrastertin','density':'pdal:density'}[action]
                    processing.run(algorithm,args)
                    if action in ('export_raster','export_raster_tin','density'):
                        ds=gdal.Open(str(output));band=ds.GetRasterBand(1);expected=band.ReadAsArray().astype(float);mask=band.GetMaskBand().ReadAsArray()==0;nodata=band.GetNoDataValue()
                        if nodata is not None:mask|=np.isnan(expected) if math.isnan(nodata) else expected==nodata
                        observed=np.array([[np.nan if v is None else v for v in row] for row in actual['output']['raster']['data']])
                        assert observed.shape==expected.shape and np.array_equal(np.isnan(observed),mask)
                        assert np.all(np.abs(observed[~mask]-expected[~mask])<=1e-8+1e-9*np.abs(expected[~mask]))
                        a,b,c0,d,e,f0=actual['output']['raster']['transform'];assert list(ds.GetGeoTransform())==[c0,a,b,f0,d,e]
                        band=None;ds=None
                    else:
                        observed_file=directory/f'actual_{n}.{actual["output"]["pointcloud"]["format"]}';observed_file.write_bytes(base64.b64decode(actual['output']['pointcloud']['base64']))
                        assert equivalent_points(points(observed_file,directory,f'actual_rows_{n}'),points(output,directory,f'reference_rows_{n}'),params if action=='reproject' else None)
                    r.update(status='PASS_EQUIVALENT',method=algorithm+' actual QGIS Processing; compare all point dimensions or exact masks/grid and pixels',quantization='explicit requested LAS scale/offset applied to reference coordinates' if action=='reproject' else None)
        except Exception as exc:r.update(status='FAIL',error=repr(exc))
        records.append(r)
    counts=dict(Counter(r['status'] for r in records));report={'status':'FAIL' if counts.get('FAIL') else 'PASS','total':len(records),'counts':counts,'reference':{'qgis':Qgis.QGIS_VERSION},'input_sha256':hashlib.sha256(input_path.read_bytes()).hexdigest(),'cases':records,'limits':['QGIS Processing and direct PDAL share the installed PDAL kernel; not an independent mathematical oracle','Small synthetic LAS; not a real-world point-cloud benchmark']}
    output_path.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8');print(json.dumps({k:v for k,v in report.items() if k!='cases'}));print([r for r in records if r['status']=='FAIL'])
    app.exitQgis()
    temporary.cleanup()
    return 1 if counts.get('FAIL') else 0


if __name__=='__main__':raise SystemExit(main())
