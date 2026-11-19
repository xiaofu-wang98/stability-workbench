<script setup lang="ts">
import { nextTick, onMounted, ref, watch } from 'vue';
import { useRoute } from 'vue-router';
import { NAlert, NButton, NInput, NSpin } from 'naive-ui';
import { api, type StandardAssessment } from './api';

const route = useRoute();
type Value = { text: string; result_version_id: string; source_ids: string[] };
type Cell = { slot_id: string; slot_key: { batch: string; item: string; method: string; condition: string;
  study: string; study_id: string; presentation: string; reporting_basis?: string; month: number | null; day: number | null };
  display: Value | null; adopted: Value | null; worksheet_id?: string | null; standard_assessment?: StandardAssessment | null };
const props = defineProps<{ project: string }>();
const cells = ref<Cell[]>([]);
const labels = ref<Record<string, string>>({});
const input = ref(String(route.query.batch || ''));
const batch = ref(String(route.query.batch || ''));
const sampleFilter = ref('');
type BatchOverview = { samples: { id: string; material_stage: string; sample_type: string; presentation: string; packaging: string; purpose: string }[];
  studies: { id: string; name: string; sample_id?: string; points: string[]; identity: { study_id: string; condition: string };
    protocol_draft_id?: string; standards: [string, string][] }[] };
const overview = ref<BatchOverview>();
const overviewError = ref('');
const selectedStudy = ref('');
const study = ref('');
const offset = ref(0);
const total = ref(0);
const busy = ref(false);
const error = ref('');
const refresh = ref(0);
type VoidImpact = { base_revision: number; value: string; studies: { id: string; name: string; release_reference: boolean }[] };
type VoidEvent = { sequence: number; action: string; reason: string; detail: { invalidated_value: string } };
const voidTarget = ref<{ project: string; version: string; key: string; impact: VoidImpact }>();
const voidReason = ref('');
const voidBusy = ref(false);
const voidMessage = ref('');
const voidError = ref('');
const voidHistory = ref<VoidEvent[]>();
const limit = 50;
const studies: Record<string, string> = { long_term: '长期', accelerated: '加速', stress: '强力破坏', release: '放行' };
const projectPath = () => '/projects/' + encodeURIComponent(props.project);
watch([() => props.project, batch], async (_, __, cleanup) => {
  const controller = new AbortController();
  cleanup(() => controller.abort());
  overview.value = undefined;
  overviewError.value = '';
  if (!batch.value) return;
  try {
    const data = await api<BatchOverview>(projectPath() + '/batch-overview?batch=' + encodeURIComponent(batch.value),
      { signal: controller.signal });
    if (!controller.signal.aborted) overview.value = data;
  } catch (e) {
    if (!controller.signal.aborted) overviewError.value = (e as Error).message;
  }
});

watch(() => props.project, async (_, __, cleanup) => {
  const controller = new AbortController();
  cleanup(() => controller.abort());
  labels.value = {};
  try {
    const response = await api<{ config: { items: { id: string; label: string }[] } }>(
      projectPath() + '/configuration', { signal: controller.signal });
    if (!controller.signal.aborted) labels.value = Object.fromEntries(response.config.items.map(item => [item.id, item.label]));
  } catch { /* Stable item IDs remain available if display labels cannot be read. */ }
}, { immediate: true });

watch([() => props.project, batch, study, offset, sampleFilter, refresh], async (_, __, cleanup) => {
  const controller = new AbortController();
  cleanup(() => controller.abort());
  busy.value = true;
  error.value = '';
  try {
    const query = new URLSearchParams({ offset: String(offset.value), limit: String(limit) });
    if (batch.value) query.set('batch', batch.value);
    if (sampleFilter.value) query.set('sample_id', sampleFilter.value);
    if (study.value) query.set('study', study.value);
    const result = await api<{ cells: Cell[]; total: number }>(projectPath() + '/result-cells?' + query, { signal: controller.signal });
    if (!controller.signal.aborted) {
      cells.value = result.cells;
      total.value = result.total;
    }
  } catch (e) {
    if (!controller.signal.aborted) error.value = (e as Error).message;
  } finally {
    if (!controller.signal.aborted) busy.value = false;
  }
}, { immediate: true });

watch(() => props.project, () => {
  voidTarget.value = undefined;
  voidHistory.value = undefined;
  voidMessage.value = '';
  voidError.value = '';
});

async function startVoid(version: string) {
  const project = props.project;
  voidBusy.value = true;
  voidError.value = '';
  voidMessage.value = '';
  voidTarget.value = undefined;
  try {
    const impact = await api<VoidImpact>('/results/' + encodeURIComponent(version) + '/invalidate');
    if (props.project !== project) return;
    voidTarget.value = { project, version, key: crypto.randomUUID(), impact };
    voidReason.value = '';
  } catch (e) {
    if (props.project === project) voidError.value = (e as Error).message;
  } finally { voidBusy.value = false; }
}

