<script setup lang="ts">
import { computed, defineAsyncComponent, onMounted, ref, watch } from 'vue';
import { NButton, NTag } from 'naive-ui';
import type { Worksheet } from '../api';
import { useWorkbenchContextStore } from '../stores/workbenchContext';
import ActionNextStepBanner, { type NextStepAction } from '../components/ActionNextStepBanner.vue';
import StudyTemplateHeader from '../StudyTemplateHeader.vue';
import StudyWorkbook from '../StudyWorkbook.vue';
import HistoricalRows from '../HistoricalRows.vue';
import WorksheetSample from '../WorksheetSample.vue';

const WorksheetEditor = defineAsyncComponent(() => import('../WorksheetEditor.vue'));

const props = defineProps<{
  sheet: Worksheet;
  editingSheet: boolean;
}>();

const emit = defineEmits<{
  saved: [Worksheet];
  refresh: [];
  editing: [boolean];
  confirmation: [];
}>();

const ctx = useWorkbenchContextStore();
const editorRef = ref<{ begin: () => Promise<void>; beginAndOpenRelease: () => Promise<void> }>();

const activeDrawer = ref<'' | 'workbook' | 'history' | 'sample' | 'events'>('');
const showCreatedBanner = ref(false);
const showCoa0mPrompt = ref(false);
const savedReceiptCount = ref<number | null>(null);
const savedReceiptSource = ref<'editor' | 'workbook'>('editor');

const cells = computed(() => new Map(props.sheet.cells.map(c => [c.row_id + ':' + c.point_id, c])));
const has0M = computed(() => props.sheet.points.some(p => p.label === '0M'));
const pendingReleaseCount = computed(() =>
  Object.keys(props.sheet.pending_release_references || {}).length + (props.sheet.invalidated_release_rows?.length || 0),
);
const outsideLimitCount = computed(() =>
  props.sheet.cells.filter(c => c.adopted && c.standard_assessment?.status === 'outside').length,
);

function checkContextPrompts() {
  showCreatedBanner.value = ctx.consumeJustCreatedSheet(props.sheet.id);
  showCoa0mPrompt.value = ctx.consumePendingWorksheetAction(props.sheet.id) === 'coa0m';
}

onMounted(checkContextPrompts);
watch(() => props.sheet.id, () => {
  activeDrawer.value = '';
  savedReceiptCount.value = null;
  checkContextPrompts();
});

function toggleDrawer(name: 'workbook' | 'history' | 'sample' | 'events') {
  activeDrawer.value = activeDrawer.value === name ? '' : name;
}

async function launchCoa0mFlow() {
  showCoa0mPrompt.value = false;
  showCreatedBanner.value = false;
  await editorRef.value?.beginAndOpenRelease();
}

const createdSteps = computed<NextStepAction[]>(() => {
  const steps: NextStepAction[] = [];
  if (has0M.value && props.sheet.editable) {
    steps.push({ label: '1. 从 COA 批量复制到 0M', primary: true, onClick: () => void launchCoa0mFlow() });
  }
  if (props.sheet.editable) {
    steps.push({
      label: has0M.value ? '2. 开始在线录数' : '1. 开始在线录数',
      primary: !has0M.value,
      onClick: () => {
        showCreatedBanner.value = false;
        void editorRef.value?.begin();
      },
    });
  }
  steps.push({
    label: '下载离线填写模板',
    onClick: () => {
      showCreatedBanner.value = false;
      activeDrawer.value = 'workbook';
    },
  });
  return steps;
});

const savedNextSteps = computed<NextStepAction[]>(() => [
  {
    label: '1. 导出本表确认单',
    primary: true,
    onClick: () => emit('confirmation'),
  },
  {
    label: '2. 查看该批次趋势与比较',
    to: {
      path: '/trends',
      query: {
        project: props.sheet.project,
        batch: props.sheet.identity.batch || undefined,
        study: props.sheet.identity.study || undefined,
      },
    },
  },
  {
    label: '3. 前往生成受控表或考察报告',
    to: {
      path: '/deliveries',
      query: {
        project: props.sheet.project,
        batch: props.sheet.identity.batch || undefined,
        format: 'controlled',
      },
    },
  },
]);
</script>

