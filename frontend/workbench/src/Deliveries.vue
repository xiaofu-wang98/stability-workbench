<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue';
import { useRoute } from 'vue-router';
import { NAlert, NButton, NInput, NSelect, NTag } from 'naive-ui';
import { api, triggerDownload, type Project, type Worksheet, type WorksheetSummary } from './api';
import { useWorkbenchContextStore } from './stores/workbenchContext';
import ActionNextStepBanner from './components/ActionNextStepBanner.vue';

const props = defineProps<{ projects: Project[] }>();
const route = useRoute();
const ctx = useWorkbenchContextStore();
type Delivery = { id: string; project: string; created_at: string; filename: string;
  attachment_filename?: string; report_version?: string; report_changes?: string };
type Export = { download_url: string; word_download_url?: string };
const project = ref(String(route.query.project || ctx.activeProject || ''));
const historySearch = ref('');
const batch = ref(String(route.query.batch || ''));
const study = ref<string | null>(typeof route.query.study === 'string' ? route.query.study : null);
const studyId = ref('');
const condition = ref('');
const kind = ref<'summary' | 'xlsx' | 'docx' | 'stability' | 'confirmation' | 'quick' | 'controlled'>(
  route.query.format === 'controlled'
    ? 'controlled'
    : route.query.format === 'confirmation'
    ? 'confirmation'
    : (['summary', 'xlsx', 'docx', 'stability', 'quick'].includes(String(route.query.format))
      ? (route.query.format as any)
      : 'xlsx')
);
type Sample = { id: string; batch: string; presentation: string; sample_type: string; purpose: string };
type SampleRole = 'selected' | 'before' | 'after';
type AlignmentField = 'presentation' | 'study_id';
type AlignmentCandidate = { field: AlignmentField; values: string[] };
type QuickScope = { base_revision: number; sample_ids: string[]; filters: Record<string, string>;
  members: { sample_id: string; role: SampleRole }[]; aligned_fields: AlignmentField[]; alignment_reason: string };
type QuickGroup = { id: string; label: string; key: { condition: string; method: string; method_version: string }; slot_ids: string[] };
const samples = ref<Sample[]>([]);
const sampleStudies = ref<{ sample_id: string; study: string }[]>([]);
const sampleIds = ref<string[]>([]);
const sampleRoles = ref<Record<string, SampleRole>>({});
const alignmentCandidates = ref<AlignmentCandidate[]>([]);
const alignedFields = ref<AlignmentField[]>([]);
const alignmentReason = ref('');
const alignmentLabels = { presentation: '规格', study_id: '原研究编号' };
const availableSamples = computed(() => {
  if (!study.value) return samples.value;
  const ids = new Set(sampleStudies.value.filter(option => option.study === study.value).map(option => option.sample_id));
  return samples.value.filter(sample => ids.has(sample.id));
});
const selectedSamples = computed(() => availableSamples.value.filter(sample => sampleIds.value.includes(sample.id)));
const selectedBatchCount = computed(() => new Set(selectedSamples.value.map(sample => sample.batch)).size);
const loadingSamples = ref(false);
const quickScope = ref<QuickScope>();
const quickGroups = ref<QuickGroup[]>([]);
const quickGroupIds = ref<string[]>([]);
const quickSlotIds = computed(() => [...new Set(quickGroups.value.filter(group => quickGroupIds.value.includes(group.id))
  .flatMap(group => group.slot_ids))]);
const sheetId = ref<string | null>(null);
const sheets = ref<WorksheetSummary[]>([]);
const controlledIds = ref<string[]>([]);
const matchingSheets = computed(() => sheets.value.filter(sheet => !batch.value.trim() ||
  sheet.batch?.toLowerCase().includes(batch.value.trim().toLowerCase())));
