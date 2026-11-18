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

export type HeaderField = 'equipment' | 'location';
export type HeaderSource = { filename: string; sha256: string; sheet: string; cell: string; raw: string };
export type HeaderMetadataPreview = Record<HeaderField, string> & {
  metadata_sources: Record<HeaderField, HeaderSource[]>;
  metadata_fingerprint: string;
};

export interface WorksheetSummary {
  id: string;
  name: string;
  plan_version: number;
  batch?: string;
  condition?: string;
}

export interface Worksheet extends WorksheetSummary {
  sample_id?: string | null;
  project: string;
  identity: { batch: string; condition: string; orientation: string; study_id: string; study: string;
    sample_type: string; presentation: string; purpose: string; reporting_basis: string };
  rows: { id: string; item_id: string; label: string; method: string; unit: string;
    plan_key?: string; reporting_basis?: string;
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
  invalidated_release_rows?: string[];
  stopped_checks?: { row_id: string; point_ids: string[]; basis: string }[];
  pending_release_references?: Record<string, { table: number; row: number; column: number; raw: string }>;
  release_references?: Record<string, { result_version_id: string; release_completed: string; basis: string;
    coa?: { source_id: string; version: number } }>;
  layout: { row_heights: Record<string, number>; column_widths: Record<string, number>; title_merge_to: number };
  editable: boolean;
  protocol_draft_id?: string;
  protocol_table?: number;
  events?: { id: string; category: string; reference: string; description: string; item_label: string;
    point_label: string; scan_id: string | null }[];
  copied_from?: { sheet_id: string; plan_version: number };
  revision: number;
  selection_id: string;
}
