// V4 ResultCell Component Module
// Consumes ResultCell / ResultPage contract: renders display.text, marks pending confirmation in red with "（待确认）",
// displays adopted value independently, and hooks cell clicks to source evidence drawer.

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

  // Normalizes conditions between frontend UI tags and backend contract values
  // 25C != 25C/60%RH, 40C != 40C/75%RH
  function normalizeCondition(cond, study) {
    if (cond != null && String(cond).trim() !== '') {
      const c = String(cond).trim();
      if (c === '5C' || c === '5℃' || c === '5°C' || c === 'lt' || c === 'long_term') return '5C';
      if (c === '25C' || c === '25℃' || c === '25°C') return '25C';
      if (c === '25C/60%RH' || c === 'acc' || c === 'accelerated') return '25C/60%RH';
      if (c === '40C' || c === '40℃' || c === '40°C') return '40C';
      if (c === '40C/75%RH' || c === 'temp40' || c === 'stress') return '40C/75%RH';
      if (c === 'release') return 'release';
      return c;
    }
    if (study === 'release') return 'release';
    if (study === 'accelerated') return '25C/60%RH';
    if (study === 'stress') return '40C/75%RH';
    if (study === 'long_term') return '5C';
    return null;
  }

  // Normalizes timepoint string/number to month number or release
  function normalizeMonth(tp) {
    if (tp == null || tp === '' || tp === 'release') return null;
    if (typeof tp === 'number') return tp;
    const match = String(tp).match(/^(\d+)M?$/i);
    return match ? parseInt(match[1], 10) : null;
  }

  const ITEM_ALIASES = {
    purity: ['purity', 'SEC-HPLC 单体纯度', 'SEC-HPLC', 'Purity', '单体纯度', 'sec_mono'],
    sec_mono: ['sec_mono', 'SEC-HPLC 单体纯度', 'SEC-HPLC', 'Purity', '单体纯度', 'purity'],
    charge: ['charge', 'icIEF 电荷异质性主峰', 'icIEF', 'Charge', '电荷异质性主峰', 'icief'],
    icief: ['icief', 'icIEF 电荷异质性主峰', 'icIEF', 'Charge', '电荷异质性主峰', 'charge'],
    protein: ['protein', '蛋白质含量 (Protein Content)', 'Protein content', 'Protein Content', '蛋白含量'],
    appearance: ['appearance', '外观 (Appearance)', 'Appearance'],
    large_fragment: ['large_fragment', '大片段', 'Large Fragment'],
    n_terminal: ['n_terminal', 'N-terminal', 'N端变异体']
  };

  function getCanonicalItemKey(itemName) {
    if (!itemName) return '';
    const trimmed = String(itemName).trim();
    for (const [canonicalId, aliases] of Object.entries(ITEM_ALIASES)) {
      if (canonicalId.toLowerCase() === trimmed.toLowerCase() || aliases.includes(trimmed)) {
        return canonicalId;
      }
    }
    return trimmed;
  }

  class V4ResultCellRenderer {
    constructor() {
      this.cellsBySlot = new Map();
      this.cellsByCoordList = new Map(); // `${project}:${batch}:${cond}:${item}:${month}` -> Cell[]
      this.currentBaseRevision = 0;
      this.loadReqSeq = 0;
      this.activeLoadingProject = null;
      this.activeScope = {};
      this.initDefaultCells();
    }

    setScope(scope) {
      if (scope && typeof scope === 'object') {
        this.activeScope = Object.assign({}, this.activeScope, scope);
      }
    }

    initDefaultCells() {
      // In real mode, never prefill mock cells!
      if (window.v4Api && window.v4Api.mode === 'real') {
        return;
      }

      const defaultCell = {
        slot_id: 'IP166E:E202506002A1:lt:12M:sec_mono',
        slot_key: {
          project: 'IP166E',
          sample_type: 'DP',
          batch: 'E202506002A1',
          presentation: '1mg (0.5ml)',
          study: 'long_term',
          study_id: 'STB(C)-QC-IP166E-P-2-005',
          condition: '5C',
          month: 12,
          item: 'SEC-HPLC 单体纯度',
          method: 'SEC-HPLC',
          method_version: 'not_reported'
        },
        display: {
          result_version_id: 'rv-retest-12m-sec',
          detection_id: 'det-retest-2026-001',
          text: '98.8%',
          pending_confirmation: true,
          source_ids: ['doc-synthetic-example-xlsx']
        },
        adopted: {
          result_version_id: 'rv-orig-12m-sec',
          detection_id: 'det-orig-2025-089',
          text: '98.1%',
          pending_confirmation: false,
          source_ids: ['doc-initial-release-coax']
        },
        display_status: 'pending',
        upload_sequence: 1
      };
      this.registerCell(defaultCell);
    }

    clearCells() {
      this.cellsBySlot.clear();
      this.cellsByCoordList.clear();
      this.currentBaseRevision = 0;
    }

    async loadProjectCells(project, filters = {}) {
      if (!project) return;
      const currentReqSeq = ++this.loadReqSeq;
      this.activeLoadingProject = project;
      this.loadError = '';
      this.loading = true;

      try {
        if(window.v45Ledger){const config=await window.v4Api.getConfiguration(project);if(currentReqSeq!==this.loadReqSeq)return;window.v45Ledger.labels=Object.fromEntries((config.config.items||[]).map(i=>[i.id,i.label]));}
        let offset = 0;
        const limit = 100;
        let total = null;
        let allCells = [];
        let expectedBaseRev = null;

        while (total === null || offset < total) {
          const page = await window.v4Api.getResultCells(project, filters, offset, limit);
          // Stale response guard: ignore completed requests from previous project/switch
          if (currentReqSeq !== this.loadReqSeq || this.activeLoadingProject !== project) {
            return;
          }
          if (!page) break;

          if (expectedBaseRev === null) {
            expectedBaseRev = page.base_revision || 0;
          } else if (page.base_revision != null && page.base_revision !== expectedBaseRev) {
            // Base revision changed during pagination, restart from offset 0
            expectedBaseRev = page.base_revision;
            offset = 0;
            total = null;
            allCells = [];
            continue;
          }

          total = page.total != null ? page.total : (page.cells ? page.cells.length : 0);
          const cells = page.cells || [];
          allCells.push(...cells);

          const pageLimit = page.limit || limit;
          offset += pageLimit;

          if (offset >= total) {
            break;
          }
        }

        if (currentReqSeq !== this.loadReqSeq || this.activeLoadingProject !== project) {
          return;
        }

        this.clearCells();
        this.currentBaseRevision = expectedBaseRev || 0;
        allCells.forEach(c => this.registerCell(c));
        this.loading = false;
        this.enhanceLedgerTable();
      } catch (err) {
        if (currentReqSeq === this.loadReqSeq) {
          console.warn('Failed to load project result cells:', err);
          // In real mode on error, clear cells so old data doesn't pose as current
          this.clearCells();
          this.loadError = err.message || '读取真实结果失败';
          this.loading = false;
          this.enhanceLedgerTable();
        }
      }
    }

    registerCell(cell) {
      if (!cell || !cell.slot_id) return;
      this.cellsBySlot.set(cell.slot_id, cell);

      const sk = cell.slot_key || {};
      const cond = sk.condition || cell.condition;
      const study = sk.study || cell.study;
      const normCond = normalizeCondition(cond, study);
      const normM = sk.day != null ? sk.day+'D' : sk.month != null ? sk.month : (normalizeMonth(cell.time_point) ?? 'null');
      const prj = sk.project || cell.project || 'IP166E';
      const batch = sk.batch || cell.batch || '';
      const item = sk.item || cell.item || '';
      const canonicalItem = getCanonicalItemKey(item);

      const addToList = (coordKey) => {
        const list = this.cellsByCoordList.get(coordKey) || [];
        const idx = list.findIndex(c => c.slot_id === cell.slot_id);
        if (idx >= 0) {
          list[idx] = cell;
        } else {
          list.push(cell);
        }
        this.cellsByCoordList.set(coordKey, list);
      };

      // Index exact coord
      addToList(`${prj}:${batch}:${normCond}:${item}:${normM}`);
      if (window.v4Api?.mode !== 'real' && canonicalItem && canonicalItem !== item) {
        addToList(`${prj}:${batch}:${normCond}:${canonicalItem}:${normM}`);
      }
      const aliases = ITEM_ALIASES[canonicalItem] || ITEM_ALIASES[item] || [];
      (window.v4Api?.mode === 'real' ? [] : aliases).forEach(alias => {
        addToList(`${prj}:${batch}:${normCond}:${alias}:${normM}`);
      });
    }

    // Precise exact coordinate lookup: matching project, batch, condition, item name, and month
    // Supports multi-dimension disambiguation via activeScope and explicit filters
    getCellByCoord(project, batch, condition, itemName, timePoint, filters = null) {
      if (!batch || !condition || !itemName) return null;
      const prj = project || (typeof currentProjectKey !== 'undefined' ? currentProjectKey : 'IP166E');
      const normCond = normalizeCondition(condition);
      const normM = normalizeMonth(timePoint) ?? 'null';
      const canonicalItem = getCanonicalItemKey(itemName);

      let candidates = this.cellsByCoordList.get(`${prj}:${batch}:${normCond}:${itemName}:${normM}`);
      if (window.v4Api?.mode !== 'real' && (!candidates || candidates.length === 0)) {
        candidates = this.cellsByCoordList.get(`${prj}:${batch}:${normCond}:${canonicalItem}:${normM}`);
      }
      if (!candidates || candidates.length === 0) {
        return null;
      }

      const scope = filters || this.activeScope || {};
      const matches = candidates.filter(cand => Object.entries(scope).every(
        ([key, value]) => value == null || cand.slot_key?.[key] === value));
      if (matches.length === 0) return null;
      if (matches.length === 1) return matches[0];

      // Multiple matches: dimension is ambiguous. Never pick the first one blindly!
      return {
        ambiguous: true,
        count: matches.length,
        candidates: matches,
        slot_id: null,
        display: {
          text: `需选择研究/规格 (${matches.length})`,
          pending_confirmation: true
        }
      };
    }

    // Render HTML string for a table cell (TD content)
    renderCellContent(cell, fallbackText) {
      if (!cell) {
        return `<span class="v4-cell-val">${esc(fallbackText || '─')}</span>`;
      }

      if (cell.ambiguous) {
        return `
          <div class="v4-result-cell-box has-ambiguous" onclick="v4ResultCell.inspectAmbiguous(event)" title="存在 ${cell.count} 个候选（需选择研究/规格）">
            <div class="v4-display-line text-pending">
              <span class="v4-val-num text-warning">需选择维度 (${cell.count})</span>
            </div>
            <div class="v4-adopted-line">
              <small class="v4-adopted-label">多项匹配</small>
            </div>
          </div>
        `;
      }

      const display = cell.display;
      const adopted = cell.adopted;

      const isPending = Boolean(display && display.pending_confirmation);
      const displayText = display ? display.text : '—';
      const adoptedText = adopted ? adopted.text : null;

      let html = `<div class="v4-result-cell-box ${isPending ? 'has-pending' : ''}" onclick="v4ResultCell.inspectCell('${esc(cell.slot_id)}')" title="点击查看双状态核对与溯源">`;

      if (isPending) {
        html += `
          <div class="v4-display-line text-pending">
            <span class="v4-val-num">${esc(displayText)}</span>
            <span class="v4-pending-tag">（待确认）</span>
          </div>
        `;
      } else {
        html += `
          <div class="v4-display-line">
            <span class="v4-val-num">${esc(displayText)}</span>
          </div>
        `;
      }

      // Display adopted value independently
      if (adoptedText) {
        html += `
          <div class="v4-adopted-line" title="正式受控采用值">
            <small class="v4-adopted-label">正式采用:</small> <span class="v4-adopted-num">${esc(adoptedText)}</span>
          </div>
        `;
      }

      html += `</div>`;
      return html;
    }

    inspectAmbiguous(e) {
      if (e && e.stopPropagation) e.stopPropagation();
      if (typeof toast === 'function') {
        toast('当前单元格存在多个研究或规格候选，请在控制台明确指定筛选维度');
      }
    }

    inspectCell(slotId) {
      const cell = this.cellsBySlot.get(slotId);
      if (cell && window.v4SourceDrawer) {
        window.v4SourceDrawer.openResultCell(cell);
      }
    }

    // Enhance existing table rows after ledger rendering
    enhanceLedgerTable() {
      const isReal = window.v4Api && window.v4Api.mode === 'real';

      const table = document.getElementById('ledgerDataTable');
      if (!table) return;

      if (isReal) {
        const charts = document.getElementById('v3LedgerCharts');
        if (charts) { charts.hidden = true; if (charts.previousElementSibling?.classList.contains('v3-label')) charts.previousElementSibling.textContent = '趋势图尚未接入真实结果'; }
        const columns = ['project','sample_type','batch','presentation','study','study_id','condition','month','item','method','method_version'];
        const labels = ['项目','DS/DP','批号','规格','用途','研究','条件','考察时间','检测项','方法','方法版本'];
        const cells = [...this.cellsBySlot.values()].sort((a,b) => a.slot_id.localeCompare(b.slot_id));
        // The real table is a project-wide result snapshot, not the selected demo sample.
        for (const id of (window.v45Ledger ? [] : ['ledgerSamplePills','ledgerConditionPills'])) {
          const row = document.getElementById(id)?.closest('.toolbar-row');
          if (row) row.hidden = true;
        }
        const scope = [...new Set(cells.map(c => [c.slot_key?.sample_type,c.slot_key?.batch,c.slot_key?.presentation].filter(Boolean).join(' · ')))];
        const banner = document.getElementById('ledgerBannerGrid');
        if (banner) banner.innerHTML = this.loadError ? `<p role="alert">台账加载失败：${esc(this.loadError)}。当前结果数量未知，请重新连接。</p>` : `<div class="v3-label">当前项目已提交结果 · ${cells.length} 个结果槽位</div><p class="v3-note">${scope.length ? scope.slice(0,3).map(esc).join('；') + (scope.length > 3 ? `；其余${scope.length-3}组见下表` : '') : '尚无已提交结果'}。全项目范围；未提交草稿不计入下表，未作质量标准符合性判定。</p>`;
        const meta = document.getElementById('workspaceMeta');
        if (meta) meta.innerHTML = '<span class="v3-note">真实模式 · 结果与状态来自当前项目数据库；演示批次数及考察期不作为真实统计。</span>';
        if (banner && !this.loadError && window.v4Api.can('confirm_fact')) {
          const edit = document.createElement('button'); edit.className = 'btn btn-outline'; edit.textContent = '修改数据';
          edit.onclick = () => {toast('点击下表中要修改的结果，在抽屉中填写新值和说明。无需调查编号。'); table.scrollIntoView({block:'start',behavior:'smooth'});};
          banner.appendChild(edit);
        }
        const badge = document.getElementById('v3LedgerStandardBadge');
        if (badge) badge.textContent = '未作标准符合性判定';
        const exportButton = document.querySelector('#tab-ledger > .card .card-header .btn-primary');
        if (exportButton) {
          exportButton.textContent = '导出当前项目Excel';
          exportButton.disabled = !window.v4Api.can('export');
          exportButton.title = exportButton.disabled ? '当前角色没有导出权限' : '按当前页面版本导出全项目结果';
          const project = this.activeLoadingProject || (typeof currentProjectKey !== 'undefined' ? currentProjectKey : '');
          const revision = this.currentBaseRevision;
          exportButton.onclick = async () => {
            exportButton.disabled = true;
            try { await window.v4Api.exportProject({project,base_revision:revision,filters:{},orientation:'items'}); }
            catch (error) { toast(error.message); }
            finally { exportButton.disabled = !window.v4Api.can('export'); }
          };
        }
        table.innerHTML = `<thead><tr>${labels.map(x => `<th>${esc(x)}</th>`).join('')}<th>展示值 / 正式采用</th></tr></thead><tbody>${cells.map(c => `<tr data-slot-id="${esc(c.slot_id)}">${columns.map(k => `<td>${esc(k === 'month' && c.slot_key?.day != null ? c.slot_key.day+'天' : k === 'month' && c.slot_key?.month != null ? c.slot_key.month+'个月' : c.slot_key?.[k] ?? '—')}</td>`).join('')}<td>${this.renderCellContent(c,'')}</td></tr>`).join('') || `<tr><td colspan="12">${this.loadError ? '结果加载失败：' + esc(this.loadError) + '。请使用顶部重新连接。' : '当前项目尚无已提交检测结果'}</td></tr>`}</tbody>`;
        if(window.v45Ledger){window.v45Ledger.controls(cells,this.activeLoadingProject);table.innerHTML=window.v45Ledger.formal(cells,this);}
        return;
      }

      const thead = table.querySelector('thead');
      const tbody = table.querySelector('tbody');
      if (!thead || !tbody) return;

      const curPrj = typeof currentProjectKey !== 'undefined' ? currentProjectKey : 'IP166E';
      const curBatch = typeof currentLedgerSample !== 'undefined' ? currentLedgerSample : '';
      const curCond = typeof currentLedgerCond !== 'undefined' ? currentLedgerCond : 'lt';

      // Extract timepoint column headers
      const thCols = [...thead.querySelectorAll('tr:last-child th')].map(th => th.textContent.trim());

      [...tbody.querySelectorAll('tr')].forEach(row => {
        if (row.cells.length < 4) return;
        const itemName = row.cells[0]?.textContent.trim() || '';

        // Check cells starting from timepoints (usually index 3 to length - 2)
        for (let c = 3; c < row.cells.length - 1; c++) {
          const tp = thCols[c] || '';
          const cell = this.getCellByCoord(curPrj, curBatch, curCond, itemName, tp);
          if (cell) {
            const raw = row.cells[c].textContent.trim();
            row.cells[c].classList.remove('val-green');
            row.cells[c].classList.add('v4-enhanced-td');
            row.cells[c].innerHTML = this.renderCellContent(cell, raw);
          } else if (isReal) {
            // In real mode, empty or unmatched cells must NOT retain static mock numbers!
            row.cells[c].classList.remove('val-green', 'v4-enhanced-td');
            row.cells[c].innerHTML = '<span class="v4-cell-val" style="color:var(--text-muted)">─</span>';
          }
        }
      });
    }
  }

  global.v4ResultCell = new V4ResultCellRenderer();

  document.addEventListener('DOMContentLoaded', () => {
    // Hook selectProject to clear or reload cells on project change
    if (typeof selectProject === 'function') {
      const orig = selectProject;
      selectProject = function(...args) {
        orig.apply(this, args);
        const prj = typeof currentProjectKey !== 'undefined' ? currentProjectKey : 'IP166E';
        if (global.v4ResultCell) {
          global.v4ResultCell.clearCells();
          if (global.v4Api && global.v4Api.mode === 'real') {
            global.v4ResultCell.loadProjectCells(prj);
          } else {
            global.v4ResultCell.initDefaultCells();
            global.v4ResultCell.enhanceLedgerTable();
          }
        }
      };
    }
  });
})(typeof window !== 'undefined' ? window : this);
