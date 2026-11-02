/* V4.5: original ledger sections, backed only by real record identities. */
(function(g){
'use strict';
const esc=v=>String(v??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const names={release:'放行检测',stability:'稳定性',stress:'影响因素'};
const studies={release:'放行检测',long_term:'长期稳定性',accelerated:'加速稳定性',stress:'影响因素'};
const ui=g.v45Ledger={
 labels:{}, section:'stability',batch:'',study:'',condition:'',project:null,
 category(study){return study==='release'?'release':study==='stress'?'stress':'stability';},
 match(k){return this.category(k.study)===this.section&&(!this.batch||k.batch===this.batch)&&(!this.study||k.study===this.study)&&(!this.condition||k.condition===this.condition);},
 change(field,value){document.getElementById('v4LedgerEditFields')?.replaceChildren();this[field]=value;if(field==='section'){document.getElementById('v45ReceiptNotice')?.remove();this.study='';this.condition='';this.batch='';}if(g.v4LedgerReview)g.v4LedgerReview.summary=true;g.v4ResultCell.enhanceLedgerTable();},
 controls(cells,project){
  if(this.project!==project){this.project=project;this.batch='';this.study='';this.condition='';}
  const rows=cells.map(c=>c.slot_key), scope=rows.filter(k=>this.category(k.study)===this.section);
  const available=[...new Set(scope.map(k=>k.batch))];if(!available.includes(this.batch))this.batch=available[0]||'';
  const shown=cells.filter(c=>this.match(c.slot_key));const banner=document.querySelector('#ledgerBannerGrid .v3-label');if(banner)banner.textContent=`${names[this.section]} · ${shown.length}个已提交结果（全项目${cells.length}个）`;const scopeNote=document.querySelector('#ledgerBannerGrid p');if(scopeNote)scopeNote.textContent=shown.length?'按当前分区、批号和条件展示；待核对来源另行高亮，正式采用值独立保留。':'本分区暂无已提交结果；其他分区的数据不会混入此处。';
  const samples=document.getElementById('ledgerSamplePills'), purposes=document.getElementById('ledgerConditionPills');
  for(const node of [samples,purposes])if(node?.closest('.toolbar-row'))node.closest('.toolbar-row').hidden=false;
  if(samples){samples.previousElementSibling && (samples.previousElementSibling.textContent='批号 / 样品：');const batches=[...new Set(scope.map(k=>k.batch))];samples.innerHTML=`<select id="v45Batch">${batches.map(b=>`<option ${b===this.batch?'selected':''}>${esc(b)}</option>`).join('')}</select>`;document.getElementById('v45Batch').onchange=e=>this.change('batch',e.target.value);}
  if(purposes){purposes.previousElementSibling && (purposes.previousElementSibling.textContent='检测分区：');purposes.innerHTML=Object.entries(names).map(([key,name])=>`<button type="button" class="purpose-btn ${key===this.section?'active':''}" data-section="${key}">${name}</button>`).join('')+`<select id="v45Study"><option value="">全部研究类型</option>${[...new Set(scope.map(k=>k.study))].map(s=>`<option value="${s}" ${s===this.study?'selected':''}>${esc(studies[s]||s)}</option>`).join('')}</select><select id="v45Condition"><option value="">全部条件</option>${[...new Set(scope.map(k=>k.condition))].map(s=>`<option ${s===this.condition?'selected':''}>${esc(s)}</option>`).join('')}</select>`;purposes.querySelectorAll('[data-section]').forEach(b=>b.onclick=()=>this.change('section',b.dataset.section));document.getElementById('v45Study').onchange=e=>this.change('study',e.target.value);document.getElementById('v45Condition').onchange=e=>this.change('condition',e.target.value);}
 },
 formal(cells,renderer){
  if(renderer.loadError)return `<tbody><tr><td>结果读取失败：${esc(renderer.loadError)}。请重新连接，当前数量未知。</td></tr></tbody>`;
  if(renderer.loading)return '<tbody><tr><td role="status">正在读取本分区正式结果，请稍候…</td></tr></tbody>';
  const groups=new Map();for(const c of cells.filter(c=>this.match(c.slot_key))){const k=c.slot_key;const group=JSON.stringify([k.batch,k.sample_type,k.presentation,k.study,k.study_id,k.condition,k.purpose]);if(!groups.has(group))groups.set(group,[]);groups.get(group).push(c);}
  if(!groups.size)return `<tbody><tr><td>本分区暂无正式结果。已接收但尚未确认的内容，请在下方选择来源文件核对。</td></tr></tbody>`;
  let html='';for(const [group,list]of groups){const k=list[0].slot_key;const times=[...new Set(list.map(c=>c.slot_key.day!=null?c.slot_key.day+'D':c.slot_key.month!=null?c.slot_key.month+'M':'release'))].sort((a,b)=>parseInt(a)* (a.endsWith('M')?30:1)-parseInt(b)*(b.endsWith('M')?30:1));
   const rows=new Map();for(const c of list){const identity=JSON.stringify([c.slot_key.item,c.slot_key.method,c.slot_key.method_version,c.slot_key.reporting_basis]);if(!rows.has(identity))rows.set(identity,[]);rows.get(identity).push(c);}
   html+=`<tbody><tr><th colspan="${times.length+2}">${esc(k.batch)} · ${esc(k.sample_type)} · ${esc(({reference:"参考品",routine:"常规样品",unknown:"用途未注明"})[k.purpose]||"用途未注明")} · ${esc(k.presentation)} · ${esc(studies[k.study])} · ${esc(k.condition)} · ${esc(k.study_id)}</th></tr><tr><th>检测项目</th><th>方法</th>${times.map(t=>`<th>${t==='release'?'放行结果':esc(t.replace('M','个月').replace('D','天'))}</th>`).join('')}</tr>`;
   for(const row of rows.values()){html+=`<tr><td>${esc(this.labels[row[0].slot_key.item]||row[0].slot_key.item)}${row[0].slot_key.reporting_basis?" · "+esc(row[0].slot_key.reporting_basis):""}</td><td>${esc(row[0].slot_key.method)}<small class="v5-evidence">${esc(row[0].slot_key.method_version==='not_reported'?'版本未注明':row[0].slot_key.method_version)}</small></td>${times.map(t=>{const c=row.find(c=>(c.slot_key.day!=null?c.slot_key.day+'D':c.slot_key.month!=null?c.slot_key.month+'M':'release')===t);return `<td ${c?`data-slot-id="${esc(c.slot_id)}"`:''}>${c?renderer.renderCellContent(c,''):'—'}</td>`;}).join('')}</tr>`;}html+='</tbody>';
  }return html;
 },
 async afterReceive(previewId,project,candidates,result){
  if(g.v5Review)return g.v5Review.received(previewId,project);
  const first=candidates.find(c=>c.record?.key?.study||c.interpreted_fields?.study);const study=first?.record?.key?.study||first?.interpreted_fields?.study;
  this.section=this.category(study);this.batch='';this.study='';this.condition='';
  g.v4LedgerReview.summary=false;g.v4LedgerReview.upload=previewId;g.v4LedgerReview.sheet=null;
  if(g.history&&g.location?.href){const url=new URL(g.location.href);url.searchParams.set('view','ledger');g.history.replaceState(null,'',url);}
  if(typeof switchMainTab==='function')switchMainTab('ledger');
  g.v46Busy?.show('正在读取台账并生成当前分表…');
  await g.v4ResultCell.loadProjectCells(project);
  await g.v4LedgerReview.attach(project);
  const ledger=document.getElementById('tab-ledger');if(ledger){let notice=document.getElementById('v45ReceiptNotice');if(!notice){notice=document.createElement('div');notice.id='v45ReceiptNotice';notice.setAttribute('role','status');ledger.prepend(notice);}notice.className='v45-receipt-notice';notice.innerHTML=`<strong>${result.alreadyReceived?'已打开本次接收记录':'接收完成'}</strong><p>来源已保存，已进入原始工作表核对。上方工作表清单显示完成进度；可整行多选核对或排除，完成一张后点击“确认整表入库”。首次有效结果由普通实验员确认即可。</p><button id="v45ViewConfirmed" class="btn btn-outline">查看正式结果</button> <button id="v45ReviewSource" class="btn btn-primary">查看来源分表并核对</button>`;document.getElementById('v45ViewConfirmed').onclick=()=>{document.getElementById('v4LedgerEditFields')?.replaceChildren();g.v4LedgerReview.summary=true;g.v4ResultCell.enhanceLedgerTable();};document.getElementById('v45ReviewSource').onclick=()=>{g.v4LedgerReview.summary=false;g.v4LedgerReview.attach(project);};}
  const status=document.getElementById('v4LedgerReviewMessage');if(status)status.textContent='当前只显示一个分表。黄色格在表内修改，确认整张工作表后正式入库。';
  await g.v46Busy?.frame();document.getElementById('v4LedgerInlineEditor')?.scrollIntoView({block:'start'});g.v46Busy?.hide();
 }
};
})(window);
