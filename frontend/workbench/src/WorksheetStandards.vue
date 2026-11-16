<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue';
import { NAlert, NButton, NSelect } from 'naive-ui';
import { api, type Worksheet } from './api';

const props = defineProps<{ sheet: Worksheet; busy: boolean }>();
const emit = defineEmits<{ save: [{ row_id: string; basis: string; criteria_text: string; criteria: { specification_id: string; table: number; row: number; lower?: number | null; upper?: number | null }[] }]; remove: [{ row_id: string; basis: string }]; stop: [{ row_id: string; point_ids: string[]; basis: string }]; confirmProtocol: [{ basis: string; fingerprint: string }]; cancel: [] }>();
type ProtocolPreview = { fingerprint: string; additions: { row_id: string; label: string; standard: {
  specification_id: string; table: number; row: number; filename: string; criterion: string;
} }[]; issues: { number: string; message: string }[] };
const protocolPreview = ref<ProtocolPreview>();
const checkingProtocol = ref(false);
async function checkProtocol() {
  checkingProtocol.value = true;
  error.value = '';
  protocolPreview.value = undefined;
  try { protocolPreview.value = await api('/worksheets/' + encodeURIComponent(props.sheet.id) + '/protocol-standards'); }
  catch (e) { error.value = (e as Error).message; }
  finally { checkingProtocol.value = false; }
}
const row = ref<string | null>(null);
const stopPoints = ref<string[]>([]);
const eligibleStopPoints = computed(() => {
  const id = row.value;
  if (!id) return [];
  const today = new Date().toLocaleDateString('sv-SE');
  return props.sheet.points.filter(point => point.planned_date >= today && point.required_row_ids.includes(id)
    && !props.sheet.cells.some(cell => cell.row_id === id && cell.point_id === point.id)
    && !(point.label === '0M' && (props.sheet.pending_release_references?.[id] || props.sheet.release_references?.[id])));
});
const source = ref<string | null>(null);
const sources = ref<{ id: string; filename: string }[]>([]);
const options = ref<{ table: number; row: number; criterion: string; method: string; item: string }[]>([]);
const scope = ref('');
const selected = ref<string[]>([]);
const links = ref<Worksheet['rows'][number]['standards']>([]);
const basis = ref('');
const criteriaText = ref('');
const lower = ref('');
const upper = ref('');
const confirmed = ref(false);
const unit = computed(() => props.sheet.rows.find(item => item.id === row.value)?.unit || '');
const error = ref('');
const loading = ref(false);

onMounted(async () => {
  try { sources.value = await api('/specifications'); }
  catch (e) { error.value = (e as Error).message; }
});
watch(row, value => {
  stopPoints.value = [];
  criteriaText.value = props.sheet.rows.find(item => item.id === value)?.criteria_text ?? '';
  links.value = [...(props.sheet.rows.find(item => item.id === value)?.standards ?? [])];
  selected.value = [];
  lower.value = '';
  upper.value = '';
  confirmed.value = false;
});
watch(source, async (id, _, cleanup) => {
  const controller = new AbortController();
  cleanup(() => controller.abort());
  options.value = [];
  selected.value = [];
  scope.value = '';
  error.value = '';
  loading.value = Boolean(id);
  if (!id) return;
  try {
    const result = await api<{ rows: typeof options.value; scope: string }>('/specifications/' + id, { signal: controller.signal });
    options.value = result.rows;
    scope.value = result.scope;
  } catch (e) {
    if (!controller.signal.aborted) error.value = (e as Error).message;
  } finally {
    if (!controller.signal.aborted) loading.value = false;
  }
});
function key(item: { table: number; row: number }) { return item.table + ':' + item.row; }
function save() {
  if (!row.value || !basis.value.trim()) return;
  const criteria = new Map();
  for (const link of links.value ?? []) criteria.set(link.specification_id + ':' + key(link), link);
  for (const item of options.value) {
    if (source.value && selected.value.includes(key(item))) {
      criteria.set(source.value + ':' + key(item), { specification_id: source.value, table: item.table, row: item.row });
    }
  }
  const explicit = confirmed.value;
  const low = lower.value === '' ? null : Number(lower.value);
  const high = upper.value === '' ? null : Number(upper.value);
  if (explicit && (criteria.size !== 1 || !unit.value || [low, high].some(value => value !== null && !Number.isFinite(value)))) {
    error.value = '设置标准线时，请仅关联一条适用标准，并以当前检项单位填写有效数字。';
    return;
  }
  emit('save', { row_id: row.value, basis: basis.value.trim(), criteria_text: criteriaText.value, criteria: Array.from(criteria.values(), item => ({
    specification_id: item.specification_id, table: item.table, row: item.row,
    lower: explicit ? low : item.plot_limits?.lower ?? null,
    upper: explicit ? high : item.plot_limits?.upper ?? null,
  })) });
}
</script>

