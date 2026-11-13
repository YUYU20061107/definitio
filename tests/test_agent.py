"""Agent-first acceptance: machine contracts, retry safety and no-UI composition."""
import copy
import json
from concurrent.futures import ThreadPoolExecutor

import pytest
from fastapi.testclient import TestClient
from jsonschema import Draft202012Validator

from definitio.agent import AgentService, MAX_REPLY_CHARS, OUTPUT_SCHEMA
from definitio.model import canonical, digest
from definitio.runtime import Runtime
from definitio.server import create_app


def fixture_geojson():
    rows = [(0,0,1,1,'A'),(1,0,2,1,'A'),(4,0,5,1,'A'),(0,0,1,1,'B'),(10,0,11,1,None)]
    return {'type':'FeatureCollection','features':[
        {'type':'Feature','geometry':{'type':'Polygon','coordinates':[[[x,y],[a,y],[a,b],[x,b],[x,y]]]},
         'properties':{'group_code':group}} for x,y,a,b,group in rows]}


@pytest.fixture
def service(tmp_path):
    return AgentService(Runtime(tmp_path/'agent.sqlite3'))


def imported(service):
    args={'name':'source','geojson':fixture_geojson(),'crs':'EPSG:32649','confirm':True,'request_id':'source-import','source_kind':'synthetic_fixture'}
    result=service.call('dataset_import',args)
    assert result['ok'],result
    return result['data']['dataset']['id']


def dissolve_args(source, **overrides):
    return {'input':source,'target':'dissolved','fields':['group_code'],'confirm':True,'request_id':'dissolve-1',**overrides}


def test_every_tool_is_typed_and_exposes_a_real_output_contract(service):
    assert len(service.tools)==60
    for tool in service.tools.values():
        Draft202012Validator.check_schema(tool['inputSchema'])
        Draft202012Validator.check_schema(tool['outputSchema'])
        assert tool['inputSchema']['additionalProperties'] is False
        assert tool['description'] and tool['annotations']['openWorldHint'] is False
    schema=service.tools['gis_dissolve']['inputSchema']
    assert schema['properties']['fields']['type']=='array'
    assert {'input','target','fields'}.issubset(schema['required']) and schema['anyOf']==[{'required':['request_id']},{'required':['authorization_id']}]


def test_agent_composes_without_calling_local_language_planner(service,monkeypatch):
    def forbidden(*a): raise AssertionError('Agent must own planning')
    monkeypatch.setattr('definitio.runtime.plan_goal',forbidden)
    source=imported(service)
    result=service.call('gis_dissolve',dissolve_args(source))
    assert result['ok'] and result['data']['task']['task_state']=='COMMITTED'
    out=result['data']['outputs'][0]
    assert out['feature_count']==3
    checked=service.call('gis_validate_geometry',{'dataset':out['id'],'repair':False})
    assert checked['ok'] and checked['data']['results'][0]['value']['valid'] is True


def test_direct_operation_retry_returns_same_task_and_output(service):
    source=imported(service)
    first=service.call('gis_dissolve',dissolve_args(source))
    again=service.call('gis_dissolve',dissolve_args(source))
    assert again['data']['replayed'] is True
    assert first['data']['task']['id']==again['data']['task']['id']
    assert first['data']['outputs'][0]['id']==again['data']['outputs'][0]['id']
    assert len(service.store.datasets())==2


def test_retry_survives_engine_restart(service):
    source=imported(service)
    first=service.call('gis_dissolve',dissolve_args(source))
    reopened=AgentService(Runtime(service.store.path))
    again=reopened.call('gis_dissolve',dissolve_args(source))
    assert again['data']['replayed'] is True
    assert again['data']['outputs']==first['data']['outputs']


def test_parallel_same_request_creates_only_one_output(service):
    source=imported(service)
    with ThreadPoolExecutor(max_workers=2) as pool:
        results=list(pool.map(lambda _:service.call('gis_dissolve',dissolve_args(source)),range(2)))
    assert all(r['ok'] for r in results)
    assert results[0]['data']['task']['id']==results[1]['data']['task']['id']
    assert len(service.store.datasets())==2


def test_changed_request_cannot_reuse_key(service):
    source=imported(service)
    service.call('gis_dissolve',dissolve_args(source))
    changed=service.call('gis_dissolve',dissolve_args(source,target='different'))
    assert changed['error']['code']=='INVALID_ARGUMENT' and not changed['ok']
    assert len(service.store.datasets())==2


def test_import_is_transactionally_idempotent(service):
    first=imported(service);again=imported(service)
    assert first==again and len(service.store.datasets())==1


