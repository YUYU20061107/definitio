const $ = (selector, root = document) => root.querySelector(selector);
const $$ = (selector, root = document) => [...root.querySelectorAll(selector)];
const esc = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const short = value => value ? `${value.slice(0, 12)}…${value.slice(-6)}` : '—';
const state = {datasets: [], documents: new Map(), active: null, visible: new Set(), task: null, tasks: [], capabilities: null, zoom: 1, pan: [0, 0], busy: false, bounds: null, projection: null, tab: 'layers'};
const colors = ['#7c9f75','#d9b77a','#7faaa5','#ac9abb','#91b792','#cd9b84'];
const labels = {DECLARED: '待确认', PRECHECKED: '预检完成', EXECUTING: '执行中', COMMITTED: '已完成', FAILED: '失败', ISOLATED: '已隔离', BLOCKED: '已阻断'};
const fieldNames = {target:'输出名称', input:'输入数据', dataset:'数据集', source:'源数据', inputs:'输入数据列表', clip_by:'裁剪边界', overlay:'叠加数据', fields:'字段', group_by:'分组字段', statistics:'统计方法', distance:'距离', unit:'单位', dissolve:'合并缓冲结果', target_crs:'目标 CRS', crs:'CRS', field:'字段定义 / 名称', expression:'算术表达式', operation:'运算', format:'格式', repair:'自动修复', output:'输出方式', null_policy:'空值策略', table:'输入数据', predicate:'空间谓词', reference_wkt:'参考 WKT', a_wkt:'几何 WKT', algorithm:'能力名称'};
const icon = '<svg viewBox="0 0 24 24"><path d="m3 7 9-5 9 5-9 5Zm0 5 9 5 9-5M3 17l9 5 9-5"/></svg>';

async function api(path, body) {
  const response = await fetch(`/api${path}`, body === undefined ? {} : {method: 'POST', headers: {'Content-Type':'application/json'}, body: JSON.stringify(body)});
  const data = await response.json();
  if (!response.ok) {
    const error = data.error || {code: 'HTTP_ERROR', message: typeof data.detail === 'string' ? data.detail : '请求未通过校验'};
    throw Object.assign(new Error(error.message), {code: error.code, details: error.details});
  }
  return data;
}

function toast(message) {
  $('#toast').textContent = message;
  $('#toast').hidden = false;
  clearTimeout(state.toastTimer);
  state.toastTimer = setTimeout(() => $('#toast').hidden = true, 3800);
}

function errorText(error) { return `${error.code || 'ERROR'} · ${error.message}${error.details ? '\n' + JSON.stringify(error.details, null, 2) : ''}`; }
function showError(error, element = $('#task-error')) { element.textContent = errorText(error); element.hidden = false; }
function badge(status) { return `<span class="status-pill ${['FAILED','ISOLATED','BLOCKED'].includes(status) ? 'failed' : status === 'DECLARED' ? 'pending' : ''}">${esc(labels[status] || status)}</span>`; }

function setBusy(busy) {
  state.busy = busy;
  $('#plan-button').disabled = busy || !state.active;
  $('#status-message').textContent = busy ? '正在计算，请稍候…' : '本地工作区 · 数据保存在本机';
}

function openModal(title, eyebrow, content) {
  $('#modal-title').textContent = title;
  $('#modal-eyebrow').textContent = eyebrow;
  $('#modal-body').innerHTML = content;
  if (!$('#modal').open) $('#modal').showModal();
}
function closeModal() { $('#modal').close(); }
$('#modal-close').onclick = closeModal;
$('#modal').addEventListener('click', event => { if (event.target === $('#modal')) { const r = $('#modal').getBoundingClientRect(); if (event.clientX < r.left || event.clientX > r.right || event.clientY < r.top || event.clientY > r.bottom) closeModal(); } });

function switchTab(tab) {
  state.tab = tab;
  for (const name of ['layers','tools','history']) $(`#${name}-pane`).hidden = name !== tab;
  $$('[data-nav]').forEach(b => b.classList.toggle('active', b.dataset.nav === tab));
  if (tab === 'history') renderHistory();
  mobileView('data');
}
$$('[data-nav]').forEach(b => b.onclick = () => switchTab(b.dataset.nav));
function mobileView(view) { document.body.dataset.view = view; $$('[data-mobile]').forEach(b => b.classList.toggle('active', b.dataset.mobile === view)); }
$$('[data-mobile]').forEach(b => b.onclick = () => mobileView(b.dataset.mobile));

