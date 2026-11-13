"""Read-only handoff integrity, strict registry recount and authoring contradictions.

No G1 baseline file is modified. This is development evidence, not Muse acceptance.
"""
import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path
import re
import sys
import zipfile


def leaves(node):
    if not isinstance(node, dict):
        raise ValueError("Registry leaf must be an object")
    if "variants" in node:
        if not isinstance(node["variants"], dict) or not node["variants"]:
            raise ValueError("Empty or invalid variant container")
        for sub in node["variants"].values():
            yield from leaves(sub)
    else:
        yield node


def recount(registry, audit):
    order=registry["verb_order"]
    verbs=registry["verbs"]
    errors=[]
    if list(verbs)!=order or len(order)!=40 or len(set(order))!=40:
        errors.append("verb identity/order mismatch")
    flat=[]
    for index,name in enumerate(order,1):
        verb=verbs[name]
        if verb["vid"]!=f"V{index:02d}": errors.append(f"VID mismatch: {name}")
        for backend,node in verb["backends"].items():
            for leaf in leaves(node): flat.append((verb["vid"],name,backend,leaf))
    status=Counter(leaf.get("status","(未声明)") for *_,leaf in flat)
    bts=Counter(leaf.get("bridge_tool_status","(缺省)") for *_,leaf in flat)
    pending=Counter((vid,name,backend) for vid,name,backend,leaf in flat if leaf.get("bridge_tool_status")=="pending")
    expected=Counter({(p["vid"],p["verb"],p["backend"]):p["leaves"] for p in audit["pending_pairs"]})
    for name,actual,wanted in [
        ("terminal_leaves",len(flat),audit["leaf_stats"]["terminal_leaves"]),
        ("status",dict(status),audit["leaf_stats"]["status"]),
        ("bts",dict(bts),audit["leaf_stats"]["bts"]),
        ("pending_leaf_total",sum(pending.values()),audit["pending_leaf_total"]),
        ("pending_pairs_identity",pending,expected)]:
        if actual!=wanted: errors.append(f"{name} mismatch")
    return {"status":"FAIL" if errors else "PASS","terminal_leaves":len(flat),"raw_status":dict(status),
            "bridge_tool_status":dict(bts),"pending_leaf_total":sum(pending.values()),"pending_groups":len(pending),"errors":errors}


def review(root):
    handoff=root/'02-g1-gate/handoff-20261111'
    manifest=(handoff/'MANIFEST-20261111.md').read_text(encoding='utf-8')
    pins=re.findall(r'^\| `([^`]+)` \| ([\d,]+) \| `([a-f0-9]{20})` \|',manifest,re.M)
    integrity=[]
    for name,size,prefix in pins:
        data=(handoff/name).read_bytes()
        integrity.append({"file":name,"bytes":len(data),"sha256":hashlib.sha256(data).hexdigest(),
                          "passed":len(data)==int(size.replace(',','')) and hashlib.sha256(data).hexdigest().startswith(prefix)})
    archive=handoff/'Definitio-G1-handoff-20261111.zip'
    with zipfile.ZipFile(archive) as z:
        archive_ok=z.testzip() is None and all((handoff/i.filename).read_bytes()==z.read(i) for i in z.infolist())
    registry=recount(json.loads((handoff/'verbs.json').read_text(encoding='utf-8')),json.loads((handoff/'audit-results.json').read_text(encoding='utf-8')))
    suite=(handoff/'gis-agent-conformance-suite-v0.1-full.md').read_text(encoding='utf-8')
    contract=(handoff/'gis-agent-semantic-contract-v0.1-full.md').read_text(encoding='utf-8')
    cases=[]
    for line_no,line in enumerate(suite.splitlines(),1):
        if re.match(r'^\|\s*(?:TC-[A-Z0-9-]+|V21-[A-Z0-9-]+)-\d{3}\s*\|',line):
            cells=[c.strip() for c in line.split('|')[1:-1]]
            cases.append({"case_id":cells[0],"line":line_no,"declared_status":cells[-1] if cells[-1] in ('ready','design_pending','provisional') else 'observation_only',"cells":cells})
    findings=[
        {"id":"G1-DEV-001","status":"FAIL","topic":"design_status contradiction", "evidence":[c["case_id"] for c in cases if c["case_id"] in ('TC-SCHEMA-002','TC-DIM-001') and c["declared_status"]=='ready'],
         "detail":"Suite appendix C requires these two cases design_pending while section 5 marks them ready."},
        {"id":"G1-DEV-002","status":"REVIEW","topic":"NOT_FOUND trace lacks an explicit expected assertion", "detail":"Appendix B names object-invisible/reference-resolution subcases, but no section-5 row explicitly expects NOT_FOUND; needs named cases before executable coverage claim."},
        {"id":"G1-DEV-003","status":"FAIL","topic":"D06 option A contradicts F07", "detail":"V21 F07 admits finite-float exact grouping; appendix D and pending list claim option A (integer/string-only) was applied. No user decision inferred."},
        {"id":"G1-DEV-004","status":"REVIEW","topic":"ready does not include executable fixture bindings", "detail":f"{sum(c['declared_status']=='ready' for c in cases)} ready rows lack per-case immutable fixture/comparator/repeat-plan bindings; design table is not runnable certification evidence."},
    ]
    # Keep machine predicates separate from manually grounded reading findings.
    findings[0]["status"]='FAIL' if findings[0]["evidence"] else 'REVIEW'
    findings[2]["status"]='FAIL' if '有限浮点值按解析后的数值精确分组' in contract and '本稿按 A 落稿' in contract else 'REVIEW'
    return {"scope":"development_read_only_review_not_G1_acceptance","integrity":{"status":"PASS" if pins and all(i['passed'] for i in integrity) and archive_ok else 'FAIL',"files":integrity,"zip_crc_and_payload":archive_ok,"zip_sha256":hashlib.sha256(archive.read_bytes()).hexdigest()},
            "registry":registry,"case_inventory":{"count":len(cases),"statuses":dict(Counter(c['declared_status'] for c in cases)),"cases":cases},
            "findings":findings,"G1_acceptance":"NOT_ISSUED","L1_L3_certification":"NOT_ISSUED"}


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root',type=Path,default=Path(__file__).resolve().parents[2])
    parser.add_argument('--registry',type=Path)
    parser.add_argument('--against',type=Path)
    args=parser.parse_args()
    try:
        if args.registry:
            if not args.against: parser.error('--registry requires --against')
            result=recount(json.loads(args.registry.read_text(encoding='utf-8')),json.loads(args.against.read_text(encoding='utf-8')))
            failed=result['status']=='FAIL'
        else:
            result=review(args.root)
            failed=result['integrity']['status']=='FAIL' or result['registry']['status']=='FAIL' or any(x['status']=='FAIL' for x in result['findings'])
    except Exception as exc:
        result={"status":"FAIL","errors":[str(exc)]};failed=True
    print(json.dumps(result,ensure_ascii=False,indent=2))
    return 1 if failed else 0


if __name__=='__main__': raise SystemExit(main())