async function confirmVoid() {
  const target = voidTarget.value;
  const reason = voidReason.value.trim();
  if (!target || !reason || voidBusy.value || target.project !== props.project) return;
  voidBusy.value = true;
  voidError.value = '';
  try {
    await api('/results/' + encodeURIComponent(target.version) + '/invalidate', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ base_revision: target.impact.base_revision, idempotency_key: target.key, reason }),
    });
    if (props.project !== target.project) return;
    voidTarget.value = undefined;
    voidHistory.value = undefined;
    voidMessage.value = '结果已作废，历史记录已保留。';
    if (offset.value) offset.value = 0;
    else refresh.value++;
  } catch (e) {
    if (props.project === target.project) voidError.value = (e as Error).message;
  } finally { voidBusy.value = false; }
}

async function loadVoidHistory() {
  const project = props.project;
  voidError.value = '';
  try {
    const events = await api<VoidEvent[]>(projectPath() + '/history');
    if (props.project === project) voidHistory.value = events.filter(event => event.action === 'invalidate_result');
  } catch (e) {
    if (props.project === project) voidError.value = (e as Error).message;
  }
}

function search() {
  offset.value = 0;
  sampleFilter.value = '';
  batch.value = input.value.trim();
  study.value = selectedStudy.value;
}

function scrollToResults() {
  if (route.query.focus === 'results') {
    nextTick(() => {
      document.getElementById('project-results-view')?.scrollIntoView({ behavior: 'smooth' });
    });
  }
}

onMounted(() => {
  scrollToResults();
});

watch(() => route.query.focus, () => {
  scrollToResults();
});
</script>