async function refresh(preferred) {
  [state.datasets, state.tasks] = await Promise.all([api('/datasets'), api('/tasks')]);
  if (preferred) state.active = preferred;
  if (!state.datasets.some(d => d.id === state.active)) state.active = state.datasets[0]?.id ?? null;
  await Promise.all(state.datasets.filter(d => !state.documents.has(d.id)).map(async d => state.documents.set(d.id, await api(`/datasets/${d.id}`))));
  if (state.active && state.visible.size === 0) state.visible.add(state.active);
  renderLayers(); renderMap(); renderHistory(); setBusy(state.busy);
  $('#workspace-label').textContent = state.datasets.length ? '空间分析项目' : '未命名项目';
}

function active() { return state.documents.get(state.active); }
function datasetName(ref) { return ref === '$previous' ? '上一步成果' : state.datasets.find(d => d.id === ref)?.name || ref; }

function renderLayers() {
  $('#layer-count').textContent = state.datasets.length;
  $('#layer-list').innerHTML = state.datasets.length ? state.datasets.map((d, i) => `<div class="layer-item ${d.id === state.active ? 'active' : ''}"><input class="layer-visibility" type="checkbox" data-visible="${esc(d.id)}" aria-label="显示 ${esc(d.name)}" ${state.visible.has(d.id) ? 'checked' : ''}><button class="layer-select" data-select="${esc(d.id)}" title="${esc(d.name)}"><span class="layer-swatch" style="background:${colors[i % colors.length]}"></span><span><strong>${esc(d.name)}</strong><small>${d.feature_count} 条要素 · ${esc(d.geometry_types.join(' / ') || '空数据集')}</small></span></button><span class="layer-kind">${d.source.kind === 'derived' ? 'OUT' : 'VEC'}</span></div>`).join('') : '<p class="empty-note">图层会出现在这里。<br>导入 GeoJSON，或载入示例项目。</p>';
  $$('[data-select]').forEach(button => button.onclick = () => { state.active = button.dataset.select; state.visible.add(state.active); state.zoom = 1; state.pan = [0,0]; $('#feature-popover').hidden = true; renderLayers(); renderMap(); });
  $$('[data-visible]').forEach(input => input.onchange = () => { input.checked ? state.visible.add(input.dataset.visible) : state.visible.delete(input.dataset.visible); renderMap(); });
  const d = active();
  $('#active-data-name').textContent = d?.name || '请先选择一个图层';
  $('#dataset-details').hidden = !d;
  if (!d) return;
  $('#dataset-details').innerHTML = `<div class="detail-title">图层信息</div><div class="detail-row"><span>坐标系</span><strong>${esc(d.crs.code)}</strong></div><div class="detail-row"><span>坐标单位</span><strong>${esc(d.crs.unit)}</strong></div><div class="detail-row"><span>要素数量</span><strong>${d.feature_count}</strong></div><div class="detail-row"><span>来源</span><strong>${d.source.kind === 'synthetic_demo' ? '合成演示数据' : d.source.kind === 'derived' ? '任务分析成果' : '用户导入'}</strong></div><div class="detail-row"><span>无效 / 空几何</span><strong>${d.invalid_geometry_count} / ${d.empty_geometry_count}</strong></div><div class="detail-title" style="margin-top:20px">属性字段</div>${d.fields.map(f => `<span class="field-pill">${esc(f.name)}<span>${esc(f.type)}</span></span>`).join('') || '<span class="muted">无业务字段</span>'}<div class="fingerprint" title="${esc(d.fingerprint)}">SHA256 · ${esc(short(d.fingerprint))}</div><button class="button secondary small full" id="attributes-button">查看属性表</button><a class="button secondary small full" href="/api/datasets/${esc(d.id)}/export" download>下载数据 <span>↓</span></a>`;
  $('#attributes-button').onclick = () => {
    const fields = d.fields.map(f => f.name);
    openModal(`${d.name} · 属性表`, `${d.feature_count} FEATURES`, `<p class="modal-copy">显示前 100 条记录。导出包含全部要素与显式 CRS 元数据。</p><div style="overflow:auto"><table class="result-table"><thead><tr><th>记录</th>${fields.map(f => `<th>${esc(f)}</th>`).join('')}</tr></thead><tbody>${d.geojson.features.slice(0,100).map(f => `<tr><td>${f.id}</td>${fields.map(k => `<td>${f.properties[k] === null ? '<i class="muted">null</i>' : esc(f.properties[k])}</td>`).join('')}</tr>`).join('')}</tbody></table></div>`);
  };
}

function walkCoordinates(geometry, visit) {
  if (!geometry) return;
  if (geometry.type === 'GeometryCollection') { geometry.geometries.forEach(g => walkCoordinates(g, visit)); return; }
  function walk(coords) { if (coords.length && typeof coords[0] === 'number') visit(coords); else coords.forEach(walk); }
  walk(geometry.coordinates);
}

