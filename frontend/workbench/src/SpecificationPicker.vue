<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue';
import { NAlert, NButton, NSelect } from 'naive-ui';
import { api } from './api';
const props = defineProps<{ initial?: string; linkOnly?: boolean; project?: string; material?: string; purpose?: string }>();
const emit = defineEmits<{ add: [{ label: string; method: string; unit: string; result_type: 'numeric' | 'qualitative'; criteria_text: string; specification_links: { specification_id: string; table: number; row: number }[] }[]]; close: [] }>();
type Row = { table: number; row: number; item: string; category: string; method: string; criterion: string;
  component?: string; unit?: string; result_type?: 'numeric' | 'qualitative' };
const sources = ref<{ id: string; filename: string; material_scope: string; registered_projects: string[] }[]>([]);
const source = ref(props.initial || '');
const rows = ref<Row[]>([]);
const selected = ref<string[]>([]);
const scope = ref('');
const error = ref('');
const loading = ref(false);
const sourcesLoaded = ref(false);
const requiredScope = computed(() => props.purpose === 'reference' ? 'reference' : props.material);
const eligible = computed(() => sources.value.filter(item =>
  (!props.project || !item.registered_projects.length || item.registered_projects.includes(props.project))
  && (!requiredScope.value || item.material_scope === 'unspecified' || item.material_scope === requiredScope.value)));
const scopeBlocked = computed(() => sourcesLoaded.value && !!source.value
  && sources.value.some(item => item.id === source.value) && !eligible.value.some(item => item.id === source.value));
const key = (row: Row) => row.table + ':' + row.row;
onMounted(async () => {
  try { sources.value = await api('/specifications'); sourcesLoaded.value = true; } catch (e) { error.value = (e as Error).message; }
});
watch(source, async (id, _, cleanup) => {
  const controller = new AbortController(); cleanup(() => controller.abort());
  rows.value = []; selected.value = []; error.value = ''; loading.value = !!id;
  if (!id) return;
  try {
    const data = await api<{ rows: Row[]; scope: string; material_scope: string }>('/specifications/' + id, { signal: controller.signal });
    rows.value = data.rows; scope.value = [data.material_scope, data.scope].filter(Boolean).join(' · ');
  } catch (e) { if (!controller.signal.aborted) error.value = (e as Error).message; }
  finally { if (!controller.signal.aborted) loading.value = false; }
}, { immediate: true });
function add() {
  if (scopeBlocked.value) return;
  emit('add', rows.value.filter(row => selected.value.includes(key(row))).map(row => ({
    label: [row.item || row.category, row.component].filter(Boolean).join(' · '), method: row.method,
    unit: row.unit || '', result_type: row.result_type || 'qualitative', criteria_text: row.criterion || '',
    specification_links: [{ specification_id: source.value, table: row.table, row: row.row }],
  })));
}
</script>
<template>
  <section class="spec-picker">
    <h3>引用质量标准条款</h3>
    <p v-if="linkOnly">选择适用于当前结果行的条款。不同子结果请分别建行、分别关联。</p>
    <p v-else>从质量标准原件中直接勾选条款加入研究表。名称、方法、单位和定性/定量类型来自原件；加入后可自由编辑与补充。</p>
    <NAlert v-if="error" type="error" role="alert">{{ error }}</NAlert>
    <label>质量标准原件 <NSelect v-model:value="source" filterable clearable placeholder="输入文件名快速检索质量标准原件..." style="min-width: 320px; display: inline-block; vertical-align: middle;" :options="eligible.map(s => ({ value: s.id, label: s.filename }))" /></label>
    <NAlert v-if="scopeBlocked" type="warning">该标准不适用于当前项目、材料或用途。请选择其他标准。</NAlert>
    <RouterLink to="/specifications">导入质量标准</RouterLink>
    <p v-if="loading" role="status">正在读取标准条款…</p>
    <p v-if="source && !loading">适用范围：{{ scope || '原件未注明，请核对' }}</p>
    <p v-if="source && !loading && !rows.length">未识别到条款，请核对原件。</p>
    <div v-if="rows.length" style="margin: 8px 0; display: flex; gap: 8px; align-items: center;">
      <NButton size="small" @click="selected = rows.map(key)">全选所有条款 ({{ rows.length }})</NButton>
      <NButton size="small" @click="selected = []">清空所选</NButton>
      <span style="font-size: 13px; color: #606266;">已选 {{ selected.length }} / {{ rows.length }} 项</span>
    </div>
    <div v-if="rows.length" class="matrix"><table><thead><tr><th>选择</th><th>检项</th><th>方法</th><th>标准原文</th></tr></thead><tbody>
      <tr v-for="row in rows" :key="key(row)"><td><input type="checkbox" v-model="selected" :value="key(row)" :aria-label="'引用条款 ' + key(row)"></td><td>{{ row.item || row.category }}</td><td>{{ row.method }}</td><td>{{ row.criterion }}</td></tr>
    </tbody></table></div>
    <div class="entry-actions"><NButton :disabled="!selected.length || loading || scopeBlocked" type="primary" @click="add">{{ linkOnly ? '关联到当前检项' : '将所选条款加入研究表' }}</NButton><NButton @click="emit('close')">收起</NButton></div>
  </section>
</template>
<style scoped>
.spec-picker { padding: 16px; background: #f1f7fb; border: 1px solid #bdd1e0; border-radius: 8px; margin: 12px 0; }
.spec-picker select { max-width: 100%; margin: 8px; }
td { white-space: pre-wrap; }
</style>
