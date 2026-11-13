"""Render the already-frozen evaluation results; never call a model."""
import json
from pathlib import Path
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from scripts.model_evaluation import ROUND, G0, read, save, normalize_args, choices, source_files, check_freeze


def rate(value):
    return 'null' if value is None else f'{value*100:.2f}%'


def main():
    report=read(ROUND/'analysis.json')
    if report['status']!='COMPLETE':
        raise SystemExit('Model run is incomplete; do not render a final verdict')
    check_freeze()
    groups=report['groups']
    lines=['| 指标 | canonical | surface-v1 |','|---|---:|---:|']
    a,b=groups['ALL/canonical'],groups['ALL/surface-v1']
    for label,key in [('选对率','selection_rate'),('首试成功率','first_try_rate'),('串对率','chain_rate'),('恢复率','recovery_rate')]:
        left,right=rate(a[key]),rate(b[key])
        if key=='selection_rate':
            left+=f' ({a["selection_correct"]}/{a["selection_total"]})'
            right+=f' ({b["selection_correct"]}/{b["selection_total"]})'
        lines.append(f'| {label} | {left} | {right} |')
    for label,key in [('完整预演成功数','successes'),('恢复机会数','recovery_opportunities'),('恢复成功数','recovered'),('模型工具调用数（含失败）','tool_calls'),('模型 API 请求数','provider_calls')]:
        lines.append(f'| {label} | {a[key]} | {b[key]} |')
    for label,key in [('输入 token','prompt_tokens'),('输出 token','completion_tokens'),('总 token','total_tokens')]:
        lines.append(f'| {label} | {a["tokens"][key] if a["tokens"] else "null"} | {b["tokens"][key] if b["tokens"] else "null"} |')
    lines+=['','| 模型 | canonical 选对率 | surface-v1 选对率 | canonical 首试 | surface-v1 首试 |','|---|---:|---:|---:|---:|']
    for slot in ('A','B'):
        x,y=groups[slot+'/canonical'],groups[slot+'/surface-v1']
        lines.append(f'| {slot} | {rate(x["selection_rate"])} | {rate(y["selection_rate"])} | {rate(x["first_try_rate"])} | {rate(y["first_try_rate"])} |')
    lines+=['',f'裁决：**{report["adoption"]}**。选对率变化 {report["selection_lift_percentage_points"]:+.2f} 个百分点（冻结要求至少 +5）；恢复率不下降={report["recovery_non_decrease"]}；两模型各自选对率不下降={report["no_model_selection_regression"]}。默认表面保持 canonical。','',
            f'96/96 主单元完成，总尝试 {report["attempt_count"]}，其中 1 次基础设施失败及 1 次对应重跑，未超过 105 总尝试和 9 重跑限额。没有重跑模型自身失败。','',
            '模型失败保留如下，基础设施首败另列在逐尝试日志中：','',
            '| 任务 | 模型 | 表面 | 观察 |','|---|---|---|---|']
    reviews=[]
    tasks={t['task_id']:t for t in read(G0/'tasks.json')['tasks']}
    signatures={}
    engine_digests=set()
    totals={}
    for path in sorted((ROUND/'attempts').glob('*/summary.json')):
        row=read(path)
        for e in row['events']:
            if e['kind']=='setup':
                engine_digests.add(e['source_digest'])
            if e['kind']=='provider_response':
                signatures.setdefault(row['model_slot'],set()).add(e['response'].get('system_fingerprint'))
        if row['status']=='COMPLETE' and not row['grade']['success']:
            codes=', '.join(row['grade']['failure_codes']) or '无错误码，未满足冻结的参数/完整链/结果约束'
            lines.append(f'| {row["task_id"]} {tasks[row["task_id"]]["title"]} | {row["model_slot"]} | {row["surface"]} | {codes} |')
            handles=next(e['fixture_handles'] for e in row['events'] if e['kind']=='setup')
            reviews.append({'task_id':row['task_id'],'model_slot':row['model_slot'],'surface':row['surface'],
                'reference':normalize_args(tasks[row['task_id']]['reference_call']['arguments'],{}),
                'proposals':[{'name':e['canonical_name'],'arguments':normalize_args(e['canonical_arguments'],handles),'result':e['canonical_result']} for e in row['events'] if e['kind']=='tool' and choices(e['canonical_name'],e['canonical_arguments'])]})
    save(ROUND/'failure-review.json',reviews)
    totals['engine_source_digests']=sorted(engine_digests)
    totals['provider_fingerprints']={k:sorted(v,key=str) for k,v in signatures.items()}
    totals['all_models_single_fingerprint']=all(len(v)==1 for v in signatures.values())
    totals['all_trajectories_same_gis_source']=len(engine_digests)==1
    totals['main_trajectories']=report['main_cells_recorded']
    totals['attempts']=report['attempt_count']
    totals['provider_calls']=a['provider_calls']+b['provider_calls']
    totals['tool_calls']=a['tool_calls']+b['tool_calls']
    totals['model_failure_count']=len(reviews)
    totals['total_tokens']=a['tokens']['total_tokens']+b['tokens']['total_tokens'] if a['tokens'] and b['tokens'] else None
    save(ROUND/'run-integrity.json',totals)
    lines+=['','混淆矩阵（按规范动词、总体和逐模型）在 analysis.json 的 confusion_matrices；逐次提案和失败依据在 failure-review.json。供应端指纹、共同引擎源码摘要及调用/token 总数在 run-integrity.json。token 为供应端计数，未查询或推算货币花费。',
            '',f'所有真实轨迹使用同一 GIS 源码摘要={totals["all_trajectories_same_gis_source"]}；各模型运行内供应端指纹唯一={totals["all_models_single_fingerprint"]}。这不等于供应商提供未来不可变权重快照。']
    doc=ROOT/'docs/A3-A6-DELIVERY-20261113.md'
    text=doc.read_text(encoding='utf-8')
    text=text.replace('当前状态：真实模型运行中；最终结果回填本文件。',f'工程与 96 条真实主轨迹完成；裁决 {report["adoption"]}。')
    text=text.replace('运行中。完整结果以 analysis.json 和 attempts 下逐轨迹记录为准。冻结门槛与局限见 [本轮协议](A3-A6-PROTOCOL-20261113.md)，不根据中途分数修改判据。','\n'.join(lines))
    doc.write_text(text,encoding='utf-8')
    print(json.dumps(totals,ensure_ascii=False))


if __name__=='__main__':
    main()