<template>
  <form class="standard-links" @submit.prevent="save">
    <h2>编辑可接受标准与引用</h2>
    <NAlert v-if="error" type="error">{{ error }}</NAlert>
    <div v-if="sheet.protocol_draft_id">
      <NButton :disabled="busy" :loading="checkingProtocol" @click="checkProtocol">核对方案引用标准</NButton>
      <template v-if="protocolPreview">
        <ul v-if="protocolPreview.issues.length"><li v-for="issue in protocolPreview.issues" :key="issue.number + issue.message">
          {{ issue.number }} · {{ issue.message }}
        </li></ul>
        <p v-if="!protocolPreview.additions.length">本次没有可自动匹配的新增条款。</p>
        <ul v-else><li v-for="item in protocolPreview.additions" :key="[item.row_id, item.standard.specification_id, item.standard.table, item.standard.row].join(':')">
          {{ item.label }} · {{ item.standard.criterion }} · {{ item.standard.filename }}
        </li></ul>
        <NButton v-if="protocolPreview.additions.length" :disabled="busy || !basis.trim()"
          @click="emit('confirmProtocol', { basis: basis.trim(), fingerprint: protocolPreview.fingerprint })">
          确认关联 {{ protocolPreview.additions.length }} 条标准
        </NButton>
      </template>
    </div>
    <NSelect v-model:value="row" placeholder="选择研究表检项" :disabled="busy"
      :options="sheet.rows.map(item => ({ value: item.id, label: item.label + ' · ' + item.method }))" />
    <ul v-if="links?.length">
      <li v-for="(link, index) in links" :key="link.specification_id + ':' + key(link)">
        {{ link.criterion }} · {{ link.filename }}
        <span v-if="link.plot_limits"> · 标准线 {{ link.plot_limits.lower ?? '—' }} 至 {{ link.plot_limits.upper ?? '—' }} {{ link.plot_limits.unit }}</span>
        <NButton text :disabled="busy" @click="links?.splice(index, 1)">移除关联</NButton>
      </li>
    </ul>
    <NSelect v-model:value="source" placeholder="选择标准原件" :disabled="busy || !row"
      :options="sources.map(item => ({ value: item.id, label: item.filename }))" />
    <p v-if="scope">{{ scope }}</p>
    <NSelect v-if="source" v-model:value="selected" multiple filterable :loading="loading" :disabled="busy"
      placeholder="选择适用条款" :options="options.map(item => ({ value: key(item), label: [item.item, item.method, item.criterion].join(' · ') }))" />
    <label>可接受标准（人工补充）<textarea v-model="criteriaText" maxlength="4000" rows="3" :disabled="busy || !row" placeholder="可填写或更正本行标准；引用条款原文保留在上方。"></textarea></label>
    <label>适用或修改依据 <input v-model="basis" required :disabled="busy"></label>
    <label v-if="unit"><input v-model="confirmed" type="checkbox" :disabled="busy">按当前单位 {{ unit }} 确认设置或清除标准线</label>
    <div v-if="confirmed" class="buttons">
      <label>下限 <input v-model="lower" type="number" step="any" :disabled="busy"></label>
      <label>上限 <input v-model="upper" type="number" step="any" :disabled="busy"></label>
      <span>两项留空会清除标准线。</span>
    </div>
    <p>未产生检测记录或放行引用的检项可移除，系统保留旧计划。需保留检项时，可以仅停检选定的后续时间点。</p>
    <details v-if="eligibleStopPoints.length">
      <summary>调整后续应检安排</summary>
      <label v-for="point in eligibleStopPoints" :key="point.id">
        <input type="checkbox" v-model="stopPoints" :value="point.id" :disabled="busy"> {{ point.label }} · {{ point.planned_date }}
      </label>
      <NButton :disabled="busy || !row || !stopPoints.length || !basis.trim()"
        @click="row && emit('stop', { row_id: row, point_ids: stopPoints, basis: basis.trim() })">确认停检所选时间点</NButton>
    </details>
    <div class="buttons">
      <NButton type="error" :disabled="busy || !row || !basis.trim() || sheet.rows.length < 2" @click="row && emit('remove', { row_id: row, basis: basis.trim() })">移除此检项（保留旧计划）</NButton>
      <NButton attr-type="submit" type="primary" :loading="busy" :disabled="busy || !row || !basis.trim()">保存标准关联</NButton>
      <NButton :disabled="busy" @click="emit('cancel')">取消</NButton>
    </div>
  </form>
</template>

<style scoped>
.standard-links { display: grid; gap: 10px; max-width: 850px; background: white; padding: 14px; margin: 12px 0; }
.buttons { display: flex; gap: 8px; }
</style>