<template>
  <div class="worksheet-detail-view">
    <!-- L3 Live Gap Alerts Strip -->
    <section
      v-if="!editingSheet && (!sheet.sample_id || pendingReleaseCount > 0 || outsideLimitCount > 0)"
      class="ws-gap-bar"
      aria-label="本研究表待处理缺口"
    >
      <span class="ws-gap-label">待处理关注：</span>
      <button v-if="pendingReleaseCount > 0 && sheet.editable" type="button" class="ws-gap-chip warn" @click="launchCoa0mFlow">
        0M 放行引用待关联或失效（{{ pendingReleaseCount }} 项）· 立即批量处理 →
      </button>
      <button v-if="!sheet.sample_id && sheet.editable" type="button" class="ws-gap-chip info" @click="toggleDrawer('sample')">
        尚未关联具体样品对象 · 去关联 →
      </button>
      <NTag v-if="outsideLimitCount > 0" type="error" size="small">
        存在 {{ outsideLimitCount }} 项超出可接受标准结果
      </NTag>
    </section>

    <!-- Post-Creation Guided Handoff -->
    <ActionNextStepBanner
      v-if="showCreatedBanner"
      :title="'研究表创建成功 · ' + sheet.name"
      summary="计划版本已建立。接下来推荐先关联 0M 放行结果，或直接开始在线录数 / 下载离线填写模板。"
      :steps="createdSteps"
      :dismissible="true"
      @dismiss="showCreatedBanner = false"
    />

    <!-- Incoming COA -> 0M Handoff Banner -->
    <ActionNextStepBanner
      v-if="showCoa0mPrompt && sheet.editable"
      tone="info"
      title="已从放行 COA 跳转至此研究表"
      summary="点击下方按钮将自动申请编辑租约并直接打开「从 COA 批量复制到 0M」预览面板。"
      :steps="[
        { label: '1. 开启编辑并打开 0M 批量复制', primary: true, onClick: () => void launchCoa0mFlow() },
      ]"
      :dismissible="true"
      @dismiss="showCoa0mPrompt = false"
    />

    <!-- Post-Save / Post-Roundtrip Downstream Handoff -->
    <ActionNextStepBanner
      v-if="savedReceiptCount !== null && !editingSheet"
      :title="savedReceiptSource === 'workbook' ? `模板回传已写入 ${savedReceiptCount} 个结果格` : `研究表结果已保存（本次变更 ${savedReceiptCount} 格）`"
      summary="数据已按 GMP 版本留痕归档。推荐顺手完成下游确认表导出、批间趋势查看或受控交付。"
      :steps="savedNextSteps"
      :dismissible="true"
      @dismiss="savedReceiptCount = null"
    />

    <!-- Compact Study Metadata Header (Always mounted with aria-label="稳定性研究信息") -->
    <StudyTemplateHeader :sheet="sheet" />

    <!-- Compact Secondary Tools Strip (Replaces 5 giant stacked panels) -->
    <div v-if="!editingSheet" class="ws-tools-strip" role="toolbar" aria-label="研究表辅助工具">
      <button type="button" class="ws-tool-btn" :class="{ active: activeDrawer === 'workbook' }" @click="toggleDrawer('workbook')">
        Excel 模板下载与离线回传
      </button>
      <button type="button" class="ws-tool-btn" :class="{ active: activeDrawer === 'history' }" @click="toggleDrawer('history')">
        补入已采用历史结果行
      </button>
      <button v-if="sheet.editable && !sheet.sample_id" type="button" class="ws-tool-btn highlight" :class="{ active: activeDrawer === 'sample' }" @click="toggleDrawer('sample')">
        关联已有样品
      </button>
      <div class="ws-tools-right">
        <RouterLink class="ws-inline-jump" :to="{ path: '/trends', query: { project: sheet.project, batch: sheet.identity.batch, study: sheet.identity.study } }">
          批间趋势对比 →
        </RouterLink>
        <RouterLink class="ws-inline-jump" :to="{ path: '/deliveries', query: { project: sheet.project, batch: sheet.identity.batch, format: 'confirmation' } }">
          定制确认表 / 交付 →
        </RouterLink>
      </div>
    </div>

    <!-- Expandable Secondary Tool Panel -->
    <div v-if="activeDrawer === 'workbook' && !editingSheet">
      <StudyWorkbook
        :key="sheet.id"
        :sheet-id="sheet.id"
        :disabled="editingSheet"
        @saved="emit('refresh')"
        @committed="count => { savedReceiptSource = 'workbook'; savedReceiptCount = count; }"
      />
    </div>
    <div v-if="activeDrawer === 'history' && !editingSheet">
      <HistoricalRows :key="'history-' + sheet.id" :sheet-id="sheet.id" :disabled="editingSheet" @saved="emit('refresh')" />
    </div>
    <div v-if="activeDrawer === 'sample' && !editingSheet">
      <WorksheetSample :key="'sample-' + sheet.id" :sheet="sheet" :disabled="editingSheet" @saved="activeDrawer = ''; emit('refresh')" />
    </div>
    <details v-if="sheet.events?.length && !editingSheet" class="panel worksheet-notes">
      <summary>研究事件 · {{ sheet.events.length }} 项</summary>
      <ul class="list"><li v-for="event in sheet.events" :key="event.id">
        <strong>{{ [event.category, event.item_label, event.point_label, event.reference].filter(Boolean).join(' · ') }}</strong>
        <p>{{ event.description }}</p>
        <a v-if="event.scan_id" :href="'/api/v1/pdf-ocr/' + event.scan_id + '/file'" target="_blank" rel="noopener">查看附件</a>
      </li></ul>
    </details>

    <!-- Primary Centerpiece: Editor Toolbar + Univer Live Grid / Read-Only Scientific Matrix -->
    <WorksheetEditor
      ref="editorRef"
      :key="sheet.id"
      :sheet="sheet"
      @saved="emit('saved', $event)"
      @editing="emit('editing', $event)"
      @results-saved="count => { savedReceiptSource = 'editor'; savedReceiptCount = count; }"
    />

    <section v-if="!editingSheet" class="panel matrix worksheet-matrix" aria-label="研究结果矩阵" tabindex="0">
      <table :style="{ minWidth: Math.max(1240, 430 + sheet.points.length * 140) + 'px' }">
        <thead>
          <tr>
            <th rowspan="2">检验项目<br>Test Item</th>
            <th rowspan="2">可接受标准<br>Acceptable Criteria</th>
            <th :colspan="sheet.points.length">考察时间 / Study timepoints</th>
          </tr>
          <tr>
            <th v-for="point in sheet.points" :key="point.id" class="template-option">
              {{ point.label }}<br>{{ point.planned_date }}<template v-if="point.signed_status"><br>{{ point.signed_status }}</template>
            </th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="row in sheet.rows" :key="row.id">
            <th scope="row" class="template-option">
              {{ row.label }}
              <p class="worksheet-criterion">{{ [row.method, row.reporting_basis ?? sheet.identity.reporting_basis].filter(Boolean).join(' · ') }}</p>
              <small v-if="row.unit">单位：{{ row.unit }}</small>
            </th>
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
              <span
                v-if="cells.get(row.id + ':' + point.id)?.adopted"
                :class="{ 'outside-limit': cells.get(row.id + ':' + point.id)?.standard_assessment?.status === 'outside' }"
                :title="cells.get(row.id + ':' + point.id)?.standard_assessment?.criterion"
              >
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
  </div>