def test_confirm_error_is_actionable_and_never_auto_grants_permission(service):
    source=imported(service)
    args=dissolve_args(source);args.pop('confirm')
    error=service.call('gis_dissolve',args)
    assert error['error']['code']=='CONFIRM_REQUIRED'
    assert error['error']['execution_started'] is False
    assert error['next_actions'][0]['requires_authorization'] is True
    assert len(service.store.datasets())==1
    # No durable receipt was made for an unapproved operation.
    assert service.call('gis_dissolve',dissolve_args(source))['ok']


@pytest.mark.parametrize('bad',[
    {'fields':'group_code'}, {'fields':[1]}, {'fields':['group_code','group_code']},
    {'confirm':'true'}, {'confirm':1}, {'surprise':True}, {'input':12}])
def test_schema_rejects_wrong_types_before_any_execution(service,bad):
    source=imported(service)
    result=service.call('gis_dissolve',dissolve_args(source,**bad))
    assert result['error']['code']=='INVALID_ARGUMENT' and result['error']['execution_started'] is False
    assert result['error']['details']['violations'] and len(service.store.datasets())==1


def test_failed_task_is_error_with_recovery_not_fake_tool_success(service):
    source=imported(service)
    args=dissolve_args(source,fields=['missing'])
    result=service.call('gis_dissolve',args)
    assert not result['ok'] and result['error']['code']=='SCHEMA_MISMATCH'
    assert result['data']['task']['task_state']=='FAILED'
    again=service.call('gis_dissolve',args)
    assert again['data']['task']['id']==result['data']['task']['id']
    assert again['data']['replayed'] is True and len(service.store.datasets())==1


def test_isolated_task_never_replayed(service):
    source=imported(service)
    result=service.call('gis_dissolve',dissolve_args(source))
    task=service.store.task(result['data']['task']['id'])
    task['task_state']='ISOLATED';task['error']={'code':'TARGET_STATE_UNKNOWN','message':'injected unknown state'}
    service.store.save_task(task)
    again=service.call('gis_dissolve',dissolve_args(source))
    assert not again['ok'] and again['error']['recovery']=='inspect_task_and_evidence'
    assert again['error']['retry_safe_after_correction'] is False


def test_feature_pagination_retains_every_record(service):
    source=imported(service)
    offset,records=0,[]
    while offset is not None:
        reply=service.call('dataset_features',{'dataset':source,'offset':offset,'limit':2})
        assert reply['ok']
        records.extend(reply['data']['items']);offset=reply['data']['next_offset']
    assert [r['record_id'] for r in records]==[1,2,3,4,5]
    assert all('geometry' not in r and 'geometry_uri' in r for r in records)


def test_artifact_chunks_reconstruct_exact_json_and_digest(service):
    source=imported(service)
    uri=f'definitio://datasets/{source}/geojson'
    content,offset='',0
    while offset is not None:
        result=service.call('artifact_read',{'uri':uri,'offset':offset,'max_chars':37})
        assert result['ok']
        data=result['data'];content+=data['content'];offset=data['next_offset']
    value=json.loads(content)
    assert len(value['features'])==5 and digest(value)==data['sha256_json_utf8']


def test_large_geometry_has_a_handle_and_bounded_result(service):
    # One geometry whose serialized coordinates exceed the inline geometry cap.
    import math
    coords=[[math.cos(i*.002),math.sin(i*.002)] for i in range(3142)]
    coords.append(coords[0])
    raw={'type':'FeatureCollection','features':[{'type':'Feature','geometry':{'type':'Polygon','coordinates':[coords]},'properties':{'name':'large'}}]}
    result=service.call('dataset_import',{'name':'large','geojson':raw,'crs':'EPSG:32649','confirm':True,'request_id':'large'})
    assert result['ok']
    rows=service.call('dataset_features',{'dataset':result['data']['dataset']['id'],'include_geometry':True})
    assert rows['data']['items'][0]['geometry_inline'] is False
    assert len(canonical(rows))<MAX_REPLY_CHARS


@pytest.mark.parametrize('uri',['file:///C:/Windows/win.ini','https://example.com/private','definitio://datasets/../private','definitio://other/id'])
def test_artifact_reader_is_not_a_filesystem_or_network_proxy(service,uri):
    result=service.call('artifact_read',{'uri':uri})
    assert not result['ok']


def test_plan_exposes_field_drops_and_authorized_chain_executes(service):
    source=imported(service)
    plan=service.call('task_plan',{'goal':'group then validate','request_id':'plan-1','steps':[
        {'verb':'gis_dissolve','parameters':{'input':source,'target':'chain','fields':[]}},
        {'verb':'gis_validate_geometry','parameters':{'dataset':'$previous','repair':False}}]})
    assert plan['ok'] and plan['data']['effects'][0]['dropped_fields']==['group_code']
    task=plan['data']['task']
    blocked=service.call('task_execute',{'task_id':task['id'],'plan_digest':task['plan_digest']})
    assert blocked['error']['code']=='CONFIRM_REQUIRED'
    done=service.call('task_execute',{'task_id':task['id'],'plan_digest':task['plan_digest'],'confirm':True})
    assert done['ok'] and len(done['data']['task']['evidence_refs'])==2