const loadingSheets = ref(false);
const confirmation = ref<Worksheet>();
const pointIds = ref<string[]>([]);
const scanId = ref<string | null>(null);
const scans = ref<{ id: string; filename: string }[]>([]);
const stabilityDocument = ref({ purpose: '', equipment: '', events: '', evaluation: '', version: '草稿', change_history: '' });
const deliveries = ref<Delivery[]>([]);
const scope = ref<{ revision: number; count: number; filters: Record<string, string> }>();
const output = ref<Export>();
const busy = ref(false);
const error = ref('');
const baseline = ref<string | null>(null);
const compared = ref<string | null>(null);
type ResultDifference = { slot_id: string; change: 'added' | 'removed' | 'changed'; batch: string;
  condition: string; study_id: string; point: string; item: string; method: string; method_version: string;
  before: string | null; after: string | null };
const comparison = ref<{ added: number; removed: number; changed: number; differences: ResultDifference[] }>();
const comparisonLimit = ref(50);
const differenceLabels = { added: '纳入范围', removed: '移出范围', changed: '采用版本变化' };
const reports = computed(() => deliveries.value.filter(item => item.filename === 'report.zip'));
const sameProjectReports = computed(() => {
  const first = reports.value.find(item => item.id === baseline.value);
  return first ? reports.value.filter(item => item.project === first.project && item.id !== first.id) : [];
});
function reportOption(item: Delivery) {
  return { value: item.id, label: [item.project, item.report_version, item.created_at].filter(Boolean).join(' · ') };
}
watch(baseline, () => { compared.value = null; });
watch([baseline, compared], () => { comparison.value = undefined; });
function compareReports() {
  const before = baseline.value;
  const after = compared.value;
  if (!before || !after) return;
  return run(async () => {
    comparisonLimit.value = 50;
    comparison.value = await api('/deliveries/' + encodeURIComponent(after) + '/compare?baseline=' + encodeURIComponent(before));
  });
}
watch(stabilityDocument, () => { output.value = undefined; }, { deep: true });
watch(quickGroupIds, () => { output.value = undefined; });
watch([sampleRoles, alignedFields, alignmentReason], () => {
  scope.value = undefined;
  quickScope.value = undefined;
  quickGroups.value = [];
  quickGroupIds.value = [];
  output.value = undefined;
}, { deep: true });
watch([project, study, kind, sampleIds], () => {
  alignmentCandidates.value = [];
  alignedFields.value = [];
  alignmentReason.value = '';
});
const filters = computed(() => Object.fromEntries(Object.entries({
  batch: kind.value === 'quick' ? '' : batch.value.trim(), study: study.value || '',
  study_id: kind.value === 'summary' ? studyId.value.trim() : '',
  condition: kind.value === 'summary' ? condition.value.trim() : '',
}).filter(([, value]) => value)));

watch([project, batch, study, kind, sheetId, sampleIds, studyId, condition, controlledIds], () => {
  scope.value = undefined;
  confirmation.value = undefined;
  output.value = undefined;
  scanId.value = null;
  quickScope.value = undefined;
  quickGroups.value = [];
  quickGroupIds.value = [];
});
watch([project, kind], async ([id, format], _, cleanup) => {
  const request = new AbortController();
  cleanup(() => request.abort());
  samples.value = [];
  sampleStudies.value = [];
  sampleIds.value = [];
  sampleRoles.value = {};
  loadingSamples.value = false;
  if (!id || format !== 'quick') return;
  loadingSamples.value = true;
  try {
    const catalog = await api<{ samples: Sample[]; options: typeof sampleStudies.value }>(
      '/projects/' + encodeURIComponent(id) + '/comparison-catalog', { signal: request.signal });
    if (!request.signal.aborted) {
      samples.value = catalog.samples;
      sampleStudies.value = catalog.options;
    }
  } catch (e) {
    if (!request.signal.aborted) error.value = (e as Error).message;
  } finally {
    if (!request.signal.aborted) loadingSamples.value = false;
  }
});
watch(matchingSheets, list => {
  if (sheetId.value && !list.some(sheet => sheet.id === sheetId.value)) sheetId.value = null;
});
watch(availableSamples, list => {
  sampleIds.value = sampleIds.value.filter(id => list.some(sample => sample.id === id));
});
watch([project, kind], async ([id, format], _, cleanup) => {
  const request = new AbortController();
  cleanup(() => request.abort());
  sheetId.value = null;
  controlledIds.value = [];
  sheets.value = [];
  loadingSheets.value = false;
  if (!id || !['confirmation', 'controlled'].includes(format)) return;
  error.value = '';
  loadingSheets.value = true;
  try {
    const list = await api<WorksheetSummary[]>('/projects/' + encodeURIComponent(id) + '/worksheets', { signal: request.signal });
    if (!request.signal.aborted) sheets.value = list;
  } catch (e) {
    if (!request.signal.aborted) error.value = (e as Error).message;
  } finally {
    if (!request.signal.aborted) loadingSheets.value = false;
  }
}, { immediate: true });