function renderMap() {
  const d = active();
  const layers = state.datasets.filter(x => state.visible.has(x.id) && d && x.crs.definition_hash === d.crs.definition_hash);
  const points = [];
  layers.forEach(layer => state.documents.get(layer.id)?.geojson.features.forEach(f => walkCoordinates(f.geometry, p => points.push(p))));
  $('#map-empty').hidden = state.datasets.length > 0;
  $('#map-title').textContent = d?.name || '空间画布';
  $('#map-crs').textContent = d?.crs.code || 'CRS 未指定';
  const hidden = [...state.visible].filter(id => {const doc = state.documents.get(id); return doc && d && doc.crs.definition_hash !== d.crs.definition_hash;}).length;
  $('#coordinate-display').textContent = hidden ? `${hidden} 个不同 CRS 图层未叠加 · 请先重投影` : '本地矢量画布 · 无在线底图';
  $('#map-world').innerHTML = '';
  $('#map-legend').hidden = !points.length;
  $('#map-scale').hidden = !points.length;
  if (!points.length) return;
  let xmin=Infinity,ymin=Infinity,xmax=-Infinity,ymax=-Infinity;
  points.forEach(([x,y]) => {xmin=Math.min(xmin,x);xmax=Math.max(xmax,x);ymin=Math.min(ymin,y);ymax=Math.max(ymax,y);});
  const dx=Math.max(xmax-xmin,1e-5),dy=Math.max(ymax-ymin,1e-5),scale=Math.min(740/dx,465/dy);
  const cx=(xmin+xmax)/2,cy=(ymin+ymax)/2;
  state.bounds=[xmin,ymin,xmax,ymax]; state.projection={cx,cy,scale};
  const project=([x,y]) => [500+(x-cx)*scale,350-(y-cy)*scale];
  function line(coords,close=false) { if(!coords.length)return ''; return coords.map((p,i)=>`${i?'L':'M'}${project(p).map(n=>n.toFixed(3)).join(',')}`).join(' ')+(close?'Z':''); }
  function path(g) {
    if(!g)return '';
    switch(g.type){
      case 'Polygon':return g.coordinates.map(r=>line(r,true)).join(' ');
      case 'MultiPolygon':return g.coordinates.flatMap(p=>p.map(r=>line(r,true))).join(' ');
      case 'LineString':return line(g.coordinates);
      case 'MultiLineString':return g.coordinates.map(r=>line(r)).join(' ');
      case 'Point':{const [x,y]=project(g.coordinates);return `M${x-4},${y}a4,4 0 1,0 8,0a4,4 0 1,0 -8,0`;}
      case 'MultiPoint':return g.coordinates.map(p=>path({type:'Point',coordinates:p})).join(' ');
      case 'GeometryCollection':return g.geometries.map(path).join(' ');
      default:return '';
    }
  }
  const legend=new Map();
  // Draw derived/selected data last so it remains visible above source layers.
  layers.sort((a,b)=>(a.id===state.active)-(b.id===state.active));
  for(const layer of layers){
    const index=state.datasets.findIndex(x=>x.id===layer.id);
    for(const f of state.documents.get(layer.id).geojson.features){
      const isLine=f.geometry?.type.includes('Line');
      const isBoundary=layer.name==='研究边界';
      const group=f.properties.group_code;
      const groupIndex=group==='A'?0:group==='B'?1:group==='C'?2:index%colors.length;
      const color=isBoundary?'#325d4c':isLine?'#94a597':colors[groupIndex];
      const geometryPath=path(f.geometry);
      if(!geometryPath)continue;
      const node=document.createElementNS('http://www.w3.org/2000/svg','path');
      node.setAttribute('d',geometryPath);node.setAttribute('fill',isLine||isBoundary?'none':color);node.setAttribute('stroke',isBoundary?'#315947':isLine?'#8fa187':'#5d7e56');
      node.setAttribute('fill-opacity',layer.id===state.active?'.72':'.24');node.setAttribute('fill-rule','evenodd');
      if(isBoundary)node.setAttribute('stroke-dasharray','5 4');
      if(isLine)node.style.strokeWidth='3.5px';
      node.setAttribute('tabindex','0');node.setAttribute('role','button');node.setAttribute('aria-label',`${layer.name} 要素 ${f.id}`);
      node.addEventListener('click',()=>{if(state.dragged)return;showFeature(layer,f,node);});
      node.addEventListener('keydown',event=>{if(event.key==='Enter'){event.preventDefault();showFeature(layer,f,node);}});
      $('#map-world').append(node);
      const label=group!==undefined?`分组 ${group===null?'null':group}`:layer.name;
      legend.set(label,color);
    }
  }
  applyCamera();
  $('#map-legend').innerHTML=[...legend].slice(0,8).map(([label,color])=>`<span class="legend-item"><i style="--swatch:${color}"></i>${esc(label)}</span>`).join('');
  updateScale();
}

