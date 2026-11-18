<script setup lang="ts">
import { computed, ref, watch } from 'vue';
import { NAlert, NButton, NSelect } from 'naive-ui';
import { api, type Worksheet } from './api';

const props = defineProps<{ sheet: Worksheet; busy: boolean }>();
const emit = defineEmits<{ save: [object]; copy: [object]; cancel: [] }>();
const row = ref<string | null>(null);
const chosen = ref<string | null>(null);
const date = ref('');
const basis = ref('');
const loading = ref(false);
const error = ref('');
type Choice = { value: string; label: string; coa_specification_id?: string; coa_version?: number; fingerprint?: string };
const intent = ref<'correction' | 'retest' | 'keep'>('keep');
const selected = computed(() => choices.value.find(item => item.value === chosen.value));
const adopted = computed(() => props.sheet.cells.find(cell => cell.row_id === row.value &&
  props.sheet.points.some(point => point.id === cell.point_id && point.label === '0M'))?.adopted);
const choices = ref<Choice[]>([]);
const rows = computed(() => props.sheet.rows.filter(item => props.sheet.points.some(
  point => point.label === '0M' && point.required_row_ids.includes(item.id))));
const previous = computed(() => row.value ? props.sheet.release_references?.[row.value] : undefined);
const pending = computed(() => row.value ? props.sheet.pending_release_references?.[row.value] : undefined);
watch(row, async (id, _, cleanup) => {
  const controller = new AbortController();
  cleanup(() => controller.abort());
  intent.value = 'keep';
  choices.value = [];
  chosen.value = null;
  date.value = '';
  error.value = '';
  loading.value = Boolean(id);
  const item = rows.value.find(item => item.id === id);
  if (!item) return;
  try {
    const query = new URLSearchParams({ study: 'release', batch: props.sheet.identity.batch,
      item: item.item_id, method: item.method, limit: '1000' });
    const result = await api<{ cells: { slot_key: { presentation: string; study_id: string };
      adopted: { text: string; result_version_id: string } | null }[] }>(
      '/projects/' + encodeURIComponent(props.sheet.project) + '/result-cells?' + query, { signal: controller.signal });
    const coa = await api<{ specification_id: string; version: number; filename: string; value: string; fingerprint: string }[]>(
      '/worksheets/' + encodeURIComponent(props.sheet.id) + '/coa-release-options?row_id=' + encodeURIComponent(item.id),
      { signal: controller.signal });
    if (controller.signal.aborted) return;
    choices.value = [...result.cells.flatMap(cell => cell.adopted ? [{
      value: cell.adopted.result_version_id,
      label: [cell.adopted.text, cell.slot_key.presentation, cell.slot_key.study_id].filter(Boolean).join(' · '),
    }] : []), ...coa.map(option => ({
      value: option.fingerprint, fingerprint: option.fingerprint,
      label: [option.value, option.filename, 'COA 版本 ' + option.version].join(' · '),
      coa_specification_id: option.specification_id, coa_version: option.version,
    }))];
  } catch (e) {
    if (!controller.signal.aborted) error.value = (e as Error).message;
  } finally {
    if (!controller.signal.aborted) loading.value = false;
  }
});
function copy() {
  if (!row.value || !selected.value?.fingerprint) return;
  emit('copy', { row_id: row.value, coa_fingerprint: selected.value.fingerprint, basis: basis.value.trim(),
    expected_version_id: adopted.value?.result_version_id ?? null, intent: adopted.value ? intent.value : 'initial' });
}
function save(remove = false) {
  if (!row.value || !basis.value.trim() || (!remove && (!chosen.value || !date.value))) return;
  const option = choices.value.find(item => item.value === chosen.value);
  emit('save', { row_id: row.value, basis: basis.value.trim(),
    ...(!remove && option?.coa_specification_id ? {
      coa_specification_id: option.coa_specification_id, coa_version: option.coa_version,
    } : { result_version_id: remove ? null : chosen.value }), release_completed: remove ? null : date.value });
}
</script>

<template>
  <form class="release-form" @submit.prevent="selected?.fingerprint ? copy() : save()">
    <h2>从 COA 复制到 0M</h2>
    <NAlert v-if="error" type="error">{{ error }}</NAlert>
    <NSelect v-model:value="row" placeholder="选择 0M 应检项目" :disabled="busy"
      :options="rows.map(item => ({ value: item.id, label: item.label + ' · ' + item.method }))" />
    <p v-if="previous">
      <a v-if="previous.coa?.source_id" :href="'/api/v1/coa-sources/' + previous.coa.source_id + '/file'">下载所引 COA 原件 · 版本 {{ previous.coa.version }}</a>
      <span v-else-if="previous.coa">在线录入的 COA · 版本 {{ previous.coa.version }}</span>
      <span v-else>已引用版本 {{ previous.result_version_id }}</span>，放行完成日期 {{ previous.release_completed }}
    </p>
    <p v-if="pending">方案表 {{ pending.table }} · 行 {{ pending.row }} · 列 {{ pending.column }}：{{ pending.raw }}</p>
    <NSelect v-model:value="chosen" placeholder="选择已采用的放行结果" :disabled="busy || !row" :loading="loading" :options="choices" />
    <label v-if="!selected?.fingerprint">放行检测完成日期 <input v-model="date" type="date" :max="sheet.start_date" :disabled="busy"></label>
    <label>处理说明 <input v-model="basis" :disabled="busy" maxlength="2000"></label>
    <div class="buttons">
      <label v-if="selected?.fingerprint && adopted">当前值 {{ adopted.text }}
        <select v-model="intent" :disabled="busy">
          <option value="keep">保留原值</option>
          <option value="correction">更正</option>
          <option value="retest">记录复测</option>
        </select>
      </label>
      <NButton v-if="selected?.fingerprint" attr-type="submit" type="primary"
        :disabled="busy || (!!adopted && intent !== 'keep' && !basis.trim())">确认复制</NButton>
      <NButton v-else attr-type="submit" type="primary" :disabled="busy || !chosen || !date || !basis.trim()">确认引用</NButton>
      <NButton v-if="previous" :disabled="busy || !basis.trim()" @click="save(true)">解除引用</NButton>
      <NButton v-if="pending" :disabled="busy || !basis.trim()" @click="save(true)">取消引用要求</NButton>
      <NButton :disabled="busy" @click="emit('cancel')">取消</NButton>
    </div>
  </form>
</template>

<style scoped>
.release-form { display: grid; gap: 9px; max-width: 650px; padding: 14px; margin: 12px 0; background: white; }
.release-form label { display: grid; gap: 5px; }
.buttons { display: flex; gap: 8px; }
</style>
