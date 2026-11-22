<script setup lang="ts">
import { computed, ref } from 'vue';
import { NAlert, NButton } from 'naive-ui';
import { api } from './api';

const props = defineProps<{ sheetId?: string; disabled?: boolean }>();
const emit = defineEmits<{ saved: []; committed: [number]; historicalSource: [file: File] }>();
type Change = { coordinate: string; item: string; timepoint: string; current: string; value: string;
  status: 'new' | 'correction' | 'unchanged' | 'blocked' | 'conflict'; voided_since_export?: boolean };
type Preview = { sheet_id: string; name: string; revision: number; fingerprint: string; source_id: string; changes: Change[] };
const labels = { new: '新增', correction: '更正', unchanged: '未变化', blocked: '此格不可填写', conflict: '导出后已更新' };
const busy = ref(false);
const error = ref('');
const message = ref('');
const preview = ref<Preview>();
const decisions = ref<Record<string, 'keep' | 'correct' | 'retest'>>({});
const resolved = ref<string[]>([]);
const reason = ref('');
const download = ref('');
const attemptedFile = ref<File>();
let retry: { signature: string; key: string } | undefined;
const changing = computed(() => preview.value?.changes.filter(change =>
  change.status !== 'unchanged' && !resolved.value.includes(change.coordinate)) ?? []);
const needsDecision = (change: Change) => change.status === 'correction' || change.status === 'conflict';
const selected = computed(() => changing.value.filter(change => {
  if (change.status === 'new') return true;
  if (!needsDecision(change)) return false;
  const choice = decisions.value[change.coordinate];
  return choice === 'keep' || (!!choice && !change.voided_since_export && !!reason.value.trim());
}));
const hasCorrection = computed(() => changing.value.some(change =>
  needsDecision(change) && ['correct', 'retest'].includes(decisions.value[change.coordinate])));

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
function inspect(file: File) {
  const form = new FormData();
  form.set('file', file);
  if (props.sheetId) form.set('sheet_id', props.sheetId);
  return api<Preview>('/study-workbooks/preview', { method: 'POST', body: form });
}
function upload(event: Event) {
  const input = event.target as HTMLInputElement;
  const file = input.files?.[0];
  input.value = '';
  if (!file) return;
  return run(async () => {
    attemptedFile.value = file;
    preview.value = undefined;
    retry = undefined;
    decisions.value = {};
    resolved.value = [];
    reason.value = '';
    preview.value = await inspect(file);
  });
}
function commit() {
  return run(async () => {
    const data = preview.value;
    if (!data || !selected.value.length) return;
    const path = '/worksheets/' + data.sheet_id;
    const coordinates = selected.value.map(change => change.coordinate);
    const body = { revision: data.revision, fingerprint: data.fingerprint, reason: reason.value,
      coordinates, decisions: Object.fromEntries(coordinates.filter(coordinate => decisions.value[coordinate])
        .map(coordinate => [coordinate, decisions.value[coordinate]])) };
    const signature = JSON.stringify([data.sheet_id, data.source_id, body]);
    if (retry?.signature !== signature) retry = { signature, key: crypto.randomUUID() };
    const idempotencyKey = retry.key;
    const { token } = await api<{ token: string }>(path + '/lease', { method: 'POST' });
    let result;
    try {
      result = await post<{ result_version_ids: string[]; resolved_coordinates: string[]; remaining_coordinates: string[] }>(
        path + '/study-workbooks/' + data.source_id, {
        ...body, token, idempotency_key: idempotencyKey,
      });
    } finally {
      await post(path + '/lease/release', { token }).catch(() => undefined);
    }
    message.value = `已写入 ${result.result_version_ids.length} 个结果格，${result.remaining_coordinates.length} 格待核对。`;
    resolved.value.push(...result.resolved_coordinates);
    preview.value = undefined;
    retry = undefined;
    emit('saved');
    emit('committed', result.result_version_ids.length);
    if (result.remaining_coordinates.length && attemptedFile.value) {
      preview.value = await inspect(attemptedFile.value);
    }
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
        {{ changing.length }} 格待核对，{{ selected.length }} 格可提交。
      </p>
      <div v-if="changing.length" class="matrix"><table>
        <thead><tr><th>原格</th><th>检项／时间点</th><th>当前值</th><th>提交值</th><th>核对</th></tr></thead>
        <tbody><tr v-for="change in changing" :key="change.coordinate">
          <td>{{ change.coordinate }}</td><td>{{ change.item }} · {{ change.timepoint }}</td>
          <td>{{ change.current }}</td><td>{{ change.value }}</td><td>
            {{ change.voided_since_export ? '导出后已作废，须重新下载模板才能写入' : labels[change.status] }}
            <select v-if="needsDecision(change)" v-model="decisions[change.coordinate]"
              :aria-label="change.coordinate + ' 的冲突处理'" :disabled="busy">
              <option value="">暂不处理</option><option value="keep">保留当前值</option>
              <option v-if="!change.voided_since_export" value="correct">提交文件值</option>
              <option v-if="!change.voided_since_export && change.current" value="retest">记录复测</option>
            </select>
          </td>
        </tr></tbody>
      </table></div>
      <label v-if="hasCorrection">更正依据 <input v-model="reason" maxlength="2000" :disabled="busy"></label>
      <NButton v-if="changing.length" type="primary" :disabled="!selected.length || busy || disabled" @click="commit">确认提交 {{ selected.length }} 格</NButton>
      <NButton :disabled="busy" @click="preview = undefined">{{ changing.length ? '取消' : '完成核对' }}</NButton>
    </template>
  </section>
</template>