function applyCamera(){ $('#map-world').setAttribute('transform',`translate(${500+state.pan[0]},${350+state.pan[1]}) scale(${state.zoom}) translate(-500,-350)`); updateScale(); }
function updateScale(){
  if(!state.projection||!active())return;
  const metres=active().crs.metres_per_unit;const width=$('#map').getBoundingClientRect().width;
  const units=100/(state.projection.scale*state.zoom)*(1000/Math.max(width,1));
  $('#scale-text').textContent=metres?`${(units*metres).toLocaleString('zh-CN',{maximumFractionDigits:1})} m`:`${units.toFixed(4)} °`;
  $('#map-scale div').style.width='100px';
}
$('#zoom-in').onclick=()=>{state.zoom=Math.min(16,state.zoom*1.3);applyCamera();};
$('#zoom-out').onclick=()=>{state.zoom=Math.max(.25,state.zoom/1.3);applyCamera();};
$('#zoom-fit').onclick=()=>{state.zoom=1;state.pan=[0,0];renderMap();};
const map=$('#map');
map.addEventListener('wheel',e=>{e.preventDefault();state.zoom=Math.max(.25,Math.min(16,state.zoom*Math.exp(-e.deltaY*.001)));applyCamera();},{passive:false});
let drag=null;
map.addEventListener('pointerdown',event=>{drag={x:event.clientX,y:event.clientY,pan:[...state.pan]};state.dragged=false;});
map.addEventListener('pointermove',event=>{
  const point=new DOMPoint(event.clientX,event.clientY).matrixTransform(map.getScreenCTM().inverse());
  if(drag&&event.buttons){const dx=event.clientX-drag.x,dy=event.clientY-drag.y;if(Math.hypot(dx,dy)>4)state.dragged=true;const factor=1000/map.getBoundingClientRect().width;state.pan=[drag.pan[0]+dx*factor,drag.pan[1]+dy*factor];applyCamera();}
  const p=state.projection;if(p){const x=(point.x-500-state.pan[0])/state.zoom/p.scale+p.cx;const y=-(point.y-350-state.pan[1])/state.zoom/p.scale+p.cy;$('#coordinate-display').textContent=`X ${x.toFixed(2)}   Y ${y.toFixed(2)} · 本地画布`;}
});
window.addEventListener('pointerup',()=>{drag=null;setTimeout(()=>state.dragged=false,50);});
new ResizeObserver(updateScale).observe(map);

function showFeature(layer,f,node){
  $$('.feature-selected').forEach(el=>el.classList.remove('feature-selected'));node.classList.add('feature-selected');
  $('#feature-popover').innerHTML=`<div class="popover-head"><strong>要素 ${f.id}</strong><button aria-label="关闭要素详情">×</button></div><span class="eyebrow">${esc(layer.name)}</span>${Object.entries(f.properties).map(([k,v])=>`<div class="detail-row"><span>${esc(k)}</span><strong>${v===null?'null':esc(v)}</strong></div>`).join('')}`;
  $('#feature-popover').hidden=false;$('#feature-popover button').onclick=()=>{$('#feature-popover').hidden=true;node.classList.remove('feature-selected');};
}

async function loadDemo(){
  if(state.busy)return;setBusy(true);
  try{const result=await api('/demo',{confirm:true});state.visible=new Set(result.dataset_refs);state.zoom=1;state.pan=[0,0];await refresh(result.dataset_refs[0]);toast('已载入 3 个合成图层；可开始规划任务');mobileView('map');}
  catch(e){toast(errorText(e));}finally{setBusy(false);}
}
$('#demo-button').onclick=loadDemo;$('#empty-demo').onclick=loadDemo;