async function run(action: () => Promise<void>) {
  if (busy.value) return;
  error.value = '';
  busy.value = true;
  try { await action(); }
  catch (e) { error.value = (e as Error).message; }
  finally { busy.value = false; }
}
async function history() { deliveries.value = await api('/deliveries'); }
onMounted(() => run(history));

function preview() {
  return run(async () => {
    scope.value = undefined;
    confirmation.value = undefined;
    output.value = undefined;
    if (kind.value === 'controlled') {
      if (!controlledIds.value.length) return;
      const config = await api<{ base_revision: number }>(
        '/projects/' + encodeURIComponent(project.value) + '/configuration');
      scope.value = { revision: config.base_revision, count: controlledIds.value.length, filters: {} };
      return;
    }
    if (kind.value === 'confirmation') {
      if (!sheetId.value) return;
      const sheet = await api<Worksheet>('/worksheets/' + encodeURIComponent(sheetId.value));
      if (sheet.project !== project.value) throw new Error('所选研究表不属于当前项目。');
      confirmation.value = sheet;
      pointIds.value = sheet.points.map(point => point.id);
      scans.value = await api('/pdf-ocr');
      return;
    }
    const selected = { ...filters.value };
    if (kind.value === 'quick') {
      const path = '/projects/' + encodeURIComponent(project.value);
      const catalog = await api<{ base_revision: number }>(path + '/comparison-catalog');
      const quick: QuickScope = { base_revision: catalog.base_revision, sample_ids: [...sampleIds.value], filters: selected,
        members: sampleIds.value.map(id => ({ sample_id: id, role: sampleRoles.value[id] ?? 'selected' })),
        aligned_fields: [...alignedFields.value], alignment_reason: alignedFields.value.length ? alignmentReason.value.trim() : '' };
      const view = await api<{ total_slots: number; groups: QuickGroup[]; alignment_candidates: AlignmentCandidate[] }>(path + '/comparison-preview', {
        method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(quick),
      });
      quickScope.value = quick;
      alignmentCandidates.value = [...alignmentCandidates.value.filter(option => alignedFields.value.includes(option.field)),
        ...view.alignment_candidates];
      quickGroups.value = view.groups;
      quickGroupIds.value = view.groups.map(group => group.id);
      scope.value = { revision: quick.base_revision, count: view.total_slots, filters: selected };
      return;
    }
    const requests = kind.value === 'stability' && !selected.study
      ? ['long_term', 'accelerated', 'stress'].map(study => ({ ...selected, study }))
      : [selected];
    const pages = await Promise.all(requests.map(filter => api<{ base_revision: number; total: number }>(
      '/projects/' + encodeURIComponent(project.value) + '/result-cells?' + new URLSearchParams({ ...filter, limit: '1' }),
    )));
    if (pages.some(data => data.base_revision !== pages[0].base_revision)) {
      throw new Error('数据已更新，请重新预览。');
    }
    scope.value = { revision: pages[0].base_revision, count: pages.reduce((sum, data) => sum + data.total, 0), filters: selected };
  });
}

