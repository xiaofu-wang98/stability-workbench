/* Inline review inside the existing ledger. Source receipt is not adoption. */
(function(global){
'use strict';
const esc = value => String(value ?? '').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const busy=(text)=>global.v46Busy?.show(text);
const request = (path,body,method='POST') => global.v4Api.sourceRequest(path,{method,headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});
const key = () => 'ledger-'+crypto.randomUUID();
const review = global.v4LedgerReview = {
 summary:true, project:null, matrix:null, selected:new Set(), serial:0, openSerial:0,
 async attach(project){
  const serial=++this.serial; this.project=project;
  let controls=document.getElementById('v4LedgerSourceControls');
  if(!controls){controls=document.createElement('div');controls.id='v4LedgerSourceControls';document.getElementById('ledgerDataTable')?.parentElement.before(controls);}
  if(controls.dataset.project!==project)controls.replaceChildren();
  try{
   const uploads=await global.v4Api.sourceRequest(`/projects/${encodeURIComponent(project)}/ledger-sources`);
   if(serial!==this.serial||(global.v4ResultCell?.activeLoadingProject&&global.v4ResultCell.activeLoadingProject!==project))return;
   const signature=JSON.stringify([project,this.summary,uploads.map(u=>[u.id,u.filename])]);
   if(this.summary&&controls.dataset.signature===signature&&controls.querySelector('#v4LedgerSourceChoice'))return;
   const keepOpen=controls.dataset.project===project&&controls.querySelector('.v013-source-entry')?.open;
   controls.innerHTML=uploads.length ? `<details class="v013-source-entry" ${this.summary&&!keepOpen?'':'open'}><summary>核对来源文件</summary>${this.summary?'':'<button type="button" class="btn btn-outline" id="v013BackToResults">返回已入库结果</button>'}<label>来源文件 <select id="v4LedgerSourceChoice"><option value="">返回已入库结果</option>${uploads.map(u=>`<option value="${esc(u.id)}">文件核对：${esc(u.filename)}</option>`).join('')}</select></label></details><div id="v4LedgerInlineEditor"></div>` : '';
   controls.dataset.project=project;controls.dataset.signature=signature;
   const back=document.getElementById('v013BackToResults');if(back)back.onclick=()=>global.v5Review?.formal();
   const select=document.getElementById('v4LedgerSourceChoice');
   if(select){select.onchange=()=>{if(global.v5Review)return global.v5Review.chooseSource(select.value);if(select.value){this.summary=false;busy('正在读取来源并生成当前分表…');this.open(select.value).finally(()=>global.v46Busy?.hide());}else{this.summary=true;global.v4ResultCell.enhanceLedgerTable();}};if(this.summary){select.value='';return;}select.value=uploads.some(u=>u.id===this.upload)?this.upload:uploads[0].id;await this.open(select.value);}
  }catch(error){controls.textContent='来源台账加载失败：'+error.message;}
 },
 async open(id){
  const project=this.project,load=++this.openSerial;this.upload=id;this.selected.clear();
  try{const matrix=await global.v4Api.sourceRequest(`/previews/${id}/ledger-matrix`);if(project!==this.project||id!==this.upload||load!==this.openSerial)return;this.matrix=matrix;await global.v46Busy?.frame();this.render();}
  catch(error){document.getElementById('v4LedgerInlineEditor').textContent=error.message;}
 },
 render(){
  const {preview,sheets,candidate_ids,adopted,states={}}=this.matrix;
  const ids=new Set(candidate_ids);this.candidates=preview.candidates.filter(c=>ids.has(c.id));
  const scoped=this.candidates;
  for(const id of ['ledgerSamplePills','ledgerConditionPills']){const node=document.getElementById(id)?.closest('.toolbar-row');if(node)node.hidden=true;}
  const banner=document.querySelector('#ledgerBannerGrid .v3-label');if(banner)banner.textContent='文件核对 · 按原始 Excel 工作表逐表入库';
  const scopeNote=document.querySelector('#ledgerBannerGrid p');if(scopeNote)scopeNote.textContent='原始工作表保留加速、长期和批次的边界；完成后可切换正式结果汇总。';
  const choices=[...new Set(scoped.map(c=>c.location.section))];
  const problems=list=>list.filter(c=>c.lifecycle==='draft'&&['needs_review','different_value'].includes(c.status));
  const count=section=>problems(scoped.filter(c=>c.location.section===section)).length;
  this.sheet=choices.includes(this.sheet)?this.sheet:choices[0];
  const sheetConfirmed=this.matrix.confirmed_sheets?.includes(this.sheet);
  const sheetDrafts=scoped.filter(c=>c.location.section===this.sheet&&c.lifecycle==='draft'&&!['empty','same_value'].includes(c.status));
  const official=scoped.filter(c=>c.location.section===this.sheet&&c.lifecycle==='submitted'&&states[c.id]==='confirmed').length;
  const editor=document.getElementById('v4LedgerInlineEditor');
  editor.innerHTML=`<label>工作表 <select id="v4LedgerSheet">${choices.map(s=>`<option value="${esc(s)}" ${s===this.sheet?'selected':''}>${esc(s)} · 争议${count(s)}格</option>`).join('')}</select></label> <button id="v4ReviewSelected" class="btn btn-outline">修改选中来源行 / 关联名称</button><button class="btn btn-outline" id="v46PrevIssue">上一个争议</button> <button class="btn btn-outline" id="v46NextIssue">下一个争议</button><strong> 本表争议 ${count(this.sheet)} 格</strong><button class="btn btn-outline" id="v46ResolveSheet">确认本表删改建议</button><button class="btn btn-primary" id="v46ConfirmSheet" ${sheetConfirmed&&!sheetDrafts.length?'disabled':''}>${sheetConfirmed&&!sheetDrafts.length?'本表已确认':'确认整表入库'}</button><p>本表正式确认 ${official} 项 · 尚待处理或确认 ${sheetDrafts.length} 项${sheetConfirmed?' · 已完成整表确认':' · 核对完成后按工作表确认一次'}</p>${this.matrix.legacy_revision_ids?.length?`<p role="status" style="color:#8b3b3b">此旧上传有${this.matrix.legacy_revision_ids.length}个已采用结果涉及删除线，旧解析未记录其修订语义。请核对历史正式值；本次升级不会自动改写它们。</p>`:''}<p id="v4LedgerReviewMessage" role="status"></p><div id="v4LedgerEditFields"></div>`;
  document.getElementById('v4LedgerSheet').onchange=e=>{this.sheet=e.target.value;this.selected.clear();this.render();};
  document.getElementById('v4ReviewSelected').onclick=()=>this.edit();
  const progress=this.matrix.sheet_progress||[];const navFile=document.createElement('div');navFile.className='v4-file-progress';
  navFile.innerHTML=`<strong>工作表进度：已完成 ${progress.filter(s=>s.state==='confirmed').length} / ${progress.length} · 未完成 ${progress.filter(s=>s.state!=='confirmed').length}</strong><div class="v4-sheet-list">${progress.map((s,i)=>`<button type="button" class="btn ${s.name===this.sheet?'btn-primary':'btn-outline'}" data-source-sheet="${esc(s.name)}">${i+1}. ${esc(s.name)}<br><small>${({confirmed:'已入库',ready:'可入库',needs_review:'待核对',not_received:'待接收'})[s.state]} · 争议 ${s.issues} 项</small></button>`).join('')}</div>`;editor.prepend(navFile);
  navFile.querySelectorAll('[data-source-sheet]').forEach(b=>b.onclick=()=>{this.sheet=b.dataset.sourceSheet;this.block=null;this.selected.clear();this.render();});
  const sheet=sheets.find(s=>s.name===this.sheet), table=document.getElementById('ledgerDataTable');
  const candidates=preview.candidates.filter(c=>c.location.section===this.sheet);
  const visibleIds=new Set(scoped.map(c=>c.id));
  const blocks=[...new Set(candidates.map(c=>c.evidence?.batch?.row||1))].sort((a,b)=>a-b);
  if(!blocks.includes(this.block))this.block=blocks[0]||1;
  const next=blocks.find(r=>r>this.block);const end=next?next-1:sheet?.rows;
  const nav=document.createElement('label');nav.innerHTML=`本工作表内批次 <select id="v46Block">${blocks.map(row=>{const c=candidates.find(c=>(c.evidence?.batch?.row||1)===row);const f=c?.record?.key||c?.interpreted_fields||{};return `<option value="${row}" ${row===this.block?'selected':''}>${esc(f.batch||'来源分表')} · ${esc(f.condition||'')} · 争议${problems(candidates.filter(c=>(c.evidence?.batch?.row||1)===row)).length}项</option>`;}).join('')}</select>`;navFile.after(nav);
  const notes=document.createElement('div');notes.className='v4-source-notes';const noteList=sheet?.notes||[];notes.innerHTML=noteList.length?`<strong>原表重要备注（请先阅读）</strong>${noteList.map(n=>`<p><small>R${n.row}C${n.column}</small> ${esc(n.text)}</p>`).join('')}`:'';nav.after(notes);
  const actions=document.createElement('div');actions.className='v4-review-actions';actions.innerHTML='<strong id="v4SelectedCount">已选择 0 项</strong> <button class="btn btn-primary" id="v4PassSelected">核对通过</button> <button class="btn btn-outline" id="v4ExcludeSelected">排除所选</button> <button class="btn btn-outline" id="v4SameRows">选中本文件同类行（预览范围）</button> <button class="btn btn-outline" id="v4ClearSelection">清空选择</button><p id="v4SelectionScope">可勾选最左侧整行，选择该行全部时间点。核对通过后显示“已核对，待整表入库”。</p>';notes.after(actions);actions.insertBefore(document.getElementById('v4ReviewSelected'),document.getElementById('v4SameRows'));document.getElementById('v4LedgerSheet').closest('label').hidden=true;
  document.getElementById('v4PassSelected').onclick=()=>this.decide('review');document.getElementById('v4ExcludeSelected').onclick=()=>this.decide('exclude');document.getElementById('v4SameRows').onclick=()=>this.selectSameRows();document.getElementById('v4ClearSelection').onclick=()=>{this.selected.clear();this.selectionChanged();};
  document.getElementById('v46Block').onchange=e=>{this.block=Number(e.target.value);this.selected.clear();this.render();};
  document.getElementById('v46PrevIssue').onclick=()=>this.jumpIssue(-1);
  document.getElementById('v46NextIssue').onclick=()=>this.jumpIssue(1);
  document.getElementById('v46ResolveSheet').onclick=()=>this.resolveSheet();
  document.getElementById('v46ConfirmSheet').onclick=()=>this.confirmSheet();

  const overlays=new Map();for(const c of candidates){const k=c.location.row+':'+c.location.column;if(!overlays.has(k))overlays.set(k,[]);overlays.get(k).push(c);}
  const cellContents=(r,col,text)=>{const list=overlays.get(r+':'+col)||[];const shared=list.length>1&&list.every(c=>c.source_span);return list.length?(shared?`<details><summary>共享原文 · 覆盖第${list[0].source_span[0]}–${list[0].source_span[2]}行</summary><span style="white-space:pre-wrap">${esc(text)}</span></details>`:'')+list.map(c=>{
   const included=ids.has(c.id);const inScope=visibleIds.has(c.id);const selectable=inScope&&included&&c.lifecycle==='draft'&&!['empty','same_value'].includes(c.status);const pending=selectable&&c.status!=='new';const awaiting=states[c.id]==='pending';const value=c.corrections.result ?? c.raw_fields.result;
   return `<div ${c.lifecycle==='withdrawn'?'style="background:#f1f3f5;color:#687078;padding:6px"':pending||awaiting?'style="background:#fff1bd;border:2px solid #ad7400;padding:6px"':''}>${list.length>1?`<strong>原表第${c.logical_row||c.location.row}行 · ${esc(c.interpreted_fields.test_item)}</strong><br>`:''}${c.record?.result?.status==='not_reported'?'<strong>不进行汇报结果</strong><br>':''}<label>${selectable?`<input type="checkbox" data-review-id="${esc(c.id)}" ${this.selected.has(c.id)?'checked':''}>`:''}<span style="white-space:pre-wrap">${shared?esc('不汇报 · '+(c.record?.result?.report_references||[]).join('、')):c.revisions?.result&&!c.corrections.result?this.rich(c.revisions.result):esc(value)}</span></label>${c.corrections.test_item?`<small>核对名称：${esc(global.v45Ledger?.labels[c.corrections.test_item]||c.corrections.test_item)}</small><br>`:''}${selectable?`<strong>${pending?'（待核对）':c.reviewed?'（已核对，待整表入库）':'（待整表确认）'}</strong><br><button class="btn btn-outline btn-sm" data-edit-id="${esc(c.id)}">在此修改</button>`:c.lifecycle==='submitted'&&states[c.id]==='confirmed'?'<small> · 已确认</small>':!included?'<small> · 未接收</small>':c.lifecycle==='withdrawn'?'<strong> · 已排除，不参与入库</strong>':awaiting?'<strong>（待高级确认）</strong>':c.status==='same_value'&&c.lifecycle==='draft'?'<small> · 正式值已存在，同值来源无需重复确认</small>':c.status==='empty'?'<small> · 不更新结果</small>':'<small> · 已确认</small>'}${c.comment&&!shared?`<details><summary>原文说明</summary><span style="white-space:pre-wrap">${esc(c.comment)}</span></details>`:''}${adopted[c.id]!=null&&(pending||awaiting)?`<p>已有正式值：${esc(adopted[c.id])}</p>`:''}</div>`;
  }).join(''):`<span style="white-space:pre-wrap">${esc(text)}</span>`;};
  let html='';
  if(sheet){
   const cells=new Map(sheet.cells.map(c=>[c.row+':'+c.column,c]));const covered=new Set(),merges=new Map();
   for(const [r,c,rr,cc] of sheet.merges){merges.set(r+':'+c,[rr-r+1,cc-c+1]);for(let y=r;y<=rr;y++)for(let x=c;x<=cc;x++)if(y!==r||x!==c)covered.add(y+':'+x);}
   html='<tbody>';for(let r=this.block;r<=end;r++){const rowIds=candidates.filter(c=>(c.logical_row||c.location.row)===r&&ids.has(c.id)&&c.lifecycle==='draft'&&!['empty','same_value'].includes(c.status)).map(c=>c.id);html+=`<tr><td style="min-width:66px;vertical-align:top">${rowIds.length?`<label><input type="checkbox" data-source-row="${r}"> 行${r}</label>`:r}</td>`;for(let c=1;c<=sheet.columns;c++){const k=r+':'+c;if(covered.has(k))continue;const span=merges.get(k)||[1,1];html+=`<td rowspan="${span[0]}" colspan="${span[1]}" style="min-width:110px;vertical-align:top">${overlays.has(k)?cellContents(r,c,cells.get(k)?.text||''):cells.get(k)?.revision?this.rich(cells.get(k).revision):cellContents(r,c,cells.get(k)?.text||'')}</td>`;}html+='</tr>';}html+='</tbody>';
  }else html='<tbody>'+candidates.map(c=>`<tr><td>${esc(c.interpreted_fields.test_item)}</td><td>${esc(c.interpreted_fields.time_point)}</td><td>${cellContents(c.location.row,c.location.column,c.raw_fields.result)}</td></tr>`).join('')+'</tbody>';
  if(!choices.length)html='<tbody><tr><td>本分区没有此来源文件的数据。请选择正式结果汇总或其他来源文件。</td></tr></tbody>';
  table.classList.add('v4-source-matrix');table.innerHTML=html;
  table.querySelectorAll('[data-review-id]').forEach(input=>input.onchange=()=>{input.checked?this.selected.add(input.dataset.reviewId):this.selected.delete(input.dataset.reviewId);this.selectionChanged();});
  table.querySelectorAll('[data-source-row]').forEach(input=>input.onchange=()=>{for(const c of candidates.filter(c=>(c.logical_row||c.location.row)===Number(input.dataset.sourceRow)&&ids.has(c.id)&&c.lifecycle==='draft'&&!['empty','same_value'].includes(c.status)))input.checked?this.selected.add(c.id):this.selected.delete(c.id);this.selectionChanged();});
  table.querySelectorAll('[data-edit-id]').forEach(button=>button.onclick=()=>{this.selected=new Set([button.dataset.editId]);this.edit();});
  this.selectionChanged();
 },
 selectionChanged(){
  const count=document.getElementById('v4SelectedCount');if(count)count.textContent=`已选择 ${this.selected.size} 项`;
  document.querySelectorAll('[data-review-id]').forEach(n=>n.checked=this.selected.has(n.dataset.reviewId));
  document.querySelectorAll('[data-source-row]').forEach(n=>{const list=this.candidates.filter(c=>c.location.section===this.sheet&&(c.logical_row||c.location.row)===Number(n.dataset.sourceRow)&&c.lifecycle==='draft'&&!['empty','same_value'].includes(c.status));n.checked=list.length>0&&list.every(c=>this.selected.has(c.id));n.indeterminate=!n.checked&&list.some(c=>this.selected.has(c.id));});
 },
 selectSameRows(){
  const signatures=new Set(this.candidates.filter(c=>this.selected.has(c.id)&&c.row_signature).map(c=>c.row_signature));if(!signatures.size)return this.message('请先勾选一行或一个结果。');
  const matches=this.candidates.filter(c=>signatures.has(c.row_signature)&&c.lifecycle==='draft'&&!['empty','same_value'].includes(c.status));this.selected=new Set(matches.map(c=>c.id));this.selectionChanged();
  const scope=[...new Set(matches.map(c=>c.location.section))].map(s=>`${s}：${matches.filter(c=>c.location.section===s).length}项`).join('；');document.getElementById('v4SelectionScope').textContent=`尚未修改。范围预览：${scope}。仅本文件中名称、方法、标准及行位置一致的行；请核实范围后点击核对通过或排除所选。`;
 },
 async decide(action){
  const ids=this.candidates.filter(c=>this.selected.has(c.id)&&c.lifecycle==='draft').map(c=>c.id);if(!ids.length)return this.message('请先勾选来源行或结果。');if(this.mutating)return;this.mutating=true;
  try{busy(action==='exclude'?'正在保存排除决定…':'正在保存核对决定…');await request(`/previews/${this.upload}/ledger-decision`,{base_revision:this.matrix.preview.base_revision,idempotency_key:key(),candidate_ids:ids,action});await this.open(this.upload);const remaining=this.candidates.filter(c=>ids.includes(c.id)&&c.lifecycle==='draft'&&['needs_review','different_value'].includes(c.status));this.message(action==='exclude'?`已排除${ids.length}项，已保存，不参与入库。`:remaining.length?`已保存核对，其中${remaining.length}项仍需明确：${remaining[0].detail}`:`已核对${ids.length}项，黄色已解除，待整表入库。`,true);}catch(e){this.message('本次未执行：'+e.message,true);}finally{this.mutating=false;global.v46Busy?.hide();}
 },
 rich(revision){return '<span style="white-space:pre-wrap;overflow-wrap:anywhere">'+revision.segments.map(p=>p.struck?`<del>${esc(p.text)}</del>`:esc(p.text)).join('')+'</span>';},
 jumpIssue(direction){
  const list=this.candidates.filter(c=>c.location.section===this.sheet&&c.lifecycle==='draft'&&['needs_review','different_value'].includes(c.status));
  if(!list.length)return this.message('本表没有未解决争议，可确认整表。');
  this.issueIndex=((this.issueIndex??(direction===1?-1:0))+direction+list.length)%list.length;const c=list[this.issueIndex];this.block=c.evidence?.batch?.row||1;this.render();
  const input=document.querySelector(`[data-review-id="${c.id}"]`);input?.closest('td')?.scrollIntoView({block:'center',behavior:'smooth'});if(input){input.closest('td').style.outline='3px solid #087baa';}this.message(`第${this.issueIndex+1}/${list.length}个争议：${c.issue_code==='deleted_source'?'整项删除，请确认排除':c.detail}`);
 },
 async resolveSheet(){
  const ids=this.candidates.filter(c=>c.location.section===this.sheet&&c.lifecycle==='draft'&&['source_revision','deleted_source'].includes(c.issue_code)).map(c=>c.id);
  if(!ids.length)return this.message('本表没有需要确认的删除线修订。');
  try{busy('正在确认未划线内容及删除项…');await request(`/previews/${this.upload}/resolve-revisions`,{base_revision:this.matrix.preview.base_revision,idempotency_key:key(),candidate_ids:ids});await this.open(this.upload);this.message('本表修订已确认；若仍有子结果歧义，可用下一个争议定位。整表确认后才正式入库。');}catch(e){this.message(e.message);}finally{global.v46Busy?.hide();}
 },
 async resolveGroup(){
  const first=this.editing[0],form=document.getElementById('v4LedgerInlineForm'),label=form.elements.test_item.value,method=form.elements.method.value;
  const sourceName=first.suggested_fields?.test_item||first.interpreted_fields.test_item;
  const ids=this.candidates.filter(c=>c.lifecycle==='draft'&&['source_revision','deleted_source'].includes(c.issue_code)&&(c.suggested_fields?.test_item||c.interpreted_fields.test_item)===sourceName).map(c=>c.id);
  try{busy('正在确认同类名称并保存可复用映射…');await request(`/previews/${this.upload}/resolve-revisions`,{base_revision:this.matrix.preview.base_revision,idempotency_key:key(),candidate_ids:ids,label,method,remember_alias:true});await this.open(this.upload);this.message('名称映射已保存，同类项目无需每张表重新配置。');}catch(e){this.message(e.message);}finally{global.v46Busy?.hide();}
 },
 async confirmSheet(){
  if(this.mutating)return;this.mutating=true;
  try{busy('正在复核并正式写入当前工作表…');const r=await request(`/previews/${this.upload}/confirm-sheet`,{base_revision:this.matrix.preview.base_revision,idempotency_key:key(),section:this.sheet});this.refreshing=true;await global.v4ResultCell.loadProjectCells(this.project);this.refreshing=false;await this.attach(this.project);this.message(`本表已正式入库${r.result_version_ids.length}项。可切换下一张表。`,true);}catch(e){this.message(e.message,true);}finally{this.refreshing=false;this.mutating=false;global.v46Busy?.hide();}
 },
 edit(){
  const list=this.candidates.filter(c=>this.selected.has(c.id)&&c.lifecycle==='draft');if(!list.length)return this.message('请先选择待核对格子。');
  const c=list[0],f={...c.interpreted_fields,...c.suggested_fields,...c.corrections};f.test_item=global.v45Ledger?.labels[f.test_item]||f.test_item;this.editing=list;this.editBaseline={...f};
  document.getElementById('v4LedgerEditFields').innerHTML=`<form id="v4LedgerInlineForm"><strong>表内修改 · ${list.length}个来源格</strong>${Object.keys(c.revisions||{}).length?`<p>原文有删除线。建议仅保留未划线内容；整项删除应排除。</p><button type="button" id="v46RevisionGroup">确认此名称/方法的全部同类修订，并记入词典</button>`:''}${c.issue_code==='ambiguous_file_slot'?'<p><strong>同一时间点的多行共用了检测名称。</strong>需要同时保留时，请分别命名子结果（如图谱一致性、主峰pI差值）；仅勾选确认不能区分这两类结果。确实不需要的内容可用下方排除按钮。</p>':''}<p>同一子结果可选择多个时间点统一命名。修改保留原文；不同子结果不要合并成同一行。</p><label>检测项 / 子结果名称<input name="test_item" value="${esc(f.test_item||'')}"></label><label>方法<input name="method" value="${esc(f.method||'')}" placeholder="原文未注明可填未注明"></label><label>检测结果<textarea name="result" rows="4" ${list.length>1?'disabled':''}>${esc(f.result||'')}</textarea></label><details><summary>样品、时间、条件或方法需要修改时展开</summary>${[['sample_type','样品类别 DS / DP / placebo'],['batch','批号'],['presentation','规格'],['time_point','时间点，如1M或10D'],['condition','条件']].map(([name,label])=>`<label>${label}<input name="${name}" value="${esc(f[name]||'')}" ${list.length>1&&name==='time_point'?'disabled':''}></label>`).join('')}</details><label>补充说明（首次核对可不填）<input name="reason" value=""></label><label><input type="checkbox" name="ack">已核对原文说明（不代表已正式采用）</label><button class="btn btn-primary">保存并核对此内容（待整表入库）</button> <button type="button" id="v46CancelEdit">取消修改</button> <button type="button" id="v4ReviewExclude">标记选中内容不作为检测结果</button></form>`;
  const revisionButton=document.getElementById('v46RevisionGroup');if(revisionButton)revisionButton.onclick=()=>this.resolveGroup();
  document.getElementById('v4LedgerInlineForm').onsubmit=e=>this.save(e);
  document.getElementById('v4ReviewExclude').onclick=()=>this.exclude();
  document.getElementById('v46CancelEdit').onclick=()=>document.getElementById('v4LedgerEditFields').replaceChildren();
  document.getElementById('v4LedgerEditFields').scrollIntoView?.({block:'center',behavior:'smooth'});
 },
 message(text,focus=false){const node=document.getElementById('v4LedgerReviewMessage');if(!node)return;node.textContent=text;if(focus)node.scrollIntoView?.({block:'center',behavior:'smooth'});},
 async save(e){
  e.preventDefault();if(this.mutating)return;this.mutating=true;busy('正在保存并重新核对所选来源…');const data=Object.fromEntries(new FormData(e.target));let revision=this.matrix.preview.base_revision;let saved=0;const editedIds=new Set(this.editing.map(c=>c.id));
  try{for(const c of this.editing){const before=this.editBaseline;const fields=Object.fromEntries(Object.entries(data).filter(([k,v])=>!['reason','ack'].includes(k)&&v!==String(before[k]||'')));if(data.ack==='on')fields.result=this.editing.length===1?data.result:(c.corrections.result??c.suggested_fields?.result??c.raw_fields.result);if(!Object.keys(fields).length)continue;
    const result=await global.v4Api.editCandidate(c.id,{base_revision:revision,candidate_revision:c.candidate_revision,idempotency_key:key(),reason:data.reason?.trim()||'核对原表后修正解析',fields,acknowledge_source_warning:true});revision=result.base_revision;saved++;
   }if(!saved)return this.message('尚未保存：没有修改内容。若要确认原文，请勾选已核对原文说明；同槽位的不同子结果还需分别命名，不能只勾选确认。',true);
   await this.open(this.upload);const unresolved=this.candidates.filter(c=>editedIds.has(c.id)&&c.lifecycle==='draft'&&['needs_review','different_value'].includes(c.status));this.message(unresolved.length?`已保存${saved}格修改；其中${unresolved.length}格仍有争议：${unresolved[0].detail} 黄色保留，尚未正式入库。`:`已保存并核对${saved}格，黄色已解除；整张工作表核对完后点击确认整表入库。`,true);
  }catch(error){await this.open(this.upload);this.message('未全部完成，请核实当前表格：'+error.message,true);}finally{this.mutating=false;global.v46Busy?.hide();}
 },
 async exclude(){
  this.selected=new Set((this.editing||[]).map(c=>c.id));return this.decide('exclude');
 },
 async confirm(){
  const list=this.candidates.filter(c=>this.selected.has(c.id));if(!list.length)return this.message('请先选择已解决的格子。');
  if(this.matrix?.preview.sheet_confirmation_required)return this.message('本次按工作表确认，请使用确认整表入库。');
  if(list.some(c=>c.status==='needs_review'))return this.message('选中来源仍有解析争议，尚未正式写入；首次结果无需高级员工确认。请先区分子结果或修正来源解释，再确认。');
  if(list.some(c=>c.status==='same_value'))return this.message('此结果已正式采用，本次为相同值来源，无需重复写入或高级确认。');
  if(list.some(c=>c.status==='different_value'))return this.message('已有正式值与本次不同。只有替换已有结果或提交独立复测时，才需高级确认。已有正式值显示在原格下方。');
  if(list.some(c=>c.status!=='new'||!c.allowed_actions.includes('confirm_initial')))return this.message('请先选中未正式提交且已解决的有效结果。');
  try{await global.v4Api.confirmPreview(this.upload,{base_revision:this.matrix.preview.base_revision,idempotency_key:key(),items:list.map(c=>({candidate_id:c.id,candidate_revision:c.candidate_revision,detection_id:c.detection_id,identity_confirmed:true,intent:'initial',reason:'台账内核对并确认来源结果',references:[]}))});await global.v4ResultCell.loadProjectCells(this.project);await this.open(this.upload);this.message('已正式确认，相关格子的待核对高亮已取消。');}
  catch(error){this.message(error.message);}
 }
};
const renderer=global.v4ResultCell;const original=renderer.enhanceLedgerTable;
renderer.enhanceLedgerTable=function(...args){
 const project=this.activeLoadingProject||(typeof currentProjectKey!=='undefined'?currentProjectKey:'');
 if(global.v5Review&&global.v4Api?.mode==='real')return global.v5Review.renderLedgerScene(original,this,args,project);
 document.getElementById('ledgerDataTable')?.classList.remove('v4-source-matrix');original.apply(this,args);
 if(!review.refreshing&&global.v4Api?.mode==='real'&&this.currentBaseRevision>0&&project)review.attach(project);
};
})(window);
