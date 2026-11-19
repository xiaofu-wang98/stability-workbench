export type Revision = { active_text?: string; all_deleted?: boolean; deleted_word?: boolean;
  segments: { struck: boolean; text: string }[] };

export function sourceRevision(detail: string): Revision | undefined {
  if (!detail.trimStart().startsWith('{')) return;
  try {
    const revision = JSON.parse(detail) as Revision;
    if (Array.isArray(revision.segments)) return revision;
  } catch { /* Unstructured details are handled by sourceReviewText. */ }
}

export function sourceReviewText(detail: string, revision = sourceRevision(detail)): string {
  if (!revision) return detail.trimStart().startsWith('{') ? '原格有待核对的结构信息，请打开来源任务。' : detail;
  const deleted = revision.segments.filter(segment => segment.struck).map(segment => segment.text.trim()).filter(Boolean).join('、');
  if (revision.deleted_word) return '原件明确标记为“删／删除”。请核对删除依据或另有依据的正确结果。';
  if (revision.all_deleted || !revision.active_text?.trim()) {
    return (deleted ? '原值 ' + deleted + ' 已全部划除。' : '原格内容已全部划除。') + '当前无有效保留值，请核对结果归属。';
  }
  return '原格部分划线。未划线正文：' + revision.active_text.trim()
    + (deleted ? '。已划除：' + deleted : '') + '。请对照原件核对。';
}

export const sourceFieldLabels: Record<string, string> = {
  batch: '批号', presentation: '规格', sample_type: '材料类别', study: '考察类型', study_id: '方案编号',
  condition: '考察条件', test_item: '检项', subresult: '子结果', method: '方法', method_version: '方法版本',
  result: '结果', unit: '单位', time_point: '时间点', criterion: '标准', purpose: '用途',
  reporting_basis: '报告体系', source_number: '来源编号', test_date: '检测日期',
};

export function cellAddress(row: number, column: number): string {
  let name = '';
  for (let value = column; value > 0; value = Math.floor((value - 1) / 26)) {
    name = String.fromCharCode(65 + (value - 1) % 26) + name;
  }
  return name + row;
}
