<script setup lang="ts">
import { computed, defineAsyncComponent, ref, watch } from 'vue';
import { NButton, NCard, NInput, NSpace, NTag } from 'naive-ui';
import { api, type SampleEntry, type WorksheetSummary } from '../api';
import { useWorkbenchContextStore } from '../stores/workbenchContext';

const ProjectResults = defineAsyncComponent(() => import('../ProjectResults.vue'));

const props = defineProps<{
  project: string;
  samples: SampleEntry[];
  sheets: WorksheetSummary[];
  worksheetDrafts: { id: string; name: string; created_at: string; protocol_draft: string; protocol_table: string; copy_source: string }[];
}>();

const ctx = useWorkbenchContextStore();
const sheetSearch = ref('');
const uncommittedPreviews = ref<{ preview_id: string; filename: string; draft: number }[]>([]);

watch(
  () => props.project,
  async (code, _, cleanup) => {
    const controller = new AbortController();
    cleanup(() => controller.abort());
    sheetSearch.value = '';
    uncommittedPreviews.value = [];
    if (!code) return;
    try {
      const list = await api<{ preview_id: string; filename: string; draft: number }[]>(
        '/projects/' + encodeURIComponent(code) + '/previews?limit=25',
        { signal: controller.signal },
      );
      if (!controller.signal.aborted) {
        uncommittedPreviews.value = list.filter(item => item.draft > 0);
      }
    } catch {
      // Optional project gap enrichment
    }
  },
  { immediate: true },
);

const filteredSheets = computed(() =>
  props.sheets.filter(item =>
    [item.name, item.batch, item.condition].join(' ').toLowerCase().includes(sheetSearch.value.trim().toLowerCase()),
  ),
);

const sampleWorksheetsMap = computed(() => {
  const map = new Map<string, WorksheetSummary[]>();
  for (const s of props.samples) map.set(s.id, []);
  const unlinked: WorksheetSummary[] = [];
  for (const item of props.sheets) {
    if (item.sample_id && map.has(item.sample_id)) {
      map.get(item.sample_id)!.push(item);
    } else {
      unlinked.push(item);
    }
  }
  return { map, unlinked };
});

const samplesWithoutStudy = computed(() =>
  props.samples.filter(s => !(sampleWorksheetsMap.value.map.get(s.id)?.length)),
);

function scrollToSection(id: string) {
  document.getElementById(id)?.scrollIntoView({ behavior: 'smooth', block: 'start' });
}
</script>