</template>

<style scoped>
.worksheet-detail-view { display: grid; gap: 14px; }
.ws-gap-bar {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 8px;
  padding: 10px 14px;
  border-radius: var(--radius-md);
  background: var(--bg-surface);
  border: 1px solid var(--border);
  box-shadow: var(--shadow-card);
}
.ws-gap-label { font-size: 13px; font-weight: 600; color: var(--text-secondary); }
.ws-gap-chip {
  padding: 4px 12px;
  border-radius: 999px;
  font: inherit;
  font-size: 13px;
  font-weight: 600;
  cursor: pointer;
  border: 1px solid transparent;
}
.ws-gap-chip.warn { background: var(--warning-bg); border-color: var(--warning-border); color: var(--warning-fg); }
.ws-gap-chip.info { background: var(--brand-soft); border-color: var(--brand-border); color: var(--brand); }

.ws-tools-strip {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 8px;
  padding: 10px 14px;
  border-radius: var(--radius-md);
  background: var(--bg-surface);
  border: 1px solid var(--border);
}
.ws-tool-btn {
  padding: 6px 12px;
  border-radius: var(--radius-sm);
  border: 1px solid var(--border);
  background: var(--bg-subtle);
  color: var(--text-secondary);
  font: inherit;
  font-size: 13px;
  font-weight: 500;
  cursor: pointer;
  transition: all 0.15s ease;
}
.ws-tool-btn:hover { border-color: var(--brand-border); color: var(--brand); background: var(--brand-soft); }
.ws-tool-btn.active { border-color: var(--brand); background: var(--brand); color: #ffffff; font-weight: 600; }
.ws-tool-btn.highlight { border-color: var(--warning-border); background: var(--warning-bg); color: var(--warning-fg); font-weight: 600; }
.ws-tools-right { margin-left: auto; display: flex; align-items: center; gap: 12px; }
.ws-inline-jump { font-size: 13px; font-weight: 600; color: var(--brand); }

.outside-limit { color: var(--danger-fg); background: var(--danger-bg); font-weight: 700; padding: 1px 4px; border-radius: 4px; }
.worksheet-matrix { border: 1px solid var(--border); border-radius: var(--radius-md); padding: 0; margin-top: 0; }
.worksheet-matrix table { table-layout: fixed; min-width: 1240px; width: 100%; border-collapse: separate; border-spacing: 0; }
.worksheet-matrix th, .worksheet-matrix td { padding: 10px 12px; border-right: 1px solid var(--border); border-bottom: 1px solid var(--border); white-space: pre-wrap; overflow-wrap: anywhere; vertical-align: top; background: var(--bg-surface); }
.worksheet-matrix thead th { background: var(--bg-subtle); color: var(--text-secondary); font-weight: 600; }
.worksheet-matrix thead tr:first-child th:first-child { width: 205px; }
.worksheet-matrix thead tr:first-child th:nth-child(2) { width: 225px; }
.worksheet-matrix .template-option { background: var(--brand-soft); color: var(--text-primary); }
.worksheet-matrix details { font-size: 12px; margin-top: 6px; }
.worksheet-criterion { white-space: pre-wrap; margin: 4px 0 0; font-weight: normal; color: var(--text-secondary); font-size: 12.5px; }
.worksheet-notes { margin-top: 0; }
.worksheet-notes li { display: grid; grid-template-columns: minmax(0, 1fr); gap: 6px; overflow-wrap: anywhere; }
.worksheet-notes p { margin: 0; white-space: pre-wrap; }
</style>
