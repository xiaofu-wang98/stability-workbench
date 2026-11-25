import { ref } from 'vue';
import type { RouteLocationRaw } from 'vue-router';
import { api } from '../api';

export type TodoCategory =
  | 'local_recovery'
  | 'import_draft'
  | 'worksheet_draft'
  | 'pending_result'
  | 'scan_ocr'
  | 'protocol_draft';

export interface TodoItem {
  id: string;
  category: TodoCategory;
  categoryLabel: string;
  severity: 'error' | 'warning' | 'info';
  project?: string;
  title: string;
  detail: string;
  actionLabel: string;
  to: RouteLocationRaw;
  priority: number;
}

export interface RecentDeliveryItem {
  id: string;
  project: string;
  created_at: string;
  filename: string;
  attachment_filename?: string;
  report_version?: string;
}

type PreviewSummary = { preview_id: string; filename: string; draft: number };
type WorksheetDraft = { id: string; name: string; created_at: string; protocol_draft: string; protocol_table: string; copy_source: string };
type PlanDraft = { id: string; filename: string; created_at?: string };
type ScanSummary = { id: string; filename: string; status: string };

function readLocalWorksheetRecoveries(): TodoItem[] {
  const found: TodoItem[] = [];
  try {
    for (let i = 0; i < sessionStorage.length; i++) {
      const key = sessionStorage.key(i);
      if (!key || !key.startsWith('stability:worksheet:')) continue;
      const sheetId = key.slice('stability:worksheet:'.length);
      const raw = sessionStorage.getItem(key);
      if (!raw || !sheetId) continue;
      const parsed = JSON.parse(raw) as { snapshot?: { rows?: string[][] } };
      const title = parsed.snapshot?.rows?.[0]?.[0] || sheetId;
      found.push({
        id: 'local:' + sheetId,
        category: 'local_recovery',
        categoryLabel: '本地未提交改动',
        severity: 'error',
        title: `研究表「${title}」有未保存的本地编辑草稿`,
        detail: '当前浏览器标签页存在未提交表格修改，建议尽快恢复并保存，避免丢失。',
        actionLabel: '恢复编辑',
        to: '/worksheets/' + encodeURIComponent(sheetId),
        priority: 1,
      });
    }
  } catch {
    // Ignore storage read errors.
  }
  return found;
}

