"""Real installed PDAL on bounded synthetic bytes; no mocks or live services."""
import base64
import copy
from pathlib import Path

import pytest

from definitio.agent import AgentService
from definitio.runtime import Runtime
from scripts.verify_bundle import verify

ROOT=Path(__file__).resolve().parents[1]


@pytest.fixture
def service(tmp_path):
    return AgentService(Runtime(tmp_path/'pdal.sqlite3'))


def imported(service, name='cloud'):
    args={'name':name,'base64':base64.b64encode((ROOT/'examples/pdal-grid.las').read_bytes()).decode(), 'format':'las','confirm':True,'request_id':'import-'+name}
    result=service.call('pointcloud_import',args)
    assert result['ok'],result
    return service.store.dataset(result['data']['dataset']['id'])


def process(service, source, action, params, target='out'):
    return service.call('gis_pointcloud_process',{'input':source['id'],'target':target,'action':action,'params':params,'confirm':True,'request_id':'process-'+target})


def test_info_reads_real_points_dimensions_and_no_new_dataset(service):
    d=imported(service)
    reply=service.call('gis_pointcloud_info',{'input':d['id'],'backend':'local-geos'})
    assert reply['ok'],reply
    data=reply['data']['results'][0]['value'] if 'value' in reply['data']['results'][0] else service.artifact(reply['data']['results'][0]['uri'])
    assert data['point_count']==49 and data['bounds']==[500000,2500000,10,500006,2500006,22]
    assert {'Intensity','Classification','X','Y','Z'}.issubset({f['name'] for f in data['dimensions']})
    assert data['crs']['code']=='EPSG:32649' and len(service.store.datasets())==1
    assert service.call('gis_pointcloud_info',{'input':d['id'],'backend':'local-geos','confirm':True})['error']['code']=='INVALID_ARGUMENT'


@pytest.mark.parametrize('action,params,expected',[
    ('clip',{'bounds':[500000,2500000,0,500002,2500002,100]},9),
    ('clip',{'bounds':[0,0,0,1,1,1]},0),
    ('thin_decimate',{'step':3,'offset':0},17),
    ('thin_radius',{'radius':2},None),
    ('convert',{'format':'laz'},49),
    ('reproject',{'target_crs':3857,'scale':[.01,.01,.01],'offset':[0,0,0]},49),
    ('export_raster',{'resolution':1,'attribute':'Z'},None),
    ('export_raster_tin',{'resolution':1,'max_triangle_edge_length':5},None),
    ('density',{'resolution':1},None)])
def test_real_actions_preserve_input_and_evidence(service,action,params,expected):
    d=imported(service)
    reply=process(service,d,action,params)
    assert reply['ok'],reply
    out=service.store.dataset(reply['data']['outputs'][0]['id'])
    assert service.store.verify_snapshot(service.store.dataset(d['id']))==d['fingerprint']
    if out['kind']=='pointcloud':
        assert out['point_dimensions']==d['point_dimensions']
        if expected is not None: assert out['point_count']==expected
    else: assert out['grid']['width']<=512 and out['grid']['valid_pixel_count']>0
    assert verify(service.runtime.evidence_bundle(reply['data']['task']['id']))['status']=='PASS'
    assert process(service,d,action,params)['data']['replayed']


def test_merge_all_inputs_are_bound_and_preserve_duplicates(service):
    d=imported(service);other=imported(service,'other')
    reply=process(service,d,'merge',{'inputs':[other['id']]})
    assert reply['ok'],reply
    out=service.store.dataset(reply['data']['outputs'][0]['id'])
    assert out['point_count']==98
    bundle=service.runtime.evidence_bundle(reply['data']['task']['id'])
    assert len(bundle['inputs'])==2 and verify(bundle)['status']=='PASS'


@pytest.mark.parametrize('action,params,code',[
    ('clip',{'bounds':[0,0,0,1,1,1],'pipeline':[]},'INVALID_ARGUMENT'),
    ('thin_decimate',{'step':0,'offset':0},'INVALID_ARGUMENT'),
    ('thin_radius',{'radius':-1},'INVALID_ARGUMENT'),
    ('boundary',{},'INVALID_ARGUMENT'),
    ('merge',{},'INVALID_ARGUMENT'),
    ('export_raster',{'resolution':.000001,'attribute':'Z'},'NOT_SUPPORTED')])
def test_domain_refusals_leave_no_output(service,action,params,code):
    d=imported(service)
    reply=process(service,d,action,params)
    assert not reply['ok'] and reply['error']['code']==code,reply
    assert len(service.store.datasets())==1


def test_pointcloud_corruption_preview_and_http_roundtrip(service,tmp_path):
    d=imported(service)
    with service.store.connect() as db:
        before=[db.execute('SELECT count(*) FROM '+t).fetchone()[0] for t in ['datasets','tasks','evidence','agent_requests']]
    result=service.call('task_dry_run',{'goal':'thin','steps':[{'verb':'gis_pointcloud_process','parameters':{'input':d['id'],'target':'thin','action':'thin_decimate','params':{'step':2,'offset':0}}}]})
    assert result['ok'] and result['data']['preview']['steps'][0]['output']['point_count']==25,result
    with service.store.connect() as db:
        assert before==[db.execute('SELECT count(*) FROM '+t).fetchone()[0] for t in ['datasets','tasks','evidence','agent_requests']]
    reply=process(service,d,'convert',{'format':'las'})
    bundle=service.runtime.evidence_bundle(reply['data']['task']['id'])
    bundle['outputs'][0]['pointcloud']['file_sha256']='0'*64
    from definitio.model import GISFault
    with pytest.raises(GISFault):verify(bundle)
    from fastapi.testclient import TestClient
    from definitio.server import create_app
    with TestClient(create_app(tmp_path/'http',ui=False)) as client:
        a={'name':'cloud','base64':d['pointcloud']['base64'],'format':'las','confirm':True,'request_id':'http'}
        result=client.post('/api/agent/call',json={'name':'pointcloud_import','arguments':a}).json()
        assert result['ok'],result
        raw=client.get('/api/datasets/'+result['data']['dataset']['id']+'/export').content
        assert raw==(ROOT/'examples/pdal-grid.las').read_bytes()
