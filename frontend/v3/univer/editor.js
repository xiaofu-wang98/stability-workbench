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

export function mountWorksheet(container, values, layout, editable, { onChange, onError, canEdit }) {
  const { univer, univerAPI } = createUniver({
    locale: LocaleType.ZH_CN,
    locales: { [LocaleType.ZH_CN]: mergeLocales(zhCN) },
    presets: [UniverSheetsCorePreset({
      container, header: false, toolbar: false, formulaBar: false, footer: false,
      contextMenu: false, disableAutoFocus: true,
    })],
  });
  const sheetId = 'study';
  const cellData = Object.fromEntries(values.map((row, r) => [r, Object.fromEntries(
    row.map((value, c) => [c, { v: value, t: CellValueType.STRING, s: r < 2 || c === 0 ? 'heading' : 'entry' }])
  )]));
  const workbook = univerAPI.createWorkbook({
    id: 'stability-worksheet',
    sheetOrder: [sheetId],
    styles: {
      heading: { bl: 1, bg: { rgb: '#edf3f8' }, tb: 3, n: { pattern: '@' } },
      entry: { tb: 3, n: { pattern: '@' } },
    },
    sheets: {
      [sheetId]: {
        id: sheetId, name: '研究工作表', cellData,
        rowCount: values.length, columnCount: values[1].length,
        defaultColumnWidth: 130, defaultRowHeight: 40,
        rowData: Object.fromEntries(Object.entries(layout.row_heights || {}).map(([i, h]) => [i, { h }])),
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
  const paste = event => {
    const text = event.clipboardData?.getData('text/plain');
    const range = workbook.getActiveSheet().getActiveRange()?.getRange();
    if (!editable || !range || !text || !container.contains(event.target)) return;
    event.preventDefault();
    event.stopImmediatePropagation();
    const rows = clipboardRows(text.replace(/\r\n/g, '\n'));
    const width = rows[0].length;
    if (rows.some((row, r) => row.length !== width ||
        row.some((_, c) => !canEdit(range.startRow + r, range.startColumn + c)))) {
      onError(`从第 ${range.startRow + 1} 行、第 ${range.startColumn + 1} 列开始的 ${rows.length} × ${width} 粘贴范围必须是连续应检格。`);
      return;
    }
    const target = workbook.getActiveSheet().getRange(range.startRow, range.startColumn, rows.length, width);
    target.setValues(rows.map(row => row.map(value => ({ v: value, t: CellValueType.STRING, s: 'entry' }))));
    onChange();
  };
  document.addEventListener('paste', paste, true);

  return {
    setEditable(value) { editable = value; workbook.setEditable(value); },
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
          row_heights: Object.fromEntries(Object.entries(sheet.rowData || {}).filter(([, row]) => row.h).map(([i, row]) => [i, Math.round(row.h)])),
          column_widths: Object.fromEntries(Object.entries(sheet.columnData || {}).filter(([, col]) => col.w).map(([i, col]) => [i, Math.round(col.w)])),
          title_merge_to: sheet.mergeData[0]?.endColumn || 0,
        },
      };
    },
    dispose() {
      document.removeEventListener('paste', paste, true);
      subscription.dispose();
      univer.dispose();
    },
  };
}
