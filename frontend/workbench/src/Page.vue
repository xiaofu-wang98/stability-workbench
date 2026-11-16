<script setup lang="ts">
import { computed, defineAsyncComponent, ref, watch } from 'vue';
import { useRoute } from 'vue-router';
import { NAlert, NButton, NInput, NSelect, NSpin } from 'naive-ui';
import { api, type Project, type WorksheetSummary, type Worksheet } from './api';
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
const page = computed(() => route.path.split('/')[1] || 'home');
const project = computed(() => String(route.params.project || ''));
const projects = ref<Project[]>([]);
const sheets = ref<WorksheetSummary[]>([]);
const worksheetDrafts = ref<{ id: string; name: string; created_at: string; protocol_draft: string; protocol_table: string; copy_source: string }[]>([]);
const sheet = ref<Worksheet>();
const editingSheet = ref(false);
const search = ref('');
const sheetSearch = ref('');
const error = ref('');
const busy = ref(false);
const operating = ref(false);
const download = ref('');
const activeProject = ref('');
const filtered = computed(() => projects.value.filter(p =>
  (p.project + ' ' + p.name).toLowerCase().includes(search.value.trim().toLowerCase())));
const filteredSheets = computed(() => sheets.value.filter(item =>
  [item.name, item.batch, item.condition].join(' ').toLowerCase().includes(sheetSearch.value.trim().toLowerCase())));
