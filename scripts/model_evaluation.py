"""Bounded A3 evaluation: real MCP, isolated fixtures, durable traces and frozen scoring.

No model request occurs without --run and a matching freeze. No subprocess agent
or scripted reference solution is counted as a model trajectory.
"""
from __future__ import annotations

import argparse
import asyncio
from collections import Counter, defaultdict
from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import random
import socket
import subprocess
import sys
import time

import httpx

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
ROUND = ROOT / 'evidence/a3-a6-20261113'
G0 = ROUND / 'g0'


def stamp():
    return datetime.now(timezone.utc).isoformat()


def read(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))


def save(path, value, exclusive=False):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('x' if exclusive else 'w', encoding='utf-8') as handle:
        json.dump(value, handle, ensure_ascii=False, indent=2)
        handle.write('\n')


def append(path, value):
    with Path(path).open('a', encoding='utf-8') as handle:
        handle.write(json.dumps(dict(time=stamp(), **value), ensure_ascii=False) + '\n')
        handle.flush()
        os.fsync(handle.fileno())


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def source_files():
    return {p.relative_to(ROOT).as_posix(): sha(p) for p in sorted(
        list((ROOT/'definitio').glob('*.py')) + list((ROOT/'definitio/surfaces').glob('*.json')) +
        [ROOT/'scripts/model_evaluation.py', ROOT/'scripts/native_gis_worker.py', ROOT/'scripts/qgis_processing_worker.py'])}


def subset(expected, actual):
    if isinstance(expected, dict):
        return isinstance(actual, dict) and all(k in actual and subset(v, actual[k]) for k, v in expected.items())
    if isinstance(expected, list):
        return isinstance(actual, list) and len(expected) == len(actual) and all(subset(a, b) for a, b in zip(expected, actual))
    return expected == actual


def normalize_args(value, handles):
    if isinstance(value, dict):
        return {k: normalize_args(v, handles) for k, v in value.items() if k not in ('goal','target')}
    if isinstance(value, list):
        return [normalize_args(v, handles) for v in value]
    return handles.get(value, value) if isinstance(value, str) else value


def matches(task, name, arguments, result, handles):
    reference = task['reference_call']
    return name == reference['name'] and subset(normalize_args(reference['arguments'], {}), normalize_args(arguments, handles)) and subset(task['expected'], result)


def choices(name, arguments):
    if name in ('task_dry_run', 'task_plan'):
        return [s.get('verb', '<missing>') for s in arguments.get('steps', []) if isinstance(s, dict)]
    if name == 'gis_call':
        return [arguments.get('verb','<missing>')]
    if name.startswith('gis_') and name not in ('gis_list_algorithms','gis_describe_algorithm'):
        return [name]
    return []


def grade(task, events, handles):
    substantive = [e for e in events if e['kind'] == 'tool' and choices(e['canonical_name'], e['canonical_arguments'])]
    first = choices(substantive[0]['canonical_name'], substantive[0]['canonical_arguments']) if substantive else []
    expected = task['choice_verbs']
    pairs = [(expected[i] if i < len(expected) else '<extra>', first[i] if i < len(first) else '<missing>') for i in range(max(len(expected),len(first)))]
    successes = [e for e in events if e['kind'] == 'tool' and matches(task,e['canonical_name'],e['canonical_arguments'],e['canonical_result'],handles)]
    success = bool(successes) and not any(e.get('host_denied') for e in events)
    errors = [e for e in events if e['kind'] == 'tool' and not e['canonical_result'].get('ok') and e not in successes]
    eligible = bool(task.get('injection') or errors)
    failure_codes = [e['canonical_result'].get('error',{}).get('code','UNKNOWN') for e in errors]
    return {'success':success,'first_try':success and bool(substantive) and substantive[0] in successes and not errors,
            'chain_success':success,'recovery_eligible':eligible,'recovered':success if eligible else None,
            'selection_correct':sum(a==b for a,b in pairs),'selection_total':len(pairs),'confusion_pairs':pairs,
            'tool_calls':sum(e['kind']=='tool' for e in events),'failure_codes':failure_codes,
            'failure_class':None if success else ('authorization' if 'HOST_WRITE_DENIED' in failure_codes else
                'schema/reference' if any(c in ('INVALID_ARGUMENT','NOT_FOUND') for c in failure_codes) else
                'CRS/fields/scope' if failure_codes else 'discovery/selection/composition'),
            'final_artifacts':[e['canonical_result'].get('data',{}).get('preview') for e in successes]}


