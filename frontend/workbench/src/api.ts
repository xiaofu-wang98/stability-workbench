export async function api<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch('/api/v1' + path, init);
  if (!response.ok) {
    const error = await response.json().catch(() => ({}));
    const detail = Array.isArray(error.detail) ? error.detail.map((item: { loc?: string[]; msg?: string }) =>
      `${(item.loc ?? []).filter(part => part !== 'body').join(' / ')}：${item.msg || '请核对填写内容'}`).join('；') : '';
    throw new Error(error.message || (typeof error.detail === 'string' ? error.detail : detail) || ('请求失败（' + response.status + '）'));
  }
  return response.json();
}

export async function archiveStudyObject(path: string, label: string): Promise<boolean> {
  const impact = await api<{ fingerprint: string; worksheets: { name: string }[];
    results: { value: string; studies: { name: string }[] }[] }>(path + '/archive-impact');
  const lines = [`删除${label}？`, `涉及 ${impact.worksheets.length} 张研究表，将作废 ${impact.results.length} 个正式结果。`,
    ...impact.worksheets.map(sheet => sheet.name),
    ...impact.results.map(result => [result.value, ...result.studies.map(study => study.name)].join(' · '))];
  const basis = window.prompt(lines.join('\n') + '\n请输入删除原因');
  if (!basis?.trim()) return false;
  await api(path, { method: 'DELETE', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ basis: basis.trim(), fingerprint: impact.fingerprint, idempotency_key: crypto.randomUUID() }) });
  return true;
}

export type StandardAssessment = { status: 'within' | 'outside' | 'undetermined'; criterion?: string; version?: string };

export interface Project {
  project: string;
  name: string;
  revision: number;
  purpose?: 'formal' | 'sandbox';
}

export const headerFieldLabels = {
  product_name: '产品中英文名称',
  equipment: '设备名称及编号',
  protocol_number: '稳定性试验方案编号及版本',
  specification_text: '交付规格',
  location: '稳定性开展地点',
  storage_location: '存放位置',
};
export type HeaderField = keyof typeof headerFieldLabels;
export type HeaderSource = { filename: string; sha256: string; sheet: string; cell: string; raw: string; reference_only?: boolean };
export type HeaderMetadataPreview = Record<HeaderField, string> & {
  metadata_sources: Record<HeaderField, HeaderSource[]>;
  metadata_fingerprint: string;
};

export function triggerDownload(url: string, filename?: string) {
  const a = document.createElement('a');
  a.href = url;
  if (filename) a.download = filename;
  a.style.display = 'none';
  document.body.appendChild(a);
  a.click();
  setTimeout(() => a.remove(), 1000);
}

export interface SampleEntry {
  id: string;
  project: string;
  batch: string;
  material_stage: string;
  sample_type: string;
  presentation: string;
  packaging: string;
  purpose: string;
  source: string;
  created_at: string;
  deleted_at: string | null;
}

export interface CoaZeroBatchSource {
  specification_id: string;
  version: number;
  source_id: string;
  value: string;
  filename: string;
  criterion_key: string;
}

export interface CoaZeroBatchRow {
  row_id: string;
  item_id: string;
  label: string;
  method: string;
  method_version: string;
  unit: string;
  slot_id: string;
  status: 'matched' | 'unmatched' | 'existing' | 'conflict';
  reason: 'sample_missing' | 'no_applicable_coa' | 'standard_scope_conflict' | 'multiple_coa_matches' | 'invalid_coa_value' | 'release_reference_exists' | 'result_history_exists' | 'worksheet_slot_conflict' | 'worksheet_occupied' | null;
  adopted_version_id: string | null;
  adopted_value: string | null;
  candidates: CoaZeroBatchSource[];
}

export interface CoaZeroBatchPreview {
  id: string;
  revision: number;
  sample_id: string | null;
  point_id: string | null;
  release_completed: string;
  preview_fingerprint: string;
  rows: CoaZeroBatchRow[];
}

export interface CoaZeroBatchResult {
  id: string;
  revision: number;
  plan_version: number;
  project_revision: number;
  result_version_ids: string[];
  copied: Record<string, string>;
}

export interface WorksheetSummary {
  id: string;
  name: string;
  plan_version: number;
  sample_id?: string | null;
  batch?: string;
  condition?: string;
  study?: 'long_term' | 'accelerated' | 'stress';
}

export interface Worksheet extends WorksheetSummary {
  sample_id?: string | null;
  project: string;
  identity: { batch: string; condition: string; orientation: string; study_id: string; study: string;
    sample_type: string; presentation: string; purpose: string; reporting_basis: string };
  rows: { id: string; item_id: string; label: string; source_test_item?: string; method: string; unit: string;
    plan_key?: string; reporting_basis?: string;
    category_code?: string; category_name_zh?: string; category_order?: number;
    subcategory_name_zh?: string; display_order?: number;
    criteria_text?: string;
    source_criteria?: string[];
    standards?: { specification_id: string; filename: string; table: number; row: number; criterion: string;
      plot_limits?: { lower: number | null; upper: number | null; unit: string } }[] }[];
  points: { id: string; label: string; planned_date: string; signed_status?: '' | '已签' | '已签报告'; required_row_ids: string[] }[];
  cells: { row_id: string; point_id: string; raw: string; release_reference?: { current: boolean }; standard_assessment?: StandardAssessment;
    adopted: { text: string; result_version_id: string } | null; display: { text: string; result_version_id: string } }[];
  start_date: string;
  equipment?: string;
  location?: string;
  product_name?: string;
  protocol_number?: string;
  specification_text?: string;
  storage_location?: string;
  legacy_location?: string;
  invalidated_release_rows?: string[];
  stopped_checks?: { row_id: string; point_ids: string[]; basis: string }[];
  pending_release_references?: Record<string, { table: number; row: number; column: number; raw: string }>;
  release_references?: Record<string, { result_version_id: string; release_completed: string; basis: string;
    coa?: { source_id: string; version: number } }>;
  layout: { row_heights: Record<string, number>; column_widths: Record<string, number>; title_merge_to: number };
  editable: boolean;
  occupied_by?: string | null;
  protocol_draft_id?: string;
  protocol_table?: number;
  events?: { id: string; category: string; reference: string; description: string; item_label: string;
    point_label: string; scan_id: string | null }[];
  copied_from?: { sheet_id: string; plan_version: number };
  revision: number;
  selection_id: string;
}
