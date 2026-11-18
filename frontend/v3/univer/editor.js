import { CellValueType, createUniver, LocaleType, mergeLocales } from '@univerjs/presets';
import { UniverSheetsCorePreset } from '@univerjs/preset-sheets-core';
import zhCN from '@univerjs/preset-sheets-core/locales/zh-CN';
import '@univerjs/preset-sheets-core/lib/index.css';

function clipboardRows(text) {
  const rows = [[]];
  let field = '', quoted = false;
  for (let i = 0; i < text.length; i++) {
    const char = text[i];
    if (char === '"' && quoted) {
      if (text[i + 1] === '"') { field += '"'; i++; }
      else quoted = false;
    } else if (char === '"' && !field && /^(?:[^"]|"")*"(?:[\t\r\n]|$)/.test(text.slice(i + 1))) {
      quoted = true;
    } else if (!quoted && (char === '\t' || char === '\r' || char === '\n')) {
      rows.at(-1).push(field);
      field = '';
      if (char !== '\t') {
        if (char === '\r' && text[i + 1] === '\n') i++;
        if (i === text.length - 1) return rows;
        rows.push([]);
      }
    } else {
      field += char;
    }
  }
  rows.at(-1).push(field);
  return rows;
}

export function mountWorksheet(container, values, layout, editable, { onChange, onError, canEdit, onSelect, fixedColumns = 1 }) {
  const { univer, univerAPI } = createUniver({
    locale: LocaleType.ZH_CN,
    locales: { [LocaleType.ZH_CN]: mergeLocales(zhCN) },
    presets: [UniverSheetsCorePreset({
      container, header: false, toolbar: false, formulaBar: false, footer: false,
      contextMenu: false,
    })],
  });
  const sheetId = 'study';
  const measure = document.createElement('canvas').getContext('2d');
  measure.font = '13px "Microsoft YaHei", sans-serif';
  const originalHeights = layout.row_heights || {};
  const displayedHeights = values.map((row, index) => {
    const fieldCount = index === 1 ? row.length : fixedColumns;
    const lines = Math.max(...row.slice(0, fieldCount).map((value, column) => {
      const width = (layout.column_widths?.[column] || (column === 0 ? 225 : 130)) - 16;
      return String(value).split('\n').reduce((total, line) => {
        let count = 1;
        let used = 0;
        for (const char of line) {
          const size = measure.measureText(char).width;
          if (used && used + size > width) { count++; used = 0; }
          used += size;
        }
        return total + count;
      }, 0);
    }));
    return Math.max(originalHeights[index] || 40, Math.min(350, lines * 18 + 4));
  });
  const cellData = Object.fromEntries(values.map((row, r) => [r, Object.fromEntries(
    row.map((value, c) => [c, { v: value, t: CellValueType.STRING, s: r === 0 || (r === 1 && c < fixedColumns) ? 'heading' : r === 1 || c < fixedColumns ? 'option' : 'entry' }])
  )]));
  const workbook = univerAPI.createWorkbook({
    id: 'stability-worksheet',
    sheetOrder: [sheetId],
    styles: {
      option: { bg: { rgb: '#d5e7f4' }, tb: 3, n: { pattern: '@' } },
      heading: { bl: 1, bg: { rgb: '#9ec4e2' }, tb: 3, n: { pattern: '@' } },
      entry: { tb: 3, n: { pattern: '@' } },
    },
    sheets: {
      [sheetId]: {
        id: sheetId, name: '研究工作表', cellData,
        rowCount: values.length, columnCount: values[1].length,
        defaultColumnWidth: 130, defaultRowHeight: 40,
        rowData: Object.fromEntries(displayedHeights.map((h, index) => [index, { h }])),
        freeze: { xSplit: fixedColumns, ySplit: 0, startColumn: fixedColumns, startRow: -1 },
        columnData: {
          0: { w: 225 },
          ...Object.fromEntries(Object.entries(layout.column_widths || {}).map(([i, w]) => [i, { w }])),
        },
        mergeData: layout.title_merge_to ? [{ startRow: 0, endRow: 0, startColumn: 0, endColumn: layout.title_merge_to }] : [],
      },
    },
  });
  workbook.setEditable(editable);
  const subscription = univerAPI.addEvent(univerAPI.Event.SheetValueChanged, onChange);
  const editGuard = univerAPI.addEvent(univerAPI.Event.BeforeSheetEditStart, event => {
    if (!editable || !canEdit(event.row, event.column)) {
      event.cancel = true;
      if (editable) onError('请选择应检结果格录入。');
    }
  });
  const selectionListeners = onSelect ? [univerAPI.Event.SelectionMoveEnd, univerAPI.Event.SelectionChanged]
    .map(name => univerAPI.addEvent(name, event => onSelect(event.selections.at(-1)))) : [];
  const isLegalPlaceholder = val => {
    if (val === null || val === undefined) return true;
    const s = String(val).trim().toLowerCase();
    return s === '' || s === 'n/a' || s === 'na' || s === 'nt' ||
           s === '—' || s === '–' || s === '─' || s === '-' || s === '--' || s === '---' ||
           s === '/' || s === '／';
  };

  const paste = event => {
    const text = event.clipboardData?.getData('text/plain');
    const range = workbook.getActiveSheet().getActiveRange()?.getRange();
    if (!editable || !range || !text || !container.contains(event.target) || workbook.isCellEditing()) return;
    event.preventDefault();
    event.stopImmediatePropagation();
    const rows = clipboardRows(text.replace(/\r\n/g, '\n'));
    if (!rows.length || !rows[0].length) return;
    const width = rows[0].length;
    if (rows.some(row => row.length !== width)) {
      onError('粘贴内容每行列数不一致。');
      return;
    }
    if (range.startRow + rows.length > values.length || range.startColumn + width > values[0].length) {
      onError(`从第 ${range.startRow + 1} 行、第 ${range.startColumn + 1} 列开始的 ${rows.length} × ${width} 粘贴范围超出表格范围。`);
      return;
    }

    const conflicts = [];
    for (let r = 0; r < rows.length; r++) {
      for (let c = 0; c < width; c++) {
        const targetR = range.startRow + r;
        const targetC = range.startColumn + c;
        const val = rows[r][c];
        const allowed = canEdit(targetR, targetC);
        if (!allowed) {
          if (!isLegalPlaceholder(val)) {
            conflicts.push({ row: targetR + 1, col: targetC + 1, value: val });
          }
        }
      }
    }

    if (conflicts.length > 0) {
      const summary = conflicts.slice(0, 3).map(item => `第 ${item.row} 行、第 ${item.col} 列（收到「${item.value}」）`).join('；');
      const more = conflicts.length > 3 ? ` 等共 ${conflicts.length} 处冲突` : '';
      onError(`非应检位置不得覆盖。发现冲突坐标：${summary}${more}。`);
      return;
    }

    const currentSheet = workbook.save().sheets[sheetId];
    const matrix = rows.map((row, r) => row.map((value, c) => {
      const targetR = range.startRow + r;
      const targetC = range.startColumn + c;
      if (canEdit(targetR, targetC)) {
        return { v: value, t: CellValueType.STRING, s: 'entry' };
      }
      const existing = currentSheet?.cellData?.[targetR]?.[targetC];
      return existing || { v: '—', t: CellValueType.STRING, s: 'option' };
    }));

    const target = workbook.getActiveSheet().getRange(range.startRow, range.startColumn, rows.length, width);
    target.setValues(matrix);
    onChange();
  };
  document.addEventListener('paste', paste, true);

  return {
    setEditable(value) { editable = value; workbook.setEditable(value); },
    async finishEditing() { if (workbook.isCellEditing()) await workbook.endEditingAsync(); },
    selection() { return workbook.getActiveSheet().getActiveRange()?.getRange(); },
    mergeTitle(merge) {
      const range = workbook.getActiveSheet().getRange(0, 0, 1, values[1].length);
      if (merge) range.merge();
      else range.breakApart();
    },
    snapshot() {
      const sheet = workbook.save().sheets[sheetId];
      if (sheet.rowCount !== values.length || sheet.columnCount !== values[1].length) {
        throw new Error('研究表的行列由计划管理，请还原新增或删除的行列。');
      }
      const rows = values.map((row, r) => row.map((_, c) => {
        const cell = sheet.cellData?.[r]?.[c];
        if (cell?.f) throw new Error('结果区只接受检测结果文本，请去除公式。');
        return cell?.p?.body?.dataStream?.replace(/\r\n$/, '') ?? String(cell?.v ?? '');
      }));
      if (sheet.mergeData.some(range => range.startRow !== 0 || range.endRow !== 0 || range.startColumn !== 0)) {
        throw new Error('只能合并标题行。');
      }
      return {
        rows,
        layout: {
          row_heights: Object.fromEntries(Object.entries(sheet.rowData || {}).flatMap(([i, row]) => {
            const height = Math.round(row.h);
            if (height !== displayedHeights[i]) return [[i, height]];
            return originalHeights[i] ? [[i, originalHeights[i]]] : [];
          })),
          column_widths: Object.fromEntries(Object.entries(sheet.columnData || {}).filter(([, col]) => col.w).map(([i, col]) => [i, Math.round(col.w)])),
          title_merge_to: sheet.mergeData[0]?.endColumn || 0,
        },
      };
    },
    dispose() {
      document.removeEventListener('paste', paste, true);
      subscription.dispose();
      editGuard.dispose();
      selectionListeners.forEach(listener => listener.dispose());
      univer.dispose();
    },
  };
}
