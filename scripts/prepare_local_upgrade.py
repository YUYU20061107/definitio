"""Prepare a reviewable local-only upgrade; never stops or replaces an engine."""
from pathlib import Path
import datetime
import hashlib
import json
import sqlite3
import urllib.request
import zipfile

ROOT=Path(__file__).resolve().parents[1]
ORIGINAL=Path(r'C:\Users\RAHUYU\WorkBuddy\2026-11-09-10-00-10\agent-native-gis\05-reference-runtime')


def state(conn):
    return {table:{json.loads(row[0])['id']:hashlib.sha256(row[0].encode()).hexdigest() for row in conn.execute('SELECT document FROM '+table)} for table in ['datasets','tasks','evidence']}


def main():
    expected=json.loads((ROOT/'evidence/completion-source-before.json').read_text(encoding='utf-8'))
    changed=[name for name,sha in expected.items() if not (ORIGINAL/name).is_file() or hashlib.sha256((ORIGINAL/name).read_bytes()).hexdigest()!=sha]
    assert not changed,changed
    health=json.load(urllib.request.urlopen('http://127.0.0.1:8765/api/health'))
    assert health['versions']['source_digest']=='a30f788fec7d4fb2da41c1ecb285e640976c6444a9b79af734e3944c69c29179',health
    conn=sqlite3.connect('file:'+str(ORIGINAL/'data/workspace.sqlite3')+'?mode=ro',uri=True)
    active=[json.loads(r[0])['id'] for r in conn.execute('SELECT document FROM tasks') if json.loads(r[0])['task_state'] in ('EXECUTING','PRECHECKED')]
    assert not active,active
    backup=ORIGINAL/'backup'/('gis-completion-upgrade-'+datetime.datetime.now().strftime('%Y%m%d-%H%M%S'));backup.mkdir(parents=True,exist_ok=False)
    files=[]
    for folder,patterns in {'definitio':['*.py','catalog.json'],'scripts':['*.py'],'tests':['*.py'],'web':['*.html','*.css','*.js','*.svg'],'examples':['*.geojson','*.las']}.items():
        for pattern in patterns:files.extend((ROOT/folder).glob(pattern))
    files.extend(p for p in ROOT.iterdir() if p.is_file() and p.suffix in ('.md','.txt','.py','.ps1','.cmd'))
    # Keep the live MCP config and its 8765 workspace address unchanged.
    relative=sorted({p.relative_to(ROOT).as_posix() for p in files})
    old={}
    with zipfile.ZipFile(backup/'source-before.zip','x',zipfile.ZIP_DEFLATED) as archive:
        for name in relative:
            target=(ORIGINAL/name).resolve();assert target.is_relative_to(ORIGINAL.resolve())
            if target.is_file():
                old[name]=hashlib.sha256(target.read_bytes()).hexdigest();archive.write(target,name)
    with sqlite3.connect(backup/'workspace-before.sqlite3') as target:conn.backup(target)
    documents=state(conn);conn.close()
    plan={'status':'PREPARED','original_root':str(ORIGINAL),'candidate_root':str(ROOT),'backup':str(backup),'engine_before':health,
          'active_tasks':active,'files':{name:hashlib.sha256((ROOT/name).read_bytes()).hexdigest() for name in relative},'old_files':old,
          'workspace_documents_before':documents,'g1_manifest':json.loads((ROOT/'evidence/completion-g1-before.json').read_text(encoding='utf-8')),
          'retained_live_configuration':'mcp.local.json','no_publication':True}
    (backup/'upgrade-plan.json').write_text(json.dumps(plan,ensure_ascii=False,indent=2),encoding='utf-8')
    (ROOT/'evidence/continuation-upgrade-plan.json').write_text(json.dumps(plan,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({'status':'PREPARED','backup':str(backup),'source_files':len(relative),'workspace_counts':{t:len(v) for t,v in documents.items()}},ensure_ascii=False))


if __name__=='__main__':main()
