/* Existing ledger + read-only result snapshots. No source parsing or adoption writes. */
(function(g){
'use strict';
const esc=g.v5Review.esc, ledger=g.v45Ledger, renderer=g.v4ResultCell;
const request=p=>g.v4Api.sourceRequest(p);
const human=v=>/^原文未注明@/.test(v||'')?'原文未注明（来源独立保留）':v==='not_reported'?'版本未注明':v||'原文未注明';
const studyNames={release:'放行检测',long_term:'长期稳定性',accelerated:'加速稳定性',stress:'影响因素'};
const selectionFields=['section','batch','study','condition','sample_type','presentation','purpose','orientation'];
const display=g.v012Display={data:null,serial:0,chart:null,filters:{},
 visible(){return [...renderer.cellsBySlot.values()].filter(c=>ledger.match(c.slot_key));},
 unmount(){++this.serial;this.chart?.destroy();this.chart=null;this.trend=null;document.getElementById('v012ResultsPanel')?.remove();},
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
  table.parentElement.classList.add('v012-matrix-frame');
  const old=document.getElementById('v3LedgerCharts');if(old){old.hidden=true;if(old.previousElementSibling?.classList.contains('v3-label'))old.previousElementSibling.hidden=true;}
  const cells=this.visible(),ids=new Set(cells.map(c=>c.slot_id));
  const groups=(this.data?.analysis_groups||[]).filter(a=>a.slot_ids.some(id=>ids.has(id)));
  const signature=JSON.stringify([this.data?.project,this.data?.base_revision,...selectionFields.map(k=>ledger[k])]);
  let node=document.getElementById('v012ResultsPanel');if(node?.dataset.signature===signature)return;
  this.unmount();node=document.createElement('section');node.id='v012ResultsPanel';node.className='v012-results card';node.dataset.signature=signature;
  table.parentElement.after(node);
  node.innerHTML=`<h3>检测结果与趋势</h3><p>趋势图：正式采用值 · 数据版本 ${esc(renderer.currentBaseRevision)}。表格保留最新展示值与正式采用值；二者不同时，曲线沿用正式采用值。</p><p>未关联明确适用标准时不绘制限度线，未作符合性判定。</p><label>检测项目 / 方法 / 条件 <select id="v012Analysis">${groups.map(a=>`<option value="${esc(a.id)}">${esc(a.label)} · ${esc(a.key.method)} · ${esc(human(a.key.method_version))} · ${esc(studyNames[a.key.study])} · ${esc(a.key.condition)} · ${esc(a.key.reporting_basis||'共用报告结果')} · ${esc(a.unit||'单位未注明')} · ${esc(human(a.key.presentation))}</option>`).join('')}</select></label><div id="v012Batches"></div><p id="v012TrendStatus" role="status"></p><div class="v012-chart"><canvas id="v012Canvas" aria-label="正式采用值趋势图" role="img"></canvas></div><div id="v012Omitted"></div><p id="v012PointDetail" role="status"></p><div class="v012-output"><button type="button" class="btn btn-outline" id="v012MatrixExcel">导出当前矩阵 Excel</button><button type="button" class="btn btn-outline" id="v012TrendExcel">导出当前趋势范围 Excel / PNG</button><span id="v012ExportStatus" role="status"></span><span id="v012Downloads"></span></div>`;
  const advanced=document.getElementById('v5OutputControls');if(advanced)node.after(advanced);
  node.querySelector('#v012MatrixExcel').disabled=!cells.length||!g.v4Api.can('export');
  node.querySelector('#v012MatrixExcel').onclick=()=>this.exportSlots(cells.map(c=>c.slot_id),false);
  node.querySelector('#v012TrendExcel').disabled=true;
  node.querySelector('#v012TrendExcel').onclick=()=>this.exportSlots(this.trendSlots,true);
  if(!groups.length){node.querySelector('#v012TrendStatus').textContent=renderer.loadError?'结果读取失败：'+renderer.loadError:'当前范围没有已入库结果。';return;}
  const select=node.querySelector('#v012Analysis');
  select.value=groups.some(a=>a.id===this.analysis)?this.analysis:(groups.find(a=>a.numeric_count>0)||groups[0]).id;
  const choose=()=>{
   this.analysis=select.value;const group=groups.find(a=>a.id===select.value);
   const current=group.batches.includes(ledger.batch)?ledger.batch:group.batches[0];
   node.querySelector('#v012Batches').innerHTML=`<p>当前批次：${esc(current)}</p><details><summary>叠加其他兼容批次（仅影响图与趋势导出）</summary>${group.batches.map(b=>`<label><input type="checkbox" data-curve-batch value="${esc(b)}" ${b===current?'checked':''}>${esc(b)}</label>`).join('')}</details>`;
   node.querySelectorAll('[data-curve-batch]').forEach(input=>input.onchange=()=>this.loadTrend(group));
   this.loadTrend(group);
  };
  select.onchange=choose;choose();
 },
 async loadTrend(group){
  const token=++this.serial,node=document.getElementById('v012ResultsPanel');if(!node)return;
  this.chart?.destroy();this.chart=null;this.trend=null;this.trendSlots=[];
  node.querySelector('#v012Downloads').replaceChildren();node.querySelector('#v012ExportStatus').textContent='';
  const status=node.querySelector('#v012TrendStatus'),button=node.querySelector('#v012TrendExcel');button.disabled=true;
  node.querySelector('#v012Omitted').replaceChildren();node.querySelector('#v012PointDetail').replaceChildren();
  const batches=[...node.querySelectorAll('[data-curve-batch]:checked')].map(i=>i.value);
  if(!batches.length){status.textContent='请至少选择一个批次。';return;}
  status.textContent='正在读取正式采用值…';
  const params=new URLSearchParams(Object.entries(this.filters).filter(([,v])=>v!=null));
  params.set('group_id',group.id);params.set('base_revision',this.data.base_revision);batches.forEach(b=>params.append('batches',b));
  try{
   const trend=await request(`/projects/${encodeURIComponent(this.data.project)}/result-trend?${params}`);
   if(token!==this.serial||!node.isConnected||g.v5Review.scene!=='formal')return;
   if(trend.revision!==renderer.currentBaseRevision)throw Error('图表与表格数据版本不同，请刷新结果。');
   this.trend=trend;this.trendSlots=[...new Set(trend.series.flatMap(s=>s.values.map(p=>p.slot_id)).filter(Boolean))];
   button.disabled=!g.v4Api.can('export')||!this.trendSlots.length;
   const text=trend.omitted.length?` · ${trend.omitted.length} 项特殊值或未正式采用结果未作为精确数值绘制`:'';
   status.textContent=trend.points.length?`正式采用值 ${trend.points.length} 点${trend.points.length===1?' · 仅1个有效时间点':''}${text}。点击图点查看采用结果来源。`:`当前检项没有可绘制的正式精确数值；文字、限值及不汇报结果请查看矩阵。${text}`;
   node.querySelector('#v012Omitted').innerHTML=trend.omitted.length?`<details><summary>未绘制结果及原因（${trend.omitted.length}）</summary>${trend.omitted.map(p=>`<p>${esc(p.batch)} · ${esc(p.time)} · ${esc(p.raw)} · ${esc(p.reason==='no adopted result'?'尚无正式采用值':p.reason==='pending'?'待确认':'非精确数值，保留原文')}</p>`).join('')}</details>`:'';
   if(typeof g.Chart==='undefined')throw Error('本地图表库未能加载，结果表仍可查看。');
   const colors=['#0284c7','#7c3aed','#16a34a','#d97706','#db2777'];
   const datasets=trend.series.map((s,i)=>({label:s.batch,borderColor:colors[i%colors.length],backgroundColor:colors[i%colors.length],tension:0,spanGaps:false,pointRadius:4,data:s.values.map(p=>({...p,y:p.value===null?null:Number(p.value)}))}));
   this.chart=new g.Chart(node.querySelector('canvas'),{type:'line',data:{datasets},options:{responsive:true,maintainAspectRatio:false,parsing:{xAxisKey:'x',yAxisKey:'y'},plugins:{legend:{display:true},tooltip:{callbacks:{label:ctx=>`${ctx.dataset.label} · ${ctx.raw.time||'放行'} · 正式采用 ${ctx.raw.value} ${trend.scope[8]}`}}},scales:{x:{type:'linear',title:{display:true,text:trend.scope[9]==='D'?'考察时间（天）':trend.scope[9]==='M'?'考察时间（月）':'批次'},ticks:trend.scope[9]==='release'?{callback:x=>trend.batches[x]||''}:{precision:0}},y:{title:{display:true,text:trend.scope[8]||'原文未注明单位'}}},onClick:(_e,elements)=>{if(elements.length){const hit=elements[0],point=datasets[hit.datasetIndex].data[hit.index];if(point.slot_id)this.inspectPoint(point);}}}});
  }catch(error){if(token!==this.serial||!node.isConnected)return;status.textContent='趋势读取失败：'+error.message+'（请重新读取结果；无需修改数据库或重新入库）';button.disabled=true;}
 },
 inspectPoint(point){
  const cell=renderer.cellsBySlot.get(point.slot_id);if(!cell)return;
  // Use the adopted version for the plot's evidence, not the latest display version.
  const adoptedCell={...cell,display:cell.adopted,display_status:'confirmed'};
  const n=document.getElementById('v012PointDetail');if(n)n.textContent=`${point.batch} · ${point.time} · 正式采用 ${point.value} ${point.unit} · 版本 ${point.version_id}`;
  g.v4SourceDrawer.openResultCell(adoptedCell).catch(e=>{if(n)n.textContent='来源读取失败：'+e.message;});
 },
 async exportSlots(ids,png){
  const node=document.getElementById('v012ResultsPanel');if(!node||!ids?.length)return;
  const project=this.data.project,revision=this.data.base_revision,selection=this.serial,status=node.querySelector('#v012ExportStatus');
  const buttons=[...node.querySelectorAll('.v012-output button')];buttons.forEach(b=>b.disabled=true);
  node.querySelector('#v012Downloads').replaceChildren();status.textContent='正在生成并反读核验…';
  try{
   const result=await g.v4Api.requestExport({project,base_revision:revision,filters:{},slot_ids:ids,include_png:png,orientation:ledger.orientation||'items'});
   if(!node.isConnected||this.data.project!==project||this.data.base_revision!==revision||this.serial!==selection)return;
   status.textContent=`已核验 · ${ids.length} 个结果槽位 · 数据版本 ${revision} · Excel双状态${png?' / PNG正式采用值':''}`;
   const box=node.querySelector('#v012Downloads');
   for(const [url,label,name] of [[result.download_url,'下载 Excel','results.xlsx'],[result.image_download_url,'下载 PNG','trend.png']])if(url){const a=document.createElement('button');a.type='button';a.className='btn btn-outline';a.textContent=label;a.onclick=()=>g.v5Outputs.download(url,name).catch(e=>status.textContent='下载失败：'+e.message);box.append(a);}
  }catch(error){if(node.isConnected&&this.serial===selection)status.textContent='导出失败：'+error.message+'；未改动任何结果。';}
  finally{if(node.isConnected){node.querySelector('#v012MatrixExcel').disabled=!this.visible().length||!g.v4Api.can('export');node.querySelector('#v012TrendExcel').disabled=!this.trend||!this.trendSlots.length||!g.v4Api.can('export');}}
 }
};
const originalLoad=renderer.loadProjectCells;
renderer.loadProjectCells=function(project,filters={}){return g.v4Api.mode==='real'?display.load(project,filters):originalLoad.call(this,project,filters);};
const originalMatch=ledger.match;
ledger.match=function(key){return originalMatch.call(this,key)&&['sample_type','presentation','purpose'].every(k=>!this[k]||key[k]===this[k]);};
const originalControls=ledger.controls;
ledger.controls=function(cells,project){
 display.restore(project,cells);
 const available=cells.filter(c=>this.category(c.slot_key.study)===this.section).map(c=>c.slot_key.batch);
 if(!available.includes(this.batch))this.batch=available[0]||'';
 const scoped=cells.filter(c=>this.category(c.slot_key.study)===this.section&&c.slot_key.batch===this.batch);
 for(const key of ['study','condition','sample_type','presentation','purpose'])if(this[key]&&!scoped.some(c=>c.slot_key[key]===this[key]))this[key]='';
 originalControls.call(this,cells,project);
 document.querySelectorAll('#v45Condition option').forEach(o=>{if(o.value)o.textContent=human(o.value);});
 const host=document.getElementById('ledgerConditionPills');
 if(host){
  let extras=document.getElementById('v012ExtraFilters');if(!extras){extras=document.createElement('span');extras.id='v012ExtraFilters';host.append(extras);}
  extras.innerHTML=['sample_type','presentation','purpose'].map(k=>`<label>${esc({sample_type:'材料类别',presentation:'规格',purpose:'用途'}[k])}<select data-result-filter="${k}"><option value="">全部</option>${[...new Set(scoped.map(c=>c.slot_key[k]))].map(v=>`<option value="${esc(v)}" ${this[k]===v?'selected':''}>${esc(k==='purpose'?({reference:'参考品',routine:'常规样品',unknown:'用途未注明'}[v]):human(v))}</option>`).join('')}</select></label>`).join('');
  if(this.section==='release')extras.innerHTML+=`<label>表格方向<select data-result-filter="orientation"><option value="items" ${this.orientation!=='batches'?'selected':''}>按检测项目查看</option><option value="batches" ${this.orientation==='batches'?'selected':''}>按批次查看</option></select></label>`;
  extras.querySelectorAll('select').forEach(s=>s.onchange=()=>this.change(s.dataset.resultFilter,s.value));
 }
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
  const label=row=>[ledger.labels[row[0].slot_key.item]||row[0].slot_key.item,row[0].slot_key.reporting_basis,row[0].unit].filter(Boolean).map(esc).join(' · ');
  html+=`<tbody><tr><th colspan="${transpose?rowList.length+1:times.length+2}">${caption}</th></tr>`;
  if(transpose){html+=`<tr><th>批号</th>${rowList.map(row=>`<th>${label(row)}<br>${esc(row[0].slot_key.method)} · ${esc(human(row[0].slot_key.method_version))}</th>`).join('')}</tr>`;for(const t of times)html+=`<tr><th>${esc(t)}</th>${rowList.map(row=>`<td>${render(row.find(c=>c.slot_key.batch===t))}</td>`).join('')}</tr>`;}
  else{html+=`<tr><th>检测项目 / 子结果</th><th>方法 / 版本</th>${times.map(t=>`<th>${release?esc(t):esc(t)+(timeUnit==='D'?'天':'个月')}</th>`).join('')}</tr>`;for(const row of rowList)html+=`<tr><td>${label(row)}</td><td>${esc(row[0].slot_key.method)}<br>${esc(human(row[0].slot_key.method_version))}</td>${times.map(t=>{const c=row.find(c=>release?c.slot_key.batch===t:(c.slot_key.day??c.slot_key.month)===t);return `<td ${c?`data-slot-id="${esc(c.slot_id)}"`:''}>${render(c)}</td>`;}).join('')}</tr>`;}
  html+='</tbody>';
 }
 return html;
};
const originalMount=g.v5Outputs.mount;
g.v5Outputs.mount=function(){originalMount.call(this);display.mount();};
const originalScene=g.v5Review.setScene;
g.v5Review.setScene=function(scene){if(scene!=='formal'){display.unmount();document.getElementById('ledgerDataTable')?.parentElement.classList.remove('v012-matrix-frame');}return originalScene.call(this,scene);};
const originalLegacyCharts=g.renderLedgerCharts;
g.renderLedgerCharts=function(...args){if(g.v4Api.mode==='real')return;return originalLegacyCharts?.apply(this,args);};
})(window);
