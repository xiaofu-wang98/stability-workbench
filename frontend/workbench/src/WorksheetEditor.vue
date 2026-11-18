<script setup lang="ts">
import { nextTick, onBeforeUnmount, ref } from 'vue';
import { onBeforeRouteLeave, onBeforeRouteUpdate } from 'vue-router';
import { NAlert, NButton, NInput, NSelect } from 'naive-ui';
import { api, headerFieldLabels, type Worksheet, type HeaderField, type HeaderMetadataPreview } from './api';
import WorksheetStandards from './WorksheetStandards.vue';
import WorksheetRelease from './WorksheetRelease.vue';
import WorksheetProtocol from './WorksheetProtocol.vue';
import WorksheetEvents from './WorksheetEvents.vue';
import WorksheetHeaderSources from './WorksheetHeaderSources.vue';

const props = defineProps<{ sheet: Worksheet }>();
const emit = defineEmits<{ saved: [Worksheet]; editing: [boolean] }>();
type Snapshot = { rows: string[][]; layout: Worksheet['layout'] };
type Recovery = { selection: string; snapshot: Snapshot; reason: string };
type Range = { startRow: number; endRow: number; startColumn: number; endColumn: number };
type Editor = { snapshot(): Snapshot; selection(): Range | undefined; setEditable(value: boolean): void;
  finishEditing(): Promise<void>; mergeTitle(merge: boolean): void; dispose(): void };
type Change = { row_id: string; point_id: string; value: string; intent: 'initial' | 'correction'; expected_version_id: string | null };

const grid = ref<HTMLDivElement>();
const activeInspection = ref<{ item: string; criteria: string; point: string }>();
const editing = ref(false);
const busy = ref(false);
const dirty = ref(false);
const expired = ref(false);
const error = ref('');
const status = ref('');
const reason = ref('');
const requiresReason = ref(false);
const confirmDiscard = ref(false);
const plan = ref(false);
const standards = ref(false);
const releaseForm = ref(false);
const pointEditor = ref<Worksheet['points'][number]>();
const protocolForm = ref(false);
const eventForm = ref(false);
const headerFields = Object.keys(headerFieldLabels) as HeaderField[];
const headerForm = ref<Record<HeaderField, string> & { basis: string; metadata_fingerprint?: string }>();
const headerPreview = ref<HeaderMetadataPreview>();
const sourceError = ref('');
const planItems = ref<string[]>([]);
const retest = ref<{ rowId: string; pointId: string; label: string; version: string }>();
const retestBasis = ref('');
const showDraftText = ref(false);
const correctionReasons = ['数据录入笔误更正', '复核原始检测记录后更正', '依据仪器重积分结果更正', '依据复核人检验报告更正'];
const retestReasons = ['时间点常规复测', '异常数据复测调查', '不同检验员比对复测', '仪器维护后复测确认'];
let editor: Editor | undefined;
let baseline: Snapshot;
let current: Worksheet;
let token = '';
let timer: ReturnType<typeof setInterval> | undefined;
let disposed = false;
let retry: { signature: string; key: string } | undefined;
let styles: HTMLLinkElement | undefined;
const recoveryKey = 'stability:worksheet:' + props.sheet.id;
function readRecovery(): Recovery | undefined {
  try {
    const raw = sessionStorage.getItem(recoveryKey);
    return raw ? JSON.parse(raw) : undefined;
  } catch { return undefined; }
}
const recovery = ref(readRecovery());
function forgetRecovery() {
  recovery.value = undefined;
  try { sessionStorage.removeItem(recoveryKey); } catch { /* storage may be unavailable */ }
}
function rememberRecovery() {
  if (!editor || !dirty.value || !current) return;
  try {
    const snapshot = editor.snapshot();
    const draft = { selection: current.selection_id, snapshot, reason: reason.value };
    sessionStorage.setItem(recoveryKey, JSON.stringify(draft));
    recovery.value = draft;
  } catch { /* Keep the live editor if a snapshot or local storage is unavailable. */ }
}