function importModal(){
  openModal('导入空间数据','ADD A DATASET',`<form id="import-form"><div class="notice">支持二维 GeoJSON FeatureCollection（最多 8 MB / 10,000 条）。坐标系必须显式确认，不会猜测。</div><div class="form-row"><label for="import-file">GeoJSON 文件</label><input type="file" id="import-file" accept=".geojson,.json,application/geo+json,application/json" required></div><div class="form-grid"><div class="form-row"><label for="import-name">图层名称</label><input type="text" id="import-name" required maxlength="64" placeholder="例如：研究区地块"></div><div class="form-row"><label for="import-crs">显式坐标系</label><input type="text" id="import-crs" required placeholder="EPSG:4326"><small>有投影坐标的数据请填写其真实 CRS。</small></div></div><div id="import-preview" class="modal-copy"></div><div class="inline-error" id="import-error" role="alert" hidden></div><div class="modal-actions"><button type="button" class="button secondary" id="import-cancel">取消</button><button type="submit" class="button primary" id="import-submit">确认导入</button></div></form>`);
  let data=null;
  $('#import-file').onchange=async event=>{
    $('#import-error').hidden=true;data=null;const file=event.target.files[0];if(!file)return;
    try{if(file.size>8*1024*1024)throw new Error('文件超过 8 MB');data=JSON.parse(await file.text());if(data.type!=='FeatureCollection'||!Array.isArray(data.features))throw new Error('需要 FeatureCollection');$('#import-name').value=file.name.replace(/\.(geojson|json)$/i,'').replace(/[^\w\u4e00-\u9fff .()（）-]/g,'_').slice(0,64);$('#import-crs').value=data.definitio?.crs?.code||data.crs?.properties?.name||'';$('#import-preview').textContent=`已读取 ${data.features.length} 条要素，请确认图层名称与 CRS。`;}
    catch(e){showError(e,$('#import-error'));}
  };
  $('#import-cancel').onclick=closeModal;
  $('#import-form').onsubmit=async event=>{event.preventDefault();if(!data){showError(new Error('请先选择可读取的文件'),$('#import-error'));return;}$('#import-submit').disabled=true;try{const d=await api('/datasets',{name:$('#import-name').value,crs:$('#import-crs').value,geojson:data,schema:data.definitio?.schema,confirm:true});state.visible.add(d.id);await refresh(d.id);state.zoom=1;state.pan=[0,0];renderMap();closeModal();toast('数据已导入，原文件保持不变');mobileView('map');}catch(e){showError(e,$('#import-error'));}finally{if($('#import-submit'))$('#import-submit').disabled=false;}};
}
$('#import-button').onclick=importModal;$('#import-icon').onclick=importModal;

$$('[data-goal]').forEach(button=>button.onclick=()=>{$('#goal').value=button.dataset.goal;$('#goal').focus();mobileView('agent');});
$('#goal-form').onsubmit=async event=>{event.preventDefault();if(state.busy||!state.active)return;setBusy(true);$('#task-error').hidden=true;try{state.task=await api('/tasks/plan',{goal:$('#goal').value,dataset:state.active});renderTask();await refresh();$('#task-view').scrollIntoView({block:'nearest',behavior:'smooth'});}catch(e){showError(e);}finally{setBusy(false);}};

function renderTask(){
  const task=state.task;if(!task){$('#task-view').innerHTML='';return;}
  const output=state.documents.get(task.output_refs?.at(-1));
  $('#task-view').innerHTML=`<section class="task-card"><div class="task-head"><h3>${task.task_state==='DECLARED'?'执行计划':'任务结果'}</h3>${badge(task.task_state)}</div><p class="task-goal">${esc(task.goal)}</p>${task.steps.map(step=>`<div class="plan-step"><div class="step-heading"><span class="step-index">${step.index}</span>${esc(step.label)}<span class="step-level">${step.level}</span></div><div class="step-operation">${esc(step.verb)}</div><div class="step-params">${Object.entries(step.parameters).filter(([k])=>k!=='backend').map(([key,value])=>`<div>${esc(fieldNames[key]||key)}：<b>${esc(['input','source','dataset','clip_by','overlay','table'].includes(key)?datasetName(value):typeof value==='object'?JSON.stringify(value):value)}</b></div>`).join('')}</div>${step.state!=='DECLARED'?`<div class="step-outcome">${esc(labels[step.state]||step.state)}${step.output_ref?' · 新成果已保存':''}</div>`:''}${step.error?`<div class="step-error">${esc(step.error.code)} · ${esc(step.error.message)}</div>`:''}${step.evidence_ref?`<button class="evidence-link" data-evidence="${esc(step.evidence_ref)}">查看执行证据 ↗</button>`:''}${step.result?`<button class="evidence-link" data-result="${esc(step.id)}" style="margin-left:12px">查看检查结果 ↗</button>`:''}</div>`).join('')}${task.task_state==='DECLARED'?`<div class="plan-actions"><button class="button primary full" id="execute-button">${task.governance_state.confirmation_required?'审核并确认执行':'运行只读检查'} <span>→</span></button></div><p class="task-footnote">规划器：${esc(task.planner==='local-intent-grammar-v1'?'本地规则 · 未接入大模型':'显式工具参数')}<br>计划 SHA256 · ${esc(short(task.plan_digest))}</p>`:''}${output?`<div class="result-metrics"><div class="result-metric"><strong>${output.feature_count}</strong><span>输出要素</span></div><div class="result-metric"><strong>${task.steps.length}</strong><span>任务步骤</span></div><div class="result-metric"><strong>${task.evidence_refs.length}</strong><span>证据记录</span></div></div>`:''}${task.task_state!=='DECLARED'?`<div class="plan-actions"><a class="button secondary small full" href="/api/tasks/${esc(task.id)}/bundle" download>下载完整证据包 ↓</a></div>`:''}<p class="task-footnote">本地执行证据不等于独立几何验证或 L1–L3 认证。${task.completion==='partial'?'本任务部分完成；前序已提交成果保留，后续步骤被阻断。':''}</p></section>`;
  if($('#execute-button'))$('#execute-button').onclick=()=>task.governance_state.confirmation_required?confirmTask():executeTask(false);
  $$('[data-evidence]').forEach(button=>button.onclick=()=>evidenceModal(button.dataset.evidence));
  $$('[data-result]').forEach(button=>button.onclick=()=>{const step=task.steps.find(s=>s.id===button.dataset.result);openModal('只读检查结果','READ-ONLY RESULT',`<pre class="code-block">${esc(JSON.stringify(step.result,null,2))}</pre>`);});
}