function generate() {
  const selection = scope.value;
  const sheet = confirmation.value;
  if (kind.value === 'confirmation') {
    if (!sheet || sheet.id !== sheetId.value || !pointIds.value.length) return;
    return run(async () => {
      output.value = undefined;
      const query = new URLSearchParams({ revision: String(sheet.revision), selection_id: sheet.selection_id });
      if (pointIds.value.length !== sheet.points.length) {
        pointIds.value.forEach(id => query.append('point_ids', id));
      }
      if (scanId.value) query.set('scan_id', scanId.value);
      output.value = await api('/worksheets/' + encodeURIComponent(sheet.id) + '/confirmation?' + query, { method: 'POST' });
      if (output.value?.download_url) triggerDownload(output.value.download_url);
      await history();
    });
  }
  if (!selection?.count) return;
  if (kind.value === 'controlled') {
    return run(async () => {
      output.value = await api('/projects/' + encodeURIComponent(project.value) + '/controlled-workbook', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ base_revision: selection.revision, sheet_ids: controlledIds.value }),
      });
      if (output.value?.download_url) triggerDownload(output.value.download_url);
      await history();
    });
  }
  if (kind.value === 'quick' && !quickSlotIds.value.length) return;
  return run(async () => {
    output.value = undefined;
    const body = { project: project.value, base_revision: selection.revision, filters: selection.filters };
    const payload = kind.value === 'summary' ? { ...body, format: 'stability_summary' } : kind.value === 'stability' ? { ...body, stability_document: stabilityDocument.value }
      : kind.value === 'quick' ? { ...body, kind: 'quick', quick: quickScope.value, slot_ids: quickSlotIds.value } : body;
    output.value = await api(['xlsx', 'summary'].includes(kind.value) ? '/exports' : '/reports', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    });
    const downloadTarget = output.value?.word_download_url || output.value?.download_url;
    if (downloadTarget) triggerDownload(downloadTarget);
    await history();
  });
}

const studies = [
  { label: '长期', value: 'long_term' }, { label: '加速', value: 'accelerated' },
  { label: '强力破坏', value: 'stress' }, { label: '放行', value: 'release' },
];
const formats = [{ label: '稳定性汇总 Excel', value: 'summary' }, { label: '结果汇总 Excel', value: 'xlsx' }, { label: '检测结果 Word', value: 'docx' },
  { label: '常熟受控版式 Excel', value: 'controlled' },
  { label: '稳定性考察报告 Word', value: 'stability' }, { label: '稳定性确认表 Excel', value: 'confirmation' },
  { label: '跨批对比 Word', value: 'quick' }];
const deliveryCards: { group: string; items: { value: typeof kind.value; label: string; desc: string; tag: string }[] }[] = [
  {
    group: '受控表格与确认表（Excel）',
    items: [
      { value: 'controlled', label: '常熟受控版式', desc: '按受控模板合并输出多张研究工作表', tag: '受控版式' },
      { value: 'confirmation', label: '稳定性确认表', desc: '针对单张研究表按时间点生成签核确认表', tag: '单表签核' },
      { value: 'summary', label: '稳定性矩阵汇总', desc: '按项目、方案编号与条件汇总稳定性结果', tag: '矩阵汇总' },
      { value: 'xlsx', label: '结果明细汇总', desc: '导出原始结构化检测结果明细表', tag: '全量明细' },
    ],
  },
  {
    group: 'Word 报告与跨批对比',
    items: [
      { value: 'stability', label: '稳定性考察报告', desc: '生成带目的、设备、事件与人工评价的完整考察报告包', tag: '正式报告' },
      { value: 'quick', label: '跨批对比报告', desc: '选择多个批次并支持变更前后分组的对比分析报告', tag: '批间对比' },
      { value: 'docx', label: '检测结果简表', desc: '按筛选范围导出标准检测结果 Word 表格', tag: '结果简表' },
    ],
  },
];
const filteredDeliveries = computed(() => {
  const kw = historySearch.value.trim().toLowerCase();
  if (!kw) return deliveries.value;
  return deliveries.value.filter(item =>
    [item.project, item.filename, item.report_version || ''].join(' ').toLowerCase().includes(kw),
  );
});
const reportFields = [
  ['purpose', '目的'], ['equipment', '设备信息'], ['events', '变更及事件说明'],
  ['evaluation', '人工评价'], ['change_history', '变更历史'],
] as const;
function href(item: Delivery) {
  return '/api/v1/' + (item.filename === 'report.zip' ? 'reports/' : 'exports/') + encodeURIComponent(item.id) + '/file';
}
</script>