const path = () => '/worksheets/' + encodeURIComponent(props.sheet.id);
function post<T>(suffix: string, body?: unknown, keepalive = false) {
  return api<T>(path() + suffix, {
    method: 'POST', keepalive,
    headers: { 'Content-Type': 'application/json' },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
}

function release() {
  clearInterval(timer);
  if (!token) return;
  const previous = token;
  token = '';
  return post('/lease/release', { token: previous }, true).catch(() => undefined);
}

async function renew() {
  const previous = token;
  try {
    await post('/lease/renew', { token: previous });
  } catch {
    if (token !== previous || disposed) return;
    clearInterval(timer);
    expired.value = true;
    editor?.setEditable(false);
    rememberRecovery();
    error.value = '编辑租约已失效或网络中断。未提交的内容已自动保存在本地草稿中；请勿抢占他人租约，网络或租约恢复后可点击「恢复本地草稿」重新载入。';
  }
}

async function mount(restore = false) {
  const latest = await api<Worksheet>(path());
  const url = '/assets/worksheet/editor.js';
  if (disposed) return;
  if (!styles) {
    const link = document.createElement('link');
    link.rel = 'stylesheet';
    link.href = '/assets/worksheet/editor.css';
    styles = link;
    await new Promise<void>((resolve, reject) => {
      link.onload = () => resolve();
      link.onerror = () => {
        link.remove();
        styles = undefined;
        reject(new Error('编辑器样式未能加载，请检查组件构建。'));
      };
      document.head.append(link);
    });
  }
  const { mountWorksheet } = await import(/* @vite-ignore */ url);
  if (disposed) return;
  current = latest;
  editing.value = true;
  emit('editing', true);
  await nextTick();
  if (!grid.value || disposed) return;
  editor?.dispose();
  activeInspection.value = undefined;
  const existing = new Map(latest.cells.map(cell => [cell.row_id + ':' + cell.point_id, cell]));
  const values = [
    [latest.name, ...latest.points.map(() => '')],
    ['检验项目 / Test Item', ...latest.points.map(point => [point.label, point.planned_date, point.signed_status].filter(Boolean).join('\n'))],
    ...latest.rows.map(row => [
      [row.source_test_item, row.source_test_item === row.label ? '' : row.label, row.method,
        row.reporting_basis ?? latest.identity.reporting_basis, row.unit].filter(Boolean).join('\n'),
      ...latest.points.map(point => existing.get(row.id + ':' + point.id)?.raw ??
        (point.required_row_ids.includes(row.id) ? '' : '—')),
    ]),
  ];
  const hooks = {
    canEdit: (r: number, c: number) => latest.points[c - 1]?.required_row_ids.includes(latest.rows[r - 2]?.id)
      && !(latest.points[c - 1]?.label === '0M' && (latest.pending_release_references?.[latest.rows[r - 2]?.id]
        || latest.invalidated_release_rows?.includes(latest.rows[r - 2]?.id)))
      && !existing.get(latest.rows[r - 2]?.id + ':' + latest.points[c - 1]?.id)?.release_reference,
    onChange: () => {
      if (busy.value || disposed) return;
      dirty.value = true;
      status.value = '';
      confirmDiscard.value = false;
      rememberRecovery();
    },
    onError: (message: string) => { error.value = message; },
  };
  // Keep persisted snapshots and result coordinates unchanged; the IP281 criteria
  // column is a read-only presentation column, never a result cell.
  function mountTemplate(rows: string[][], layout: Worksheet['layout']): Editor {
    const criteria = [ '', '可接受标准 / Acceptable Criteria', ...latest.rows.map(row =>
      [row.criteria_text, ...(row.standards ?? []).map(standard => standard.criterion)].filter(Boolean).join('\n')) ];
    const displayed = rows.map((row, index) => [row[0], criteria[index], ...row.slice(1)]);
    const visualLayout = { ...layout,
      column_widths: { 1: 225, ...Object.fromEntries(Object.entries(layout.column_widths || {}).map(([key, value]) => [Number(key) > 0 ? Number(key) + 1 : 0, value])) },
      title_merge_to: layout.title_merge_to ? layout.title_merge_to + 1 : 0,
    };
    const mounted = mountWorksheet(grid.value, displayed, visualLayout, !expired.value, {
      ...hooks, fixedColumns: 2,
      canEdit: (r: number, c: number) => c > 1 && hooks.canEdit(r, c - 1),
      onSelect: (range?: Range) => {
        const index = (range?.startRow ?? 0) - 2;
        activeInspection.value = index >= 0 && index < latest.rows.length ? {
          item: rows[index + 2][0], criteria: criteria[index + 2],
          point: latest.points[(range?.startColumn ?? 0) - 2]?.label || '',
        } : undefined;
      },
    });
    return {
      ...mounted,
      snapshot() {
        const value = mounted.snapshot() as Snapshot;
        if (value.rows.some((row, index) => row[1] !== criteria[index])) throw new Error('可接受标准请通过“关联适用标准”修改，不能在结果区覆盖。');
        return { rows: value.rows.map(row => [row[0], ...row.slice(2)]), layout: {
          ...value.layout,
          column_widths: Object.fromEntries(Object.entries(value.layout.column_widths).filter(([key]) => Number(key) !== 1).map(([key, width]) => [Number(key) > 1 ? Number(key) - 1 : 0, width])),
          title_merge_to: value.layout.title_merge_to > 1 ? value.layout.title_merge_to - 1 : 0,
        } };
      },
      selection() {
        const selection = mounted.selection() as Range | undefined;
        return selection && { ...selection, startColumn: selection.startColumn > 1 ? selection.startColumn - 1 : -1,
          endColumn: selection.endColumn > 1 ? selection.endColumn - 1 : -1 };
      },
    };
  }
  editor = mountTemplate(values, latest.layout);
  if (!editor) throw new Error('工作表编辑器无法初始化。');
  baseline = editor.snapshot();
  dirty.value = false;
  retry = undefined;
  requiresReason.value = false;
  reason.value = '';
  const draft = recovery.value;
  if (restore && draft) {
    const rows = draft.snapshot?.rows;
    if (draft.selection === latest.selection_id && Array.isArray(rows) && rows.length === values.length
        && rows.every((row, index) => Array.isArray(row) && row.length === values[index].length && row.every(value => typeof value === 'string'))) {
      editor.dispose();
      editor = mountTemplate(rows, draft.snapshot.layout);
      dirty.value = true;
      reason.value = draft.reason || '';
      status.value = '已恢复本标签页的未保存改动。';
    } else {
      status.value = '表格或结果版本已变化，未自动恢复。可复制原草稿供人工核对。';
    }
  }
  emit('saved', latest);
}

async function begin() {
  busy.value = true;
  error.value = '';
  expired.value = false;
  try {
    const lease = await post<{ token: string }>('/lease');
    token = lease.token;
    if (disposed) { await release(); return; }
    timer = setInterval(renew, 25_000);
    await mount(true);
  } catch (e) {
    error.value = (e as Error).message;
    await release();
    editor?.dispose();
    editor = undefined;
    editing.value = false;
    emit('editing', false);
  } finally {
    busy.value = false;
  }
}

async function save() {
  if (!editor || expired.value) return;
  busy.value = true;
  error.value = '';
  try {
    await editor.finishEditing();
    editor.setEditable(false);
    const snapshot = editor.snapshot();
    const changes: Change[] = [];
    const existing = new Map(current.cells.map(cell => [cell.row_id + ':' + cell.point_id, cell]));
    snapshot.rows.forEach((row, r) => row.forEach((value, c) => {
      if (value === baseline.rows[r][c]) return;
      const item = current.rows[r - 2], point = current.points[c - 1];
      if (!item || !point?.required_row_ids.includes(item.id)) throw new Error('请还原标题、日期、检项或非应检单元格中的改动。');
      const previous = existing.get(item.id + ':' + point.id);
      changes.push({ row_id: item.id, point_id: point.id, value,
        intent: previous ? 'correction' : 'initial', expected_version_id: previous?.adopted?.result_version_id ?? null });
    }));
    if (JSON.stringify(snapshot) === JSON.stringify(baseline)) {
      status.value = '没有待保存的改动。';
      dirty.value = false;
      if (recovery.value?.selection === current.selection_id) forgetRecovery();
      return;
    }
    requiresReason.value = changes.some(change => change.intent === 'correction');
    if (requiresReason.value && !reason.value.trim()) return;
    const body = { token, revision: current.revision, reason: reason.value.trim(), changes, layout: snapshot.layout };
    const signature = JSON.stringify(body);
    if (retry?.signature !== signature) retry = { signature, key: crypto.randomUUID() };
    await post('/save', { ...body, idempotency_key: retry.key });
    forgetRecovery();
    dirty.value = false;
    status.value = '已保存。';
    try {
      await mount();
    } catch {
      expired.value = true;
      error.value = '结果已保存，重新读取失败。请结束编辑并刷新。';
    }
  } catch (e) {
    error.value = (e as Error).message;
  } finally {
    editor?.setEditable(!expired.value);
    busy.value = false;
  }
}

async function end() {
  if (dirty.value && !confirmDiscard.value) {
    confirmDiscard.value = true;
    return;
  }
  busy.value = true;
  try {
    if (dirty.value) forgetRecovery();
    editor?.dispose();
    editor = undefined;
    await release();
    headerForm.value = undefined;
    editing.value = false;
    emit('editing', false);
    dirty.value = false;
    confirmDiscard.value = false;
    plan.value = false;
    standards.value = false;
    releaseForm.value = false;
    protocolForm.value = false;
    eventForm.value = false;
    retest.value = undefined;
    status.value = '';
    emit('saved', await api<Worksheet>(path()));
  } catch (e) {
    error.value = (e as Error).message;
  } finally {
    busy.value = false;
  }
}

function toggleTitle() {
  try {
    if (editor) editor.mergeTitle(!editor.snapshot().layout.title_merge_to);
  } catch (e) {
    error.value = (e as Error).message;
  }
}

function assertClean() {
  if (!editor || JSON.stringify(editor.snapshot()) !== JSON.stringify(baseline)) {
    throw new Error('请先保存表格改动。');
  }
}

function openRetest() {
  eventForm.value = false;
  protocolForm.value = false;
  releaseForm.value = false;
  error.value = '';
  try {
    assertClean();
    const range = editor?.selection();
    if (!range || range.startRow !== range.endRow || range.startColumn !== range.endColumn) {
      throw new Error('请选择一个已有结果的单元格。');
    }
    const row = current.rows[range.startRow - 2];
    const point = current.points[range.startColumn - 1];
    const cell = current.cells.find(value => value.row_id === row?.id && value.point_id === point?.id);
    if (!cell || !point.required_row_ids.includes(row.id)) throw new Error('请选择一个已有结果的应检单元格。');
    plan.value = false;
    standards.value = false;
    retestBasis.value = '';
    retest.value = { rowId: row.id, pointId: point.id, label: row.label + ' · ' + point.label,
      version: cell.display.result_version_id };
  } catch (e) {
    error.value = (e as Error).message;
  }
}

function openPlan() {
  eventForm.value = false;
  protocolForm.value = false;
  releaseForm.value = false;
  error.value = '';
  try {
    assertClean();
    retest.value = undefined;
    standards.value = false;
    planItems.value = [];
    plan.value = true;
  } catch (e) {
    error.value = (e as Error).message;
  }
}

function openStandards() {
  eventForm.value = false;
  protocolForm.value = false;
  releaseForm.value = false;
  error.value = '';
  try {
    assertClean();
    retest.value = undefined;
    plan.value = false;
    standards.value = true;
  } catch (e) { error.value = (e as Error).message; }
}

function openRelease() {
  eventForm.value = false;
  protocolForm.value = false;
  error.value = '';
  try {
    assertClean();
    retest.value = undefined;
    plan.value = false;
    standards.value = false;
    releaseForm.value = true;
  } catch (e) { error.value = (e as Error).message; }
}

function openProtocol() {
  eventForm.value = false;
  error.value = '';
  try {
    assertClean();
    retest.value = undefined;
    plan.value = standards.value = releaseForm.value = false;
    protocolForm.value = true;
  } catch (e) { error.value = (e as Error).message; }
}

function openEvent() {
  error.value = '';
  try {
    assertClean();
    retest.value = undefined;
    plan.value = standards.value = releaseForm.value = protocolForm.value = false;
    eventForm.value = true;
  } catch (e) { error.value = (e as Error).message; }
}

async function confirmRowRemoval(input: { row_id: string; basis: string }) {
  try {
    assertClean();
    const impact = await api<{ label: string; fingerprint: string; release_reference: unknown;
      events: unknown[]; results: { value: string; studies: { name: string }[] }[] }>(
      '/worksheets/' + encodeURIComponent(current.id) + '/remove-row?row_id=' + encodeURIComponent(input.row_id));
    const entries = impact.results.map(result =>
      [result.value, ...result.studies.map(study => study.name)].join(' · '));
    const lines = [`移除检项「${impact.label}」？`, `将作废 ${entries.length} 个正式结果。`, ...entries];
    if (impact.release_reference) lines.push('此检项的放行引用将解除。');
    if (impact.events.length) lines.push(`${impact.events.length} 条关联事件将保存在旧计划中。`);
    if (window.confirm(lines.join('\n'))) {
      await submitExtra({ ...input, fingerprint: impact.fingerprint }, 'remove-row');
    }
  } catch (e) {
    error.value = (e as Error).message;
  }
}

function choosePoint(event: Event) {
  const point = current.points.find(item => item.id === (event.target as HTMLSelectElement).value);
  if (point) pointEditor.value = { ...point };
}

async function openHeaderMetadata() {
  const fields = Object.fromEntries(headerFields.map(field => [field, current[field] ?? ''])) as Record<HeaderField, string>;
  headerForm.value = { ...fields, basis: '' };
  const form = headerForm.value;
  headerPreview.value = undefined;
  sourceError.value = '';
  try {
    const preview = await api<HeaderMetadataPreview & { revision: number }>(path() + '/header-metadata');
    if (headerForm.value === form && preview.revision === current.revision) headerPreview.value = preview;
  } catch (e) {
    if (headerForm.value === form) sourceError.value = (e as Error).message;
  }
}

function applyHeaderSuggestion(field: HeaderField) {
  if (!headerForm.value || !headerPreview.value?.[field]) return;
  headerForm.value[field] = headerPreview.value[field];
  headerForm.value.metadata_fingerprint = headerPreview.value.metadata_fingerprint;
}

async function submitExtra(input: Event | object, kind: 'stop-checks' | 'remove-row' | 'plan' | 'retest' | 'standards' | 'protocol-standards' | 'release-reference' | 'copy-coa-to-0m' | 'copy-coa-to-0m/batch' | 'protocol' | 'events' | 'point-metadata' | 'header-metadata') {
  if (busy.value || expired.value) return;
  busy.value = true;
  error.value = '';
  try {
    assertClean();
    editor?.setEditable(false);
    let payload;
    if (kind !== 'plan' && kind !== 'retest') {
      payload = input;
    } else {
      const form = new FormData((input as Event).target as HTMLFormElement);
      const basis = (kind === 'retest' ? (retestBasis.value.trim() || String(form.get('basis') ?? '').trim()) : String(form.get('basis') ?? '').trim());
      if (!basis) throw new Error('请填写说明。');
      if (kind === 'plan') {
      if (!planItems.value.length) throw new Error('请选择新增时间点的应检项目。');
      const points = String(form.get('points')).trim().split(/[\s,，、]+/).map(text => {
        const match = /^(\d+)([MD])$/i.exec(text);
        if (!match) throw new Error('请使用 6M 或 14D 这样的时间点格式。');
        return { amount: Number(match[1]), unit: match[2].toUpperCase(), required_items: planItems.value };
      });
      payload = { basis, points };
      } else {
      const target = retest.value;
      if (!target) return;
      const value = String(form.get('value') ?? '').trim();
      if (!value) throw new Error('请填写复测结果。');
      payload = { reason: basis, changes: [{ row_id: target.rowId, point_id: target.pointId,
        value, intent: 'retest', expected_version_id: target.version }] };
      }
    }
    const body = { ...payload, token, revision: current.revision };
    const signature = JSON.stringify([kind, body]);
    if (retry?.signature !== signature) retry = { signature, key: crypto.randomUUID() };
    await post(kind === 'retest' ? '/save' : '/' + kind, { ...body, idempotency_key: retry.key });
    pointEditor.value = undefined;
    headerForm.value = undefined;
    plan.value = false;
    standards.value = false;
    releaseForm.value = false;
    protocolForm.value = false;
    eventForm.value = false;
    retest.value = undefined;
    status.value = kind === 'header-metadata' ? '已保存研究表头。' : kind === 'stop-checks' ? '已停检所选时间点，历史结果和计划已保留。' : kind === 'remove-row' ? '已移除检项，旧计划仍保留。' : kind === 'events' ? '已保存研究事件。' : kind === 'protocol' ? '已关联研究方案。' : kind === 'copy-coa-to-0m/batch' ? '已批量复制 COA 结果到 0M。' : kind === 'copy-coa-to-0m' ? '已处理 COA 复制。' : kind === 'release-reference' ? '已保存放行引用设置。' : ['standards', 'protocol-standards'].includes(kind) ? '已保存标准关联。' :
      kind === 'plan' ? '已保存新计划版本。' : '已保存独立复测，当前采用结果保持原值。';
    try { await mount(); }
    catch {
      expired.value = true;
      error.value = '操作已保存，重新读取失败。请结束编辑并刷新。';
    }
  } catch (e) {
    error.value = (e as Error).message;
  } finally {
    editor?.setEditable(!expired.value);
    busy.value = false;
  }
}

function canNavigate() {
  if (busy.value || dirty.value) {
    error.value = '请先保存或结束编辑，再离开工作表。';
    return false;
  }
}
onBeforeRouteLeave(canNavigate);
onBeforeRouteUpdate(canNavigate);
function beforeUnload(event: BeforeUnloadEvent) {
  if (editor && !busy.value && !dirty.value) {
    try { dirty.value = JSON.stringify(editor.snapshot()) !== JSON.stringify(baseline); } catch { /* Keep current state. */ }
  }
  if (dirty.value) { rememberRecovery(); event.preventDefault(); event.returnValue = ''; }
}
function onPageHide() {
  rememberRecovery();
  void release();
}
window.addEventListener('beforeunload', beforeUnload);
window.addEventListener('pagehide', onPageHide);
onBeforeUnmount(() => {
  disposed = true;
  window.removeEventListener('beforeunload', beforeUnload);
  window.removeEventListener('pagehide', onPageHide);
  editor?.dispose();
  styles?.remove();
  void release();
});
</script>

<template>
  <NAlert v-if="error" type="error" role="alert">{{ error }}</NAlert>
  <div class="worksheet-controls" :class="{ 'is-editing': editing }">
    <NButton v-if="!editing" type="primary" :loading="busy" :disabled="busy || !sheet.editable" @click="begin">
      {{ recovery ? '恢复编辑' : '编辑结果' }}
    </NButton>
    <template v-else>
      <NButton type="primary" :loading="busy" :disabled="expired" @click="save">保存结果</NButton>
      <NButton :disabled="busy || expired" @click="toggleTitle">合并或拆分标题</NButton>
      <NButton :disabled="busy || expired" @click="openRetest">新增复测</NButton>
      <NButton :disabled="busy || expired" @click="openPlan">增加计划时间点</NButton>
      <NButton v-if="current.points.length" :disabled="busy || expired" @click="pointEditor = { ...current.points[0] }">日期与签署状态</NButton>
      <NButton :disabled="busy || expired" @click="openHeaderMetadata">核对研究表头</NButton>
      <NButton :disabled="busy || expired" @click="openStandards">标准与检项维护</NButton>
      <NButton :disabled="busy || expired" @click="openProtocol">后补研究方案</NButton>
      <NButton :disabled="busy || expired" @click="openEvent">记录研究事件</NButton>
      <NButton v-if="sheet.points.some(point => point.label === '0M')" :disabled="busy || expired" @click="openRelease">从 COA 复制到 0M</NButton>
      <NButton :disabled="busy" @click="end">{{ confirmDiscard ? '放弃未保存内容并结束' : '结束编辑' }}</NButton>
      <NButton v-if="confirmDiscard" @click="confirmDiscard = false">继续编辑</NButton>
    </template>
    <span role="status">{{ status || (dirty ? '有未保存的改动' : editing ? '正在编辑' : '只读') }}</span>
  </div>
  <NAlert v-if="recovery" type="warning" title="检测到本工作表存在未提交的本地编辑草稿" class="recovery-box">
    <p>包含未提交的表格修改与说明。在网络或租约恢复后，可一键恢复至表格继续编辑。</p>
    <div class="recovery-actions">
      <NButton v-if="editing && recovery.selection === current?.selection_id" size="small" type="warning" @click="mount(true)">
        恢复草稿到当前表格
      </NButton>
      <NButton size="small" @click="showDraftText = !showDraftText">
        {{ showDraftText ? '收起草稿内容' : '查看/复制草稿文本' }}
      </NButton>
      <NButton size="small" tertiary @click="forgetRecovery">清除此草稿</NButton>
    </div>
    <div v-if="showDraftText" class="mt-2">
      <textarea readonly :value="recovery.snapshot.rows.map(row => row.map(value => '&quot;' + value.replaceAll('&quot;', '&quot;&quot;') + '&quot;').join('\t')).join('\n')" rows="5" class="draft-textarea" aria-label="未保存内容"></textarea>
    </div>
  </NAlert>
  <div v-if="editing && requiresReason" class="extra-form reason-box">
    <label>更正原因（必填）
      <NInput v-model:value="reason" placeholder="说明本次对已有结果的更正依据" @update:value="rememberRecovery" />
    </label>
    <div class="quick-reasons">
      <span class="quick-title">快捷依据参考：</span>
      <NButton v-for="opt in correctionReasons" :key="opt" size="tiny" tertiary @click="reason = opt; rememberRecovery()">{{ opt }}</NButton>
    </div>
  </div>
  <form v-if="editing && plan" class="extra-form" @submit.prevent="submitExtra($event, 'plan')">
    <label>新增时间点 <input name="points" placeholder="6M, 12M" required></label>
    <NSelect v-model:value="planItems" multiple placeholder="选择应检项目"
      :options="current.rows.map(row => ({ label: [row.label, row.reporting_basis].filter(Boolean).join(' · '), value: row.plan_key ?? row.item_id }))" />
    <label>计划变更依据 <input name="basis" required></label>
    <NButton attr-type="submit" type="primary" :loading="busy" :disabled="expired">保存计划</NButton>
    <NButton :disabled="busy" @click="plan = false">取消</NButton>
  </form>
  <form v-if="editing && pointEditor" class="extra-form" @submit.prevent="submitExtra({
    point_id: pointEditor.id, planned_date: pointEditor.planned_date, signed_status: pointEditor.signed_status || ''
  }, 'point-metadata')">
    <label>时间点 <select :value="pointEditor.id" :disabled="busy" @change="choosePoint">
      <option v-for="point in current.points" :key="point.id" :value="point.id">{{ point.label }}</option>
    </select></label>
    <label>计划取样日期 <input v-model="pointEditor.planned_date" type="date" required :disabled="busy"></label>
    <label>签署状态 <select v-model="pointEditor.signed_status" :disabled="busy">
      <option value="">未标记</option><option>已签</option><option>已签报告</option>
    </select></label>
    <NButton attr-type="submit" type="primary" :disabled="busy || expired">保存时间点</NButton>
    <NButton :disabled="busy" @click="pointEditor = undefined">取消</NButton>
  </form>
  <form v-if="editing && headerForm" class="extra-form" @submit.prevent="submitExtra(headerForm, 'header-metadata')">
    <p v-if="current.legacy_location" class="pending">
      旧计划的地点字段待核对：{{ current.legacy_location }}。请按原件确认其归属。
    </p>
    <div v-for="field in headerFields" :key="field">
      <label>{{ headerFieldLabels[field] }}
        <textarea v-if="field === 'product_name'" v-model="headerForm[field]" rows="2" maxlength="500" placeholder="待确认" :disabled="busy"></textarea>
        <input v-else v-model="headerForm[field]" maxlength="500" placeholder="待确认" :disabled="busy">
      </label>
      <NButton v-if="headerPreview?.[field]" :disabled="busy || expired" @click="applyHeaderSuggestion(field)">
        带入来源{{ headerFieldLabels[field] }}
      </NButton>
    </div>
    <WorksheetHeaderSources v-if="headerPreview" :preview="headerPreview" />
    <p v-if="sourceError" role="status">{{ sourceError }}</p>
    <label>修改依据 <input v-model="headerForm.basis" required :disabled="busy"></label>
    <NButton attr-type="submit" type="primary" :disabled="busy || expired">保存研究表头</NButton>
    <NButton :disabled="busy" @click="headerForm = undefined">取消</NButton>
  </form>
  <WorksheetStandards v-if="editing && standards" :sheet="current" :busy="busy || expired"
    @stop="value => submitExtra(value, 'stop-checks')" @remove="confirmRowRemoval" @save="value => submitExtra(value, 'standards')" @confirm-protocol="value => submitExtra(value, 'protocol-standards')" @cancel="standards = false" />
  <WorksheetRelease v-if="editing && releaseForm" :sheet="current" :busy="busy || expired"
    @batch-copy="value => submitExtra(value, 'copy-coa-to-0m/batch')"
    @copy="value => submitExtra(value, 'copy-coa-to-0m')" @save="value => submitExtra(value, 'release-reference')" @cancel="releaseForm = false" />
  <WorksheetProtocol v-if="editing && protocolForm" :sheet="current" :busy="busy || expired"
    @save="value => submitExtra(value, 'protocol')" @cancel="protocolForm = false" />
  <WorksheetEvents v-if="editing && eventForm" :sheet="current" :busy="busy || expired"
    @save="value => submitExtra(value, 'events')" @cancel="eventForm = false" />
  <form v-if="editing && retest" class="extra-form" @submit.prevent="submitExtra($event, 'retest')">
    <strong>{{ retest.label }}</strong>
    <label>复测结果 <textarea name="value" required></textarea></label>
    <label>复测说明 <input v-model="retestBasis" name="basis" required placeholder="请说明复测背景与依据"></label>
    <div class="quick-reasons">
      <span class="quick-title">快捷依据参考：</span>
      <NButton v-for="opt in retestReasons" :key="opt" size="tiny" tertiary @click="retestBasis = opt">{{ opt }}</NButton>
    </div>
    <div class="buttons">
      <NButton attr-type="submit" type="primary" :loading="busy" :disabled="expired">保存复测</NButton>
      <NButton :disabled="busy" @click="retest = undefined">取消</NButton>
    </div>
  </form>
  <details v-if="editing" class="worksheet-active-cell">
    <summary>查看所选检项与标准 <template v-if="activeInspection">· {{ activeInspection.point }} {{ activeInspection.item.split('\n')[0] }}</template></summary>
    <div v-if="activeInspection"><p>{{ activeInspection.item }}</p><p>{{ activeInspection.criteria || '未关联可接受标准' }}</p></div>
    <p v-else>在表格中选择检项或结果格。</p>
  </details>
  <div v-if="editing" ref="grid" class="worksheet-editor" aria-label="结果编辑表格"></div>
