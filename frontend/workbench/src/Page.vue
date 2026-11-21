<script setup lang="ts">
import { computed, defineAsyncComponent, inject, ref, watch, type Ref } from 'vue';
import { useRoute, useRouter } from 'vue-router';
import { NAlert, NButton, NCard, NInput, NSelect, NSpace, NSpin, NTag } from 'naive-ui';
import { api, archiveStudyObject, triggerDownload, type Project, type SampleEntry, type WorksheetSummary, type Worksheet } from './api';
import StudyTemplateHeader from './StudyTemplateHeader.vue';
import StudyWorkbook from './StudyWorkbook.vue';
import HistoricalRows from './HistoricalRows.vue';
import WorksheetSample from './WorksheetSample.vue';
const Trend = defineAsyncComponent(() => import('./Trend.vue'));
const NewWorksheet = defineAsyncComponent(() => import('./NewWorksheet.vue'));
const ImportWorkspace = defineAsyncComponent(() => import('./ImportWorkspace.vue'));
const Specifications = defineAsyncComponent(() => import('./Specifications.vue'));
const ScanWorkspace = defineAsyncComponent(() => import('./ScanWorkspace.vue'));
const Deliveries = defineAsyncComponent(() => import('./Deliveries.vue'));
const ProjectResults = defineAsyncComponent(() => import('./ProjectResults.vue'));
const WorksheetEditor = defineAsyncComponent(() => import('./WorksheetEditor.vue'));

const route = useRoute();
const router = useRouter();
const page = computed(() => route.path.split('/')[1] || 'home');
const project = computed(() => String(route.params.project || ''));
const projectScope = inject<Ref<string>>('workbenchProject');
const projects = ref<Project[]>([]);
const sheets = ref<WorksheetSummary[]>([]);
const samples = ref<SampleEntry[]>([]);
const worksheetDrafts = ref<{ id: string; name: string; created_at: string; protocol_draft: string; protocol_table: string; copy_source: string }[]>([]);
const sheet = ref<Worksheet>();
watch(() => sheet.value?.project, code => {
  if (code && projectScope) projectScope.value = code;
});
const editingSheet = ref(false);
const search = ref('');
const sheetSearch = ref('');
const error = ref('');
const busy = ref(false);
const operating = ref(false);
async function removeSheet() {
  if (!sheet.value || editingSheet.value || operating.value) return;
  operating.value = true;
  error.value = '';
  try {
    if (await archiveStudyObject('/worksheets/' + encodeURIComponent(sheet.value.id), '研究表')) {
      await router.push('/projects/' + encodeURIComponent(sheet.value.project));
    }
  } catch (e) {
    error.value = (e as Error).message;
  } finally {
    operating.value = false;
  }
}
const download = ref('');
const activeProject = ref('');
const filtered = computed(() => projects.value.filter(p =>
  (p.project + ' ' + p.name).toLowerCase().includes(search.value.trim().toLowerCase())));
const filteredSheets = computed(() => sheets.value.filter(item =>
  [item.name, item.batch, item.condition].join(' ').toLowerCase().includes(sheetSearch.value.trim().toLowerCase())));
const cells = computed(() => new Map(sheet.value?.cells.map(c => [c.row_id + ':' + c.point_id, c])));
const titles: Record<string, string> = { home: '工作台', new: '新建研究表', projects: '项目数据', worksheets: '研究工作表', import: '导入文件', specifications: '质量标准与 COA', scans: '扫描 PDF', trends: '趋势与比较', deliveries: '生成与历史' };
const actions = [
  ['/import', '导入文件'], ['/new', '新建研究表'],
  ['/projects', '查看项目数据'], ['/trends', '趋势与导出'],
];

const sampleWorksheetsMap = computed(() => {
  const map = new Map<string, WorksheetSummary[]>();
  for (const s of samples.value) {
    map.set(s.id, []);
  }
  const unlinked: WorksheetSummary[] = [];
  for (const item of sheets.value) {
    if (item.sample_id && map.has(item.sample_id)) {
      map.get(item.sample_id)!.push(item);
    } else {
      unlinked.push(item);
    }
  }
  return { map, unlinked };
});

async function loadSamples(base: string, init: RequestInit) {
  const limit = 100;
  const entries: SampleEntry[] = [];
  for (let offset = 0; ; offset += limit) {
    const page = await api<SampleEntry[]>(base + '/samples?limit=' + limit + '&offset=' + offset, init);
    entries.push(...page);
    if (page.length < limit) return entries;
  }
}