function confirmTask(){
  const task=state.task;
  openModal('确认这次空间分析','REVIEW BEFORE EXECUTION',`<p class="modal-copy">确认后将按以下计划创建新成果。已导入图层保持不变；每一步独立提交，失败时后续步骤停止。</p>${task.steps.filter(s=>s.level!=='R0').map(step=>{const p=step.parameters;const src=state.documents.get(p.input||p.dataset||p.source);const dropped=step.verb==='gis_dissolve'?src?.fields.filter(f=>!p.fields.includes(f.name)).map(f=>f.name)||[]:step.verb==='gis_buffer'&&p.dissolve?src?.fields.map(f=>f.name)||[]:[];return `<div class="confirm-item"><strong>${step.index}. ${esc(step.label)} · ${step.level}</strong><div>新成果：${esc(p.target)}</div>${dropped.length?`<div class="notice warning" style="margin:9px 0 0">输出将不保留这些业务字段：${esc(dropped.join('、'))}。请确认其中是否含稳定业务键。</div>`:''}<div class="muted">参数：${esc(JSON.stringify(p))}</div></div>`;}).join('')}<div class="notice">缓冲区为投影平面计算；跨后端等价、容差校准与未开放能力不包含在本次承诺内。</div><label class="checkbox-line"><input type="checkbox" id="confirm-check"><span>我已核对输出名称、参数及字段变化，确认执行此计划。</span></label><div class="modal-actions"><button class="button secondary" id="cancel-execute">返回检查</button><button class="button primary" id="confirm-execute" disabled>确认并执行</button></div>`);
  $('#confirm-check').onchange=()=>$('#confirm-execute').disabled=!$('#confirm-check').checked;
  $('#cancel-execute').onclick=closeModal;
  $('#confirm-execute').onclick=()=>{closeModal();executeTask(true);};
}

async function executeTask(confirm){
  if(state.busy)return;setBusy(true);$('#task-error').hidden=true;
  try{state.task=await api(`/tasks/${state.task.id}/execute`,{plan_digest:state.task.plan_digest,confirm});const output=state.task.output_refs.at(-1);if(output)state.visible.add(output);await refresh(output);state.zoom=1;state.pan=[0,0];renderMap();renderTask();toast(state.task.task_state==='COMMITTED'?'任务已完成，成果与证据已保存':`任务${labels[state.task.task_state]}：${state.task.error?.message}`);}
  catch(e){showError(e);}finally{setBusy(false);}
}

function renderHistory(){
  $('#history-list').innerHTML=state.tasks.length?state.tasks.map(task=>`<button class="history-item" data-task="${esc(task.id)}">${badge(task.task_state)}<strong>${esc(task.goal)}</strong><small>${new Date(task.created_at).toLocaleString('zh-CN',{month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit'})} · ${task.steps.length} 步 · ${task.evidence_refs.length} 条证据</small></button>`).join(''):'<p class="empty-note">还没有任务。<br>描述目标或选择工具，生成第一份执行计划。</p>';
  $$('[data-task]').forEach(button=>button.onclick=async()=>{try{state.task=await api(`/tasks/${button.dataset.task}`);renderTask();mobileView('agent');$('#task-view').scrollIntoView({block:'start',behavior:'smooth'});}catch(e){toast(errorText(e));}});
}

