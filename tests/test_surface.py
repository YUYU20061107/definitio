"""MCP naming must preserve execution, opaque data, authorization and receipts."""
import asyncio
import copy
import json
from pathlib import Path
import sys

from jsonschema import Draft202012Validator
import pytest

from definitio.agent import AgentService, tool_definitions
from definitio.runtime import Runtime
from definitio.surface import Surface, SurfaceError
from scripts.model_evaluation import grade, matches, read, G0, trajectory, StopRun
from test_agent import imported
from test_mcp import engine


@pytest.fixture
def surface():
    return Surface(tool_definitions(), 'surface-v1')


def test_surface_schema_and_destructive_names(surface):
    for definition in tool_definitions():
        presented=surface.tool(definition)
        Draft202012Validator.check_schema(presented['inputSchema'])
        assert presented['annotations']==definition['annotations']
        assert presented['outputSchema']==definition['outputSchema']
    assert len(set(surface.names.values()))==60
    for name in ('gis_delete_dataset','gis_delete_field','destructive_preflight'):
        assert surface.names[name]==name
        assert all(k==v for k,v in surface.fields(name).items())
    with pytest.raises(SurfaceError):
        surface.decode('gis_transform_coordinates', {'input':'source'})


def test_surface_leaves_user_fields_names_and_strings_opaque(surface):
    args={'name':'gis_reproject','geojson':{'type':'FeatureCollection','features':[{'properties':{'input':'gis_copy_dataset','target':'m'}}]},'schema':[{'name':'input','type':'string','nullable':False}], 'crs':'EPSG:32649','request_id':'gis_copy_dataset'}
    assert surface.arguments('dataset_import',args,True)==args
    receipt={'ok':True,'data':{'task':{'steps':[{'verb':'gis_reproject','vid':'V09'}],'plan_digest':'fixed'},'dataset':{'fields':[{'name':'input'}]},'preview':{'steps':[{'verb':'gis_reproject'}]}},'next_actions':[]}
    assert surface.result(receipt)==receipt


def test_nested_mapping_preserves_previous_and_control_placement(surface):
    args={'goal':'copy','steps':[{'verb':'gis_copy_dataset','parameters':{'source':'source','target':'job_copy','authorization_id':'bad'}},{'verb':'gis_validate_geometry','parameters':{'dataset':'$previous','repair':False}}]}
    encoded=surface.arguments('task_dry_run',args,True)
    assert encoded['steps'][0]['parameters']['source_dataset']=='source'
    assert encoded['steps'][1]['parameters']['dataset_id']=='$previous'
    assert surface.decode('workflow_preview_without_writes',encoded)==('task_dry_run',args)
    assert args['steps'][0]['parameters']['authorization_id']=='bad'
    wrapped={'verb':'gis_buffer','arguments':{'input':'source','target':'x','distance':1,'unit':'m','dissolve':False}}
    assert surface.decode('gis_call',surface.arguments('gis_call',wrapped,True))==('gis_call',wrapped)


@pytest.mark.parametrize('bad', ['control','reference'])
@pytest.mark.parametrize('tool', ['task_dry_run','task_plan'])
def test_a1_rules_survive_surface_and_do_not_write(tmp_path,surface,bad,tool):
    service=AgentService(Runtime(tmp_path/'workspace.sqlite3')); source=imported(service)
    args={'goal':'probe','steps':[{'verb':'gis_copy_dataset','parameters':{'source':source,'target':'job_copy'}},{'verb':'gis_validate_geometry','parameters':{'dataset':'$previous','repair':False}}]}
    if tool=='task_plan': args['request_id']='plan-probe'
    if bad=='control': args['steps'][0]['parameters']['authorization_id']='misplaced'
    else: args['steps'][1]['parameters']['dataset']='job_copy'
    name,decoded=surface.decode(surface.names[tool],surface.arguments(tool,args,True))
    with service.store.connect() as db:
        before=list(db.iterdump())
    result=service.call(name,decoded)
    assert result['error']['details']['rule']==('operation_parameters_only' if bad=='control' else 'previous_dataset_output')
    with service.store.connect() as db:
        assert before==list(db.iterdump())


