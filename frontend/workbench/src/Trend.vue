<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, ref, watch } from 'vue';
import { NAlert, NButton, NSelect } from 'naive-ui';
import { Chart } from 'chart.js/auto';
import { api } from './api';

const props = defineProps<{ project: string; batch?: string; study?: string }>();
type Group = { id: string; label: string; batches: string[]; key: { method: string; condition: string;
  study: string; study_id: string; presentation: string; reporting_basis: string }; time_unit: string };
type Limits = { lower: number | null; upper: number | null; unit: string };
type TrendData = { axis: { min: number; max: number }; series: { batch: string; values: { x: number; value: string | null; slot_id: string | null }[] }[];
  omitted: unknown[]; batches: string[]; standard_limits?: Limits | null };
const groups = ref<Group[]>([]);
const selected = ref<string | null>(null);
const activeGroup = computed(() => groups.value.find(group => group.id === selected.value));
const batches = ref<string[]>([]);
const plottedSlots = ref<string[]>([]);
const error = ref('');
const loading = ref(false);
const omitted = ref<number>();
const exporting = ref(false);
const imageUrl = ref('');
const ready = ref(false);
const standard = ref<Limits | null>(null);
const canvas = ref<HTMLCanvasElement>();
let revision = 0;
let chart: Chart | undefined;
function clearChart() {
  chart?.destroy();
  chart = undefined;
  omitted.value = undefined;
  imageUrl.value = '';
  ready.value = false;
  standard.value = null;
  plottedSlots.value = [];
}
onBeforeUnmount(clearChart);
watch([() => props.project, () => props.batch, () => props.study], async ([project, batch, study], _, cleanup) => {
  const controller = new AbortController();
  cleanup(() => controller.abort());
  selected.value = null;
  batches.value = [];
  groups.value = [];
  error.value = '';
  loading.value = true;
  try {
    const query = new URLSearchParams();
    if (batch) query.set('batch', batch);
    if (study) query.set('study', study);
    const data = await api<{ base_revision: number; analysis_groups: Group[] }>('/projects/' + encodeURIComponent(project) + '/result-display?' + query, { signal: controller.signal });
    revision = data.base_revision;
    groups.value = data.analysis_groups;
  } catch (e) {
    if (!controller.signal.aborted) error.value = (e as Error).message;
  } finally {
    if (!controller.signal.aborted) loading.value = false;
  }
}, { immediate: true });

function selectGroup(id: string | null) {
  selected.value = id;
  batches.value = [...(activeGroup.value?.batches ?? [])];
}
watch([selected, batches], async ([id, selection], _, cleanup) => {
  const controller = new AbortController();
  cleanup(() => controller.abort());
  clearChart();
  error.value = '';
  const group = activeGroup.value;
  if (!group || group.id !== id || !selection.length) return;
  const query = new URLSearchParams({ group_id: group.id, base_revision: String(revision) });
  if (props.batch) query.set('batch', props.batch);
  if (props.study) query.set('study', props.study);
  selection.forEach(batch => query.append('batches', batch));
  try {
    const data = await api<TrendData>('/projects/' + encodeURIComponent(props.project) + '/result-trend?' + query, { signal: controller.signal });
    if (controller.signal.aborted) return;
    omitted.value = data.omitted.length;
    await nextTick();
    if (!canvas.value || controller.signal.aborted) return;
    const release = group.time_unit === 'release';
    const limits = data.standard_limits;
    standard.value = limits ?? null;
    chart = new Chart(canvas.value, {
      type: release ? 'bar' : 'line',
      data: { datasets: data.series.map(series => ({
        label: series.batch,
        data: series.values.map(p => ({ x: p.x, y: p.value === null ? null : Number(p.value) })),
        spanGaps: false,
      })) },
      plugins: [{
        id: 'worksheet-standard-limits',
        afterDatasetsDraw(instance) {
          if (!limits) return;
          const { ctx, chartArea, scales } = instance;
          ctx.save();
          ctx.strokeStyle = '#a74b30';
          ctx.setLineDash([6, 4]);
          for (const value of [limits.lower, limits.upper]) {
            if (value === null) continue;
            const y = scales.y.getPixelForValue(value);
            ctx.beginPath();
            ctx.moveTo(chartArea.left, y);
            ctx.lineTo(chartArea.right, y);
            ctx.stroke();
          }
          ctx.restore();
        },
      }],
      options: { scales: {
        x: { type: 'linear', ticks: {
          precision: 0, callback: value => release ? (data.batches[Number(value)] || '') : String(value) + group.time_unit,
        } },
        y: { min: data.axis.min, max: data.axis.max },
      } },
    });
    plottedSlots.value = [...new Set(data.series.flatMap(series =>
      series.values.flatMap(point => point.slot_id ? [point.slot_id] : [])))];
    ready.value = true;
  } catch (e) {
    if (!controller.signal.aborted) error.value = (e as Error).message;
  }
});

