// Real intake: file summary and explicit batch confirmation, using existing workflow rules.
(function(global) {
  'use strict';
  global.v46Busy = {
    show(text) {
      if (!document.body) return;
      let node=document.getElementById('v46Busy');
      if(!node){node=document.createElement('div');node.id='v46Busy';node.className='v46-busy';node.setAttribute('role','status');node.setAttribute('aria-live','polite');node.innerHTML='<div><strong>正在准备检测台账</strong><p></p><small>请稍候，完成后会直接显示当前分表。</small></div>';document.body.appendChild(node);}
      node.querySelector('p').textContent=text;node.hidden=false;
    },
    hide(){const node=document.getElementById('v46Busy');if(node)node.hidden=true;},
    frame(){return new Promise(resolve=>global.requestAnimationFrame?global.requestAnimationFrame(()=>global.requestAnimationFrame(resolve)):resolve());}
  };
  const intake = global.v4Intake;
  const proto = Object.getPrototypeOf(intake);
  const oldSelect = proto.onFileSelected;
  const oldRender = proto.renderPreviewRightPanel;
  const oldOnContextChanged = proto.onContextChanged;
  proto.onContextChanged = function() {
    if (this._isRouting) return;
    return oldOnContextChanged.call(this);
  };
  const esc = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const fields = c => ({...c.interpreted_fields, ...c.corrections, ...c.record?.key,
    presentation:c.record?.key?.presentation || c.corrections?.presentation || c.interpreted_fields?.presentation || c.applied_defaults?.presentation || ''});
  const labels = {long_term:'长期',accelerated:'加速',stress:'应力',release:'放行'};
  const statuses = {new:'可直接导入',needs_review:'待处理',different_value:'与历史不同',same_value:'与历史相同',empty:'空白不更新'};
  const reason = c => c.issue_code === 'project_conflict' ? '文件项目与当前项目不一致，请重新选择正确项目上传。' : c.detail;
  const blockKey = c => JSON.stringify([c.location.section, ...['batch','sample_type','presentation','study','condition','study_id'].map(k => fields(c)[k] || '')]);
  const cmd = (kind, value) => ({base_revision:intake.currentPreview.base_revision,
    idempotency_key:global.v4Api.generateIdempotencyKey(kind, JSON.stringify(value)), ...value});

  proto.goUploadHome = function() {
    if (global.v4Api.mode !== 'real') return;
    if(global.v5Review&&!global.v5Review.leave())return;
    global.v5Review?.teardownWorkspace?.();
    if(global.v4LedgerReview){global.v4LedgerReview.summary=true;global.v4LedgerReview.upload=null;}
    currentProjectKey = '';
    this.onContextChanged();
    global.v4ResultCell?.clearCells();
    const meta=document.getElementById('workspaceMeta');if(meta)meta.replaceChildren();
    const projectLabel=document.getElementById('intakeProjectVal');if(projectLabel)projectLabel.textContent='上传后识别，或选择已有项目';
    document.getElementById('workspaceName').textContent = '上传文件 · 新建或选择项目';
    const url = new URL(location.href); for(const key of ['project','upload','sheet','view'])url.searchParams.delete(key); history.replaceState(null,'',url);
    document.querySelector('main.main-content').hidden = false;
    document.getElementById('scopedWorkspace').hidden = true;
    switchMainTab('intake');
    document.querySelectorAll('.nav-tab').forEach((button,index) => {if (index === 1) button.disabled=true;});
    const recent = document.getElementById('v43RecentPreviews');
    if (recent) recent.textContent = '选择项目后可继续处理已保存的上传。';
    const favorite = document.getElementById('workspaceFavorite'); if (favorite) {favorite.checked=false; favorite.disabled=true; favorite.hidden=true;}
    this.renderEmptyRightPanel();
  };

  proto.onFileSelected = async function(file) {
    if (global.v4Api.mode !== 'real') return oldSelect.call(this,file);
    if (!file || !/\.(xlsx|docx)$/i.test(file.name)) return oldSelect.call(this,file);
    const seq = ++this.currentReqId;
    this.currentFile = file; this.currentPreview = null;
    this.renderLoadingRightPanel(file.name);
    global.v46Busy.show('正在读取文件并识别项目…');
    try {
      const form = new FormData(); form.append('file',file);
      const info = await global.v4Api.sourceRequest('/intake/inspect',{method:'POST',body:form});
      const projects = await global.v4Api.getProjects();
      if (seq !== this.currentReqId) return;
      this.routing = {file,info,projects};
      const target = info.projects.length === 1 ? info.projects[0] : '';
      if (target) {
        if (!projects.some(p => p.project === target)) {
          try {
            await global.v4Api.sourceRequest('/projects', {
              method: 'POST',
              headers: {'Content-Type': 'application/json'},
              body: JSON.stringify({project: target, name: target})
            });
          } catch(e) {}
        }
        return await this.finishRouting(target);
      }
      this.renderRouting();
    } catch(error) { if (seq === this.currentReqId) this.renderErrorRightPanel(error.message); }
    finally{global.v46Busy.hide();}
  };

  proto.renderRouting = function() {
    const {info,projects,file} = this.routing;
    const target = info.projects.length === 1 ? info.projects[0] : '';
    const col = document.getElementById('v4IntakeRightCol');
    const defaultPrj = target || (typeof currentProjectKey !== 'undefined' ? currentProjectKey : '') || (projects[0]?.project || '');
    const selection = document.getElementById('v3FileSelection'); if (selection) {selection.hidden=false; selection.textContent = file.name + ' · 等待确认项目归属';}
    col.innerHTML = `<div class="card"><div class="card-body"><h3>1. 确认文件项目归属</h3><p><strong>${esc(file.name)}</strong></p>
      <form onsubmit="v4Intake.submitUnifiedProject(event)">
        <label>项目编号 / 代号（如 IP275）
          <input name="project" required list="v4ExistingProjects" value="${esc(defaultPrj)}" placeholder="输入或选择项目编号" style="padding:8px;font-size:14px;width:100%;max-width:320px;display:block;margin:6px 0 12px;">
          <datalist id="v4ExistingProjects">${projects.map(p => `<option value="${esc(p.project)}">${esc(p.name || p.project)}</option>`).join('')}</datalist>
        </label>
        <button class="btn btn-primary btn-lg" style="padding:9px 24px;font-size:15px;">确认项目并生成预览 ➔</button>
      </form>
      <p class="v3-note" style="margin-top:12px;">项目若已存在将直接进入；若不存在将自动创建，无需重复操作。</p></div></div>`;
  };

  proto.submitUnifiedProject = async function(event) {
    event.preventDefault();
    const data = Object.fromEntries(new FormData(event.target));
    const prj = (data.project || '').trim();
    if (!prj) return toast('请输入或选择项目编号');
    const projects = await global.v4Api.getProjects();
    if (!projects.some(p => p.project === prj)) {
      try {
        await global.v4Api.sourceRequest('/projects', {
          method: 'POST',
          headers: {'Content-Type': 'application/json'},
          body: JSON.stringify({project: prj, name: prj})
        });
      } catch(e) {}
    }
    await this.finishRouting(prj);
  };

  proto.createRoutingProject = async function(event) {
    event.preventDefault();
    const routing = this.routing;
    const data = Object.fromEntries(new FormData(event.target));
    const button = event.target.querySelector('button'); button.disabled = true;
    try {
      await global.v4Api.sourceRequest('/projects',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(data)});
      if (this.routing !== routing) return;
      await this.finishRouting(data.project);
    } catch(error) {
      if (this.routing !== routing) return;
      if (error.message.includes('项目已存在')) {toast('项目已存在，将进入已有项目，不修改其配置。'); return this.finishRouting(data.project);}
      toast(error.message); button.disabled = false;
    }
  };

  proto.finishRouting = async function(project) {
    if (!project || !this.routing) return;
    const routing = this.routing;
    const file = routing.file;
    const projects = await global.v4Api.getProjects();
    if (this.routing !== routing) return;
    projects.forEach(p => { if (!projectRegistry.some(known => known.id === p.project)) projectRegistry.push({id:p.project,name:p.name || p.project,type:'待配置',stage:'待确认',status:'进行中',modules:['intake','ledger']}); });
    this._isRouting = true;
    try {
      selectProject(project,false);
    } finally {
      this._isRouting = false;
    }
    const favorite = document.getElementById('workspaceFavorite'); if (favorite) {favorite.disabled=false; favorite.hidden=false;}
    this.excludedBlocks = new Set(); this.excludedCandidates = new Set();
    global.v46Busy.show('正在解析来源、检查归属并生成文件概览…');
    try{
      return await oldSelect.call(this,file);
    }finally{await global.v46Busy.frame();global.v46Busy.hide();}
  };

  proto.renderPreviewRightPanel = function(preview) {
    if (global.v4Api.mode !== 'real') return oldRender.call(this,preview);
    if (this.summaryPreviewId !== preview.preview_id) {this.summaryPreviewId=preview.preview_id; this.excludedBlocks=new Set(); this.excludedCandidates=new Set(); this.newItemsCommand=null;}
    this.currentPreview = preview;
    this.excludedBlocks ||= new Set(); this.excludedCandidates ||= new Set();
    for (const c of preview.candidates) Object.assign(c,this.candidateEdits.get(c.id) || {});
    const col = document.getElementById('v4IntakeRightCol'); if (!col) return;
    const groups = new Map();
    for (const c of preview.candidates) {
      const key = blockKey(c); if (!groups.has(key)) groups.set(key,[]); groups.get(key).push(c);
    }
    this.fileBlocks = [...groups.values()];
    const drafts = preview.candidates.filter(c => c.lifecycle === 'draft');
    const ready = drafts.filter(c => c.can_receive && !c.received && !this.excludedBlocks.has(blockKey(c)) && !this.excludedCandidates.has(c.id));
    const problems = new Map();
    drafts.filter(c => c.status === 'needs_review' && !c.can_receive && !c.received).forEach(c => {
      const key = JSON.stringify([c.issue_code,c.detail,c.issue_code === 'ambiguous_file_slot' ? c.interpreted_fields?.test_item : '']); if (!problems.has(key)) problems.set(key,[]); problems.get(key).push(c);
    });
    const duplicates = drafts.filter(c => ['same_value','different_value'].includes(c.status));
    this.problemGroups = [...problems.values()];
    const sourceGroups = new Map();
    drafts.filter(c => c.issue_code === 'unknown_item').forEach(c => {
      const name = c.interpreted_fields?.test_item || c.raw_fields.test_item;
      if (!sourceGroups.has(name)) sourceGroups.set(name,[]); sourceGroups.get(name).push(c);
    });
    this.newItemGroups = [...sourceGroups.values()];
    const submitted = preview.candidates.filter(c => c.lifecycle === 'submitted').length;
    const candidateRow = c => `<div class="v4-simple-row"><span>${esc(c.interpreted_fields?.test_item || c.record?.key?.item || c.raw_fields.test_item)} · ${esc((c.record?.key?.month != null || c.record?.key?.day != null) ? (c.record.key.day != null ? c.record.key.day+'天' : c.record.key.month+'M') : c.interpreted_fields?.time_point || c.raw_fields.time_point)}<br><small>${esc(c.location.section)} R${c.location.row}C${c.location.column}</small></span><strong>${esc(c.record?.result?.raw_text || c.raw_fields.result)}</strong><button class="btn btn-outline btn-sm" onclick="v4Intake.inspectCandidate('${esc(c.id)}')">${c.status === 'new' ? '查看 / 修改' : '处理此项'}</button></div>`;
    col.innerHTML = `<div class="card v4-preview-card"><div class="card-header"><strong>文件预览 · ${esc(preview.filename)}</strong><span>尚未导入部分继续保留</span></div><div class="card-body">
      <div class="v4-direct-enter-box" style="background:#eaf4ff;border:2px solid #087baa;border-radius:8px;padding:14px 18px;margin-bottom:14px;display:flex;justify-content:space-between;align-items:center;">
        <div>
          <h4 style="margin:0 0 4px;font-size:16px;color:#0f3861;">文件已解析完成 · ${esc(preview.filename)}</h4>
          <p style="margin:0;font-size:13px;color:#335;">项目：<strong>${esc(preview.project)}</strong> · 共提取 <strong>${preview.candidates.length}</strong> 项检测数据</p>
        </div>
        <button type="button" class="btn btn-primary" style="font-size:14px;padding:8px 18px;" onclick="v5Review.received('${esc(preview.preview_id)}', '${esc(preview.project)}')">➔ 进入原表核对与验收</button>
      </div>
      ${preview.duplicate_source?'<p role="status"><strong>相同文件已上传，本次未新增任何记录。</strong>请继续处理原上传；需要更新数据时，请上传内容有变化的文件。</p>':''}
      <h3>2. 文件概览 · ${esc(preview.project)}</h3><p>${new Set(preview.candidates.map(c=>c.location.section)).size} 个来源表 · ${this.fileBlocks.length} 个来源分组 · ${ready.length} 项可直接导入 · ${this.problemGroups.length} 组待处理（${drafts.filter(c => c.status === 'needs_review' && !c.can_receive && !c.received).length} 项） · ${duplicates.length} 项涉及历史结果 · ${submitted} 项已正式提交；${drafts.filter(c=>c.received).length}项已接收待台账核对</p>
      <p class="v3-note">先接收至台账，按每个工作表核对并确认一次，无需逐格勾选正常结果。尚无质量标准不妨碍收集结果；此处不自动判断合格。</p>
      <details><summary>查看批号、条件和时间点范围 / 统一补充信息</summary>${this.fileBlocks.map((list,index) => {
        const f = fields(list[0]); const months = [...new Set(list.map(c => (c.record?.key?.month != null || c.record?.key?.day != null) ? (c.record.key.day != null ? c.record.key.day+'天' : c.record.key.month+'M') : c.interpreted_fields?.time_point || c.raw_fields.time_point))];
        return `<div class="v4-simple-block"><strong>${esc(f.batch || '批号待补充')} · ${esc(f.sample_type)} · ${esc(f.presentation || '规格待补充')}</strong><p>${esc(list[0].location.section)} · ${esc(labels[f.study] || f.study)} · ${esc(f.condition)} · ${months.map(esc).join('、')} · ${list.length} 项</p><label><input type="checkbox" ${this.excludedBlocks.has(blockKey(list[0])) ? '' : 'checked'} onchange="v4Intake.excludeBlock(${index},!this.checked)">纳入本次正常结果确认</label> <button class="btn btn-outline btn-sm" onclick="v4Intake.showSharedForm(${index})">修改这组共用信息</button></div>`;
      }).join('')}<div id="v44SharedForm"></div></details>
      <details><summary>来源解析范围、计划日期与提示</summary>${preview.parsing_summary.map(s => `<p><strong>${esc(s.section)}</strong>：${s.result_cells} 项候选。${esc(s.note)}</p>${(s.blocks || []).flatMap(b => b.planning_notes || []).map(note => `<p class="v3-note">${esc(note)}</p>`).join('')}`).join('')}</details>
      ${this.newItemGroups.length ? `<h3>3. 确认新检测项目（${this.newItemGroups.length} 个名称）</h3><p>每个名称只配置一次，覆盖其全部时间点。先确认名称、方法、单位和数据类型；不需要填写质量限度。</p><form id="v44NewItems" onsubmit="v4Intake.saveNewItems(event)"><label><input type="checkbox" onchange="this.form.querySelectorAll('[name^=use_]').forEach(c=>c.checked=this.checked)">选择全部新检项（下方展开可调整）</label>${this.newItemGroups.map((list,index) => {
        const f = fields(list[0]);
        const textResult = list.some(c => !/^(?:[<>≤≥＜＞]=?\s*)?[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:e[+-]?\d+)?$|^(?:n\/?a|nd|---|\/|符合规定)$/i.test(String(c.raw_fields.result).trim()));
        return `<div class="v4-simple-block"><label><input type="checkbox" name="use_${index}">${esc(f.test_item || list[0].raw_fields.test_item)} · ${list.length} 项结果</label><p class="v3-note">方法：${esc(f.method || '待填写')} · 单位：${esc(f.unit || '未注明')} · 类型建议：${textResult ? '文字 / 定性' : '数值'}（确认后才生效）</p><details><summary>查看示例 / 调整配置</summary><label>标准名称<input name="label_${index}" required value="${esc(f.test_item || list[0].raw_fields.test_item)}"></label><label>方法<input name="method_${index}" value="${esc(f.method || '')}" placeholder="原文未注明时请明确填写"></label><label>单位<input name="unit_${index}" value="${esc(f.unit || '')}" placeholder="无单位可留空"></label><label>数据类型<select name="type_${index}"><option value="numeric" ${textResult ? '' : 'selected'}>数值</option><option value="qualitative" ${textResult ? 'selected' : ''}>文字 / 定性</option></select></label><p>来源示例：${esc(list[0].raw_fields.result)}</p></details></div>`;
      }).join('')}<button class="btn btn-primary" ${global.v4Api.can('confirm_fact') ? '' : 'disabled'}>确认勾选的新检项并重新检查</button></form><details><summary>选择已有检项、名称归并或项目默认规格（按需使用）</summary>${this.configurationPanel(preview)}</details>` : `<details><summary>检测项目映射与默认规格（按需使用）</summary>${this.configurationPanel(preview)}</details>`}
      <h3>待处理异常 · 按原因集中处理</h3>${this.problemGroups.map((list,index) => `<details class="v4-simple-block"><summary>${esc(list[0].issue_code === 'ambiguous_file_slot' ? list[0].interpreted_fields?.test_item : reason(list[0]))} · ${list.length} 项</summary><p>正常部分可先导入；本组不会因整份确认而自动解除。</p>${list.slice(0,30).map(candidateRow).join('')}${list.length > 30 ? `<p>共${list.length}项。共享身份问题可在上方来源分组统一处理；更多明细使用下方分页查看。</p>` : ''}</details>`).join('') || '<p>基础归属均可识别。争议项接收后在检测台账高亮核对。</p>'}
      ${duplicates.length ? `<details class="v4-simple-block"><summary>历史重复 / 改值 · ${duplicates.length} 项</summary><p>仅重复写入需要高级确认；相同值可保留在草稿而不重复写入。不同值请明确是更正还是独立复测，不默认选OOE/OOS。</p>${duplicates.slice(0,30).map(candidateRow).join('')}<button class="btn btn-outline" onclick="v4Intake.submitConfirm()">提交已明确意图的重复结果</button></details>` : ''}
      <details><summary>全部结果明细（无需逐项打开）</summary><div class="v3-toolbar"><button class="btn btn-outline" onclick="v4Intake.changePage(-1)" ${this.candidatePage ? '' : 'disabled'}>上一页</button> 第${this.candidatePage+1} / ${Math.max(1,Math.ceil(preview.candidates.length/80))}页 <button class="btn btn-outline" onclick="v4Intake.changePage(1)" ${(this.candidatePage+1)*80 >= preview.candidates.length ? 'disabled' : ''}>下一页</button></div>${preview.candidates.slice(this.candidatePage*80,(this.candidatePage+1)*80).map(c => `<p>${esc(statuses[c.status])} · ${c.lifecycle === 'submitted' ? '已提交' : c.lifecycle === 'withdrawn' ? '已排除' : '未导入'}</p>${candidateRow(c)}`).join('')}</details>
      <div class="v4-commit-action-box"><h3>4. 汇总确认</h3>${preview.candidates.some(c=>c.received||c.lifecycle==='submitted')?`<button class="btn btn-primary" onclick="v4Intake.openReceivedLedger()">打开本次数据所在台账 / 继续核对</button>`:''}<p>本次将接收 <strong>${ready.length}</strong> 项来源数据。接收后在原检测台账展示，按每个工作表确认后正式采用；争议高亮，已有正式值保留。</p><label><input id="v44ConfirmFile" type="checkbox">我已核对项目归属、来源范围和原文检测事实，接收后在台账按工作表确认</label><p id="v44CommitStatus" role="status"></p><button class="btn btn-primary" onclick="v4Intake.confirmFile()" ${!ready.length || !global.v4Api.can('confirm_fact') ? 'disabled' : ''}>接收并进入台账（${ready.length} 项）</button><p class="v3-note">${submitted}项已提交；其余草稿可继续处理，无需重复上传。</p></div>
      </div></div>`;
    const selection = document.getElementById('v3FileSelection');
    if (selection) {selection.hidden = false; selection.textContent = `${preview.filename} · ${submitted}项已提交，${drafts.length}项保留草稿`;}
  };

  proto.excludeBlock = function(index, exclude) {
    const key = blockKey(this.fileBlocks[index][0]);
    exclude ? this.excludedBlocks.add(key) : this.excludedBlocks.delete(key);
    this.renderPreviewRightPanel(this.currentPreview);
  };
  proto.showSharedForm = function(index) {
    const list = this.fileBlocks[index]; const f = fields(list[0]); this.sharedIndex = index;
    document.getElementById('v44SharedForm').innerHTML = `<form onsubmit="v4Intake.saveShared(event)"><h4>仅修改此来源分组的${list.filter(c => c.lifecycle === 'draft').length}项草稿共用信息，不改检测值</h4>${[['batch','批号'],['presentation','规格'],['sample_type','样品类型 DS/DP'],['study_id','研究编号'],['study','用途 long_term/accelerated/stress'],['condition','条件']].map(([k,label]) => `<label>${label}<input name="${k}" value="${esc(f[k] || '')}"></label>`).join('')}<label>修改说明<input name="reason" required></label><button class="btn btn-primary">应用修改并重新检查</button></form>`;
  };
  proto.saveShared = async function(event) {
    event.preventDefault(); const data = Object.fromEntries(new FormData(event.target));
    const list = this.fileBlocks[this.sharedIndex].filter(c => c.lifecycle === 'draft'); const before = fields(list[0]);
    const changed = Object.fromEntries(Object.entries(data).filter(([key,val]) => key !== 'reason' && val.trim() !== String(before[key] || '')));
    if (!Object.keys(changed).length) return toast('未修改信息。');
    try {
      await global.v4Api.sourceRequest(`/previews/${this.currentPreview.preview_id}/shared`,{method:'PATCH',headers:{'Content-Type':'application/json'},body:JSON.stringify(cmd('shared',{reason:data.reason,fields:changed,candidate_ids:list.map(c => c.id)}))});
      await this.reloadPreview(); toast('共用信息已修改并重新检查，尚未导入。');
    } catch(error) { toast(error.message); }
  };
  proto.saveNewItems = async function(event) {
    event.preventDefault(); const form = event.target; const data = Object.fromEntries(new FormData(form));
    const items = this.newItemGroups.flatMap((list,index) => data[`use_${index}`] ? [{id:'item_'+list[0].id,label:data[`label_${index}`].trim(),method:data[`method_${index}`].trim(),unit:data[`unit_${index}`].trim(),result_type:data[`type_${index}`],aliases: data[`label_${index}`].trim() === (list[0].interpreted_fields?.test_item || list[0].raw_fields.test_item) ? [] : [list[0].interpreted_fields?.test_item || list[0].raw_fields.test_item]}] : []);
    if (!items.length) return toast('请勾选要新增的检测项目。');
    if (items.some(item => !item.method)) return toast('请明确所选检项的方法；原文未注明的可填写“未注明”，不从其他项目借用。');
    const button = form.querySelector('button'); button.disabled = true;
    try {
      this.newItemsCommand = cmd('new-items',{reason:'用户批量确认新检项名称、方法、单位和数据类型',items});
      await global.v4Api.sourceRequest(`/projects/${this.currentPreview.project}/items`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(this.newItemsCommand)});
      this.newItemsCommand = null; await this.reloadPreview(); toast('检项已配置并重新检查，检测结果尚未导入。');
    } catch(error) { toast(error.message); button.disabled = false; }
  };
  proto.openReceivedLedger = async function(){await global.v45Ledger.afterReceive(this.currentPreview.preview_id,this.currentPreview.project,this.currentPreview.candidates,{result_version_ids:[],alreadyReceived:true});const message=document.getElementById('v4LedgerReviewMessage');if(message)message.textContent='已打开本次来源所在分区。黄色格可在表内修改并确认；正常结果在正式结果汇总中查看。';};
  proto.confirmFile = async function() {
    if (!document.getElementById('v44ConfirmFile').checked) return toast('请先确认文件范围与原文检测事实。');
    if (this.confirmingFile) return;
    const candidates = this.currentPreview.candidates.filter(c => c.lifecycle === 'draft' && c.can_receive && !c.received && !this.excludedBlocks.has(blockKey(c)) && !this.excludedCandidates.has(c.id));
    if (!candidates.length) return;
    this.confirmingFile = true;
    global.v46Busy.show('正在接收来源；随后打开第一张工作表…');await global.v46Busy.frame();
    document.getElementById('v44CommitStatus').textContent = '正在校验并导入，请勿重复操作…';
    try {
      const result = await global.v4Api.sourceRequest(`/previews/${this.currentPreview.preview_id}/receive`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({...cmd('receive-file',{}),defer_confirmation:true,candidate_ids:candidates.map(c=>c.id)})});
      await this.reloadPreview(); await this.refreshRecent();
      document.getElementById('v44CommitStatus').textContent = '来源已接收，正在打开分表；核对后按工作表确认正式入库。';
      if(global.v45Ledger)await global.v45Ledger.afterReceive(this.currentPreview.preview_id,this.currentPreview.project,candidates,result);
    } catch(error) { document.getElementById('v44CommitStatus').textContent = `未确认完成：${error.message}。请刷新核实，原件与草稿保留。`; }
    finally { this.confirmingFile = false;await global.v46Busy.frame();global.v46Busy.hide(); }
  };
  document.addEventListener('DOMContentLoaded',() => {
    if (global.v4Api.mode !== 'real') return;
    const home = document.createElement('button'); home.className = 'btn btn-outline btn-sm'; home.textContent = '上传其他项目数据'; home.onclick = () => intake.goUploadHome();
    document.getElementById('v43Connection')?.appendChild(home);
  });
})(window);
