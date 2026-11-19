<script setup lang="ts">
import { computed, ref } from 'vue';
import { NAlert, NButton } from 'naive-ui';
import { api } from './api';
import { cellAddress, sourceFieldLabels, sourceReviewText } from './sourceReviewText';

export type CandidateIssue = { id: string; code: string; source_kind: string; detail: string; actions: string[];
  fields: string[]; source_field?: string; evidence: { section: string; row: number; column: number }[];
  proposal: { after?: string; eligible?: boolean } };
const props = defineProps<{ issue: CandidateIssue; source: string; candidate: string; candidateRevision: number;
  revision: number; current: Record<string, string> }>();
const emit = defineEmits<{ saved: [] }>();
const labels: Record<string, string> = { keep_active: '核对并保留未划线正文', keep_body: '核对并保留正文',
  manual: '人工明确此问题的解释', exclude: '不采用本候选', keep_single: '确认合并排版' };
const actions = props.issue.actions.filter(action => Object.hasOwn(labels, action));
const action = ref('');
const reason = ref('');
const values = ref<Record<string, string>>({});
const busy = ref(false);
const error = ref('');
const confirmed = ref<{ fingerprint: string; scope: object; fields: Record<string, string>; action: string;
  reason: string; idempotency_key: string }>();
const endpoint = '/previews/' + encodeURIComponent(props.source) + '/v5/issues';
const fields = computed(() => action.value === 'keep_active' && props.issue.source_field
  ? { [props.issue.source_field]: props.issue.proposal.after ?? '' }
  : action.value === 'manual' ? Object.fromEntries(Object.entries(values.value).filter(([, v]) => v.trim())) : {});
async function send(suffix: string, body: object) {
  return api<{ fingerprint: string }>(endpoint + suffix, {
    method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body)
  });
}
async function run(operation: () => Promise<void>) {
  if (busy.value) return;
  busy.value = true;
  error.value = '';
  try { await operation(); } catch (e) { error.value = (e as Error).message; }
  finally { busy.value = false; }
}
function preview() {
  return run(async () => {
    const scope = { issues: [{ candidate_id: props.candidate, candidate_revision: props.candidateRevision,
      issue_id: props.issue.id, action: action.value, fields: fields.value }] };
    const result = await send('/preview', scope);
    confirmed.value = { fingerprint: result.fingerprint, scope, fields: fields.value, action: action.value,
      reason: reason.value.trim(), idempotency_key: crypto.randomUUID() };
  });
}
function save() {
  const pending = confirmed.value;
  if (!pending) return;
  return run(async () => {
    await send('/apply', { fingerprint: pending.fingerprint, scope: pending.scope, reason: pending.reason,
      idempotency_key: pending.idempotency_key, base_revision: props.revision });
    emit('saved');
  });
}
</script>

<template>
  <details class="candidate-issue">
    <summary>核对此原格 · {{ sourceReviewText(issue.detail) }}</summary>
    <p>{{ issue.evidence.map(loc => loc.section + '!' + cellAddress(loc.row, loc.column)).join('、') }}</p>
    <p v-if="issue.source_kind === 'system_hint'">此条为系统解释建议，尚未确认。</p>
    <p v-if="!actions.length">请转到关联任务中的原表结构核对入口。</p>
    <NAlert v-if="error" type="error" role="alert">{{ error }}</NAlert>
    <form v-if="actions.length" @submit.prevent="preview" @input="confirmed = undefined" @change="confirmed = undefined">
      <label>本项处理方式 <select v-model="action" :disabled="busy">
        <option value="">请选择</option>
        <option v-for="item in actions" :key="item" :value="item">{{ labels[item] }}</option>
      </select></label>
      <label v-for="field in action === 'manual' ? issue.fields : []" :key="field">
        {{ sourceFieldLabels[field] || field }} · 当前 {{ current[field] || '空白' }}
        <textarea v-model="values[field]" :aria-label="'新解释：' + (sourceFieldLabels[field] || field)" :disabled="busy" />
      </label>
      <label>本项核对依据 <input v-model="reason" :disabled="busy"></label>
      <NButton attr-type="submit" :disabled="busy || !action || !reason.trim() || action === 'manual' && !Object.keys(fields).length">预览本原格处置</NButton>
    </form>
    <div v-if="confirmed">
      <p>{{ labels[confirmed.action] }} · 仅核对当前候选的这一项争议。</p>
      <p v-for="(value, field) in confirmed.fields" :key="field">{{ sourceFieldLabels[field] || field }}：
        {{ current[field] || '空白' }} → {{ value || '空白' }}</p>
      <NButton type="primary" :disabled="busy" @click="save">保存此原格决定</NButton>
    </div>
  </details>
</template>

<style scoped>
.candidate-issue { padding: .6rem; border-bottom: 1px solid #dbe5e2; }
.candidate-issue label { display: block; margin: .4rem 0; }
</style>
