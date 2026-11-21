<script setup lang="ts">
import { onMounted, ref } from 'vue';
import { NAlert, NButton, NSelect } from 'naive-ui';
import { api, type Worksheet } from './api';
const props = defineProps<{ sheet: Worksheet; busy: boolean }>();
const emit = defineEmits<{ save: [object]; cancel: [] }>();
const types = ['异常调查', '复测', '方法变更', '标准变更', '设备转移', '备注'];
const category = ref('');
const reference = ref('');
const description = ref('');
const row = ref<string | null>(null);
const point = ref<string | null>(null);
const scan = ref<string | null>(null);
const scans = ref<{ id: string; filename: string }[]>([]);
const error = ref('');
onMounted(async () => {
  try { scans.value = await api('/pdf-ocr'); }
  catch (e) { error.value = (e as Error).message; }
});
function save() {
  if (props.busy || !category.value || !description.value.trim()) return;
  emit('save', { category: category.value, reference: reference.value.trim(), description: description.value.trim(),
    row_id: row.value, point_id: point.value, scan_id: scan.value });
}
</script>
<template>
  <form class="event-form" @submit.prevent="save">
    <h2>记录研究事件</h2>
    <NAlert v-if="error" type="error">{{ error }}</NAlert>
    <label>事件类别 <select v-model="category" :disabled="busy" required>
      <option value="">请选择</option><option v-for="type in types" :key="type">{{ type }}</option>
    </select></label>
    <NSelect v-model:value="row" clearable placeholder="检项（可选）" :disabled="busy"
      :options="sheet.rows.map(row => ({ value: row.id, label: row.label + ' · ' + row.method }))" />
    <NSelect v-model:value="point" clearable placeholder="时间点（可选）" :disabled="busy"
      :options="sheet.points.map(point => ({ value: point.id, label: point.label }))" />
    <label>事件编号 <input v-model="reference" maxlength="200" :disabled="busy"></label>
    <label>事件说明 <textarea v-model="description" maxlength="4000" required :disabled="busy"></textarea></label>
    <NSelect v-if="scans.length" v-model:value="scan" clearable placeholder="扫描附件（可选）" :disabled="busy"
      :options="scans.map(item => ({ value: item.id, label: item.filename }))" />
    <div class="buttons">
      <NButton attr-type="submit" type="primary" :disabled="busy || !category || !description.trim()">保存事件</NButton>
      <NButton :disabled="busy" @click="emit('cancel')">取消</NButton>
    </div>
  </form>
</template>
<style scoped>
.event-form { display: grid; gap: 10px; max-width: 680px; padding: 16px 18px; margin: 12px 0; background: var(--bg-surface); border: 1px solid var(--border); border-radius: var(--radius-md); box-shadow: var(--shadow-card); }
.event-form label { display: grid; gap: 5px; font-size: 13px; font-weight: 600; color: var(--text-secondary); }
.buttons { display: flex; gap: 8px; }
</style>