def test_partial_failure_is_actionable_and_preserves_committed_refs(service):
    source=imported(service)
    plan=service.call('task_plan',{'goal':'partial','request_id':'partial','steps':[
        {'verb':'gis_copy_dataset','parameters':{'source':source,'target':'copy'}},
        {'verb':'gis_dissolve','parameters':{'input':'$previous','target':'bad','fields':['missing']}}]})['data']['task']
    done=service.call('task_execute',{'task_id':plan['id'],'plan_digest':plan['plan_digest'],'confirm':True})
    assert not done['ok'] and done['data']['task']['completion']=='partial'
    assert len(done['data']['outputs'])==1 and done['error']['execution_started'] is True


def test_evidence_pages_are_available_from_returned_handle(service):
    source=imported(service)
    done=service.call('gis_dissolve',dissolve_args(source))
    ref=done['data']['task']['evidence_refs'][0]
    summary=service.call('evidence_get',{'evidence_id':ref})
    assert summary['data']['evidence']['input_unchanged'] is True
    groups=service.call('evidence_get',{'evidence_id':ref,'section':'groups','limit':2})['data']
    assert groups['total']==3 and groups['next_offset']==2


def test_destruction_discovery_does_not_bypass_required_preflight(service):
    result=service.call('gis_describe_algorithm',{'algorithm':'gis_delete_dataset'})
    assert result['ok'] and result['data']['capability']['status']=='local_implemented'
    attempted=service.call('gis_delete_dataset',{})
    assert not attempted['ok'] and attempted['error']['code']=='INVALID_ARGUMENT'


def test_read_only_measure_is_one_call_without_data_or_confirmation(service):
    result=service.call('gis_measure',{'operation':'area','a_wkt':'POLYGON ((0 0,2 0,2 3,0 3,0 0))','crs':'EPSG:32649'})
    assert result['ok'] and result['data']['results'][0]['value']['value']==6


def test_headless_engine_does_not_load_static_web_files(tmp_path,monkeypatch):
    def forbidden(*a,**kw):raise AssertionError('Headless engine must not load GUI assets')
    monkeypatch.setattr('definitio.server.StaticFiles',forbidden)
    with TestClient(create_app(tmp_path,ui=False)) as client:
        assert client.get('/').json()['ui'] is False
        manifest=client.get('/api/agent/manifest').json()
        assert len(manifest['tools'])==60
        result=client.post('/api/agent/call',json={'name':'workspace_list','arguments':{}}).json()
        assert result['ok'] and result['data']['items']==[]


def test_every_envelope_matches_machine_output_schema(service):
    for name,args in [('workspace_list',{}),('dataset_describe',{'dataset':'absent'}),('gis_slope',{})]:
        result=service.call(name,args)
        Draft202012Validator(OUTPUT_SCHEMA).validate(result)


def test_plan_field_effects_follow_added_fields_between_steps(service):
    source=imported(service)
    plan=service.call('task_plan',{'goal':'add then project fields','request_id':'add-then-drop','steps':[
        {'verb':'gis_add_field','parameters':{'dataset':source,'target':'with_new','field':{'name':'new','type':'integer','nullable':True,'default':None}}},
        {'verb':'gis_dissolve','parameters':{'input':'$previous','target':'projected','fields':['group_code']}}]})
    assert plan['ok'] and plan['data']['effects'][1]['dropped_fields']==['new']


def test_metadata_budget_never_masks_committed_writes_as_argument_errors(service):
    props={f'f{i}_'+('x'*95):i for i in range(60)}
    raw={'type':'FeatureCollection','features':[{'type':'Feature','geometry':{'type':'Point','coordinates':[0,0]},'properties':props}]}
    source=service.call('dataset_import',{'name':'wide','geojson':raw,'crs':'EPSG:32649','request_id':'wide','confirm':True})['data']['dataset']['id']
    plan=service.call('task_plan',{'goal':'six copies','request_id':'copies','steps':[
        {'verb':'gis_copy_dataset','parameters':{'source':source if i==0 else '$previous','target':f'copy{i}'}} for i in range(6)]})['data']['task']
    reply=service.call('task_execute',{'task_id':plan['id'],'plan_digest':plan['plan_digest'],'confirm':True})
    assert reply['ok'] and reply['data']['task']['task_state']=='COMMITTED'
    assert len(reply['data']['task']['output_refs'])==6
    assert len(canonical(reply))<MAX_REPLY_CHARS