<template>
  <div class="project-detail-view">
    <!-- Top Overview Strip + L3 Gap Signals -->
    <section class="panel project-overview-bar" aria-label="项目概览与待办缺口">
      <div class="overview-metrics">
        <div class="metric-box">
          <span>样品批次</span>
          <strong>{{ samples.length }}</strong>
        </div>
        <div class="metric-box">
          <span>研究工作表</span>
          <strong>{{ sheets.length }}</strong>
        </div>
        <div class="metric-box" :class="{ warn: worksheetDrafts.length > 0 }">
          <span>待补全计划</span>
          <strong>{{ worksheetDrafts.length }}</strong>
        </div>
        <div class="metric-box" :class="{ warn: uncommittedPreviews.length > 0 }">
          <span>未提交导入</span>
          <strong>{{ uncommittedPreviews.length }}</strong>
        </div>
      </div>

      <div class="overview-actions">
        <NButton size="small" secondary @click="ctx.togglePin(project)">
          {{ ctx.isPinned(project) ? '★ 已置顶关注' : '☆ 置顶关注' }}
        </NButton>
        <RouterLink :to="{ path: '/import', query: { project } }">
          <NButton size="small">导入文件</NButton>
        </RouterLink>
        <RouterLink :to="{ path: '/specifications', query: { project } }">
          <NButton size="small">标准与 COA</NButton>
        </RouterLink>
        <RouterLink :to="{ path: '/new', query: { project } }">
          <NButton type="primary" size="small">新建研究表</NButton>
        </RouterLink>
        <RouterLink :to="'/trends?project=' + encodeURIComponent(project)">
          <NButton size="small">查看项目趋势</NButton>
        </RouterLink>
        <RouterLink :to="'/deliveries?project=' + encodeURIComponent(project)">
          <NButton size="small">生成交付文件</NButton>
        </RouterLink>
      </div>

      <!-- Actionable Gap Signals -->
      <div
        v-if="uncommittedPreviews.length || worksheetDrafts.length || sampleWorksheetsMap.unlinked.length || samplesWithoutStudy.length"
        class="project-gap-strip"
      >
        <span class="gap-strip-title">当前待处理：</span>
        <RouterLink
          v-for="p in uncommittedPreviews.slice(0, 2)"
          :key="p.preview_id"
          class="gap-chip warn"
          :to="{ path: '/import', query: { project, source: p.preview_id } }"
        >
          未提交导入「{{ p.filename }}」（{{ p.draft }} 条）→
        </RouterLink>
        <button
          v-if="worksheetDrafts.length"
          type="button"
          class="gap-chip warn"
          @click="scrollToSection('project-drafts-section')"
        >
          {{ worksheetDrafts.length }} 份待补全研究计划 →
        </button>
        <button
          v-if="sampleWorksheetsMap.unlinked.length"
          type="button"
          class="gap-chip info"
          @click="scrollToSection('unlinked-worksheets-section')"
        >
          {{ sampleWorksheetsMap.unlinked.length }} 张研究表未关联样品 →
        </button>
        <span v-if="samplesWithoutStudy.length" class="gap-chip muted-chip">
          {{ samplesWithoutStudy.length }} 个样品尚未建研究表
        </span>
      </div>

      <!-- Anchor Navigation Bar -->
      <div class="section-anchor-tabs">
        <button type="button" @click="scrollToSection('project-samples-section')">样品批次与研究表</button>
        <button type="button" @click="scrollToSection('project-results-view')">检测结果台账</button>
        <button v-if="worksheetDrafts.length || uncommittedPreviews.length" type="button" @click="scrollToSection('project-drafts-section')">
          待补全计划与导入（{{ worksheetDrafts.length + uncommittedPreviews.length }}）
        </button>
      </div>
    </section>

    <!-- Section 1: Samples & Worksheets -->
    <section id="project-samples-section" class="panel">
      <div class="project-header-row">
        <h2>样品批次与研究表</h2>
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
              <RouterLink :to="{ path: '/specifications', query: { project, sample: s.id, batch: s.batch, focus: 'coa' } }">
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

      <div v-if="sampleWorksheetsMap.unlinked.length" id="unlinked-worksheets-section" class="unlinked-section">
        <h3>未关联具体样品的研究表（{{ sampleWorksheetsMap.unlinked.length }}）</h3>
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
        <NInput
          v-model:value="sheetSearch"
          placeholder="搜索研究表名称、批号或条件"
          :input-props="{ 'aria-label': '搜索研究表' }"
          clearable
          style="margin: 8px 0;"
        />
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

      <div v-if="worksheetDrafts.length || uncommittedPreviews.length" id="project-drafts-section" class="drafts-and-imports">
        <template v-if="worksheetDrafts.length">
          <h2>待补全计划</h2>
          <ul class="list">
            <li v-for="draft in worksheetDrafts" :key="draft.id">
              <RouterLink :to="{ path: '/new', query: { project, resume: draft.id,
                draft: draft.protocol_draft || undefined, table: draft.protocol_table || undefined,
                copy: draft.copy_source || undefined } }">{{ draft.name }}</RouterLink>
              <span>{{ draft.created_at }}</span>
            </li>
          </ul>
        </template>

        <template v-if="uncommittedPreviews.length">
          <h2 style="margin-top: 16px;">未提交的导入文件</h2>
          <ul class="list">
            <li v-for="item in uncommittedPreviews" :key="item.preview_id">
              <RouterLink :to="{ path: '/import', query: { project, source: item.preview_id } }">{{ item.filename }}</RouterLink>
              <NTag size="small" type="warning">{{ item.draft }} 条尚未提交</NTag>
            </li>
          </ul>
        </template>
      </div>
    </section>

    <!-- Section 2: Project Results Ledger -->
    <ProjectResults :key="project" :project="project" />
  </div>