def test_discovery_hints_use_surface_without_rewriting_receipts(tmp_path,surface):
    service=AgentService(Runtime(tmp_path/'workspace.sqlite3'))
    r=surface.result(service.call('gis_describe_algorithm',{'algorithm':'gis_buffer'}))
    assert r['data']['tool']['inputSchema']['properties']['unit']['enum']==['metres']
    assert r['data']['plan_step']['example']['steps'][0]['verb']=='gis_copy_to_new_dataset'
    assert 'input_dataset' in r['data']['tool']['inputSchema']['properties']
    route=surface.result(service.call('workflow_route',{'intent':'reproject'}))
    assert 'gis_transform_coordinates' in route['data']['families'][0]['tools']


def test_surface_chain_and_handshake_over_official_mcp(engine,surface):
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client
    url,root=engine
    async def exercise():
        params=StdioServerParameters(command=sys.executable,args=[str(Path(__file__).resolve().parents[1]/'run_mcp.py'),'--engine-url',url,'--no-autostart','--surface-profile','surface-v1'],env={'PYTHONIOENCODING':'utf-8'})
        async with stdio_client(params) as (r,w):
            async with ClientSession(r,w) as session:
                initialized=await session.initialize()
                assert surface.sha256 in initialized.instructions and 'surface-v1' in initialized.instructions
                assert len((await session.list_tools()).tools)==21
                result=await session.call_tool('gis_call', {'verb':'gis_measure','arguments':{'operation':'area_square_metres','a_wkt':'POLYGON ((0 0,2 0,2 3,0 3,0 0))','crs':'EPSG:32649'}})
                assert result.structuredContent['data']['results'][0]['value']['value']==6
                bad=await session.call_tool('workflow_preview_without_writes',{'goal':'bad','steps':[{'verb':'gis_copy_dataset','parameters':{'source_dataset':'x','new_dataset_name':'y'}}]})
                assert bad.isError and bad.structuredContent['error']['details']['rule']=='published_surface_names'
                prompt=await session.get_prompt('buffer_validate',{'input':'source','distance_m':'1','target_prefix':'job_'})
                text=prompt.messages[0].content.text
                assert 'input_dataset=source' in text and 'unit=metres' in text
    asyncio.run(exercise())


@pytest.mark.parametrize('profile,status', [('canonical',200),('surface-v1',200),('canonical',429)])
def test_runner_uses_real_mcp_and_stops_without_live_provider(tmp_path,monkeypatch,profile,status):
    import httpx
    from scripts import model_evaluation as evaluation
    protocol=read(G0/'protocol.json'); task=read(G0/'tasks.json')['tasks'][3]
    test_g0=tmp_path/'g0'; test_g0.mkdir()
    (test_g0/'frozen.json').write_text('{"test_only":true}',encoding='utf-8')
    (test_g0/'tasks.json').write_text((G0/'tasks.json').read_text(encoding='utf-8'),encoding='utf-8')
    monkeypatch.setattr(evaluation,'G0',test_g0)
    s=Surface(tool_definitions(),profile)
    call=task['reference_call']; requests=[]
    async def handler(request):
        requests.append(request)
        return httpx.Response(status,json={'model':'deepseek-flash','system_fingerprint':'test','usage':{'prompt_tokens':10,'completion_tokens':20,'total_tokens':30},'choices':[{'message':{'role':'assistant','content':None,'tool_calls':[{'id':'c1','type':'function','function':{'name':s.names[call['name']],'arguments':json.dumps(s.arguments(call['name'],call['arguments'],True))}}]}}]})
    original=httpx.AsyncClient
    monkeypatch.setenv('DEEPSEEK_API_KEY','offline-test-placeholder')
    monkeypatch.setattr(evaluation.httpx,'AsyncClient',lambda **kw:original(transport=httpx.MockTransport(handler),**kw))
    if status==429:
        result=asyncio.run(trajectory(tmp_path,task,protocol['models'][0],profile,protocol,{}))
        assert result['status']=='STOPPED' and '429' in result['stop_reason']
    else:
        result=asyncio.run(trajectory(tmp_path,task,protocol['models'][0],profile,protocol,{}))
        assert result['grade']['success'] and result['grade']['recovered']
        assert result['grade']['first_try']
    assert len(requests)==1


def test_scoring_keeps_wrong_first_choice_and_zero_denominator():
    task=read(G0/'tasks.json')['tasks'][0]
    result=grade(task,[],{})
    assert not result['success'] and result['selection_correct']==0 and result['selection_total']==2
    assert not result['recovery_eligible'] and result['recovered'] is None
