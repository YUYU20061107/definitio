"""Create the pre-run A3/A6 artifacts once; never contact a model."""
import copy
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('x', encoding='utf-8') as handle:
        handle.write(json.dumps(value, ensure_ascii=False, indent=2) + '\n')


def prepare():
    import sys
    sys.path.insert(0, str(ROOT))
    from definitio.agent import tool_definitions
    definitions = tool_definitions()
    names = {t['name']: t['name'] for t in definitions}
    names.update({
        'gis_assign_projection': 'gis_relabel_crs_metadata',
        'gis_reproject': 'gis_transform_coordinates',
        'gis_merge_datasets': 'gis_merge_into_new_dataset',
        'gis_append_dataset': 'gis_append_to_existing_dataset',
        'gis_copy_dataset': 'gis_copy_to_new_dataset',
        'gis_dissolve': 'gis_dissolve_groups',
        'gis_spatial_filter': 'gis_select_whole_records',
        'task_dry_run': 'workflow_preview_without_writes',
        'task_plan': 'workflow_store_plan',
        'task_execute': 'workflow_execute_plan',
    })
    parameters = {}
    for t in definitions:
        n = t['name']
        mapping = {k: k for k in t['inputSchema']['properties']}
        if n.startswith('gis_') and n not in ('gis_delete_dataset', 'gis_delete_field', 'gis_append_dataset', 'gis_rename_dataset'):
            for k, alias in {'input': 'input_dataset', 'dataset': 'dataset_id', 'source': 'source_dataset',
                             'inputs': 'input_datasets', 'target': 'new_dataset_name'}.items():
                if k in mapping:
                    mapping[k] = alias
        if n == 'gis_dissolve':
            mapping['fields'] = 'group_fields'
        if n == 'gis_buffer':
            mapping['distance'] = 'distance_metres'
        if n == 'gis_clip':
            mapping['clip_by'] = 'clip_polygon_dataset'
        if n == 'gis_field_statistics':
            mapping['fields'] = 'numeric_fields'
        parameters[n] = mapping
    profile = {'format': 'definitio-surface-profile-v1', 'name': 'surface-v1', 'version': 1,
               'status': 'CANDIDATE_NOT_ADOPTED', 'tools': names, 'parameters': parameters,
               'enums': {'gis_buffer': {'unit': {'m': 'metres'}},
                         'gis_measure': {'operation': {'area': 'area_square_metres', 'length': 'length_metres', 'distance': 'distance_metres'}},
                         'gis_overlay': {'operation': {'intersection': 'intersect_polygons', 'difference': 'subtract_overlay'}}}}
    write(ROOT / 'definitio/surfaces/surface-v1.json', profile)
    geo = json.loads((ROOT / 'examples/analytic-dissolve.geojson').read_text(encoding='utf-8'))
    for f, score in zip(geo['features'], [10, 20, 30, 40, None]):
        f['properties']['score'] = score
    def fc(geometry, properties=None):
        return {'type': 'FeatureCollection', 'features': [{'type': 'Feature', 'geometry': geometry, 'properties': properties or {}}]}
    fixtures = [
        {'name': 'source', 'crs': 'EPSG:32649', 'geojson': geo},
        {'name': 'second', 'crs': 'EPSG:32649', 'geojson': copy.deepcopy(geo)},
        {'name': 'mask', 'crs': 'EPSG:32649', 'geojson': fc({'type': 'Polygon', 'coordinates': [[[0,-1],[1.5,-1],[1.5,2],[0,2],[0,-1]]]})},
        {'name': 'geographic', 'crs': 'EPSG:4326', 'geojson': fc({'type': 'Point', 'coordinates': [113,23]})},
        {'name': 'invalid', 'crs': 'EPSG:32649', 'geojson': fc({'type': 'Polygon', 'coordinates': [[[0,0],[2,2],[0,2],[2,0],[0,0]]]})},
        {'name': 'mismatch', 'crs': 'EPSG:32649', 'geojson': fc({'type': 'Point', 'coordinates': [0,0]}, {'other': 1})},
    ]
    tasks = []
    def step(verb, **parameters):
        return {'verb': verb, 'parameters': parameters}
    def add(title, prompt, steps, expected, injection=None, category='composition'):
        tid = f'T{len(tasks)+1:02d}'
        tasks.append({'task_id': tid, 'title': title, 'category': category, 'prompt': prompt,
                      'reference_call': {'name': 'task_dry_run', 'arguments': {'goal': title, 'steps': steps}},
                      'expected': expected, 'injection': injection,
                      'choice_verbs': [s['verb'] for s in steps],
                      'prediction': 'largest naming effect' if category in ('distinction','recovery') else 'smaller naming effect'})
    def output(count, **extra):
        return {'state': 'PASS', 'output': {'feature_count': count, **extra}}
    def preview(items, ok=True):
        return {'ok': ok, 'data': {'preview': {'ok': ok, 'workspace_writes': 0, 'steps': items}}}
    copy_step = step('gis_copy_dataset', source='source', target='job_copy')
    validate_step = step('gis_validate_geometry', dataset='$previous', repair=False)
    add('分组融合再缓冲', '把 source 按 group_code 分组融合，再逐组作 1 米缓冲，保留三个组；仅预演。',
        [step('gis_dissolve', input='source', target='job_groups', fields=['group_code']),step('gis_buffer', input='$previous', target='job_buffer', distance=1, unit='m', dissolve=False)], preview([output(3),output(3)]))
    add('坐标转换', '将 geographic 的坐标真正变换到 EPSG:32649，输出新副本；仅预演。不要只改 CRS 标签。',
        [step('gis_reproject', input='geographic', target='job_projected', target_crs='EPSG:32649')], preview([{'state':'PASS','effects':{'crs_changed':True}}]), category='distinction')
    add('合并为新数据集', '把 source 和 second 的同构记录合并为新的 job_merged，原逻辑对象不变；仅预演。',
        [step('gis_merge_datasets', inputs=['source','second'], target='job_merged')], preview([output(10)]), category='distinction')
    add('恢复步骤授权字段错误', '修复给出的失败方案，复制 source 后检查几何，授权与幂等字段应放在控制面；仅预演。',
        [copy_step,validate_step], preview([output(5),{'state':'PASS','result_summary':{'scalars':{'valid':True}}}]),
        {'goal':'bad control', 'steps':[step('gis_copy_dataset', source='source', target='job_copy', authorization_id='misplaced'),validate_step]}, category='recovery')
    add('统计在丢字段之前', '先对 source 的 score 求和，再按 group_code 融合。融合只保留分组字段；仅预演，统计在前。',
        [step('gis_field_statistics', dataset='source', fields=['score'], group_by=[], statistics=['sum'], output='result', null_policy='exclude'),step('gis_dissolve', input='source', target='job_groups', fields=['group_code'])], preview([{'state':'PASS'},output(3)]))
    add('核心路线复制', '只用核心能力复制 source 为新数据集并预演；不要调用或安装 QGIS、ArcPy 或服务后端。', [copy_step],preview([output(5)]),category='distinction')
    add('裁剪几何', '用 mask 裁切 source 的多边形几何，保留来源属性；仅预演。', [step('gis_clip',input='source',clip_by='mask',target='job_clip')],preview([output(3)]),category='distinction')
    add('选择完整记录', '选择 source 中与矩形 POLYGON ((0 -1,1.5 -1,1.5 2,0 2,0 -1)) 相交的完整记录，不裁切；仅预演。',
        [step('gis_spatial_filter',table='source',predicate='intersects',reference_wkt='POLYGON ((0 -1,1.5 -1,1.5 2,0 2,0 -1))')],preview([{'state':'PASS'}]),category='distinction')
    add('相交叠加', '求 source 与 mask 的多边形相交叠加，保留输入侧属性；仅预演。', [step('gis_overlay',input='source',overlay='mask',target='job_intersection',operation='intersection')],preview([output(3)]),category='distinction')
    add('差集叠加', '从 source 的多边形中减去 mask 覆盖部分；仅预演。', [step('gis_overlay',input='source',overlay='mask',target='job_difference',operation='difference')],preview([{'state':'PASS'}]),category='distinction')
    add('面积量测', '在 EPSG:32649 下量测 POLYGON ((0 0,2 0,2 3,0 3,0 0)) 的面积，单位平方米；仅预演。',
        [step('gis_measure',operation='area',a_wkt='POLYGON ((0 0,2 0,2 3,0 3,0 0))',crs='EPSG:32649')],preview([{'state':'PASS','result_summary':{'scalars':{'value':6.0}}}]))
    add('米制缓冲', '对 source 作 2 米缓冲，保留每条记录，不全体融合；仅预演。', [step('gis_buffer',input='source',target='job_buffer',distance=2,unit='m',dissolve=False)],preview([output(5)]))
    add('缓冲后检查几何', 'source 作 1 米逐记录缓冲后检查有效性；仅预演，第二步引用紧邻成果。',
        [step('gis_buffer',input='source',target='job_buffer',distance=1,unit='m',dissolve=False),validate_step],preview([output(5),{'state':'PASS','result_summary':{'scalars':{'valid':True}}}]))
    add('仅改坐标系标签', '仅将 source 副本的 CRS 标签改为 EPSG:32650，不移动数值坐标；仅预演。',
        [step('gis_assign_projection',dataset='source',target='job_relabel',crs='EPSG:32650')],preview([output(5)]),category='distinction')
    add('全体融合', '将 source 全部面融合为一个组，不保留属性；仅预演，显式给空分组字段。',
        [step('gis_dissolve',input='source',target='job_all',fields=[])],preview([output(1)]))
    add('数值统计排除空值', '对 source 的 score 计算 sum 和 count，明确排除空值；仅预演。',
        [step('gis_field_statistics',dataset='source',fields=['score'],group_by=[],statistics=['sum','count'],output='result',null_policy='exclude')],preview([{'state':'PASS'}]))
    add('复制而非重命名', '保留 source 原对象，复制为新数据集 job_copy；仅预演，不要重命名。',[copy_step],preview([output(5)]),category='distinction')
    add('字段计算副本', '将 second 的 score 乘以 2，产生 job_calculated 副本；仅预演。空值会被拒绝，请先预演并如实报告错误，不自行删行。',
        [step('gis_calculate_field',dataset='second',target='job_calculated',field='score',expression='score * 2')],{'ok':False,'error':{'code':'SCHEMA_MISMATCH'}},category='scope_rejection')
    add('添加字段副本', '给 source 的新副本增加 integer 字段 flag，nullable=false，default=1；仅预演。',
        [step('gis_add_field',dataset='source',target='job_added',field={'name':'flag','type':'integer','nullable':False,'default':1})],preview([output(5)]))
    add('地理坐标缓冲拒绝', '直接在 geographic 上预演 1 米缓冲，确认明确拒绝地理 CRS；不要猜投影或静默修复。',
        [step('gis_buffer',input='geographic',target='job_badbuffer',distance=1,unit='m',dissolve=False)],{'ok':False,'error':{'code':'NOT_SUPPORTED'}},category='scope_rejection')
    add('异构合并拒绝', '预演 source 与 mismatch 合并为新数据集，确认字段不兼容会被拒绝；不要静默改字段。',
        [step('gis_merge_datasets',inputs=['source','mismatch'],target='job_badmerge')],{'ok':False,'error':{'code':'SCHEMA_MISMATCH'}},category='scope_rejection')
    add('无效几何诊断', '只诊断 invalid 的几何有效性，不修复；仅预演，valid=false 是成功诊断。',
        [step('gis_validate_geometry',dataset='invalid',repair=False)],preview([{'state':'PASS','result_summary':{'scalars':{'valid':False}}}]))
    add('恢复目标名串链错误', '修复给出的失败方案：复制 source 后检查几何，使用正确的紧邻数据引用；仅预演。',
        [copy_step,validate_step],preview([output(5),{'state':'PASS','result_summary':{'scalars':{'valid':True}}}]),
        {'goal':'bad reference','steps':[copy_step,step('gis_validate_geometry',dataset='job_copy',repair=False)]},category='recovery')
    add('恢复步骤幂等字段错误', '修复给出的失败方案：按 group_code 融合 source，步骤内不应包含控制面幂等字段；仅预演并保留 null 组。',
        [step('gis_dissolve',input='source',target='job_groups',fields=['group_code'])],preview([output(3)]),
        {'goal':'bad request key','steps':[step('gis_dissolve',input='source',target='job_groups',fields=['group_code'],request_id='misplaced')]},category='recovery')
    g0 = ROOT / 'evidence/a3-a6-20261113/g0'
    write(g0/'tasks.json', {'format':'a3-taskset-v1','fixtures':fixtures,'tasks':tasks})
    write(g0/'protocol.json', {
        'format':'a3-protocol-v1','tool_profile':'compact','surfaces':['canonical','surface-v1'],
        'models':[{'slot':'A','base_url':'https://api.deepseek.com','model':'deepseek-flash','catalog_name':'DeepSeek-V4.1-Flash','key_env':'DEEPSEEK_API_KEY'},
                  {'slot':'B','base_url':'https://api.deepseek.com','model':'deepseek-v4-pro','catalog_name':'DeepSeek-V4-Pro','key_env':'DEEPSEEK_API_KEY'}],
        'identity_policy':'Pin catalog bytes and requested/returned model IDs; retain system_fingerprint each response; stop on unexpected returned ID or within-model fingerprint drift. Provider offers no immutable snapshot guarantee.',
        'request':{'temperature':0,'thinking':{'type':'disabled'},'max_tokens':2048},
        'max_turns':8,'max_tool_calls':24,'max_request_chars':180000,'timeout_seconds':60,
        'main_trajectories':96,'max_infrastructure_reruns':9,'max_total_trajectories':105,
        'retry_policy':'Only network/timeout/5xx with no provider/model output anywhere in that trajectory. Never rerun model errors, partial output or unknown provider output. All attempts retained.',
        'stop_http_statuses':[401,402,403,429], 'schedule_seed':20261113,
        'adoption':{'selection_improvement_percentage_points':5,'recovery_must_not_decrease':True,'require_complete_96':True,'require_no_model_selection_regression':True},
        'selection':'First substantive dry-run proposal: compare canonical verbs positionally with frozen choices; missing/extra slots are wrong. Discovery calls excluded. Direct operation calls count as substantive choices but cannot satisfy preview-only completion.',
        'success':'One complete canonical dry-run call matches frozen argument constraints and frozen result subset. No writes. Earlier failed proposals are retained; host stops at first matching complete preview. Literal target names may vary; inputs, enum/numeric/field arguments and dependency references must match.',
        'first_try':'First substantive proposal passes the frozen oracle with no previous model tool errors.',
        'recovery':'Frozen injected-error tasks plus tasks with an observed tool error; denominator zero is null.',
        'scope':'24 bounded GIS preview tasks, 6 synthetic immutable fixture inputs, no external GIS services or production writes; comparison is two DeepSeek models, not cross-provider generalization.',
        'system_prompt':'Use the provided GIS tools to complete the user task. All tasks require actual preview evidence, including read-only operations. Submit a complete multi-step task as ONE preview chain. Discover schemas as needed. Do not write datasets, plans, grants or external resources. Do not claim success without tool evidence. The host ends the trajectory when the complete preview satisfies the task. Synthetic task fixtures have already been imported by the host.'})
    write(g0/'predictions.json', {'frozen_before_models':True,'predictions':[
        {'tasks':[t['task_id'] for t in tasks if t['category']=='distinction'],'prediction':'Largest positive surface effect on relabel/transform, new merge/append, whole-record select/clip distinctions.'},
        {'tasks':[t['task_id'] for t in tasks if t['category']=='recovery'],'prediction':'Explicit workflow names and schema keys may improve correction; recovery must not decrease.'},
        {'tasks':[t['task_id'] for t in tasks if t['category'] not in ('distinction','recovery')],'prediction':'Smaller or no improvement; longer aliases may increase token use. Negative results retained.'}]})
    print('Prepared 24 tasks, 2 models, 2 surfaces; 0 model generation calls.')


if __name__ == '__main__':
    prepare()