watch([() => route.path, () => route.params.project, () => route.params.sheet], async (_, __, onCleanup) => {
  const controller = new AbortController();
  onCleanup(() => controller.abort());
  const init = { signal: controller.signal };
  error.value = '';
  busy.value = true;
  sheet.value = undefined;
  editingSheet.value = false;
  sheets.value = [];
  samples.value = [];
  worksheetDrafts.value = [];
  download.value = '';
  try {
    projects.value = await api<Project[]>('/projects', init);
    if (project.value) {
      const base = '/projects/' + encodeURIComponent(project.value);
      const [sheetsData, draftsData, samplesData] = await Promise.all([
        api<WorksheetSummary[]>(base + '/worksheets', init),
        api<typeof worksheetDrafts.value>(base + '/worksheet-drafts', init),
        page.value === 'projects' ? loadSamples(base, init) : Promise.resolve([]),
      ]);
      sheets.value = sheetsData;
      worksheetDrafts.value = draftsData;
      samples.value = samplesData;
    }
    if (route.params.sheet) sheet.value = await api('/worksheets/' + encodeURIComponent(String(route.params.sheet)), init);
  } catch (e) {
    if (!controller.signal.aborted) error.value = String((e as Error).message);
  } finally {
    if (!controller.signal.aborted) busy.value = false;
  }
}, { immediate: true });
watch(project, () => { sheetSearch.value = ''; });

async function confirmation() {
  const current = sheet.value;
  if (!current) return;
  operating.value = true;
  error.value = '';
  try {
    const query = new URLSearchParams({ revision: String(current.revision), selection_id: current.selection_id });
    const result = await api<{download_url: string}>('/worksheets/' + encodeURIComponent(current.id) + '/confirmation?' + query, { method: 'POST' });
    if (sheet.value === current) {
      download.value = result.download_url;
      triggerDownload(result.download_url);
    }
  } catch (e) {
    error.value = (e as Error).message;
  } finally {
    operating.value = false;
  }
}
async function refreshSheet() {
  const id = sheet.value?.id;
  if (!id) return;
  try {
    const updated = await api<Worksheet>('/worksheets/' + id);
    if (sheet.value?.id === id) sheet.value = updated;
  } catch (e) {
    error.value = (e as Error).message;
  }
}
</script>

