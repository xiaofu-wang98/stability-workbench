/* Quick comparison in the existing V3 workspace. Preview POSTs are read-only. */
(function(g){
'use strict';
const r=g.v014Research,esc=g.v5Review.esc,axes=g.v014Axes;
const names={release:'放行',long_term:'长期',accelerated:'加速',stress:'应力'};
const roles={selected:'按批次对比',before:'变更前',after:'变更后'};
const fields={presentation:'规格',study_id:'原稳定性研究编号'};
const human=v=>/^原文未注明@/.test(v||'')?'原文未注明（独立来源）':v==='not_reported'?'版本未注明':v==='unknown'?'用途未注明':v==='reference'?'参考品':v==='routine'?'常规样品':v||'未注明';
const get=p=>g.v4Api.sourceRequest(p);
const post=(p,v,signal)=>g.v4Api.sourceRequest(p,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(v),signal});
const key=o=>JSON.stringify([o.study,o.condition]);
const baseEnter=r.enter,baseClear=r.clear;
r.qControllers=new Set();r.qActive=0;r.qQueue=[];
r.clear=function(){clearTimeout(this.qTimer);for(const c of this.qControllers)c.abort();this.qControllers.clear();this.qQueue=[];baseClear.call(this);};
r.enter=async function(tab,project,options={}){
 if(tab!=='comparability')return baseEnter.call(this,tab,project);
 this.clear();this.tab=tab;this.project=project;this.selected=null;const token=this.serial;
 const root=document.getElementById('tab-comparability');root.innerHTML='<p role="status">正在读取可选批次与检测范围…</p>';
 try{
  const catalog=await get(`/projects/${encodeURIComponent(project)}/comparison-catalog`),data=catalog;
  if(token!==this.serial||project!==currentProjectKey)return;
  this.data=data;this.catalog=catalog;
  this.q={ids:new Set(),roles:new Map(),filters:{},aligned_fields:[],alignment_reason:'',tab:'trend',search:'',grouping:false,dirty:false};
  const saved=data.studies.find(s=>s.id===options.studyId);
  if(saved)this.useStudy(saved,false);
  const context=this.ledgerContext;
  if(context?.project===project){
   this.q.ids=new Set(catalog.samples.filter(s=>s.batch===context.batch&&(!context.sample_type||s.sample_type===context.sample_type)&&(!context.presentation||s.presentation===context.presentation)&&(!context.purpose||s.purpose===context.purpose)).map(s=>s.id));
   this.q.filters={study:context.study,condition:context.condition,item:context.item};
   this.ledgerContext=null;
  }
  this.studyPage(root);
 }catch(e){if(token===this.serial){root.innerHTML=`<p role="alert">读取批次失败：${esc(e.message)}</p><button class="btn btn-outline" data-q-reconnect>重新读取</button>`;root.querySelector('button').onclick=()=>this.enter(tab,project);}}
};
r.useStudy=function(study,render=true){
 this.selected=study;const available=new Set(this.catalog.samples.map(s=>s.id));
 this.q.ids=new Set(study.members.filter(m=>['before','after','selected'].includes(m.role)&&available.has(m.sample_id)).map(m=>m.sample_id));
 this.q.roles=new Map(study.members.filter(m=>this.q.ids.has(m.sample_id)).map(m=>[m.sample_id,m.role]));
 this.q.filters=Object.fromEntries(Object.entries(study.filters||{}).filter(([,v])=>v!=null));
 this.q.allItems=!this.q.filters.item;
 this.q.aligned_fields=[...study.aligned_fields];this.q.alignment_reason=study.alignment_reason;this.q.dirty=false;
 if(render)this.studyPage(document.getElementById('tab-comparability'));
};
r.changed=function(){this.q.aligned_fields=[];this.q.alignment_reason='';this.q.dirty=true;this.studyPage(document.getElementById('tab-comparability'));};
r.facets=function(){
 const q=this.q,options=this.catalog.options.filter(o=>q.ids.has(o.sample_id));
 const conditions=[...new Map(options.map(o=>[key(o),o])).values()];
 if(!conditions.some(o=>o.study===q.filters.study&&o.condition===q.filters.condition)){
  const first=conditions[0];q.filters=first?{study:first.study,condition:first.condition}:{};
 }
 const scoped=options.filter(o=>o.study===q.filters.study&&o.condition===q.filters.condition),items=new Map();
 for(const o of scoped){const old=items.get(o.item)||{id:o.item,label:o.label,count:0};old.count+=o.exact_numeric_count;items.set(o.item,old);}
 const list=[...items.values()].sort((a,b)=>(b.count>0)-(a.count>0)||a.label.localeCompare(b.label,'zh'));
 if(q.filters.item&&!items.has(q.filters.item))delete q.filters.item;
 if(!q.allItems&&!q.filters.item&&list.length)q.filters.item=list[0].id;
 return {conditions,items:list};
};
r.studyPage=function(root){
 const q=this.q,{conditions,items}=this.facets(),batches=[...new Set(this.catalog.samples.map(s=>s.batch))].sort((a,b)=>a.localeCompare(b,undefined,{numeric:true}));
 root.innerHTML=`<div class="v3-toolbar"><strong>快速批次对比</strong><label>已保存研究 <select data-q-study><option value="">快速对比（不必先保存）</option>${this.data.studies.map(s=>`<option value="${esc(s.id)}" ${s.id===this.selected?.id?'selected':''}>${esc(s.title)}</option>`).join('')}</select></label><button class="btn btn-primary" data-q-show-trend disabled>查看趋势</button><button class="hub-button" data-q-save disabled>保存为研究</button><button class="hub-button" data-q-export-range disabled>导出当前范围</button><button class="hub-button" data-q-refresh>刷新数据</button></div>
 <div class="v015-workbench"><aside class="v015-sidebar"><section class="card v015-controls"><div class="card-body"><div class="v015-section-label"><strong>1 选择批次</strong><input type="search" data-q-search aria-label="搜索批号" placeholder="搜索批号" value="${esc(q.search)}"><button class="btn btn-outline" data-q-clear>清空选择</button><button class="btn btn-outline" data-q-group aria-expanded="${q.grouping}">批量分组（可选）</button></div>
 <p class="v3-note">选择后直接查看正式采用值曲线。前后分组、研究名称和保存都可稍后处理。</p>
 <div class="v015-batches">${batches.map(batch=>{
  const samples=this.catalog.samples.filter(s=>s.batch===batch),selected=samples.filter(s=>q.ids.has(s.id)),types=[...new Set(samples.map(s=>s.sample_type))].join('/');
  return `<div data-q-batch-row="${esc(batch)}"><button class="purpose-btn ${selected.length?'active':''}" data-q-batch="${esc(batch)}" aria-pressed="${selected.length===samples.length?'true':selected.length?'mixed':'false'}">${esc(batch)} · ${esc(types)}${selected.length&&selected.length<samples.length?' · 部分来源':''}</button><details><summary>来源／规格（${samples.length}）</summary>${samples.map(s=>`<label><input type="checkbox" data-q-sample="${esc(s.id)}" ${q.ids.has(s.id)?'checked':''}>${esc(s.sample_type)} · ${esc(human(s.presentation))} · ${esc(human(s.purpose))}<small>${s.slot_count} 条结果 · 来源身份 ${esc(s.id.slice(0,8))}</small></label>`).join('')}</details></div>`;
 }).join('')}</div><div data-q-bulk ${q.grouping?'':'hidden'}><p>勾选批次后批量指定分组；未分前后也可以成图。</p>${batches.filter(b=>this.catalog.samples.some(s=>s.batch===b&&q.ids.has(s.id))).map(b=>`<label><input type="checkbox" data-q-group-batch="${esc(b)}">${esc(b)} <small>${[...new Set(this.catalog.samples.filter(s=>s.batch===b&&q.ids.has(s.id)).map(s=>roles[q.roles.get(s.id)||'selected']))].map(esc).join('／')}</small></label>`).join('')}<div>${Object.entries(roles).map(([role,name])=>`<button class="btn btn-outline" data-q-role="${role}">设为${name}</button>`).join('')}</div><p data-q-bulk-status role="status"></p></div>
 <div class="v015-section-label"><strong>2 选择条件与检项</strong></div><div class="v015-condition-buttons">${conditions.map(o=>`<button class="purpose-btn ${o.study===q.filters.study&&o.condition===q.filters.condition?'active':''}" data-q-condition="${esc(key(o))}" aria-pressed="${o.study===q.filters.study&&o.condition===q.filters.condition}">${esc(names[o.study])} · ${esc(human(o.condition))}</button>`).join('')}</div>
 <label>检测项目 <select data-q-item ${!items.length?'disabled':''}><option value="*" ${q.allItems?'selected':''}>全部检项（按需加载）</option>${items.map(o=>`<option value="${esc(o.id)}" ${q.filters.item===o.id?'selected':''}>${esc(o.label)}${o.count?'':' · 暂无精确采用值'}</option>`).join('')}</select></label></div></section></aside><div class="v015-results">
 <div data-q-feedback role="status">${q.ids.size?'正在准备当前选择…':'请选择一个或多个批次，即可查看趋势。'}</div><div data-q-range-download role="status"></div><div data-q-alignment></div>
 <div class="v015-result-tabs" role="tablist" aria-label="对比结果"><button class="btn" role="tab" id="qTrendTab" aria-controls="qTrendPanel" data-q-tab="trend" aria-selected="${q.tab==='trend'}">趋势图</button><button class="btn" role="tab" id="qTableTab" aria-controls="qTablePanel" data-q-tab="table" aria-selected="${q.tab==='table'}">完整结果</button></div>
 <section id="qTrendPanel" role="tabpanel" aria-labelledby="qTrendTab" ${q.tab==='trend'?'':'hidden'}><div class="v013-trend-grid" data-q-charts></div><p data-q-chart-message></p></section>
 <section id="qTablePanel" role="tabpanel" aria-labelledby="qTableTab" ${q.tab==='table'?'':'hidden'}><div data-q-table></div></section></div></div>`;
 root.querySelector('[data-q-study]').onchange=e=>{
  const study=this.data.studies.find(s=>s.id===e.target.value);if(study)this.useStudy(study);else{this.selected=null;q.ids.clear();q.roles.clear();this.changed();}
 };
 root.querySelector('[data-q-save]').onclick=()=>this.saveQuickStudy();
 root.querySelector('[data-q-export-range]').onclick=()=>{const data=this.q.summary;if(!data)return;const chart=data.groups.length===1?this.q.chartData.get(data.groups[0].id):null;const axis=chart?.trend.axis;this.exportReport({kind:'quick',quick:this.quickPayload(),slot_ids:data.groups.flatMap(g=>g.slot_ids),...(axis?{axis_mode:axis.mode,axis_min:axis.min,axis_max:axis.max}:{})},root.querySelector('[data-q-range-download]'));};
 root.querySelector('[data-q-show-trend]').onclick=()=>{this.showQuickTab('trend');root.querySelector('[data-q-chart]')?.scrollIntoView({behavior:'smooth',block:'start'});};
 root.querySelector('[data-q-refresh]').onclick=()=>this.enter('comparability',this.project,{studyId:this.selected?.id});
 root.querySelector('[data-q-group]').onclick=()=>{q.grouping=!q.grouping;root.querySelector('[data-q-bulk]').hidden=!q.grouping;root.querySelector('[data-q-group]').setAttribute('aria-expanded',q.grouping);};
 root.querySelector('[data-q-clear]').onclick=()=>{q.ids.clear();q.roles.clear();this.changed();};
 const search=root.querySelector('[data-q-search]');search.oninput=()=>{q.search=search.value;this.filterBatchRows(root);};this.filterBatchRows(root);
 root.querySelectorAll('[data-q-batch]').forEach(b=>b.onclick=()=>{const ids=this.catalog.samples.filter(s=>s.batch===b.dataset.qBatch).map(s=>s.id),remove=ids.every(id=>q.ids.has(id));ids.forEach(id=>{if(remove){q.ids.delete(id);q.roles.delete(id);}else q.ids.add(id);});this.changed();});
 root.querySelectorAll('[data-q-sample]').forEach(n=>n.onchange=()=>{if(n.checked)q.ids.add(n.dataset.qSample);else{q.ids.delete(n.dataset.qSample);q.roles.delete(n.dataset.qSample);}this.changed();});
 root.querySelectorAll('[data-q-role]').forEach(b=>b.onclick=()=>{const batches=[...root.querySelectorAll('[data-q-group-batch]:checked')].map(n=>n.dataset.qGroupBatch);if(!batches.length){root.querySelector('[data-q-bulk-status]').textContent='请先勾选要分组的批次。';return;}this.catalog.samples.filter(s=>q.ids.has(s.id)&&batches.includes(s.batch)).forEach(s=>q.roles.set(s.id,b.dataset.qRole));q.dirty=true;this.studyPage(root);});
 root.querySelectorAll('[data-q-condition]').forEach(b=>b.onclick=()=>{const [study,condition]=JSON.parse(b.dataset.qCondition);q.filters={...q.filters,study,condition};this.changed();});
 root.querySelector('[data-q-item]').onchange=e=>{q.allItems=e.target.value==='*';if(q.allItems)delete q.filters.item;else q.filters.item=e.target.value;this.changed();};
 root.querySelectorAll('[data-q-tab]').forEach(b=>b.onclick=()=>this.showQuickTab(b.dataset.qTab));
 this.schedulePreview();
};
r.filterBatchRows=function(root){root.querySelectorAll('[data-q-batch-row]').forEach(n=>n.hidden=!n.dataset.qBatchRow.toLowerCase().includes(this.q.search.toLowerCase()));};
r.quickPayload=function(extra={}){const q=this.q;return {base_revision:this.data.base_revision,sample_ids:[...q.ids].sort(),members:[...q.ids].sort().map(id=>({sample_id:id,role:q.roles.get(id)||'selected'})),filters:{...q.filters},aligned_fields:[...q.aligned_fields],alignment_reason:q.alignment_reason,...extra};};
r.schedulePreview=function(){
 this.clear();const token=this.serial,project=this.project;
 this.q.summary=null;this.q.matrixData=null;this.q.chartData=new Map();this.q.matrixLoading=false;
 const root=document.getElementById('tab-comparability');root.querySelector('[data-q-save]').disabled=true;root.querySelector('[data-q-show-trend]').disabled=true;root.querySelector('[data-q-export-range]').disabled=true;
 const oldStatus=root.querySelector('[data-q-range-download]');oldStatus.replaceWith(oldStatus.cloneNode(false));
 root.querySelector('[data-q-charts]').replaceChildren();root.querySelector('[data-q-table]').replaceChildren();
 root.querySelector('[data-q-alignment]').replaceChildren();root.querySelector('[data-q-chart-message]').textContent='';
 root.querySelector('[data-q-feedback]').textContent=this.q.ids.size?'正在读取所选批次、条件与检项…':'请选择一个或多个批次，即可查看趋势。';
 if(!this.q.ids.size)return;
 this.qTimer=setTimeout(()=>this.previewQuick(root,token,project),120);
};
r.previewQuick=async function(root,token,project){
 const current=()=>token===this.serial&&root.isConnected&&project===currentProjectKey;
 const controller=new AbortController();this.qControllers.add(controller);
 try{
  const value=await post(`/projects/${encodeURIComponent(project)}/comparison-preview`,this.quickPayload(),controller.signal);
  if(!current())return;
  this.q.summary=value;this.results=value;this.mode=this.q.filters.study==='release'?'release':'stability';
  const batches=new Set(this.catalog.samples.filter(s=>this.q.ids.has(s.id)).map(s=>s.batch));
  const exact=value.groups.reduce((n,a)=>n+a.exact_numeric_count,0),c=value.roles_count;
  root.querySelector('[data-q-feedback]').textContent=`已选 ${batches.size} 批 · ${value.total_slots} 条结果 · ${exact} 个可绘制正式数值 · ${value.groups.length} 个独立检测组。`+(this.selected?`研究 ${this.selected.title}${this.q.dirty?'（当前调整尚未保存）':''}。`:'无需保存研究即可查看。')+((c.before&&!c.after)||(c.after&&!c.before)?'当前只有'+(c.before?'变更前':'变更后')+'样品，可以查看曲线，尚不能进行前后比较。':'');
  root.querySelector('[data-q-export-range]').disabled=!value.total_slots||!g.v4Api.can('export');
  root.querySelector('[data-q-save]').disabled=!value.total_slots||!g.v4Api.currentActor?.capabilities.some(c=>['preview','export','manage'].includes(c));
  const alignment=root.querySelector('[data-q-alignment]');
  alignment.innerHTML=value.alignment_candidates.length?`<div class="v015-alignment-note">所选数据的 ${value.alignment_candidates.map(c=>esc(fields[c.field])).join('、')} 不同，当前分表分图。<button class="btn btn-outline" data-q-align>查看差异并确认对齐</button></div>`:this.q.aligned_fields.length?`<p class="v3-note">已确认仅当前所选范围对齐：${this.q.aligned_fields.map(f=>esc(fields[f])).join('、')}。${esc(this.q.alignment_reason)}</p>`:'';
  if(this.q.filters.item&&value.groups.length>1&&!value.alignment_candidates.length)alignment.insertAdjacentHTML('beforeend','<p class="v3-note">当前检项仍有多组检测身份；材料、用途、方法、单位等差异继续分图。可展开图中的“检测身份与来源范围”查看。</p>');
  alignment.querySelector('[data-q-align]')?.addEventListener('click',()=>this.alignQuick());
  const numeric=value.groups.filter(a=>a.exact_numeric_count>0),other=value.groups.length-numeric.length;
  root.querySelector('[data-q-show-trend]').disabled=!numeric.length;
  root.querySelector('[data-q-charts]').innerHTML=numeric.map(a=>this.quickCard(a)).join('');
  root.querySelector('[data-q-chart-message]').textContent=!value.total_slots?'当前范围没有已提交结果，请调整批次或条件。':!exact?'已找到结果，但没有可绘制的精确正式采用值。请打开“完整结果”查看原文与采用状态。':other?`${other} 个检测组为定性、限值或暂无精确采用值，可在“完整结果”中查看。`:'';
  this.observer=new IntersectionObserver(entries=>{for(const e of entries)if(e.isIntersecting){this.observer.unobserve(e.target);if(!e.target._qStarted)this.enqueueQuick(e.target,token);}}, {rootMargin:'200px'});
  for(const node of root.querySelectorAll('[data-q-chart]')){
   axes.bind(node,()=>this.enqueueQuick(node,token));node.querySelector('[data-q-retry]').onclick=()=>this.enqueueQuick(node,token);
   node.querySelector('[data-q-export]').onclick=()=>this.exportQuickGroup(node);
   this.observer.observe(node);
  }
  // The first graph starts immediately; it is not hidden below a table or dependent on scrolling.
  if(this.q.tab==='trend'){const first=root.querySelector('[data-q-chart]');if(first){this.observer.unobserve(first);this.enqueueQuick(first,token);}}
  else this.loadQuickTable();
 }catch(e){if(current()&&e.name!=='AbortError'){root.querySelector('[data-q-feedback]').textContent='对比读取失败：'+e.message;root.querySelector('[data-q-chart-message]').innerHTML='<button class="btn btn-outline" data-q-preview-retry>重试当前选择</button>';root.querySelector('[data-q-preview-retry]').onclick=()=>this.schedulePreview();}}
 finally{this.qControllers.delete(controller);}
};
r.quickCard=function(a){return `<article class="card v013-trend-card" data-q-chart="${esc(a.id)}"><div class="card-header"><h4>${esc(a.label)} (${esc(a.unit)})</h4><span class="v013-trend-badge">正式采用值</span></div><div class="card-body"><p class="v013-identity">${esc(a.key.sample_type)} · ${esc(human(a.key.purpose))} · ${esc(names[a.key.study])} · ${esc(human(a.key.condition))}<br>${esc(a.key.method)} · ${esc(human(a.key.method_version))} · ${esc(a.key.reporting_basis||'共用结果')}</p><details><summary>检测身份与来源范围</summary><p>规格 ${esc(human(a.scope.presentation))} · 原研究 ${esc(human(a.scope.study_id))}</p><p>用途 ${esc(human(a.scope.purpose))} · 时间单位 ${esc(a.scope.time_unit)} · 方法 ${esc(a.scope.method)} · ${esc(human(a.scope.method_version))} · 单位 ${esc(a.scope.unit)} · 报告体系 ${esc(a.scope.reporting_basis||'共用结果')}</p></details><p data-chart-status role="status">准备读取正式采用值…</p><div class="v013-canvas"><canvas></canvas></div>${axes.markup()}<p data-chart-omitted></p><p data-chart-point></p><button class="btn btn-outline" data-q-retry>重新读取本图</button><button class="btn btn-outline" data-q-export disabled>导出本组报告</button><span data-group-download></span></div></article>`;};
r.enqueueQuick=function(node,token){
 if(token!==this.serial)return;
 let axis;try{axis=axes.options(node);}catch(e){node.querySelector('[data-chart-status]').textContent=e.message;return;}
 node._qStarted=true;
 const sequence=(node._qSequence||0)+1;node._qSequence=sequence;
 node.querySelector('[data-q-export]').disabled=true;node.querySelector('[data-group-download]').replaceChildren();
 this.qQueue.push({node,token,sequence,project:this.project,payload:this.quickPayload({group_id:node.dataset.qChart,include_cells:true,...axis})});this.drainQuick();
};
r.drainQuick=function(){
 while(this.qActive<3&&this.qQueue.length){
  const {node,token,sequence,project,payload}=this.qQueue.shift(),current=()=>token===this.serial&&node.isConnected&&sequence===node._qSequence&&project===currentProjectKey;
  if(!current())continue;this.qActive++;
  const controller=new AbortController();this.qControllers.add(controller);
  node._chart?.destroy();node._chart=null;node.querySelector('[data-chart-status]').textContent='正在读取正式采用值…';node.querySelector('[data-chart-point]').textContent='';
  post(`/projects/${encodeURIComponent(project)}/comparison-preview`,payload,controller.signal).then(data=>{
   if(!current())return;
   this.draw(node,data.trend,{cells:data.cells});this.q.chartData.set(node.dataset.qChart,data);
   node.querySelector('[data-q-export]').disabled=!g.v4Api.can('export');
  }).catch(e=>{if(current()&&e.name!=='AbortError'){node.querySelector('[data-chart-status]').textContent='本图读取失败：'+e.message+'；可单独重试。';node.querySelector('[data-chart-omitted]').textContent='';}}).finally(()=>{this.qControllers.delete(controller);this.qActive--;this.drainQuick();});
 }
};
r.showQuickTab=function(tab){
 this.q.tab=tab;const root=document.getElementById('tab-comparability');
 root.querySelector('#qTrendPanel').hidden=tab!=='trend';root.querySelector('#qTablePanel').hidden=tab!=='table';
 root.querySelectorAll('[data-q-tab]').forEach(b=>b.setAttribute('aria-selected',b.dataset.qTab===tab));
 if(tab==='table')this.loadQuickTable();else for(const c of this.charts)c.resize();
};
r.loadQuickTable=async function(){
 if(!this.q.summary||this.q.matrixLoading)return;
 const token=this.serial,project=this.project,box=document.querySelector('[data-q-table]');
 if(this.q.matrixData){this.renderQuickTable(box,this.q.matrixData);return;}
 this.q.matrixLoading=true;box.textContent='正在读取当前选择的完整结果…';const controller=new AbortController();this.qControllers.add(controller);
 try{const data=await post(`/projects/${encodeURIComponent(project)}/comparison-preview`,this.quickPayload({include_cells:true}),controller.signal);if(token!==this.serial||!box.isConnected)return;this.q.matrixData=data;this.renderQuickTable(box,data);}
 catch(e){if(token===this.serial&&box.isConnected&&e.name!=='AbortError'){box.innerHTML=`<p role="alert">完整结果读取失败：${esc(e.message)}</p><button class="btn btn-outline">重试完整结果</button>`;box.querySelector('button').onclick=()=>this.loadQuickTable();}}
 finally{this.qControllers.delete(controller);if(token===this.serial)this.q.matrixLoading=false;}
};
r.renderQuickTable=function(box,data){
 box.innerHTML=(this.mode==='release'?'<button class="btn btn-outline" data-q-orientation>切换表格方向</button>':'')+(data.cells.length?this.matrix(data):'<p>此范围暂无已提交结果，请调整选择。</p>');
 box.querySelector('[data-q-orientation]')?.addEventListener('click',()=>{this.orientation=this.orientation==='items'?'batches':'items';this.renderQuickTable(box,data);});
 box.querySelectorAll('[data-result-source]').forEach(b=>b.onclick=()=>g.v4SourceDrawer.openResultCell(data.cells.find(c=>c.slot_id===b.dataset.resultSource)).catch(e=>toast(e.message)));
};
r.alignQuick=function(){
 const q=this.q,candidates=q.summary.alignment_candidates,token=this.serial;
 const dialog=document.createElement('dialog');dialog.className='v014-study-dialog';
 const batches=[...new Set(this.catalog.samples.filter(s=>q.ids.has(s.id)).map(s=>s.batch))];
 dialog.innerHTML=`<form><h3>确认当前范围的对齐</h3><p>仅对当前批次、条件和检项生效：${batches.map(esc).join('、')}；${esc(names[q.filters.study])} · ${esc(human(q.filters.condition))}；${esc(q.filters.item?this.catalog.options.find(o=>o.item===q.filters.item)?.label:'全部检项')}。</p>${candidates.map(c=>`<fieldset><legend><label><input type="checkbox" data-q-align-field="${c.field}">确认在本次对比中对齐 ${esc(fields[c.field])}</label></legend><p>${c.values.map(v=>esc(human(v))).join('；')}</p><details><summary>查看原始身份记录</summary>${c.values.map(v=>`<p>${esc(v)}</p>`).join('')}</details></fieldset>`).join('')}<label>确认依据<textarea name="reason" required placeholder="填写这些来源为何可在当前范围内对齐"></textarea></label><p>方法、单位、材料类别、用途、条件和报告体系仍分别处理。正式身份及原始数据保持不变。</p><p data-q-align-status role="status"></p><button class="btn btn-primary" type="submit">确认并刷新对比图</button><button class="btn btn-outline" type="button" data-q-close>取消</button></form>`;
 document.body.append(dialog);dialog.showModal();dialog.onclose=()=>dialog.remove();dialog.querySelector('[data-q-close]').onclick=()=>dialog.close();
 dialog.querySelector('form').onsubmit=e=>{e.preventDefault();if(token!==this.serial){dialog.querySelector('[data-q-align-status]').textContent='选择范围已变化，请重新打开确认。';return;}const selected=[...dialog.querySelectorAll('[data-q-align-field]:checked')].map(n=>n.dataset.qAlignField);if(!selected.length){dialog.querySelector('[data-q-align-status]').textContent='请明确勾选要对齐的字段。';return;}q.aligned_fields=[...new Set([...q.aligned_fields,...selected])];q.alignment_reason=dialog.querySelector('[name=reason]').value.trim();q.dirty=true;dialog.close();this.schedulePreview();};
};
r.saveQuickStudy=function(){
 if(!this.q.summary?.total_slots)return;
 const token=this.serial,project=this.project,payload=this.quickPayload(),summary=this.q.summary;
 const batches=[...new Set(this.catalog.samples.filter(s=>this.q.ids.has(s.id)).map(s=>s.batch))];
 const roleBatches=Object.fromEntries(Object.keys(roles).map(role=>[role,[...new Set(this.catalog.samples.filter(s=>this.q.ids.has(s.id)&&(this.q.roles.get(s.id)||'selected')===role).map(s=>s.batch))]]));
 const dialog=document.createElement('dialog');dialog.className='v014-study-dialog';
 dialog.innerHTML=`<form><h3>保存当前对比为研究</h3><p>${batches.map(esc).join('、')} · ${esc(names[payload.filters.study])} · ${summary.total_slots} 条结果 · ${summary.groups.length} 个检测组。</p><p>${Object.entries(roles).map(([k,v])=>`${v}：${roleBatches[k].map(esc).join('、')||'无'}`).join('<br>')}。仅保存当前明确选择的样品和范围。</p><label>研究名称<input name="title" required value="${esc(this.selected?.title||'')}" placeholder="为这次对比填写名称"></label><label>研究／分组依据<textarea name="reason" required></textarea></label><p>对齐字段：${payload.aligned_fields.map(f=>esc(fields[f])).join('、')||'无'}。${esc(payload.alignment_reason)}</p><p>保存独立研究配置，原始文件、正式结果和身份保持不变。${this.selected?'原研究保留，本次保存为新研究。':''}</p><p data-q-save-status role="status"></p><button class="btn btn-primary" type="submit">确认保存研究</button><button class="btn btn-outline" type="button" data-q-close>取消</button></form>`;
 document.body.append(dialog);dialog.showModal();dialog.onclose=()=>dialog.remove();dialog.querySelector('[data-q-close]').onclick=()=>dialog.close();const id=crypto.randomUUID();
 dialog.querySelector('form').onsubmit=async e=>{
  e.preventDefault();const status=dialog.querySelector('[data-q-save-status]'),button=dialog.querySelector('[type=submit]');
  if(token!==this.serial||project!==currentProjectKey){status.textContent='选择范围已变化，请重新预览后保存。';return;}
  button.disabled=true;status.textContent='正在保存研究配置…';
  const form=e.target;
  try{const saved=await post(`/projects/${encodeURIComponent(project)}/comparison-studies/new`,{base_revision:payload.base_revision,idempotency_key:id,title:form.elements.title.value,reason:form.elements.reason.value,members:payload.members,filters:payload.filters,aligned_fields:payload.aligned_fields,alignment_reason:payload.alignment_reason});dialog.close();if(token===this.serial&&project===currentProjectKey)await this.enter('comparability',project,{studyId:saved.study.id});}
  catch(error){status.textContent='保存失败：'+error.message;button.disabled=false;}
 };
};
r.exportQuickGroup=function(node){
 const data=this.q.chartData.get(node.dataset.qChart);if(!data)return;
 const group=this.q.summary.groups.find(a=>a.id===node.dataset.qChart),axis=data.trend.axis;
 this.exportReport({kind:'quick',quick:this.quickPayload(),slot_ids:group.slot_ids,axis_mode:axis.mode,axis_min:axis.min,axis_max:axis.max},node.querySelector('[data-group-download]'));
};
r.fromLedger=function(item=null){
 const ledger=g.v45Ledger;
 this.ledgerContext={project:currentProjectKey,batch:ledger.batch,study:ledger.study,condition:ledger.condition,item,sample_type:ledger.sample_type,presentation:ledger.presentation,purpose:ledger.purpose};
 switchMainTab('comparability');
};
// Preserve the ledger's table-first order; add an explicit handoff for the current range/item.
const oldMount=g.v012Display.mount;
g.v012Display.mount=function(...args){oldMount.apply(this,args);if(g.v4Api.mode!=='real')return;const tools=document.getElementById('v013TableTools');if(tools&&!tools.querySelector('[data-q-open-comparison]')){const b=document.createElement('button');b.className='btn btn-outline';b.textContent='当前批次快速对比';b.dataset.qOpenComparison='';b.onclick=()=>r.fromLedger();tools.append(b);}for(const state of this.cards.values()){if(state.node.querySelector('[data-q-open-item]'))continue;const b=document.createElement('button');b.className='btn btn-outline';b.textContent='对比此检项';b.dataset.qOpenItem='';b.onclick=()=>r.fromLedger(state.group.key.item);state.node.querySelector('.card-body').append(b);}};
})(window);
