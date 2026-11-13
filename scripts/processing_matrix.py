"""Actual V02 outputs versus separate OGR centroid results and channel denials."""
from pathlib import Path
import json,os,subprocess,sys,tempfile
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from shapely.geometry import box,MultiPolygon,mapping
from definitio.agent import AgentService
from definitio.runtime import Runtime


def main():
    output=ROOT/'evidence/processing-equivalence.json';inputs=output.with_suffix('.inputs.json');assert not output.exists() and not inputs.exists()
    with tempfile.TemporaryDirectory(prefix='processing-matrix-') as directory:
        s=AgentService(Runtime(Path(directory)/'matrix.sqlite3'))
        d=s.runtime.import_dataset('source',{'type':'FeatureCollection','features':[{'type':'Feature','geometry':mapping(MultiPolygon([box(0,0,4,2),box(10,0,12,2)])),'properties':{'key':'A'}}]},32649)
        records=[]
        for label,algorithm,all_parts,error in [('centroid','native:centroids',False,None),('all_parts','native:centroids',True,None),('named_bypass','native:buffer',False,['NOT_SUPPORTED']),('defer','qgis:executesql',False,['DEFER_BLOCKED']),('unknown','unknown:algorithm',False,['NOT_SUPPORTED'])]:
            p={'algorithm':algorithm,'params':{'INPUT':d['id'],'ALL_PARTS':all_parts,'OUTPUT':'channel_'+label}}
            reply=s.call('gis_run_processing',{**p,'confirm':True,'request_id':label});actual={'ok':reply['ok']}
            if reply['ok']:actual['output']=s.store.dataset(reply['data']['outputs'][0]['id'])
            else:actual['error']=reply['error']
            records.append({'id':'gis_run_processing/'+label,'verb':'gis_run_processing','wave':'W1_CHANNEL','parameters':p,'inputs':[d],'actual':actual,'expected_error':error})
        inputs.write_text(json.dumps({'runtime':s.runtime.versions,'cases':records},ensure_ascii=False,indent=2),encoding='utf-8')
    env={k:v for k,v in os.environ.items() if k not in ('PROJ_DATA','PROJ_LIB','GDAL_DATA','PYTHONPATH')};env['QT_QPA_PLATFORM']='offscreen';env['PYTHONUTF8']='1'
    r=subprocess.run([r'C:\Program Files\QGIS 3.44.15\bin\python-qgis-ltr.bat',str(ROOT/'scripts/completion_reference.py'),str(inputs),str(output)],env=env,capture_output=True,timeout=60)
    output.with_suffix('.stdout.txt').write_bytes(r.stdout);output.with_suffix('.stderr.txt').write_bytes(r.stderr)
    print(r.stdout.decode('utf-8',errors='replace')[-2000:]);print(r.stderr.decode('utf-8',errors='replace')[-500:]);return r.returncode


if __name__=='__main__':raise SystemExit(main())