<template>
  <header class="page-header" :class="{ 'entry-heading': ['new', 'import', 'worksheets'].includes(page) }">
    <div class="page-header-main">
      <h1>{{ sheet?.name || project || titles[page] }}</h1>
      <p v-if="page === 'home'" class="page-subtitle">稳定性研究、结果与交付</p>
      <p v-else-if="sheet" class="page-subtitle">
        <RouterLink :to="'/projects/' + encodeURIComponent(sheet.project)">{{ sheet.project }}</RouterLink>
        · {{ sheet.identity.batch }} · {{ sheet.identity.condition }} {{ sheet.identity.orientation }}
      </p>
    </div>
    <div v-if="sheet" class="page-header-actions">
      <RouterLink class="header-ghost-link" :to="{ path: '/new', query: { project: sheet.project, copy: sheet.id } }">复制研究表结构</RouterLink>
      <RouterLink v-if="sheet.copied_from" class="header-ghost-link" :to="'/worksheets/' + sheet.copied_from.sheet_id">查看结构来源 · 计划版本 {{ sheet.copied_from.plan_version }}</RouterLink>
      <a v-if="sheet.protocol_draft_id" class="header-ghost-link" :href="'/api/v1/plan-drafts/' + sheet.protocol_draft_id + '/file'">下载原方案<template v-if="sheet.protocol_table"> · 表 {{ sheet.protocol_table }}</template></a>
      <NButton type="primary" size="small" :disabled="editingSheet" :loading="operating" @click="confirmation">生成确认表 Excel</NButton>
      <NButton type="error" secondary size="small" :disabled="editingSheet || operating" @click="removeSheet">删除研究表</NButton>
      <a v-if="download" class="download-pill" :href="download">下载确认表</a>
    </div>
  </header>
  <NAlert v-if="error" type="error" role="alert" style="margin-bottom: 16px;">{{ error }}</NAlert>
  <NSpin v-if="busy" aria-label="正在读取数据" />
  <div v-else>
    <NewWorksheet v-if="page === 'new'" :key="route.fullPath" :projects="projects" />
    <div v-if="page === 'home'" class="actions">
      <RouterLink v-for="[path, label] in actions" :key="path" :to="path" :aria-label="label === '新建研究表' ? '创建新研究表' : undefined">{{ label }} <span aria-hidden="true">↗</span></RouterLink>
    </div>
    <section v-if="page === 'home' || (page === 'projects' && !project)" class="panel">
      <div class="section-bar">
        <h2>项目目录</h2>
        <span class="section-count">共 {{ filtered.length }} 个项目</span>
      </div>
      <NInput v-model:value="search" placeholder="搜索项目编号或名称" aria-label="搜索项目" clearable style="max-width: 420px;" />
      <ul class="list project-list">
        <li v-for="p in filtered" :key="p.project">
          <RouterLink class="project-code-link" :to="'/projects/' + encodeURIComponent(p.project)">{{ p.project }}</RouterLink>
          <span>{{ p.name }}</span>
        </li>
      </ul>
      <p v-if="!filtered.length" class="muted">{{ projects.length ? '没有匹配项目。' : '当前数据目录还没有项目。' }}</p>
    </section>
    <section v-if="page === 'projects' && project" class="panel">
      <div class="project-header-row">
        <h2>样品批次与研究表</h2>
        <NSpace align="center">
          <RouterLink :to="{ path: '/new', query: { project } }">
            <NButton type="primary" size="small">新建研究表</NButton>
          </RouterLink>
          <RouterLink :to="'/trends?project=' + encodeURIComponent(project)">
            <NButton size="small">查看项目趋势</NButton>
          </RouterLink>
          <RouterLink :to="'/deliveries?project=' + encodeURIComponent(project)">
            <NButton size="small">生成交付文件</NButton>
          </RouterLink>
        </NSpace>
      </div>

      <div v-if="samples.length" class="sample-cards-grid">
        <NCard v-for="s in samples" :key="s.id" class="sample-card" size="small" hoverable>
          <template #header>
            <div class="sample-card-header">
              <span class="sample-batch-title"><strong>批号：{{ s.batch }}</strong></span>
              <NSpace size="small">
                <NTag type="info" size="small">{{ s.material_stage || s.sample_type }}</NTag>
                <NTag v-if="s.sample_type && s.sample_type !== s.material_stage" size="small">{{ s.sample_type }}</NTag>
              </NSpace>
            </div>
          </template>
          <div class="sample-card-body">
            <div class="sample-meta-grid">
              <div><span class="meta-label">材料类别：</span>{{ s.material_stage || '—' }}</div>
              <div><span class="meta-label">批号：</span>{{ s.batch }}</div>
              <div><span class="meta-label">规格：</span>{{ s.presentation || '—' }}</div>
              <div><span class="meta-label">包装：</span>{{ s.packaging || '—' }}</div>
              <div><span class="meta-label">用途：</span>{{ s.purpose || '—' }}</div>
            </div>

            <div class="sample-worksheets-box">
              <span class="group-title">关联研究表 ({{ sampleWorksheetsMap.map.get(s.id)?.length || 0 }})：</span>
              <ul v-if="sampleWorksheetsMap.map.get(s.id)?.length" class="sample-sheets-list">
                <li v-for="ws in sampleWorksheetsMap.map.get(s.id)" :key="ws.id">
                  <RouterLink :to="'/worksheets/' + encodeURIComponent(ws.id)">
                    {{ ws.name }} <span class="muted-tag">{{ [ws.condition, '计划版本 ' + ws.plan_version].filter(Boolean).join(' · ') }}</span>
                  </RouterLink>
                </li>
              </ul>
              <span v-else class="empty-hint">暂无关联研究表</span>
            </div>
          </div>
          <template #action>
            <div class="sample-card-actions">
              <RouterLink v-if="sampleWorksheetsMap.map.get(s.id)?.length" :to="'/worksheets/' + encodeURIComponent(sampleWorksheetsMap.map.get(s.id)![0].id)">
                <NButton size="tiny" type="primary">进入研究表</NButton>
              </RouterLink>
              <RouterLink v-else :to="{ path: '/new', query: { project, sample: s.id, batch: s.batch } }">
                <NButton size="tiny" type="primary">新建研究表</NButton>
              </RouterLink>
              <RouterLink :to="{ path: '/specifications', query: { project, sample: s.id, batch: s.batch } }">
                <NButton size="tiny">COA</NButton>
              </RouterLink>
              <RouterLink :to="{ path: '/import', query: { project, sample: s.id, batch: s.batch } }">
                <NButton size="tiny">上传数据</NButton>
              </RouterLink>
            </div>
          </template>
        </NCard>
      </div>
      <p v-else class="muted">此项目暂未登记样品批次。可通过新建研究表或上传 COA 登记新样品。</p>

      <div v-if="sampleWorksheetsMap.unlinked.length" class="unlinked-section" style="margin-top: 18px;">
        <h3>未关联具体样品的研究表</h3>
        <ul class="list">
          <li v-for="item in sampleWorksheetsMap.unlinked" :key="item.id">
            <RouterLink :to="'/worksheets/' + encodeURIComponent(item.id)">{{ item.name }}</RouterLink>
            <span v-if="item.batch || item.condition">{{ [item.batch, item.condition].filter(Boolean).join(' · ') }}</span>
            <span>计划版本 {{ item.plan_version }}</span>
          </li>
        </ul>
      </div>

      <details v-if="sheets.length" class="all-study-search">
      <summary>搜索全部研究表（{{ sheets.length }}）</summary>
      <NInput v-if="sheets.length" v-model:value="sheetSearch" placeholder="搜索研究表名称、批号或条件"
        :input-props="{ 'aria-label': '搜索研究表' }" clearable style="margin: 8px 0;" />
      <ul class="list">
        <li v-for="item in filteredSheets" :key="item.id">
          <RouterLink :to="'/worksheets/' + encodeURIComponent(item.id)">{{ item.name }}</RouterLink>
          <span v-if="item.batch || item.condition">{{ [item.batch, item.condition].filter(Boolean).join(' · ') }}</span>
          <span>计划版本 {{ item.plan_version }}</span>
        </li>
      </ul>
      <p v-if="!filteredSheets.length" class="muted">未找到匹配的研究表。</p>
      </details>
      <p v-else class="muted">此项目暂无研究工作表。</p>
      <template v-if="worksheetDrafts.length">
        <h2>待补全计划</h2>
        <ul class="list"><li v-for="draft in worksheetDrafts" :key="draft.id">
          <RouterLink :to="{ path: '/new', query: { project, resume: draft.id,
            draft: draft.protocol_draft || undefined, table: draft.protocol_table || undefined,
            copy: draft.copy_source || undefined } }">{{ draft.name }}</RouterLink>
          <span>{{ draft.created_at }}</span>
        </li></ul>
      </template>
    </section>
    <ProjectResults v-if="page === 'projects' && project" :key="project" :project="project" />
    <template v-if="sheet">
      <StudyWorkbook :key="sheet.id" :sheet-id="sheet.id" :disabled="editingSheet"
        @saved="refreshSheet" />
      <HistoricalRows :key="'history-' + sheet.id" :sheet-id="sheet.id" :disabled="editingSheet" @saved="refreshSheet" />
      <WorksheetSample :key="'sample-' + sheet.id" :sheet="sheet" :disabled="editingSheet" @saved="refreshSheet" />
      <StudyTemplateHeader :sheet="sheet" />
      <WorksheetEditor :key="sheet.id" :sheet="sheet" @saved="sheet = $event; download = ''" @editing="editingSheet = $event" />
      <details v-if="sheet.events?.length && !editingSheet" class="panel worksheet-notes">
        <summary>研究事件 · {{ sheet.events.length }} 项</summary>
        <ul class="list"><li v-for="event in sheet.events" :key="event.id">
          <strong>{{ [event.category, event.item_label, event.point_label, event.reference].filter(Boolean).join(' · ') }}</strong>
          <p>{{ event.description }}</p>
          <a v-if="event.scan_id" :href="'/api/v1/pdf-ocr/' + event.scan_id + '/file'" target="_blank" rel="noopener">查看附件</a>
        </li></ul>
      </details>
      <section v-if="!editingSheet" class="panel matrix worksheet-matrix" aria-label="研究结果矩阵" tabindex="0">
        <table :style="{ minWidth: Math.max(1240, 430 + sheet.points.length * 140) + 'px' }">
          <thead><tr><th rowspan="2">检验项目<br>Test Item</th><th rowspan="2">可接受标准<br>Acceptable Criteria</th><th :colspan="sheet.points.length">考察时间 / Study timepoints</th></tr>
            <tr><th v-for="point in sheet.points" :key="point.id" class="template-option">{{ point.label }}<br>{{ point.planned_date }}<template v-if="point.signed_status"><br>{{ point.signed_status }}</template></th></tr></thead>
          <tbody>
            <tr v-for="row in sheet.rows" :key="row.id">
              <th scope="row" class="template-option">{{ row.label }}<p class="worksheet-criterion">{{ [row.method, row.reporting_basis ?? sheet.identity.reporting_basis].filter(Boolean).join(' · ') }}</p><small v-if="row.unit">单位：{{ row.unit }}</small></th>
              <td class="template-option">
                <p v-if="row.criteria_text" class="worksheet-criterion">{{ row.criteria_text }}</p>
                <template v-if="!row.criteria_text && !row.standards?.length && row.source_criteria?.length">
                  <p class="pending">历史来源记载</p>
                  <p v-for="criterion in row.source_criteria" :key="criterion" class="worksheet-criterion">{{ criterion }}</p>
                </template>
                <div v-for="standard in row.standards" :key="standard.specification_id + ':' + standard.table + ':' + standard.row">
                  <p class="worksheet-criterion">{{ standard.criterion }}</p>
                  <details><summary>标准来源</summary><a :href="'/api/v1/specifications/' + standard.specification_id + '/file'">{{ standard.filename }} · 表 {{ standard.table }} 行 {{ standard.row }}</a></details>
                </div>
              </td>
              <td v-for="point in sheet.points" :key="point.id">
                <span v-if="cells.get(row.id + ':' + point.id)?.adopted"
                  :class="{ 'outside-limit': cells.get(row.id + ':' + point.id)?.standard_assessment?.status === 'outside' }"
                  :title="cells.get(row.id + ':' + point.id)?.standard_assessment?.criterion">
                  {{ cells.get(row.id + ':' + point.id)?.adopted?.text }}
                  <small v-if="cells.get(row.id + ':' + point.id)?.standard_assessment?.status === 'outside'"> 超出标准</small>
                  <small v-if="cells.get(row.id + ':' + point.id)?.standard_assessment?.status === 'undetermined'"> 待判断</small>
                </span>
                <span v-else-if="point.label === '0M' && sheet.pending_release_references?.[row.id]" class="pending">方案要求引用放行结果，待关联</span>
                <span v-else-if="point.label === '0M' && sheet.invalidated_release_rows?.includes(row.id)" class="pending">放行结果已作废，请重新关联或解除引用</span>
                <span v-else-if="cells.get(row.id + ':' + point.id)?.release_reference" class="pending">
                  {{ cells.get(row.id + ':' + point.id)?.release_reference?.current ? '引用放行' : '放行引用待复核' }} · {{ cells.get(row.id + ':' + point.id)?.display.text }}
                </span>
                <span v-else-if="cells.has(row.id + ':' + point.id)" class="pending">待采用 · {{ cells.get(row.id + ':' + point.id)?.display.text }}</span>
                <span v-else-if="sheet.stopped_checks?.some(change => change.row_id === row.id && change.point_ids.includes(point.id))" class="muted">已停检</span>
                <span v-else-if="!point.required_row_ids.includes(row.id)" class="muted">不要求</span>
                <div v-if="cells.get(row.id + ':' + point.id)?.adopted && cells.get(row.id + ':' + point.id)?.adopted?.result_version_id !== cells.get(row.id + ':' + point.id)?.display.result_version_id" class="pending">
                  最新记录 · {{ cells.get(row.id + ':' + point.id)?.display.text }}
                </div>
              </td>
            </tr>
          </tbody>
        </table>
      </section>
    </template>
    <ImportWorkspace v-if="page === 'import'" :projects="projects" />
    <Specifications v-if="page === 'specifications'" />
    <ScanWorkspace v-if="page === 'scans'" />
    <section v-if="page === 'trends'" class="panel">
      <NSelect :value="activeProject || String(route.query.project || '') || null" filterable clearable placeholder="输入项目编号或名称快速检索 (如 501 / IP281)..." :options="projects.map(p => ({ value: p.project, label: p.project + (p.name ? ' · ' + p.name : '') }))" @update:value="activeProject = $event" />
      <Trend v-if="activeProject || route.query.project" :project="activeProject || String(route.query.project)"
        :batch="!activeProject && route.query.batch ? String(route.query.batch) : undefined"
        :study="!activeProject && route.query.study ? String(route.query.study) : undefined" />
    </section>
    <Deliveries v-if="page === 'deliveries'" :key="route.fullPath" :projects="projects" />
  </div>
