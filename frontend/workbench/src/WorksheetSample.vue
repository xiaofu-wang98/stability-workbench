<script setup lang="ts">
import { ref } from 'vue';
import { NAlert, NButton } from 'naive-ui';
import { api, type Worksheet } from './api';

const props = defineProps<{ sheet: Worksheet; disabled: boolean }>();
const emit = defineEmits<{ saved: [] }>();
type Sample = { id: string; material_stage: string; sample_type: string; presentation: string; packaging: string; purpose: string; compatible: boolean };
const candidates = ref<Sample[]>();
const sampleId = ref('');
const basis = ref('');
const busy = ref(false);
const error = ref('');
const path = () => '/worksheets/' + encodeURIComponent(props.sheet.id);
function post<T>(suffix: string, body: unknown) {
  return api<T>(path() + suffix, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) });
}
async function run(action: () => Promise<void>) {
  busy.value = true;
  error.value = '';
  try { await action(); }
  catch (e) { error.value = (e as Error).message; }
  finally { busy.value = false; }
}
function inspect() {
  return run(async () => { candidates.value = await api(path() + '/sample-candidates'); });
}
function save() {
  return run(async () => {
    const revision = props.sheet.revision;
    const { token } = await api<{ token: string }>(path() + '/lease', { method: 'POST' });
    try {
      await post('/sample', { token, revision, sample_id: sampleId.value, basis: basis.value,
        idempotency_key: crypto.randomUUID() });
    } finally {
      await post('/lease/release', { token });
    }
    emit('saved');
  });
}
</script>

<template>
  <details v-if="sheet.editable && !sheet.sample_id" class="panel" open>
    <summary>关联已有样品</summary>
    <NAlert v-if="error" type="error">{{ error }}</NAlert>
    <NButton :disabled="disabled || busy" @click="inspect">查找同批号样品</NButton>
    <form v-if="candidates?.length" @submit.prevent="save">
      <fieldset>
        <legend>按材料阶段、规格和用途核对样品</legend>
        <div v-for="sample in candidates" :key="sample.id">
          <label><input v-model="sampleId" type="radio" :value="sample.id" :disabled="disabled || busy || !sample.compatible">
            {{ [sample.material_stage, sample.sample_type, sample.presentation, sample.packaging, sample.purpose].filter(Boolean).join(' · ') }}
            <span v-if="!sample.compatible">（与研究表属性不符）</span>
          </label>
        </div>
      </fieldset>
      <label>关联依据 <input v-model.trim="basis" required maxlength="2000" :disabled="disabled || busy"></label>
      <NButton attr-type="submit" :disabled="disabled || busy || !sampleId || !basis">确认关联样品</NButton>
    </form>
    <p v-else-if="candidates">当前项目下没有此批号的样品对象。</p>
  </details>
</template>
