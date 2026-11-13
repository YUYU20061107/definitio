"""Build a local source-and-evidence ZIP from explicit directory/file allowlists."""
import argparse
import hashlib
import json
from pathlib import Path
import zipfile


ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, default=ROOT/'release/definitio-agent-0.2.0-dev.zip')
    parser.add_argument('--profile', choices=['core','full'], default='full', help='Core source without historical evidence; full source plus recorded evidence. Native backends are not bundled.')
    args = parser.parse_args()
    root_files = ['README.md','VALIDATION.md','G1-READONLY-FINDINGS.md','AGENT.md','AGENT-FIRST-REVIEW.md','AGENT-FIRST-VALIDATION.md','run_mcp.py','run_engine.py','AGENT-USABILITY-VALIDATION.md','mcp.local.json','requirements.txt',
                  'GIS-COMPLETION-DELIVERY.md','W1-VALIDATION.md','W2-VALIDATION.md','W3-POINTCLOUD-PROPOSAL.md','RASTER-TOLERANCE-PROPOSAL.md','REMAINING-VERBS-PROPOSAL.md',
                  'README-BASELINE-0.2.md','AGENT-COMPLETION.md',
                  'CONTINUATION-DELIVERY.md','W3-VALIDATION.md','BOUNDARY-CONTRACT-PROPOSAL.md','REMAINING-VERBS-STATUS.md',
                  'ROUND4-DELIVERY.md','LOCAL-CONTRACT-ADDENDUM.md',
                  'start.ps1','launch.ps1','setup.ps1','打开工作台.cmd','启动Agent引擎.cmd','.gitignore']
    root_files += ['requirements-core.txt', 'WORKSTATION-MANIFEST.json']
    files = [ROOT/name for name in root_files]
    files.extend((ROOT/'docs').rglob('*.md'))
    files.extend((ROOT/'definitio/surfaces').glob('*.json'))
    # Evaluation definitions travel with the code; database workspaces and
    # provider credentials never enter either source package.
    files.extend((ROOT/'evidence/a3-a6-20261113/g0').glob('*.json'))
    model_catalog = ROOT/'evidence/a3-a6-20261113/model-catalog.json'
    if model_catalog.is_file():
        files.append(model_catalog)
    round_root = ROOT/'evidence/a3-a6-20261113'
    for name in ('infrastructure-amendment.json','analysis.json','preservation.json','run-integrity.json','failure-review.json'):
        artifact = round_root/name
        if artifact.is_file():
            files.append(artifact)
    if args.profile == 'full':
        files.extend(round_root.glob('*.log'))
        files.extend(round_root.glob('*tests.xml'))
        files.extend(round_root.glob('ledger.jsonl'))
        files.extend((round_root/'attempts').glob('*/summary.json'))
        files.extend((round_root/'attempts').glob('*/trace.jsonl'))
    patterns = {'definitio':('*.py','catalog.json'), 'web':('*.html','*.css','*.js','*.svg'),
                'tests':('*.py',), 'scripts':('*.py',), 'examples':('*.geojson','*.las'),
                'evidence':('*.json','*.xml','*.jpg','*.txt','*.log','*.zip')}
    for folder, globs in patterns.items():
        if args.profile == 'core' and folder == 'evidence':
            continue
        for pattern in globs:
            files.extend((ROOT/folder).glob(pattern))
    # Exact round-4 proof families only, never pytest workspaces or SQLite data.
    for folder in (sorted((ROOT/'evidence').glob('round4-native-*')) + [ROOT/'evidence/round4-reference'] if args.profile == 'full' else []):
        if folder.is_dir():
            files.extend(p for p in folder.iterdir() if p.is_file() and p.suffix in ('.json','.txt','.log','.png','.qgz','.pbf'))
    files = sorted(set(files))
    manifest = {p.relative_to(ROOT).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest() for p in files}
    manifest_bytes = (json.dumps({'format':'definitio-source-package-v1','profile':args.profile,'scope':'private local development; no publication authorized',
                                 'files':manifest}, ensure_ascii=False, indent=2)+'\n').encode('utf-8')
    args.output.parent.mkdir(parents=True,exist_ok=True)
    with zipfile.ZipFile(args.output,'x',zipfile.ZIP_DEFLATED) as archive:
        for p in files: archive.write(p,p.relative_to(ROOT).as_posix())
        archive.writestr('SOURCE-MANIFEST.json',manifest_bytes)
    with zipfile.ZipFile(args.output) as archive:
        if archive.testzip(): raise RuntimeError('ZIP CRC failed')
        for name,expected in manifest.items():
            if hashlib.sha256(archive.read(name)).hexdigest()!=expected:
                raise RuntimeError(f'Roundtrip mismatch: {name}')
    result={'status':'PASS','artifact':str(args.output),'bytes':args.output.stat().st_size,
            'sha256':hashlib.sha256(args.output.read_bytes()).hexdigest(),
            'file_count':len(files)+1,'crc':'PASS','all_manifest_hashes':'PASS',
            'excluded':['data/','credentials','G1 frozen originals','Python runtime and dependency wheels']}
    args.output.with_suffix('.verification.json').write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print(json.dumps(result,ensure_ascii=False,indent=2))


if __name__=='__main__': main()
