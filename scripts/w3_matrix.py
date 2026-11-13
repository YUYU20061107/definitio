"""Freeze real Agent outputs; compare through installed QGIS Processing in another process."""
from pathlib import Path
import base64
import json
import os
import subprocess
import sys
import tempfile

ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from definitio.agent import AgentService
from definitio.runtime import Runtime


def main():
    import argparse
    parser=argparse.ArgumentParser();parser.add_argument('--output',type=Path,default=ROOT/'evidence/w3-equivalence-round1.json');parser.add_argument('--only-failed-from',type=Path);a=parser.parse_args()
    selected={c['id'] for c in json.loads(a.only_failed_from.read_text(encoding='utf-8'))['cases'] if c['status']=='FAIL'} if a.only_failed_from else None
    output=a.output.resolve()
    inputs=output.with_suffix('.inputs.json')
    assert not output.exists() and not inputs.exists()
    records=[]
    with tempfile.TemporaryDirectory(prefix='w3-matrix-') as directory:
        service=AgentService(Runtime(Path(directory)/'matrix.sqlite3'))
        encoded=base64.b64encode((ROOT/'examples/pdal-grid.las').read_bytes()).decode()
        def imported(name):
            reply=service.call('pointcloud_import',{'name':name,'base64':encoded,'format':'las','confirm':True,'request_id':'import-'+name})
            assert reply['ok'],reply
            return service.store.dataset(reply['data']['dataset']['id'])
        source,other=imported('source'),imported('other')
        def record(verb,label,args,expected=None):
            if selected is not None and verb+'/'+label not in selected:return
            if verb!='gis_pointcloud_info':args={**args,'confirm':True,'request_id':label}
            reply=service.call(verb,args);actual={'ok':reply['ok']}
            if not reply['ok']:actual['error']=reply['error']
            elif reply['data']['outputs']:actual['output']=service.store.dataset(reply['data']['outputs'][0]['id'])
            else:
                item=reply['data']['results'][0];actual['result']=item['value'] if 'value' in item else service.artifact(item['uri'])
            records.append({'id':verb+'/'+label,'verb':verb,'parameters':args,'source':source,'additional_inputs':[other] if args.get('action')=='merge' else [],'actual':actual,'expected_error':expected})
        record('gis_pointcloud_info','metadata',{'input':source['id'],'backend':'local-geos'})
        record('gis_pointcloud_info','missing',{'input':'absent','backend':'local-geos'},['NOT_FOUND'])
        record('gis_pointcloud_info','missing_backend',{'input':source['id']},['INVALID_ARGUMENT'])
        record('gis_pointcloud_info','undeclared_option',{'input':source['id'],'backend':'local-geos','option':'anything'},['INVALID_ARGUMENT'])
        actions=[('clip',{'bounds':[500000.5,2500000.5,0,500002.5,2500002.5,100]}),
                 ('thin_decimate',{'step':3,'offset':0}),('thin_radius',{'radius':2}),
                 ('merge',{'inputs':[other['id']]}),('convert',{'format':'laz'}),
                 ('reproject',{'target_crs':3857,'scale':[.01,.01,.01],'offset':[0,0,0]}),
                 ('export_raster',{'resolution':1,'attribute':'Z'}),('export_raster_tin',{'resolution':1,'max_triangle_edge_length':5}),('density',{'resolution':1})]
        for action,params in actions:record('gis_pointcloud_process',action,{'input':source['id'],'target':'w3_'+action,'action':action,'params':params})
        for label,action,params,code in [('boundary_conflict','boundary',{},'NOT_SUPPORTED'),('unknown_action','not_registered',{},'INVALID_ARGUMENT'),
            ('option_injection','clip',{'bounds':[0,0,0,1,1,1],'pipeline':[]},'INVALID_ARGUMENT'),('invalid_radius','thin_radius',{'radius':0},'INVALID_ARGUMENT'),
            ('missing_merge_inputs','merge',{},'INVALID_ARGUMENT'),('budget_grid','density',{'resolution':.000001},'NOT_SUPPORTED')]:
            record('gis_pointcloud_process',label,{'input':source['id'],'target':'w3_'+label,'action':action,'params':params},[code])
        inputs.write_text(json.dumps({'runtime':service.runtime.versions,'cases':records},ensure_ascii=False,indent=2),encoding='utf-8')
    env={k:v for k,v in os.environ.items() if k not in ('PROJ_DATA','PROJ_LIB','GDAL_DATA','PYTHONPATH')};env['QT_QPA_PLATFORM']='offscreen';env['PYTHONUTF8']='1'
    p=subprocess.run([r'C:\Program Files\QGIS 3.44.15\bin\python-qgis-ltr.bat',str(ROOT/'scripts/w3_reference.py'),str(inputs),str(output)],env=env,capture_output=True,timeout=180)
    output.with_suffix('.stdout.txt').write_bytes(p.stdout);output.with_suffix('.stderr.txt').write_bytes(p.stderr)
    print(p.stdout.decode('utf-8',errors='replace')[-4500:]);print(p.stderr.decode('utf-8',errors='replace')[-1000:])
    return p.returncode


if __name__=='__main__':raise SystemExit(main())