</template>

<style scoped>
.project-detail-view { display: grid; gap: 18px; }
.project-overview-bar { margin-top: 0; display: grid; gap: 14px; }
.overview-metrics {
  display: grid;
  grid-template-columns: repeat(4, minmax(0, 1fr));
  gap: 12px;
}
.metric-box {
  padding: 12px 16px;
  border-radius: var(--radius-md);
  background: var(--bg-subtle);
  border: 1px solid var(--border);
  display: flex;
  align-items: baseline;
  justify-content: space-between;
}
.metric-box span { font-size: 13.5px; color: var(--text-secondary); font-weight: 500; }
.metric-box strong { font-size: 22px; font-weight: 700; color: var(--text-primary); font-variant-numeric: tabular-nums; }
.metric-box.warn { background: var(--warning-bg); border-color: var(--warning-border); }
.metric-box.warn strong { color: var(--warning-fg); }

.overview-actions { display: flex; flex-wrap: wrap; align-items: center; gap: 10px; }

.project-gap-strip {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 8px;
  padding: 10px 14px;
  border-radius: var(--radius-md);
  background: var(--bg-subtle);
  border: 1px solid var(--border);
}
.gap-strip-title { font-size: 13px; font-weight: 600; color: var(--text-secondary); }
.gap-chip {
  display: inline-flex;
  align-items: center;
  padding: 4px 10px;
  border-radius: 999px;
  font: inherit;
  font-size: 12.5px;
  font-weight: 600;
  cursor: pointer;
  border: 1px solid transparent;
}
.gap-chip.warn { background: var(--warning-bg); border-color: var(--warning-border); color: var(--warning-fg); }
.gap-chip.info { background: var(--brand-soft); border-color: var(--brand-border); color: var(--brand); }
.gap-chip.muted-chip { background: var(--bg-surface); border-color: var(--border); color: var(--text-secondary); cursor: default; }

.section-anchor-tabs {
  display: flex;
  gap: 8px;
  padding-top: 10px;
  border-top: 1px solid var(--border);
}
.section-anchor-tabs button {
  padding: 6px 14px;
  border-radius: var(--radius-sm);
  border: 1px solid var(--border);
  background: var(--bg-surface);
  color: var(--text-secondary);
  font: inherit;
  font-size: 13.5px;
  font-weight: 600;
  cursor: pointer;
}
.section-anchor-tabs button:hover { border-color: var(--brand); color: var(--brand); background: var(--brand-soft); }

.project-header-row { display: flex; justify-content: space-between; align-items: center; margin-bottom: 14px; }
.project-header-row h2 { margin: 0; }
.sample-cards-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(330px, 1fr)); gap: 16px; margin: 12px 0 20px; }
.sample-card { border: 1px solid var(--border); border-radius: var(--radius-md); box-shadow: var(--shadow-card); }
.sample-card-header { display: flex; justify-content: space-between; align-items: center; width: 100%; gap: 8px; }
.sample-batch-title { font-family: var(--font-mono); font-size: 14.5px; color: var(--text-primary); }
.sample-meta-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 6px 12px; font-size: 13px; margin-bottom: 10px; color: var(--text-primary); }
.meta-label { color: var(--text-muted); font-weight: 500; }
.sample-worksheets-box { margin-top: 10px; padding-top: 10px; border-top: 1px dashed var(--border); font-size: 13px; }
.group-title { font-weight: 600; color: var(--text-secondary); }
.sample-sheets-list { list-style: none; padding: 0; margin: 6px 0 0; display: grid; gap: 4px; }
.muted-tag { font-size: 12px; color: var(--text-muted); }
.empty-hint { color: var(--text-muted); font-size: 12.5px; margin: 4px 0; display: inline-block; }
.sample-card-actions { display: flex; gap: 8px; flex-wrap: wrap; }
.unlinked-section { margin-top: 18px; padding-top: 14px; border-top: 1px solid var(--border); }
.all-study-search { margin-top: 16px; padding-top: 12px; border-top: 1px solid var(--border); }
.drafts-and-imports { margin-top: 20px; padding-top: 16px; border-top: 1px solid var(--border); }
</style>