async function evidenceModal(id){
  try{const evidence=await api(`/evidence/${id}`);openModal('执行证据','TRACEABLE BY DESIGN',`<div class="notice">这些核验来自实际数据库读回。与计算路线共享内核，未构成独立几何 oracle，也不是 G1 验收结果。</div><div class="detail-row"><span>动词</span><strong>${esc(evidence.verb)}</strong></div><div class="detail-row"><span>实际路线</span><strong>${esc(evidence.implementation_route)}</strong></div><div class="detail-row"><span>输入保持不变</span><strong>${evidence.input_unchanged?'是 · 指纹一致':'否'}</strong></div><div class="detail-row"><span>输入 / 输出要素</span><strong>${evidence.input_feature_count} / ${evidence.output_feature_count??'只读结果'}</strong></div>${evidence.postcondition_checks.map(check=>`<div class="evidence-check"><span>${esc(check.id)}</span><strong>${check.passed?'通过':'失败'}</strong></div>`).join('')}<div class="evidence-meta">证据摘要 SHA256<br>${esc(evidence.evidence_digest)}</div><details><summary style="font-size:11px;cursor:pointer">完整证据 JSON</summary><pre class="code-block">${esc(JSON.stringify(evidence,null,2))}</pre></details>`);}catch(e){toast(errorText(e));}
}

function renderTools(){
  const implemented=state.capabilities.verbs.filter(c=>c.implemented&&!['V03','V04'].includes(c.vid));
  $('#tool-list').innerHTML=implemented.map(c=>`<button class="tool-item" data-tool="${esc(c.name)}">${icon}<span><strong>${esc(c.label)}</strong><small>${esc(c.vid)} · ${esc(c.local_level)}</small></span><span>↗</span></button>`).join('');
  $$('[data-tool]').forEach(button=>button.onclick=()=>toolModal(button.dataset.tool));
}

const defaults={
  gis_create_dataset:{target:'新建数据集',crs:'EPSG:32649',fields:[{name:'name',type:'string',nullable:true}]},
  gis_add_field:{field:{name:'new_value',type:'number',nullable:true,default:null}},
  gis_calculate_field:{field:'height',expression:'height * 2'},
  gis_buffer:{distance:20,unit:'m',dissolve:false},gis_dissolve:{fields:['group_code']},
  gis_reproject:{target_crs:'EPSG:4326'},gis_assign_projection:{crs:'EPSG:32649'},
  gis_convert_format:{format:'geojson'},gis_overlay:{operation:'intersection'},
  gis_field_statistics:{fields:['population'],group_by:[],statistics:['count','sum','min','max','mean'],output:'result',null_policy:'exclude'},
  gis_validate_geometry:{repair:false},gis_spatial_filter:{predicate:'intersects',reference_wkt:'POLYGON ((500000 2560000,500200 2560000,500200 2560200,500000 2560200,500000 2560000))'},
  gis_measure:{operation:'area',a_wkt:'POLYGON ((0 0,100 0,100 100,0 100,0 0))',crs:'EPSG:32649'}
};

