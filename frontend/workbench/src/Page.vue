<script setup lang="ts">
import { computed, defineAsyncComponent, inject, ref, watch, type Ref } from 'vue';
import { useRoute, useRouter } from 'vue-router';
import { NAlert, NButton, NSelect, NSpin } from 'naive-ui';
import { api, archiveStudyObject, triggerDownload, type Project, type SampleEntry, type WorksheetSummary, type Worksheet } from './api';
import { useWorkbenchContextStore } from './stores/workbenchContext';
import HomeWorkbench from './views/HomeWorkbench.vue';
import ProjectCatalogView from './views/ProjectCatalogView.vue';
import ProjectDetailView from './views/ProjectDetailView.vue';
import WorksheetDetailView from './views/WorksheetDetailView.vue';

const Trend = defineAsyncComponent(() => import('./Trend.vue'));
const NewWorksheet = defineAsyncComponent(() => import('./NewWorksheet.vue'));
const ImportWorkspace = defineAsyncComponent(() => import('./ImportWorkspace.vue'));
const Specifications = defineAsyncComponent(() => import('./Specifications.vue'));
const ScanWorkspace = defineAsyncComponent(() => import('./ScanWorkspace.vue'));
const Deliveries = defineAsyncComponent(() => import('./Deliveries.vue'));

const route = useRoute();
const router = useRouter();
const ctx = useWorkbenchContextStore();
const page = computed(() => route.path.split('/')[1] || 'home');
const project = computed(() => String(route.params.project || ''));
const projectScope = inject<Ref<string>>('workbenchProject');
const projects = ref<Project[]>([]);
const sheets = ref<WorksheetSummary[]>([]);
const samples = ref<SampleEntry[]>([]);
const worksheetDrafts = ref<{ id: string; name: string; created_at: string; protocol_draft: string; protocol_table: string; copy_source: string }[]>([]);
const sheet = ref<Worksheet>();
const editingSheet = ref(false);
const error = ref('');
const busy = ref(false);
const operating = ref(false);
const download = ref('');
const activeProject = ref('');

watch(() => sheet.value, val => {
  if (val) {
    if (val.project && projectScope) projectScope.value = val.project;
    ctx.recordSheetVisit({ id: val.id, name: val.name, project: val.project, batch: val.identity.batch, condition: val.identity.condition });
  }
});

watch(() => [route.query.project, ctx.activeProject], ([qProj, ctxProj]) => {
  if (page.value === 'trends') {
    activeProject.value = String(qProj || ctxProj || '');
  }
}, { immediate: true });

const titles: Record<string, string> = {
  home: '工作台',
  new: '新建研究表',
  projects: '项目数据',
  worksheets: '研究工作表',
  import: '导入文件',
  specifications: '质量标准与 COA',
  scans: '扫描 PDF',
  trends: '趋势与比较',
  deliveries: '生成与历史',
};

const subtitles: Record<string, string> = {
  home: '任务分发、实时待办与全链路工作流起点',
  import: '统一文件导入中心：汇总 Excel、放行 CoA、方案 Word、研究表回传、质量标准与扫描 PDF',
  new: '按方案矩阵、质量标准、复制结构或空白模板快速创建研究表',
  specifications: '标准原件条款核对 → 项目检项字典登记 → 样品放行 COA 录入与 0M 联动',
  scans: '扫描件 PDF 归档、OCR 识别对照与标准条款登记',
  trends: '按检项与方法绘制多批次趋势曲线与标准警戒线',
  deliveries: '受控表格导出、稳定性确认表、考察报告 Word 与报告版本差异对比',
};

async function loadSamples(base: string, init: RequestInit) {
  const limit = 100;
  const entries: SampleEntry[] = [];
  for (let offset = 0; ; offset += limit) {
    const pageData = await api<SampleEntry[]>(base + '/samples?limit=' + limit + '&offset=' + offset, init);
    entries.push(...pageData);
    if (pageData.length < limit) return entries;
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
    ctx.setProjectsSnapshot(projects.value);
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
    if (route.params.sheet) {
      sheet.value = await api('/worksheets/' + encodeURIComponent(String(route.params.sheet)), init);
    }
  } catch (e) {
    if (!controller.signal.aborted) error.value = String((e as Error).message);
  } finally {
    if (!controller.signal.aborted) busy.value = false;
  }
}, { immediate: true });

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