</template>

<style scoped>
.page-header-main { display: flex; flex-direction: column; gap: 4px; }
.page-subtitle { margin: 0; color: var(--text-secondary); font-size: 13.5px; }
.page-header-actions { display: flex; align-items: center; gap: 10px; flex-wrap: wrap; }
.header-ghost-link {
  font-size: 13px;
  font-weight: 500;
  color: var(--text-secondary);
  padding: 5px 10px;
  border-radius: var(--radius-sm);
  border: 1px solid var(--border);
  background: var(--bg-surface);
}
.header-ghost-link:hover { color: var(--brand); border-color: var(--brand-border); background: var(--brand-soft); text-decoration: none; }
.download-pill {
  font-size: 13px;
  font-weight: 600;
  color: var(--success-fg);
  background: var(--success-bg);
  border: 1px solid var(--success-border);
  padding: 5px 12px;
  border-radius: var(--radius-sm);
}

.section-bar { display: flex; align-items: center; justify-content: space-between; margin-bottom: 12px; }
.section-bar h2 { margin: 0; }
.section-count { font-size: 12.5px; color: var(--text-muted); font-weight: 500; }
.project-code-link { font-family: var(--font-mono); font-weight: 600; min-width: 110px; }

.outside-limit { color: var(--danger-fg); background: var(--danger-bg); font-weight: 700; padding: 1px 4px; border-radius: 4px; }
.worksheet-matrix { border: 1px solid var(--border); border-radius: var(--radius-md); padding: 0; }
.worksheet-matrix table { table-layout: fixed; min-width: 1240px; width: 100%; border-collapse: separate; border-spacing: 0; }
.worksheet-matrix th, .worksheet-matrix td { padding: 10px 12px; border-right: 1px solid var(--border); border-bottom: 1px solid var(--border); white-space: pre-wrap; overflow-wrap: anywhere; vertical-align: top; background: var(--bg-surface); }
.worksheet-matrix thead th { background: var(--bg-subtle); color: var(--text-secondary); font-weight: 600; }
.worksheet-matrix thead tr:first-child th:first-child { width: 205px; }
.worksheet-matrix thead tr:first-child th:nth-child(2) { width: 225px; }
.worksheet-matrix .template-option { background: var(--brand-soft); color: var(--text-primary); }
.worksheet-matrix details { font-size: 12px; margin-top: 6px; }