async function exportImage() {
  const group = groups.value.find(item => item.id === selected.value);
  if (!group || !ready.value || !plottedSlots.value.length || exporting.value) return;
  const project = props.project;
  const slots = plottedSlots.value;
  exporting.value = true;
  error.value = '';
  try {
    const result = await api<{ image_download_url: string }>('/exports', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ project, base_revision: revision, slot_ids: slots, include_png: true }),
    });
    if (props.project === project && selected.value === group.id && plottedSlots.value === slots) imageUrl.value = result.image_download_url;
  } catch (e) {
    error.value = (e as Error).message;
  } finally {
    exporting.value = false;
  }
}
</script>

<template>
  <div class="trend">
    <NAlert v-if="error" type="error">{{ error }}</NAlert>
    <NSelect :value="selected" :loading="loading" filterable placeholder="选择检项与方法" @update:value="selectGroup"
      :options="groups.map(g => ({ value: g.id, label: [g.label, g.key.method, g.key.condition, g.key.study, g.key.presentation, g.key.study_id, g.key.reporting_basis].filter(Boolean).join(' · ') }))" />
    <label v-if="activeGroup" class="batch-choice">对比批次
      <NSelect v-model:value="batches" multiple filterable placeholder="选择一个或多个批次"
        :options="activeGroup.batches.map(batch => ({ value: batch, label: batch }))" />
    </label>
    <p v-if="activeGroup && !batches.length">请选择至少一个批次。</p>
    <p v-if="!loading && !groups.length">此项目暂无结果。</p>
    <p v-if="omitted !== undefined">未绘制 {{ omitted }} 个结果</p>
    <p v-if="standard">标准线：<span v-if="standard.lower !== null">下限 {{ standard.lower }} {{ standard.unit }}　</span><span v-if="standard.upper !== null">上限 {{ standard.upper }} {{ standard.unit }}</span></p>
    <div v-if="ready" class="trend-action-bar">
      <NButton :loading="exporting" type="primary" size="small" @click="exportImage">生成趋势 PNG</NButton>
      <a v-if="imageUrl" :href="imageUrl" class="trend-dl-link">下载趋势 PNG</a>
      <RouterLink class="trend-next-link" :to="{ path: '/deliveries', query: { project: props.project, format: 'quick' } }">前往生成跨批对比 Word →</RouterLink>
      <RouterLink class="trend-next-link" :to="{ path: '/deliveries', query: { project: props.project, batch: batches.length === 1 ? batches[0] : undefined, format: 'stability' } }">前往生成稳定性考察报告 →</RouterLink>
    </div>
    <canvas v-show="omitted !== undefined" ref="canvas" aria-label="结果趋势图" role="img" />
  </div>
</template>

<style scoped>
.trend { margin-top: 14px; max-width: 1120px; }
.batch-choice { display: grid; gap: 5px; margin-top: 12px; }
.trend-action-bar { display: flex; align-items: center; flex-wrap: wrap; gap: 12px; margin: 12px 0; }
.trend-dl-link { font-weight: 600; color: var(--success-fg); }
.trend-next-link { font-size: 13.5px; font-weight: 600; color: var(--brand); padding: 4px 10px; border-radius: var(--radius-sm); border: 1px solid var(--brand-border); background: var(--brand-soft); }
.trend-next-link:hover { text-decoration: none; background: var(--brand); color: #ffffff; }
</style>
