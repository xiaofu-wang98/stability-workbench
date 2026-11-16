<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue';
import { NAlert, NButton, NSelect } from 'naive-ui';
import { api, type Worksheet } from './api';

const props = defineProps<{ sheet: Worksheet; busy: boolean }>();
const emit = defineEmits<{ save: [object]; cancel: [] }>();
type Matrix = { table: number; caption: string; study: string | null; warnings: string[];
  conditions: { text: string }[]; points: { amount: number; unit: string }[]; items: { row: number; label: string }[] };
const sources = ref<{ id: string; filename: string }[]>([]);
const source = ref<string | null>(null);
const selected = ref<number | null>(null);
const matrices = ref<Matrix[]>([]);
const error = ref('');
const basis = ref('');
const loading = ref(false);
const addMonths = ref(false);
const mapping = ref<Record<number, string[]>>({});
const matrix = computed(() => matrices.value.find(item => item.table === selected.value));
watch(matrix, () => { mapping.value = {}; addMonths.value = false; });
function setMapping(row: number, values: string[]) {
  mapping.value[row] = values.includes(':exclude') ? [':exclude'] : values;
}
const differences = computed(() => {
  const current = props.sheet.points.map(p => p.label);
  const proposed = matrix.value?.points.map(p => p.amount + p.unit) ?? [];
  return { added: proposed.filter(p => !current.includes(p)), remaining: current.filter(p => !proposed.includes(p)) };
});
onMounted(async () => {
  try { sources.value = await api('/plan-drafts'); }
  catch (e) { error.value = (e as Error).message; }
});
watch(source, async (id, _, cleanup) => {
  const request = new AbortController();
  cleanup(() => request.abort());
  selected.value = null;
  matrices.value = [];
  error.value = '';
  loading.value = Boolean(id);
  if (!id) return;
  try {
    const result = await api<{ matrices: Matrix[] }>('/plan-drafts/' + encodeURIComponent(id), { signal: request.signal });
    if (!request.signal.aborted) matrices.value = result.matrices;
  } catch (e) {
    if (!request.signal.aborted) error.value = (e as Error).message;
  } finally {
    if (!request.signal.aborted) loading.value = false;
  }
});
function save() {
  if (matrix.value && source.value && basis.value.trim()) {
    const row_mapping = Object.fromEntries(matrix.value.items.map(item => [item.row,
      (mapping.value[item.row] || []).filter(id => id !== ':exclude')]));
    emit('save', { protocol_draft_id: source.value, protocol_table: matrix.value.table, basis: basis.value.trim(),
      ...(addMonths.value ? { row_mapping, add_months: true } : {}) });
  }
}
</script>

<template>
  <form class="protocol-form" @submit.prevent="save">
    <h2>后补研究方案</h2>
    <NAlert v-if="error" type="error">{{ error }}</NAlert>
    <NSelect v-model:value="source" placeholder="选择已导入的方案" :disabled="busy"
      :options="sources.map(item => ({ value: item.id, label: item.filename }))" />
    <NSelect v-model:value="selected" placeholder="选择方案矩阵" :disabled="busy || !source" :loading="loading"
      :options="matrices.map(item => ({ value: item.table, label: item.caption || '原件表 ' + item.table,
        disabled: Boolean(item.study && item.study !== sheet.identity.study) }))" />
    <template v-if="matrix">
      <a :href="'/api/v1/plan-drafts/' + source + '/file'">下载方案原件</a>
      <p>方案新增时间点 {{ differences.added.join('、') || '无' }}；仅在当前表中的时间点 {{ differences.remaining.join('、') || '无' }}。</p>
      <label v-if="differences.added.length"><input v-model="addMonths" type="checkbox" :disabled="busy">同时补入新增时间点</label>
      <template v-if="addMonths">
        <label v-for="item in matrix.items" :key="item.row">{{ item.label || '原方案行 ' + item.row }}
          <NSelect :value="mapping[item.row] || []" multiple filterable placeholder="选择本研究检项或不纳入" :disabled="busy"
            :options="[{ value: ':exclude', label: '不纳入' }, ...sheet.rows.map(row =>
              ({ value: row.plan_key ?? row.item_id, label: [row.label, row.method, row.reporting_basis].filter(Boolean).join(' · ') }))]"
            @update:value="setMapping(item.row, $event)" />
        </label>
      </template>
      <ul v-if="matrix.warnings.length"><li v-for="warning in matrix.warnings" :key="warning">{{ warning }}</li></ul>
      <details><summary>查看方案条件与检项</summary>
        <p v-for="(condition, index) in matrix.conditions" :key="index">{{ condition.text }}</p>
        <ul><li v-for="item in matrix.items" :key="item.row">{{ item.label || '待核对' }}</li></ul>
      </details>
      <label>条件和检项核对依据 <input v-model="basis" required :disabled="busy"></label>
    </template>
    <div class="protocol-actions">
      <NButton attr-type="submit" type="primary" :disabled="busy || !matrix || !basis.trim() || (addMonths && matrix.items.some(item => !mapping[item.row]?.length))">确认关联方案</NButton>
      <NButton :disabled="busy" @click="emit('cancel')">取消</NButton>
    </div>
  </form>
</template>

<style scoped>
.protocol-form { display: grid; gap: 9px; max-width: 750px; padding: 14px; margin: 12px 0; background: white; }
.protocol-actions { display: flex; gap: 8px; }
</style>