function toolModal(verb){
  const cap=state.capabilities.verbs.find(c=>c.name===verb);
  const params={backend:'local-geos',...defaults[verb]};
  const d=active();
  for(const key of cap.required_parameters){
    if(['input','dataset','source','table'].includes(key))params[key]=d?.id||'';
    if(['clip_by','overlay'].includes(key))params[key]=state.datasets.find(x=>x.id!==d?.id)?.id||'';
    if(key==='inputs')params[key]=[d?.id,...state.datasets.filter(x=>x.id!==d?.id).map(x=>x.id).slice(0,1)].filter(Boolean);
    if(key==='target'&&verb!=='gis_create_dataset')params.target=`${d?.name.slice(0,34)||'空间数据'}_${cap.label.slice(0,8)}_${Math.random().toString(36).slice(2,6)}`;
  }
  const dataOptions=state.datasets.map(x=>`<option value="${esc(x.id)}">${esc(x.name)} · ${x.feature_count} 要素</option>`).join('');
  openModal(cap.label,`${cap.vid} · ${cap.local_level} · LOCAL GEOS`, `<form id="tool-form"><p class="modal-copy">明确参数后先生成计划。${cap.local_level==='R0'?'只读操作不创建数据成果。':'写操作会创建独立的新成果，执行前需要确认。'}</p>${cap.required_parameters.map(key=>{
    const value=params[key];
    if(['input','dataset','source','table','clip_by','overlay'].includes(key))return `<div class="form-row"><label for="param-${key}">${esc(fieldNames[key]||key)}</label><select id="param-${key}" data-param="${key}" required>${dataOptions}</select></div>`;
    const isObj=typeof value==='object';const isBool=typeof value==='boolean';
    return `<div class="form-row"><label for="param-${key}">${esc(fieldNames[key]||key)}${isObj?'（JSON）':''}</label>${isBool?`<select id="param-${key}" data-param="${key}" data-type="boolean"><option value="false">false</option><option value="true">true</option></select>`:isObj?`<textarea class="code-input" style="min-height:72px" id="param-${key}" data-param="${key}" data-type="json">${esc(JSON.stringify(value,null,2))}</textarea>`:`<input type="${typeof value==='number'?'number':'text'}" step="any" id="param-${key}" data-param="${key}" data-type="${typeof value==='number'?'number':'string'}" value="${esc(value??'')}" required>`}</div>`;
  }).join('')}${verb==='gis_field_statistics'?'<div class="notice">空值策略固定为显式 exclude；同时报告 null_count。</div>':''}${verb==='gis_buffer'?'<div class="notice">仅投影平面缓冲。端点 round、连接 round、每四分之一圆 8 段。</div>':''}<div id="tool-error" class="inline-error" role="alert" hidden></div><details><summary style="font-size:10px;cursor:pointer">查看本地接口参数</summary><pre class="code-block">${esc(JSON.stringify(params,null,2))}</pre></details><div class="modal-actions"><button class="button secondary" id="tool-cancel" type="button">取消</button><button class="button primary" type="submit" id="tool-plan">生成执行计划 →</button></div></form>`);
  for(const key of cap.required_parameters){const input=$(`#param-${key}`);if(input?.tagName==='SELECT')input.value=String(params[key]??'');}
  $('#tool-cancel').onclick=closeModal;
  $('#tool-form').onsubmit=async event=>{
    event.preventDefault();$('#tool-plan').disabled=true;$('#tool-error').hidden=true;
    try{const p={backend:'local-geos'};for(const input of $$('[data-param]'))p[input.dataset.param]=input.dataset.type==='json'?JSON.parse(input.value):input.dataset.type==='boolean'?input.value==='true':input.dataset.type==='number'?Number(input.value):input.value;if(verb==='gis_field_statistics')p.null_policy='exclude';state.task=await api('/tasks/plan',{goal:`${cap.label} · ${d?.name||'新数据'}`,steps:[{verb,parameters:p}]});closeModal();renderTask();await refresh();mobileView('agent');$('#task-view').scrollIntoView({block:'start',behavior:'smooth'});}catch(e){showError(e,$('#tool-error'));}finally{if($('#tool-plan'))$('#tool-plan').disabled=false;}
  };
}

$('#about-button').onclick=()=>{
  if(!state.capabilities)return;
  const c=state.capabilities;
  openModal('一份清晰的能力边界','DEFINITIO · LOCAL REFERENCE RUNTIME',`<p class="modal-copy">Definitio 将操作契约、任务执行与结果证据连成一个本地空间工作台。当前为独立开发版，G1 基线与四后端注册表保持原样。</p><div class="notice"><strong>${c.implemented} / ${c.total} 个动词已有本地实现</strong><br>二维 GeoJSON 矢量；GEOS 几何计算，GDAL/PROJ 坐标转换。本地规则规划器可拆解明确任务，尚未接入大模型。</div><p class="modal-copy">尚未实现：栅格分析、点云、外部服务发布、四后端连接、破坏性 R3 操作。未通过 G1 正式验收或 L1–L3 认证；未作跨后端等价或容差已校准声明。</p><div class="cap-grid">${c.verbs.filter(v=>v.implemented).map(v=>`<div class="cap-item">${esc(v.label)}<small>${esc(v.vid)} · ${esc(v.local_level)} · 本地实现</small></div>`).join('')}</div><details style="margin-top:18px"><summary style="font-size:11px;cursor:pointer">运行环境与版本</summary><pre class="code-block">${esc(JSON.stringify(c.versions,null,2))}</pre></details>`);
};

async function initialize(){
  try{const [health,capabilities]=await Promise.all([api('/health'),api('/capabilities')]);state.capabilities=capabilities;$('#connection').innerHTML='<span></span> 本地运行中';$('#capability-count').textContent=`${capabilities.implemented} 个本地动词 · GEOS ${health.versions.geos} · 未认证`;renderTools();await refresh();if(state.tasks.length){state.task=state.tasks[0];renderTask();}setBusy(false);}
  catch(e){$('#connection').textContent='本地服务未连接';$('#status-message').textContent='连接失败，请检查运行时';showError(e);}
}
mobileView('map');initialize();
