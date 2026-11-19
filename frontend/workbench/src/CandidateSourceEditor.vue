<script setup lang="ts">
import { computed, ref } from 'vue';
import { NAlert, NButton } from 'naive-ui';
import { api } from './api';
import CandidateIssueReview, { type CandidateIssue } from './CandidateIssueReview.vue';
import { cellAddress, sourceFieldLabels, sourceReviewText, type Revision } from './sourceReviewText';

type Location = { section: string; row: number; column: number };
export type SourceCandidate = {
  id: string; candidate_revision: number; location: Location; evidence?: Record<string, Location>;
  raw_fields: Record<string, string>; interpreted_fields: Record<string, string>; corrections: Record<string, string>;
  revisions?: Record<string, Revision>; comment?: string; detail: string; allowed_actions: string[]; issues?: CandidateIssue[];
};
const props = defineProps<{ candidate: SourceCandidate; revision: number; source: string }>();
const emit = defineEmits<{ saved: []; close: []; review: [candidate: string] }>();
const candidate = props.candidate;
const editable = computed(() => candidate.allowed_actions.includes('edit'));
const current = { ...candidate.interpreted_fields, ...candidate.corrections };
const values = ref<Record<string, string>>({ ...current });
const reason = ref('');
const processing = ref(false);
const error = ref('');
const differences = computed(() => Object.fromEntries(Object.keys(sourceFieldLabels)
  .filter(field => (values.value[field] ?? '') !== (current[field] ?? ''))
  .map(field => [field, values.value[field] ?? ''])));
type Command = { candidate_revision: number; base_revision: number; idempotency_key: string; reason: string; fields: Record<string, string> };
const plan = ref<{ command: Command; detail: string; record: unknown }>();
const endpoint = '/candidates/' + encodeURIComponent(candidate.id);
function request(method: string, body: Command) {
  return { method, body: JSON.stringify(body), headers: { 'Content-Type': 'application/json' } };
}
async function run(action: () => Promise<void>) {
  if (processing.value) return;
  error.value = '';
  processing.value = true;
  try { await action(); }
  catch (e) { error.value = (e as Error).message; }
  finally { processing.value = false; }
}
function inspect() {
  if (!editable.value || !Object.keys(differences.value).length || !reason.value.trim()) return;
  return run(async () => {
    plan.value = undefined;
    const command = { base_revision: props.revision, candidate_revision: candidate.candidate_revision,
      idempotency_key: crypto.randomUUID(), fields: differences.value, reason: reason.value.trim() };
    const result = await api<{ record: unknown; detail: string }>(endpoint + '/edit-preview', request('POST', command));
    plan.value = { command, ...result };
  });
}
function save() {
  const preview = plan.value;
  if (!preview) return;
  return run(async () => {
    await api(endpoint, request('PATCH', preview.command));
    emit('saved');
  });
}
</script>

<template>
  <section class="panel candidate-source-editor" tabindex="-1">
    <h2>{{ editable ? '修改来源解释' : '查看原格' }} · {{ candidate.location.section }}!{{ cellAddress(candidate.location.row, candidate.location.column) }}</h2>
    <p>{{ sourceReviewText(candidate.detail, candidate.revisions?.result) }}</p>
    <p v-if="candidate.comment" class="original-value">原件批注：{{ candidate.comment }}</p>
    <NAlert v-if="error" type="error" role="alert">{{ error }}</NAlert>
    <p>解释修改仅保存草稿。删除线、批注、身份歧义和历史结果仍须通过各自核对流程。</p>
    <CandidateIssueReview v-for="issue in editable ? candidate.issues : []" :key="issue.id"
      :issue="issue" :source="source" :candidate="candidate.id" :candidate-revision="candidate.candidate_revision"
      :revision="revision" :current="current" @saved="emit('saved')" />
    <form @submit.prevent="inspect" @input="plan = undefined">
      <div class="matrix"><table>
        <thead><tr><th>字段／原格</th><th>原文</th><th>当前解释</th><th v-if="editable">本次解释</th></tr></thead>
        <tbody><tr v-for="(label, field) in sourceFieldLabels" :key="field">
          <th>{{ label }}<template v-if="candidate.evidence?.[field]">
            · {{ cellAddress(candidate.evidence[field].row, candidate.evidence[field].column) }}</template></th>
          <td>{{ candidate.raw_fields[field] || '—' }}</td>
          <td>{{ current[field] || '—' }}<strong v-if="Object.hasOwn(candidate.corrections, field)"> · 已人工修改</strong></td>
          <td v-if="editable"><textarea :aria-label="String(label)" v-model="values[field]" rows="1" :disabled="processing" /></td>
        </tr></tbody>
      </table></div>
      <label v-if="editable">本次解释依据 <input v-model="reason" :disabled="processing"></label>
      <NButton v-if="editable" attr-type="submit" :disabled="processing || !reason.trim() || !Object.keys(differences).length">预览解释修改</NButton>
    </form>
    <section v-if="plan" aria-label="解释修改预览">
      <h3>本次解释预览</h3>
      <ul><li v-for="(value, field) in plan.command.fields" :key="field">
        {{ sourceFieldLabels[field] }}：{{ current[field] || '空白' }} → {{ value || '空白' }}
      </li></ul>
      <p>{{ plan.record ? '本次解释可映射，保存后仍需整表预检。' : sourceReviewText(plan.detail) }}</p>
      <NButton :disabled="processing" type="primary" @click="save">保存解释草稿</NButton>
    </section>
    <NButton v-if="editable" :disabled="processing" @click="emit('review', candidate.id)">核对本原格争议</NButton>
    <NButton :disabled="processing" @click="emit('close')">返回候选与任务</NButton>
  </section>
</template>

<style scoped>
.candidate-source-editor textarea { width: 100%; min-width: 160px; font: inherit; }
.original-value { white-space: pre-wrap; }
</style>
