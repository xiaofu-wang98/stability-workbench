<script setup lang="ts">
import { computed, ref, watch } from 'vue';
import { NAlert, NButton, NDrawer, NDrawerContent, NInput, useMessage } from 'naive-ui';
import { api } from './api';
import CandidateIssueReview, { type CandidateIssue } from './CandidateIssueReview.vue';
import { cellAddress, sourceFieldLabels, sourceReviewText, type Revision } from './sourceReviewText';

type Location = { section: string; row: number; column: number };
export type SourceCandidate = {
  id: string; candidate_revision: number; location: Location; evidence?: Record<string, Location>;
  raw_fields: Record<string, string>; interpreted_fields: Record<string, string>; corrections: Record<string, string>;
  revisions?: Record<string, Revision>; comment?: string; detail: string; allowed_actions: string[]; issues?: CandidateIssue[];
};
const props = defineProps<{ candidate: SourceCandidate; revision: number; source: string; candidatesList?: SourceCandidate[] }>();
const emit = defineEmits<{ saved: []; close: []; review: [candidate: string]; navigate: [id: string] }>();

const message = useMessage();
const candidate = computed(() => props.candidate);
const editable = computed(() => candidate.value.allowed_actions.includes('edit'));
const current = computed(() => ({ ...candidate.value.interpreted_fields, ...candidate.value.corrections }));
const values = ref<Record<string, string>>({ ...current.value });
watch(current, val => { values.value = { ...val }; }, { deep: true });

const reason = ref('');
const processing = ref(false);
const error = ref('');
const differences = computed(() => Object.fromEntries(Object.keys(sourceFieldLabels)
  .filter(field => (values.value[field] ?? '') !== (current.value[field] ?? ''))
  .map(field => [field, values.value[field] ?? ''])));

type Command = { candidate_revision: number; base_revision: number; idempotency_key: string; reason: string; fields: Record<string, string> };
const plan = ref<{ command: Command; detail: string; record: unknown }>();
const endpoint = computed(() => '/candidates/' + encodeURIComponent(candidate.value.id));

const anomalyCandidates = computed(() => (props.candidatesList || []).filter(c => (c.issues && c.issues.length > 0) || c.revisions));
const currIdx = computed(() => anomalyCandidates.value.findIndex(c => c.id === candidate.value.id));
const prevAnomalyId = computed(() => currIdx.value > 0 ? anomalyCandidates.value[currIdx.value - 1]?.id : undefined);
const nextAnomalyId = computed(() => currIdx.value >= 0 && currIdx.value < anomalyCandidates.value.length - 1 ? anomalyCandidates.value[currIdx.value + 1]?.id : undefined);

const focusedFields = computed(() => {
  const list = [...new Set((candidate.value.issues || []).flatMap(i => i.fields || []))];
  return list.length ? list : ['result', 'subresult'];
});

const quickReasons = ['数据录入笔误更正', '核对原始记录无误', '按新版标准更正', '仪器重新校准后复测'];

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
    const command = { base_revision: props.revision, candidate_revision: candidate.value.candidate_revision,
      idempotency_key: crypto.randomUUID(), fields: differences.value, reason: reason.value.trim() };
    const result = await api<{ record: unknown; detail: string }>(endpoint.value + '/edit-preview', request('POST', command));
    plan.value = { command, ...result };
  });
}
function save() {
  const preview = plan.value;
  if (!preview) return;
  return run(async () => {
    await api(endpoint.value, request('PATCH', preview.command));
    try { message.success('已保存单元格修改草稿'); } catch {}
    emit('saved');
  });
}
async function quickSave() {
  if (!editable.value || !Object.keys(differences.value).length) return;
  if (!reason.value.trim()) reason.value = quickReasons[0];
  await inspect();
  if (plan.value) await save();
}
</script>

