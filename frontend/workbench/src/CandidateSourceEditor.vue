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
const savedOverrides = ref<Record<string, string>>({});
const current = computed(() => ({ ...candidate.value.interpreted_fields, ...candidate.value.corrections, ...savedOverrides.value }));
const values = ref<Record<string, string>>({ ...current.value });
watch(() => props.candidate.id, () => {
  savedOverrides.value = {};
  plan.value = undefined;
  reason.value = '';
});
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

const revisionFieldOrder = ['result', 'test_item', 'criterion', 'subresult', 'method'];
const primaryRevision = computed<Revision | undefined>(() => {
  const revs = candidate.value.revisions;
  if (!revs) return undefined;
  for (const f of [...focusedFields.value, ...revisionFieldOrder]) {
    if (revs[f]?.segments?.length) return revs[f];
  }
  return Object.values(revs).find(r => r?.segments?.length);
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
    savedOverrides.value = { ...savedOverrides.value, ...preview.command.fields };
    plan.value = undefined;
    reason.value = '';
    try { message.success('已保存单元格修改草稿'); } catch {}
    emit('saved');
  });
}
const isDirty = computed(() => Object.keys(differences.value).length > 0);
defineExpose({ isDirty });

async function quickSave() {
  if (!editable.value || !Object.keys(differences.value).length || !reason.value.trim()) return;
  await inspect();
  if (plan.value?.record) await save();
}
</script>