.worksheet-criterion { white-space: pre-wrap; margin: 4px 0 0; font-weight: normal; color: var(--text-secondary); font-size: 12.5px; }
.worksheet-notes li { display: grid; grid-template-columns: minmax(0, 1fr); gap: 6px; overflow-wrap: anywhere; }
.worksheet-notes p { margin: 0; white-space: pre-wrap; }

.project-header-row { display: flex; justify-content: space-between; align-items: center; margin-bottom: 16px; }
.project-header-row h2 { margin: 0; }
.sample-cards-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(320px, 1fr)); gap: 16px; margin: 12px 0 20px; }
.sample-card { border: 1px solid var(--border); border-radius: var(--radius-md); box-shadow: var(--shadow-card); }
.sample-card-header { display: flex; justify-content: space-between; align-items: center; width: 100%; gap: 8px; }
.sample-batch-title { font-family: var(--font-mono); font-size: 14px; color: var(--text-primary); }
.sample-meta-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 6px 12px; font-size: 12.5px; margin-bottom: 10px; color: var(--text-primary); }
.meta-label { color: var(--text-muted); font-weight: 500; }
.sample-worksheets-box { margin-top: 10px; padding-top: 10px; border-top: 1px dashed var(--border); font-size: 12.5px; }
.group-title { font-weight: 600; color: var(--text-secondary); }
.sample-sheets-list { list-style: none; padding: 0; margin: 6px 0 0; display: grid; gap: 4px; }
.muted-tag { font-size: 11.5px; color: var(--text-muted); }
.empty-hint { color: var(--text-muted); font-size: 12px; margin: 4px 0; display: inline-block; }
.sample-card-actions { display: flex; gap: 8px; flex-wrap: wrap; }
.all-study-search { margin-top: 16px; padding-top: 12px; border-top: 1px solid var(--border); }
</style>