<template>
  <NAlert v-if="error" type="error" role="alert" style="margin-bottom: 14px;">{{ error }}</NAlert>

  <!-- Categorized Delivery Scenario Picker -->
  <section class="panel delivery-picker-panel" aria-label="选择交付文件场景">
    <div v-for="group in deliveryCards" :key="group.group" class="delivery-group-block">
      <div class="delivery-group-title">{{ group.group }}</div>
      <div class="delivery-card-grid">
        <button
          v-for="card in group.items"
          :key="card.value"
          type="button"
          class="delivery-card"
          :class="{ 'is-active': kind === card.value }"
          :disabled="busy"
          @click="kind = card.value"
        >
          <div class="dc-top">
            <strong>{{ card.label }}</strong>
            <NTag size="tiny" :type="kind === card.value ? 'info' : 'default'">{{ card.tag }}</NTag>
          </div>
          <small>{{ card.desc }}</small>
        </button>
      </div>
    </div>
    <div class="delivery-planned-strip">
      <span>未来监测报告预留：</span>
      <NTag size="small">影响因素总结报告 · 规划预留</NTag>
      <NTag size="small">水/空气系统周期回顾报告 · 规划预留</NTag>
    </div>
  </section>

  <section class="panel delivery-form">
    <h2>从已有结果生成</h2>
    <label>项目<NSelect v-model:value="project" :disabled="busy" placeholder="选择项目" filterable
      :options="projects.map(p => ({ value: p.project, label: p.project + ' ' + p.name }))" /></label>
    <label v-if="kind !== 'quick'">批号<NInput v-model:value="batch" :disabled="busy" placeholder="留空包含所有批次" /></label>
    <label v-if="kind !== 'confirmation'">考察类型<NSelect v-model:value="study" :disabled="busy" :options="studies" clearable placeholder="全部" /></label>
    <label>文件类型<NSelect v-model:value="kind" :disabled="busy" :options="formats" /></label>
    <label v-if="kind === 'summary'">方案编号<NInput v-model:value="studyId" :disabled="busy" /></label>
    <label v-if="kind === 'summary'">考察条件<NInput v-model:value="condition" :disabled="busy" /></label>
    <label v-if="kind === 'controlled'">研究表<NSelect v-model:value="controlledIds" multiple filterable
      :loading="loadingSheets" :disabled="busy || !project"
      :options="sheets.map(sheet => ({ label: [sheet.batch, sheet.condition, sheet.name].filter(Boolean).join(' · '), value: sheet.id }))" /></label>
    <label v-if="kind === 'quick'" class="quick-samples">参与对比的样品<NSelect v-model:value="sampleIds" multiple filterable
      :disabled="busy || !project" :loading="loadingSamples" placeholder="选择至少两个不同批次"
      :options="availableSamples.map(sample => ({ value: sample.id, label: [sample.batch, sample.sample_type, sample.presentation, sample.purpose].join(' · ') }))" /></label>
    <details v-if="kind === 'quick' && selectedSamples.length" class="quick-groups">
      <summary>样品分组（可选）</summary>
      <ul><li v-for="sample in selectedSamples" :key="sample.id"><label>
        {{ sample.batch }} · {{ sample.presentation }}
        <select :value="sampleRoles[sample.id] ?? 'selected'" :disabled="busy"
          @change="sampleRoles[sample.id] = ($event.target as HTMLSelectElement).value as SampleRole">
          <option value="selected">按批次对比</option><option value="before">变更前</option><option value="after">变更后</option>
        </select>
      </label></li></ul>
    </details>
    <label v-if="kind === 'confirmation'">研究表<NSelect v-model:value="sheetId" :disabled="busy || !project"
      :loading="loadingSheets" placeholder="选择研究表" filterable
      :options="matchingSheets.map(sheet => ({ value: sheet.id, label: [sheet.name, sheet.batch, sheet.condition].filter(Boolean).join(' · ') }))" /></label>
    <p v-if="kind === 'confirmation' && project && !loadingSheets && !matchingSheets.length">{{ batch.trim() ? '未找到符合批号的研究表。' : '此项目暂无可导出的研究表。' }}</p>
    <label v-if="confirmation" class="confirmation-points">导出时间点<NSelect v-model:value="pointIds" multiple
      :disabled="busy" :options="confirmation.points.map(point => ({ label: point.label, value: point.id }))"
      @update:value="output = undefined" /></label>
    <label v-if="confirmation && scans.length" class="confirmation-attachment">扫描附件（可选）
      <NSelect v-model:value="scanId" clearable filterable :disabled="busy" placeholder="选择已归档 PDF"
        :options="scans.map(scan => ({ label: scan.filename, value: scan.id }))" @update:value="output = undefined" />
    </label>
    <fieldset v-if="kind === 'stability'" class="report-settings" :disabled="busy">
      <legend>稳定性考察报告内容</legend>
      <p>适用于单一批次、材料、规格和用途。可同时包含长期和加速条件。</p>
      <label>报告版本 <NInput v-model:value="stabilityDocument.version" :maxlength="50" /></label>
      <label v-for="[key, label] in reportFields" :key="key">{{ label }}
        <NInput v-model:value="stabilityDocument[key]" type="textarea" :maxlength="4000" :input-props="{ 'aria-label': label }" />
      </label>
    </fieldset>
    <div class="delivery-actions">
      <NButton :disabled="!project || busy || (kind === 'confirmation' && !sheetId) || (kind === 'controlled' && !controlledIds.length) || (kind === 'quick' && (selectedBatchCount < 2 || (alignedFields.length > 0 && !alignmentReason.trim())))" @click="preview">预览范围</NButton>
      <NButton type="primary" :disabled="busy || (kind === 'confirmation' ? !confirmation || !pointIds.length : !scope?.count || (kind === 'stability' && study === 'release') || (kind === 'quick' && !quickSlotIds.length))" @click="generate">生成文件</NButton>
    </div>
    <p v-if="scope" role="status">范围内 {{ kind === 'quick' ? quickSlotIds.length : scope.count }} {{ kind === 'controlled' ? '张研究表' : '条结果' }} · 数据版本 {{ scope.revision }}</p>
    <details v-if="kind === 'quick' && alignmentCandidates.length" class="quick-groups">
      <summary>本次比较的字段对齐（可选）</summary>
      <ul><li v-for="option in alignmentCandidates" :key="option.field"><label>
        <input v-model="alignedFields" type="checkbox" :value="option.field" :disabled="busy">
        {{ alignmentLabels[option.field] }} · {{ option.values.join('、') }}
      </label></li></ul>
      <label v-if="alignedFields.length">对齐依据 <input v-model="alignmentReason" :disabled="busy" maxlength="1000"></label>
    </details>
    <details v-if="quickGroups.length" class="quick-groups">
      <summary>选择检测组（{{ quickGroupIds.length }}/{{ quickGroups.length }}）</summary>
      <NButton :disabled="busy" @click="quickGroupIds = quickGroups.map(group => group.id)">全选</NButton>
      <NButton :disabled="busy" @click="quickGroupIds = []">清空选择</NButton>
      <ul><li v-for="group in quickGroups" :key="group.id"><label>
        <input v-model="quickGroupIds" type="checkbox" :value="group.id" :disabled="busy">
        {{ group.label }} · {{ group.key.method }} · {{ group.key.method_version }} · {{ group.key.condition }} · {{ group.slot_ids.length }} 条
      </label></li></ul>
    </details>
    <p v-if="confirmation" role="status">
      {{ confirmation.name }} · {{ confirmation.identity.batch }} · {{ confirmation.identity.condition }}
      · 计划版本 {{ confirmation.plan_version }} · {{ confirmation.rows.length }} 个检项、{{ pointIds.length }} 个计划时间点
    </p>
    <div v-if="output" class="delivery-actions">
      <span class="delivery-success-tip">文件已生成并自动触发下载，保留备用链接：</span>
      <a v-if="output.word_download_url" :href="output.word_download_url">下载 Word</a>
      <a :href="output.download_url">{{ output.word_download_url ? '下载完整报告包' : '下载 Excel' }}</a>
    </div>
    <div v-if="output" style="grid-column: 1 / -1;">
      <ActionNextStepBanner
        title="交付文件已生成并归档至历史记录"
        summary="文件已自动触发下载，并保留交付时的完整数据快照用于 GMP 追溯与版本比对。"
        :steps="[
          ...(project ? [{ label: `1. 返回 ${project} 项目台账`, primary: true, to: '/projects/' + encodeURIComponent(project) }] : []),
          ...(project ? [{ label: '2. 查看该项目趋势图', to: { path: '/trends', query: { project } } }] : []),
          { label: '3. 返回工作台首页', to: '/' },
        ]"
      />
    </div>
  </section>
  <section class="panel">
    <div class="history-head-row">
      <h2>已生成文件（{{ filteredDeliveries.length }}）</h2>
      <NInput v-model:value="historySearch" size="small" clearable placeholder="按项目编号、文件名或版本过滤..." style="max-width: 300px;" />
    </div>
    <details v-if="reports.length > 1" class="delivery-comparison">
      <summary>比较 Word 报告结果</summary>
      <label>基准报告 <NSelect v-model:value="baseline" :disabled="busy" filterable clearable placeholder="输入报告名称快速检索..." style="min-width: 260px; display: inline-block; vertical-align: middle;"
        :options="reports.map(reportOption)" /></label>
      <label>对比报告 <NSelect v-model:value="compared" :disabled="busy || !baseline" filterable clearable placeholder="输入对比报告名称快速检索..." style="min-width: 260px; display: inline-block; vertical-align: middle;"
        :options="sameProjectReports.map(reportOption)" /></label>
      <NButton :disabled="busy || !compared" @click="compareReports">比较结果</NButton>
      <p v-if="comparison" role="status">纳入范围 {{ comparison.added }} 条 · 移出范围 {{ comparison.removed }} 条 · 采用版本变化 {{ comparison.changed }} 条</p>
      <div v-if="comparison?.differences.length" class="matrix">
        <table aria-label="Word 报告结果差异">
          <thead><tr><th>变化</th><th>批号</th><th>方案及条件</th><th>时间点</th><th>检项及方法</th><th>基准采用值</th><th>对比采用值</th></tr></thead>
          <tbody><tr v-for="row in comparison.differences.slice(0, comparisonLimit)" :key="row.slot_id">
            <td>{{ differenceLabels[row.change] }}</td><td>{{ row.batch }}</td>
            <td>{{ row.study_id }} · {{ row.condition }}</td><td>{{ row.point || '放行' }}</td>
            <td>{{ row.item }} · {{ row.method }}<template v-if="row.method_version !== 'not_reported'"> · {{ row.method_version }}</template></td>
            <td>{{ row.change === 'added' ? '未纳入' : row.before ?? '尚无采用值' }}</td>
            <td>{{ row.change === 'removed' ? '未纳入' : row.after ?? '尚无采用值' }}</td>
          </tr></tbody>
        </table>
        <NButton v-if="comparison.differences.length > comparisonLimit" @click="comparisonLimit += 50">显示更多</NButton>
      </div>
    </details>
    <ul class="list"><li v-for="item in filteredDeliveries" :key="item.id">
      <a :href="href(item)">{{ item.project }} · {{ item.filename }}</a>
      <span v-if="item.report_version">版本 {{ item.report_version }}</span>
      <a v-if="item.filename === 'report.zip'" :href="'/api/v1/reports/' + encodeURIComponent(item.id) + '/word'">下载 Word</a>
      <a v-if="item.attachment_filename" :href="'/api/v1/exports/' + encodeURIComponent(item.id) + '/attachment'">下载附件 {{ item.attachment_filename }}</a>
      <span>{{ item.created_at }}</span>
      <details v-if="item.report_changes" class="delivery-changes">
        <summary>查看变更说明</summary>
        <p>{{ item.report_changes }}</p>
      </details>
    </li></ul>
    <p v-if="!filteredDeliveries.length" class="muted">暂无匹配的生成记录。</p>
  </section>
