/* V5 worksheet commands. All interpretation, selection and commit rules stay in Python. */
(function(g){
'use strict';
const esc=v=>String(v??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const names={reporting_basis:'报告标准体系',source_decision:'本次采用范围',criterion:'标准',subresult:'子结果',planned_date:'计划日期',test_item:'检测项 / 子结果',result:'解释结果',unit:'单位',method:'方法',method_version:'方法版本',time_point:'时间点',batch:'批号',presentation:'规格',study_id:'研究编号',study:'研究类型',condition:'条件',purpose:'用途',sample_type:'材料类别'};
const actions={initial:'首次结果',repeat_source:'相同值的重复来源',keep_old:'保留旧值',correction:'更正旧值',retest:'独立复测',exclude:'排除',blank:'空白不更新'};
const colName=n=>{let s='';while(n>0){n--;s=String.fromCharCode(65+n%26)+s;n=Math.floor(n/26);}return s;};
const api=(p,b,method='POST')=>g.v4Api.sourceRequest(p,b===undefined?{}:{method,headers:{'Content-Type':'application/json'},body:JSON.stringify(b)});
const fieldDescriptions={
 test_item:'检测项目名称（如 纯度、含量、pH、SEC-HPLC）',
 batch:'样品批号（如 20230101、DS-01）',
 timepoint:'考察时间点（如 0M、1M、3M、6M）',
 time_point:'考察时间点（如 0M、1M、3M、6M）',
 condition:'考察条件（如 25℃/60%RH、40℃/75%RH、5℃）',
 result:'实测数据结果值（如 98.5、<0.1、符合规定）',
 unit:'结果数值单位（如 %、mg/mL，无单位可留空）',
 method:'检测分析方法（如 SEC-HPLC、CE-SDS、微量滴定法）',
 method_version:'检测方法版本号（原表未注明可留空或填 not_reported）',
 specification:'质量标准 / 合格限度（如 ≥95.0%、6.5~7.5）',
 criterion:'质量标准 / 合格限度（如 ≥95.0%、6.5~7.5）',
 presentation:'规格 / 制剂剂型 / 浓度（如 100mg/支、20mg/mL）',
 substructure:'子结构 / 分项（如 主峰、前峰、后峰、高分子杂质）',
 replicate:'平行样序号（平行测定时如 1、2，无平行留空）',
 purpose:'参考品用途不代替材料类别或研究类型；原文未注明时保留未知',
 sample_type:'材料类别（DS 原液、DP 制剂、安慰剂；不能从批号猜测）',
 study:'研究类型（长期稳定性、加速稳定性、影响因素试验等）'
};

const ui=g.v5Review={esc,names,actions,api,colName,dirty:false,pending:new Map(),serial:0,
 issueName(i){if(i.proposal?.guidance)return i.proposal.guidance.title;if(i.code==='identity_conflict')return '明确中英文分项含义';if(i.code==='method_conflict')return '明确中英文方法含义';if(i.source_kind==='system_hint')return '系统解释提示';return ({identity_ambiguity:'规格或方法需明确',source_comment:'原文说明需核对',source_revision:'原文删改需解释',formula_result:'公式结果需核对',numeric_format:'数值格式需明确',structure:'归属或结构需明确'})[i.code]||'来源问题';},
 root(){return `/previews/${this.upload}/v5`;},
 command(value,reason='人工核对来源'){return {base_revision:this.preview.base_revision,idempotency_key:'v5-'+crypto.randomUUID(),...value,reason:value.reason?.trim()||reason?.trim()||'核对原表后修正来源解释'};},
  fields(keys,values={},disabled=false,forExport=false){return keys.map(k=>{
  let choices=k==='purpose'?{unknown:'原文未注明',reference:'参考品',routine:'常规样品（有明确依据）'}:k==='study'?{release:'放行',long_term:'长期稳定性',accelerated:'加速稳定性',stress:'影响因素'}:k==='sample_type'?{DP:'DP · 制剂',DS:'DS · 原液',placebo:'安慰剂'}:null;
  if(k==='item')choices=Object.fromEntries([...new Set([...g.v4ResultCell.cellsBySlot.values()].map(c=>c.slot_key.item))].map(id=>[id,g.v45Ledger.labels[id]||id]));
  if(forExport&&k==='method_version')choices=Object.fromEntries([...new Set([...g.v4ResultCell.cellsBySlot.values()].map(c=>c.slot_key.method_version))].map(v=>[v,v==='not_reported'?'原文未注明':v]));
  if(choices&&values[k]&&!choices[values[k]])choices={...choices,[values[k]]:values[k]};
  const input=choices?`<select name="${k}" ${disabled?'disabled':''}><option value="">请选择 / 保留未填写</option>${Object.entries(choices).map(([v,l])=>`<option value="${esc(v)}" ${values[k]===v?'selected':''}>${esc(l)}</option>`).join('')}</select>`:String(values[k]??'').includes('\n')?`<textarea name="${k}" rows="3" ${disabled?'disabled':''}>${esc(values[k])}</textarea>`:`<input ${['month','day'].includes(k)?'type="number" min="0" step="1"':''} name="${k}" value="${esc(values[k]??'')}" ${disabled?'disabled':''}>`;
  const desc=fieldDescriptions[k]?`<small class="v5-field-hint">${esc(fieldDescriptions[k])}</small>`:'';return `<label><span>${esc(names[k]||({item:'检测项',month:'月',day:'天'})[k]||k)}</span>${input}${desc}</label>`;
 }).join('');},
 form(title,content,defaultReason='确认原表内容并核对保留'){this.previewSerial=(this.previewSerial||0)+1;const tray=document.getElementById('v55ActionTray');if(tray){tray.style.display='none';tray.replaceChildren();}const node=document.getElementById('v5Form');node.innerHTML=`<form id="v5ActiveForm" class="v5-panel"><div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:8px;border-bottom:1px solid #dbe6f2;padding-bottom:5px;"><h4 style="margin:0;font-size:15px;color:#0f3861;">${esc(title)}</h4><button type="button" class="btn btn-outline" data-v5="cancel" style="padding:1px 8px;font-size:12px;line-height:1.4;">✕ 关闭</button></div>${content}<p class="v55-local-status" role="status" aria-live="polite"></p><div class="v5-fields" style="margin-top:10px;"><label><span>处置依据 / 备注说明</span><input name="reason" value="${esc(defaultReason)}" placeholder="可直接使用默认依据或修改"><small class="v5-field-hint">依据已预填；请先完成本次所需选择并核对预览。</small></label></div><div style="margin-top:12px;display:flex;gap:8px;"><button class="btn btn-primary" style="flex:1;">保存草稿决定</button><button type="button" class="btn btn-outline" data-v5="cancel">取消编辑</button></div></form>`;node.oninput=()=>{this.dirty=true;};this.bind(node);return document.getElementById('v5ActiveForm');},
 values(form){return Object.fromEntries(new FormData(form));},
 message(text){const n=document.getElementById('v4LedgerReviewMessage');if(n){n.textContent=text;if(this.scene!=='source')n.scrollIntoView({block:'nearest'});}},
 leave(){if(!this.dirty&&!this.busy){document.getElementById('v5ProjectGuard')?.remove();return true;}const text=this.busy?'操作仍在处理中，请等待结果后再切换。':'有未保存修改。请先保存，或点击“取消编辑”后再切换。';this.message(text);const dialog=document.getElementById('projectCenter');if(dialog?.open){let n=document.getElementById('v5ProjectGuard');if(!n){n=document.createElement('p');n.id='v5ProjectGuard';n.setAttribute('role','alert');dialog.prepend(n);}n.textContent=text+' 请关闭项目中心返回处理。';}return false;},
 cancel(){if(this.busy)return;this.previewSerial=(this.previewSerial||0)+1;this.dirty=false;document.getElementById('v5Form')?.replaceChildren();},
 localMessage(text){this.message(text);document.querySelectorAll('#v5Form .v55-local-status,#v55ActionTray .v55-local-status').forEach(n=>n.textContent=text);},
 async mutate(path,body,success,method='POST',reload=true){
  if(this.busy)return;this.rememberSheet?.();this.busy=true;this.localMessage('正在保存决定…');const buttons=[...document.querySelectorAll('#v5ActiveForm button,#v55ActionTray button')];const disabled=buttons.map(b=>b.disabled);let savedThisAttempt=false;buttons.forEach(b=>b.disabled=true);g.v46Busy?.show('正在保存决定并重新预检…');
  const signature=JSON.stringify([path,body,method]);let command=this.pending.get(signature);
  if(!command){command=this.command(body,body.reason);this.pending.set(signature,command);}
  try{
  let result;
  try{result=await api(path,command,method);}
  catch(e){if(e.status>=400&&e.status<500)this.pending.delete(signature);this.localMessage('保存未确认完成：'+e.message+'。输入和预览已保留，可核实状态后重试。');throw e;}
  if(result){
   savedThisAttempt=true;this.pending.delete(signature);this.dirty=false;this.lastSaved={path,result};this.localMessage('已保存，正在更新显示…');g.v46Busy?.show('已保存，正在更新显示…');
   try{if(reload){if(this.returnView?.key===this.viewKey?.()&&this.returnView.value)sessionStorage.setItem(this.returnView.key,this.returnView.value);await this.open(this.upload,true);this.returnView=null;}if(path.includes('identity-associations/apply')||path.endsWith('/display-name')||path.endsWith('/commit'))await this.refreshResults();result.display_refreshed=true;this.message(success);}
   catch(e){result.display_refreshed=false;this.showReloadFailure(result,e);}
   return result;
  }
  }finally{this.busy=false;buttons.forEach((b,i)=>{if(b.isConnected)b.disabled=savedThisAttempt&&this.lastSaved?.result?.display_refreshed===false?true:disabled[i];});g.v46Busy?.hide();}
 },
 showReloadFailure(result,error){
  const n=document.getElementById('v4LedgerReviewMessage');if(!n)return;n.replaceChildren();
  const text=document.createElement('span');text.textContent=`已保存（修订 ${result.base_revision}），显示刷新失败：${error.message}。无需重复保存。`;n.append(text);
  const retry=document.createElement('button');retry.type='button';retry.className='btn btn-outline';retry.textContent='重新读取已保存内容';retry.onclick=()=>this.open(this.upload,true).then(()=>this.message('已重新读取保存结果。')).catch(e=>this.showReloadFailure(result,e));n.append(retry);if(this.scene!=='source')n.scrollIntoView({block:'nearest'});
 },
 async open(id,force=false,retries=1){
  if(!force&&!this.leave())return;
  this.setScene('source');
  const serial=++this.serial,project=g.v4LedgerReview.project;this.upload=id;g.v4LedgerReview.upload=id;g.v4LedgerReview.summary=false;
  const cached=this.preview?.preview_id===id?this.preview.base_revision:null;
  let matrix=await api(`/previews/${id}/ledger-matrix`+(cached==null?'':'?after_revision='+cached));
  if(serial!==this.serial||project!==g.v4LedgerReview.project)return;
  if(matrix.not_modified){if(this.preview?.preview_id!==id||this.preview.base_revision!==matrix.base_revision){if(retries>0){this.preview=null;return this.open(id,true,retries-1);}throw Error('刷新基线失效。');}if(!document.getElementById('v52Workspace'))this.render();return;}
  if(matrix.partial_base_revision!=null){
   if(this.preview?.preview_id!==id||this.preview.base_revision!==matrix.partial_base_revision){if(retries>0)return this.open(id,true,retries-1);throw Error('增量基线已变化，请刷新来源。');}
   const removed=new Set(matrix.removed_candidate_ids),updates=new Map(matrix.candidate_updates.map(c=>[c.id,c])),known=new Set(this.preview.candidates.map(c=>c.id));
   const candidates=this.preview.candidates.filter(c=>!removed.has(c.id)).map(c=>updates.get(c.id)||c).concat(matrix.candidate_updates.filter(c=>!known.has(c.id)));
   matrix={...this.matrix,...matrix,preview:{...this.preview,...matrix.preview,candidates},worksheet_review:{...this.matrix.worksheet_review,...matrix.worksheet_review},sheets:this.matrix.sheets};
  }
  if(matrix.preview.project!==project)throw Error('来源与当前项目不符，请刷新来源选择。');
  if(matrix.preview.contract_revision!=='5')return this.legacyOpen.call(g.v4LedgerReview,id);
  const {sheets,preflights:flights,decisions}=matrix.worksheet_review;
  const state={base_revision:matrix.preview.base_revision,decisions};
  const config={base_revision:matrix.preview.base_revision,config:matrix.worksheet_review.configuration};
  if(serial!==this.serial||project!==g.v4LedgerReview.project)return;
  const revision=matrix.preview.base_revision;
  if(state.base_revision!==revision||config.base_revision!==revision||flights.some(f=>f.base_revision!==revision)){
   if(retries>0)return this.open(id,true,retries-1);
   throw Error('读取期间数据已变化，请刷新后重新核对。');
  }
  if(this.preview?.preview_id!==id){this.selected=new Set();this.highlighted=null;}this.selected ||= new Set();this.selected=new Set([...this.selected].filter(id=>matrix.preview.candidates.some(c=>c.id===id&&c.lifecycle==='draft')));
  this.matrix=matrix;this.preview=matrix.preview;this.sheets=sheets;this.saved=state.decisions;this.config=config.config;
  this.flights=Object.fromEntries(flights.map(f=>[f.section,f]));this.sheet=sheets.some(s=>s.section===this.sheet)?this.sheet:sheets[0]?.section;
  g.v4LedgerReview.matrix=matrix;g.v4LedgerReview.upload=id;g.v4LedgerReview.summary=false;
  const url=new URL(g.location.href);url.searchParams.set('upload',id);url.searchParams.set('sheet',this.sheet||'');g.history.replaceState(null,'',url);
  this.render();
 },
 bind(root){root.querySelectorAll('[data-v5]').forEach(b=>b.onclick=()=>{const fn=this[b.dataset.v5];if(typeof fn!=='function')return;Promise.resolve().then(()=>fn.call(this,b.dataset.id,b.dataset.issue)).catch(e=>this.message(e.message));});},
 selection(){const count=this.selectedHere?.().length||0;const node=document.getElementById('v5Selection');if(node)node.textContent=`本表已选 ${count} 格；切表后保留各表选择，常用动作只处理当前表。`;for(const name of ['suggestSelected','excludeSelected','modifySelected']){const button=document.querySelector(`[data-v5="${name}"]`);if(button)button.disabled=!count;}},
 member(id){return this.preview.candidates.find(c=>c.id===id);},
 label(id){return this.config?.items?.find(i=>i.id===id)?.label||this.matrix?.worksheet_review?.source_items?.find(i=>i.id===id)?.label||g.v45Ledger?.labels?.[id]||id;},
 renderRaw(offset=0){
  const raw=this.matrix.sheets.find(s=>s.name===this.sheet),node=document.getElementById('v5Raw');if(!raw||!node)return;
  const rows=[...new Set(raw.cells.map(c=>c.row))].sort((a,b)=>a-b),cols=[...new Set(raw.cells.map(c=>c.column))].sort((a,b)=>a-b),cells=new Map(raw.cells.map(c=>[c.row+':'+c.column,c]));this.rawOffset=offset;
  node.innerHTML=`<p>非空物理行 ${Math.min(offset+1,rows.length)}–${Math.min(offset+120,rows.length)} / ${rows.length}；黄色为当前指定区域。<button type="button" class="btn btn-outline" data-v5="rawPrev" ${!offset?'disabled':''}>前120行</button> <button type="button" class="btn btn-outline" data-v5="rawNext" ${offset+120>=rows.length?'disabled':''}>后120行</button></p><table><thead><tr><th>原行</th>${cols.map(c=>`<th>${this.colName(c)}</th>`).join('')}</tr></thead><tbody>${rows.slice(offset,offset+120).map(r=>`<tr><th>${r}</th>${cols.map(c=>`<td data-raw-row="${r}" data-raw-col="${c}">${esc(cells.get(r+':'+c)?.text||'')}</td>`).join('')}</tr>`).join('')}</tbody></table>`;
  this.bind(node);if(this.highlighted?.section===this.sheet)this.highlight(this.highlighted.range,this.sheet);
 },
 rawPrev(){this.renderRaw(Math.max(0,(this.rawOffset||0)-120));},
 rawNext(){this.renderRaw((this.rawOffset||0)+120);},
 highlight(range,section){
  this.highlighted={range,section};if(section!==this.sheet)return;
  const m=/^([A-Z]+)(\d+):([A-Z]+)(\d+)$/i.exec(range);if(!m)return;const column=s=>[...s.toUpperCase()].reduce((n,c)=>n*26+c.charCodeAt(0)-64,0);
  document.querySelectorAll('#v5Raw [data-raw-row]').forEach(n=>n.classList.toggle('v5-raw-highlight',Number(n.dataset.rawRow)>=Number(m[2])&&Number(n.dataset.rawRow)<=Number(m[4])&&Number(n.dataset.rawCol)>=column(m[1])&&Number(n.dataset.rawCol)<=column(m[3])));
  document.getElementById('v5Raw')?.closest('details')?.setAttribute('open','');
 },
 async refreshResults(){const review=g.v4LedgerReview;review.refreshing=true;try{await g.v4ResultCell.loadProjectCells(this.preview.project);}finally{review.refreshing=false;}},
 members(){return this.preview.candidates.filter(c=>this.selected.has(c.id)&&c.lifecycle==='draft');},

 async refresh(){if(this.leave())await this.open(this.upload,true);},
 async original(){await g.v5Outputs.download(`/api/v1/previews/${this.upload}/file`,this.preview.filename);},

 reviewSummary(){return this.commit(true);},
 async commit(inspectOnly=false){
  if(!this.leave())return;const f=this.flights[this.sheet];
  const tasks=(this.matrix.worksheet_review.tasks||[]).filter(t=>t.blockers.some(b=>b.startsWith(this.sheet+'|')));
  const form=this.form('确认本表 · 核对提交范围',`<p><strong>${esc(this.sheet)}</strong> · ${Object.entries(f.counts).map(([k,v])=>`${esc(actions[k]||k)} ${v}`).join(' · ')}</p><p>未解决 ${f.unresolved.length} 项；待复核 ${f.review_required.length} 项。各表独立，本次只提交当前表。</p>${tasks.length?`<div class="v52-source-list">${tasks.map(t=>`<p><button type="button" data-v5="task" data-id="${esc(t.id)}">${esc(this.taskTitle(t))} · ${esc(t.evidence.map(e=>this.colName(e.column)+e.row).join('、'))}</button></p>`).join('')}</div>`:'<p>本表预检通过，正常值无需逐格确认。</p>'}`,'核对原表、修改及本表全部范围后确认入库');
  form.querySelector('.btn-primary').textContent='确认整表入库';form.querySelector('.btn-primary').disabled=!f.can_commit;
  form.onsubmit=async e=>{e.preventDefault();const r=await this.mutate(this.root()+'/commit',{section:this.sheet,fingerprint:f.fingerprint,reason:form.elements.reason.value},'工作表已提交');if(r){if(r.display_refreshed!==false)this.message(`本表已正式提交 · ${Object.entries(r.counts).map(([k,v])=>`${actions[k]||k} ${v}`).join(' · ')} · 历史已提交 ${r.historical_count}`);g.v4ResultCell.currentBaseRevision=r.base_revision;}};
 },
 archiveAuxiliary(){if(!this.leave())return;const section=this.sheet,f=this.flights[section];const form=this.form('审定整表为无结果辅助来源',`<p>${esc(section)} · ${this.sheets.find(s=>s.section===section).regions.filter(r=>r.role==='unclassified').length} 个未分类区域。</p><p>请先核对完整原表，确认本表没有需要入库的检测结果。此操作保留冻结原件和全部区域，不生成检测事实；如有遗漏结果，请取消并补录或重解析。</p><label><input type="checkbox" name="checked" required>我已核对整表，确认仅为辅助来源</label>`);form.querySelector('.btn-primary').textContent='确认辅助来源用途';form.querySelector('[name="reason"]').required=true;form.onsubmit=async e=>{e.preventDefault();const d=this.values(form);if(!d.reason.trim())return this.message('请填写本表用途和无结果判断依据。');try{await this.mutate(this.root()+'/auxiliary-source',{section,fingerprint:f.fingerprint,reason:d.reason},'本表已完成辅助来源审定；冻结原件保留，未生成检测事实。');}catch{}};},
 edit(id,keys){
  if(!this.leave())return;
  const c=this.member(id),baseline={...c.applied_defaults,...c.inherited_fields,...Object.fromEntries(Object.entries(c.interpreted_fields||{}).filter(([,v])=>String(v??'').trim())),...c.corrections};
  const inherited=Object.entries(c.inherited_fields||{}).filter(([k])=>['batch','presentation','study_id','condition','sample_type'].includes(k));
  const context=inherited.length?`<p class="v5-edit-context">沿用本文件公共信息：${inherited.map(([k,v])=>`${esc(names[k]||k)} ${esc(v)}`).join(' · ')}。仅保存本次实际修改的字段。</p>`:'';
  const content=Array.isArray(keys)?`<div class="v5-fields">${this.fields(keys,baseline)}</div>`:
   `<div class="v5-fields" data-edit-basic>${this.fields(['result','unit'],baseline)}</div><details data-edit-advanced><summary>检项、时间点与其他信息（按需修改）</summary><div class="v5-fields">${this.fields(Object.keys(names).filter(k=>!['result','unit'].includes(k)),baseline)}</div></details>`;
  const form=this.form('修改结果数据',`<p>填写本次结果和修改说明，保存为草稿。</p>${context}${content}`,'核对原表并修正字段');
  const initial=this.values(form);
  form.onsubmit=async e=>{e.preventDefault();const data=this.values(form),fields=Object.fromEntries(Object.entries(data).filter(([k,v])=>k!=='reason'&&v!==String(initial[k]??'')));if(!Object.keys(fields).length)return this.message('没有修改任何字段内容。');try{await this.mutate(`/candidates/${id}`,{candidate_revision:c.candidate_revision,fields,reason:data.reason||'核对原表并修正字段',acknowledge_source_warning:false},'修改已保存；未提交前可继续修改。','PATCH');}catch{}};
 },
 issue(id,iid){if(!this.leave())return;const c=this.member(id),issue=c.issues.find(i=>i.id===iid);if(issue.actions.includes('structure'))return this.structure(id);const form=this.form('处理一个来源问题',`<p>${esc(this.issueName(issue))} · ${esc(issue.detail)}</p><p>来源：${esc(issue.evidence.map(e=>`${e.section}!R${e.row}C${e.column}`).join('、'))}</p><label>明确处置<select name="action" required><option value="">请选择</option>${issue.actions.filter(a=>['keep_body','manual','exclude'].includes(a)).map(a=>`<option value="${a}">${({keep_body:'保留正文',manual:'手工明确解释',exclude:'排除此结果'})[a]}</option>`).join('')}</select></label><div class="v5-fields">${this.fields(issue.fields.length?issue.fields:['result','unit'])}</div><p>手工输入仅解除本问题，其他问题仍待核对；保留正文/排除不携带字段修改。</p>`,'');form.elements.action.onchange=()=>{form.elements.reason.required=form.elements.action.value==='exclude';};form.onsubmit=async e=>{e.preventDefault();const data=this.values(form);const fields=data.action==='manual'?Object.fromEntries(Object.entries(data).filter(([k,v])=>!['action','reason'].includes(k)&&v.trim())):{};if(data.action==='exclude'&&!data.reason.trim())return this.message('请填写不采用本来源的业务依据。');const defaultReason=data.action==='exclude'?data.reason:issue.code==='source_revision'?'确认原表删改并保留有效数据':'核对并保留原表内容';const reason=(data.reason||'').trim()||defaultReason;try{await this.mutate(this.root()+'/issues',{candidate_id:id,candidate_revision:c.candidate_revision,issue_id:iid,action:data.action,fields,reason},'处置决定已保存；争议已更新。');}catch{}};},
 decision(id){if(!this.leave())return;const c=this.member(id),f=this.flights[c.location.section];const form=this.form('明确本次来源处置',`<p>本次：${esc(c.raw_fields.result)}；已有结果：${esc(c.existing_record?.result?.value??c.existing_record?.result?.raw_text??'无')}。保留旧值、更正与独立复测由人工选择。</p><label>本次处置<select name="action" required><option value="">请选择</option>${Object.entries(actions).filter(([k])=>k!=='blank'&&k!=='restore').map(([k,v])=>`<option value="${k}">${v}</option>`).join('')}</select></label>`);form.onsubmit=async e=>{e.preventDefault();const data=this.values(form),previous=['correction','keep_old'].includes(data.action)?f.adopted_versions[id]:f.previous_versions[id];if(!data.reason.trim())return this.message('历史处置须填写简短业务依据。');try{await this.mutate(this.root()+'/decisions',{candidate_id:id,candidate_revision:c.candidate_revision,action:data.action,previous_version_id:previous||null,reason:data.reason},'本次决定已保存草稿；必要复核和整表提交前，正式值不变。');}catch{}};},
 approve(id){if(!this.leave())return;const f=this.flights[this.sheet];const form=this.form('必要高级复核 · 正式结果暂不改变',`<p>本次草稿决定：${esc(actions[this.saved[id]?.action])}</p><label>复核动作<select name="action" required><option value="">请选择</option><option value="confirm_fact">仅确认检测事实</option><option value="adopt">正式采用（整表提交时执行）</option></select></label>`);form.onsubmit=async e=>{e.preventDefault();const d=this.values(form);try{await this.mutate(this.root()+'/review',{candidate_id:id,decision_fingerprint:f.decision_fingerprints[id],...d},'必要复核已保存；正式值在整表提交时执行。');}catch{}};},
 restore(id){if(!this.leave())return;const c=this.member(id),form=this.form('恢复被排除草稿 / 撤销未提交拆分','<p>恢复后重新检查问题和重复；已形成事实的候选不能草稿恢复。</p>');form.onsubmit=async e=>{e.preventDefault();try{await this.mutate(this.root()+'/restore',{candidate_id:id,candidate_revision:c.candidate_revision,action:'restore',reason:this.values(form).reason},'已恢复草稿并重新判断来源问题。');}catch{}};},
 classify(id){if(!this.leave())return;const region=this.sheets.find(s=>s.section===this.sheet).regions.find(r=>r.id===id);const form=this.form('明确区域用途',`<p>${esc(this.sheet)} · ${esc(region.coordinates.join(', '))}</p><p>包含检测结果时应先补录或重解析，不能把真实结果当作备注跳过。</p><label>用途<select name="role" required><option value="">请选择</option>${Object.entries({unclassified:'重新标为待核对',identity:'身份 / 表头',standard:'标准',note:'备注',auxiliary:'辅助数据',excluded:'人工排除区域'}).map(([k,v])=>`<option value="${k}">${v}</option>`).join('')}</select></label>`);form.onsubmit=async e=>{e.preventDefault();try{await this.mutate(this.root()+'/regions',{region_id:id,...this.values(form)},'区域用途已保存；其他未分类区域继续阻断。');}catch{}};}
};
ui.legacyOpen=g.v4LedgerReview.open;
g.v4LedgerReview.open=function(id){return ui.open(id).catch(e=>{document.getElementById('v4LedgerInlineEditor').textContent='读取失败：'+e.message;});};
const proto=Object.getPrototypeOf(g.v4Intake),renderIntake=proto.renderPreviewRightPanel;
async function directEnterV5(p) {
  g.v46Busy?.show('正在准备原始工作表并加载台账…');
  try {
    const unreceived = p.candidates.filter(c => c.lifecycle === 'draft' && !c.received);
    if (unreceived.length || !p.candidates.length) {
      await api(`/previews/${p.preview_id}/receive`, {
        base_revision: p.base_revision,
        idempotency_key: 'v5-auto-receive-' + p.preview_id + '-' + p.base_revision,
        defer_confirmation: true,
        candidate_ids: unreceived.map(c => c.id)
      });
    }
    await ui.received(p.preview_id, p.project);
  } catch(e) {
    g.toast?.(e.message || '进入台账失败');
  } finally {
    g.v46Busy?.hide();
  }
}
proto.renderPreviewRightPanel=function(p){if(g.v4Api.mode!=='real'||p.contract_revision!=='5')return renderIntake.call(this,p);this.currentPreview=p;const target=document.getElementById('v4IntakeRightCol');if(p.duplicate_source){target.innerHTML=`<div class="v5-panel"><h3>已保存的同一来源</h3><p>${esc(p.filename)} · 继续原有核对进度，不重复接收。</p><button class="btn btn-primary" id="v51ResumeSource">进入原表核对与验收 ➔</button></div>`;document.getElementById('v51ResumeSource').onclick=()=>directEnterV5(p);return;}target.innerHTML=`<div class="v5-panel"><h3>来源已解析完成 · ${esc(p.project)}</h3><p><strong>${esc(p.filename)}</strong> · 共提取 <strong>${p.candidates.length}</strong> 项检测数据。</p><p class="v3-note">系统已自动完成项目与来源归属识别，支持多表原样对照核对与一键采纳。</p><button class="btn btn-primary btn-lg" id="v5DirectWorksheet" style="padding:10px 24px;font-size:15px;margin-top:8px;">➔ 进入原表核对与验收</button></div>`;document.getElementById('v5DirectWorksheet').onclick=()=>directEnterV5(p);};
g.addEventListener('beforeunload',e=>{if(ui.dirty){e.preventDefault();e.returnValue='';}});
document.addEventListener('DOMContentLoaded',()=>{
 const brand=document.querySelector('.brand-titles p');if(brand)brand.textContent='Analytical Testing Hub · V5.5 原表核对候选版 · 尚未完整验收';
 const select=g.selectProject;if(select&&g.v4Api.mode==='real')g.selectProject=function(id,...args){if(!ui.leave())return;if(ui.preview&&id!==ui.preview.project){ui.teardownWorkspace?.();ui.serial++;ui.preview=null;ui.selected?.clear();ui.sheet=null;g.v4LedgerReview.summary=true;g.v4LedgerReview.upload=null;const u=new URL(g.location.href);u.searchParams.delete('upload');u.searchParams.delete('sheet');g.history.replaceState(null,'',u);}return select.call(this,id,...args);};
 const u=new URL(g.location.href);ui.sheet=u.searchParams.get('sheet');const wanted=u.searchParams.get('upload');if(wanted){g.v4LedgerReview.summary=false;g.v4LedgerReview.upload=wanted;}
});
})(window);
