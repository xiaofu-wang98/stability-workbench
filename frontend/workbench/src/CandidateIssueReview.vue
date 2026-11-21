<script setup lang="ts">
import { computed, ref } from 'vue';
import { NAlert, NButton, NInput } from 'naive-ui';
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

const reasonPills = ['核对未划线正文无误', '核对批注确认有效', '以原表标注为准', '确认为无效记录，不予采用'];

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
  <div class="candidate-issue">
    <div class="issue-header">
      <strong>问题 · {{ sourceReviewText(issue.detail) }}</strong>
      <span class="evidence-loc">{{ issue.evidence.map(loc => loc.section + '!' + cellAddress(loc.row, loc.column)).join('、') }}</span>
    </div>
    <p v-if="issue.source_kind === 'system_hint'" class="hint-text">此条为系统解释建议，尚未确认。</p>
    <p v-if="!actions.length" class="no-action-text">请转到关联任务中的原表结构核对入口。</p>
    <NAlert v-if="error" type="error" role="alert">{{ error }}</NAlert>
    <form v-if="actions.length" @submit.prevent="preview" @input="confirmed = undefined" @change="confirmed = undefined">
      <div class="action-select-row">
        <label>处理方式
          <select v-model="action" :disabled="busy">
            <option value="">请选择</option>
            <option v-for="item in actions" :key="item" :value="item">{{ labels[item] }}</option>
          </select>
        </label>
      </div>
      <div v-for="field in action === 'manual' ? issue.fields : []" :key="field" class="field-edit-row">
        <label>{{ sourceFieldLabels[field] || field }} · 当前 {{ current[field] || '空白' }}</label>
        <textarea v-model="values[field]" :aria-label="'新解释：' + (sourceFieldLabels[field] || field)" :disabled="busy" rows="1" />
      </div>
      <div class="reason-row">
        <label>核对依据
          <NInput v-model:value="reason" placeholder="请输入核对依据..." :disabled="busy" />
        </label>
        <div class="reason-pills">
          <NButton v-for="pill in reasonPills" :key="pill" size="tiny" secondary type="info" :disabled="busy" @click="reason = pill">
            {{ pill }}
          </NButton>
        </div>
      </div>
      <div class="button-row">
        <NButton attr-type="submit" :disabled="busy || !action || !reason.trim() || action === 'manual' && !Object.keys(fields).length">
          预览本原格处置
        </NButton>
      </div>
    </form>
    <div v-if="confirmed" class="confirm-box">
      <p>{{ labels[confirmed.action] }} · 仅核对当前候选的这一项争议。</p>
      <p v-for="(value, field) in confirmed.fields" :key="field">{{ sourceFieldLabels[field] || field }}：
        {{ current[field] || '空白' }} → {{ value || '空白' }}</p>
      <NButton type="primary" :disabled="busy" @click="save">保存此原格决定</NButton>
    </div>
  </div>
</template>

<style scoped>
.candidate-issue { padding: 0.8rem; margin-bottom: 0.8rem; background: #fdfdfd; border: 1px solid #e2e8e6; border-radius: 4px; }
.issue-header { display: flex; justify-content: space-between; align-items: baseline; margin-bottom: 0.4rem; }
.evidence-loc { font-size: 0.82rem; color: #64748b; }
.hint-text { color: #f59e0b; font-size: 0.85rem; margin: 0.2rem 0; }
.no-action-text { color: #6b7280; font-size: 0.85rem; }
.action-select-row select { padding: 4px 8px; border-radius: 4px; border: 1px solid #cbd5e1; }
.field-edit-row { margin: 0.5rem 0; }
.field-edit-row textarea { width: 100%; padding: 4px 8px; font: inherit; }
.reason-row { margin: 0.6rem 0; }
.reason-pills { display: flex; flex-wrap: wrap; gap: 4px; margin-top: 4px; }
.button-row { margin-top: 0.6rem; }
.confirm-box { margin-top: 0.6rem; padding: 0.6rem; background: #f0fdf4; border: 1px solid #bbf7d0; border-radius: 4px; }
</style>
