/* Existing ledger + read-only result snapshots. No source parsing or adoption writes. */
(function(g){
'use strict';
const esc=g.v5Review.esc, ledger=g.v45Ledger, renderer=g.v4ResultCell;
const request=p=>g.v4Api.sourceRequest(p);
const human=v=>/^原文未注明@/.test(v||'')?'原文未注明（来源独立保留）':v==='not_reported'?'版本未注明':v||'原文未注明';
const studyNames={release:'放行检测',long_term:'长期稳定性',accelerated:'加速稳定性',stress:'影响因素'};
const selectionFields=['section','batch','study','condition','sample_type','presentation','purpose','orientation','tableMode'];
const display=g.v012Display={data:null,serial:0,chart:null,filters:{},cards:new Map(),queue:[],active:0,
 visible(){return [...renderer.cellsBySlot.values()].filter(c=>ledger.match(c.slot_key));},
 unmount(){++this.serial;this.observer?.disconnect();for(const card of this.cards.values())card.chart?.destroy();this.cards.clear();this.queue=[];this.chart=null;this.trend=null;document.getElementById('v012ResultsPanel')?.remove();document.getElementById('v013TableTools')?.remove();},
 save(){if(!ledger.project)return;try{localStorage.setItem('results-view:'+ledger.project,JSON.stringify(Object.fromEntries(selectionFields.map(k=>[k,ledger[k]||'']))));}catch{}},
 restore(project,cells){
  if(ledger.project===project)return;
  let state={};try{state=JSON.parse(localStorage.getItem('results-view:'+project)||'{}');}catch{}
  ledger.project=project;
  for(const k of selectionFields)ledger[k]=typeof state[k]==='string'?state[k]:'';
  if(!['release','stability','stress'].includes(ledger.section))ledger.section='stability';
  if(!cells.some(c=>ledger.category(c.slot_key.study)===ledger.section)&&!state.section)ledger.section=ledger.category(cells[0]?.slot_key.study);
  ledger.orientation=ledger.orientation==='batches'?'batches':'items';
 },
 async load(project,filters={}){
  if(renderer.activeLoadingProject!==project){
   document.getElementById('v4LedgerSourceControls')?.replaceChildren();
   document.getElementById('ledgerSamplePills')?.replaceChildren();document.getElementById('ledgerConditionPills')?.replaceChildren();
   const table=document.getElementById('ledgerDataTable');if(table)table.innerHTML='<tbody><tr><td role="status">正在读取当前项目的已入库结果…</td></tr></tbody>';
   const banner=document.getElementById('ledgerBannerGrid');if(banner)banner.textContent='正在读取当前项目结果…';
   renderer.clearCells();this.data=null;
  }
  const sequence=++renderer.loadReqSeq;renderer.activeLoadingProject=project;renderer.loading=true;renderer.loadError='';
  this.unmount();
  try{
   const params=new URLSearchParams(Object.entries(filters).filter(([,v])=>v!=null));
   const data=await request(`/projects/${encodeURIComponent(project)}/result-display?${params}`);
   if(sequence!==renderer.loadReqSeq||renderer.activeLoadingProject!==project)return;
   if(data.project!==project||data.cells.length!==data.total)throw Error('结果范围不完整，请重新读取。');
   renderer.clearCells();renderer.currentBaseRevision=data.base_revision;data.cells.forEach(c=>renderer.registerCell(c));
   this.data=data;this.filters=filters;ledger.labels=data.item_labels;renderer.loading=false;renderer.enhanceLedgerTable();
  }catch(error){
   if(sequence!==renderer.loadReqSeq)return;
   this.data=null;renderer.clearCells();renderer.loading=false;
   renderer.loadError=error.status===404&&error.code==='HTTP_ERROR'?'后端尚未加载结果展示接口，请关闭旧服务并重新运行 start.cmd（无需修改数据库）':error.message;
   renderer.enhanceLedgerTable();
  }
 },
 mount(){
  if(g.v4Api.mode!=='real'||g.v5Review.scene!=='formal')return;
  const table=document.getElementById('ledgerDataTable');if(!table)return;
  table.parentElement.classList.add('v012-matrix-frame','v013-matrix-frame');
  table.parentElement.dataset.compact=ledger.tableMode==='compact'?'true':'false';
  const old=document.getElementById('v3LedgerCharts');if(old){old.hidden=true;if(old.previousElementSibling?.classList.contains('v3-label'))old.previousElementSibling.hidden=true;}
  const cells=this.visible(),ids=new Set(cells.map(c=>c.slot_id));
  const groups=(this.data?.analysis_groups||[]).filter(a=>a.slot_ids.some(id=>ids.has(id)));
  const signature=JSON.stringify([this.data?.project,this.data?.base_revision,...selectionFields.map(k=>ledger[k])]);
  let node=document.getElementById('v012ResultsPanel');if(node?.dataset.signature===signature)return;
  this.unmount();node=document.createElement('section');node.id='v012ResultsPanel';node.className='v013-results';node.dataset.signature=signature;
  table.parentElement.after(node);
  const tools=document.createElement('div');tools.id='v013TableTools';tools.className='v013-table-tools';
  tools.innerHTML=`<span>完整结果表 · 当前范围 ${cells.length} 个结果槽位</span><button type="button" class="btn btn-outline" data-table-mode>${ledger.tableMode==='compact'?'展开完整表格':'紧凑滚动查看'}</button><button type="button" class="btn btn-primary" id="v012MatrixExcel" ${!cells.length||!g.v4Api.can('export')?'disabled':''}>导出当前样品台账 Excel</button>`;
  table.parentElement.before(tools);
  tools.querySelector('[data-table-mode]').onclick=()=>ledger.change('tableMode',ledger.tableMode==='compact'?'full':'compact');
  tools.querySelector('#v012MatrixExcel').onclick=()=>this.exportSlots(cells.map(c=>c.slot_id),false);
  const numeric=groups.filter(a=>(a.exact_numeric_count??a.numeric_count)>0),qualitative=groups.filter(a=>!(a.exact_numeric_count??a.numeric_count));
  const cardMarkup=a=>`<article class="card v013-trend-card" data-trend-card="${esc(a.id)}"><div class="card-header"><h4>${esc(a.label)}${a.unit?' ('+esc(a.unit)+')':''}</h4><span class="v013-trend-badge">正式采用值</span></div><div class="card-body"><p class="v013-identity">${esc(a.key.sample_type)} · ${esc(human(a.key.presentation))} · ${esc(studyNames[a.key.study])} · ${esc(human(a.key.condition))}<br>${esc(a.key.method)} · ${esc(human(a.key.method_version))} · ${esc(a.key.reporting_basis||'共用报告结果')}</p><p data-trend-status role="status">滚动到此图时读取正式采用值…</p><div class="v013-canvas"><canvas role="img" aria-label="${esc(a.label)} 正式采用值趋势图"></canvas></div><details class="v013-curve-options"><summary>叠加兼容批次</summary>${a.batches.map(batch=>`<label><input type="checkbox" data-curve-batch value="${esc(batch)}" ${batch===ledger.batch?'checked':''}>${esc(batch)}</label>`).join('')}</details>${g.v014Axes.markup()}<div data-omitted></div><p data-point-detail role="status"></p><button type="button" class="btn btn-outline" data-retry>重新读取本图</button><button type="button" class="btn btn-outline" data-chart-export disabled>导出本图 Excel / PNG</button><span data-export-status role="status"></span><span data-downloads></span></div></article>`;
  node.innerHTML=`<div class="v3-label">趋势图</div><p class="v3-note">当前批次 ${esc(ledger.batch)} · 正式采用值 · 数据版本 ${esc(renderer.currentBaseRevision)}。表格保留双状态；未关联适用标准时不绘制限度线，不作合格判定。</p><div id="v013TrendGrid" class="v013-trend-grid">${numeric.map(cardMarkup).join('')}</div>${qualitative.length?`<details id="v013Qualitative"><summary>定性、限值及其他结果（${qualitative.length}）</summary><div class="v013-trend-grid">${qualitative.map(cardMarkup).join('')}</div></details>`:''}<div class="v013-export-message"><span id="v012ExportStatus" role="status"></span><span id="v012Downloads"></span></div>`;
  const advanced=document.getElementById('v5OutputControls');if(advanced)node.after(advanced);
  if(!groups.length){node.insertAdjacentHTML('beforeend',`<p role="status">${esc(renderer.loadError?'结果读取失败：'+renderer.loadError:'当前范围没有已入库结果。')}</p>`);return;}
  this.observer=new IntersectionObserver(entries=>{for(const entry of entries)if(entry.isIntersecting){const state=this.cards.get(entry.target.dataset.trendCard);if(state&&!state.started){this.observer.unobserve(entry.target);this.loadCard(state);}}},{rootMargin:'250px 0px'});
  for(const group of groups){
   const card=node.querySelector(`[data-trend-card="${group.id}"]`),state={group,node:card,sequence:0,chart:null,trend:null,started:false};this.cards.set(group.id,state);
   card.querySelectorAll('[data-curve-batch]').forEach(input=>input.onchange=()=>this.loadCard(state));
   card.querySelector('[data-retry]').onclick=()=>this.loadCard(state);
   card.querySelector('[data-chart-export]').onclick=()=>this.exportSlots(state.slotIds,true,state);
   g.v014Axes.bind(card,()=>this.loadCard(state));
   this.observer.observe(card);
  }
 },
 loadCard(state){
  state.started=true;const sequence=++state.sequence;
  if(this.chart===state.chart){this.chart=null;this.trend=null;}state.chart?.destroy();state.chart=null;state.trend=null;state.slotIds=[];
  state.node.querySelector('[data-chart-export]').disabled=true;
  state.node.querySelector('[data-downloads]').replaceChildren();state.node.querySelector('[data-export-status]').textContent='';
  state.node.querySelector('[data-omitted]').replaceChildren();state.node.querySelector('[data-point-detail]').replaceChildren();
  const batches=[...state.node.querySelectorAll('[data-curve-batch]:checked')].map(i=>i.value),status=state.node.querySelector('[data-trend-status]');
  if(!batches.length){status.textContent='请至少选择一个批次。';return;}
  let axis;try{axis=g.v014Axes.options(state.node);}catch(e){status.textContent=e.message;return;}
  status.textContent='正在读取正式采用值…';
  this.queue.push({state,sequence,generation:this.serial,project:this.data.project,revision:this.data.base_revision,batches,axis});this.drain();
 },
 drain(){
  while(this.active<3&&this.queue.length){
   const task=this.queue.shift(),{state,sequence,generation,project,revision,batches,axis}=task;
   if(sequence!==state.sequence||generation!==this.serial||!state.node.isConnected)continue;
   this.active++;
   const params=new URLSearchParams(Object.entries(this.filters).filter(([,v])=>v!=null));params.set('group_id',state.group.id);params.set('base_revision',revision);batches.forEach(b=>params.append('batches',b));Object.entries(axis).forEach(([k,v])=>params.set(k,v));
   const current=()=>sequence===state.sequence&&generation===this.serial&&state.node.isConnected&&g.v5Review.scene==='formal';
   request(`/projects/${encodeURIComponent(project)}/result-trend?${params}`).then(trend=>{
    if(!current())return;
    if(trend.revision!==renderer.currentBaseRevision)throw Error('图表与表格数据版本不同，请刷新结果。');
    this.drawCard(state,trend);
   }).catch(error=>{if(current()){state.node.querySelector('[data-trend-status]').textContent='趋势读取失败：'+error.message+'；请重读本图，无需修改数据库。';state.node.querySelector('[data-chart-export]').disabled=true;}}).finally(()=>{this.active--;this.drain();});
  }
 },
 drawCard(state,trend){
  const node=state.node;g.v014Axes.describe(node,trend.axis);state.trend=trend;state.slotIds=[...new Set(trend.series.flatMap(s=>s.values.map(p=>p.slot_id)).filter(Boolean))];
  node.querySelector('[data-chart-export]').disabled=!g.v4Api.can('export')||!state.slotIds.length;
  node.querySelector('[data-trend-status]').textContent=trend.points.length?`正式采用 ${trend.points.length} 点${trend.points.length===1?' · 仅1个有效时间点':''}${trend.omitted.length?' · '+trend.omitted.length+'项未绘制':''}`:'无可绘制的正式精确数值；结果原文保留在上方完整表格中。';
  node.querySelector('[data-omitted]').innerHTML=trend.omitted.length?`<details><summary>未绘制结果及原因（${trend.omitted.length}）</summary>${trend.omitted.map(p=>`<p>${esc(p.batch)} · ${esc(p.time)} · ${esc(p.raw)} · ${esc(p.reason==='no adopted result'?'尚无正式采用值':p.reason==='pending'?'待确认':'非精确数值，保留原文')}</p>`).join('')}</details>`:'';
  node.querySelector('.v013-canvas').hidden=!trend.points.length;
  if(!trend.points.length)return;
  if(typeof g.Chart==='undefined')throw Error('本地图表库未能加载，结果表仍可查看。');
  const colors=['#0284c7','#7c3aed','#16a34a','#d97706','#db2777'];
  const datasets=trend.series.map((s,i)=>({label:s.batch,borderColor:colors[i%colors.length],backgroundColor:colors[i%colors.length],tension:0,spanGaps:false,pointRadius:3,data:s.values.map(p=>({...p,y:p.value===null?null:Number(p.value)}))}));
  state.chart=new g.Chart(node.querySelector('canvas'),{type:'line',data:{datasets},options:{animation:false,responsive:true,maintainAspectRatio:false,parsing:{xAxisKey:'x',yAxisKey:'y'},plugins:{legend:{display:true,labels:{font:{size:10}}},tooltip:{callbacks:{label:ctx=>`${ctx.dataset.label} · ${ctx.raw.time||'放行'} · 正式采用 ${ctx.raw.value} ${trend.scope[8]}`}}},scales:{x:{type:'linear',title:{display:true,text:trend.scope[9]==='D'?'考察时间（天）':trend.scope[9]==='M'?'考察时间（月）':'批次'},ticks:trend.scope[9]==='release'?{callback:x=>trend.batches[x]||''}:{precision:0}},y:{min:trend.axis.min,max:trend.axis.max,title:{display:true,text:trend.scope[8]||'原文未注明单位'}}},onClick:(_e,elements)=>{if(elements.length){const hit=elements[0],point=datasets[hit.datasetIndex].data[hit.index];if(point.slot_id)this.inspectPoint(point,state);}}}});
  if(!this.chart){this.chart=state.chart;this.trend=trend;}
 },
 inspectPoint(point,state){
  const cell=renderer.cellsBySlot.get(point.slot_id);if(!cell)return;
  // Use the adopted version for the plot's evidence, not the latest display version.
  const adoptedCell={...cell,display:cell.adopted,display_status:'confirmed'};
  const n=state?.node.querySelector('[data-point-detail]');if(n)n.textContent=`${point.batch} · ${point.time} · 正式采用 ${point.value} ${point.unit} · 版本 ${point.version_id}`;
  g.v4SourceDrawer.openResultCell(adoptedCell).catch(e=>{if(n)n.textContent='来源读取失败：'+e.message;});
 },
 async exportSlots(ids,png,state=null){
  const panel=document.getElementById('v012ResultsPanel'),node=state?.node||panel;if(!node||!ids?.length)return;
  const project=this.data.project,revision=this.data.base_revision,generation=this.serial,sequence=state?.sequence;
  const current=()=>node.isConnected&&this.data?.project===project&&this.data?.base_revision===revision&&generation===this.serial&&(!state||state.sequence===sequence);
  const status=state?node.querySelector('[data-export-status]'):panel.querySelector('#v012ExportStatus'),box=state?node.querySelector('[data-downloads]'):panel.querySelector('#v012Downloads');
  const button=state?node.querySelector('[data-chart-export]'):document.getElementById('v012MatrixExcel');button.disabled=true;box.replaceChildren();status.textContent='正在生成并反读核验…';
  try{
   const result=await g.v4Api.requestExport({project,base_revision:revision,filters:{},slot_ids:ids,include_png:png,...(state?.trend?{axis_mode:state.trend.axis.mode,axis_min:state.trend.axis.min,axis_max:state.trend.axis.max}:{}),orientation:ledger.orientation||'items'});
   if(!current())return;
   status.textContent=`已核验 · ${ids.length} 个结果槽位 · 数据版本 ${revision} · Excel双状态${png?' / PNG正式采用值':''}`;
   for(const [url,label,name] of [[result.download_url,'下载 Excel','results.xlsx'],[result.image_download_url,'下载 PNG','trend.png']])if(url){const a=document.createElement('button');a.type='button';a.className='btn btn-outline';a.textContent=label;a.onclick=()=>g.v5Outputs.download(url,name).catch(e=>status.textContent='下载失败：'+e.message);box.append(a);}
  }catch(error){if(current())status.textContent='导出失败：'+error.message+'；未改动任何结果。';}
  finally{if(current())button.disabled=!g.v4Api.can('export');}
 }
};
const originalLoad=renderer.loadProjectCells;
renderer.loadProjectCells=function(project,filters={}){return g.v4Api.mode==='real'?display.load(project,filters):originalLoad.call(this,project,filters);};
const originalMatch=ledger.match;
ledger.match=function(key){return originalMatch.call(this,key)&&['sample_type','presentation','purpose'].every(k=>!this[k]||key[k]===this[k]);};
const originalControls=ledger.controls;
ledger.controls=function(cells,project){
 display.restore(project,cells);
 const batches=[...new Set(cells.map(c=>c.slot_key.batch))].sort((a,b)=>a.localeCompare(b,undefined,{numeric:true}));
 if(!batches.includes(this.batch))this.batch=batches[0]||'';
 const batchCells=cells.filter(c=>c.slot_key.batch===this.batch),conditionMap=new Map();
 for(const c of batchCells){const k=c.slot_key,key=JSON.stringify([k.study,k.condition]);if(!conditionMap.has(key))conditionMap.set(key,{study:k.study,condition:k.condition,cells:[]});conditionMap.get(key).cells.push(c);}
 const order={long_term:0,accelerated:1,stress:2,release:3};
 const conditions=[...conditionMap.values()].sort((a,b)=>order[a.study]-order[b.study]||a.condition.localeCompare(b.condition));
 const chosen=conditions.find(c=>c.study===this.study&&c.condition===this.condition)||conditions.find(c=>c.study===this.study)||conditions.find(c=>ledger.category(c.study)===this.section)||conditions[0];
 if(chosen){this.study=chosen.study;this.condition=chosen.condition;this.section=this.category(chosen.study);}
 const scoped=chosen?.cells||[];
 for(const key of ['sample_type','presentation','purpose'])if(this[key]&&!scoped.some(c=>c.slot_key[key]===this[key]))this[key]='';
 originalControls.call(this,cells,project);
 const samples=document.getElementById('ledgerSamplePills'),host=document.getElementById('ledgerConditionPills');
 if(samples){
  if(samples.previousElementSibling)samples.previousElementSibling.textContent='样品批号：';
  samples.innerHTML=batches.map(batch=>{const keys=cells.filter(c=>c.slot_key.batch===batch).map(c=>c.slot_key),types=[...new Set(keys.map(k=>k.sample_type))].join('/'),specs=[...new Set(keys.map(k=>k.presentation).filter(v=>v&&!v.startsWith('原文未注明')))];return `<button type="button" class="purpose-btn ${batch===this.batch?'active':''}" data-result-batch="${esc(batch)}" aria-pressed="${batch===this.batch}">${esc(batch)}${types?' ('+esc(types)+(specs.length===1?' · '+esc(specs[0]):specs.length>1?' · 多规格':'')+')':''}</button>`;}).join('');
  samples.querySelectorAll('[data-result-batch]').forEach(button=>button.onclick=()=>this.change('batch',button.dataset.resultBatch));
 }
 if(host){
  if(host.previousElementSibling)host.previousElementSibling.textContent='考察条件：';
  const range=c=>{if(c.study==='release')return '放行结果';const parts=[];for(const [field,unit] of [['month','个月'],['day','天']]){const values=c.cells.map(x=>x.slot_key[field]).filter(v=>v!=null);if(values.length)parts.push(Math.min(...values)+'~'+Math.max(...values)+unit);}return parts.join(' / ');};
  host.innerHTML=conditions.map((c,index)=>`<button type="button" class="purpose-btn ${c===chosen?'active':''}" data-result-condition="${index}" aria-pressed="${c===chosen}">${({long_term:'🔵',accelerated:'🟠',stress:'🔥',release:'🟢'})[c.study]} ${esc(studyNames[c.study])} (${esc(human(c.condition))} · ${esc(range(c))})</button>`).join('');
  host.querySelectorAll('[data-result-condition]').forEach(button=>button.onclick=()=>{const c=conditions[Number(button.dataset.resultCondition)];this.study=c.study;this.section=this.category(c.study);this.change('condition',c.condition);});
  const extras=document.createElement('details');extras.id='v012ExtraFilters';extras.innerHTML='<summary>更多筛选 / 表格方向</summary>'+['sample_type','presentation','purpose'].map(k=>`<label>${esc({sample_type:'材料类别',presentation:'规格',purpose:'用途'}[k])}<select data-result-filter="${k}"><option value="">全部</option>${[...new Set(scoped.map(c=>c.slot_key[k]))].map(v=>`<option value="${esc(v)}" ${this[k]===v?'selected':''}>${esc(k==='purpose'?({reference:'参考品',routine:'常规样品',unknown:'用途未注明'}[v]):human(v))}</option>`).join('')}</select></label>`).join('');
  if(this.section==='release')extras.innerHTML+=`<label>表格方向<select data-result-filter="orientation"><option value="items" ${this.orientation!=='batches'?'selected':''}>按检测项目查看</option><option value="batches" ${this.orientation==='batches'?'selected':''}>按批次查看</option></select></label>`;
  extras.querySelectorAll('select').forEach(select=>select.onchange=()=>this.change(select.dataset.resultFilter,select.value));host.append(extras);
 }
 const exportButton=document.querySelector('#tab-ledger > .card .card-header .btn-primary');
 if(exportButton){exportButton.textContent='导出当前样品台账 Excel';exportButton.onclick=()=>display.exportSlots(display.visible().map(c=>c.slot_id),false);exportButton.disabled=!g.v4Api.can('export')||!display.visible().length;}
 display.save();
};
const originalChange=ledger.change;
ledger.change=function(field,value){display.unmount();originalChange.call(this,field,value);display.save();};
const originalFormal=ledger.formal;
ledger.formal=function(cells,view){
 if(g.v4Api.mode!=='real'||view.loading||view.loadError)return originalFormal.call(this,cells,view);
 const groups=new Map();
 for(const c of cells.filter(c=>this.match(c.slot_key))){const k=c.slot_key,time=k.day!=null?'D':k.month!=null?'M':'release';const key=JSON.stringify([this.section==='release'?'':k.batch,k.sample_type,k.presentation,k.study,k.study_id,k.condition,k.purpose,time]);if(!groups.has(key))groups.set(key,[]);groups.get(key).push(c);}
 if(!groups.size)return '<tbody><tr><td>当前范围暂无已入库结果，请调整分区、批次或条件。</td></tr></tbody>';
 const render=c=>c?view.renderCellContent(c,'')+((!c.adopted||c.display.result_version_id!==c.adopted.result_version_id)?'<small class="v012-value-note">曲线仅使用正式采用值；无采用值时不画点</small>':''):'—';
 let html='';
 for(const list of groups.values()){
  const k=list[0].slot_key,release=k.study==='release',timeUnit=k.day!=null?'D':'M';
  const times=release?[...new Set(list.map(c=>c.slot_key.batch))]:[...new Set(list.map(c=>c.slot_key.day??c.slot_key.month))].sort((a,b)=>a-b);
  const rows=new Map();for(const c of list){const key=JSON.stringify([c.slot_key.item,c.slot_key.method,c.slot_key.method_version,c.slot_key.reporting_basis,c.unit]);if(!rows.has(key))rows.set(key,[]);rows.get(key).push(c);}
  const rowList=[...rows.values()],transpose=release&&this.orientation==='batches';
  const caption=[release?'放行检测':k.batch,k.sample_type,human(k.presentation),studyNames[k.study],k.condition,human(k.study_id),{reference:'参考品',routine:'常规样品',unknown:'用途未注明'}[k.purpose]].filter(Boolean).map(esc).join(' · ');
  const standard=row=>{const values=[...new Set(row.flatMap(c=>(c.standards||[]).map(s=>s.criterion_text).filter(Boolean)))];return values.length?'<span class="v013-standard-note">原文标准 · 适用性未确认</span>'+values.map(esc).join('<hr>'):'<span class="v013-standard-note">未关联适用标准</span>';};
  const label=row=>[ledger.labels[row[0].slot_key.item]||row[0].slot_key.item,row[0].slot_key.reporting_basis,row[0].unit].filter(Boolean).map(esc).join(' · ');
  html+=`<tbody><tr><th colspan="${transpose?rowList.length+2:times.length+4}">${caption}</th></tr>`;
  if(transpose){html+=`<tr><th>批号</th>${rowList.map(row=>`<th>${label(row)}<br>${esc(row[0].slot_key.method)} · ${esc(human(row[0].slot_key.method_version))}<br>${standard(row)}</th>`).join('')}<th>结论</th></tr>`;for(const t of times)html+=`<tr><th>${esc(t)}</th>${rowList.map(row=>`<td>${render(row.find(c=>c.slot_key.batch===t))}</td>`).join('')}<td>未评价</td></tr>`;}
  else{html+=`<tr><th>检测项目 / 子结果</th><th>方法 / 版本</th><th>质量标准 / 原文记录</th>${times.map(t=>`<th>${release?esc(t):esc(t)+(timeUnit==='D'?'天':'个月')}</th>`).join('')}<th>结论</th></tr>`;for(const row of rowList)html+=`<tr><td>${label(row)}</td><td>${esc(row[0].slot_key.method)}<br>${esc(human(row[0].slot_key.method_version))}</td><td class="v013-standard">${standard(row)}</td>${times.map(t=>{const c=row.find(c=>release?c.slot_key.batch===t:(c.slot_key.day??c.slot_key.month)===t);return `<td ${c?`data-slot-id="${esc(c.slot_id)}"`:''}>${render(c)}</td>`;}).join('')}<td class="v013-conclusion">未评价</td></tr>`;}
  html+='</tbody>';
 }
 return html;
};
const originalMount=g.v5Outputs.mount;
g.v5Outputs.mount=function(){originalMount.call(this);display.mount();};
const originalScene=g.v5Review.setScene;
g.v5Review.setScene=function(scene){if(scene!=='formal'){display.unmount();document.getElementById('ledgerDataTable')?.parentElement.classList.remove('v012-matrix-frame','v013-matrix-frame');}return originalScene.call(this,scene);};
const originalLegacyCharts=g.renderLedgerCharts;
g.renderLedgerCharts=function(...args){if(g.v4Api.mode==='real')return;return originalLegacyCharts?.apply(this,args);};
})(window);