async function confirmation() {
  const current = sheet.value;
  if (!current) return;
  operating.value = true;
  error.value = '';
  try {
    const query = new URLSearchParams({ revision: String(current.revision), selection_id: current.selection_id });
    const result = await api<{ download_url: string }>('/worksheets/' + encodeURIComponent(current.id) + '/confirmation?' + query, { method: 'POST' });
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
      <p v-if="sheet" class="page-subtitle">
        <RouterLink :to="'/projects/' + encodeURIComponent(sheet.project)">{{ sheet.project }}</RouterLink>
        · {{ sheet.identity.batch }} · {{ sheet.identity.condition }} {{ sheet.identity.orientation }}
      </p>
      <p v-else-if="project" class="page-subtitle">
        项目台账 · {{ projects.find(p => p.project === project)?.name || '稳定性研究项目' }}
      </p>
      <p v-else-if="subtitles[page]" class="page-subtitle">{{ subtitles[page] }}</p>
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
    <HomeWorkbench v-if="page === 'home'" />
    <NewWorksheet v-if="page === 'new'" :key="route.fullPath" :projects="projects" />
    <ProjectCatalogView v-if="page === 'projects' && !project" :projects="projects" />
    <ProjectDetailView
      v-if="page === 'projects' && project"
      :project="project"
      :samples="samples"
      :sheets="sheets"
      :worksheet-drafts="worksheetDrafts"
    />
    <WorksheetDetailView
      v-if="sheet"
      :sheet="sheet"
      :editing-sheet="editingSheet"
      @saved="sheet = $event; download = ''"
      @refresh="refreshSheet"
      @editing="editingSheet = $event"
      @confirmation="confirmation"
    />
    <ImportWorkspace v-if="page === 'import'" :projects="projects" />
    <Specifications v-if="page === 'specifications'" />
    <ScanWorkspace v-if="page === 'scans'" />
    <section v-if="page === 'trends'" class="panel trend-shell-panel">
      <div class="trend-selector-bar">
        <label class="trend-proj-label">目标项目</label>
        <div style="width: 380px;">
          <NSelect
            :value="activeProject || String(route.query.project || '') || null"
            filterable
            clearable
            placeholder="输入项目编号或名称快速检索 (如 501 / IP281)..."
            :options="projects.map(p => ({ value: p.project, label: p.project + (p.name ? ' · ' + p.name : '') }))"
            @update:value="val => { activeProject = val || ''; if (val) ctx.setActiveProject(val, true); }"
          />
        </div>
      </div>
      <Trend
        v-if="activeProject || route.query.project"
        :project="activeProject || String(route.query.project)"
        :batch="!activeProject && route.query.batch ? String(route.query.batch) : (route.query.batch ? String(route.query.batch) : undefined)"
        :study="!activeProject && route.query.study ? String(route.query.study) : (route.query.study ? String(route.query.study) : undefined)"
      />
      <p v-else class="muted" style="margin-top: 14px;">请先在上方或顶栏选择一个项目，即可按检项与方法绘制批间趋势曲线与标准线。</p>
    </section>
    <Deliveries v-if="page === 'deliveries'" :key="route.fullPath" :projects="projects" />
  </div>
</template>

<style scoped>
.page-header-main { display: flex; flex-direction: column; gap: 4px; }
.page-subtitle { margin: 0; color: var(--text-secondary); font-size: 14px; }
.page-header-actions { display: flex; align-items: center; gap: 10px; flex-wrap: wrap; }
.header-ghost-link {
  font-size: 13.5px;
  font-weight: 500;
  color: var(--text-secondary);
  padding: 5px 11px;
  border-radius: var(--radius-sm);
  border: 1px solid var(--border);
  background: var(--bg-surface);
}
.header-ghost-link:hover { color: var(--brand); border-color: var(--brand-border); background: var(--brand-soft); text-decoration: none; }
.download-pill {
  font-size: 13.5px;
  font-weight: 600;
  color: var(--success-fg);
  background: var(--success-bg);
  border: 1px solid var(--success-border);
  padding: 5px 12px;
  border-radius: var(--radius-sm);
}
.trend-shell-panel { margin-top: 0; }
.trend-selector-bar { display: flex; align-items: center; gap: 12px; margin-bottom: 12px; }
.trend-proj-label { font-size: 14px; font-weight: 600; color: var(--text-secondary); }
</style>
