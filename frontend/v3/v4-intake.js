// V4 Intake Component Module
// Binds file selection on left with candidate preview on right
// Clears previous success state on file change, categorizes candidates, handles identity verification,
// and supports state reset on sample/project switching.

(function(global) {
  'use strict';

  function esc(s) {
    if (s == null) return '';
    return String(s)
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;')
      .replace(/'/g, '&#39;');
  }

  const STATUS_CONFIG = {
    needs_review: { label: '待核对', priority: 1, defaultOpen: true, desc: '检测身份、单位、方法或多候选需明确' },
    different_value: { label: '与旧值不同', priority: 2, defaultOpen: true, desc: '检测值与当前槽位有效记录不同' },
    new: { label: '新增候选', priority: 3, defaultOpen: true, desc: '槽位当前无有效记录，等待首次入库' },
    same_value: { label: '与已有值相同', priority: 4, defaultOpen: false, desc: '同槽位等值，重复检测已默认折叠' },
    empty: { label: '空白不更新', priority: 5, defaultOpen: false, desc: '占位或空白，保留来源不清除旧值' }
  };

  class V4Intake {
    constructor() {
      this.configuration = null;
      this.configurationError = "";
      this.pendingConfigurationCommand = null;
      this.currentFile = null;
      this.currentPreview = null;
      this.candidatePage = 0;
      this.activeStatusFilter = 'all'; // 'all' or specific status
      this.currentReqId = 0; // Request versioning to ignore stale async responses
      this.candidateEdits = new Map(); // candidateId -> { intent, retest_ref_type, retest_ref_id, confirm_reason, corrections, ... }
      this.recentOffset = 0;
      this.mappingPage = 0;
      this.selectedMappingGroups = new Set();
      this.mappingSelectionPreviewId = null;
    }

    saveCandidateEdit(candId, data) {
      if (!candId) return;
      const prev = this.candidateEdits.get(candId) || {};
      const updated = { ...prev, ...data };
      this.candidateEdits.set(candId, updated);
      if (this.currentPreview && this.currentPreview.candidates) {
        const cand = this.currentPreview.candidates.find(c => c.id === candId);
        if (cand) {
          Object.assign(cand, updated);
        }
      }
    }

    init() {
      const container = document.querySelector('#tab-intake .upload-split-grid');
      if (!container) return;

      // Ensure the right preview container is properly wired (the second top-level child of upload-split-grid)
      const topDivs = container.querySelectorAll(':scope > div');
      const rightCol = topDivs.length > 1 ? topDivs[1] : null;
      if (rightCol) {
        rightCol.id = 'v4IntakeRightCol';
        this.renderEmptyRightPanel();
      }

      if (window.v4Api?.mode === 'real') {
        const drop = document.getElementById('fileDropZone');
        if (drop) {
          const textNodes = document.createTreeWalker(drop, NodeFilter.SHOW_TEXT);
          let node;
          while ((node = textNodes.nextNode())) node.nodeValue = node.nodeValue.replace('支持 .docx、.xlsx、.pdf', '支持已适配的 .docx、.xlsx 模板');
        }
        const recent = document.getElementById('v3RecentRecords');
        if (recent) {
          recent.querySelectorAll('table, #v3ExpandRecords').forEach(node => { node.hidden = true; });
          const list = document.createElement('div'); list.id = 'v43RecentPreviews';
          recent.appendChild(list);
        }
      }

      // Re-bind dropzone events
      const dropzone = document.getElementById('fileDropZone');
      if (dropzone) {
        dropzone.removeAttribute('onclick');
        // File input is setup by v3.js, make sure change event calls this.onFileSelected
        const fileInput = document.getElementById('v3FileInput');
        if (fileInput) {
          fileInput.addEventListener('change', (e) => {
            if (e.target.files && e.target.files.length > 0) {
              this.onFileSelected(e.target.files[0]);
            }
          });
        }
      }
    }

    // Called when project or sample changes to reset stale preview/intake state
    onContextChanged() {
      this.resetFile(false);
      this.recentOffset = 0;
      this.refreshRecent();
    }

    async refreshRecent(offset = this.recentOffset) {
      const node = document.getElementById('v43RecentPreviews');
      if (!node || window.v4Api?.mode !== 'real') return;
      const request = this.recentReqId = (this.recentReqId || 0) + 1;
      const project = typeof currentProjectKey !== 'undefined' ? currentProjectKey : '';
      if(!project){node.textContent='上传文件后确认新项目，或选择已有项目继续处理。';return;}
      node.textContent = '正在读取已保存预览…';
      try {
        const rows = await window.v4Api.listPreviews(project, offset);
        if (request !== this.recentReqId || project !== currentProjectKey) return;
        this.recentOffset = offset;
        node.innerHTML = `<p class="v3-note">已保存的上传预览 · 可继续处理；已提交结果见检测台账。</p>${rows.map(r => `<p><button class="btn btn-outline btn-sm" data-preview-id="${esc(r.preview_id)}">继续处理</button> ${esc(r.filename)}<br><small>${esc(r.uploaded_at)} · 共 ${r.total} 项 / 草稿 ${r.draft} / 已提交 ${r.submitted} / 撤回 ${r.withdrawn}</small></p>`).join('') || '<p>当前项目没有上传记录</p>'}<button class="btn btn-outline btn-sm" id="v43RecentPrev" ${offset === 0 ? 'disabled' : ''}>上一页</button> <button class="btn btn-outline btn-sm" id="v43RecentNext" ${rows.length < 20 ? 'disabled' : ''}>下一页</button>`;
        node.querySelectorAll('[data-preview-id]').forEach(button => { button.onclick = () => this.resumePreview(button.dataset.previewId); });
        node.querySelector('#v43RecentPrev').onclick = () => this.refreshRecent(Math.max(0, offset - 20));
        node.querySelector('#v43RecentNext').onclick = () => this.refreshRecent(offset + 20);
      } catch (error) { if(request===this.recentReqId&&project===currentProjectKey)node.textContent = `读取上传记录失败：${error.message}。请使用顶部重新连接。`; }
    }

    async resumePreview(id) {
      const reqId = ++this.currentReqId;
      this.renderLoadingRightPanel('已保存的上传预览');
      try {
        const preview = await window.v4Api.getPreview(id);
        const configuration = await window.v4Api.getConfiguration(preview.project);
        if (reqId !== this.currentReqId) return;
        this.currentPreview = preview; this.configuration = configuration; this.configurationError = '';
        this.currentFile = null; this.candidatePage = 0; this.mappingPage = 0; this.activeStatusFilter = 'all';
        this.candidateEdits.clear();
        this.renderPreviewRightPanel(preview);
      } catch (error) { if (reqId === this.currentReqId) this.renderErrorRightPanel(error.message); }
    }

    getImportContext() {
      if (window.v4Api?.mode === 'real') {
        // The real file supplies identity. No demo batch/specification/study defaults.
        const release = typeof currentLedgerCond !== 'undefined' && currentLedgerCond === 'release';
        return {role: release ? 'release_coa' : 'stability_source', defaults: {}};
      }
      const curBatch = typeof currentLedgerSample !== 'undefined' && currentLedgerSample ? currentLedgerSample : 'E202506002A1';
      const curCond = typeof currentLedgerCond !== 'undefined' ? currentLedgerCond : 'lt';
      const curProject = typeof currentProjectKey !== 'undefined' ? currentProjectKey : 'IP166E';

      const condMap = {
        lt: '5C',
        acc: '25C/60%RH',
        temp40: '40C/75%RH',
        release: 'release'
      };

      const studyMap = {
        lt: 'long_term',
        acc: 'accelerated',
        temp40: 'stress',
        release: 'release'
      };

      let sampleType = 'DP';
      let presentation = '1mg (0.5ml)';
      let studyId = 'STB(C)-QC-IP166E-P-2-005';

      if (typeof sampleProfiles !== 'undefined' && sampleProfiles[curBatch]) {
        const prof = sampleProfiles[curBatch];
        if (prof.stdCode?.includes('DS') || prof.protoName?.includes('原液') || /DS/i.test(curBatch)) {
          sampleType = 'DS';
        } else if (prof.stdCode?.includes('DP') || prof.protoName?.includes('制剂') || prof.protoName?.includes('成品') || /DP/i.test(curBatch)) {
          sampleType = 'DP';
        }
        if (prof.spec) {
          presentation = prof.spec.includes('1mg') ? '1mg (0.5ml)' : prof.spec;
        }
        if (prof.protoId) {
          studyId = prof.protoId;
        }
      } else if (/DS/i.test(curBatch)) {
        sampleType = 'DS';
      }

      if (typeof v3Study === 'function' && v3Study()?.id) {
        studyId = v3Study().id;
      }

      const defaults = {
        project: curProject,
        batch: curBatch,
        sample_type: sampleType,
        presentation: presentation,
        study_id: studyId,
        condition: condMap[curCond] || '5C',
        study: studyMap[curCond] || 'long_term'
      };

      return {
        role: curCond === 'release' ? 'release_coa' : 'stability_source',
        defaults: defaults
      };
    }

    // Changing file must clear old success/preview state
    async onFileSelected(file) {
      if (!file) return;

      // Backend only supports XLSX and simple DOCX, NOT PDF
      if (/\.pdf$/i.test(file.name)) {
        toast('后端当前仅支持长表 XLSX 和简单 DOCX 文件解析，暂不支持 PDF 格式。');
        this.renderErrorRightPanel('不支持的文件格式：PDF。请上传 .xlsx 或 .docx 格式的检验报告。');
        return;
      }

      if (!/\.(docx|xlsx)$/i.test(file.name)) {
        toast('请选择 DOCX 或 XLSX 格式的检验文件。');
        return;
      }

      const reqId = ++this.currentReqId;
      this.candidatePage = 0;
      this.mappingPage = 0;
      this.activeStatusFilter = 'all';
      this.currentFile = file;
      this.currentPreview = null;
      if (window.v4Api) {
        window.v4Api.clearUploadKey(file);
      }
      if (typeof v3SelectedFile !== 'undefined') {
        v3SelectedFile = file.name;
      }

      // Update file selection UI on left (compatible with check.py expectation)
      const selectionDiv = document.getElementById('v3FileSelection');
      if (selectionDiv) {
        selectionDiv.hidden = false;
        selectionDiv.innerHTML = `<strong>${esc(file.name)}</strong> · 待解析核对 <button class="hub-button" onclick="v3OpenGroups()">登记样品 / 核对分组</button><p class="v3-note">新文件尚未入库，右侧展示解析候选明细与身份核对。</p>`;
      }

      // Render loading state in right preview
      this.renderLoadingRightPanel(file.name);

      try {
        const projectKey = typeof currentProjectKey !== 'undefined' ? currentProjectKey : 'IP166E';
        const context = this.getImportContext();

        // Request contract preview via API adapter
        const preview = await window.v4Api.createPreview(file, projectKey, context);
        // If another request was started while waiting, ignore this response
        if (reqId !== this.currentReqId) return;

        this.currentPreview = preview;
        this.configuration = null;
        this.configurationError = '';
        if (window.v4Api.mode === 'real') {
          try { this.configuration = await window.v4Api.getConfiguration(projectKey); }
          catch (error) { this.configurationError = error.message; }
          if (reqId !== this.currentReqId) return;
        }
        this.renderPreviewRightPanel(preview);
        this.refreshRecent(0);
      } catch (err) {
        if (reqId !== this.currentReqId) return;
        this.renderErrorRightPanel(err.message || '预览解析异常');
      }
    }

    renderEmptyRightPanel() {
      const col = document.getElementById('v4IntakeRightCol');
      if (!col) return;
      col.innerHTML = `
        <div class="card ai-intake-card v4-preview-empty-card">
          <div class="v4-preview-placeholder">
            <div class="v4-placeholder-icon">📑</div>
            <h3>等待选择检验文件</h3>
            <p>选择 .xlsx、.docx 文件后先确认项目，再查看文件范围和检测结果；确认导入前仅保存草稿。</p>
          </div>
        </div>
      `;
    }

    renderLoadingRightPanel(filename) {
      const col = document.getElementById('v4IntakeRightCol');
      if (!col) return;
      col.innerHTML = `
        <div class="card ai-intake-card v4-preview-card">
          <div class="card-header">
            <div class="card-title"><span>⏳</span><span>正在生成预览候选 · ${esc(filename)}</span></div>
          </div>
          <div class="card-body v4-loading-box">
            <div class="v4-spinner"></div>
            <p>正在执行规则校验、槽位比对与防覆盖检查...</p>
          </div>
        </div>
      `;
    }

    renderErrorRightPanel(errMsg) {
      const col = document.getElementById('v4IntakeRightCol');
      if (!col) return;
      col.innerHTML = `
        <div class="card ai-intake-card v4-preview-card">
          <div class="card-header">
            <div class="card-title"><span>⚠️</span><span>候选解析失败</span></div>
          </div>
          <div class="card-body">
            <div class="v4-drawer-alert v4-alert-error">
              <strong>解析或校验错误</strong>
              <p>${esc(errMsg)}</p>
            </div>
            <div class="v4-btn-group" style="margin-top:12px">
              <button type="button" class="btn btn-outline" onclick="v4Intake.resetFile()">重置文件</button>
            </div>
          </div>
        </div>
      `;
    }

    renderPreviewRightPanel(preview) {
      const col = document.getElementById('v4IntakeRightCol');
      if (!col) return;

      const candidates = preview.candidates || [];
      const selection = document.getElementById('v3FileSelection');
      if (selection) {
        selection.hidden = false;
        const submitted = candidates.filter(c => c.lifecycle === 'submitted').length;
        const remaining = candidates.filter(c => c.lifecycle === 'draft').length;
        selection.innerHTML = `<strong>${esc(preview.filename)}</strong><p class="v3-note">${submitted} 项已提交 · ${remaining} 项待处理。来源原件已保存；待审状态以检测台账为准。</p>`;
      }
      // Re-hydrate candidates with persistent local edits
      candidates.forEach(c => {
        const saved = this.candidateEdits.get(c.id);
        if (saved) {
          if (saved.intent) c.intent = saved.intent;
          if (saved.retest_ref_type) c.retest_ref_type = saved.retest_ref_type;
          if (saved.retest_ref_id) c.retest_ref_id = saved.retest_ref_id;
          if (saved.confirm_reason) c.confirm_reason = saved.confirm_reason;
          if (saved.identity_confirmed != null) c.identity_confirmed = saved.identity_confirmed;
          if (saved.source_number) c.source_number = saved.source_number;
        }
      });

      const counts = preview.counts || {
        new: 0,
        same_value: 0,
        empty: 0,
        different_value: 0,
        needs_review: 0
      };

      // Summary counts pills
      const countsHtml = `
        <div class="v4-counts-summary-bar">
          <button type="button" class="v4-count-pill pill-needs_review ${this.activeStatusFilter === 'needs_review' ? 'active' : ''}" onclick="v4Intake.filterStatus('needs_review')">
            <span class="pill-dot"></span> 待核对 <strong>${counts.needs_review || 0}</strong>
          </button>
          <button type="button" class="v4-count-pill pill-different_value ${this.activeStatusFilter === 'different_value' ? 'active' : ''}" onclick="v4Intake.filterStatus('different_value')">
            <span class="pill-dot"></span> 与旧值不同 <strong>${counts.different_value || 0}</strong>
          </button>
          <button type="button" class="v4-count-pill pill-new ${this.activeStatusFilter === 'new' ? 'active' : ''}" onclick="v4Intake.filterStatus('new')">
            <span class="pill-dot"></span> 新增候选 <strong>${counts.new || 0}</strong>
          </button>
          <button type="button" class="v4-count-pill pill-same_value ${this.activeStatusFilter === 'same_value' ? 'active' : ''}" onclick="v4Intake.filterStatus('same_value')">
            <span class="pill-dot"></span> 与已有值相同 <strong>${counts.same_value || 0}</strong>
          </button>
          <button type="button" class="v4-count-pill pill-empty ${this.activeStatusFilter === 'empty' ? 'active' : ''}" onclick="v4Intake.filterStatus('empty')">
            <span class="pill-dot"></span> 空白不更新 <strong>${counts.empty || 0}</strong>
          </button>
          ${this.activeStatusFilter !== 'all' ? `
            <button type="button" class="v3-text-button" onclick="v4Intake.filterStatus('all')">清除筛选 (显示全部)</button>
          ` : ''}
        </div>
      `;

      // Identity verification section: dynamically extract from candidate, never hardcode!
      const firstCand = candidates[0] || null;
      const sampleBatch = firstCand?.record?.key?.batch || firstCand?.interpreted_fields?.batch || firstCand?.raw_fields?.batch || '待核对';
      const sampleSpec = firstCand?.record?.key?.presentation || firstCand?.applied_defaults?.presentation || firstCand?.interpreted_fields?.presentation || firstCand?.raw_fields?.presentation || '待核对';
      const sampleType = firstCand?.record?.key?.sample_type || firstCand?.interpreted_fields?.sample_type || firstCand?.raw_fields?.sample_type || '待核对';
      const studyCond = `${firstCand?.record?.key?.study || firstCand?.interpreted_fields?.study || firstCand?.raw_fields?.study || '待核对'} · ${firstCand?.record?.key?.condition || firstCand?.interpreted_fields?.condition || firstCand?.raw_fields?.condition || '待核对'}`;
      const sourceNum = firstCand?.source_number || firstCand?.raw_fields?.source_number || '无（未在文档标头记载）';
      const detectionId = firstCand?.detection_id || '系统自动生成（确认时分配）';

      // base_revision is an integer! Format as r${base_revision} without calling .slice()
      const baseRevText = preview.base_revision != null ? `r${preview.base_revision}` : 'r0';

      const identitySection = `
        <div class="v4-identity-card">
          <div class="v4-identity-header">
            <h4>检测身份概览（以下逐项核对）</h4>
            <span class="v4-identity-tip">原文件无需修改补内部编号，系统按原文件编号与规则生成唯一内部标识</span>
          </div>
          <div class="v4-identity-grid">
            <div class="v4-identity-item">
              <span class="v4-meta-lbl">系统内部编号 (只读，系统生成)</span>
              <strong class="v4-code-text" title="${esc(detectionId)}">${esc(detectionId)}</strong>
            </div>
            <div class="v4-identity-item">
              <span class="v4-meta-lbl">原文件检测编号 (如有)</span>
              <span class="v4-meta-val">${esc(sourceNum)}</span>
            </div>
            <div class="v4-identity-item">
              <span class="v4-meta-lbl">供试样品批号</span>
              <strong class="v4-meta-val">${esc(sampleBatch)}</strong>
            </div>
            <div class="v4-identity-item">
              <span class="v4-meta-lbl">规格与样品类型</span>
              <span class="v4-meta-val">${esc(sampleType)} · ${esc(sampleSpec)}${firstCand?.applied_defaults?.presentation ? '（来自项目默认规格）' : ''}</span>
            </div>
            <div class="v4-identity-item">
              <span class="v4-meta-lbl">检测用途 / 场景</span>
              <span class="v4-meta-val">${esc(studyCond)}</span>
            </div>
            <div class="v4-identity-item">
              <span class="v4-meta-lbl">基础快照版本 (base_revision)</span>
              <span class="v4-code-text">${esc(baseRevText)}</span>
            </div>
          </div>
        </div>
      `;

      // Candidate groups by status
      const groupedCandidates = {};
      Object.keys(STATUS_CONFIG).forEach(st => groupedCandidates[st] = []);
      candidates.forEach(c => {
        if (!groupedCandidates[c.status]) groupedCandidates[c.status] = [];
        groupedCandidates[c.status].push(c);
      });

      let groupsHtml = '';
      const order = ['needs_review', 'different_value', 'new', 'same_value', 'empty'];
      const ordered = order.flatMap(st => groupedCandidates[st] || []).filter(c => this.activeStatusFilter === 'all' || c.status === this.activeStatusFilter);
      const pageCount = Math.max(1, Math.ceil(ordered.length / 80));
      this.candidatePage = Math.min(this.candidatePage, pageCount - 1);
      const pageIds = new Set(ordered.slice(this.candidatePage * 80, (this.candidatePage + 1) * 80).map(c => c.id));
      const paging = `<div class="v3-toolbar"><button type="button" class="btn btn-outline" ${this.candidatePage ? '' : 'disabled'} onclick="v4Intake.changePage(-1)">上一页</button><span>候选第 ${this.candidatePage+1} / ${pageCount} 页 · 共 ${ordered.length} 项（每页80项）</span><button type="button" class="btn btn-outline" ${this.candidatePage+1 < pageCount ? '' : 'disabled'} onclick="v4Intake.changePage(1)">下一页</button></div>`;
      order.forEach(st => {
        const list = (groupedCandidates[st] || []).filter(c => pageIds.has(c.id));
        if (!list.length) return;
        if (this.activeStatusFilter !== 'all' && this.activeStatusFilter !== st) return;

        const conf = STATUS_CONFIG[st];
        const isOpen = conf.defaultOpen || this.activeStatusFilter === st;

        groupsHtml += `
          <details class="v4-candidate-group-details" ${isOpen ? 'open' : ''}>
            <summary class="v4-group-summary group-${st}">
              <div class="v4-summary-left">
                <span class="v4-status-badge ${st}">${conf.label}</span>
                <span class="v4-group-title">${conf.desc}</span>
              </div>
              <span class="v4-group-count">${list.length} 项</span>
            </summary>
            <div class="v4-candidate-list">
              ${list.map(c => `
                <div class="v4-candidate-item ${c.status}" onclick="v4Intake.inspectCandidate('${esc(c.id)}')">
                  <div class="v4-cand-left">
                    <strong>${esc(this.configuration?.config.items.find(i => i.id === c.record?.key?.item)?.label || c.record?.key?.item || c.interpreted_fields?.test_item || c.raw_fields?.test_item || '检测项目')}</strong>
                    <div class="v4-cand-sub">
                      <span>时间点：${esc(c.record?.key?.month != null ? c.record.key.month + 'M' : c.interpreted_fields?.time_point || c.raw_fields?.time_point || '—')}</span>
                      <span>· 方法：${esc(c.record?.key?.method || c.interpreted_fields?.method || '—')}</span>
                      <span>· 位置：${esc(c.location.section)} R${c.location.row}C${c.location.column}</span>
                      ${c.applied_defaults?.presentation ? `<span>· 规格：${esc(c.applied_defaults.presentation)}（项目默认）</span>` : ''}
                      ${c.source_number ? `<span>· 编号：${esc(c.source_number)}</span>` : ''}
                    </div>
                    <div class="v4-cand-detail-text">${esc(c.detail)}</div>
                  </div>
                  <div class="v4-cand-right">
                    <div class="v4-cand-val-box">
                      <span class="v4-cand-val">${esc(c.record?.result?.raw_text || c.raw_fields?.result || '空白')}</span>
                      ${c.existing_record ? `
                        <div class="v4-cand-old-val">旧值: ${esc(c.existing_record.result?.raw_text || '—')}</div>
                      ` : ''}
                    </div>
                    <label onclick="event.stopPropagation()"><input type="checkbox" aria-label="确认此项检测身份" ${c.identity_confirmed ? 'checked' : ''} ${!c.allowed_actions?.some(a => ['confirm_initial','submit_review'].includes(a)) ? 'disabled' : ''} onchange="v4Intake.saveCandidateEdit('${esc(c.id)}',{identity_confirmed:this.checked})"> 已核对身份</label>
                    <button type="button" class="v3-text-button">核对 / 纠错 ➔</button>
                  </div>
                </div>
              `).join('')}
            </div>
          </details>
        `;
      });

      // Commit button bar respecting can_commit contract
      const canCommit = Boolean(preview.can_commit);
      const canConfirmFact = window.v4Api.can('confirm_fact');
      const canAdopt = window.v4Api.can('adopt_result');

      const commitBar = `
        <div class="v4-commit-action-box">
          <div class="v4-commit-note">
            ${!canCommit ? `
              <div class="v4-drawer-alert v4-alert-warning" style="margin:0 0 12px">
                <strong>${candidates.some(c => c.lifecycle === 'draft') ? '当前没有可提交的候选' : '本次预览已处理完成'}</strong>
                <p>${candidates.some(c => c.lifecycle === 'draft') ? '请处理待核对项。已解决的候选可单独确认，未解决项继续保留。' : '结果已提交；重复或复测结果的高级审核状态请在检测台账查看。'}</p>
              </div>
            ` : ''}
            <p class="v3-note">${candidates.filter(c => c.lifecycle === 'submitted').length} 项已提交 · ${candidates.filter(c => c.lifecycle === 'draft').length} 项待处理。未关联质量标准不影响保存检测结果，也不自动判定合格。勾选已核对身份的候选后提交。</p>
            <div class="v4-perm-status-line">
              当前操作者：<strong>${esc(window.v4Api.currentActor ? window.v4Api.currentActor.display_name : '未登录/无权限')}</strong>
              ${canAdopt ? '（具备高级确认与正式采用权限）' : '（实验员权限：首次结果可直接确认，重复/复测结果将提交进入待高级复核队列）'}
            </div>
          </div>
          <div class="v4-btn-group">
            <button type="button" class="btn btn-outline" onclick="v4Intake.resetFile()">清空当前文件</button>
            <button type="button" class="btn btn-primary" ${!canCommit ? 'disabled title="只读阶段不可提交"' : ''} onclick="v4Intake.submitConfirm()">
              <span>${canCommit ? '✓ 提交入库 / 确认候选' : '不可提交入库（只读预览）'}</span>
            </button>
          </div>
        </div>
      `;

      col.innerHTML = `
        <div class="card ai-intake-card v4-preview-card">
          <div class="card-header">
            <div class="card-title">
              <span>🤖</span>
              <span>初步解析与候选核对 · ${esc(preview.filename)}</span>
            </div>
            <span class="v3-tag">${preview.state}</span>
          </div>
          <div class="card-body" style="padding-top:14px">
            ${countsHtml}
            ${identitySection}
            ${(preview.parsing_summary || []).length ? `<details class="v3-details"><summary>文件解析范围（不等于正式入库）</summary><div class="v3-inner">${preview.parsing_summary.map(s => `<p class="v3-note"><strong>${esc(s.section)}</strong> · ${s.result_cells || 0} 个结果候选 · ${s.blank_cells || 0} 个空白位置 · ${s.outside_result_cells || 0} 个区域外单元格。${esc(s.note)}</p>`).join('')}</div></details>` : ''}
            ${this.configurationPanel(preview)}
            <div class="v4-candidates-container">
              ${paging}
              ${groupsHtml}
              ${paging}
            </div>
            ${commitBar}
          </div>
        </div>
      `;
      if (document.getElementById('v42SpecForm')) this.selectSpecification();
    }

    configurationPanel(preview) {
      if (window.v4Api?.mode !== 'real') return '';
      if (!this.configuration || this.configuration.config.project !== preview.project) {
        return `<p class="v3-note">项目配置未载入：${esc(this.configurationError || '请刷新预览')}</p><button type="button" class="btn btn-outline" onclick="v4Intake.reloadPreview()">刷新预览</button>`;
      }
      const cfg = this.configuration.config;
      if (this.mappingSelectionPreviewId !== preview.preview_id) {
        this.selectedMappingGroups.clear();
        this.mappingSelectionPreviewId = preview.preview_id;
      }
      const writable = window.v4Api.can('confirm_fact');
      const senior = window.v4Api.can('correct_result');
      const unknown = preview.candidates.filter(c => c.lifecycle === 'draft' && c.issue_code === 'unknown_item');
      const groups = new Map();
      for (const c of unknown) {
        const signature = this.mappingSignature(c);
        if (!groups.has(signature)) groups.set(signature, {candidate:c,count:0});
        groups.get(signature).count++;
      }
      const mappingGroups = [...groups.values()];
      for (const signature of this.selectedMappingGroups) {
        if (!groups.has(signature)) this.selectedMappingGroups.delete(signature);
      }
      const mappingPages = Math.max(1, Math.ceil(mappingGroups.length / 40));
      this.mappingPage = Math.max(0, Math.min(this.mappingPage, mappingPages - 1));
      return `<details class="v3-details" ${unknown.length ? 'open' : ''}><summary>检测项目映射与默认规格${unknown.length ? ` · ${unknown.length} 项待映射` : ''}</summary>
        <div class="v3-inner"><p class="v3-note">首次新增可由实验员确认；修改已有定义或默认规格需高级权限。配置保存后重新预览，检测结果仍需单独确认。无质量标准也可收集结果。</p>
        <div class="v43-merge-toolbar"><span id="v43MappingCount">已选择 ${this.selectedMappingGroups.size} 组、${this.selectedMappingCandidates(preview).length} 项</span><button type="button" class="btn btn-outline btn-sm" onclick="v4Intake.openMappingMerge()">处理选中名称</button></div>
        ${mappingGroups.slice(this.mappingPage * 40, (this.mappingPage + 1) * 40).map(({candidate:c,count}, index) => `<div class="v4-mapping-row"><strong>${esc(c.interpreted_fields?.test_item || c.corrections?.test_item || c.raw_fields.test_item)}</strong> · ${count} 个同项目/方法/单位候选
          <label><input type="checkbox" aria-label="选择归并 ${esc(c.interpreted_fields?.test_item || c.raw_fields.test_item)} · ${esc(c.interpreted_fields?.method || '方法未写明')} · ${esc(c.interpreted_fields?.unit || '单位未写明')}" ${this.selectedMappingGroups.has(this.mappingSignature(c)) ? 'checked' : ''} ${!writable ? 'disabled' : ''} onchange="v4Intake.toggleMappingGroup('${esc(c.id)}',this.checked)">归为同一检测项</label>
          <label>选择已有项目<select aria-label="映射项目 ${index + 1}" ${!writable ? 'disabled' : ''} onchange="v4Intake.mapItem('${esc(c.id)}',this.value)"><option value="">待映射</option>${cfg.items.map(i => `<option value="${esc(i.id)}">${esc(i.label)} · ${esc(i.method)} · ${esc(i.unit)}</option>`).join('')}</select></label>
          <button type="button" class="btn btn-outline" ${!writable ? 'disabled' : ''} onclick="v4Intake.prepareNewItem('${esc(c.id)}')">新增检测项目</button></div>`).join('')}
        <p class="v3-note">${mappingGroups.length} 组待映射 · 映射分组第 ${this.mappingPage + 1} / ${mappingPages} 页。同组超过500项时本次只处理前500项。</p>
        <button type="button" class="btn btn-outline btn-sm" ${this.mappingPage === 0 ? 'disabled' : ''} onclick="v4Intake.changeMappingPage(-1)">上一组页</button>
        <button type="button" class="btn btn-outline btn-sm" ${this.mappingPage + 1 >= mappingPages ? 'disabled' : ''} onclick="v4Intake.changeMappingPage(1)">下一组页</button>
        <form id="v43MergeMappingForm" class="v3-form v43-merge-form" onsubmit="v4Intake.mergeMappingGroups(event)">
          <strong>将选中的原始名称归到同一检测项</strong>
          <div id="v43MappingSelection" aria-live="polite">${this.mappingSelectionSummary(preview)}</div>
          <label>统一检测项目<select name="item_id" aria-label="统一检测项目" required><option value="">请选择</option>${cfg.items.map(i => `<option value="${esc(i.id)}">${esc(i.label)} · ${esc(i.method)} · ${esc(i.unit)}</option>`).join('')}</select></label>
          <p class="v3-note">没有合适项目时，先用下方表单新增一个统一检测项目，再执行归并。只修改本次上传映射，原名称、数值和来源坐标保留，不自动登记全局别名。缺失方法/单位沿用所选项目配置；已有方法不覆盖，单位不自动换算。同一检测身份出现多条结果，仍须逐项消歧，不能直接重复采用。</p>
          <label>归并理由<input name="reason" required maxlength="500" placeholder="例如：核对原文，两种名称均指-H与-HG缺失含量"></label>
          <label class="v43-inline-check"><input name="acknowledge" type="checkbox" required>已核对所选原始名称指向同一检测项</label>
          <button class="btn btn-primary" ${!writable ? 'disabled' : ''}>归并映射并重新预览</button>
          <p id="v43MergeMessage" role="status"></p>
        </form>
        <form id="v42ItemForm" class="v3-form" onsubmit="v4Intake.saveItem(event)">
          <label>维护检测项目<select name="existing" aria-label="维护检测项目" onchange="v4Intake.selectItem(this.value)"><option value="">新增项目</option>${cfg.items.map(i => `<option value="${esc(i.id)}">${esc(i.label)}</option>`).join('')}</select></label>
          <div class="v3-form-grid"><label>项目名称<input name="label" required maxlength="120"></label><label>方法<input name="method" required maxlength="120"></label><label>单位<input name="unit" maxlength="40"></label><label>方法版本<input name="method_version" value="not_reported" required maxlength="80"></label><label>结果类型<select name="result_type"><option value="numeric">数值</option><option value="qualitative">文字 / 定性</option></select></label><label>别名（逗号分隔）<input name="aliases" maxlength="500"></label></div>
          <label>确认理由<input name="reason" required maxlength="500"></label><button class="btn btn-primary" ${!writable ? 'disabled' : ''}>确认配置并重新预览</button><p class="v3-note" id="v42ItemPermission">${senior ? '可新增或批准修改已有项目。' : '可新增项目；已有项目仅可查看，修改须由高级人员确认。'}</p>
        </form>
        <details><summary>样品默认规格（只补文件缺失值）</summary><form id="v42SpecForm" class="v3-form" onsubmit="v4Intake.saveSpecification(event)"><label>样品类型<select name="sample_type" onchange="v4Intake.selectSpecification()"><option>DS</option><option>DP</option></select></label><label>规格<input name="presentation" required maxlength="120" placeholder="原液或已确认的浓度" value="${esc(cfg.presentation_defaults?.DS || '')}"></label><p class="v3-note">文件中的明确规格优先。浓度检测结果不会自动成为样品规格。已有默认值仅高级人员可修改。</p><label>确认理由<input name="reason" required maxlength="500"></label><button class="btn btn-primary" ${!writable ? 'disabled' : ''}>保存默认规格并重新预览</button></form></details>
        <button type="button" class="btn btn-outline" onclick="v4Intake.reloadPreview()">刷新预览</button><p id="v42ConfigMessage" role="status" aria-live="polite"></p></div></details>`;
    }

    selectItem(id) {
      const form = document.getElementById('v42ItemForm');
      const item = this.configuration.config.items.find(i => i.id === id);
      for (const key of ['label','method','unit','method_version','result_type','aliases']) {
        form.elements[key].value = item ? (key === 'aliases' ? item.aliases.join(', ') : item[key]) :
          (key === 'result_type' ? 'numeric' : key === 'method_version' ? 'not_reported' : '');
        form.elements[key].disabled = Boolean(item && !window.v4Api.can('correct_result'));
      }
      form.querySelector('button').disabled = !window.v4Api.can('confirm_fact') || Boolean(item && !window.v4Api.can('correct_result'));
    }

    changeMappingPage(delta) {
      this.mappingPage += delta;
      this.renderPreviewRightPanel(this.currentPreview);
    }

    prepareNewItem(candidateId) {
      const cand = this.currentPreview.candidates.find(c => c.id === candidateId);
      this.selectItem('');
      const form = document.getElementById('v42ItemForm');
      form.elements.existing.value = '';
      form.elements.label.value = cand.interpreted_fields?.test_item || cand.corrections?.test_item || cand.raw_fields.test_item || '';
      form.elements.method.value = cand.interpreted_fields?.method || cand.corrections?.method || cand.raw_fields.method || '';
      form.elements.unit.value = cand.interpreted_fields?.unit || cand.corrections?.unit || cand.raw_fields.unit || '';
      form.elements.label.focus();
    }

    selectSpecification() {
      const form = document.getElementById('v42SpecForm');
      const existing = this.configuration.config.presentation_defaults?.[form.elements.sample_type.value];
      form.elements.presentation.value = existing || '';
      form.querySelector('button').disabled = !window.v4Api.can('confirm_fact') || Boolean(existing && !window.v4Api.can('correct_result'));
    }

    async reloadPreview() {
      try { await this.refreshConfigurationPreview(this.currentPreview.preview_id); }
      catch (error) { toast(error.message); }
    }

    async refreshConfigurationPreview(previewId) {
      const fresh = await window.v4Api.getPreview(previewId);
      const config = await window.v4Api.getConfiguration(fresh.project);
      if (this.currentPreview?.preview_id !== previewId) return;
      this.currentPreview = fresh;
      this.configuration = config;
      this.candidateEdits.clear(); // Changed mapping requires explicit identity confirmation again.
      this.renderPreviewRightPanel(fresh);
    }

    async saveConfiguration(change) {
      const preview = this.currentPreview;
      const message = document.getElementById('v42ConfigMessage');
      if (this.configBusy) return;
      this.configBusy = true;
      try {
        const payload = {base_revision:preview.base_revision, ...change};
        const signature = JSON.stringify(payload);
        if (this.pendingConfigurationCommand?.signature !== signature) {
          this.pendingConfigurationCommand = {signature, command:{...payload,idempotency_key:crypto.randomUUID()}};
        }
        await window.v4Api.changeConfiguration(preview.project, this.pendingConfigurationCommand.command);
        this.pendingConfigurationCommand = null;
        await this.refreshConfigurationPreview(preview.preview_id);
        toast('配置已保存，请核对重新生成的候选；结果尚未提交。');
      } catch (error) {
        message.textContent = error.message + '；请刷新预览后核对再重试。';
      } finally { this.configBusy = false; }
    }

    saveItem(event) {
      event.preventDefault();
      const data = new FormData(event.target);
      const existing = this.configuration.config.items.find(i => i.id === data.get('existing'));
      const item = {id:existing?.id || data.get('label').trim(), label:data.get('label').trim(),
        method:data.get('method').trim(), unit:data.get('unit').trim(), method_version:data.get('method_version').trim(),
        result_type:data.get('result_type'), aliases:data.get('aliases').split(/[,，]/).map(x => x.trim()).filter(Boolean)};
      return this.saveConfiguration({item,reason:data.get('reason').trim()});
    }

    saveSpecification(event) {
      event.preventDefault();
      const data = new FormData(event.target);
      return this.saveConfiguration({sample_type:data.get('sample_type'),presentation:data.get('presentation').trim(),reason:data.get('reason').trim()});
    }

    async mapItem(candidateId, itemId) {
      if (!itemId || this.configBusy) return;
      const preview = this.currentPreview;
      const cand = preview.candidates.find(c => c.id === candidateId);
      this.configBusy = true;
      try {
        const matches = preview.candidates.filter(c => c.lifecycle === 'draft' && c.issue_code === 'unknown_item' && this.mappingSignature(c) === this.mappingSignature(cand)).slice(0,500);
        await window.v4Api.mapItems(preview.preview_id, {base_revision:preview.base_revision,
          idempotency_key:window.v4Api.generateIdempotencyKey('mapping', preview.preview_id + ':' + preview.base_revision + ':' + candidateId + ':' + itemId),
          reason:'人工核对同项目、方法及单位的本次检测映射', candidate_ids:matches.map(c => c.id), item_id:itemId});
        await this.refreshConfigurationPreview(preview.preview_id);
      } catch (error) { toast(error.message); }
      finally { this.configBusy = false; }
    }

    selectedMappingCandidates(preview) {
      return preview.candidates.filter(c => c.lifecycle === 'draft' && c.issue_code === 'unknown_item' &&
        this.selectedMappingGroups.has(this.mappingSignature(c)));
    }

    mappingSelectionSummary(preview) {
      const candidates = this.selectedMappingCandidates(preview);
      const descriptions = [...new Map(candidates.map(c => [this.mappingSignature(c), c.interpreted_fields || c.raw_fields])).values()];
      return `<p>已选择 ${descriptions.length} 组、${candidates.length} 项候选（可跨组页勾选；单次最多500项）。</p>${descriptions.length ? `<ul>${descriptions.map(f => `<li>${esc(f.test_item)} · 方法：${esc(f.method || '未写明')} · 单位：${esc(f.unit || '未写明')}</li>`).join('')}</ul>` : ''}`;
    }

    toggleMappingGroup(candidateId, checked) {
      const candidate = this.currentPreview?.candidates.find(c => c.id === candidateId);
      if (!candidate || candidate.lifecycle !== 'draft' || candidate.issue_code !== 'unknown_item') return;
      const signature = this.mappingSignature(candidate);
      if (checked) this.selectedMappingGroups.add(signature); else this.selectedMappingGroups.delete(signature);
      const node = document.getElementById('v43MappingSelection');
      if (node) node.innerHTML = this.mappingSelectionSummary(this.currentPreview);
      const count = document.getElementById('v43MappingCount');
      if (count) count.textContent = `已选择 ${this.selectedMappingGroups.size} 组、${this.selectedMappingCandidates(this.currentPreview).length} 项`;
    }

    openMappingMerge() {
      const form = document.getElementById('v43MergeMappingForm');
      form?.scrollIntoView({block:'center'});
      form?.elements.item_id.focus({preventScroll:true});
    }

    async mergeMappingGroups(event) {
      event.preventDefault();
      if (this.configBusy) return;
      const preview = this.currentPreview;
      const data = new FormData(event.target);
      const itemId = data.get('item_id');
      const reason = String(data.get('reason') || '').trim();
      const message = document.getElementById('v43MergeMessage');
      const matches = this.selectedMappingCandidates(preview);
      if (!matches.length || matches.length > 500 || !itemId || !reason || data.get('acknowledge') !== 'on') {
        message.textContent = matches.length > 500 ? '本次选择超过500项，请减少所选分组；大组可用逐组映射分批处理。本次未修改。' : '请勾选原始名称组、选择统一项目、填写理由并确认已核对。';
        return;
      }
      this.configBusy = true;
      message.textContent = '正在保存本次归并映射…';
      try {
        const payload = {base_revision:preview.base_revision,reason,candidate_ids:matches.map(c => c.id),item_id:itemId};
        await window.v4Api.mapItems(preview.preview_id, {...payload,
          idempotency_key:window.v4Api.generateIdempotencyKey('merge-mapping',preview.preview_id + ':' + JSON.stringify(payload))});
        this.selectedMappingGroups.clear();
        await this.refreshConfigurationPreview(preview.preview_id);
        toast(`已将 ${matches.length} 项候选归到同一检测项，请核对重新预览的身份及重复结果；尚未入库。`);
      } catch (error) { message.textContent = error.message + '；请刷新预览核对后重试。'; }
      finally { this.configBusy = false; }
    }

    mappingSignature(candidate) {
      const fields = candidate.interpreted_fields || candidate.raw_fields || {};
      return JSON.stringify([fields.test_item,fields.method || '',fields.unit || '']);
    }

    changePage(delta) {
      this.candidatePage = Math.max(0,this.candidatePage + delta);
      this.renderPreviewRightPanel(this.currentPreview);
    }

    filterStatus(status) {
      this.candidatePage = 0;
      this.activeStatusFilter = this.activeStatusFilter === status ? 'all' : status;
      if (this.currentPreview) {
        this.renderPreviewRightPanel(this.currentPreview);
      }
    }

    inspectCandidate(candidateId) {
      if (!this.currentPreview) return;
      const cand = this.currentPreview.candidates.find(c => c.id === candidateId);
      if (cand && window.v4SourceDrawer) {
        window.v4SourceDrawer.openCandidate(cand);
      }
    }

    resetFile(showToast = true) {
      this.currentReqId++;
      if (this.currentFile && window.v4Api) {
        window.v4Api.clearUploadKey(this.currentFile);
      }
      this.currentFile = null;
      this.currentPreview = null;
      this.candidateEdits.clear();
      const fileInput = document.getElementById('v3FileInput');
      if (fileInput) fileInput.value = '';
      const selectionDiv = document.getElementById('v3FileSelection');
      if (selectionDiv) {
        selectionDiv.hidden = true;
        selectionDiv.innerHTML = '';
      }
      this.renderEmptyRightPanel();
      if (showToast) {
        toast('已清空当前文件与预览状态。');
      }
    }

    async submitConfirm() {
      if (!this.currentPreview || !this.currentPreview.can_commit) {
        toast('当前候选尚未就绪或处于只读状态，无法提交。');
        return;
      }

      const preview = this.currentPreview;
      const candidates = preview.candidates || [];

      // Filter eligible candidates for confirmation:
      // must be lifecycle === 'draft' and status !== 'empty'
      // if needs_review, only submit if user explicitly chose intent or confirmed identity
      const eligible = candidates.filter(c => c.lifecycle === 'draft' && c.identity_confirmed === true &&
        (c.allowed_actions || []).some(a => ['confirm_initial','submit_review'].includes(a)));
      if (eligible.length > 20000) { toast('本次超过20000项，请按来源拆分文件。'); return; }

      if (!eligible.length) {
        toast('没有可提交的有效草稿候选。待核对项请先完成核对与意图选择。');
        return;
      }
      if (eligible.some(c => c.existing_record && !c.intent)) { toast('请先说明历史数据是更正、重复来源还是独立复测。'); return; }

      // Build ConfirmItem[] conforming to ConfirmRequest
      const items = eligible.map(c => {
        // Explicit candidate intent has highest priority, otherwise map from status
        const intent = c.intent || 'initial';
        let detId;

        if (intent === 'repeat_source') {
          detId = c.existing_detection_id || c.detection_id;
        } else if (intent === 'correction') {
          detId = c.adopted_detection_id || c.detection_id;
        } else {
          // 'initial' or 'retest'
          detId = c.detection_id;
        }

        const references = [];
        if (c.retest_ref_id) {
          const kind = (c.retest_ref_type || 'OTHER').toUpperCase();
          references.push({ kind: kind, number: c.retest_ref_id });
        }

        return {
          candidate_id: c.id,
          candidate_revision: c.candidate_revision || 1,
          detection_id: detId,
          identity_confirmed: c.identity_confirmed === true,
          intent: intent,
          reason: c.confirm_reason || '界面人工核对确认',
          references: references
        };
      });

      const confirmationScope = preview.preview_id + ':' + JSON.stringify({revision:preview.base_revision,items});
      const confirmCommand = {
        base_revision: preview.base_revision,
        idempotency_key: window.v4Api.generateIdempotencyKey('confirm', confirmationScope),
        items: items
      };

      try {
        const result = await window.v4Api.confirmPreview(preview.preview_id, confirmCommand);
        window.v4Api.clearIdempotencyKey('confirm', confirmationScope);

        const pendingIds = result.pending_confirmation_ids || [];
        const adoptedIds = result.result_version_ids || [];

        if (pendingIds.length > 0) {
          toast(`已提交高级确认！其中 ${adoptedIds.length - pendingIds.length} 项已采用，${pendingIds.length} 项（复测/重复）进入待高级复核队列。`);
        } else {
          toast(`确认入库成功！共 ${adoptedIds.length} 项已正式采用。`);
        }

        // Refresh preview and results
        const projectKey = preview.project || (typeof currentProjectKey !== 'undefined' ? currentProjectKey : 'IP166E');
        if (window.v4Api.mode === 'real') {
          try {
            const freshPreview = await window.v4Api.getPreview(preview.preview_id);
            this.currentPreview = freshPreview;
            this.renderPreviewRightPanel(freshPreview);
            this.refreshRecent();
          } catch (_) {}

          if (window.v4ResultCell) {
            await window.v4ResultCell.loadProjectCells(projectKey);
          }
        } else {
          // Mock mode: update state to submitted
          preview.can_commit = false;
          preview.state = 'committed';
          candidates.forEach(c => c.lifecycle = 'submitted');
          this.renderPreviewRightPanel(preview);
        }
      } catch (err) {
        toast(err.message || '提交确认失败');
      }
    }
  }

  global.v4Intake = new V4Intake();
  document.addEventListener('DOMContentLoaded', () => {
    global.v4Intake.init();
  });
})(typeof window !== 'undefined' ? window : this);