</template>

<style scoped>
.worksheet-controls { display: flex; align-items: center; flex-wrap: wrap; gap: 10px; margin: 12px 0; }
.worksheet-controls.is-editing { padding: 12px; margin-bottom: 8px; background: #eef6f3; border: 1px solid #d3e5df; border-radius: 10px; }
.worksheet-controls [role="status"] { margin-left: auto; color: #395a53; font-size: 13px; }
.worksheet-editor { border-radius: 10px; overflow: hidden; height: min(64vh, 660px); min-height: 360px; border: 1px solid #dbe5e2; }
.extra-form { display: grid; gap: 10px; max-width: 480px; margin: 12px 0; padding: 14px; background: white; border: 1px solid #dbe5e2; border-radius: 8px; }
.extra-form label { display: grid; gap: 4px; }
.extra-form input, .extra-form textarea { font: inherit; padding: 6px; border: 1px solid #aabdb8; border-radius: 5px; }
.quick-reasons { display: flex; flex-wrap: wrap; align-items: center; gap: 6px; margin-top: 4px; }
.quick-title { font-size: 12px; color: #637772; }
.buttons { display: flex; gap: 10px; margin-top: 6px; }
.recovery-box { margin: 12px 0; }
.recovery-actions { display: flex; gap: 8px; align-items: center; margin-top: 8px; }
.draft-textarea { width: 100%; font-size: 12px; padding: 6px; border: 1px solid #aabdb8; border-radius: 4px; }
.mt-2 { margin-top: 8px; }
.mb-2 { margin-bottom: 8px; }
</style>