<template>
  <NDrawer class="candidate-drawer" :show="true" :width="'min(620px, 92vw)'" placement="right" :on-update:show="val => { if (!val) emit('close'); }">
    <NDrawerContent closable :on-close="() => emit('close')">
      <template #header>
        <div class="drawer-header">
          <div class="header-title">
            <span class="coord-badge">{{ candidate.location.section }}!{{ cellAddress(candidate.location.row, candidate.location.column) }}</span>
            <span>{{ editable ? '核对/修改原格解释' : '查看原格' }}</span>
          </div>
          <div class="nav-buttons">
            <NButton size="tiny" secondary :disabled="!prevAnomalyId" @click="prevAnomalyId && emit('navigate', prevAnomalyId)">上一个异常</NButton>
            <NButton size="tiny" secondary :disabled="!nextAnomalyId" @click="nextAnomalyId && emit('navigate', nextAnomalyId)">下一个异常</NButton>
          </div>
        </div>
      </template>

      <div class="drawer-body">
        <div class="orig-card">
          <div class="card-label">原件原文</div>
          <div class="card-text">{{ sourceReviewText(candidate.detail, candidate.revisions?.result) }}</div>
          <div v-if="candidate.comment" class="comment-text"><strong>批注：</strong>{{ candidate.comment }}</div>
        </div>

        <NAlert v-if="error" type="error" role="alert" style="margin-bottom: 12px;">{{ error }}</NAlert>

        <section v-if="candidate.issues && candidate.issues.length" class="issues-section">
          <h4>待核对原格问题 ({{ candidate.issues.length }})</h4>
          <CandidateIssueReview v-for="issue in candidate.issues" :key="issue.id"
            :issue="issue" :source="source" :candidate="candidate.id" :candidate-revision="candidate.candidate_revision"
            :revision="revision" :current="current" @saved="emit('saved')" />
        </section>

        <section v-if="editable" class="focused-fields-section">
          <h4>快速修正出错字段 ({{ focusedFields.length }})</h4>
          <form @submit.prevent="quickSave">
            <div v-for="field in focusedFields" :key="field" class="field-row">
              <label>
                <div class="field-meta">
                  <span class="field-title">{{ sourceFieldLabels[field] || field }}</span>
                  <span class="field-raw">原文: {{ candidate.raw_fields[field] || '—' }}</span>
                </div>
                <NInput v-model:value="values[field]" :placeholder="'当前: ' + (current[field] || '空白')" :disabled="processing" />
              </label>
            </div>

            <div class="reason-section">
              <label>修改依据（必填）
                <NInput v-model:value="reason" placeholder="请输入修改说明..." :disabled="processing" />
              </label>
              <div class="quick-pills">
                <NButton v-for="pill in quickReasons" :key="pill" size="tiny" secondary type="info" :disabled="processing" @click="reason = pill">
                  {{ pill }}
                </NButton>
              </div>
            </div>

            <div class="actions-row">
              <NButton type="primary" attr-type="submit" :loading="processing" :disabled="processing || !Object.keys(differences).length">
                一键保存修改草稿
              </NButton>
              <NButton v-if="Object.keys(differences).length && reason.trim()" secondary :disabled="processing" @click="inspect">
                预览变动
              </NButton>
            </div>
          </form>
        </section>

        <section v-if="plan" aria-label="解释修改预览" class="plan-preview">
          <h4>本次解释预览</h4>
          <ul><li v-for="(value, field) in plan.command.fields" :key="field">
            {{ sourceFieldLabels[field] }}：{{ current[field] || '空白' }} → {{ value || '空白' }}
          </li></ul>
          <p>{{ plan.record ? '本次解释可映射，保存后仍需整表预检。' : sourceReviewText(plan.detail) }}</p>
          <NButton :disabled="processing" type="primary" @click="save">确认保存草稿</NButton>
        </section>

        <details class="all-fields-details">
          <summary>展开全部 18 个底层字段（高级修改）</summary>
          <div class="matrix-table-wrap">
            <table class="fields-table">
              <thead><tr><th>字段</th><th>原文</th><th>当前</th><th>本次修改</th></tr></thead>
              <tbody>
                <tr v-for="(label, field) in sourceFieldLabels" :key="field">
                  <th>{{ label }}</th>
                  <td>{{ candidate.raw_fields[field] || '—' }}</td>
                  <td>{{ current[field] || '—' }}</td>
                  <td v-if="editable">
                    <input v-model="values[field]" :disabled="processing" />
                  </td>
                </tr>
              </tbody>
            </table>
          </div>
        </details>
      </div>

      <template #footer>
        <div class="drawer-footer">
          <NButton v-if="editable" size="small" secondary @click="emit('review', candidate.id)">核对本原格争议</NButton>
          <NButton size="small" @click="emit('close')">关闭抽屉</NButton>
        </div>
      </template>
    </NDrawerContent>
  </NDrawer>
</template>

<style scoped>
.drawer-header { display: flex; align-items: center; justify-content: space-between; width: 100%; gap: 12px; }
.header-title { display: flex; align-items: center; gap: 8px; font-weight: 600; font-size: 1.05rem; }
.coord-badge { background: #e0f2fe; color: #0369a1; padding: 2px 8px; border-radius: 4px; font-family: monospace; font-size: 0.9rem; }
.nav-buttons { display: flex; gap: 6px; }
.drawer-body { display: flex; flex-direction: column; gap: 16px; }
.orig-card { background: #f8fafc; border: 1px solid #e2e8f0; border-radius: 6px; padding: 12px; }
.card-label { font-size: 0.8rem; font-weight: 600; color: #64748b; text-transform: uppercase; margin-bottom: 4px; }
.card-text { font-size: 0.95rem; line-height: 1.5; color: #1e293b; white-space: pre-wrap; }
.comment-text { margin-top: 6px; font-size: 0.88rem; color: #d97706; }
.focused-fields-section { background: #ffffff; border: 1px solid #e2e8e6; border-radius: 6px; padding: 14px; }
.focused-fields-section h4 { margin: 0 0 12px; font-size: 0.95rem; color: #0f172a; }
.field-row { margin-bottom: 12px; }
.field-meta { display: flex; justify-content: space-between; margin-bottom: 4px; font-size: 0.85rem; }
.field-title { font-weight: 600; color: #334155; }
.field-raw { color: #64748b; }
.reason-section { margin: 12px 0; }
.quick-pills { display: flex; flex-wrap: wrap; gap: 6px; margin-top: 6px; }
.actions-row { display: flex; gap: 10px; margin-top: 14px; }
.plan-preview { background: #f0fdf4; border: 1px solid #bbf7d0; border-radius: 6px; padding: 12px; }
.all-fields-details { margin-top: 8px; border: 1px solid #e2e8f0; border-radius: 6px; padding: 8px 12px; }
.all-fields-details summary { cursor: pointer; font-size: 0.88rem; color: #475569; font-weight: 500; }
.matrix-table-wrap { max-height: 260px; overflow-y: auto; margin-top: 8px; }
.fields-table { width: 100%; border-collapse: collapse; font-size: 0.85rem; }
.fields-table th, .fields-table td { border: 1px solid #e2e8f0; padding: 4px 8px; text-align: left; }
.fields-table input { width: 100%; padding: 2px 4px; font: inherit; }
.drawer-footer { display: flex; justify-content: flex-end; gap: 8px; width: 100%; }
</style>
