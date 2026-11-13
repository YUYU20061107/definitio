"""Real registered QGIS channel and pre-persistence rejection of bypasses."""
from pathlib import Path

import pytest
from shapely.geometry import box, mapping, shape

from definitio.agent import AgentService
from definitio.runtime import Runtime


def service(tmp_path):
    return AgentService(Runtime(tmp_path/'processing.sqlite3'))


def test_registered_centroids_are_actual_qgis_outputs(tmp_path):
    s=service(tmp_path)
    d=s.runtime.import_dataset('source',{'type':'FeatureCollection','features':[{'type':'Feature','geometry':mapping(box(0,0,4,2)),'properties':{'key':'A'}}]},32649)
    args={'algorithm':'native:centroids','params':{'INPUT':d['id'],'ALL_PARTS':False,'OUTPUT':'centroids'},'confirm':True,'request_id':'centroid-1'}
    reply=s.call('gis_run_processing',args)
    assert reply['ok'],reply
    out=s.store.dataset(reply['data']['outputs'][0]['id'])
    assert shape(out['geojson']['features'][0]['geometry']).wkt=='POINT (2 1)'
    assert out['geojson']['features'][0]['properties']=={'key':'A'} and out['fields']==d['fields']
    assert s.call('gis_run_processing',args)['data']['replayed']
    ev=s.store.evidence(reply['data']['task']['evidence_refs'][0])
    assert ev['implementation_route']=='installed_qgis_processing_seed'


@pytest.mark.parametrize('algorithm,code',[('native:buffer','NOT_SUPPORTED'),('qgis:executesql','DEFER_BLOCKED'),('unknown:algorithm','NOT_SUPPORTED')])
def test_channel_gate_runs_before_task_persistence(tmp_path,algorithm,code):
    s=service(tmp_path)
    reply=s.call('gis_run_processing',{'algorithm':algorithm,'params':{'INPUT':'missing','ALL_PARTS':False,'OUTPUT':'new'},'confirm':True,'request_id':algorithm})
    assert reply['error']['code']==code,reply
    with s.store.connect() as db:
        assert db.execute('SELECT count(*) FROM tasks').fetchone()[0]==0