<template>
  <NDrawer class="candidate-drawer" :show="true" :mask-closable="false" :show-mask="false" :trap-focus="false" :block-scroll="false" :width="'min(620px, 92vw)'" placement="right" :on-update:show="val => { if (!val) emit('close'); }">
    <NDrawerContent closable :on-close="() => emit('close')">
      <template #header>
        <div class="drawer-header">
          <div class="header-title">
            <span class="coord-badge" :title="candidate.location.section + '!' + cellAddress(candidate.location.row, candidate.location.column)">{{ candidate.location.section }}!{{ cellAddress(candidate.location.row, candidate.location.column) }}</span>
            <span class="header-title-text">{{ editable ? '核对/修改原格解释' : '查看原格' }}</span>
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
          <div class="card-text">
            <template v-if="primaryRevision?.segments?.length">
              <span v-for="(seg, idx) in primaryRevision.segments" :key="idx">
                <del v-if="seg.struck" class="struck-text">{{ seg.text }}</del>
                <span v-else>{{ seg.text }}</span>
              </span>
            </template>
            <template v-else>
              {{ sourceReviewText(candidate.detail, primaryRevision) }}
            </template>
          </div>
          <div v-if="candidate.comment" class="comment-text"><strong>批注：</strong>{{ candidate.comment }}</div>
        </div>

        <NAlert v-if="error" type="error" role="alert" style="margin-bottom: 12px;">{{ error }}</NAlert>

        <section v-if="editable && candidate.issues && candidate.issues.length" class="issues-section">
          <h4>待核对原格问题 ({{ candidate.issues.length }})</h4>
          <CandidateIssueReview v-for="issue in candidate.issues" :key="issue.id"
            :issue="issue" :source="source" :candidate="candidate.id" :candidate-revision="candidate.candidate_revision"
            :revision="revision" :current="current" @saved="emit('saved')" />
        </section>

        <section v-if="editable" class="focused-fields-section">
          <h4>快速修正出错字段 ({{ focusedFields.length }})</h4>
          <form @submit.prevent="quickSave" @input="plan = undefined" @change="plan = undefined">
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
                <NButton v-for="pill in quickReasons" :key="pill" size="tiny" secondary type="info" :disabled="processing" @click="reason = pill; plan = undefined">
                  {{ pill }}
                </NButton>
              </div>
            </div>

            <div class="actions-row">
              <NButton type="primary" attr-type="submit" :loading="processing" :disabled="processing || !Object.keys(differences).length || !reason.trim()">
                一键保存修改草稿
              </NButton>
              <NButton secondary :disabled="processing || !Object.keys(differences).length || !reason.trim()" @click="inspect">
                预览变动
              </NButton>
            </div>
          </form>
        </section>

        <section v-if="editable && plan" aria-label="解释修改预览" class="plan-preview">
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
              <thead><tr><th>字段</th><th>原文</th><th>当前</th><th v-if="editable">本次修改</th></tr></thead>
              <tbody>
                <tr v-for="(label, field) in sourceFieldLabels" :key="field">
                  <th>{{ label }}</th>
                  <td>{{ candidate.raw_fields[field] || '—' }}</td>
                  <td>{{ current[field] || '—' }}</td>
                  <td v-if="editable">
                    <input v-model="values[field]" :disabled="processing" @input="plan = undefined" />
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
.drawer-header { display: flex; align-items: center; justify-content: space-between; width: 100%; gap: 10px; min-width: 0; }
.header-title { display: flex; align-items: center; gap: 8px; font-weight: 600; font-size: 14.5px; color: var(--text-primary); min-width: 0; flex: 1; }
.header-title-text { white-space: nowrap; flex-shrink: 0; }
.coord-badge {
  background: var(--brand-soft);
  color: var(--brand);
  border: 1px solid var(--brand-border);
  padding: 2px 8px;
  border-radius: var(--radius-sm);
  font-family: var(--font-mono);
  font-size: 12px;
  font-weight: 700;
  max-width: 240px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.nav-buttons { display: flex; gap: 6px; flex-shrink: 0; }
.drawer-body { display: flex; flex-direction: column; gap: 16px; }

.orig-card {
  background: var(--bg-subtle);
  border: 1px solid var(--border);
  border-left: 3px solid var(--brand);
  border-radius: var(--radius-md);
  padding: 12px 14px;
}
.card-label { font-size: 11.5px; font-weight: 600; color: var(--text-muted); letter-spacing: 0.03em; margin-bottom: 5px; }
.card-text { font-family: var(--font-mono); font-size: 13.5px; line-height: 1.55; color: var(--text-primary); white-space: pre-wrap; }
.struck-text { color: var(--danger-fg); text-decoration: line-through; background-color: var(--danger-bg); padding: 1px 4px; border-radius: 3px; }
.comment-text { margin-top: 8px; padding-top: 6px; border-top: 1px dashed var(--border); font-size: 12.5px; color: var(--warning-fg); }

.issues-section h4,
.focused-fields-section h4,
.plan-preview h4 { margin: 0 0 12px; font-size: 14px; font-weight: 600; color: var(--text-primary); }
.focused-fields-section { background: var(--bg-surface); border: 1px solid var(--border); border-radius: var(--radius-md); padding: 16px; }
.field-row { margin-bottom: 12px; }
.field-meta { display: flex; justify-content: space-between; margin-bottom: 5px; font-size: 12.5px; }
.field-title { font-weight: 600; color: var(--text-secondary); }
.field-raw { color: var(--text-muted); font-family: var(--font-mono); font-size: 12px; }
.reason-section { margin: 14px 0; }
.reason-section label { font-size: 13px; font-weight: 600; color: var(--text-secondary); display: flex; flex-direction: column; gap: 5px; }
.quick-pills { display: flex; flex-wrap: wrap; gap: 6px; margin-top: 8px; }
.actions-row { display: flex; gap: 10px; margin-top: 14px; }

.plan-preview { background: var(--success-bg); border: 1px solid var(--success-border); border-radius: var(--radius-md); padding: 14px 16px; }
.plan-preview ul { margin: 0 0 8px; padding-left: 18px; font-size: 13px; }
.plan-preview p { margin: 0 0 10px; font-size: 13px; color: var(--text-secondary); }

.all-fields-details { border: 1px solid var(--border); border-radius: var(--radius-md); padding: 10px 14px; background: var(--bg-subtle); }
.all-fields-details summary { font-size: 13px; color: var(--text-secondary); font-weight: 600; }
.matrix-table-wrap { max-height: 280px; overflow-y: auto; margin-top: 10px; border: 1px solid var(--border); border-radius: var(--radius-sm); background: var(--bg-surface); }
.fields-table { width: 100%; border-collapse: collapse; font-size: 12.5px; }
.fields-table th, .fields-table td { border-bottom: 1px solid var(--border); border-right: 1px solid var(--border); padding: 6px 8px; text-align: left; min-width: 0; }
.fields-table input { width: 100%; min-height: 30px; padding: 3px 6px; font: inherit; border: 1px solid var(--border-strong); border-radius: 4px; }
.drawer-footer { display: flex; justify-content: flex-end; gap: 8px; width: 100%; }
</style>
