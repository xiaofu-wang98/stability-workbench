// V4 Source Evidence & Decision Drawer Module
// Provides side drawer for candidate evidence review, quick error correction, and result cell traceability

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

  class V4SourceDrawer {
    constructor() {
      this.dialogId = 'v4SourceDrawer';
      this.currentCandidate = null;
      this.currentCell = null;
      this.ensureDom();
    }

    ensureDom() {
      if (document.getElementById(this.dialogId)) return;
      const html = `
        <dialog id="${this.dialogId}" class="v4-drawer-dialog" aria-labelledby="v4DrawerTitle">
          <div class="v4-drawer-content">
            <div class="v4-drawer-header">
              <div>
                <span class="v4-drawer-eyebrow">溯源核对与依据审查 · SOURCE EVIDENCE</span>
                <h2 id="v4DrawerTitle" class="v4-drawer-title">检测详情</h2>
              </div>
              <button type="button" class="hub-icon v4-drawer-close" aria-label="关闭抽屉" onclick="v4SourceDrawer.close()">✕</button>
            </div>
            <div id="v4DrawerBody" class="v4-drawer-body">
              <!-- Dynamic content -->
            </div>
          </div>
        </dialog>
      `;
      document.body.insertAdjacentHTML('beforeend', html);
      const dialog = document.getElementById(this.dialogId);
      dialog.addEventListener('click', (e) => {
        const rect = dialog.getBoundingClientRect();
        if (
          e.clientX < rect.left ||
          e.clientX > rect.right ||
          e.clientY < rect.top ||
          e.clientY > rect.bottom
        ) {
          this.close();
        }
      });
    }

    openCandidate(candidate) {
      this.ensureDom();
      this.currentCandidate = candidate;
      this.currentCell = null;
      const body = document.getElementById('v4DrawerBody');
      const title = document.getElementById('v4DrawerTitle');
      const interpreted = candidate.interpreted_fields || candidate.raw_fields || {};
      const values = {...interpreted, ...(candidate.corrections || {}), ...(candidate.record?.key || {})};
      title.textContent = `${interpreted.test_item || candidate.record?.key?.item || '检测项目'} · 查看 / 修改`;

      const canAdopt = window.v4Api && window.v4Api.can('adopt_result');
      const canReviewDup = window.v4Api && window.v4Api.can('review_duplicate');
      const canConfirmFact = window.v4Api && window.v4Api.can('confirm_fact');

      const isFirstNew = candidate.status === 'new';
      const hasHistory = Boolean(candidate.existing_record);
      const isWithdrawn = candidate.lifecycle === 'withdrawn';
      const isSubmitted = candidate.lifecycle === 'submitted';

      let actionButtonsHtml = '';
      if (window.v4Api.mode === 'mock') {
        actionButtonsHtml = `
          <div class="v4-drawer-alert v4-alert-warning">
            <strong>⚠️ 当前环境为 Mock 演练模式</strong>
            <p>可直接模拟提交或演练复核。若切换为真实模式将执行实际 HTTP 契约调用。</p>
          </div>
          <div class="v4-drawer-actions">
            ${isFirstNew ? `
              <button type="button" class="btn btn-primary" onclick="v4SourceDrawer.mockConfirmCandidate('initial')">确认首次结果入库</button>
            ` : `
              <button type="button" class="btn btn-outline" onclick="v4SourceDrawer.mockConfirmCandidate('retest')">提交高级确认</button>
              ${canAdopt ? '<button type="button" class="btn btn-primary" onclick="v4SourceDrawer.mockReviewCandidate(\'adopt\')">高级确认 / 正式采用</button>' : ''}
            `}
            <button type="button" class="hub-button" onclick="v4SourceDrawer.close()">关闭</button>
          </div>
        `;
      } else {
        // Real mode
        if (window.v4Api.authError) {
          actionButtonsHtml = `
            <div class="v4-drawer-alert v4-alert-error">
              <strong>❌ 权限认证失败</strong>
              <p>${esc(window.v4Api.authError)}。当前操作者无写权限。</p>
            </div>
            <div class="v4-drawer-actions">
              <button type="button" class="hub-button" onclick="v4SourceDrawer.close()">关闭</button>
            </div>
          `;
        } else if (isWithdrawn) {
          actionButtonsHtml = `
            <div class="v4-drawer-alert v4-alert-info">
              <strong>ℹ️ 该候选已被撤回 (withdrawn)</strong>
            </div>
            <div class="v4-drawer-actions">
              <button type="button" class="hub-button" onclick="v4SourceDrawer.close()">关闭</button>
            </div>
          `;
        } else {
          actionButtonsHtml = `
            <div class="v4-drawer-actions">
              ${candidate.allowed_actions && candidate.allowed_actions.includes('withdraw') ? `
                <button type="button" class="btn btn-outline" onclick="v4SourceDrawer.withdrawCandidate()">撤回此候选</button>
              ` : ''}
              <button type="button" class="hub-button" onclick="v4SourceDrawer.close()">关闭</button>
            </div>
          `;
        }
      }

      body.innerHTML = `
        <div class="v4-drawer-section">
          <div class="v4-drawer-meta-grid">
            <div><span class="v4-meta-lbl">样品批号</span><strong>${esc(values.batch || '待核对')}</strong></div>
            <div><span class="v4-meta-lbl">时间点</span><strong>${esc(candidate.record?.key?.month != null ? candidate.record.key.month + 'M' : interpreted.time_point || candidate.raw_fields?.time_point || '—')}</strong></div>
            <div><span class="v4-meta-lbl">分析方法</span><strong>${esc(values.method || '待核对')}</strong></div>
            <div><span class="v4-meta-lbl">状态</span><span class="v4-status-badge ${esc(candidate.status)}">${esc(({new:'可直接导入',needs_review:'待处理',same_value:'与历史相同',different_value:'与历史不同',empty:'空白不更新'})[candidate.status])}</span></div>
          </div>
        </div>

        <div class="v4-evidence-compare">
          <div class="v4-evidence-card incoming">
            <span class="v4-card-tag">本次解析候选 (Incoming)</span>
            <div class="v4-val-large">${esc(candidate.record?.result?.raw_text || candidate.raw_fields?.result || '—')}</div>
            <div class="v4-card-meta">
              <div>文件位置：${esc(candidate.location?.format || 'xlsx')} / ${esc(candidate.location?.section || 'Sheet')} (第 ${candidate.location?.row || 0} 行，第 ${candidate.location?.column || 0} 列)</div>
              <div>原始文本：${esc(candidate.record?.result?.raw_text || candidate.raw_fields?.result || '')}</div>
              <div>${candidate.lifecycle === 'submitted' ? '已提交，修改请从检测台账进入' : '尚未导入，应用修改后需在文件汇总处确认'}</div>
              <div style="margin-top:6px;">
                ${candidate.location.format === 'xlsx' && window.v4Api?.mode === 'real'
              ? `<a class="hub-button" href="/api/v1/sources/${encodeURIComponent(candidate.id)}/file" download>下载冻结原始Excel（按工作表和原格坐标核对）</a>`
              : `<button type="button" class="hub-button" onclick="v4SourceDrawer.close(); if(window.v4SourceTables) window.v4SourceTables.locateCandidate(v4SourceDrawer.currentCandidate);">在来源全表中高亮定位</button>`}
              </div>
            </div>
          </div>

          ${candidate.existing_record ? `
            <div class="v4-evidence-card existing">
              <span class="v4-card-tag">当前已有有效记录 (Existing Effective)</span>
              <div class="v4-val-large">${esc(candidate.existing_record.result?.raw_text || '—')}</div>
              <div class="v4-card-meta">
                <div>来源方法版本：${esc(candidate.existing_record.key?.method_version || '未记录')}</div>
                <div>已有检测编号：${esc(candidate.existing_detection_id || '—')}</div>
                <div>状态：正式受控有效值</div>
              </div>
            </div>
          ` : `
            <div class="v4-evidence-card empty">
              <span class="v4-card-tag">当前已有记录</span>
              <div class="v4-val-large" style="color:var(--text-muted)">无历史记录</div>
              <div class="v4-card-meta">当前槽位在系统中尚未有正式归档记录（属于首次录入候选）</div>
            </div>
          `}
        </div>

        <div class="v4-drawer-section">
          <h4 class="v4-section-title">核对详情与系统判定</h4>
          <p class="v4-drawer-desc">${esc(candidate.detail || '暂无说明')}</p>
        </div>

        <details class="v4-drawer-section"><summary>原格与字段来源（只读）</summary><pre style="white-space:pre-wrap;overflow-wrap:anywhere;font-size:11px">${esc(JSON.stringify({fields:candidate.raw_fields,evidence:candidate.evidence || {},comment:candidate.comment},null,2))}</pre></details>
        <!-- 身份核对与快速纠错表单 -->
        <div class="v4-drawer-section v4-correction-box">
          <h4 class="v4-section-title">修改这条数据</h4>
          <p class="v4-drawer-desc" style="margin-bottom:8px">
            系统如实展示识别来源。若解析有偏差，可在下方快速修正样品批号、规格、条件、时间点或编号，修改后将提交后端重新判重。原文件无需修改。
          </p>
          <form class="v4-correction-form" id="v4CorrectionForm" onsubmit="v4SourceDrawer.applyCorrection(event)">
            <details><summary>修改批号、规格、条件或检测项目（仅本条）</summary><div class="v4-form-grid">
              <label class="v4-field">
                <span>系统候选标识 (只读)</span>
                <input type="text" readonly value="${esc(candidate.id)}" class="v4-input-readonly">
              </label>
              <label class="v4-field">
                <span>原文件检测编号 (source_number)</span>
                <input type="text" name="source_number" value="${esc(candidate.source_number || candidate.raw_fields?.source_number || '')}" placeholder="文档中记载的检测编号" class="v4-input">
              </label>
              <label class="v4-field">
                <span>供试样品批号</span>
                <input type="text" name="batch" value="${esc(values.batch || '')}" class="v4-input">
              </label>
              <label class="v4-field">
                <span>剂型与规格</span>
                <input type="text" name="presentation" value="${esc(values.presentation || candidate.applied_defaults?.presentation || '')}" class="v4-input">
              </label>
              <label class="v4-field">
                <span>考察用途 / 场景</span>
                <select name="study" class="v4-select"><option value="">待核对</option>
                  <option value="long_term" ${values.study === 'long_term' ? 'selected' : ''}>长期稳定性 (long_term)</option>
                  <option value="accelerated" ${values.study === 'accelerated' ? 'selected' : ''}>加速试验 (accelerated)</option>
                  <option value="stress" ${values.study === 'stress' ? 'selected' : ''}>应力试验 (stress)</option>
                  <option value="release" ${values.study === 'release' ? 'selected' : ''}>出厂放行 (release)</option>
                </select>
              </label>
              <label class="v4-field">
                <span>考察条件</span>
                <input type="text" name="condition" value="${esc(values.condition || '')}" class="v4-input">
              </label>
              <label class="v4-field">
                <span>考察时间点（月，如3M）</span>
                <input type="text" name="time_point" value="${esc(candidate.record?.key?.month != null ? candidate.record.key.month + 'M' : interpreted.time_point || '')}" class="v4-input">
              </label>
              <label class="v4-field"><span>样品类型</span><select name="sample_type" class="v4-select"><option value="">待核对</option><option value="DS" ${values.sample_type === 'DS' ? 'selected' : ''}>DS 原液</option><option value="DP" ${values.sample_type === 'DP' ? 'selected' : ''}>DP 制剂</option></select></label>
              <label class="v4-field"><span>研究 / 方案编号</span><input name="study_id" class="v4-input" value="${esc(values.study_id || '')}"></label>
              <label class="v4-field"><span>检测项目（已有项目ID或名称）</span><input name="test_item" class="v4-input" value="${esc(candidate.record?.key?.item || values.test_item || '')}"></label>
              <label class="v4-field"><span>方法</span><input name="method" class="v4-input" value="${esc(values.method || '')}"></label>
              <label class="v4-field"><span>方法版本 / 编号</span><input name="method_version" class="v4-input" value="${esc(values.method_version || '')}"></label>
              <label class="v4-field"><span>单位</span><input name="unit" class="v4-input" value="${esc(candidate.record?.result?.unit || values.unit || '')}"></label>
            </div></details><div class="v4-form-grid">
              <label class="v4-field"><span>修改后的结果（原文保留）</span><textarea name="result" class="v4-input" rows="3">${esc(candidate.corrections?.result ?? candidate.raw_fields?.result ?? '')}</textarea></label>
              ${candidate.comment || ['source_comment','formula_result'].includes(candidate.issue_code) ? '<label class="v4-field"><span><input type="checkbox" name="acknowledge_source_warning" value="yes"> 已查看来源说明，明确确认上方结果</span></label>' : ''}
              ${hasHistory ? `<label class="v4-field">
                <span>检测意图确认 (Intent)</span>
                <select name="intent" class="v4-select">
                  <option value="">请选择本次变动含义</option>
                  <option value="retest" ${candidate.intent === 'retest' ? 'selected' : ''}>独立复测 (retest)</option>
                  <option value="repeat_source" ${candidate.intent === 'repeat_source' ? 'selected' : ''}>重复登记/同来源 (repeat_source)</option>
                  <option value="correction" ${candidate.intent === 'correction' ? 'selected' : ''}>正式更正 (correction)</option>
                </select>
              </label>` : '<input name="intent" type="hidden" value="initial">'}
              <label class="v4-field">
                <span>修正原因 (Reason)</span>
                <input type="text" name="reason" required value="${esc(candidate.confirm_reason || '')}" placeholder="简要说明修改了什么、为什么修改" class="v4-input">
              </label>
              </div><details><summary>关联调查或其他编号（可选，不影响普通修改）</summary><div class="v4-form-grid"><label class="v4-field">
                <span>复测关联类型 (Reference Kind)</span>
                <select name="retest_ref_type" class="v4-select">
                  <option value="">不关联调查</option>
                  <option value="OOE" ${candidate.retest_ref_type === 'OOE' ? 'selected' : ''}>OOE (趋势异常调查)</option>
                  <option value="OOS" ${candidate.retest_ref_type === 'OOS' ? 'selected' : ''}>OOS (超标调查)</option>
                  <option value="CC" ${candidate.retest_ref_type === 'CC' ? 'selected' : ''}>CC (变更控制)</option>
                  <option value="OTHER" ${candidate.retest_ref_type === 'OTHER' ? 'selected' : ''}>OTHER (其他)</option>
                </select>
              </label>
              <label class="v4-field">
                <span>复测关联编号 (Reference Number)</span>
                <input type="text" name="retest_ref_id" value="${esc(candidate.retest_ref_id || '')}" placeholder="如 OOE-2026-009" class="v4-input">
              </label>
            </div></details>
            <div class="v4-correction-actions">
              <button type="submit" class="hub-button" ${candidate.lifecycle === 'draft' ? '' : 'disabled'}>应用修改并重新检查（尚未导入）</button>
            </div>
          </form>
        </div>

        <div class="v4-perm-note">
          ${canAdopt ? '<span class="v4-badge-perm">具备高级确认与正式更正权限 (adopt_result / correct_result)</span>' : '<span class="v4-badge-perm v4-perm-limited">当前为实验员普通权限：首次结果可确认，重复/复测结果只能“提交高级确认”</span>'}
        </div>

        ${actionButtonsHtml}
      `;

      document.getElementById(this.dialogId).showModal();
    }

    async applyCorrection(event) {
      event.preventDefault();
      const form = event.target;
      const data = Object.fromEntries(new FormData(form));

      if (!this.currentCandidate) return;
      const c = this.currentCandidate;

      // Prepare fields dict
      const fields = {};
      if (data.batch) fields.batch = data.batch.trim();
      if (data.presentation) fields.presentation = data.presentation.trim();
      if (data.study) fields.study = data.study;
      if (data.condition) fields.condition = data.condition.trim();
      if (data.time_point) fields.time_point = data.time_point.trim();
      if ((data.source_number || '').trim() !== (c.source_number || '')) fields.source_number = (data.source_number || '').trim();
      for (const name of ['sample_type','study_id','test_item','method','method_version','unit','result']) {
        if (data[name] !== undefined) fields[name] = data[name].trim();
      }

      c.retest_ref_id = (data.retest_ref_id || '').trim();
      c.retest_ref_type = data.retest_ref_type || 'OTHER';
      if (data.intent) {
        c.intent = data.intent;
      }
      if (data.reason) {
        c.confirm_reason = data.reason.trim();
      }

      // Persist in v4Intake so re-fetching preview retains reference numbers and intent
      if (window.v4Intake) {
        window.v4Intake.saveCandidateEdit(c.id, {
          retest_ref_id: c.retest_ref_id,
          retest_ref_type: c.retest_ref_type,
          intent: c.intent,
          confirm_reason: c.confirm_reason,
          source_number: data.source_number ? data.source_number.trim() : c.source_number
        });
      }

      if (window.v4Api.mode === 'real') {
        const preview = window.v4Intake.currentPreview;
        const baseRev = preview && preview.base_revision != null ? preview.base_revision : 0;
        const candRev = c.candidate_revision || 1;

        const editCommand = {
          candidate_revision: candRev,
          base_revision: baseRev,
          idempotency_key: window.v4Api.generateIdempotencyKey('edit', c.id),
          reason: data.reason ? data.reason.trim() : '界面核对纠错',
          fields: fields,
          acknowledge_source_warning: data.acknowledge_source_warning === 'yes'
        };

        try {
          await window.v4Api.editCandidate(c.id, editCommand);
          window.v4Intake.candidateEdits.delete(c.id);
          window.v4Intake.saveCandidateEdit(c.id, {identity_confirmed:Boolean(c.existing_record && c.intent),intent:c.intent,confirm_reason:c.confirm_reason,
            retest_ref_id:c.retest_ref_id,retest_ref_type:c.retest_ref_type});

          // Re-fetch preview from backend to obtain re-evaluated status
          if (preview && preview.preview_id) {
            const freshPreview = await window.v4Api.getPreview(preview.preview_id);
            window.v4Intake.currentPreview = freshPreview;
            window.v4Intake.renderPreviewRightPanel(freshPreview);

            const updatedCand = freshPreview.candidates.find(item => item.id === c.id);
            if (updatedCand) {
              this.openCandidate(updatedCand);
            }
          }
          toast('纠错已保存，后端已重新比对槽位与状态。');
        } catch (err) {
          toast(err.message || '纠错提交失败');
        }
      } else {
        // Mock mode: update candidate in-memory
        if (c.record && c.record.key) {
          if (fields.batch) c.record.key.batch = fields.batch;
          if (fields.presentation) c.record.key.presentation = fields.presentation;
          if (fields.study) c.record.key.study = fields.study;
          if (fields.condition) c.record.key.condition = fields.condition;
          if (fields.time_point) {
            const m = parseInt(fields.time_point);
            c.record.key.month = isNaN(m) ? null : m;
          }
        }
        if (fields.source_number) {
          c.source_number = fields.source_number;
          if (c.raw_fields) c.raw_fields.source_number = fields.source_number;
        }

        toast('已保存修正！原提取值与修正前后差异已记录。');
        this.openCandidate(c);

        if (window.v4Intake && window.v4Intake.currentPreview) {
          window.v4Intake.renderPreviewRightPanel(window.v4Intake.currentPreview);
        }
      }
    }

    async withdrawCandidate() {
      if (!this.currentCandidate) return;
      const c = this.currentCandidate;

      if (window.v4Api.mode === 'real') {
        const preview = window.v4Intake.currentPreview;
        const baseRev = preview && preview.base_revision != null ? preview.base_revision : 0;
        const withdrawCommand = {
          base_revision: baseRev,
          idempotency_key: window.v4Api.generateIdempotencyKey('withdraw'),
          reason: '用户主动撤回'
        };

        try {
          await window.v4Api.withdrawCandidate(c.id, withdrawCommand);
          if (preview && preview.preview_id) {
            const freshPreview = await window.v4Api.getPreview(preview.preview_id);
            window.v4Intake.currentPreview = freshPreview;
            window.v4Intake.renderPreviewRightPanel(freshPreview);
          }
          toast('候选已撤回。');
          this.close();
        } catch (err) {
          toast(err.message || '撤回失败');
        }
      } else {
        c.lifecycle = 'withdrawn';
        toast('Mock 模式：候选已撤回。');
        this.openCandidate(c);
      }
    }

    mockConfirmCandidate(intent) {
      toast(intent === 'initial' ? '首次结果已确认（Mock 演练）' : '已提交高级确认请求（待复核）');
      this.close();
    }

    mockReviewCandidate(action) {
      toast(`高级复核完成：执行 ${action}（Mock 演练）`);
      this.close();
    }

    async openResultCell(cellData) {
      this.ensureDom();
      this.currentCell = cellData;
      this.currentCandidate = null;
      this.loadedSource = null;
      const body = document.getElementById('v4DrawerBody');
      const title = document.getElementById('v4DrawerTitle');

      const itemName = cellData.slot_key?.item || cellData.item || '检测项目';
      const timePoint = cellData.slot_key?.month != null ? cellData.slot_key.month + 'M' : cellData.time_point || '';
      title.textContent = `${itemName} (${timePoint}) · 结果溯源与正式采用`;

      const canAdopt = window.v4Api && window.v4Api.can('adopt_result');
      const canReview = window.v4Api && window.v4Api.can('review_duplicate');
      const isPending = Boolean(cellData.display?.pending_confirmation);

      // Attempt to load source metadata if source_id is available
      const sourceId = cellData.display?.source_ids?.[0] || cellData.adopted?.source_ids?.[0];
      let sourceInfoHtml = '';
      if (sourceId) {
        try {
          const src = await window.v4Api.getSource(sourceId);
          this.loadedSource = src;
          src.coordinates = src.raw?.location || src.coordinates;
          if (src.kind === 'worksheet_entry') {
            sourceInfoHtml = `<div class="v4-drawer-section">
              <h4 class="v4-section-title">研究工作表录入</h4>
              <div class="v4-drawer-meta-grid">
                <div><span class="v4-meta-lbl">录入人</span><span>${esc(src.submitted_by)}</span></div>
                <div><span class="v4-meta-lbl">录入时间</span><span>${esc(src.submitted_at)}</span></div>
                <div><span class="v4-meta-lbl">计划版本</span><span>${esc(src.plan_version)}</span></div>
                <div><span class="v4-meta-lbl">前一版本</span><span>${esc(src.previous_version_id || '—')}</span></div>
              </div>
              <p>${esc(src.reason || '')}</p>
            </div>`;
          } else {
            sourceInfoHtml = `
              <div class="v4-drawer-section">
                <h4 class="v4-section-title">📄 来源文档与定位溯源</h4>
                <div class="v4-drawer-meta-grid">
                  <div><span class="v4-meta-lbl">来源文件</span><strong>${esc(src.filename || '—')}</strong></div>
                  <div><span class="v4-meta-lbl">文件指纹 (SHA-256)</span><span class="v4-code-text" title="${esc(src.source_hash || '')}">${esc(src.source_hash ? src.source_hash.slice(0, 16) + '...' : '—')}</span></div>
                  <div><span class="v4-meta-lbl">表格坐标</span><span>${esc(src.coordinates ? `${src.coordinates.section || 'Sheet'} R${src.coordinates.row}C${src.coordinates.column}` : '—')}</span></div>
                  <div><span class="v4-meta-lbl">上传序号</span><span>第 ${esc(src.upload_sequence || 1)} 次上传</span></div>
                </div>
                <div style="margin-top:8px;">
                  ${src.coordinates?.format === 'xlsx' || /\.xlsx$/i.test(src.filename || '') ? `
                  <a class="btn btn-outline btn-sm" href="/api/v1/sources/${encodeURIComponent(sourceId)}/file" download>下载原始 Excel（按上方坐标核对）</a>
                  ` : `
                  <button type="button" class="btn btn-outline btn-sm" style="font-size:11px;padding:3px 8px;" onclick="v4SourceDrawer.close(); if(window.v4SourceTables) window.v4SourceTables.locateSource(v4SourceDrawer.loadedSource.source_hash, v4SourceDrawer.loadedSource.coordinates);">
                    📑 在来源全表中高亮查看原始表格
                  </button>
                  `}
                </div>
              </div>
            `;
          }
        } catch (_) {}
      }

      // Review actions if pending and senior permissions available
      let reviewActionsHtml = '';
      if (isPending && (canReview || canAdopt)) {
        reviewActionsHtml = `
          <div class="v4-drawer-section" style="background:#fef2f2;border:1px solid #fecaca;border-radius:6px;padding:12px">
            <h4 class="v4-section-title" style="color:#b91c1c">⚖️ 高级复核与正式采用操作 (Senior Review)</h4>
            <p class="v4-drawer-desc" style="margin-bottom:10px">
              当前展示为待确认复测事实。作为具备复核权限的角色，您可以选择：
            </p>
            <div class="v4-btn-group">
              <button type="button" class="btn btn-outline" onclick="v4SourceDrawer.executeReview('${esc(cellData.display?.result_version_id)}', 'confirm_fact')">
                仅确认事实 (confirm_fact)
              </button>
              <button type="button" class="btn btn-primary" onclick="v4SourceDrawer.executeReview('${esc(cellData.display?.result_version_id)}', 'adopt')">
                正式采用为基线 (adopt)
              </button>
              <button type="button" class="btn btn-outline" style="color:#dc2626" onclick="v4SourceDrawer.executeReview('${esc(cellData.display?.result_version_id)}', 'reject')">
                排除拒绝 (reject)
              </button>
            </div>
          </div>
        `;
      }
      if (!isPending && canAdopt && this.loadedSource?.intent === 'retest' &&
          cellData.display?.result_version_id !== cellData.adopted?.result_version_id) {
        reviewActionsHtml = `<div class="v4-drawer-section">
          <button type="button" class="btn btn-primary" onclick="v4SourceDrawer.executeReview('${esc(cellData.display.result_version_id)}', 'adopt')">正式采用此复测</button>
        </div>`;
      }

      const worksheetEntry = this.loadedSource?.kind === 'worksheet_entry' ||
        cellData.adopted?.source_ids?.some(id => id.startsWith('entry_result_'));
      body.innerHTML = `
        <div class="v4-drawer-section">
          <div class="v4-drawer-meta-grid">
            <div><span class="v4-meta-lbl">槽位标识</span><strong class="v4-code-text" title="${esc(cellData.slot_id)}">${esc(cellData.slot_id ? cellData.slot_id.slice(0, 16) + '...' : '—')}</strong></div>
            <div><span class="v4-meta-lbl">检测项目</span><strong>${esc(itemName)}</strong></div>
            <div><span class="v4-meta-lbl">时间点</span><strong>${esc(timePoint || '—')}</strong></div>
            <div><span class="v4-meta-lbl">供试样品批号</span><strong>${esc(cellData.slot_key?.batch || cellData.batch || '—')}</strong></div>
            <div><span class="v4-meta-lbl">分析方法</span><span>${esc(cellData.slot_key?.method || '—')}</span></div>
            <div><span class="v4-meta-lbl">方法版本</span><span>${esc(cellData.slot_key?.method_version || 'not_reported')}</span></div>
          </div>
        </div>

        <div class="v4-evidence-compare">
          <div class="v4-evidence-card ${isPending ? 'pending-cell' : 'incoming'}">
            <span class="v4-card-tag">当前展示结果 (Display)</span>
            <div class="v4-val-large ${isPending ? 'text-pending' : ''}">
              ${esc(cellData.display?.text || '—')}${isPending ? ' <small class="v4-badge-pending">（待确认）</small>' : ''}
            </div>
            <div class="v4-card-meta">
              <div>结果版本：${esc(cellData.display?.result_version_id || '—')}</div>
              <div>独立检测编号：${esc(cellData.display?.detection_id || '—')}</div>
              <div>来源：${esc(cellData.display?.source_ids?.join(', ') || '—')}</div>
              <div>状态：${isPending ? '⚠️ 待确认事实（尚未正式采用）' : '已归档'}</div>
            </div>
          </div>

          <div class="v4-evidence-card adopted-cell">
            <span class="v4-card-tag">正式采用记录 (Adopted / Effective)</span>
            <div class="v4-val-large text-adopted">
              ${esc(cellData.adopted?.text || '（暂无正式采用值）')}
            </div>
            <div class="v4-card-meta">
              <div>结果版本：${esc(cellData.adopted?.result_version_id || '—')}</div>
              <div>独立检测编号：${esc(cellData.adopted?.detection_id || '—')}</div>
              <div>来源：${esc(cellData.adopted?.source_ids?.join(', ') || '—')}</div>
              <div>状态：正式受控基线</div>
            </div>
          </div>
        </div>

        ${sourceInfoHtml}

        ${cellData.adopted?.source_ids?.some(id => id.startsWith('entry_result_')) ?
          '<div class="v4-drawer-section">请打开研究工作表更正此结果。</div>' :
          cellData.adopted?.result_version_id && window.v4Api.can('confirm_fact') ? `<div class="v4-drawer-section"><h4>修改正式记录的数据</h4><p>填写新值和简短说明即可，无需OOS/OOE编号。保存后为待高级确认的新版本，原正式值继续保留。</p><form onsubmit="v4SourceDrawer.saveDisplayChange(event)"><label class="v4-field"><span>修改后的结果（保持原结果单位）</span><textarea class="v4-input" name="result" required>${esc(this.loadedSource?.corrections?.result ?? this.loadedSource?.raw?.fields?.result ?? '')}</textarea></label><label class="v4-field"><span>修改说明</span><input class="v4-input" name="reason" required placeholder="例如：原始录入小数点有误，已核对原件"></label><button class="btn btn-primary">保存修改并提交高级确认</button><p id="v44DisplayChangeStatus" role="status"></p></form></div>` : ''}

        <div class="v4-drawer-section">
          <h4 class="v4-section-title">审计与权限规则</h4>
          <p class="v4-drawer-desc">
            ${worksheetEntry
              ? '在研究工作表更正结果时，填写原因后保存新版本并更新正式采用值。独立复测需明确采用。'
              : '来源核对页展示最新检测事实，待确认值以红色警示。首次无冲突的结果由实验员核对后采用，来源重复提交须复核。'}
          </p>
          <div class="v4-perm-note">
            ${canAdopt ? '<span class="v4-badge-perm">您具备结果复核和采用权限</span>' : ''}
          </div>
        </div>

        ${reviewActionsHtml}

        <div class="v4-drawer-alert v4-alert-info">
          <span>ℹ️ 展示 “${esc(cellData.display?.text || '')}${isPending ? '（待确认）' : ''}” 与 正式采用 “${esc(cellData.adopted?.text || '—')}” 保持清晰隔离，绝不自动覆盖正式值。</span>
        </div>

        <div class="v4-drawer-actions">
          <button type="button" class="hub-button" onclick="v4SourceDrawer.close()">关闭</button>
        </div>
      `;

      document.getElementById(this.dialogId).showModal();
    }

    async saveDisplayChange(event) {
      event.preventDefault();
      const form = event.target;
      const button = form.querySelector('button'); button.disabled = true;
      const data = Object.fromEntries(new FormData(form));
      const version = this.currentCell.adopted.result_version_id;
      const payload = {base_revision:window.v4ResultCell.currentBaseRevision,result:data.result.trim(),reason:data.reason.trim(),references:[]};
      payload.idempotency_key = window.v4Api.generateIdempotencyKey('display-change',JSON.stringify({version,...payload}));
      const status = form.querySelector('[role="status"]');
      try {
        await window.v4Api.sourceRequest(`/results/${encodeURIComponent(version)}/change`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(payload)});
        status.textContent = '修改已保存，待高级确认。原正式值保留，页面新值标红。';
        const slot = this.currentCell.slot_id;
        await window.v4ResultCell.loadProjectCells(currentProjectKey);
        const fresh = window.v4ResultCell.cellsBySlot.get(slot);
        if (fresh) { await this.openResultCell(fresh); toast('修改已保存（待确认），原正式值保留。'); }
      } catch(error) { status.textContent = error.message; button.disabled = false; }
    }

    async executeReview(versionId, action) {
      if (!versionId) return;

      if (window.v4Api.mode === 'real') {
        const projectKey = typeof currentProjectKey !== 'undefined' ? currentProjectKey : 'IP166E';
        const baseRev = window.v4ResultCell?.currentBaseRevision ?? 0;

        const reviewCommand = {
          base_revision: baseRev,
          idempotency_key: window.v4Api.generateIdempotencyKey('review'),
          action: action,
          reason: `界面高级复核：执行 ${action}`
        };

        try {
          await window.v4Api.reviewResult(versionId, reviewCommand);
          toast(`复核操作成功：${action}`);
          this.close();
          // Reload cells in ledger
          if (window.v4ResultCell) {
            await window.v4ResultCell.loadProjectCells(projectKey);
          }
        } catch (err) {
          toast(err.message || '复核操作失败');
        }
      } else {
        toast(`Mock 模式复核执行：${action}`);
        this.close();
      }
    }

    close() {
      const dialog = document.getElementById(this.dialogId);
      if (dialog && dialog.open) {
        dialog.close();
      }
    }
  }

  global.v4SourceDrawer = new V4SourceDrawer();
})(typeof window !== 'undefined' ? window : this);