const cells = computed(() => new Map(sheet.value?.cells.map(c => [c.row_id + ':' + c.point_id, c])));
const titles: Record<string, string> = { home: '工作台', new: '新建研究表', projects: '项目数据', worksheets: '研究工作表', import: '导入文件', specifications: '质量标准', scans: '扫描 PDF', trends: '趋势与比较', deliveries: '生成与历史' };
const actions = [
  ['/import', '导入文件'], ['/new', '新建研究表'],
  ['/projects', '查看项目数据'], ['/trends', '趋势与导出'],
];
watch(() => route.fullPath, async (_, __, onCleanup) => {
  const controller = new AbortController();
  onCleanup(() => controller.abort());
  const init = { signal: controller.signal };
  error.value = '';
  busy.value = true;
  sheet.value = undefined;
  editingSheet.value = false;
  sheets.value = [];
  worksheetDrafts.value = [];
  download.value = '';
  try {
    projects.value = await api<Project[]>('/projects', init);
    if (project.value) {
      const base = '/projects/' + encodeURIComponent(project.value);
      [sheets.value, worksheetDrafts.value] = await Promise.all([
        api<WorksheetSummary[]>(base + '/worksheets', init), api<typeof worksheetDrafts.value>(base + '/worksheet-drafts', init),
      ]);
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
    if (sheet.value === current) download.value = result.download_url;
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
  <header :class="{ 'entry-heading': ['new', 'import', 'worksheets'].includes(page) }">
    <h1>{{ sheet?.name || project || titles[page] }}</h1>
  </header>
  <p v-if="page === 'home'" class="subtitle">稳定性研究、结果与交付</p>
  <NAlert v-if="error" type="error" role="alert">{{ error }}</NAlert>
  <NSpin v-if="busy" aria-label="正在读取数据" />
  <div v-else>
    <NewWorksheet v-if="page === 'new'" :key="route.fullPath" :projects="projects" />
    <div v-if="page === 'home'" class="actions">
      <RouterLink v-for="[path, label] in actions" :key="path" :to="path">{{ label }} <span aria-hidden="true">↗</span></RouterLink>
    </div>
    <section v-if="page === 'home' || (page === 'projects' && !project)" class="panel">
      <h2>项目目录</h2>
      <NInput v-model:value="search" placeholder="搜索项目编号或名称" aria-label="搜索项目" clearable />
      <ul class="list">
        <li v-for="p in filtered" :key="p.project">
          <RouterLink :to="'/projects/' + encodeURIComponent(p.project)">{{ p.project }}</RouterLink>
          <span>{{ p.name }}</span>
        </li>
      </ul>
      <p v-if="!filtered.length" class="muted">{{ projects.length ? '没有匹配项目。' : '当前数据目录还没有项目。' }}</p>
    </section>
    <section v-if="page === 'projects' && project" class="panel">
      <h2>研究工作表</h2>
      <RouterLink :to="{ path: '/new', query: { project } }">新建研究表</RouterLink>
      <NInput v-if="sheets.length" v-model:value="sheetSearch" placeholder="搜索研究表名称、批号或条件"
        :input-props="{ 'aria-label': '搜索研究表' }" clearable />
      <ul class="list">
        <li v-for="item in filteredSheets" :key="item.id">
          <RouterLink :to="'/worksheets/' + encodeURIComponent(item.id)">{{ item.name }}</RouterLink>
          <span v-if="item.batch || item.condition">{{ [item.batch, item.condition].filter(Boolean).join(' · ') }}</span>
          <span>计划版本 {{ item.plan_version }}</span>
        </li>
      </ul>
      <p v-if="!sheets.length" class="muted">此项目暂无研究工作表。</p>
      <p v-else-if="!filteredSheets.length" class="muted">未找到匹配的研究表。</p>
      <template v-if="worksheetDrafts.length">
        <h2>待补全计划</h2>
        <ul class="list"><li v-for="draft in worksheetDrafts" :key="draft.id">
          <RouterLink :to="{ path: '/new', query: { project, resume: draft.id,
            draft: draft.protocol_draft || undefined, table: draft.protocol_table || undefined,
            copy: draft.copy_source || undefined } }">{{ draft.name }}</RouterLink>
          <span>{{ draft.created_at }}</span>
        </li></ul>
      </template>
      <RouterLink :to="'/trends?project=' + encodeURIComponent(project)">查看项目趋势</RouterLink>
      <RouterLink :to="'/deliveries?project=' + encodeURIComponent(project)"> · 生成文件</RouterLink>
    </section>
    <ProjectResults v-if="page === 'projects' && project" :key="project" :project="project" />
    <template v-if="sheet">
      <p><RouterLink :to="{ path: '/new', query: { project: sheet.project, copy: sheet.id } }">复制研究表结构</RouterLink></p>
      <p v-if="sheet.copied_from"><RouterLink :to="'/worksheets/' + sheet.copied_from.sheet_id">查看结构来源 · 计划版本 {{ sheet.copied_from.plan_version }}</RouterLink></p>
      <p v-if="sheet.protocol_draft_id"><a :href="'/api/v1/plan-drafts/' + sheet.protocol_draft_id + '/file'">下载原方案<template v-if="sheet.protocol_table"> · 表 {{ sheet.protocol_table }}</template></a></p>
      <p class="subtitle"><RouterLink :to="'/projects/' + encodeURIComponent(sheet.project)">{{ sheet.project }}</RouterLink> · {{ sheet.identity.batch }} · {{ sheet.identity.condition }} {{ sheet.identity.orientation }}</p>
      <NButton :disabled="editingSheet" :loading="operating" @click="confirmation">生成确认表 Excel</NButton>
      <a v-if="download" :href="download"> 下载确认表</a>
      <StudyWorkbook :key="sheet.id" :sheet-id="sheet.id" :disabled="editingSheet"
        @saved="refreshSheet" />
      <HistoricalRows :key="'history-' + sheet.id" :sheet-id="sheet.id" :disabled="editingSheet" @saved="refreshSheet" />
      <WorksheetSample :key="'sample-' + sheet.id" :sheet="sheet" :disabled="editingSheet" @saved="refreshSheet" />
      <StudyTemplateHeader :sheet="sheet" :product-name="projects.find(project => project.project === sheet?.project)?.name || ''" />
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
            <tr><th v-for="point in sheet.points" :key="point.id" class="template-option">{{ point.label }}<br>{{ point.planned_date }}</th></tr></thead>
          <tbody>
            <tr v-for="row in sheet.rows" :key="row.id">
              <th scope="row" class="template-option">{{ row.label }}<p class="worksheet-criterion">{{ [row.method, row.reporting_basis ?? sheet.identity.reporting_basis].filter(Boolean).join(' · ') }}</p><small v-if="row.unit">单位：{{ row.unit }}</small></th>
              <td class="template-option">
                <p v-if="row.criteria_text" class="worksheet-criterion">{{ row.criteria_text }}</p>
                <div v-for="standard in row.standards" :key="standard.specification_id + ':' + standard.table + ':' + standard.row">
                  <p class="worksheet-criterion">{{ standard.criterion }}</p>
                  <details><summary>标准来源</summary><a :href="'/api/v1/specifications/' + standard.specification_id + '/file'">{{ standard.filename }} · 表 {{ standard.table }} 行 {{ standard.row }}</a></details>
                </div>
              </td>
              <td v-for="point in sheet.points" :key="point.id">
                <template v-if="cells.get(row.id + ':' + point.id)?.adopted">{{ cells.get(row.id + ':' + point.id)?.adopted?.text }}</template>
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
.worksheet-matrix { border: 1px solid #afc4d5; border-radius: 9px; padding: 0; }
.worksheet-matrix table { table-layout: fixed; min-width: 1240px; width: 100%; border-collapse: separate; border-spacing: 0; }
.worksheet-matrix th,.worksheet-matrix td { padding: 8px 10px; border-right: 1px solid #becfdd; border-bottom: 1px solid #becfdd; white-space: pre-wrap; overflow-wrap: anywhere; vertical-align: top; background: #fff; }
.worksheet-matrix thead th { background: #9ec4e2; color: #102d49; }
.worksheet-matrix thead tr:first-child th:first-child { width: 205px; }
.worksheet-matrix thead tr:first-child th:nth-child(2) { width: 225px; }
.worksheet-matrix .template-option { background: #d5e7f4; }
.worksheet-matrix details { font-size: 12px; margin-top: 6px; }

.worksheet-criterion { white-space: pre-wrap; margin: 4px 0 0; font-weight: normal; }
.worksheet-notes li { display: grid; grid-template-columns: minmax(0, 1fr); gap: 6px; overflow-wrap: anywhere; }
.worksheet-notes p { margin: 0; white-space: pre-wrap; }
</style>

