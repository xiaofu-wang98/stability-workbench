/* The worksheet remains the workspace; all task/qualification facts come from Python. */
(function(g){
'use strict';
const ui=g.v5Review,esc=ui.esc,api=ui.api;
const originalIssue=ui.issue;
const labels={method_conflict:'中英文方法冲突',identity_conflict:'中英文子结果冲突',row_exclusion:'整行不采用',source_revision:'来源删改',identity_ambiguity:'名称 / 规格',source_comment:'来源批注',identity:'身份 / 子结果',collision:'身份重复',region:'来源区域',history:'历史处置',approval:'等待批准',formula_result:'结果公式',numeric_format:'数值解释',structure:'来源结构',receipt:'接收来源',unresolved:'需重新核对'};
const states={needs_review:'待判断',needs_approval:'等待批准',ready:'可确认入库',committed:'已入库',historical_partial:'待判断'};
Object.assign(ui,{
 // One route owns source review, formal output and upload scenes.
 scene:'upload',
 updateFloatingHeader(){
  const table=document.getElementById('ledgerDataTable'),frame=table?.parentElement;
  if(!table||this.scene!=='source')return;
  const blocks=this.sheets.find(s=>s.section===this.sheet)?.blocks||[];
  const active=blocks.filter(b=>b.header_row&&table.querySelector(`[data-source-row="${b.header_row}"]`)?.getBoundingClientRect().top<0&&table.querySelector(`[data-source-row="${b.end_row}"]`)?.getBoundingClientRect().bottom>0).at(-1);
  let node=document.getElementById('v53FloatingHeader');
  if(!active){if(node)node.hidden=true;return;}
  if(!node||active.header_row!==this.floatingHeaderRow){this.installFloatingHeader(active);node=document.getElementById('v53FloatingHeader');}
  const r=frame.getBoundingClientRect();node.hidden=false;node.style.left=Math.max(0,r.left)+'px';node.style.width=Math.min(frame.clientWidth,g.innerWidth-r.left)+'px';
  const clone=node.firstChild;clone.style.transform=`translateX(${-frame.scrollLeft}px)`;
  clone.querySelectorAll('[data-frozen-left]').forEach(c=>{c.style.position='relative';c.style.left=frame.scrollLeft+'px';c.style.zIndex='2';});
 },
 installFloatingHeader(block){
  document.getElementById('v53FloatingHeader')?.remove();const table=document.getElementById('ledgerDataTable');
  block||=this.sheets.find(s=>s.section===this.sheet)?.blocks?.find(b=>b.header_row);if(!block)return;
  this.floatingHeaderRow=block.header_row;const node=document.createElement('div');node.id='v53FloatingHeader';node.hidden=true;node.setAttribute('aria-hidden','true');node.dataset.headerRow=block.header_row;
  const clone=document.createElement('table');clone.className='v53-header-table';clone.style.width=table.style.width;
  clone.innerHTML=table.querySelector('colgroup').outerHTML+'<tbody>'+[block.header_row,block.header_row+1].map(r=>table.querySelector(`[data-source-row="${r}"]`)?.outerHTML||'').join('')+'</tbody>';
  clone.querySelectorAll('input,button,details,.v52-source-warning,.v52-changed').forEach(n=>n.remove());
  clone.querySelectorAll('td,th').forEach(n=>{const freeze=n.classList.contains('v52-item-column')||n.tagName==='TH';n.removeAttribute('style');n.removeAttribute('id');if(freeze)n.dataset.frozenLeft='true';});
  node.append(clone);document.body.append(node);
 },
 updateViewport(){
  const frame=document.querySelector('.v52-matrix-frame'),bar=document.getElementById('v54HorizontalScroll'),table=document.getElementById('ledgerDataTable');
  if(!frame||!bar||!table||this.scene!=='source')return;
  const r=frame.getBoundingClientRect(),width=Math.min(frame.clientWidth,g.innerWidth-r.left),overflow=table.scrollWidth>frame.clientWidth+1;
  bar.hidden=!(overflow&&r.top<g.innerHeight-22&&r.bottom>22);
  bar.style.left=Math.max(0,r.left)+'px';bar.style.width=Math.max(0,width)+'px';
  const innerWidth=table.scrollWidth+'px';if(bar.firstChild.style.width!==innerWidth)bar.firstChild.style.width=innerWidth;
  if(Math.abs(bar.scrollLeft-frame.scrollLeft)>1)bar.scrollLeft=frame.scrollLeft;
  this.updateFloatingHeader();
 },
 installViewport(){
  this.viewportController?.abort();this.viewportObserver?.disconnect();this.viewportFrame&&cancelAnimationFrame(this.viewportFrame);
  document.getElementById('v54HorizontalScroll')?.remove();document.getElementById('v53FloatingHeader')?.remove();this.floatingHeaderRow=null;
  const frame=document.querySelector('.v52-matrix-frame'),table=document.getElementById('ledgerDataTable');if(!frame||!table)return;
  const bar=document.createElement('div');bar.id='v54HorizontalScroll';bar.tabIndex=0;bar.setAttribute('role','region');bar.setAttribute('aria-label','原表横向滚动');bar.append(document.createElement('div'));document.body.append(bar);
  const controller=this.viewportController=new AbortController(),options={signal:controller.signal,passive:true};
  const schedule=()=>{if(this.viewportFrame)return;this.viewportFrame=requestAnimationFrame(()=>{this.viewportFrame=null;this.updateViewport();});};
  frame.addEventListener('scroll',()=>{this.rememberSheet();schedule();},options);
  bar.addEventListener('scroll',()=>{if(Math.abs(frame.scrollLeft-bar.scrollLeft)>1)frame.scrollLeft=bar.scrollLeft;schedule();},options);
  g.addEventListener('scroll',()=>{this.rememberSheet();schedule();},options);g.addEventListener('resize',schedule,options);
  g.visualViewport?.addEventListener('resize',schedule,options);
  this.viewportObserver=new ResizeObserver(schedule);this.viewportObserver.observe(frame);this.viewportObserver.observe(table);schedule();
 },
 setScene(scene){
  if(scene!=='source'){this.teardownWorkspace();g.v5Outputs?.unmount();}
  this.scene=scene;document.body.dataset.reviewScene=scene;
  const bar=document.getElementById('v43Connection');if(bar&&scene==='source'&&!bar.querySelector('details')){const detail=document.createElement('details');detail.innerHTML='<summary>本地已连接 · 连接详情</summary>';detail.append(...bar.childNodes);bar.append(detail);const home=[...detail.querySelectorAll('button')].find(b=>b.textContent==='上传其他项目数据');if(home)bar.append(home);}else if(bar&&scene!=='source'&&bar.querySelector('details')){const detail=bar.querySelector('details');detail.querySelector('summary').remove();bar.append(...detail.childNodes);detail.remove();}
 },
 renderLedgerScene(base,renderer,args,project){
  if(this.dirty||this.scene==='source'||this.scene==='upload')return;
  this.teardownWorkspace();document.getElementById('ledgerDataTable')?.classList.remove('v4-source-matrix');
  base.apply(renderer,args);
  if(!g.v4LedgerReview.refreshing&&project){g.v4LedgerReview.summary=true;g.v4LedgerReview.attach(project);}
  g.v5Outputs?.mount();
 },
 async enterLedger(project){
  if(!project)return;
  if(!g.v4LedgerReview.summary&&g.v4LedgerReview.upload){this.setScene('source');await g.v4LedgerReview.attach(project);}
  else{this.setScene('formal');await g.v4ResultCell.loadProjectCells(project);}
 },
 async received(id,project){
  if(!this.leave())return;this.selected=new Set();this.sheet=null;
  Object.assign(g.v4LedgerReview,{summary:false,upload:id,project});this.setScene('source');
  const u=new URL(g.location.href);u.searchParams.set('view','ledger');u.searchParams.set('upload',id);u.searchParams.delete('sheet');g.history.replaceState(null,'',u);
  await g.switchMainTab('ledger');
 },
 async chooseSource(id){
  if(!this.leave()){document.getElementById('v4LedgerSourceChoice').value=this.upload||'';return;}
  if(!id)return this.formal();this.sheet=null;this.setScene('source');await this.open(id);
 },
 stashSourceChrome(card){
  if(this.chromeStash)return;this.chromeStash=[];
  const stash=node=>{if(!node)return;const marker=document.createComment('formal scene');node.before(marker);this.chromeStash.push([marker,node]);node.remove();};
  for(const node of [...document.getElementById('tab-ledger').children])if(node!==card)stash(node);
  stash(card.querySelector(':scope>.card-header'));
  for(const node of [...card.querySelector(':scope>.card-body').children])if(!['v52Workspace','v4LedgerSourceControls'].includes(node.id)&&!node.contains(document.getElementById('ledgerDataTable')))stash(node);
  document.getElementById('v45ReceiptNotice')?.remove();g.v5Outputs?.unmount();
 },
 toggleDirectory(){this.directoryClosed=!this.directoryClosed;document.getElementById('v52Workspace')?.classList.toggle('directory-closed',this.directoryClosed);},
 taskTitle(t){
  if(t.proposal?.guidance)return t.proposal.guidance.title;
  return ({
    row_exclusion: t.proposal.rows ? '原表已整项划线，建议确认排除' : ('第 ' + t.proposal.row + ' 行原表已划线，建议确认排除'),
    identity: '核对 ' + (t.fields?.length ? t.fields.map(f => this.names[f] || f).join(' / ') : '基础信息'),
    source_revision: t.proposal.retain_complete_text ? '完整保留未划线内容（中英文全文）' : t.proposal.kind === 'whole' ? '原表已整格划线作废，确认排除' : '原表有删改痕迹，采纳有效值',
    identity_ambiguity: '核对 ' + (t.fields?.length ? t.fields.map(f => this.names[f] || f).join(' / ') : '检测项/方法'),
    source_comment: '核对原表备注说明',
    numeric_format: '核对数值格式',
    collision: '检测项重复冲突',
    region: '明确来源用途',
    structure: t.proposal?.operation==='keep_single'?'核对合并排版，沿用各时间点结果':'核对真实逻辑归属',
    history: '历史正式值对比核对',
    approval: '签批说明'
  })[t.code] || labels[t.code] || '待核对项';
 },
 selectedHere(){return this.preview.candidates.filter(c=>this.selected.has(c.id)&&c.lifecycle==='draft'&&c.location.section===this.sheet).map(c=>c.id);},
 suggestSelected(){const ids=this.selectedHere();if(!ids.length)return this.message('先勾选原行或结果格。');const tasks=this.tasks.filter(t=>t.proposal?.eligible&&t.members.some(m=>ids.includes(m.candidate_id)));if(!tasks.length)return this.message('所选范围没有可直接采用的明确建议，请点击源格说明核对。');return this.previewScope({section:this.sheet,task_ids:tasks.map(t=>t.id),operation:'suggested',exclude_candidate_ids:[...new Set(tasks.flatMap(t=>t.members.map(m=>m.candidate_id)))].filter(id=>!ids.includes(id))},'按建议核对所选');},
 quickConfirm(id){
  if(!this.leave())return;
  const c=this.member(id);if(!c)return;
  const state=this.matrix.worksheet_review.candidate_states[id];
  if(!state)return this.message('当前核对状态未加载，请重新读取原表。');
  if(state.suggestion_ids.length)return this.previewScope({section:this.sheet,task_ids:state.suggestion_ids,operation:'suggested'},'采纳核对建议');
  if(state.task_ids.length)return this.task(state.task_ids[0]);
  if(state.has_unresolved)return this.edit(id);
  this.message('本格无需逐格确认；整表资格请查看本表核对进度。');
 },
 quickExclude(id){
  if(!this.leave())return;
  const c=this.member(id);if(!c)return;
  return this.previewScope({section:this.sheet,candidate_ids:[id],operation:'exclude_selected'},'确认排除此格数据',true);
 },
 excludeSelected(){const ids=this.selectedHere();if(!ids.length)return this.message('先勾选原行或结果格。');return this.previewScope({section:this.sheet,candidate_ids:ids,operation:'exclude_selected'},'不采用所选 · 确认明确范围',true);},
 modifySelected(){const ids=this.selectedHere();if(!ids.length)return this.message('先勾选原行或结果格。');if(ids.length===1)return this.edit(ids[0]);const shared=this.tasks.find(t=>t.code==='identity'&&ids.every(id=>t.members.some(m=>m.candidate_id===id)));if(shared)return this.sharedEdit(shared,ids);return this.message('所选位置含不同来源，请点具体源格修改；同类删改可预览各格自己的建议。');},
 sharedEdit(t,ids){
  const members=ids.map(id=>this.member(id)).filter(Boolean),batches=[...new Set(members.map(c=>c.corrections.batch||c.interpreted_fields.batch||c.raw_fields.batch))],times=[...new Set(members.map(c=>c.corrections.time_point||c.interpreted_fields.time_point||c.raw_fields.time_point))];
  const origins=t.evidence.map(e=>{const sheet=this.matrix.sheets.find(s=>s.name===e.section),cell=sheet?.cells.find(c=>c.row===e.row&&c.column===e.column);return `<p>${esc(e.section+'!'+this.colName(e.column)+e.row)}：${esc(cell?.text||'（来源未注明该字段）')}</p>`;}).join('');
  const form=this.form('明确共享来源解释',`<p>${esc(t.evidence.map(e=>e.section+'!'+this.colName(e.column)+e.row).join('、'))}：${esc(t.detail)}</p>${origins}<p>范围：${esc(batches.join("、"))}；时间点 ${esc(times.join("、"))}；共 ${members.length} 个结果。仅保存填写字段，其他待确认项继续保留。</p><div class="v5-fields">${this.fields(t.fields)}</div>`);
  form.onsubmit=e=>{e.preventDefault();const d=this.values(form),fields=Object.fromEntries(t.fields.filter(k=>String(d[k]||'').trim()).map(k=>[k,d[k]]));if(!Object.keys(fields).length)return this.message('请填写需要明确的来源字段。');this.cancel();return this.previewScope({section:this.sheet,task_ids:[t.id],operation:'manual',fields,exclude_candidate_ids:t.members.map(m=>m.candidate_id).filter(id=>!ids.includes(id))},'预览共享来源修改');};
 },
 captureSourceView(){this.rememberSheet();this.returnView={key:this.viewKey(),value:sessionStorage.getItem(this.viewKey())};},
 previewContext(){return [this.upload,this.sheet,this.serial,this.previewSerial].join('|');},
 previewValid(info){
  if(!info?.fingerprint||!Array.isArray(info.changes)||!info.summary)throw Error('预览契约不完整，请重新读取。');
  for(const c of info.changes){
   if(!['keep_active','manual','exclude','classify','keep_body','keep_single'].includes(c.operation)||!c.section||!Array.isArray(c.evidence)||!c.evidence.length||c.evidence.some(e=>!Number.isInteger(e.row)||!Number.isInteger(e.column)))throw Error('预览缺少操作类型或来源坐标，无法确认。');
   if(!c.fields||typeof c.fields!=='object')throw Error('预览缺少字段变更，无法确认。');
   if(['keep_active','manual','classify'].includes(c.operation)&&!Object.keys(c.fields).length)throw Error('预览缺少拟保存字段，无法确认。');
   for(const v of Object.values(c.fields))if(!v||!Object.hasOwn(v,'before')||!Object.hasOwn(v,'after'))throw Error('预览缺少变更前后值，无法确认。');
  }
  return info;
 },
 async previewScope(scope,title,requireReason=false,questionReason=''){
  if(!this.leave())return;
  const requestKey=JSON.stringify([this.upload,scope]);if(this.preparingScope?.key===requestKey&&this.preparingScope.context===this.previewContext())return;
  this.captureSourceView();
  const sources=(scope.task_ids||[]).flatMap(id=>this.matrix.worksheet_review.tasks.find(t=>t.id===id)?.evidence||[]).map(e=>this.colName(e.column)+e.row).join('、');
  const loading=this.form(title,`<p role="status">正在准备 ${esc(sources)} 的预览…</p>`);loading.querySelector('.btn-primary').disabled=true;
  const context=this.previewContext(),pending={key:requestKey,context};this.preparingScope=pending;
  try{
    const info=this.previewValid(await api(this.root()+'/tasks/preview',scope));
    if(context!==this.previewContext()||!loading.isConnected)return;
    requireReason ||= scope.operation==='exclude_selected';
    const excluding=info.changes.length&&info.changes.every(c=>c.operation==='exclude');
    const defaultReason=requireReason?'':excluding?info.changes.map(c=>c.explanation).filter(Boolean).join('；'):'核对原表建议、范围和例外后保存草稿';
    const form=this.form(title,`${this.scopeMarkup(info)}<p>${esc(info.notice||'')}</p>`,questionReason||defaultReason);
    const questionTask=scope.task_ids?.length===1?this.matrix.worksheet_review.tasks.find(t=>t.id===scope.task_ids[0]):null;
    if(scope.operation==='manual'&&questionTask&&(questionTask.proposal.guidance||['identity_conflict','method_conflict'].includes(questionTask.code))){
     this.prepareFocusedForm(form);const back=document.createElement('button');back.type='button';back.className='btn btn-outline';back.textContent='返回修改';form.querySelector('.btn-primary').after(back);
     back.onclick=()=>{const reason=form.elements.reason.value;this.cancel();if(questionTask.proposal.guidance)this.focusedQuestion(questionTask,scope.fields,reason);else this.identityConflict(questionTask,scope.fields[questionTask.fields[0]],reason);};
    }
    const readyContext=this.previewContext();
    form.querySelector('.btn-primary').textContent=excluding?'确认排除此范围':info.changes.every(c=>c.operation==='keep_single')?'确认沿用各时间点结果':'确认采纳并应用';
    form.elements.reason.required=requireReason;
    if(requireReason){form.elements.reason.placeholder='请说明不采用这些来源的业务依据';form.querySelector('.v5-field-hint').textContent='手选排除需要明确依据。';}
    form.querySelector('.btn-primary').disabled=!info.summary.tasks;
    form.onsubmit=async e=>{e.preventDefault();if(readyContext!==this.previewContext())return;const reason=form.elements.reason.value.trim();if(requireReason&&!reason)return this.localMessage('请填写不采用这些来源的业务依据。');try{await this.mutate(this.root()+'/tasks/apply',{scope,fingerprint:info.fingerprint,reason:reason||'核对原表建议、范围和例外后保存草稿'},`已保存 ${info.summary.sources} 处来源决定；其他独立问题已重新核对。`);}catch{}};
  }catch(e){if(context===this.previewContext()){this.localMessage('预览失败：'+e.message);const retry=document.createElement('button');retry.type='button';retry.textContent='重新准备预览';retry.onclick=()=>this.previewScope(scope,title,requireReason);loading.append(retry);}}finally{if(this.preparingScope===pending)this.preparingScope=null;}
 },
 scopeMarkup(info){
  this.previewValid(info);
  if(info.changes.length&&info.changes.every(c=>c.operation==='keep_single')){
   return `<div class="v52-preview-table"><p>原表合并范围的延伸行没有独立来源内容。本次只确认排版归属，各时间点仍保留自己的结果和已有决定。</p>${info.changes.map(c=>`<p><strong>${esc(c.section)}</strong> · ${c.affected_results} 个时间点 / 来源</p><table class="v55-action-diff-table"><thead><tr><th>原格范围</th><th>时间点</th><th>沿用值</th></tr></thead><tbody>${c.retained_results.map(r=>`<tr><td>${r.range?this.colName(r.range[1])+r.range[0]+':'+this.colName(r.range[3])+r.range[2]:this.colName(r.location.column)+r.location.row}</td><td>${esc(r.fields.time_point)}</td><td>${esc(r.display_result||r.fields.result)} ${esc(r.display_unit||r.fields.unit)}${(r.warnings||[]).map(w=>`<p role="alert">${esc(w)}</p>`).join('')}</td></tr>`).join('')}</tbody></table><details><summary>已识别身份与逐来源信息（均不改写）</summary><p>${Object.entries(c.common_fields||{}).filter(([k])=>!['result','time_point'].includes(k)).map(([k,v])=>esc(this.names[k]||k)+'：'+esc(v)).join('；')}</p>${c.retained_results.map(r=>`<p>${this.colName(r.location.column)}${r.location.row} · ${esc(r.fields.test_item)} · ${esc(r.fields.method)}</p>`).join('')}</details>`).join('')}<p>本次 ${info.summary.tasks} 个排版决定，沿用 ${info.summary.results} 个原有结果。独立方法、数值或单位问题继续保留，不会整表自动入库。</p>${info.exceptions.map(e=>`<p role="alert">例外保留：${esc(e.reason)}</p>`).join('')}</div>`;
  }
  const operation={keep_active:'采纳有效值',manual:'人工明确解释',exclude:'排除不入库',classify:'区域分类',keep_body:'保留当前正文及数值',keep_single:'核对排版，沿用每处现有一条结果'};
  const revision=(c)=>Object.entries(c.source_revisions||{}).map(([field,r])=>`<p>${esc(this.names[field]||field)}原表：${(r.segments||[]).map(s=>s.struck?'<del>'+esc(s.text)+'</del>':esc(s.text)).join('')}<br>已划掉：${esc((r.segments||[]).filter(s=>s.struck).map(s=>s.text).join(''))}；保留：${esc(r.active_text||'（空）')}</p>`).join('');
  return `<div class="v52-preview-table"><table class="v55-action-diff-table"><thead><tr><th>来源位置 / 动作</th><th>原表证据 / 当前值</th><th>本次拟保存</th></tr></thead><tbody>${info.changes.map(c=>`<tr><td>${c.evidence.map(e=>esc(e.section)+'!'+this.colName(e.column)+e.row).join('、')}<br>${operation[c.operation]}</td><td>${revision(c)}${c.retained_results?.length?`<ul>${c.retained_results.map(r=>`<li>${this.colName(r.location.column)}${r.location.row} · ${esc(r.fields.time_point)} · 沿用值 ${esc(r.display_result||r.fields.result)} ${esc(r.display_unit||r.fields.unit)} · ${esc(r.fields.test_item)}</li>`).join('')}</ul>`:''}${(c.review_results||[]).length?`<details open><summary>关联结果与时间点（${c.review_results.length} 条）</summary>${c.review_results.map(r=>`<p>${this.colName(r.location.column)}${r.location.row} · 原表第 ${r.logical_row||r.location.row} 行 · ${esc(r.time_point)}<br>原始结果：${esc(r.original_result)}<br>${r.result_changed?'本次另行明确结果':'结果保持'}：${esc(r.current_result)}</p>`).join('')}</details>`:''}${Object.entries(c.fields).map(([k,v])=>'<strong>'+esc(this.names[k]||k)+'</strong>：'+esc(v.before??'（空）')).join('<br>')||esc(c.explanation)}</td><td>${Object.entries(c.fields).map(([k,v])=>'<strong>'+esc(this.names[k]||k)+'</strong>：<span class="v55-diff-after">'+esc(v.after??'（空）')+'</span>').join('<br>')||operation[c.operation]}${c.explanation?'<p>'+esc(c.explanation)+'</p>':''}</td></tr>`).join('')}</tbody></table></div>${info.exceptions.map(e=>`<p role="alert">例外保留：${esc(e.section)} ${esc((e.evidence||[]).map(l=>this.colName(l.column)+l.row).join('、'))} · ${esc(e.reason)}</p>`).join('')}<p>涉及 ${info.summary.tasks} 处来源决定，影响 ${info.summary.results} 个关联结果。</p>`;
 },
 notesMarkup(){const notes=(this.matrix.worksheet_review.notes||[]).filter(n=>n.section===this.sheet);if(!notes.length)return '';return `<details class="v53-notes"><summary>本表重要备注${notes.length>4?'，还有 '+(notes.length-4)+' 条':''} · 展开完整原文与范围</summary>${notes.map(n=>`<p>${esc(n.text)}<button type="button" data-v5="locateNote" data-id="${esc(n.id)}">${n.pending?'定位待处理来源':'定位原文'}</button><small>${esc(n.evidence.map(e=>this.colName(e.column)+e.row).join('、'))} · ${esc(n.scope)}</small></p>`).join('')}</details><div class="v53-note-lines">${notes.slice(0,4).map(n=>`<div title="${esc(n.text)}">${esc(n.evidence.map(e=>this.colName(e.column)+e.row).join('、'))} · ${esc(n.text.replace(/\s+/g,' '))}</div>`).join('')}</div>`;},
 locateNote(id){const n=this.matrix.worksheet_review.notes.find(n=>n.id===id);if(n)this.locateTask({evidence:n.evidence});},
 taskList(){return (this.matrix?.worksheet_review?.tasks||[]).filter(t=>t.blockers.length&&t.blockers.some(b=>b.startsWith(this.sheet+'|')));},
 viewKey(){return 'v52-view:'+this.upload+':'+this.sheet;},
 rememberSheet(){const frame=document.getElementById('ledgerDataTable')?.parentElement;if(!frame||!this.sheet)return;sessionStorage.setItem(this.viewKey(),JSON.stringify({top:g.scrollY,left:frame.scrollLeft,filter:this.taskFilter||'',selected:[...this.selected]}));},
 restoreSheet(){let view;try{view=JSON.parse(sessionStorage.getItem(this.viewKey())||'null');}catch{}if(!view){g.scrollTo(0,0);return;}this.selected=new Set((view.selected||[]).filter(id=>this.preview.candidates.some(c=>c.id===id&&c.lifecycle==='draft')));document.querySelectorAll('[data-select]').forEach(n=>n.checked=this.selected.has(n.dataset.select));this.selection();this.taskFilter=view.filter||'';const filter=document.getElementById('v52TaskFilter');if(filter)filter.value=this.taskFilter;const frame=document.getElementById('ledgerDataTable').parentElement;g.scrollTo(0,view.top||0);frame.scrollLeft=view.left||0;},
 workspace(){
  let shell=document.getElementById('v52Workspace');const table=document.getElementById('ledgerDataTable'),frame=table.parentElement,controls=document.getElementById('v4LedgerSourceControls');
  if(!shell){shell=document.createElement('div');shell.id='v52Workspace';shell.className='v52-workspace';frame.before(shell);shell.innerHTML='<nav id="v52SheetDirectory" aria-label="原工作表目录"></nav><main id="v52SheetPane"></main>';const pane=document.getElementById('v52SheetPane');pane.append(controls,frame);}
  document.getElementById('tab-ledger')?.classList.add('v52-review-active');table.closest('.card')?.classList.add('v52-source-card');
  this.stashSourceChrome(table.closest('.card'));frame.classList.add('v52-matrix-frame');
 },
 render(){
  this.previewSerial=(this.previewSerial||0)+1;
  const editor=document.getElementById('v4LedgerInlineEditor');if(!editor)return;
  const f=this.flights[this.sheet];if(!f)return;this.workspace();
  const review=this.matrix.worksheet_review,progress=review.sheet_states||[],status=progress.find(s=>s.section===this.sheet)||{};
  this.tasks=this.taskList();this.sourceTasks=new Map();this.impactTasks=new Map();
  for(const task of this.tasks){for(const loc of (task.code==='row_exclusion'?task.evidence.slice(0,1):task.evidence)){if(loc.section!==this.sheet)continue;const k=loc.row+':'+loc.column;if(!this.sourceTasks.has(k))this.sourceTasks.set(k,[]);this.sourceTasks.get(k).push(task);}for(const m of task.members){if(!m.candidate_id)continue;if(!this.impactTasks.has(m.candidate_id))this.impactTasks.set(m.candidate_id,[]);this.impactTasks.get(m.candidate_id).push(task);}}
  this.sourceChanges=new Map();for(const c of review.changes||[]){if(c.section!==this.sheet)continue;const k=c.row+':'+c.column;if(!this.sourceChanges.has(k))this.sourceChanges.set(k,[]);this.sourceChanges.get(k).push(c);}
  document.getElementById('v52SheetDirectory').innerHTML=`<strong>原工作表 · ${this.sheets.length}</strong>${this.sheets.map(s=>{const p=progress.find(p=>p.section===s.section)||{};return `<button class="v52-sheet ${s.section===this.sheet?'active':''}" data-v5="switchSheet" data-id="${esc(s.section)}" title="${esc(s.section)}" ${s.section===this.sheet?'aria-current="page"':''}><span>${esc(s.section)}${s.hidden?'（原件隐藏）':''}</span><small>${p.source_only&&p.state==='committed'?'辅助表已核对':states[p.state]||'未开始核对'} · ${p.remaining_tasks??0} 处待判断 处 · 涉及 ${p.affected_results??0} 个结果</small><small>已保存修改 ${p.saved_changes??0} 处</small></button>`;}).join('')}`;
  const sourceOnly=status.source_only;
  const choiceLabel=document.getElementById('v4LedgerSourceChoice')?.closest('label');choiceLabel?.remove();
  editor.innerHTML=`<section class="v52-review" aria-label="原表核对"><div class="v52-heading"><button class="btn btn-outline" data-v5="toggleDirectory">工作表目录</button><strong>${esc(this.sheet)}</strong><span id="v5Preflight" role="status">${states[f.state]} · ${status.remaining_tasks??0} 处 · 涉及 ${status.affected_results??0} 个结果 · 已处理 ${status.saved_changes??0} 处</span><button type="button" class="btn btn-outline" data-v5="reviewSummary">本表资格</button></div><div class="v5-toolbar"><button class="btn btn-outline" data-v5="previousTask">上一待核对</button><button class="btn btn-outline" data-v5="nextIssue">下一待核对</button><button class="btn btn-outline" data-v5="suggestSelected">按建议核对所选</button><button class="btn btn-outline" data-v5="excludeSelected">不采用所选</button><button class="btn btn-outline" data-v5="modifySelected">修改所选</button><button class="btn btn-outline" data-v5="manualSheet" ${f.state==='committed'||!g.v4Api.can('confirm_fact')?'disabled':''}>人工兜底编辑</button><button class="btn btn-outline" data-v5="batchTasks">预览本文件同类情况</button><button class="btn btn-primary" id="v46ConfirmSheet" data-v5="${sourceOnly?'archiveAuxiliary':'commit'}" ${f.state==='committed'||!g.v4Api.can('confirm_fact')||(!sourceOnly&&!f.can_commit)?'disabled':''}>${f.state==='committed'?'本表已入库':sourceOnly?'确认辅助表':'确认本表'}</button>${f.state==='committed'&&!sourceOnly?'<button class="btn btn-outline" data-v5="formal">查看已入库结果</button>':''}</div><div id="v55ActionBar" class="v55-action-bar"></div><div id="v55ActionTray" class="v55-action-tray" style="display:none;"></div><p id="v4LedgerReviewMessage" role="status" aria-live="polite"></p>${this.notesMarkup()}<div id="v5Form"></div><details class="v52-secondary"><summary>核对详情与其他操作</summary><p id="v5Selection"></p><label>定位类别 <select id="v52TaskFilter"><option value="">全部待核对</option>${Object.entries(labels).map(([k,v])=>`<option value="${k}">${v}</option>`).join('')}<option value="changed">已保存修改</option></select></label><div id="v5Regions"></div><div id="v52Changes"></div><details><summary>完整来源原文</summary><div id="v5Raw" class="v5-scroll"></div></details><details><summary>来源与规则设置</summary><div class="v5-toolbar">${Object.entries({original:'下载冻结原件',bulk:'批量身份预览',issueGroup:'问题组范围预览',reinterpret:'预览规则升级',mapping:'本文件检项映射',newItem:'建立独立检项',rule:'另存为项目规则',rules:'项目规则管理',regionParse:'指定区域和表头',manual:'绑定原格补录',association:'历史身份关联',rename:'修改检项展示名',refresh:'刷新核对',formal:'查看正式结果汇总'}).map(([k,v])=>`<button class="btn btn-outline" data-v5="${k}">${v}</button>`).join('')}</div></details></details></section>`;
  if(choiceLabel)editor.querySelector('.v52-heading').append(choiceLabel);
  const sheet=this.sheets.find(s=>s.section===this.sheet),unknown=sheet.regions.filter(r=>r.role==='unclassified'),known=sheet.regions.filter(r=>r.role!=='unclassified');
  document.getElementById('v5Regions').innerHTML=`<details id="v51Unclassified"><summary>未明用途 ${unknown.length} 处</summary><button class="btn btn-outline" data-v5="sourceStructure">重新核对来源结构</button>${unknown.map(r=>`<p>${esc(r.coordinates.join('、'))} <button class="btn btn-outline" data-v5="classify" data-id="${esc(r.id)}">明确区域用途</button></p>`).join('')}</details><details><summary>已识别来源用途与依据 ${known.length} 处</summary>${known.map(r=>`<p>${esc(r.coordinates.join('、'))} · ${esc({result:'检测结果',identity:'表头 / 身份',standard:'标准',note:'备注',auxiliary:'辅助来源',excluded:'明确不采用'}[r.role]||r.role)} · ${esc(r.basis)} ${r.role!=='result'&&f.state!=='committed'?`<button data-v5="classify" data-id="${esc(r.id)}">重新核对用途</button>`:''}</p>`).join('')}</details>`;
  const batches=(review.batches||[]).filter(b=>!b.undone);
  document.getElementById('v52Changes').innerHTML=`<p>系统识别与标准化不计作人工改值；人工核对保留单独记录。</p><p>人工字段修改：<br>${(review.changes||[]).filter(c=>c.section===this.sheet).map(c=>`${this.colName(c.column)}${c.row} ${esc(this.names[c.field]||c.field)}：${esc(c.before)} → ${esc(c.after)}`).join('<br>')||'本表没有有效字段修改。'}</p>${(review.checks||[]).filter(c=>c.section===this.sheet).map(c=>`<p>${esc(c.label||'人工核对保留')} · ${esc(c.evidence.map(e=>this.colName(e.column)+e.row).join('、'))} · ${esc(c.reason)}${c.identity_pending?'；仍有独立方法/子结果身份待明确，本次核对未解除该冲突。':''}</p>`).join('')}${batches.length?`<button class="btn btn-outline" data-v5="undoBatch" data-id="${esc(batches.at(-1).batch_id)}">撤销上次草稿处理</button>`:''}`;
  this.renderActionBar();this.renderMatrix(this.preview.candidates.filter(c=>c.location.section===this.sheet));this.renderRaw(0);this.installViewport();this.bind(editor);this.bind(document.getElementById('v52SheetDirectory'));this.bind(document.getElementById('ledgerDataTable'));
  document.getElementById('v52TaskFilter').value=this.taskFilter||'';document.getElementById('v52TaskFilter').onchange=e=>{this.taskFilter=e.target.value;this.taskIndex=-1;this.rememberSheet();};
  document.querySelectorAll('[data-select]').forEach(n=>n.onchange=()=>{n.checked?this.selected.add(n.dataset.select):this.selected.delete(n.dataset.select);this.selection();this.rememberSheet();});this.selection();this.restoreSheet();this.updateViewport();
 },
 sourceMarkup(r,col,body){const k=r+':'+col,ts=this.sourceTasks.get(k)||[],changes=this.sourceChanges.get(k)||[];
  const root=ts.length?`<div class="v52-source-warning">${ts.map(t=>`<button data-v5="task" data-id="${esc(t.id)}">${esc(this.taskTitle(t))}</button>`).join('')}</div>`:'';
  if(changes.length){return `${body}<div class="v52-changed"><strong>已改</strong><details><summary>原值与修改依据</summary>${changes.map(c=>`<p>${esc(this.names[c.field]||c.field)}：${esc(c.before)} → ${esc(c.field==='test_item'?this.label(c.after):c.after||'未注明')}<br>${esc(c.reason||'已保存的当前来源解释')}</p>`).join('')}</details></div>${root}`;}
  return `${body}${root}`;
 },
 renderMatrix(candidates){
  const raw=this.matrix.sheets.find(s=>s.name===this.sheet),table=document.getElementById('ledgerDataTable');if(!raw)return;
  const cells=new Map(raw.cells.map(c=>[c.row+':'+c.column,c])),overlays=new Map(),covered=new Set(),merges=new Map();
  for(const c of candidates){const k=c.location.row+':'+c.location.column;if(!overlays.has(k))overlays.set(k,[]);overlays.get(k).push(c);}
  for(const [r,c,rr,cc] of raw.merges){merges.set(r+':'+c,[rr-r+1,cc-c+1]);for(let y=r;y<=rr;y++)for(let x=c;x<=cc;x++)if(y!==r||x!==c)covered.add(y+':'+x);}
  const content=(r,col)=>{const k=r+':'+col,cell=cells.get(k),list=overlays.get(k)||[];
   if(!list.length)return this.sourceMarkup(r,col,cell?.revision?g.v4LedgerReview.rich(cell.revision):esc(cell?.text||''));
   const archived=list.filter(c=>c.superseded_by?.length),active=list.filter(c=>!c.superseded_by?.length);
   return (archived.length?`<details open class="v53-cell-details"><summary>原格已按标准分别保留 · 查看完整原文</summary>${archived.map(c=>`<div data-candidate="${esc(c.id)}"><p>${esc(c.raw_fields.result)}${c.lifecycle==='withdrawn'?` <button data-v5="restore" data-id="${esc(c.id)}">恢复草稿</button>`:''}</p></div>`).join('')}</details>`:'')+this.sourceMarkup(r,col,'')+active.map(c=>{const direct=(this.sourceTasks.get(k)||[]).filter(t=>t.members.some(m=>m.candidate_id===c.id)),indirect=(this.impactTasks.get(c.id)||[]).filter(t=>!direct.includes(t));const value=c.record?.result?.interpretation?.display_text??c.interpreted_fields.result??c.corrections.result??c.raw_fields.result;const basis=c.record?.key?.reporting_basis||c.interpreted_fields.reporting_basis;
    const reviewState=this.matrix.worksheet_review.candidate_states[c.id];
    const hasProposal=!!reviewState?.suggestion_ids.length;
    const hasPending=!!reviewState?.has_unresolved;
    const quickBar = c.lifecycle === 'draft' ? `<div class="v5-quick-bar" style="margin-top:3px;display:flex;gap:3px;align-items:center;flex-wrap:wrap;">
      ${hasProposal ? `<button type="button" class="btn btn-sm btn-primary" style="padding:0 5px;font-size:11px;line-height:1.5;" data-v5="quickConfirm" data-id="${esc(c.id)}" title="采纳原表核对建议">采纳建议</button>
      <button type="button" class="btn btn-sm btn-outline" style="padding:0 5px;font-size:11px;line-height:1.5;color:#dc3545;" data-v5="quickExclude" data-id="${esc(c.id)}" title="排除此项数据">排除</button>` : hasPending?`<button type="button" class="btn btn-sm btn-outline" data-v5="quickConfirm" data-id="${esc(c.id)}">来源待核对</button>`:`<span class="v54-cell-state">${reviewState?.modified?'已处理草稿':'正常，待整表确认'}</span>`}
      <button type="button" class="btn btn-sm btn-outline" style="padding:0 5px;font-size:11px;line-height:1.5;" data-v5="edit" data-id="${esc(c.id)}" title="人工修改此项">修改</button>
    </div>` : '';
    return `<div data-candidate="${esc(c.id)}" data-review-state="${esc(reviewState?.state||'pending')}" class="${c.lifecycle==='withdrawn'?'v52-excluded':direct.length?'v5-source-pending':''}">${basis?`<strong class="v54-standard-label">${esc(({CN:"中国标准",EP:"欧洲标准",US:"美国标准"})[basis]||basis)}</strong>`:""}<label><input type="checkbox" data-select="${esc(c.id)}" ${this.selected.has(c.id)?'checked':''} ${c.lifecycle!=='draft'?'disabled':''}>${esc(value)}</label>${quickBar}<details class="v53-cell-details"><summary aria-label="查看本格来源与修改">详情${c.lifecycle==='submitted'?' · 已入库':c.lifecycle==='withdrawn'?' · 不采用':''}</summary>${indirect.length?`<p>关联源格待核对，当前数值未判错。</p>${indirect.map(t=>`<button data-v5="task" data-id="${esc(t.id)}">${esc(this.taskTitle(t))}</button>`).join('')}`:''}${c.lifecycle==='draft'?`<button data-v5="edit" data-id="${esc(c.id)}">修改本格</button>${!['same_value','different_value'].includes(c.status)?`<button data-v5="decision" data-id="${esc(c.id)}">本次来源处置</button>`:''}`:''}${this.issueDetails(c,direct)}${['different_value','same_value'].includes(c.status)?`<button data-v5="decision" data-id="${esc(c.id)}">选择历史处置</button>`:''}${this.flights[this.sheet].review_required.includes(c.id)?`<button data-v5="approve" data-id="${esc(c.id)}">复核草稿更正</button>`:''}${c.lifecycle==='withdrawn'?`<button data-v5="restore" data-id="${esc(c.id)}">恢复草稿</button>`:''}<p style="font-size:11px;color:#555;">原表识别值：<strong>${esc(c.raw_fields.result||'空')}</strong><br>${Object.hasOwn(c.corrections,'result')?'人工确认值':'系统解释值（未作人工改值）'}：<strong style="color:#0d6efd;">${esc(value||'空')}</strong>${c.record?.result?.interpretation?.factor?' · 原数值 × '+esc(c.record.result.interpretation.factor):''}</p></details></div>`;}).join('');};
  const widths=Array.from({length:raw.columns},(_,i)=>i===0?130:i===1?150:i===2?120:i===3?210:160);table.style.width=(42+widths.reduce((a,b)=>a+b,0))+'px';
  let html='<colgroup><col style="width:42px">'+widths.map(w=>`<col style="width:${w}px">`).join('')+'</colgroup><thead><tr><th>原行</th>'+Array.from({length:raw.columns},(_,i)=>`<th>${this.colName(i+1)}</th>`).join('')+'</tr></thead><tbody>';
    const reviewBlocks=(this.matrix.worksheet_review?.review_blocks?.[this.sheet]||[]).filter(b=>b.collapsed||b.role==='auxiliary'||b.role==='unclassified'||b.role==='excluded');
  const blockRowRanges=[];
  for(const b of reviewBlocks){for(const ref of (b.range_refs||[])){const m=ref.match(/^[A-Za-z]+(\d+):[A-Za-z]+(\d+)$/);if(m){const r1=Math.min(Number(m[1]),Number(m[2])),r2=Math.max(Number(m[1]),Number(m[2]));const hasActive=candidates.some(c=>{const row=c.logical_row||c.location.row;return row>=r1&&row<=r2&&c.lifecycle!=='withdrawn';});if(!hasActive)blockRowRanges.push({start:r1,end:r2,block:b});}}}
  for(let r=1;r<=raw.rows;r++){
   const collapsedRange=blockRowRanges.find(rng=>rng.start===r);
   if(collapsedRange){
    const b=collapsedRange.block,count=collapsedRange.end-collapsedRange.start+1;
    const badgeCls=b.role==='auxiliary'?'v55-badge-aux':b.role==='excluded'?'v55-badge-ex':'v55-badge-unclass';
    const badgeName=b.role==='auxiliary'?'辅助区域':b.role==='excluded'?'已排除':'表尾未分类';
    html+=`<tr class="v55-collapsed-row" data-collapsed-start="${r}" data-collapsed-end="${collapsedRange.end}"><th>${r}..${collapsedRange.end}</th><td colspan="${raw.columns}" class="v55-collapsed-td"><details class="v55-collapsed-details"><summary><span class="v55-badge ${badgeCls}">${badgeName}</span><span>第 ${r} 至 ${collapsedRange.end} 行 · ${esc(b.summary||'')} (${count} 行已折叠)</span></summary><div class="v55-collapsed-body"><p style="font-size:11px;color:#666;margin:4px 0;">该区域已折叠不作为入库结果行。点击原表可展开复查。</p></div></details></td></tr>`;
    r=collapsedRange.end;continue;
   }
   const rowCandidates=candidates.filter(c=>(c.logical_row||c.location.row)===r);
   const hasDraft=rowCandidates.some(c=>c.lifecycle==='draft');
   const allWithdrawn=rowCandidates.length>0&&rowCandidates.every(c=>c.lifecycle==='withdrawn');
   if(allWithdrawn){
    html+=`<tr data-source-row="${r}" class="v55-collapsed-row v55-excluded-row"><th>${r}</th><td colspan="${raw.columns}" class="v55-collapsed-td"><details class="v55-collapsed-details"><summary><span class="v55-badge v55-badge-ex">整行已排除</span><span>第 ${r} 行 · 全部 ${rowCandidates.length} 项候选已排除不入库</span></summary><div class="v55-collapsed-body" style="display:flex;gap:6px;flex-wrap:wrap;padding:4px;">${Array.from({length:raw.columns},(_,i)=>i+1).map(col=>`<div style="padding:2px 4px;background:#fdf2f2;border:1px solid #f5c6cb;border-radius:3px;font-size:11px;">${this.colName(col)}: ${content(r,col)}</div>`).join('')}</div></details></td></tr>`;
    continue;
   }
   html+=`<tr data-source-row="${r}"><th>${r}${hasDraft?`<input type="checkbox" data-row="${r}" aria-label="全选原表第${r}行">`:''}</th>`;
   for(let col=1;col<=raw.columns;col++){const k=r+':'+col;if(covered.has(k))continue;const span=merges.get(k)||[1,1];html+=`<td data-source-cell="${r}:${col}" class="${col===1?'v52-item-column':''}" rowspan="${span[0]}" colspan="${span[1]}">${content(r,col)}</td>`;}
   html+='</tr>';
  }
  table.innerHTML=html+'</tbody>'+(candidates.length?'':`<tfoot><tr><td colspan="${raw.columns+1}">来源已保存，目前没有候选；仍须核对本表用途。</td></tr></tfoot>`);table.classList.add('v4-source-matrix');
  const headerHeight=table.tHead?.getBoundingClientRect().height||30,blocks=this.sheets.find(s=>s.section===this.sheet)?.blocks||[];
  for(const block of blocks.filter(b=>b.header_row)){let top=headerHeight;for(const row of [block.header_row,block.header_row+1]){const line=table.querySelector(`[data-source-row="${row}"]`);if(!line)continue;for(const cell of line.querySelectorAll('td,th')){cell.classList.add('v52-frozen-header');cell.style.top=top+'px';if(cell.tagName==='TH')cell.style.zIndex='7';}top+=line.getBoundingClientRect().height;}}
  const itemColumns=new Set(candidates.map(c=>c.evidence.test_item?.column).filter(Boolean)),lastItem=Math.min(2,Math.max(1,...itemColumns)),cellWidths=[...table.tHead.rows[0].cells].map(c=>c.getBoundingClientRect().width);let left=cellWidths[0]||42;table.tHead.rows[0].cells[0].style.left='0px';table.tHead.rows[0].cells[0].style.zIndex='9';
  for(let col=1;col<=lastItem;col++){table.tHead.rows[0].cells[col].style.left=left+'px';table.tHead.rows[0].cells[col].style.zIndex='8';for(const cell of table.querySelectorAll(`[data-source-cell$=":${col}"]`)){cell.classList.add('v52-item-column');cell.style.left=left+'px';}left+=cellWidths[col]||145;}
  table.querySelectorAll('[data-row]').forEach(n=>n.onchange=()=>{for(const c of candidates.filter(c=>(c.logical_row||c.location.row)===Number(n.dataset.row)&&c.lifecycle==='draft'))n.checked?this.selected.add(c.id):this.selected.delete(c.id);table.querySelectorAll('[data-select]').forEach(i=>i.checked=this.selected.has(i.dataset.select));this.selection();this.rememberSheet();});
 },
 issueDetails(c,direct){const ids=new Set(direct.flatMap(t=>t.members.filter(m=>m.candidate_id===c.id).map(m=>m.issue_id)));const issues=c.issues.filter(i=>ids.has(i.id));return issues.length?`<details class="v51-cell-issues"><summary>查看 ${issues.length} 项问题</summary>${issues.map(i=>`<div class="v5-issue" data-issue-code="${esc(i.code)}">${esc(this.issueName(i))}<p>${esc(i.source_field?i.proposal.reason:i.detail)}</p><button data-v5="issue" data-id="${esc(c.id)}" data-issue="${esc(i.id)}">处理此问题</button></div>`).join('')}</details>`:'';},
 locateTask(task){const loc=task.evidence.find(e=>e.section===this.sheet);if(!loc)return;const cell=document.querySelector(`[data-source-cell="${loc.row}:${loc.column}"]`);document.querySelectorAll('.v52-focused').forEach(n=>n.classList.remove('v52-focused'));if(cell){cell.classList.add('v52-focused');cell.querySelector('.v51-cell-issues')?.setAttribute('open','');cell.scrollIntoView({block:'center',inline:'center'});}},
 nextIssue(){this.navigateTask(1);},previousTask(){this.navigateTask(-1);},
 navigateTask(step){const list=this.tasks.filter(t=>!this.taskFilter||t.code===this.taskFilter);if(this.taskFilter==='changed'){const cs=(this.matrix.worksheet_review.changes||[]).filter(c=>c.section===this.sheet);if(!cs.length)return this.message('本表没有有效字段修改。');this.changeIndex=((this.changeIndex??-1)+step+cs.length)%cs.length;this.locateTask({evidence:[cs[this.changeIndex]]});return;}if(!list.length)return this.message('当前类别没有待决定任务。');this.taskIndex=((this.taskIndex??-1)+step+list.length)%list.length;const t=list[this.taskIndex];this.locateTask(t);if(!t.evidence.length)return this.task(t.id);this.message(`${this.taskTitle(t)} · ${t.evidence.map(e=>this.colName(e.column)+e.row).join('、')}。`);},
 async switchSheet(name){if(!this.leave()||!this.sheets.some(s=>s.section===name))return;this.rememberSheet();this.sheet=name;this.taskIndex=-1;this.taskFilter='';const url=new URL(g.location.href);url.searchParams.set('sheet',name);g.history.replaceState(null,'',url);this.render();},
 teardownWorkspace(){document.getElementById('tab-ledger')?.classList.remove('v52-review-active');const shell=document.getElementById('v52Workspace');if(shell){const controls=document.getElementById('v4LedgerSourceControls'),frame=document.getElementById('ledgerDataTable').parentElement;shell.before(controls,frame);frame.classList.remove('v52-matrix-frame');shell.remove();}if(this.chromeStash){for(const [marker,node] of this.chromeStash)marker.replaceWith(node);this.chromeStash=null;}this.viewportController?.abort();this.viewportObserver?.disconnect();if(this.viewportFrame)cancelAnimationFrame(this.viewportFrame);this.viewportFrame=null;document.getElementById('v54HorizontalScroll')?.remove();document.getElementById('v53FloatingHeader')?.remove();},
 formal(){if(!this.leave())return;this.rememberSheet();const key=this.preview?.candidates.find(c=>c.location.section===this.sheet&&c.lifecycle==='submitted'&&c.record)?.record.key;if(key)g.v45Ledger.section=g.v45Ledger.category(key.study);g.v4LedgerReview.summary=true;document.getElementById('v4LedgerInlineEditor')?.replaceChildren();this.setScene('formal');const choice=document.getElementById('v4LedgerSourceChoice');if(choice)choice.value='';const u=new URL(g.location.href);u.searchParams.delete('upload');u.searchParams.delete('sheet');g.history.replaceState(null,'',u);g.v4ResultCell.enhanceLedgerTable();},
 task(id){if(!this.leave())return;const t=this.matrix.worksheet_review.tasks.find(t=>t.id===id);if(!t)return;this.locateTask(t);const member=t.members.find(m=>m.candidate_id),c=member?this.member(member.candidate_id):null;
  if(t.code==='unresolved')return this.refresh();
  if(t.code==='receipt'){const form=this.form('接收已保存来源',`<p>${esc(t.detail)}</p>`);form.onsubmit=async e=>{e.preventDefault();await this.mutate(`/previews/${this.upload}/receive`,{candidate_ids:this.preview.candidates.filter(c=>c.lifecycle==='draft'&&!c.received).map(c=>c.id),defer_confirmation:true},'来源已接收，请继续原表核对。');};return;}
  if(t.code==='region'){if(t.actions.includes('classify'))return this.classify(t.members[0].region_id);const form=this.form('补充未投影的来源结果',`<p>${esc(t.detail)}</p><button type="button" data-v5="regionParse">指定区域和表头</button><button type="button" data-v5="manual">绑定原格补录</button>`);form.querySelector('.btn-primary').hidden=true;return;}
  if(t.code==='approval')return this.approve(member.candidate_id);
  if(t.code==='history')return this.decision(member.candidate_id);
  if(t.code==='row_exclusion'||(t.code==='source_revision'&&t.proposal.eligible)||t.proposal?.operation==='keep_single')return this.previewScope({section:this.sheet,task_ids:[t.id],operation:'suggested'},this.taskTitle(t));
  if(t.code==='identity'&&t.members.length>1)return this.sharedEdit(t,t.members.map(m=>m.candidate_id));
  if(t.code==='identity'||t.code==='collision'){
   const form=this.form(labels[t.code],`<p>${esc(t.detail)}</p>${t.members.map(m=>{const c=this.member(m.candidate_id);return `<p>${esc(c.location.section)}!${this.colName(c.location.column)}${c.location.row} · 原值 ${esc(c.raw_fields.result)} <button type="button" data-edit-task="${esc(c.id)}">补充 / 修改此来源身份</button><button type="button" data-v5="decision" data-id="${esc(c.id)}">明确本来源处置</button><button type="button" data-v5="structure" data-id="${esc(c.id)}">核对原表结构</button></p>`;}).join('')}`);form.querySelector('.btn-primary').hidden=true;form.querySelectorAll('[data-edit-task]').forEach(b=>b.onclick=()=>this.edit(b.dataset.editTask,t.fields));return;
  }
  if(t.code==='structure')return this.structure(member.candidate_id);
  if(['identity_conflict','method_conflict'].includes(t.code))return this.identityConflict(t);
  if(t.proposal?.guidance||['identity_ambiguity','numeric_format','formula_result'].includes(t.code))return this.focusedQuestion(t);
  const proposal=t.proposal||{};
  const form=this.form(t.proposal?.source_kind==='system_hint'?'系统解释提示':labels[t.code]||'来源核对',`<p>源格 ${t.evidence.map(e=>esc(e.section)+'!'+this.colName(e.column)+e.row).join('、')} · 关联 ${t.members.length} 个结果</p>${proposal.field?`<p>${esc(this.names[proposal.field]||proposal.field)}原文：${esc(proposal.before)}</p><p>建议：${esc(proposal.after||'未注明')} · ${esc(proposal.reason)}</p>`:`<p>${esc(t.detail)}</p>`}<button type="button" id="v52ThisType">预览本表同类处理</button>${t.exception?`<p role="alert">例外：${esc(t.exception)}</p>`:''}<details open><summary>请明确当前争议字段</summary><div class="v5-fields">${this.fields(t.fields,c?.corrections||{})}</div></details>`);
  document.getElementById('v52ThisType').onclick=()=>{this.cancel();this.batchTasks(id);};
  const confirm=form.querySelector('.btn-primary');const check=()=>{confirm.disabled=!t.fields.some(k=>String(form.elements[k]?.value||'').trim());};form.addEventListener('input',check);check();form.querySelector('.v5-field-hint').textContent='请先填写展开的争议字段，再预览保存。';
  form.onsubmit=async e=>{e.preventDefault();const d=this.values(form),fields=Object.fromEntries(t.fields.filter(k=>String(d[k]||'').trim()).map(k=>[k,d[k]]));if(!Object.keys(fields).length)return this.message('请明确当前争议字段，或先预览系统建议。');this.cancel();await this.previewScope({section:this.sheet,task_ids:[t.id],operation:'manual',fields},'预览本处共享来源解释');};
 },
 batchTasks(id){if(!this.leave())return;const all=this.matrix.worksheet_review.tasks.filter(t=>t.blockers.length&&t.batch_key),current=all.filter(t=>t.members.some(m=>this.member(m.candidate_id)?.location.section===this.sheet));if(!current.length)return this.message('当前表没有可批量解释的来源任务；请用下一问题补充身份或处理历史决定。');const chosen=current.find(t=>t.id===id)||current.find(t=>t.proposal.eligible&&!t.exception)||current[0];this.locateTask(chosen);
  const kinds=[...new Map(current.map(t=>[t.batch_key,t])).values()];
  const form=this.form('预览本文件同类情况 · 默认本表',`<label>处理类型<select name="kind">${kinds.map(t=>`<option value="${t.batch_key}" ${t.batch_key===chosen.batch_key?'selected':''}>${labels[t.code]} · ${this.names[t.fields[0]]||t.fields[0]||'来源'} · ${['row','row_block'].includes(t.proposal.kind)?'整行 / 明确子行不采用':t.proposal.kind==='whole'?'整格 / 整项删除':t.proposal.kind==='partial'?'保留未划线内容':'明确解释'}</option>`).join('')}</select></label><label><input name="extend" type="checkbox">扩展至本文件其他表的同类情况</label><div id="v52BatchFields"></div><div id="v5ScopePreview"></div>`);form.querySelector('.btn-primary').textContent='预览精确范围';
  const show=()=>{const matching=all.filter(t=>t.batch_key===form.elements.kind.value),sample=matching[0];const root=matching.find(t=>t.section===this.sheet);const sections=[...new Set(matching.map(t=>t.section))].filter(s=>s!==this.sheet);document.getElementById('v52BatchFields').innerHTML=`<label>本次处理<select name="operation">${sample.proposal.operation?`<option value="${sample.proposal.operation}">${sample.proposal.operation==='exclude'?'按建议不采用本行 / 全划线来源':'采用各处自己的未划线内容建议'}</option>`:''}${sample.actions.includes('keep_body')?'<option value="keep_body">核对后保留正文</option>':''}<option value="manual">人工明确共同字段</option></select></label><div class="v5-fields" id="v52ManualFields" hidden>${this.fields(sample.fields)}</div>${form.elements.extend.checked?`<div class="v52-scope-sheets">${sections.map(s=>`<label><input type="checkbox" name="sheet" value="${esc(s)}" checked>${esc(s)} · ${matching.filter(t=>t.section===s).length} 处来源 / ${new Set(matching.filter(t=>t.section===s).flatMap(t=>t.members.map(m=>m.candidate_id))).size} 个关联 / ${matching.filter(t=>t.section===s&&t.exception).length} 项例外</label>`).join('')}</div>`:''}<details class="v52-source-options"><summary>来源范围（默认保留明确建议，可调整）</summary><div class="v52-source-list">${matching.filter(t=>t.section===this.sheet||form.elements.extend.checked).map(t=>`<label><input type="checkbox" name="source" value="${esc(t.id)}" ${!t.exception?'checked':''}>${esc(t.section)}!${t.evidence.map(e=>this.colName(e.column)+e.row).join('、')} · ${t.members.length} 个关联 ${t.exception?' · 例外：'+esc(t.exception):''}</label>`).join('')}</div></details><details><summary>逐成员例外（默认保留全部明确范围）</summary><div class="v52-member-list">${matching.filter(t=>t.section===this.sheet||form.elements.extend.checked).flatMap(t=>t.members).map(m=>{const c=this.member(m.candidate_id);return `<label><input type="checkbox" name="except" value="${esc(c.id)}">不处理 ${esc(c.location.section)}!${this.colName(c.location.column)}${c.location.row}</label>`;}).join('')}</div></details>`;const manual=document.getElementById('v52ManualFields');form.elements.operation.onchange=()=>{manual.hidden=form.elements.operation.value!=='manual';};manual.hidden=form.elements.operation.value!=='manual';requestAnimationFrame(()=>{if(root)this.locateTask(root);});};show();form.elements.kind.onchange=show;form.elements.extend.onchange=show;
  let generation=0;const origin=[this.upload,this.sheet,this.serial].join('|');
  form.addEventListener('input',()=>{generation++;document.getElementById('v5ApplyScope')?.setAttribute('disabled','');});form.addEventListener('change',()=>{generation++;document.getElementById('v5ApplyScope')?.setAttribute('disabled','');});
  form.onsubmit=async e=>{e.preventDefault();if(form.dataset.loading)return;form.dataset.loading='1';const request=++generation;const submit=form.querySelector('.btn-primary');submit.disabled=true;this.localMessage('正在准备明确范围的预览…');try{const sample=all.find(t=>t.batch_key===form.elements.kind.value),sections=[this.sheet,...(form.elements.extend.checked?[...form.querySelectorAll('[name="sheet"]:checked')].map(n=>n.value):[])];const scope={section:this.sheet,include_sections:sections.slice(1),task_ids:[...form.querySelectorAll('[name="source"]:checked')].map(n=>n.value).filter(id=>sections.includes(all.find(t=>t.id===id).section)),operation:form.elements.operation.value,fields:form.elements.operation.value==='manual'?Object.fromEntries(sample.fields.filter(k=>String(form.elements[k]?.value||'').trim()).map(k=>[k,form.elements[k].value])):{},exclude_candidate_ids:[...form.querySelectorAll('[name="except"]:checked')].map(n=>n.value)};const info=await api(this.root()+'/tasks/preview',scope),s=info.summary;if(!form.isConnected||request!==generation||origin!==[this.upload,this.sheet,this.serial].join('|'))return;this.localMessage('预览已准备，请核对各处自己的值与例外。');document.getElementById('v5ScopePreview').innerHTML=this.scopeMarkup(info)+`<p>${esc(info.notice)}</p><button type="button" class="btn btn-primary" id="v5ApplyScope" ${s.tasks?'':'disabled'}>确认此范围并保存草稿</button>`;document.getElementById('v5ApplyScope').onclick=async()=>{if(request!==generation)return;this.rememberSheet();try{const result=await this.mutate(this.root()+'/tasks/apply',{scope,fingerprint:info.fingerprint,reason:form.elements.reason.value||'核对各处建议、范围与例外后保存本文件草稿'},'草稿处理已保存');if(result&&result.display_refreshed!==false)this.message(`本次已处理 ${s.sources} 处来源；仍有 ${result.remaining_tasks} 项待决定。其他表没有自动入库。`);}catch{}};}catch(e){if(form.isConnected)this.localMessage('预览失败：'+e.message);}finally{delete form.dataset.loading;if(form.isConnected)submit.disabled=false;}};
 },
 issue(id,iid){const t=this.matrix?.worksheet_review.tasks.find(t=>t.members.some(m=>m.candidate_id===id&&m.issue_id===iid));if(t&&(t.proposal?.guidance||['identity_conflict','method_conflict','identity_ambiguity','numeric_format','formula_result'].includes(t.code)))return this.task(t.id);return originalIssue.call(this,id,iid);},
 reviewContext(t){
  const members=t.members.map(m=>this.member(m.candidate_id)).filter(Boolean),first=members[0];if(!first)return '';
  const current=(c,k)=>c.corrections[k]??c.interpreted_fields[k]??c.raw_fields[k]??'';
  const address=loc=>loc.section+'!'+this.colName(loc.column)+loc.row;
  const source=t.evidence.map(e=>{const cell=this.matrix.sheets.find(s=>s.name===e.section)?.cells.find(c=>c.row===e.row&&c.column===e.column);return `<p><button type="button" class="btn btn-outline" data-review-locate="${e.row}:${e.column}">定位 ${esc(address(e))}</button> <span>${esc(cell?.text||'原表未填写')}</span></p>`;}).join('');
  const results=members.map(c=>{const range=c.result_anchor?.range,ref=range?this.colName(range[1])+range[0]+':'+this.colName(range[3])+range[2]:this.colName(c.location.column)+c.location.row;return `<li><button type="button" class="btn btn-outline" data-review-locate="${c.location.row}:${c.location.column}">定位 ${esc(ref)}</button> · 原表第 ${c.logical_row||c.location.row} 行 · ${esc(current(c,'time_point')||'时间点未注明')}<div class="v020-source-text">原始结果：${esc(c.raw_fields.result||'（空）')}</div>${Object.hasOwn(c.corrections,'result')?`<div class="v020-source-text">当前草稿结果：${esc(c.corrections.result||'（空）')}</div>`:''}</li>`;}).join('');
  return `<div class="v020-review-context"><p><strong>${esc(current(first,'batch')||'批号未注明')}</strong> · ${esc(current(first,'condition')||'条件未注明')} · ${members.length} 个关联结果</p><p>原检测项：${esc(first.raw_fields.test_item)}<br>当前方法：${esc(current(first,'method')||'原表未注明')}</p><details><summary>查看有疑问的标签原文与定位</summary>${source}</details><details ${members.length<=2?'open':''}><summary>查看结果原文、时间点及定位（${members.length} 条）</summary><ul class="v020-result-context">${results}</ul></details></div>`;
 },
 prepareFocusedForm(form){
  form.classList.add('v020-focused-question');const body=document.createElement('div');body.className='v020-question-body';const reason=form.elements.reason.closest('.v5-fields');let node=form.firstElementChild.nextElementSibling;while(node&&node!==reason){const next=node.nextElementSibling;body.append(node);node=next;}reason.before(body);
 },
 bindReviewLocations(form){form.querySelectorAll('[data-review-locate]').forEach(b=>b.onclick=()=>{const [row,column]=b.dataset.reviewLocate.split(':').map(Number);this.locateTask({evidence:[{section:this.sheet,row,column}]});});},
 focusedQuestion(t,initial={},reason=null){
  const guide=t.proposal.guidance||{},member=this.member(t.members[0].candidate_id),options=guide.name_options||[],naming=guide.required_fields?.includes('test_item');
  const title=guide.title||({numeric_format:'明确结果的数值与单位',formula_result:'填写核实后的公式结果',identity_ambiguity:'明确本处规格或方法'})[t.code];
  const question=guide.question||t.detail;
  const values={...(naming?{}:Object.fromEntries(t.fields.map(k=>[k,member.corrections[k]??member.interpreted_fields[k]??'']))),...initial};
  const choices=options.map((o,i)=>`<button type="button" class="btn btn-outline" data-name-option="${i}">采用同一行名称：${esc(o.value)}</button>`).join('');
  const evidence=options.flatMap(o=>o.evidence).map(e=>`<li>${esc(e.location.section)}!${this.colName(e.location.column)}${e.location.row} · ${esc(e.time_point)}：${esc(e.result)}</li>`).join('');
  const form=this.form(title,`<p class="v020-question">${esc(question)}</p>${this.reviewContext(t)}${naming?`<p>${esc(guide.suggestion_notice||'')}</p><div class="v020-name-options">${choices}</div>${evidence?`<details><summary>候选名称的原文依据</summary><ul>${evidence}</ul></details>`:''}`:''}<div class="v5-fields">${this.fields(t.fields,values)}</div><p class="v020-question-help">${esc(guide.help||'请核实以上字段；下一步将展示修改前后及明确影响范围。')}</p><p>${t.fields.includes('result')?'本次只处理列出的字段。':'本次只明确名称或身份，各结果原文、单位和 AR 等引用保留。'}</p><p class="v020-question-status" role="status"></p>`,naming?'对照同一原表行明确检测分项，保留各时间点结果':'对照原表明确本处字段解释');
  this.prepareFocusedForm(form);this.bindReviewLocations(form);
  if(reason!==null)form.elements.reason.value=reason;
  if(naming){form.elements.test_item.placeholder='填写完整检测项及分项名称';form.elements.test_item.closest('label').querySelector('.v5-field-hint').textContent='可点选有原文依据的候选名称，也可直接填写。';}
  const submit=form.querySelector('.btn-primary');submit.textContent='预览修改与影响范围';
  const check=()=>{const required=guide.required_fields||t.fields.filter(k=>k!=='unit');submit.disabled=required.some(k=>!String(form.elements[k]?.value||'').trim());form.querySelector('.v020-question-status').textContent=submit.disabled?'请点选候选名称或填写本次需要明确的字段。':'下一步核对拟保存内容；此时尚未改动草稿。';};
  form.querySelectorAll('[data-name-option]').forEach(b=>b.onclick=()=>{form.elements.test_item.value=options[Number(b.dataset.nameOption)].value;this.dirty=true;check();});form.addEventListener('input',check);check();
  form.onsubmit=e=>{e.preventDefault();if(submit.disabled)return;const fields=Object.fromEntries(t.fields.map(k=>[k,form.elements[k].value.trim()])),reason=form.elements.reason.value;this.cancel();return this.previewScope({section:this.sheet,task_ids:[t.id],operation:'manual',fields},'预览'+title,false,reason);};
 },
 identityConflict(t,initial='',reason=null){
  const field=t.fields[0],method=field==='method',kind=method?'方法':'子结果身份';
  const options=t.proposal.identity_options||{},members=t.members.map(m=>this.member(m.candidate_id));
  const rows=members.map(c=>`<li>${this.colName(c.location.column)}${c.location.row} · 时间点 ${esc(c.interpreted_fields.time_point||c.raw_fields.time_point)} · 保留各自结果 ${esc(c.corrections.result??c.interpreted_fields.result??c.raw_fields.result)}</li>`).join('');
  const quickBtns=Object.entries(options).map(([k,v])=>`<button type="button" class="btn btn-sm btn-outline" data-quick-identity="${esc(k)}" style="margin-right:6px;">采用${k==='chinese'?'中文':'英文'}含义：<strong>${esc(v)}</strong></button>`).join('');
  const form=this.form('明确中英文'+kind,`<p class="v020-question">同一标签的中英文含义不同，请选择本行实际采用的${esc(kind)}。</p>${this.reviewContext(t)}<details><summary>查看冲突说明</summary><p>${esc(t.detail)}</p></details>${quickBtns?`<div style="margin:8px 0;"><span>快捷点选（不预选答案）：</span>${quickBtns}</div>`:''}<div class="v5-fields" style="grid-template-columns:1fr"><label>解释依据<select name="identityChoice" required><option value="">请选择，不预选业务答案</option><option value="chinese">按中文含义解释</option><option value="english">按英文含义解释</option><option value="custom">分别指定当前行身份</option></select></label><label>本次范围拟采用的${esc(kind)}<input name="identityValue" required readonly value=""></label></div><p>本次只明确${esc(kind)}，影响 ${members.length} 个结果；各时间点仍保留各自原值。</p><details><summary>查看当前解释值（${members.length} 处）</summary><ul>${rows}</ul></details><p class="v55-identity-reason" role="status">尚未选择${esc(kind)}，不能确认。</p>`,'核对中英文原标签后明确'+kind);
  this.prepareFocusedForm(form);this.bindReviewLocations(form);
  if(reason!==null)form.elements.reason.value=reason;
  const submit=form.querySelector('.btn-primary'),choice=form.elements.identityChoice,input=form.elements.identityValue;submit.textContent='预览'+kind+'与受影响时间点';submit.disabled=true;
  const check=()=>{submit.disabled=!choice.value||!input.value.trim();form.querySelector('.v55-identity-reason').textContent=submit.disabled?'尚未完成'+kind+'解释，不能确认。':'下一步预览'+kind+'及受影响时间点。';};
  choice.onchange=()=>{input.readOnly=choice.value!=='custom';input.value=options[choice.value]||'';this.dirty=true;check();};input.oninput=check;
  form.querySelectorAll('[data-quick-identity]').forEach(btn=>{btn.onclick=()=>{choice.value=btn.dataset.quickIdentity;choice.onchange();};});
  if(initial){choice.value='custom';input.readOnly=false;input.value=initial;check();}
  form.onsubmit=e=>{e.preventDefault();if(submit.disabled)return;const fields={[field]:input.value.trim()},reason=form.elements.reason.value;this.cancel();return this.previewScope({section:this.sheet,task_ids:[t.id],operation:'manual',fields},'预览明确'+kind,false,reason);};
 },
 async sourceStructure(){if(!this.leave())return;try{const info=await api(this.root()+'/source-structure');const form=this.form('核对冻结原件的来源用途',`<p>${esc(info.version)} · ${info.changes.length} 处明确非结果区域</p><div class="v52-source-list">${info.changes.map(c=>`<p>${esc(c.section)}!${esc(c.coordinates.join('、'))} · ${Object.entries(c.cell_roles).map(([k,v])=>esc(k)+': '+esc({identity:'表头信息',standard:'标准说明',note:'模板/备注',auxiliary:'计划日期/辅助来源'}[v]||v)).join('；')}</p>`).join('')}</div><p>${esc(info.notice)}</p>`);form.querySelector('.btn-primary').disabled=!info.changes.length;form.onsubmit=async e=>{e.preventDefault();await this.mutate(this.root()+'/source-structure',{fingerprint:info.fingerprint,reason:form.elements.reason.value||'核对非结果来源用途证据及范围'},'来源用途解释版本已保存；剩余争议已重新核对。');};}catch(e){this.message(e.message);}},
 async undoBatch(id){if(!this.leave())return;await this.mutate(this.root()+'/tasks/undo',{batch_id:id,reason:'撤销本次明确草稿批次，重新核对受影响解释'},'草稿批次已撤销；争议及资格已重新计算。');},
 renderActionBar(){
  const bar=document.getElementById('v55ActionBar');if(!bar)return;
  const groups=this.matrix.worksheet_review?.action_groups?.[this.sheet]||[];
  const coveredTaskIds=new Set(groups.flatMap(g=>g.scope?.task_ids||[]));
  const otherTasks=this.tasks.filter(t=>t.section===this.sheet&&t.proposal?.eligible&&!t.exception&&!coveredTaskIds.has(t.id));
  if(!groups.length&&!otherTasks.length){bar.innerHTML='<span class="v55-action-bar-title">当前工作表全部动作组已处理</span>';return;}
  let html='<span class="v55-action-bar-title">快捷动作：</span>';
  for(const ag of groups){html+=`<button type="button" class="btn btn-sm btn-outline v55-action-pill" data-v55-action="${esc(ag.group_id)}" title="${esc(ag.reason)}">${esc(ag.title)}</button>`;}
  if(otherTasks.length){html+=`<span class="v55-other-tasks">其他零星任务 ${otherTasks.length} 处</span>`;}
  bar.innerHTML=html;
  bar.querySelectorAll('[data-v55-action]').forEach(btn=>{btn.onclick=()=>{const ag=groups.find(g=>g.group_id===btn.dataset.v55Action);if(ag)this.openActionTray(ag);};});
 },
 async openActionTray(ag){
  if(!this.leave())return;
  const requestKey=JSON.stringify([this.upload,ag.scope]);if(this.preparingScope?.key===requestKey&&this.preparingScope.context===this.previewContext())return;this.cancel();
  this.captureSourceView();const tray=document.getElementById('v55ActionTray');if(!tray)return;
  this.previewSerial=(this.previewSerial||0)+1;const context=this.previewContext(),pending={key:requestKey,context};this.preparingScope=pending;
  tray.style.display='flex';tray.innerHTML=`<div class="v55-action-preview-card"><p role="status">${esc(ag.title)} · 正在准备预览…</p><button type="button" id="v55CancelTray">取消</button></div>`;
  const close=()=>{if(this.busy)return;++this.previewSerial;tray.style.display='none';tray.replaceChildren();};
  tray.querySelector('#v55CancelTray').onclick=close;
  try{
   const info=this.previewValid(await api(this.root()+'/tasks/preview',ag.scope));
   if(context!==this.previewContext()||!tray.isConnected)return;
   tray.innerHTML=`<div class="v55-action-preview-card"><div class="v55-action-preview-header"><strong>${esc(ag.title)} · 修订 ${info.base_revision}</strong></div><div class="v55-action-preview-body">${this.scopeMarkup(info)}</div><p class="v55-local-status" role="status" aria-live="polite"></p><div class="v55-action-preview-footer"><button type="button" class="btn btn-outline btn-sm" id="v55CancelTray">取消</button><button type="button" class="btn btn-primary btn-sm" id="v55ApplyTray" ${info.summary.tasks?'':'disabled'}>确认并保存此动作组</button></div></div>`;
   tray.querySelector('#v55CancelTray').onclick=close;
   tray.querySelector('#v55ApplyTray').onclick=async()=>{
    if(context!==this.previewContext())return;
    try{const res=await this.mutate(this.root()+'/tasks/apply',{scope:ag.scope,fingerprint:info.fingerprint,reason:ag.reason||'核对动作组范围与各处自己的值'},'动作组草稿已保存');
     if(res&&res.display_refreshed!==false)this.message(`已保存 ${info.summary.sources} 处来源决定；仍有 ${res.remaining_tasks} 项独立问题待决定。`);
    }catch{}
   };
  }catch(err){
   if(context!==this.previewContext()||!tray.isConnected)return;
   tray.innerHTML=`<div class="v55-action-preview-card"><p role="alert">预览失败：${esc(err.message)}</p><button type="button" id="v55RetryTray">重试预览</button><button type="button" id="v55CancelTray">关闭</button></div>`;
   tray.querySelector('#v55RetryTray').onclick=()=>this.openActionTray(ag);tray.querySelector('#v55CancelTray').onclick=close;
  }finally{if(this.preparingScope===pending)this.preparingScope=null;}
 },

});
})(window);
