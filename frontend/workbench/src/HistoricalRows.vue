<script setup lang="ts">
import { ref } from 'vue';
import { NAlert, NButton } from 'naive-ui';
import { api } from './api';

const props = defineProps<{ sheetId: string; disabled: boolean }>();
const emit = defineEmits<{ saved: [] }>();
type Preview = { revision: number; fingerprint: string; rows: {
  plan_key: string; label: string; method: string; reporting_basis: string; points: string[];
}[] };
const preview = ref<Preview>();
const busy = ref(false);
const error = ref('');
const basis = ref('');
const path = () => '/worksheets/' + props.sheetId;
function post<T>(url: string, body: unknown) {
  return api<T>(url, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) });
}
async function run(action: () => Promise<void>) {
  busy.value = true;
  error.value = '';
  try { await action(); }
  catch (e) { error.value = (e as Error).message; }
  finally { busy.value = false; }
}
function inspect() {
  return run(async () => { preview.value = await api(path() + '/historical-rows'); });
}
function save() {
  return run(async () => {
    const data = preview.value;
    if (!data) return;
    const { token } = await api<{ token: string }>(path() + '/lease', { method: 'POST' });
    try {
      await post(path() + '/historical-rows?fingerprint=' + data.fingerprint, {
        token, basis: basis.value, revision: data.revision, idempotency_key: crypto.randomUUID(),
      });
      preview.value = undefined;
      emit('saved');
    } finally {
      await post(path() + '/lease/release', { token });
    }
  });
}
</script>

<template>
  <details class="panel" open>
    <summary>补入已采用的历史结果行</summary>
    <NAlert v-if="error" type="error" role="alert">{{ error }}</NAlert>
    <NButton :disabled="disabled || busy" @click="inspect">核对当前研究范围</NButton>
    <template v-if="preview">
      <p v-if="!preview.rows.length" role="status">当前计划月份没有待补入的采用结果行。</p>
      <form v-else @submit.prevent="save">
        <p>可新增 {{ preview.rows.length }} 行并引用原有采用结果，确认后保存为新计划版本。</p>
        <ul><li v-for="row in preview.rows" :key="row.plan_key">
          {{ [row.label, row.method, row.reporting_basis, row.points.join('、')].filter(Boolean).join(' · ') }}
        </li></ul>
        <label>关联依据 <input v-model.trim="basis" required></label>
        <NButton attr-type="submit" :disabled="disabled || busy">确认补入</NButton>
      </form>
    </template>
  </details>
</template>
