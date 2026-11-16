// V4 Source Tables Viewer Component Module
// Provides full fidelity browsing of immutable source tables (Word / conversion package archive),
// hierarchical table directory, multi-level merged headers, pagination, formulas, images,
// unmapped reason transparency, and bi-directional cell traceability.

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

  // Realistic synthetic fixture for historical Word table archive
  // Covers detection results, quality standards, statistics, study notes, long tables, merged cells, formulas, images, footnotes
  const SYNTHETIC_SOURCE_IMPORT = {
    import_id: 'src-import-ip166e-word-20261012',
    format_version: '1.0',
    source_filename: 'IP166E_工艺变更前后稳定性研究综合评价报告.docx',
    source_hash: '3a8f9c1b2d4e5f6a7b8c9d0e1f2a3b4c5d6e7f8a9b0c1d2e3f4a5b6c7d8e9f0a',
    extractor_version: 'word-table-extractor-v1.0-deterministic',
    created_at: '2026-10-12 00:00:00',
    summary: {
      total_tables: 5,
      categories: {
        results: 2,
        standards: 1,
        statistics: 1,
        notes: 1
      },
      total_cells: 320,
      mapped_cells: 48,
      unmapped_cells: 272
    },
    tables: [
      {
        table_id: 'tbl-001',
        table_index: 1,
        caption: '表 1 依苏帕格鲁肽α注射液长期稳定性考察 (5℃) 检定结果大表',
        category: 'results',
        category_label: '检测结果表',
        study: 'long_term',
        condition: '5C',
        batch: 'E202506002A1',
        headers: [
          [
            { text: '检验项目 (Test Item)', rowspan: 2, colspan: 1 },
            { text: '方法学与执行标准', rowspan: 2, colspan: 1 },
            { text: '单位', rowspan: 2, colspan: 1 },
            { text: '考察时间点 (Time Points)', rowspan: 1, colspan: 5 },
            { text: '质量标准限度 (Acceptance Criteria)', rowspan: 2, colspan: 1 }
          ],
          [
            { text: '0M (放行)', rowspan: 1, colspan: 1 },
            { text: '3M', rowspan: 1, colspan: 1 },
            { text: '6M', rowspan: 1, colspan: 1 },
            { text: '9M', rowspan: 1, colspan: 1 },
            { text: '12M', rowspan: 1, colspan: 1 }
          ]
        ],
        rows: [
          [
            { text: '外观 (Appearance)', mapped: true, candidate_id: 'cand-app-01', item: 'appearance', coord: 'R1C1' },
            { text: '澄清带微细乳光液体', mapped: false, unmapped_reason: '检验方法描述', coord: 'R1C2' },
            { text: '—', mapped: false, unmapped_reason: '单位占位', coord: 'R1C3' },
            { text: '符合规定', mapped: true, candidate_id: 'cand-app-0m', month: 0, coord: 'R1C4' },
            { text: '符合规定', mapped: true, candidate_id: 'cand-app-3m', month: 3, coord: 'R1C5' },
            { text: '符合规定', mapped: true, candidate_id: 'cand-app-6m', month: 6, coord: 'R1C6' },
            { text: '符合规定', mapped: true, candidate_id: 'cand-app-9m', month: 9, coord: 'R1C7' },
            { text: '符合规定', mapped: true, candidate_id: 'cand-app-12m', month: 12, coord: 'R1C8' },
            { text: '应为澄明液体，允许微带乳光', mapped: false, unmapped_reason: '质量标准限度（非检测值）', coord: 'R1C9' }
          ],
          [
            { text: 'SEC-HPLC 单体纯度', mapped: true, candidate_id: 'cand-sec-01', item: 'purity', coord: 'R2C1' },
            { text: 'SEC-HPLC (Rev.02)', mapped: false, unmapped_reason: '方法学版本', coord: 'R2C2' },
            { text: '%', mapped: false, unmapped_reason: '检测单位', coord: 'R2C3' },
            { text: '99.2%', mapped: true, candidate_id: 'cand-sec-0m', month: 0, coord: 'R2C4' },
            { text: '98.9%', mapped: true, candidate_id: 'cand-sec-3m', month: 3, coord: 'R2C5' },
            { text: '98.6%', mapped: true, candidate_id: 'cand-sec-6m', month: 6, coord: 'R2C6' },
            { text: '98.4%', mapped: true, candidate_id: 'cand-sec-9m', month: 9, coord: 'R2C7' },
            { text: '98.8%', mapped: true, candidate_id: 'cand-001', month: 12, coord: 'R2C8' },
            { text: '≥ 95.0%', mapped: false, unmapped_reason: '质量标准限度（非检测值）', coord: 'R2C9' }
          ],
          [
            { text: 'icIEF 主峰比例 (Charge)', mapped: true, candidate_id: 'cand-icief-01', item: 'charge', coord: 'R3C1' },
            { text: 'icIEF 毛细管电泳', mapped: false, unmapped_reason: '方法学描述', coord: 'R3C2' },
            { text: '%', mapped: false, unmapped_reason: '检测单位', coord: 'R3C3' },
            { text: '74.5%', mapped: true, candidate_id: 'cand-icief-0m', month: 0, coord: 'R3C4' },
            { text: '73.8%', mapped: true, candidate_id: 'cand-icief-3m', month: 3, coord: 'R3C5' },
            { text: '73.0%', mapped: true, candidate_id: 'cand-icief-6m', month: 6, coord: 'R3C6' },
            { text: '72.4%', mapped: true, candidate_id: 'cand-icief-9m', month: 9, coord: 'R3C7' },
            { text: '72.1%', mapped: true, candidate_id: 'cand-icief-12m', month: 12, coord: 'R3C8' },
            { text: '≥ 65.0%', mapped: false, unmapped_reason: '质量标准限度（非检测值）', coord: 'R3C9' }
          ],
          [
            { text: '蛋白质含量 (Protein Content)', mapped: true, candidate_id: 'cand-pro-01', item: 'protein', coord: 'R4C1' },
            { text: '紫外分光光度法 (UV280)', mapped: false, unmapped_reason: '方法学描述', coord: 'R4C2' },
            { text: 'mg/ml', mapped: false, unmapped_reason: '检测单位', coord: 'R4C3' },
            { text: '2.04', mapped: true, candidate_id: 'cand-pro-0m', month: 0, coord: 'R4C4' },
            { text: '2.02', mapped: true, candidate_id: 'cand-pro-3m', month: 3, coord: 'R4C5' },
            { text: '2.01', mapped: true, candidate_id: 'cand-pro-6m', month: 6, coord: 'R4C6' },
            { text: '1.99', mapped: true, candidate_id: 'cand-pro-9m', month: 9, coord: 'R4C7' },
            { text: '2.00', mapped: true, candidate_id: 'cand-pro-12m', month: 12, coord: 'R4C8' },
            { text: '1.80 ~ 2.20 mg/ml', mapped: false, unmapped_reason: '质量标准限度（非检测值）', coord: 'R4C9' }
          ],
          [
            { text: '高分子聚合物 (HMW)', mapped: true, candidate_id: 'cand-hmw-01', item: 'hmw', coord: 'R5C1' },
            { text: 'SEC-HPLC', mapped: false, unmapped_reason: '方法描述', coord: 'R5C2' },
            { text: '%', mapped: false, unmapped_reason: '单位', coord: 'R5C3' },
            { text: '0.4%', mapped: true, candidate_id: 'cand-hmw-0m', month: 0, coord: 'R5C4' },
            { text: '0.6%', mapped: true, candidate_id: 'cand-hmw-3m', month: 3, coord: 'R5C5' },
            { text: '0.7%', mapped: true, candidate_id: 'cand-hmw-6m', month: 6, coord: 'R5C6' },
            { text: '0.9%', mapped: true, candidate_id: 'cand-hmw-9m', month: 9, coord: 'R5C7' },
            { text: '', mapped: false, unmapped_reason: '原始文档此处为空白未填', is_blank: true, coord: 'R5C8' },
            { text: '≤ 3.0%', mapped: false, unmapped_reason: '质量标准限度（非检测值）', coord: 'R5C9' }
          ]
        ],
        footnotes: [
          '注1：批号 E202506002A1 为 6000L PPQ 工艺验证批；试验考察条件为 5℃±3℃ 温控冷库。',
          '注2：第 12 个月 SEC-HPLC 单体纯度检测原记录存在复测事实（OOE-2026-009），原正式记录为 98.1%，复测结果为 98.8%。',
          '注3：第 12 个月高分子聚合物 (HMW) 原始报告中留空未录入，保持空白不覆盖。'
        ]
      },
      {
        table_id: 'tbl-002',
        table_index: 2,
        caption: '表 2 依苏帕格鲁肽α商业化成品注册质量标准 (YBH-IP166E-DP-2025)',
        category: 'standards',
        category_label: '质量标准表',
        headers: [
          [
            { text: '序号', rowspan: 1, colspan: 1 },
            { text: '质控项目 (CQA)', rowspan: 1, colspan: 1 },
            { text: '分析方法编号', rowspan: 1, colspan: 1 },
            { text: '接受标准 (Acceptance Criteria)', rowspan: 1, colspan: 1 },
            { text: '分类与属性', rowspan: 1, colspan: 1 }
          ]
        ],
        rows: [
          [
            { text: '1', mapped: false, unmapped_reason: '表格序号', coord: 'R1C1' },
            { text: '外观', mapped: false, unmapped_reason: '标准项目名', coord: 'R1C2' },
            { text: 'ChP 2025 通则 0102', mapped: false, unmapped_reason: '标准方法依据', coord: 'R1C3' },
            { text: '无色至微黄色澄明液体', mapped: false, unmapped_reason: '质量标准限度（非检测值）', coord: 'R1C4' },
            { text: '理化常规属性', mapped: false, unmapped_reason: '属性说明', coord: 'R1C5' }
          ],
          [
            { text: '2', mapped: false, unmapped_reason: '表格序号', coord: 'R2C1' },
            { text: 'SEC-HPLC 单体纯度', mapped: false, unmapped_reason: '标准项目名', coord: 'R2C2' },
            { text: 'SOP-QC-SEC-012 (Rev.03)', mapped: false, unmapped_reason: '标准方法依据', coord: 'R2C3' },
            { text: '≥ 95.0%', mapped: false, unmapped_reason: '质量标准限度（非检测值）', coord: 'R2C4' },
            { text: '关键质量属性 (CQA)', mapped: false, unmapped_reason: '属性说明', coord: 'R2C5' }
          ],
          [
            { text: '3', mapped: false, unmapped_reason: '表格序号', coord: 'R3C1' },
            { text: 'icIEF 电荷异质性主峰', mapped: false, unmapped_reason: '标准项目名', coord: 'R3C2' },
            { text: 'SOP-QC-IEF-008 (Rev.02)', mapped: false, unmapped_reason: '标准方法依据', coord: 'R3C3' },
            { text: '≥ 65.0%', mapped: false, unmapped_reason: '质量标准限度（非检测值）', coord: 'R3C4' },
            { text: '关键质量属性 (CQA)', mapped: false, unmapped_reason: '属性说明', coord: 'R3C5' }
          ]
        ],
        footnotes: ['注：本表为受控质量标准限度，供对照参考，不直接作为单批次检测数值入库。']
      },
      {
        table_id: 'tbl-003',
        table_index: 3,
        caption: '表 3 变更前 (500L) 与变更后 (6000L) 降解速率动力学斜率统计拟合比较表',
        category: 'statistics',
        category_label: '统计汇总表',
        headers: [
          [
            { text: '检测项目', rowspan: 2, colspan: 1 },
            { text: '考察条件', rowspan: 2, colspan: 1 },
            { text: '变更前 (500L 临床批)', rowspan: 1, colspan: 2 },
            { text: '变更后 (6000L PPQ批)', rowspan: 1, colspan: 2 },
            { text: '统计学等效性评价', rowspan: 2, colspan: 1 }
          ],
          [
            { text: '降解斜率 (k/月)', rowspan: 1, colspan: 1 },
            { text: '拟合度 (R²)', rowspan: 1, colspan: 1 },
            { text: '降解斜率 (k/月)', rowspan: 1, colspan: 1 },
            { text: '拟合度 (R²)', rowspan: 1, colspan: 1 }
          ]
        ],
        rows: [
          [
            { text: 'SEC-HPLC 单体纯度', mapped: false, unmapped_reason: '项目名', coord: 'R1C1' },
            { text: '5℃ (长期)', mapped: false, unmapped_reason: '条件', coord: 'R1C2' },
            { text: '-0.071', formula: '=SLOPE(Y1,X1)', mapped: false, unmapped_reason: '拟合统计量（非原始检测值）', coord: 'R1C3' },
            { text: '0.982', formula: '=RSQ(Y1,X1)', mapped: false, unmapped_reason: '统计检验值', coord: 'R1C4' },
            { text: '-0.068', formula: '=SLOPE(Y2,X2)', mapped: false, unmapped_reason: '拟合统计量（非原始检测值）', coord: 'R1C5' },
            { text: '0.987', formula: '=RSQ(Y2,X2)', mapped: false, unmapped_reason: '统计检验值', coord: 'R1C6' },
            { text: '斜率无显著差异 (p = 0.62 > 0.05)', mapped: false, unmapped_reason: '评价结论', coord: 'R1C7' }
          ],
          [
            { text: 'icIEF 主峰比例', mapped: false, unmapped_reason: '项目名', coord: 'R2C1' },
            { text: '5℃ (长期)', mapped: false, unmapped_reason: '条件', coord: 'R2C2' },
            { text: '-0.185', formula: '=SLOPE(Y3,X3)', mapped: false, unmapped_reason: '拟合统计量（非原始检测值）', coord: 'R2C3' },
            { text: '0.974', formula: '=RSQ(Y3,X3)', mapped: false, unmapped_reason: '统计检验值', coord: 'R2C4' },
            { text: '-0.179', formula: '=SLOPE(Y4,X4)', mapped: false, unmapped_reason: '拟合统计量（非原始检测值）', coord: 'R2C5' },
            { text: '0.981', formula: '=RSQ(Y4,X4)', mapped: false, unmapped_reason: '统计检验值', coord: 'R2C6' },
            { text: '动力学趋势高度一致 (p = 0.78 > 0.05)', mapped: false, unmapped_reason: '评价结论', coord: 'R2C7' }
          ]
        ],
        footnotes: ['注：本表数据由回归计算得出，属衍生统计量，不作为单点原始检测事实入库。']
      },
      {
        table_id: 'tbl-004',
        table_index: 4,
        caption: '表 4 参试批次背景信息与方案受控编号对照表',
        category: 'notes',
        category_label: '说明与方案表',
        headers: [
          [
            { text: '工艺阶段', rowspan: 1, colspan: 1 },
            { text: '样品批号', rowspan: 1, colspan: 1 },
            { text: '批量规模', rowspan: 1, colspan: 1 },
            { text: '生产日期', rowspan: 1, colspan: 1 },
            { text: '方案编号 (Protocol ID)', rowspan: 1, colspan: 1 },
            { text: '当前状态', rowspan: 1, colspan: 1 }
          ]
        ],
        rows: [
          [
            { text: '工艺验证批 (PPQ)', mapped: false, unmapped_reason: '阶段属性', coord: 'R1C1' },
            { text: 'E202506002A1', mapped: false, unmapped_reason: '批号元数据', coord: 'R1C2' },
            { text: '6000L', mapped: false, unmapped_reason: '生产规模', coord: 'R1C3' },
            { text: '2025-06-15', mapped: false, unmapped_reason: '日期', coord: 'R1C4' },
            { text: 'STB(C)-QC-IP166E-P-2-005', mapped: false, unmapped_reason: '方案编号', coord: 'R1C5' },
            { text: '在研进行中 (已完成18M)', mapped: false, unmapped_reason: '状态说明', coord: 'R1C6' }
          ],
          [
            { text: '工艺验证批 (PPQ)', mapped: false, unmapped_reason: '阶段属性', coord: 'R2C1' },
            { text: 'E202506002B1', mapped: false, unmapped_reason: '批号元数据', coord: 'R2C2' },
            { text: '6000L', mapped: false, unmapped_reason: '生产规模', coord: 'R2C3' },
            { text: '2025-06-22', mapped: false, unmapped_reason: '日期', coord: 'R2C4' },
            { text: 'STB(C)-QC-IP166E-P-2-005', mapped: false, unmapped_reason: '方案编号', coord: 'R2C5' },
            { text: '在研进行中 (已完成18M)', mapped: false, unmapped_reason: '状态说明', coord: 'R2C6' }
          ]
        ],
        footnotes: ['注：本表用于明确批次背景，确立供试品生命周期追溯依据。']
      },
      {
        table_id: 'tbl-005',
        table_index: 5,
        caption: '表 5 长期稳定性 (5℃) 多时间点全项检验长表 (演示大表末行可达与多页滚动)',
        category: 'results',
        category_label: '检测结果表',
        headers: [
          [
            { text: '行号', rowspan: 1, colspan: 1 },
            { text: '检测项目代号', rowspan: 1, colspan: 1 },
            { text: '检验参数', rowspan: 1, colspan: 1 },
            { text: '原始记录值', rowspan: 1, colspan: 1 },
            { text: '图表/公式', rowspan: 1, colspan: 1 },
            { text: '映射判定', rowspan: 1, colspan: 1 }
          ]
        ],
        rows: (function() {
          const list = [];
          for (let i = 1; i <= 65; i++) {
            const isLast = (i === 65);
            list.push([
              { text: String(i), mapped: false, unmapped_reason: '物理行号', coord: `R${i}C1` },
              { text: isLast ? 'SEC-HPLC 单体纯度 (末行校验)' : `PARAM_${i.toString().padStart(3, '0')}`, mapped: isLast, candidate_id: isLast ? 'cand-001' : null, coord: `R${i}C2` },
              { text: isLast ? '12M 终点测试' : `第 ${i} 项考察指标`, mapped: false, unmapped_reason: '参数说明', coord: `R${i}C3` },
              { text: isLast ? '98.8%' : (95 + (i % 50) * 0.08).toFixed(1) + '%', mapped: isLast, candidate_id: isLast ? 'cand-001' : null, coord: `R${i}C4` },
              { text: (i % 5 === 0) ? '包含谱图' : '—', has_image: (i % 5 === 0), formula: (i % 10 === 0) ? '=AVERAGE(R1C4:R5C4)' : null, mapped: false, unmapped_reason: '附件/计算值', coord: `R${i}C5` },
              { text: isLast ? '已映射到候选' : (i % 3 === 0 ? '未映射：非检测参数' : '待核对'), mapped: isLast, unmapped_reason: isLast ? null : '需确认槽位', coord: `R${i}C6` }
            ]);
          }
          return list;
        })(),
        footnotes: ['注：第 65 行为大表末行，用于严格测试分页跳转与最后一行完整可见。']
      }
    ]
  };

  class V4SourceTablesManager {
    constructor() {
      this.dialogId = 'v4SourceTablesModal';
      this.currentData = window.v4Api?.mode === 'real' ? {tables:[], source_filename:'尚无归档', source_hash:''} : SYNTHETIC_SOURCE_IMPORT;
      this.currentTableId = 'tbl-001';
      this.currentPage = 1;
      this.pageSize = 20; // 20, 50, or 'all'
      this.activeCategoryFilter = 'all';
      this.searchKeyword = '';
      this.highlightCoord = null;
      this.ensureDom();
    }

    ensureDom() {
      if (document.getElementById(this.dialogId)) return;
      const html = `
        <dialog id="${this.dialogId}" class="v4-source-tables-modal" aria-labelledby="v4SourceTablesTitle">
          <div class="v4-st-content">
            <div class="v4-st-header">
              <div class="v4-st-header-left">
                <span class="v4-st-eyebrow">不可变来源归档 · IMMUTABLE SOURCE TABLES</span>
                <h2 id="v4SourceTablesTitle" class="v4-st-title">原始文件与表格</h2>
                <span class="v4-st-badge-readonly">只读归档 · 保持原始结构与文字</span>
              </div>
              <div class="v4-st-header-right">
                <button type="button" class="btn btn-outline btn-sm" onclick="v4SourceTables.createResultPreview()">生成检测核对预览</button>
                <details class="v42-history-import"><summary>历史资料导入工具</summary><button type="button" class="btn btn-outline btn-sm" onclick="v4SourceTables.showImportPairDialog()">📥 配对导入 (原Word+转换包)</button><p>用于准备了映射包的复杂历史Word。日常模板文件请从数据接收上传。</p></details>
                <button type="button" class="hub-icon v4-st-close" aria-label="关闭窗口" onclick="v4SourceTables.close()">✕</button>
              </div>
            </div>

            <!-- Notice bar explaining status boundary -->
            <div class="v4-st-boundary-banner">
              <span>📌 <strong>状态边界说明：</strong>来源归档保存仅建立原始凭据与结构化索引，<strong>不改变有效检测台账</strong>。检测项需经人工核对确认后，方可记为检测事实或受控采用。</span>
            </div>

            <div class="v4-st-body">
              <!-- Left: Table Directory / Sidebar -->
              <aside class="v4-st-sidebar">
                <div class="v4-st-doc-meta">
                  <div class="v4-st-doc-title" id="v4StDocTitle">文档载入中...</div>
                  <div class="v4-st-doc-hash" id="v4StDocHash">SHA-256: —</div>
                </div>

                <div class="v4-st-search-box">
                  <input type="text" id="v4StSearchInput" placeholder="搜索表题注、编号或关键词..." class="v4-input v4-st-search-input" oninput="v4SourceTables.onSearch(this.value)">
                </div>

                <div class="v4-st-category-filters" id="v4StCatFilters">
                  <!-- Category Pills -->
                </div>

                <div class="v4-st-dir-list" id="v4StDirList">
                  <!-- Table list items -->
                </div>
              </aside>

              <!-- Right: Table Viewer -->
              <main class="v4-st-viewer" id="v4StViewer">
                <!-- Dynamic active table view -->
              </main>
            </div>
          </div>
        </dialog>
      `;
      document.body.insertAdjacentHTML('beforeend', html);
    }

    async open(tableId = null, highlightCoord = null, archiveId = null) {
      if (window.v4Api?.mode === 'real') {
        try {
          const project = typeof currentProjectKey !== 'undefined' ? currentProjectKey : 'IP166E';
          this.archives = await window.v4Api.getSourceImports(project);
          const selected = archiveId || this.archives[0]?.import_id;
          this.currentData = selected ? await window.v4Api.getSourceTables(selected) : {tables:[], source_filename:'尚无归档', source_hash:''};
          this.currentTableId = tableId || this.currentData.tables[0]?.table_id;
        } catch (error) {
          this.currentData = {tables:[], source_filename:'读取失败：' + error.message, source_hash:''};
        }
      }
      this.ensureDom();
      if (tableId) {
        this.currentTableId = tableId;
      }
      this.highlightCoord = highlightCoord || null;
      this.currentPage = 1;

      // Update doc info
      const docTitle = document.getElementById('v4StDocTitle');
      const docHash = document.getElementById('v4StDocHash');
      if (docTitle) docTitle.textContent = this.currentData.source_filename;
      if (docHash) docHash.textContent = `SHA-256: ${this.currentData.source_hash.slice(0, 16)}...`;
      if (window.v4Api?.mode === 'real') {
        let select = document.getElementById('v4ArchiveSelect');
        if (!select) { select = document.createElement('select'); select.id = 'v4ArchiveSelect'; docTitle.after(select); }
        select.innerHTML = (this.archives || []).map(a => `<option value="${esc(a.import_id)}">${esc(a.source_filename)} · ${esc(a.created_at)}</option>`).join('');
        select.value = this.currentData.import_id || '';
        select.onchange = () => this.open(null, null, select.value);
      }

      let originalLink = document.getElementById('v4SourceOriginal');
      if (!originalLink) { originalLink = document.createElement('a'); originalLink.id = 'v4SourceOriginal'; docHash.after(originalLink); }
      originalLink.textContent = this.currentData.import_id && window.v4Api?.mode === 'real' ? '查看原始 Word（含原版公式与附件）' : '';
      originalLink.href = this.currentData.import_id ? `/api/v1/source-imports/${encodeURIComponent(this.currentData.import_id)}/file` : '#';
      this.renderCategoryFilters();
      this.renderDirectory();
      this.renderActiveTable();

      const modal = document.getElementById(this.dialogId);
      if (modal && typeof modal.showModal === 'function') {
        modal.showModal();
      }

      if (this.highlightCoord) {
        setTimeout(() => this.applyHighlight(), 200);
      }
    }

    async createResultPreview() {
      if (window.v4Api?.mode !== 'real' || !this.currentData.import_id) { toast('请先选择真实来源归档'); return; }
      try {
        const id = this.currentData.import_id;
        const projects = await window.v4Api.getProjects();
        const project = projects.find(p => p.project === this.currentData.project);
        if (!project) throw new Error('项目不存在');
        if (this.previewArchive !== id) { this.previewArchive = id; this.previewKey = crypto.randomUUID(); }
        const preview = await window.v4Api.sourcePreview(id, {base_revision:project.revision,
          idempotency_key:this.previewKey,context:{role:'stability_source',defaults:{}}});
        this.close();
        if (typeof switchTab === 'function') switchTab('intake');
        window.v4Intake.currentPreview = preview;
        window.v4Intake.renderPreviewRightPanel(preview);
        toast('已生成候选，请核对身份；尚未确认结果');
      } catch (error) { toast(error.message); }
    }

    async locateCandidate(candidate) {
      if (window.v4Api?.mode !== 'real') { await this.open(null, candidate.location); return; }
      try {
        const source = await window.v4Api.getSource(candidate.id);
        await this.locateSource(source.source_hash, source.raw?.location);
      } catch (error) { toast('来源定位失败：' + error.message); }
    }

    async locateSource(hash, location) {
      if (!hash || !location?.section) { toast('此来源尚无全表归档'); return; }
      const project = typeof currentProjectKey !== 'undefined' ? currentProjectKey : 'IP166E';
      const archives = await window.v4Api.getSourceImports(project);
      const archive = archives.find(a => a.source_hash === hash);
      if (!archive) { toast('此原件尚未建立来源归档'); return; }
      const data = await window.v4Api.getSourceTables(archive.import_id);
      const table = data.tables.find(t => t.section === location.section);
      if (!table) { toast('无法匹配原表坐标'); return; }
      await this.open(table.table_id, location, archive.import_id);
      this.currentPage = this.pageSize === 'all' ? 1 : Math.ceil(location.row / this.pageSize);
      this.renderActiveTable(); this.applyHighlight();
    }

    close() {
      const modal = document.getElementById(this.dialogId);
      if (modal && typeof modal.close === 'function') {
        modal.close();
      }
    }

    onSearch(val) {
      this.searchKeyword = (val || '').trim().toLowerCase();
      this.renderDirectory();
    }

    setCategoryFilter(cat) {
      this.activeCategoryFilter = cat;
      this.renderCategoryFilters();
      this.renderDirectory();
    }

    renderCategoryFilters() {
      const container = document.getElementById('v4StCatFilters');
      if (!container) return;

      const cats = [
        { id: 'all', label: '全部表格' },
        { id: 'results', label: '检测结果' },
        { id: 'standards', label: '质量标准' },
        { id: 'statistics', label: '统计量' },
        { id: 'notes', label: '说明与方案' }
      ];

      container.innerHTML = cats.map(c => `
        <button type="button" class="v4-st-cat-pill ${this.activeCategoryFilter === c.id ? 'active' : ''}" onclick="v4SourceTables.setCategoryFilter('${c.id}')">
          ${c.label}
        </button>
      `).join('');
    }

    renderDirectory() {
      const listEl = document.getElementById('v4StDirList');
      if (!listEl) return;

      const tables = this.currentData.tables || [];
      const filtered = tables.filter(t => {
        if (this.activeCategoryFilter !== 'all' && t.category !== this.activeCategoryFilter) {
          return false;
        }
        if (this.searchKeyword) {
          const matchCaption = t.caption.toLowerCase().includes(this.searchKeyword);
          const matchId = t.table_id.toLowerCase().includes(this.searchKeyword);
          const matchRows = t.rows && t.rows.some(r => r.some(c => (c.text || '').toLowerCase().includes(this.searchKeyword)));
          return matchCaption || matchId || matchRows;
        }
        return true;
      });

      if (!filtered.length) {
        listEl.innerHTML = `<div class="v4-st-empty-list">未找到符合条件的表格</div>`;
        return;
      }

      listEl.innerHTML = filtered.map(t => {
        const isSelected = t.table_id === this.currentTableId;
        const rowCount = t.rows ? t.rows.length : 0;
        const colCount = Math.max(0, ...(t.rows || []).map(r => r.reduce((sum,c) => sum + (c.colspan || 1),0)));
        return `
          <div class="v4-st-dir-item ${isSelected ? 'selected' : ''}" onclick="v4SourceTables.selectTable('${esc(t.table_id)}')">
            <div class="v4-st-dir-top">
              <span class="v4-st-tbl-num">表 ${t.table_index}</span>
              <span class="v4-st-cat-badge ${esc(t.category)}">${esc(t.category_label || t.category)}</span>
            </div>
            <div class="v4-st-dir-caption" title="${esc(t.caption)}">${esc(t.caption)}</div>
            <div class="v4-st-dir-meta">
              <span>${rowCount} 行 × ${colCount} 列</span>
              <span>${t.footnotes ? `${t.footnotes.length} 项附注` : '无附注'}</span>
            </div>
          </div>
        `;
      }).join('');
    }

    selectTable(tableId) {
      this.currentTableId = tableId;
      this.currentPage = 1;
      this.highlightCoord = null;
      this.renderDirectory();
      this.renderActiveTable();
    }

    setPage(page) {
      this.currentPage = page;
      this.renderActiveTable();
    }

    setPageSize(size) {
      this.pageSize = size === 'all' ? 'all' : parseInt(size, 10);
      this.currentPage = 1;
      this.renderActiveTable();
    }

    renderActiveTable() {
      const viewerEl = document.getElementById('v4StViewer');
      if (!viewerEl) return;

      const table = this.currentData.tables.find(t => t.table_id === this.currentTableId);
      if (!table) {
        viewerEl.innerHTML = `<div class="v4-st-empty-view">请从左侧选择表格</div>`;
        return;
      }

      const totalRows = table.rows ? table.rows.length : 0;
      let pagedRows = table.rows || [];
      let totalPages = 1;

      if (this.pageSize !== 'all') {
        totalPages = Math.max(1, Math.ceil(totalRows / this.pageSize));
        if (this.currentPage > totalPages) this.currentPage = totalPages;
        const start = (this.currentPage - 1) * this.pageSize;
        const end = start + this.pageSize;
        pagedRows = table.rows.slice(start, end);
      }

      // Build Table Headers HTML
      let headersHtml = '';
      if (table.headers && table.headers.length) {
        headersHtml = `<thead>` + table.headers.map(row => `
          <tr>
            ${row.map(h => `
              <th rowspan="${h.rowspan || 1}" colspan="${h.colspan || 1}" class="v4-st-th">
                ${esc(h.text)}
              </th>
            `).join('')}
          </tr>
        `).join('') + `</thead>`;
      }

      // Build Table Body HTML
      // Clip merged cells to each page and repeat their anchor at a continued page boundary.
      const startRow = this.pageSize === 'all' ? 1 : (this.currentPage - 1) * this.pageSize + 1;
      const endRow = this.pageSize === 'all' ? totalRows : Math.min(totalRows, startRow + this.pageSize - 1);
      const anchors = new Map((table.rows || []).flat().map(c => [c.coord, c]));
      pagedRows = pagedRows.map((row, index) => row.flatMap(c => {
        if (c.continuation) {
          const anchor = anchors.get(c.anchor);
          if (!anchor || index !== 0 || anchor.row >= startRow) return [];
          return [{...anchor, rowspan: Math.min(anchor.row + anchor.rowspan - startRow, endRow-startRow+1)}];
        }
        return [{...c, rowspan: Math.min(c.rowspan || 1, endRow-(c.row || startRow+index)+1)}];
      }));
      const bodyHtml = `<tbody>` + pagedRows.map((row, rIdx) => {
        const actualRowIdx = this.pageSize === 'all' ? rIdx + 1 : (this.currentPage - 1) * this.pageSize + rIdx + 1;
        return `
          <tr data-row="${actualRowIdx}">
            ${row.map((c, cIdx) => {
              const cellCoord = c.coord || `R${actualRowIdx}C${cIdx + 1}`;
              const isMapped = Boolean(c.mapped);
              const isBlank = Boolean(c.is_blank || !c.text);
              const hasFormula = Boolean(c.formula);
              const hasImg = Boolean(c.has_image);

              let contentHtml = isBlank ? `<span class="v4-empty-cell">(空白)</span>` : esc(c.text);

              if (Array.isArray(c.references) && this.currentData.import_id) {
                contentHtml += c.references.filter(ref => !ref.external && ref.sha256).map(ref => {
                  const url = `/api/v1/source-imports/${encodeURIComponent(this.currentData.import_id)}/asset?part=${encodeURIComponent(ref.part)}`;
                  return /\.(png|jpe?g|gif)$/i.test(ref.part) ? `<img src="${url}" alt="原表附件" style="max-width:240px;display:block">` : `<span>附件：${esc(ref.part)}（请查看原件）</span>`;
                }).join('');
              }
              let badgesHtml = '';
              if (hasFormula) {
                badgesHtml += `<span class="v4-formula-badge" title="公式: ${esc(c.formula)}">fx</span>`;
              }
              if (hasImg) {
                badgesHtml += `<span class="v4-img-badge" title="内嵌图像/谱图">🖼️</span>`;
              }

              let statusTagHtml = '';
              if (isMapped) {
                statusTagHtml = `<span class="v4-cell-tag mapped" onclick="v4SourceTables.inspectCellCandidate('${esc(c.candidate_id)}')" title="已映射检测候选，点击打开双状态溯源抽屉">已关联结果 ➔</span>`;
                if (c.candidate_ids?.length) statusTagHtml = c.candidate_ids.map(id => `<button type="button" class="v4-cell-tag mapped" onclick="v4SourceTables.inspectCellCandidate('${esc(id)}')">查看关联候选</button>`).join('');
              } else if (c.unmapped_reason) {
                statusTagHtml = `<span class="v4-cell-tag unmapped" title="未关联原因: ${esc(c.unmapped_reason)}">未映射</span>`;
              }

              return `
                <td rowspan="${c.rowspan || 1}" colspan="${c.colspan || 1}" class="v4-st-td ${isMapped ? 'is-mapped' : ''}" data-coord="${cellCoord}" title="坐标: ${cellCoord}">
                  <div class="v4-st-cell-inner">
                    <div class="v4-st-cell-text">${badgesHtml}${contentHtml}</div>
                    <div class="v4-st-cell-foot">${statusTagHtml}<span class="v4-st-coord-lbl">${cellCoord}</span></div>
                  </div>
                </td>
              `;
            }).join('')}
          </tr>
        `;
      }).join('') + `</tbody>`;

      // Build Footnotes HTML
      let footnotesHtml = '';
      if (table.footnotes && table.footnotes.length) {
        footnotesHtml = `
          <div class="v4-st-footnotes">
            <div class="v4-st-footnotes-title">表附注与说明：</div>
            <ul>
              ${table.footnotes.map(fn => `<li>${esc(fn)}</li>`).join('')}
            </ul>
          </div>
        `;
      }

      // Build Pagination Toolbar HTML
      const startItem = this.pageSize === 'all' ? 1 : (this.currentPage - 1) * this.pageSize + 1;
      const endItem = this.pageSize === 'all' ? totalRows : Math.min(totalRows, this.currentPage * this.pageSize);

      const paginationHtml = `
        <div class="v4-st-pagination-bar">
          <div class="v4-st-pag-left">
            <span>显示第 <strong>${startItem} - ${endItem}</strong> 行，共 <strong>${totalRows}</strong> 行</span>
            <label class="v4-st-size-select">
              每页:
              <select onchange="v4SourceTables.setPageSize(this.value)">
                <option value="20" ${this.pageSize === 20 ? 'selected' : ''}>20 行</option>
                <option value="50" ${this.pageSize === 50 ? 'selected' : ''}>50 行</option>
                <option value="all" ${this.pageSize === 'all' ? 'selected' : ''}>全部显示</option>
              </select>
            </label>
          </div>
          ${this.pageSize !== 'all' && totalPages > 1 ? `
            <div class="v4-st-pag-controls">
              <button type="button" class="btn btn-outline btn-sm" ${this.currentPage <= 1 ? 'disabled' : ''} onclick="v4SourceTables.setPage(1)">首页</button>
              <button type="button" class="btn btn-outline btn-sm" ${this.currentPage <= 1 ? 'disabled' : ''} onclick="v4SourceTables.setPage(${this.currentPage - 1})">上一页</button>
              <span class="v4-st-page-num">${this.currentPage} / ${totalPages}</span>
              <button type="button" class="btn btn-outline btn-sm" ${this.currentPage >= totalPages ? 'disabled' : ''} onclick="v4SourceTables.setPage(${this.currentPage + 1})">下一页</button>
              <button type="button" class="btn btn-outline btn-sm" ${this.currentPage >= totalPages ? 'disabled' : ''} onclick="v4SourceTables.setPage(${totalPages})">末页 (保证可达)</button>
            </div>
          ` : ''}
        </div>
      `;

      viewerEl.innerHTML = `
        <div class="v4-st-active-header">
          <div class="v4-st-act-title-box">
            <span class="v4-st-act-cat ${esc(table.category)}">${esc(({unclassified:'待分类',results:'检测结果',standards:'质量标准',statistics:'统计',notes:'说明',mixed:'混合'})[table.category] || table.category_label || table.category)}</span>
            <h3 class="v4-st-act-title">${esc(table.caption)}</h3>
          </div>
          <div class="v4-st-act-meta">
            <span>原文件表编号：${table.table_id}</span>
            <span>· 规格/研究：${esc(table.study || '未指定')}</span>
            <span>· 来源完整性：${this.currentData.summary?.source_coverage === 'complete' ? '全部源格已核验' : '合成演示数据'}</span>
          </div>
        </div>

        ${paginationHtml}

        <div class="v4-st-table-scroll-wrapper">
          <table class="v4-st-table">
            ${headersHtml}
            ${bodyHtml}
          </table>
        </div>

        ${footnotesHtml}
      `;
    }

    applyHighlight() {
      if (!this.highlightCoord) return;
      const targetCoord = typeof this.highlightCoord === 'string'
        ? this.highlightCoord
        : `R${this.highlightCoord.row}C${this.highlightCoord.column}`;

      const cell = document.querySelector(`.v4-st-td[data-coord="${targetCoord}"]`);
      if (cell) {
        cell.classList.add('v4-cell-highlight');
        cell.scrollIntoView({ behavior: 'smooth', block: 'center', inline: 'center' });
      }
    }

    async inspectCellCandidate(candidateId) {
      if (window.v4Api?.mode === 'real') {
        try {
          const source = await window.v4Api.getSource(candidateId);
          const preview = await window.v4Api.getPreview(source.preview_id);
          const candidate = preview.candidates.find(c => c.id === candidateId);
          if (!candidate) throw new Error('候选不存在');
          window.v4Intake.currentPreview = preview;
          this.close(); window.v4SourceDrawer.openCandidate(candidate);
        } catch(error) { toast(error.message); }
        return;
      }
      if (!candidateId) return;
      // Close source tables modal and open evidence drawer for that candidate
      this.close();
      if (window.v4Intake && window.v4Intake.currentPreview) {
        const cand = window.v4Intake.currentPreview.candidates.find(c => c.id === candidateId);
        if (cand && window.v4SourceDrawer) {
          window.v4SourceDrawer.openCandidate(cand);
          return;
        }
      }
      if (window.v4Api?.mode === 'real') { toast('请先打开该归档的检测预览，不能推测候选内容'); return; }
      // If not in active intake, inspect mock/synthetic candidate
      if (window.v4SourceDrawer) {
        window.v4SourceDrawer.openCandidate({
          id: candidateId,
          status: 'different_value',
          detail: '来自来源全表归档（表 1 第 2 行第 8 列）关联检测候选',
          source_number: 'CoA-2025-0817',
          record: {
            key: { project: 'IP166E', batch: 'E202506002A1', item: 'SEC-HPLC 单体纯度', month: 12, condition: '5C' },
            result: { raw_text: '98.8%', value: '98.8', unit: '%' }
          },
          existing_record: {
            key: { project: 'IP166E', batch: 'E202506002A1', item: 'SEC-HPLC 单体纯度', month: 12, condition: '5C' },
            result: { raw_text: '98.1%', value: '98.1', unit: '%' }
          }
        });
      }
    }

    showImportPairDialog() {
      const pairHtml = `
        <div class="v4-st-pair-dialog">
          <h4>📥 历史资料一次性配对导入 (Word + 转换包)</h4>
          <p class="v4-st-pair-desc">
            为初始化历史 Word 全量表格，请选择原 Word 文档 (.docx) 及一次性结构提取转换包 (.json)。系统独立比对哈希与原文，归档全量表格。
          </p>
          <div class="v4-st-pair-form">
            <label class="v4-field">
              <span>1. 原 Word 报告文件 (.docx)</span>
              <input type="file" id="v4PairWordInput" accept=".docx" class="v4-input">
            </label>
            <label class="v4-field">
              <span>2. 对应全表提取转换包 (.json)</span>
              <input type="file" id="v4PairJsonInput" accept=".json" class="v4-input">
            </label>
            <div class="v4-btn-group" style="margin-top:14px">
              <button type="button" class="btn btn-primary" onclick="v4SourceTables.submitPairImport()">校验并保存来源归档</button>
            </div>
          </div>
          <div class="v4-st-pair-note">
            💡 <strong>注意：</strong>日常检验文件上传请直接使用页面主界面左侧上传区；本功能仅用于历史复杂大文件一次性初始化归档。
          </div>
        </div>
      `;

      const viewerEl = document.getElementById('v4StViewer');
      if (viewerEl) {
        viewerEl.innerHTML = pairHtml;
      }
    }

    async submitPairImport() {
      const wordInput = document.getElementById('v4PairWordInput');
      const jsonInput = document.getElementById('v4PairJsonInput');

      if (!wordInput || !wordInput.files || !wordInput.files.length) {
        if (typeof toast === 'function') toast('请选择原 Word 报告文件 (.docx)');
        return;
      }
      if (!jsonInput || !jsonInput.files || !jsonInput.files.length) {
        if (typeof toast === 'function') toast('请选择结构提取转换包 (.json)');
        return;
      }

      const wordFile = wordInput.files[0];
      const jsonFile = jsonInput.files[0];

      if (window.v4Api?.mode !== 'real') { toast('演示模式未写入数据库'); return; }
      if (this.importBusy) return;
      this.importBusy = true;
      try {
        const project = typeof currentProjectKey !== 'undefined' ? currentProjectKey : 'IP166E';
        const signature = [project, wordFile.name,wordFile.size,wordFile.lastModified,jsonFile.name,jsonFile.size,jsonFile.lastModified].join(':');
        if (this.importSignature !== signature) { this.importSignature = signature; this.importKey = crypto.randomUUID(); }
        const result = await window.v4Api.importSourcePair(project, wordFile, jsonFile, this.importKey);
        await this.open(null, null, result.import_id);
        toast(`来源归档已保存，共 ${result.summary.total_tables} 张表；未确认检测结果`);
      } catch (error) { toast('未完成归档：' + error.message); }
      finally { this.importBusy = false; }

    }
  }

  global.v4SourceTables = new V4SourceTablesManager();

  document.addEventListener('DOMContentLoaded', () => {
    // Add lightweight "来源全表查看" entry buttons in ledger & intake
    const ledgerToolbar = document.querySelector('#tab-ledger > .card .card-header');
    if (ledgerToolbar && !document.getElementById('v4OpenSourceTablesLedgerBtn')) {
      const btn = document.createElement('button');
      btn.id = 'v4OpenSourceTablesLedgerBtn';
      btn.type = 'button';
      btn.className = 'btn btn-outline';
      btn.style.fontSize = '11px';
      btn.style.padding = '4px 10px';
      btn.style.marginLeft = '8px';
      btn.innerHTML = '查看原文';
      btn.onclick = () => global.v4SourceTables.open();
      ledgerToolbar.appendChild(btn);
    }

    const intakeHeader = document.querySelector('#tab-intake .card-header .card-title');
    if (intakeHeader && !document.getElementById('v4OpenSourceTablesIntakeBtn')) {
      const span = document.createElement('span');
      span.style.marginLeft = 'auto';
      span.innerHTML = `<button type="button" class="btn btn-outline" id="v4OpenSourceTablesIntakeBtn" style="font-size:11px;padding:3px 10px;" onclick="v4SourceTables.open()">查看原文</button>`;
      intakeHeader.parentNode.appendChild(span);
    }
  });
})(typeof window !== 'undefined' ? window : this);
