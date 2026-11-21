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
.candidate-issue { padding: 12px 14px; margin-bottom: 10px; background: var(--bg-subtle); border: 1px solid var(--border); border-radius: var(--radius-md); }
.issue-header { display: flex; justify-content: space-between; align-items: baseline; gap: 8px; margin-bottom: 6px; }
.evidence-loc { font-family: var(--font-mono); font-size: 12px; color: var(--brand); }
.hint-text { color: var(--warning-fg); font-size: 12.5px; margin: 4px 0; }
.no-action-text { color: var(--text-muted); font-size: 12.5px; }
.action-select-row label { display: flex; align-items: center; gap: 8px; font-size: 13px; font-weight: 600; color: var(--text-secondary); }
.action-select-row select { padding: 5px 10px; border-radius: var(--radius-sm); border: 1px solid var(--border-strong); background: var(--bg-surface); min-height: 34px; }
.field-edit-row { margin: 8px 0; display: flex; flex-direction: column; gap: 4px; }
.field-edit-row label { font-size: 12.5px; font-weight: 600; color: var(--text-secondary); }
.field-edit-row textarea { width: 100%; padding: 6px 10px; font: inherit; border-radius: var(--radius-sm); border: 1px solid var(--border-strong); }
.reason-row { margin: 10px 0; }
.reason-row label { font-size: 12.5px; font-weight: 600; color: var(--text-secondary); display: flex; flex-direction: column; gap: 4px; }
.reason-pills { display: flex; flex-wrap: wrap; gap: 6px; margin-top: 6px; }
.button-row { margin-top: 10px; }
.confirm-box { margin-top: 10px; padding: 10px 12px; background: var(--success-bg); border: 1px solid var(--success-border); border-radius: var(--radius-sm); }
</style>