<template>
  <section id="project-results-view" class="panel">
    <h2>检测结果</h2>
    <form class="result-controls" @submit.prevent="search">
      <NInput v-model:value="input" aria-label="结果批号" placeholder="输入批号筛选" clearable />
      <label>考察类型 <select v-model="selectedStudy">
        <option value="">全部</option>
        <option v-for="(label, key) in studies" :key="key" :value="key">{{ label }}</option>
      </select></label>
      <NButton attr-type="submit">{{ input.trim() ? '按批号查看' : '筛选' }}</NButton>
      <RouterLink :to="{ path: '/trends', query: { project, batch: batch || undefined, study: study || undefined } }">查看趋势</RouterLink>
      <RouterLink :to="{ path: '/deliveries', query: { project, batch: batch || undefined, study: study || undefined } }">生成文件</RouterLink>
      <RouterLink :to="{ path: '/deliveries', query: { project, batch: batch || undefined, format: 'confirmation' } }">生成确认表</RouterLink>
    </form>
    <NButton text @click="loadVoidHistory">查看作废记录</NButton>
    <ul v-if="voidHistory?.length" class="list">
      <li v-for="event in voidHistory" :key="event.sequence">原值 {{ event.detail.invalidated_value }} · {{ event.reason }}</li>
    </ul>
    <p v-else-if="voidHistory">暂无作废记录。</p>
    <NAlert v-if="voidError" type="error" role="alert">{{ voidError }}</NAlert>
    <p v-if="voidMessage" role="status">{{ voidMessage }}</p>
    <form v-if="voidTarget" class="panel" @submit.prevent="confirmVoid">
      <h3>留痕作废</h3>
      <p>原记录值 {{ voidTarget.impact.value }}。作废后将取消此值的采用，保留原件与历史。</p>
      <ul v-if="voidTarget.impact.studies.length">
        <li v-for="item in voidTarget.impact.studies" :key="item.id">{{ item.name }}{{ item.release_reference ? ' · 放行引用将失效' : '' }}</li>
      </ul>
      <label>作废原因 <textarea v-model="voidReason" required :disabled="voidBusy"></textarea></label>
      <div class="entry-actions">
        <NButton type="error" attr-type="submit" :disabled="!voidReason.trim() || voidBusy">确认作废</NButton>
        <NButton :disabled="voidBusy" @click="voidTarget = undefined">取消</NButton>
      </div>
    </form>
    <NAlert v-if="overviewError" type="error">{{ overviewError }}</NAlert>
    <div v-if="overview">
      <h3>批号 {{ batch }}</h3>
      <ul v-if="overview.samples.length" class="list">
        <li v-for="sample in overview.samples" :key="sample.id">
          {{ [sample.material_stage, sample.sample_type, sample.presentation, sample.packaging, sample.purpose].filter(Boolean).join(' · ') }}
          <RouterLink :to="{ path: '/new', query: { project, batch, sample: sample.id } }">为此样品新建研究</RouterLink>
          <RouterLink :to="{ path: '/import', query: { project, batch, sample: sample.id } }">为此样品上传补充数据</RouterLink>
          <NButton text :aria-pressed="sampleFilter === sample.id" @click="offset = 0; sampleFilter = sampleFilter === sample.id ? '' : sample.id">
            {{ sampleFilter === sample.id ? '查看批号全部结果' : '查看此样品结果' }}
          </NButton>
        </li>
      </ul>
      <p v-else>该批号尚未登记样品对象。</p>
      <ul v-if="overview.studies.length" class="list">
        <li v-for="item in overview.studies" :key="item.id">
          <RouterLink :to="'/worksheets/' + item.id">{{ item.name }}</RouterLink>
          <span>{{ [item.identity.study_id, item.identity.condition, item.points.join('、')].filter(Boolean).join(' · ') }}</span>
          <a v-if="item.protocol_draft_id" :href="'/api/v1/plan-drafts/' + item.protocol_draft_id + '/file'">原方案</a>
          <a v-for="[id, filename] in item.standards" :key="id" :href="'/api/v1/specifications/' + id + '/file'">{{ filename }}</a>
        </li>
      </ul>
      <RouterLink :to="{ path: '/import', query: { project, batch } }">上传补充数据</RouterLink>
      <RouterLink v-if="!overview.samples.length" :to="{ path: '/new', query: { project, batch } }">新建研究表</RouterLink>
    </div>
    <NAlert v-if="error" type="error">{{ error }}</NAlert>
    <NSpin v-else-if="busy" />
    <p v-else-if="!total" class="muted">{{ batch || study ? '当前筛选范围没有已提交结果。' : '此项目还没有已提交结果。' }}</p>
    <template v-else>
      <p role="status">共 {{ total }} 条结果</p>
      <div class="matrix"><table>
        <thead><tr><th>批号／规格</th><th>考察／条件</th><th>检项／方法</th><th>时间点</th><th>当前采用值</th><th>最新记录</th><th>来源</th></tr></thead>
        <tbody><tr v-for="cell in cells" :key="cell.slot_id">
          <th scope="row">{{ cell.slot_key.batch }}<br>{{ cell.slot_key.presentation }}</th>
          <td>{{ studies[cell.slot_key.study] || cell.slot_key.study }} · {{ cell.slot_key.condition }}<br>{{ cell.slot_key.study_id }}<template v-if="cell.slot_key.reporting_basis"> · {{ cell.slot_key.reporting_basis }}</template></td>
          <td>{{ labels[cell.slot_key.item] || cell.slot_key.item }}<br>{{ cell.slot_key.method }}</td>
          <td>{{ cell.slot_key.day != null ? cell.slot_key.day + 'D' : cell.slot_key.month != null ? cell.slot_key.month + 'M' : '放行' }}</td>
          <td><span :class="{ 'outside-limit': cell.standard_assessment?.status === 'outside' }"
            :title="[cell.standard_assessment?.criterion, cell.standard_assessment?.version].filter(Boolean).join(' · ')">
            {{ cell.adopted?.text ?? '尚无采用值' }}
            <small v-if="cell.standard_assessment?.status === 'outside'"> 超出标准</small>
            <small v-if="cell.standard_assessment?.status === 'undetermined'"> 待判断</small>
          </span>
            <NButton v-if="cell.adopted" text :disabled="voidBusy" @click="startVoid(cell.adopted.result_version_id)">留痕作废</NButton>
          </td>
          <td>{{ cell.display?.result_version_id !== cell.adopted?.result_version_id ? cell.display?.text : '—' }}</td>
          <td><a v-for="(id, index) in (cell.adopted || cell.display)?.source_ids" :key="id"
            :href="'/api/v1/sources/' + encodeURIComponent(id) + '/file'">原件 {{ index + 1 }} </a>
            <RouterLink v-if="cell.worksheet_id" :to="'/worksheets/' + encodeURIComponent(cell.worksheet_id)">打开研究表</RouterLink>
            <RouterLink v-else-if="cell.adopted && cell.slot_key.study !== 'release'" :to="{ path: '/new', query: {
              project, batch: cell.slot_key.batch, slot: cell.slot_id } }">按结果建研究表</RouterLink></td>
        </tr></tbody>
      </table></div>
      <div v-if="total > limit" class="result-controls">
        <NButton :disabled="!offset" @click="offset -= limit">上一页</NButton>
        <span>{{ offset + 1 }}–{{ Math.min(offset + limit, total) }}</span>
        <NButton :disabled="offset + limit >= total" @click="offset += limit">下一页</NButton>
      </div>
    </template>
  </section>
</template>

<style scoped>
.outside-limit { color: #9d2424; background: #fde8e8; font-weight: 700; }
.result-controls { display: flex; flex-wrap: wrap; align-items: center; gap: 12px; margin: 10px 0; }
.result-controls .n-input { width: min(100%, 260px); }
</style>
