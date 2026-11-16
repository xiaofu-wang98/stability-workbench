<script setup lang="ts">
import { computed, ref } from 'vue';
import { NAlert, NButton } from 'naive-ui';
import { api } from './api';

const props = defineProps<{ sheetId?: string; disabled?: boolean }>();
const emit = defineEmits<{ saved: []; historicalSource: [file: File] }>();
type Change = { coordinate: string; item: string; timepoint: string; current: string; value: string;
  status: 'new' | 'correction' | 'unchanged' | 'blocked' | 'conflict' };
type Preview = { sheet_id: string; name: string; revision: number; fingerprint: string; source_id: string; changes: Change[] };
const labels = { new: '新增', correction: '更正', unchanged: '未变化', blocked: '此格不可填写', conflict: '导出后已更新' };
const busy = ref(false);
const error = ref('');
const message = ref('');
const preview = ref<Preview>();
const decisions = ref<Record<string, 'keep' | 'correct'>>({});
const reason = ref('');
const download = ref('');
const attemptedFile = ref<File>();
const changing = computed(() => preview.value?.changes.filter(change => change.status !== 'unchanged') ?? []);
const hasCorrection = computed(() => changing.value.some(change =>
  change.status === 'correction' || (change.status === 'conflict' && decisions.value[change.coordinate] === 'correct')));
const ready = computed(() => changing.value.every(change =>
  change.status !== 'blocked' && (change.status !== 'conflict' || decisions.value[change.coordinate]))
  && (!hasCorrection.value || reason.value.trim()));

function post<T>(path: string, value: unknown) {
  return api<T>(path, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(value) });
}
async function run(action: () => Promise<void>) {
  if (busy.value || props.disabled) return;
  busy.value = true;
  error.value = '';
  message.value = '';
  try { await action(); }
  catch (e) { error.value = (e as Error).message; }
  finally { busy.value = false; }
}
function generate(filled: boolean) {
  return run(async () => {
    download.value = (await api<{ download_url: string }>(
      '/worksheets/' + props.sheetId + '/study-workbook?filled=' + filled, { method: 'POST' })).download_url;
  });
}
function upload(event: Event) {
  const input = event.target as HTMLInputElement;
  const file = input.files?.[0];
  input.value = '';
  if (!file) return;
  return run(async () => {
    attemptedFile.value = file;
    preview.value = undefined;
    decisions.value = {};
    reason.value = '';
    const form = new FormData();
    form.set('file', file);
    if (props.sheetId) form.set('sheet_id', props.sheetId);
    preview.value = await api('/study-workbooks/preview', { method: 'POST', body: form });
  });
}
function commit() {
  return run(async () => {
    const data = preview.value;
    if (!data || !ready.value) return;
    const path = '/worksheets/' + data.sheet_id;
    const { token } = await api<{ token: string }>(path + '/lease', { method: 'POST' });
    let result;
    try {
      result = await post<{ result_version_ids: string[] }>(path + '/study-workbooks/' + data.source_id, {
        token, revision: data.revision, fingerprint: data.fingerprint, idempotency_key: crypto.randomUUID(),
        reason: reason.value, decisions: decisions.value,
      });
    } finally {
      await post(path + '/lease/release', { token });
    }
    message.value = `已写入 ${result.result_version_ids.length} 个结果格。`;
    preview.value = undefined;
    emit('saved');
  });
}
</script>

<template>
  <section class="panel">
    <h2>研究表 Excel</h2>
    <div class="entry-actions">
      <template v-if="sheetId">
        <NButton :disabled="busy || disabled" @click="generate(false)">下载填写模板</NButton>
        <NButton :disabled="busy || disabled" @click="generate(true)">生成稳定性汇总 Excel</NButton>
        <a v-if="download" :href="download">下载 Excel</a>
      </template>
      <label>上传填写后的研究表 <input type="file" accept=".xlsx" :disabled="busy || disabled" @change="upload"></label>
    </div>
    <NAlert v-if="error" type="error" role="alert">{{ error }}</NAlert>
    <NButton v-if="!sheetId && error && attemptedFile" :disabled="busy || disabled"
      @click="emit('historicalSource', attemptedFile)">改用历史结果导入</NButton>
    <p v-if="message" role="status">{{ message }}</p>
    <template v-if="preview">
      <p role="status">
        目标研究 <RouterLink :to="'/worksheets/' + preview.sheet_id">{{ preview.name }}</RouterLink>。
        {{ changing.length }} 格有差异，{{ preview.changes.length - changing.length }} 格未变化。
      </p>
      <div v-if="changing.length" class="matrix"><table>
        <thead><tr><th>原格</th><th>检项／时间点</th><th>当前值</th><th>提交值</th><th>核对</th></tr></thead>
        <tbody><tr v-for="change in changing" :key="change.coordinate">
          <td>{{ change.coordinate }}</td><td>{{ change.item }} · {{ change.timepoint }}</td>
          <td>{{ change.current }}</td><td>{{ change.value }}</td><td>
            {{ labels[change.status] }}
            <select v-if="change.status === 'conflict'" v-model="decisions[change.coordinate]"
              :aria-label="change.coordinate + ' 的冲突处理'" :disabled="busy">
              <option value="">请选择</option><option value="keep">保留当前值</option><option value="correct">提交文件值</option>
            </select>
          </td>
        </tr></tbody>
      </table></div>
      <label v-if="hasCorrection">更正依据 <input v-model="reason" maxlength="2000" :disabled="busy"></label>
      <NButton v-if="changing.length" type="primary" :disabled="!ready || busy || disabled" @click="commit">确认写回研究表</NButton>
      <NButton :disabled="busy" @click="preview = undefined">{{ changing.length ? '取消' : '完成核对' }}</NButton>
    </template>
  </section>
</template>