export function useTodoAggregator() {
  const items = ref<TodoItem[]>([]);
  const recentDeliveries = ref<RecentDeliveryItem[]>([]);
  const loading = ref(false);
  const error = ref('');
  const lastUpdatedAt = ref<number>(0);

  let activeController: AbortController | null = null;

  async function refresh(projectCodes: string[]) {
    activeController?.abort();
    const controller = new AbortController();
    activeController = controller;
    const init = { signal: controller.signal };

    loading.value = true;
    error.value = '';
    const collected: TodoItem[] = [...readLocalWorksheetRecoveries()];

    try {
      // L1: Global O(1) endpoints
      const [planDrafts, scans, deliveries] = await Promise.all([
        api<PlanDraft[]>('/plan-drafts', init).catch(() => [] as PlanDraft[]),
        api<ScanSummary[]>('/pdf-ocr', init).catch(() => [] as ScanSummary[]),
        api<RecentDeliveryItem[]>('/deliveries?limit=6', init).catch(() => [] as RecentDeliveryItem[]),
      ]);
      if (controller.signal.aborted) return;

      recentDeliveries.value = deliveries.slice(0, 6);

      // Scan OCR pending/failed/running
      for (const scan of scans) {
        if (['archived', 'running', 'failed', 'interrupted'].includes(scan.status)) {
          const statusMap: Record<string, string> = {
            archived: '已归档待开始识别',
            running: '正在识别中',
            failed: '识别失败待重试',
            interrupted: '上次识别中断',
          };
          collected.push({
            id: 'scan:' + scan.id,
            category: 'scan_ocr',
            categoryLabel: '扫描 PDF',
            severity: scan.status === 'failed' || scan.status === 'interrupted' ? 'error' : 'info',
            title: scan.filename,
            detail: `当前状态：${statusMap[scan.status] || scan.status}，识别完成后可登记质量标准条款。`,
            actionLabel: '处理扫描件',
            to: { path: '/scans', query: { scan: scan.id } },
            priority: scan.status === 'failed' ? 2 : 5,
          });
        }
      }

      // Recent uploaded protocol drafts (top 4)
      for (const draft of planDrafts.slice(0, 4)) {
        collected.push({
          id: 'plan:' + draft.id,
          category: 'protocol_draft',
          categoryLabel: '已上传方案',
          severity: 'info',
          title: draft.filename,
          detail: '已归档的稳定性方案 Word，可直接查看识别矩阵或按矩阵新建研究表。',
          actionLabel: '查看矩阵建表',
          to: { path: '/import', query: { mode: 'protocol', planDraft: draft.id } },
          priority: 6,
        });
      }

      // L2: Scoped to monitored projects (max 5, zero N+1 at 300+ scale)
      const scopedProjects = projectCodes.slice(0, 5);
      await Promise.all(scopedProjects.map(async (code) => {
        const base = '/projects/' + encodeURIComponent(code);
        const [previews, wsDrafts, pendingResults] = await Promise.all([
          api<PreviewSummary[]>(base + '/previews?limit=20', init).catch(() => [] as PreviewSummary[]),
          api<WorksheetDraft[]>(base + '/worksheet-drafts', init).catch(() => [] as WorksheetDraft[]),
          api<unknown[]>(base + '/pending-results', init).catch(() => [] as unknown[]),
        ]);
        if (controller.signal.aborted) return;

        for (const p of previews) {
          if (p.draft > 0) {
            collected.push({
              id: `preview:${code}:${p.preview_id}`,
              category: 'import_draft',
              categoryLabel: '未提交导入',
              severity: 'warning',
              project: code,
              title: p.filename,
              detail: `尚有 ${p.draft} 条解析候选未核对提交，点击继续上次工作表核对。`,
              actionLabel: '继续核对',
              to: { path: '/import', query: { project: code, source: p.preview_id } },
              priority: 2,
            });
          }
        }

        for (const d of wsDrafts) {
          collected.push({
            id: `wsdraft:${code}:${d.id}`,
            category: 'worksheet_draft',
            categoryLabel: '待补全研究表',
            severity: 'warning',
            project: code,
            title: d.name || '未命名研究表草稿',
            detail: `保存于 ${d.created_at || '近期'}，补齐研究信息后即可正式建表。`,
            actionLabel: '续做建表',
            to: {
              path: '/new',
              query: {
                project: code,
                resume: d.id,
                draft: d.protocol_draft || undefined,
                table: d.protocol_table || undefined,
                copy: d.copy_source || undefined,
              },
            },
            priority: 3,
          });
        }

        if (pendingResults.length > 0) {
          collected.push({
            id: `pending:${code}`,
            category: 'pending_result',
            categoryLabel: '待复核结果',
            severity: 'warning',
            project: code,
            title: `项目 ${code} 有 ${pendingResults.length} 条待采用/待复核检测结果`,
            detail: '已入库但尚未采用或等待复核的结果记录，建议前往项目台账确认。',
            actionLabel: '查看结果',
            to: { path: '/projects/' + encodeURIComponent(code), query: { focus: 'results' } },
            priority: 4,
          });
        }
      }));

      if (controller.signal.aborted) return;
      collected.sort((a, b) => a.priority - b.priority);
      items.value = collected;
      lastUpdatedAt.value = Date.now();
    } catch (e) {
      if (!controller.signal.aborted) {
        error.value = (e as Error).message;
      }
    } finally {
      if (!controller.signal.aborted) {
        loading.value = false;
      }
    }
  }

  return {
    items,
    recentDeliveries,
    loading,
    error,
    lastUpdatedAt,
    refresh,
  };
}