def seed(call, fixtures):
    handles = {}
    for fixture in fixtures:
        result = call('dataset_import', dict(fixture, confirm=True, request_id='fixture-'+fixture['name'], source_kind='synthetic_fixture'))
        if not result['ok']:
            raise RuntimeError('Fixture import failed: '+fixture['name'])
        handles[result['data']['dataset']['id']] = fixture['name']
    return handles


def offline_check(folder):
    from definitio.agent import AgentService, tool_definitions
    from definitio.runtime import Runtime
    from definitio.surface import Surface
    spec = read(G0/'tasks.json')
    service = AgentService(Runtime(folder/'workspace.sqlite3'))
    handles = seed(service.call, spec['fixtures'])
    rows = []
    for profile in ('canonical','surface-v1'):
        surface = Surface(tool_definitions(), profile)
        for task in spec['tasks']:
            call = task['reference_call']
            name,args = surface.decode(surface.names[call['name']],surface.arguments(call['name'],call['arguments'],True))
            with service.store.connect() as db:
                before = {r[0]: db.execute('SELECT COUNT(*) FROM "'+r[0]+'"').fetchone()[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            result = service.call(name,args)
            with service.store.connect() as db:
                after = {r[0]: db.execute('SELECT COUNT(*) FROM "'+r[0]+'"').fetchone()[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            passed = matches(task,name,args,result,handles) and before == after
            rows.append({'profile':profile,'task_id':task['task_id'],'pass':passed,'zero_writes':before==after,'result':result})
    save(folder/'reference-smoke.json',{'model_api_calls':0,'passed':sum(r['pass'] for r in rows),'total':len(rows),'rows':rows})
    if not all(r['pass'] for r in rows):
        raise RuntimeError('Offline oracle mismatch: '+str([(r['profile'],r['task_id']) for r in rows if not r['pass']]))
    return {'passed':len(rows),'model_api_calls':0}


def freeze():
    protocol = read(G0/'protocol.json')
    spec = read(G0/'tasks.json')
    if len(spec['tasks']) != 24 or len(protocol['models']) != 2 or protocol['surfaces'] != ['canonical','surface-v1']:
        raise RuntimeError('Run envelope mismatch')
    files = {p.relative_to(ROOT).as_posix():sha(p) for p in [G0/'tasks.json',G0/'protocol.json',G0/'predictions.json',ROOT/'definitio/surfaces/surface-v1.json',ROUND/'model-catalog.json']}
    trials = [(t['task_id'],m['slot'],s) for t in spec['tasks'] for m in protocol['models'] for s in protocol['surfaces']]
    random.Random(protocol['schedule_seed']).shuffle(trials)
    value = {'frozen_at':stamp(),'files':files,'source_files':source_files(),'schedule':trials,
             'review_wait':'Waived by Fish in current chat; independent acceptance remains pending.',
             'certification':'NOT_CERTIFIED'}
    save(G0/'frozen.json',value,exclusive=True)
    return value


def check_freeze():
    pin = read(G0/'frozen.json')
    expected_sources=dict(pin['source_files'])
    amendment=ROUND/'infrastructure-amendment.json'
    if amendment.is_file():
        change=read(amendment)
        if change['frozen_sha256'] != sha(G0/'frozen.json') or change['path']!='scripts/model_evaluation.py' or change['previous_sha256']!=expected_sources[change['path']]:
            raise RuntimeError('Infrastructure amendment does not bind the original freeze')
        expected_sources[change['path']]=change['new_sha256']
    if any(sha(ROOT/p)!=h for p,h in pin['files'].items()) or expected_sources != source_files():
        raise RuntimeError('Frozen artifacts or execution source changed; no model call allowed')
    return pin


@contextmanager
def engine(folder):
    with socket.socket() as sock:
        sock.bind(('127.0.0.1',0))
        port=sock.getsockname()[1]
    with (folder/'engine.log').open('w',encoding='utf-8') as log:
        process=subprocess.Popen([sys.executable,'-m','definitio.server','--port',str(port),'--data-dir',str(folder/'data'),'--headless'],cwd=ROOT,env=dict(os.environ,PYTHONIOENCODING='utf-8'),stdout=log,stderr=log,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
        try:
            url='http://127.0.0.1:'+str(port)
            with httpx.Client(base_url=url,trust_env=False,timeout=2) as client:
                for _ in range(100):
                    if process.poll() is not None:
                        raise RuntimeError('Isolated engine exited; inspect engine.log')
                    try:
                        ready=client.get('/api/agent/manifest').status_code == 200
                    except (httpx.ConnectError,httpx.ConnectTimeout):
                        ready=False
                    if ready:
                        break
                    time.sleep(.1)
                else:
                    raise RuntimeError('Isolated engine startup timed out')
                yield url,client
        finally:
            process.terminate()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)


class StopRun(RuntimeError):
    pass


async def trajectory(folder, task, model, profile, protocol, fingerprints):
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client
    from definitio.surface import Surface, SurfaceError, surface_failure
    events = []
    trace = folder/'trace.jsonl'
    def record(event):
        events.append(event)
        append(trace,event)
    def stopped(reason, handles):
        record({'kind':'stop','reason':reason})
        return {'status':'STOPPED','stop_reason':reason,'events':events,'grade':grade(task,events,handles)}
    record({'kind':'start','task_id':task['task_id'],'model':model,'surface':profile,'freeze_sha256':sha(G0/'frozen.json'),'started_at':stamp()})
    with engine(folder) as (url, client):
        def call(name,args):
            r=client.post('/api/agent/call',json={'name':name,'arguments':args})
            r.raise_for_status()
            return r.json()
        handles=seed(call,read(G0/'tasks.json')['fixtures'])
        manifest=client.get('/api/agent/manifest').json()
        surface=Surface(manifest['tools'],profile)
        record({'kind':'setup','source_digest':manifest['engine_source_digest'],'surface_profile':surface.declaration,'fixture_handles':handles,'task_sha256':hashlib.sha256(json.dumps(task,sort_keys=True).encode()).hexdigest()})
        params=StdioServerParameters(command=sys.executable,args=[str(ROOT/'run_mcp.py'),'--engine-url',url,'--no-autostart','--tool-profile',protocol['tool_profile'],'--surface-profile',profile],cwd=str(ROOT),env=dict(os.environ,PYTHONIOENCODING='utf-8'))
        async with stdio_client(params) as (rstream,wstream):
            async with ClientSession(rstream,wstream) as session:
                initialized=await session.initialize()
                listed=await session.list_tools()
                tools=[{'type':'function','function':{'name':t.name,'description':t.description,'parameters':t.inputSchema}} for t in listed.tools]
                messages=[{'role':'system','content':protocol['system_prompt']+'\n'+initialized.instructions}, {'role':'user','content':task['prompt']}]
                if task.get('injection'):
                    injected=call('task_dry_run',task['injection'])
                    if injected['ok']:
                        raise RuntimeError('Frozen injection failed to fail')
                    record({'kind':'injected_error','canonical_call':task['injection'],'canonical_result':injected})
                    messages.append({'role':'user','content':json.dumps({'failed_proposal':surface.arguments('task_dry_run',task['injection'],True),'error':surface.result(injected)},ensure_ascii=False)})
                async with httpx.AsyncClient(trust_env=False,timeout=protocol['timeout_seconds']) as provider:
                    for turn in range(protocol['max_turns']):
                        body=dict(protocol['request'],model=model['model'],messages=messages,tools=tools,tool_choice='auto',stream=False)
                        if len(json.dumps(body,ensure_ascii=False))>protocol['max_request_chars']:
                            record({'kind':'budget_stop','reason':'request_chars'}); break
                        record({'kind':'provider_request','turn':turn,'message_count':len(messages),'request_sha256':hashlib.sha256(json.dumps(body,sort_keys=True).encode()).hexdigest()})
                        try:
                            response=await provider.post(model['base_url']+'/chat/completions',json=body,headers={'Authorization':'Bearer '+os.environ[model['key_env']]})
                        except httpx.HTTPError as exc:
                            record({'kind':'infrastructure_error','exception':type(exc).__name__,'no_received_model_output':not any(e['kind']=='provider_response' for e in events),'retryable':isinstance(exc,(httpx.ConnectError,httpx.ConnectTimeout))})
                            return {'status':'INFRASTRUCTURE_FAILURE','events':events,'grade':grade(task,events,handles)}
                        record({'kind':'provider_http','turn':turn,'http_status':response.status_code})
                        if response.status_code in protocol['stop_http_statuses']:
                            return stopped('Provider stop status '+str(response.status_code),handles)
                        if response.status_code>=500:
                            record({'kind':'infrastructure_error','http_status':response.status_code,'no_received_model_output':not any(e['kind']=='provider_response' for e in events),'retryable':True})
                            return {'status':'INFRASTRUCTURE_FAILURE','events':events,'grade':grade(task,events,handles)}
                        if response.status_code!=200:
                            return stopped('Unexpected provider status '+str(response.status_code)+'; no automatic retry',handles)
                        try:
                            result=response.json()
                            record({'kind':'provider_response','turn':turn,'response':result})
                            message=result['choices'][0]['message']
                        except (ValueError,KeyError,IndexError,TypeError):
                            return stopped('Malformed provider output; retained without rerun',handles)
                        if result.get('model')!=model['model']:
                            return stopped('Unexpected response model identity',handles)
                        fingerprint=result.get('system_fingerprint')
                        old=fingerprints.get(model['slot'])
                        if old and fingerprint and old!=fingerprint:
                            return stopped('Provider fingerprint changed within model',handles)
                        if fingerprint:
                            fingerprints[model['slot']]=fingerprint
                        messages.append({k:message[k] for k in ('role','content','tool_calls','reasoning_content') if k in message})
                        tool_calls=message.get('tool_calls') or []
                        if not tool_calls:
                            break
                        for tool_call in tool_calls:
                            if sum(e['kind']=='tool' for e in events)>=protocol['max_tool_calls']:
                                record({'kind':'budget_stop','reason':'tool_calls'})
                                return {'status':'COMPLETE','events':events,'grade':grade(task,events,handles)}
                            fn=tool_call['function']
                            denied=False
                            try:
                                supplied=json.loads(fn['arguments'])
                                name,args=surface.decode(fn['name'],supplied)
                                definition=next(t for t in manifest['tools'] if t['name']==name)
                                read_only=definition['annotations']['readOnlyHint']
                                if name=='gis_call':
                                    read_only=next(t for t in manifest['tools'] if t['name']==args['verb'])['annotations']['readOnlyHint']
                                if not read_only:
                                    denied=True
                                    canonical_result=surface_failure('This preview-only task does not authorize writes')
                                    canonical_result['error']['code']='HOST_WRITE_DENIED'
                                    presented=canonical_result
                                else:
                                    returned=await session.call_tool(fn['name'],supplied)
                                    presented=returned.structuredContent
                                    # Most responses are canonical receipts. For discovery only,
                                    # record the presented result, which is not scored as completion.
                                    canonical_result=presented
                            except (ValueError,SurfaceError,KeyError,StopIteration) as exc:
                                name=fn['name']; args={}
                                canonical_result=surface_failure(str(exc)); presented=canonical_result
                            event={'kind':'tool','name':fn['name'],'arguments':fn['arguments'],'canonical_name':name,'canonical_arguments':args,'canonical_result':canonical_result,'presented_result':presented,'host_denied':denied}
                            record(event)
                            messages.append({'role':'tool','tool_call_id':tool_call['id'],'content':json.dumps(presented,ensure_ascii=False)})
                            if matches(task,name,args,canonical_result,handles):
                                return {'status':'COMPLETE','events':events,'grade':grade(task,events,handles)}
    return {'status':'COMPLETE','events':events,'grade':grade(task,events,handles)}


def analyze():
    protocol=read(G0/'protocol.json')
    summaries=[read(p) for p in sorted((ROUND/'attempts').glob('*/summary.json'))]
    chosen={}
    for row in summaries:
        chosen[(row['task_id'],row['model_slot'],row['surface'])]=row
    groups={}
    confusion={}
    for slot in ('ALL','A','B'):
        for surface in protocol['surfaces']:
            rows=[r for r in chosen.values() if r['surface']==surface and (slot=='ALL' or r['model_slot']==slot)]
            g=[r['grade'] for r in rows]
            denominator=sum(x['selection_total'] for x in g)
            recovery=sum(x['recovery_eligible'] for x in g)
            usages=[e['response'].get('usage') for r in rows for e in r['events'] if e['kind']=='provider_response']
            complete_usage=bool(usages) and all(isinstance(u,dict) and all(k in u for k in ('prompt_tokens','completion_tokens','total_tokens')) for u in usages)
            key=slot+'/'+surface
            groups[key]={'trajectories':len(rows),'selection_correct':sum(x['selection_correct'] for x in g),'selection_total':denominator,
                'selection_rate':sum(x['selection_correct'] for x in g)/denominator if denominator else None,
                'first_try_successes':sum(x['first_try'] for x in g),'successes':sum(x['success'] for x in g),
                'first_try_rate':sum(x['first_try'] for x in g)/len(g) if g else None,
                'chain_rate':sum(x['chain_success'] for x in g)/len(g) if g else None,
                'recovery_opportunities':recovery,'recovered':sum(x['recovered'] is True for x in g),
                'recovery_rate':sum(x['recovered'] is True for x in g)/recovery if recovery else None,
                'tool_calls':sum(x['tool_calls'] for x in g),'provider_calls':len(usages),
                'tokens':{k:sum(u[k] for u in usages) for k in ('prompt_tokens','completion_tokens','total_tokens')} if complete_usage else None}
            pairs=Counter(tuple(pair) for x in g for pair in x['confusion_pairs'])
            confusion[key]=[{'expected':a,'selected':b,'count':n} for (a,b),n in sorted(pairs.items())]
    a,b=groups['ALL/canonical'],groups['ALL/surface-v1']
    complete=len(chosen)==96 and all(r['status']=='COMPLETE' for r in chosen.values())
    lift=(b['selection_rate']-a['selection_rate'])*100 if a['selection_rate'] is not None and b['selection_rate'] is not None else None
    recovery_ok=a['recovery_rate'] is not None and b['recovery_rate'] is not None and b['recovery_rate']>=a['recovery_rate']
    model_ok=all(groups[s+'/surface-v1']['selection_rate'] is not None and groups[s+'/canonical']['selection_rate'] is not None and groups[s+'/surface-v1']['selection_rate']>=groups[s+'/canonical']['selection_rate'] for s in ('A','B'))
    adopt=complete and lift>=protocol['adoption']['selection_improvement_percentage_points'] and recovery_ok and model_ok
    report={'generated_at':stamp(),'status':'COMPLETE' if complete else 'INCOMPLETE','adoption':'ADOPT' if adopt else 'DO_NOT_ADOPT' if complete else 'PENDING_INCOMPLETE',
            'selection_lift_percentage_points':lift,'recovery_non_decrease':recovery_ok,'no_model_selection_regression':model_ok,
            'groups':groups,'confusion_matrices':confusion,'attempt_count':len(summaries),'main_cells_recorded':len(chosen),
            'failures':[{'task_id':r['task_id'],'model_slot':r['model_slot'],'surface':r['surface'],'status':r['status'],'grade':r['grade']} for r in summaries if not r['grade']['success']],
            'certification':'NOT_CERTIFIED','default_surface':'canonical','limitations':['Two models from one provider; no cross-provider claim.','Provider IDs are pinned aliases with catalog provenance, not immutable snapshots.','Synthetic bounded preview tasks; no production execution or GIS conformance certification.','One trajectory per model/task/surface; no multi-seed stability claim.']}
    save(ROUND/'analysis.json',report)
    return report


def run():
    pin=check_freeze(); protocol=read(G0/'protocol.json'); spec=read(G0/'tasks.json')
    tasks={t['task_id']:t for t in spec['tasks']}; models={m['slot']:m for m in protocol['models']}
    for model in models.values():
        if not os.environ.get(model['key_env']):
            raise RuntimeError('Missing credential environment variable '+model['key_env'])
    lock=ROUND/'run.lock'
    with lock.open('x',encoding='utf-8') as handle:
        handle.write(str(os.getpid()))
    try:
        journal=ROUND/'ledger.jsonl'
        previous=[json.loads(line) for line in journal.read_text(encoding='utf-8').splitlines()] if journal.exists() else []
        if any(e['kind']=='stop' for e in previous):
            raise RuntimeError('Run has a stop-line event; automatic continuation is disabled')
        attempts=ROUND/'attempts'; attempts.mkdir(exist_ok=True)
        fingerprints={}
        for p in attempts.glob('*/summary.json'):
            for e in read(p)['events']:
                if e['kind']=='provider_response' and e['response'].get('system_fingerprint'):
                    fingerprints[read(p)['model_slot']]=e['response']['system_fingerprint']
        last_finished={tuple(e['cell']):e for e in previous if e['kind']=='finished'}
        completed={cell for cell,e in last_finished.items() if not e.get('rerun_pending')}
        started=[e for e in previous if e['kind']=='started']
        finished_attempts={e['attempt'] for e in previous if e['kind']=='finished'}
        if any(e['attempt'] not in finished_attempts for e in started):
            raise RuntimeError('Interrupted attempt requires readback; never silently repeat a potentially billed request')
        reruns=sum(e.get('rerun',False) for e in started)
        for cell in pin['schedule']:
            if tuple(cell) in completed:
                continue
            tid,slot,profile=cell
            retry=bool(last_finished.get(tuple(cell),{}).get('rerun_pending'))
            while True:
                if len(started)>=protocol['max_total_trajectories'] or retry and reruns>=protocol['max_infrastructure_reruns']:
                    raise StopRun('Trajectory or rerun cap reached')
                index=len(started)+1
                folder=attempts/f'{index:03d}-{tid}-{slot}-{profile}'
                folder.mkdir()
                event={'kind':'started','cell':cell,'attempt':index,'rerun':retry}
                append(journal,event); started.append(event)
                if retry:
                    reruns+=1
                try:
                    result=asyncio.run(trajectory(folder,tasks[tid],models[slot],profile,protocol,fingerprints))
                except StopRun as exc:
                    append(journal,{'kind':'stop','cell':cell,'reason':str(exc)})
                    raise
                except Exception as exc:
                    events=[json.loads(line) for line in (folder/'trace.jsonl').read_text(encoding='utf-8').splitlines()]
                    no_request=not any(e['kind']=='provider_request' for e in events)
                    failure={'kind':'infrastructure_error','exception':type(exc).__name__,'no_received_model_output':no_request,'retryable':no_request}
                    append(folder/'trace.jsonl',failure); events.append(failure)
                    handles=next((e['fixture_handles'] for e in events if e['kind']=='setup'),{})
                    result={'status':'INFRASTRUCTURE_FAILURE','events':events,'grade':grade(tasks[tid],events,handles)}
                result.update(task_id=tid,model_slot=slot,surface=profile,attempt=index,finished_at=stamp())
                save(folder/'summary.json',result,exclusive=True)
                append(journal,{'kind':'finished','cell':cell,'attempt':index,'status':result['status']})
                if result['status']=='STOPPED':
                    append(journal,{'kind':'stop','cell':cell,'attempt':index,'reason':result['stop_reason']})
                    raise StopRun(result['stop_reason'])
                print(json.dumps({'attempt':index,'task':tid,'model':slot,'surface':profile,'status':result['status'],'success':result['grade']['success']},ensure_ascii=False),flush=True)
                infra=[e for e in result['events'] if e['kind']=='infrastructure_error']
                retry=bool(infra and infra[-1]['retryable'] and infra[-1]['no_received_model_output'] and reruns<protocol['max_infrastructure_reruns'])
                if not retry:
                    break
            analyze()
    finally:
        # Only the current process's explicit lock is removed; evidence is retained.
        lock.unlink()


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--check',type=Path,help='New isolated folder for zero-model oracle smoke')
    parser.add_argument('--freeze',action='store_true')
    parser.add_argument('--run',action='store_true')
    parser.add_argument('--analyze',action='store_true')
    args=parser.parse_args()
    if args.check:
        args.check.mkdir(parents=True,exist_ok=False)
        print(offline_check(args.check))
    if args.freeze:
        print({'frozen':freeze()['frozen_at']})
    if args.run:
        try:
            run()
        finally:
            analyze()
    if args.analyze:
        r=analyze(); print({'status':r['status'],'adoption':r['adoption'],'cells':r['main_cells_recorded']})


if __name__=='__main__':
    main()