</template>

<style scoped>
.delivery-picker-panel { margin-top: 0; display: grid; gap: 14px; }
.delivery-group-block { display: grid; gap: 8px; }
.delivery-group-title { font-size: 13px; font-weight: 600; color: var(--text-muted); }
.delivery-card-grid { display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: 10px; }
.delivery-card {
  display: grid;
  gap: 5px;
  text-align: left;
  padding: 12px 14px;
  border-radius: var(--radius-md);
  border: 1.5px solid var(--border);
  background: var(--bg-subtle);
  cursor: pointer;
  font: inherit;
  transition: all 0.15s ease;
}
.delivery-card:hover { background: var(--bg-surface); border-color: var(--brand-border); }
.delivery-card.is-active { background: var(--brand-soft); border-color: var(--brand); box-shadow: 0 0 0 2px var(--brand-ring); }
.dc-top { display: flex; align-items: center; justify-content: space-between; gap: 8px; font-size: 14px; color: var(--text-primary); }
.delivery-card.is-active .dc-top strong { color: var(--brand); }
.delivery-card small { font-size: 12.5px; color: var(--text-secondary); line-height: 1.45; }
.delivery-planned-strip { display: flex; align-items: center; flex-wrap: wrap; gap: 8px; padding-top: 10px; border-top: 1px dashed var(--border); font-size: 12.5px; color: var(--text-muted); }
.history-head-row { display: flex; align-items: center; justify-content: space-between; gap: 14px; margin-bottom: 10px; }
.history-head-row h2 { margin: 0; }

.delivery-form { display: grid; grid-template-columns: repeat(auto-fit, minmax(210px, 1fr)); gap: 14px 16px; }
.delivery-form h2, .delivery-form p, .delivery-form > a, .delivery-actions { grid-column: 1 / -1; }
.delivery-form label { display: grid; gap: 5px; font-size: 13px; font-weight: 600; color: var(--text-secondary); }
.report-settings {
  grid-column: 1 / -1;
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(220px, 1fr));
  gap: 12px;
  padding: 14px 16px;
  background: var(--bg-subtle);
  border: 1px solid var(--border);
  border-radius: var(--radius-md);
}
.report-settings p { grid-column: 1 / -1; margin: 0; font-size: 13px; color: var(--text-secondary); }
.delivery-actions { display: flex; align-items: center; gap: 12px; padding-top: 8px; border-top: 1px solid var(--border); }
.delivery-changes p { white-space: pre-wrap; }
.quick-groups { grid-column: 1 / -1; display: flex; flex-wrap: wrap; gap: 8px 16px; }
.quick-groups label { display: inline-flex; align-items: center; gap: 6px; font-weight: 500; }
</style>
