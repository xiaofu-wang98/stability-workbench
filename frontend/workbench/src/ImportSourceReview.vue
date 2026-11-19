<script setup lang="ts">
import { computed, nextTick, onMounted, ref } from 'vue';
import { NAlert, NButton } from 'naive-ui';
import { api } from './api';
import { cellAddress, sourceFieldLabels as fieldNames, sourceRevision, sourceReviewText as describeTask } from './sourceReviewText';
import type { SourceCandidate } from './CandidateSourceEditor.vue';

const props = defineProps<{ source: string; section: string; project: string; excludeCandidate?: string;
  refreshing: boolean; candidates: Record<string, SourceCandidate>; focus?: string }>();
const emit = defineEmits<{ saved: []; cancel: []; history: [candidate: string, approve: boolean]; receive: []; edit: [id: string] }>();
type Group = { group_id: string; title: string; reason: string; scope: object; decision_count: number };
type ManualTask = { id: string; detail: string; fields: string[]; affected_results: number;
  member_ids: string[];
  evidence: { section: string; row: number; column: number }[];
  identity_options?: Record<string, string>;
  guidance?: { question: string; help: string; suggestion_notice?: string;
    name_options?: { value: string; evidence: { location: { row: number; column: number }; time_point: string; result: string }[] }[] } | null };
type Change = { task_id: string; section: string; operation: string; explanation: string; affected_results: number;
  evidence: { section: string; row: number; column: number }[];
  fields: Record<string, { before: unknown; after: unknown }> };
type Plan = { base_revision: number; fingerprint: string; changes: Change[];
  exceptions: { section?: string; reason?: string; evidence?: Change['evidence'] }[]; notice: string };
type Blocker = { id: string; kind: string; section: string; region_summary: string; candidate_id: string | null;
  cells: { coordinate: string; text: string; comment?: string }[];
  tasks: { id: string; code: string; detail: string; actions: string[]; exception?: string;
    proposal: { eligible?: boolean; operation?: string; source_kind?: string } }[] };
const groups = ref<Group[]>([]);
const tasks = ref<ManualTask[]>([]);
const blockers = ref<Blocker[]>([]);
const manual = ref<ManualTask>();
const manualCandidate = ref<string>();
const manualForm = ref<HTMLFormElement>();
const planHeading = ref<HTMLElement>();
const manualValues = ref<Record<string, string>>({});
const visibleTasks = ref(20);
type Summary = { unreceived_candidates: number; pending_receipt: boolean; tasks: number; affected_candidates: number;
  categories: Record<string, { references: number; tasks: number; candidates: number }> };
const summary = ref<Summary>();
const total = ref(0);
const pageSize = 25;
const positionKey = 'source-review:' + JSON.stringify([props.source, props.section]);
const category = ref(props.focus ? '' : sessionStorage.getItem(positionKey + ':category') || '');
const offset = ref(props.focus ? 0 : Number(sessionStorage.getItem(positionKey + ':offset')) || 0);
const batchMembers = ref<Record<string, string[]>>({});
const exceptions = ref<string[]>([]);
const undoBatch = ref<string>();
const projectRevision = ref(0);
const listRoot = ref<HTMLOListElement>();
const fieldOptions: Record<string, [string, string][]> = {
  sample_type: [['DS', '原液'], ['DP', '制剂'], ['placebo', '安慰剂']],
  study: [['long_term', '长期'], ['accelerated', '加速'], ['stress', '强力破坏'], ['release', '放行']],
  purpose: [['routine', '常规样品'], ['reference', '参考品'], ['unknown', '未注明']],
};
const selected = ref<Group>();
const plan = ref<Plan>();
const error = ref('');
const processing = ref(false);
const busy = computed(() => props.refreshing || processing.value);
const reason = ref('');
const base = '/previews/' + encodeURIComponent(props.source) + '/v5';
let key = '';

function post<T>(suffix: string, body: object) {
  return api<T>(base + suffix, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) });
}
async function run(action: () => Promise<void>) {
  if (processing.value) return;
  error.value = '';
  processing.value = true;
  try { await action(); }
  catch (e) { error.value = (e as Error).message; }
  finally { processing.value = false; }
}
async function loadQueue(focus = '') {
  const result = await api<{ groups: Group[]; manual: ManualTask[]; blockers: Blocker[]; summary: Summary; total: number;
    offset: number; batch_members: Record<string, string[]>; undo_batch_id?: string; base_revision: number }>(
    base + '/review-actions?' + new URLSearchParams({ section: props.section, category: category.value, focus,
      limit: String(pageSize), offset: String(offset.value) }));
  groups.value = result.groups;
  tasks.value = result.manual;
  blockers.value = result.blockers;
  summary.value = result.summary;
  total.value = result.total;
  offset.value = result.offset;
  batchMembers.value = result.batch_members;
  undoBatch.value = result.undo_batch_id;
  projectRevision.value = result.base_revision;
  sessionStorage.setItem(positionKey + ':category', category.value);
  sessionStorage.setItem(positionKey + ':offset', String(offset.value));
  if (focus) {
    const index = blockers.value.findIndex(b => b.candidate_id === focus || b.tasks.some(task => task.id === focus));
    if (index >= 0) await focusCard(index);
  }
}
onMounted(() => run(async () => {
  await loadQueue(props.focus);
  if (props.excludeCandidate) {
    const group: Group = {
      group_id: props.excludeCandidate, title: '核对所选原格的排除范围', reason: '', decision_count: 1,
      scope: { section: props.section, candidate_ids: [props.excludeCandidate], operation: 'exclude_selected' },
    };
    selected.value = group;
    key = crypto.randomUUID();
    plan.value = await post('/tasks/preview', group.scope);
  }
}));
function movePage(step: number) {
  offset.value = Math.max(0, offset.value + step * pageSize);
  return run(loadQueue);
}
function filter(code: string) {
  category.value = code;
  offset.value = 0;
  return run(loadQueue);
}
async function focusCard(index: number) {
  await nextTick();
  const card = listRoot.value?.querySelectorAll('details').item(index);
  if (!card) return;
  card.open = true;
  card.querySelector('summary')?.focus({ preventScroll: true });
  card.scrollIntoView({ block: 'start' });
}
async function nextCard(index: number) {
  if (index + 1 < blockers.value.length) return focusCard(index + 1);
  if (offset.value + pageSize < total.value) await run(async () => {
    offset.value += pageSize;
    await loadQueue();
    await focusCard(0);
  });
}

const blockerLabels: Record<string, string> = { region: '区域用途未确认', unprojected_region: '结果区尚未映射',
  candidate: '原格结果或身份待核对', not_received: '来源尚未接收', approval: '决定待复核',
  stale_decision: '原决定已过期', missing_intent: '与历史结果不同', previous_changed: '历史版本已变化' };
const actionLabels: Record<string, string> = { classify: '确认区域用途', region_parse: '重解析区域', manual: '人工核对',
  edit: '明确身份', structure: '核对合并或拆分', decision: '核对历史结果', approve: '复核决定',
  keep_active: '核对未划线正文', keep_body: '核对保留正文', exclude: '核对不采用依据',
  receive: '接收来源', keep_single: '核对合并排版', refresh: '刷新预检' };
const categoryLabels: Record<string, string> = {
  source_revision: '删除线／删', identity: '来源身份', identity_conflict: '双语身份冲突', identity_ambiguity: '检项歧义',
  method_conflict: '方法冲突', source_comment: '批注与解释', region: '来源区域', unprojected_region: '结果区未映射', numeric_format: '数字格式',
  row_exclusion: '整行不采用建议', approval: '待复核', history: '历史版本', structure: '原表结构', formula_result: '公式结果',
};

function inspectTask(blocker: Blocker, task: Blocker['tasks'][number]) {
  const operation = blocker.kind === 'region' ? 'classify' : 'suggested';
  return inspect({ group_id: task.id, title: blockerLabels[blocker.kind] || '原件核对', reason: '', decision_count: 1,
    scope: { section: props.section, task_ids: [task.id], operation } });
}
function advanced(task: string) {
  return '/legacy?' + new URLSearchParams({ view: 'ledger', project: props.project, upload: props.source,
    sheet: props.section, task });
}

function chooseGroup(group: Group) {
  manual.value = undefined;
  exceptions.value = [];
  return inspect(group);
}

async function chooseManual(task: ManualTask, candidate?: string | null) {
  manual.value = task;
  manualCandidate.value = candidate || undefined;
  exceptions.value = [];
  manualValues.value = {};
  plan.value = undefined;
  selected.value = undefined;
  await nextTick();
  manualForm.value?.scrollIntoView({ block: 'start' });
}
function activeRevisionText(task: ManualTask) {
  return sourceRevision(task.detail)?.active_text || '';
}
function inspectManual() {
  const task = manual.value;
  if (!task) return;
  const fields = Object.fromEntries(task.fields.map(field => [field, manualValues.value[field]?.trim() || ''])
    .filter(([, value]) => value));
  if (!Object.keys(fields).length) { error.value = '请填写至少一个需要明确的字段。'; return; }
  const resultOnly = Object.hasOwn(fields, 'result') && task.member_ids.length > 1;
  if (resultOnly && !manualCandidate.value) { error.value = '结果值必须从具体候选任务逐格处理。'; return; }
  return inspect({ group_id: task.id, title: '人工核对', reason: '', decision_count: 1,
    scope: { section: props.section, task_ids: [task.id], operation: 'manual', fields,
      exclude_candidate_ids: resultOnly ? task.member_ids.filter(id => id !== manualCandidate.value) : exceptions.value } });
}
function previewWithExceptions() {
  if (selected.value) return inspect({ ...selected.value, scope: { ...selected.value.scope, exclude_candidate_ids: exceptions.value } });
}
function labelFor(id: string) {
  const c = props.candidates[id];
  return c ? c.location.section + '!' + cellAddress(c.location.row, c.location.column)
    + ' · ' + (c.interpreted_fields.time_point || '') : id;
}
function undoLastBatch() {
  const basis = window.prompt('撤销此次来源批处理的依据');
  if (!basis?.trim() || !undoBatch.value) return;
  return run(async () => {
    await post('/tasks/undo', { batch_id: undoBatch.value, base_revision: projectRevision.value,
      idempotency_key: crypto.randomUUID(), reason: basis.trim() });
    emit('saved');
  });
}
function inspect(group: Group) {
  return run(async () => {
    plan.value = undefined;
    selected.value = group;
    reason.value = '';
    key = crypto.randomUUID();
    plan.value = await post('/tasks/preview', group.scope);
    await nextTick();
    planHeading.value?.scrollIntoView({ block: 'start' });
  });
}
function save() {
  if (!selected.value || !plan.value?.changes.length || !reason.value.trim()) return;
  const scope = selected.value.scope;
  const { base_revision, fingerprint } = plan.value;
  return run(async () => {
    await post('/tasks/apply', { scope, base_revision, fingerprint, reason: reason.value.trim(), idempotency_key: key });
    emit('saved');
  });
}
function renewRequestKey() { key = crypto.randomUUID(); }
</script>

<template>
  <section class="panel">
    <h2>核对来源问题</h2>
    <NAlert v-if="error" type="error" role="alert">{{ error }}</NAlert>
    <p v-if="busy" role="status">正在读取或保存核对范围…</p>
    <template v-if="summary">
      <p v-if="summary.pending_receipt || summary.unreceived_candidates">
        待接收 {{ summary.unreceived_candidates }} 条候选<template v-if="summary.pending_receipt">，原件尚未接收</template>。
        <NButton text :disabled="busy" @click="emit('receive')">接收来源</NButton>
      </p>
      <h3>待核对 {{ summary.tasks }} 个独立任务 · 涉及 {{ summary.affected_candidates }} 条候选</h3>
      <div class="review-actions" aria-label="争议分类">
        <NButton :type="!category ? 'primary' : 'default'" :disabled="busy" @click="filter('')">全部</NButton>
        <NButton v-for="(counts, code) in summary.categories" :key="code" :type="category === code ? 'primary' : 'default'"
          :disabled="busy" @click="filter(String(code))">{{ categoryLabels[code] || code }} {{ counts.tasks }} 项 · {{ counts.candidates }} 条候选</NButton>
      </div>
      <p v-if="total">当前 {{ offset + 1 }}–{{ offset + blockers.length }} / {{ total }} 个引用。一个共享任务可涉及多个原格。</p>
      <p v-else>当前分类没有待核对引用。</p>
    </template>
    <ol ref="listRoot" class="blocker-list" :start="offset + 1">
      <li v-for="(blocker, index) in blockers" :key="blocker.id" class="source-blocker" :data-blocker="blocker.id">
        <details>
          <summary>查看原格 · {{ blockerLabels[blocker.kind] || blocker.kind }} · {{ blocker.section }}<template v-if="blocker.cells.length">
            · {{ blocker.cells.map(cell => cell.coordinate).join('、') }}</template></summary>
          <p v-if="blocker.region_summary">{{ blocker.region_summary }}</p>
          <p v-if="blocker.candidate_id && candidates[blocker.candidate_id]?.revisions?.result">
            {{ describeTask('', candidates[blocker.candidate_id]?.revisions?.result) }}
          </p>
          <dl class="original-cells"><template v-for="cell in blocker.cells" :key="cell.coordinate">
            <dt>{{ cell.coordinate }}</dt><dd>{{ cell.text || '空白' }}<div v-if="cell.comment">原件批注：{{ cell.comment }}</div></dd>
          </template></dl>
          <NButton v-if="blocker.candidate_id && candidates[blocker.candidate_id]?.allowed_actions.includes('edit')"
            text :disabled="busy" @click="emit('edit', blocker.candidate_id)">修改来源解释</NButton>
          <NButton v-if="blocker.candidate_id && candidates[blocker.candidate_id]?.allowed_actions.includes('withdraw')"
            text :disabled="busy" @click="chooseGroup({ group_id: '', title: '预览不采用本候选', reason: '', decision_count: 1,
              scope: { section, candidate_ids: [blocker.candidate_id], operation: 'exclude_selected' } })">不采用</NButton>
          <div v-for="task in blocker.tasks" :key="task.id">
            <p>{{ describeTask(task.detail) }}</p>
            <p v-if="task.proposal.source_kind === 'system_hint'">此条为系统解释建议，尚未确认。</p>
            <p v-if="task.exception">{{ task.exception }}</p>
            <p v-if="task.actions.length">可处理方式 · {{ task.actions.map(action => actionLabels[action] || action).join('、') }}</p>
            <NButton v-if="blocker.kind === 'not_received'" text :disabled="busy" @click="emit('receive')">接收来源</NButton>
            <NButton v-else-if="['approval', 'missing_intent', 'stale_decision', 'previous_changed'].includes(blocker.kind) && blocker.candidate_id"
              text :disabled="busy" @click="emit('history', blocker.candidate_id, blocker.kind === 'approval')">核对历史决定</NButton>
            <template v-else>
              <NButton v-if="task.proposal.eligible && !task.exception && blocker.kind !== 'unprojected_region'"
                text :disabled="busy" @click="inspectTask(blocker, task)">{{ blocker.kind === 'region' ? '预览确认辅助用途' : '按建议核对' }}</NButton>
              <NButton v-if="tasks.find(item => item.id === task.id)" text :disabled="busy"
                @click="chooseManual(tasks.find(item => item.id === task.id)!, blocker.candidate_id)">人工核对本项</NButton>
              <a v-if="task.actions.some(action => ['region_parse', 'structure', 'manual'].includes(action))"
                :href="advanced(task.id)">在原表处理工具打开本项</a>
            </template>
          </div>
          <NButton text :disabled="busy || offset + index + 1 >= total" @click="nextCard(index)">继续下一项</NButton>
        </details>
      </li>
    </ol>
    <div v-if="total > pageSize" class="review-actions" aria-label="核对分页">
      <NButton :disabled="busy || offset === 0" @click="movePage(-1)">上一页</NButton>
      <NButton :disabled="busy || offset + pageSize >= total" @click="movePage(1)">下一页</NButton>
    </div>
    <NButton v-if="undoBatch" :disabled="busy" @click="undoLastBatch">撤销本表上次处理</NButton>
    <div v-if="groups.length" class="review-actions">
      <NButton v-for="group in groups" :key="group.group_id" :disabled="busy" @click="chooseGroup(group)">{{ group.title }}</NButton>
    </div>
    <p v-else-if="!busy && !error">本表暂无可批量预览的建议。</p>
    <details v-if="tasks.length" :open="!groups.length">
      <summary>逐项核对身份与来源 · {{ tasks.length }} 项</summary>
      <ul class="list"><li v-for="task in tasks.slice(0, visibleTasks)" :key="task.id">
        <NButton text :disabled="busy" @click="chooseManual(task)">处理此项</NButton>
        <span>{{ describeTask(task.detail) }} · {{ task.evidence.map(e => '行 ' + e.row + ' 列 ' + e.column).join('；') }} · 影响 {{ task.affected_results }} 条</span>
      </li></ul>
      <NButton v-if="visibleTasks < tasks.length" :disabled="busy" @click="visibleTasks += 20">显示更多问题</NButton>
    </details>
    <form v-if="manual" ref="manualForm" class="review-actions" @submit.prevent="inspectManual" @input="plan = undefined" @change="plan = undefined">
      <p>{{ describeTask(manual.detail) }}</p>
      <p>{{ manual.evidence.map(e => e.section + ' · 行 ' + e.row + ' 列 ' + e.column).join('；') }} · 影响 {{ manual.affected_results }} 条</p>
      <div v-if="manual.guidance">
        <p>{{ manual.guidance.question }} {{ manual.guidance.help }}</p>
        <p v-if="manual.guidance.suggestion_notice">{{ manual.guidance.suggestion_notice }}</p>
        <template v-for="option in manual.guidance.name_options" :key="option.value">
          <NButton :disabled="busy" @click="manualValues.test_item = option.value; plan = undefined">
            采用同一原表行的检项 · {{ option.value }}
          </NButton>
          <ul><li v-for="item in option.evidence" :key="item.location.row + ':' + item.location.column">
            {{ item.time_point }} · 行 {{ item.location.row }} 列 {{ item.location.column }} · {{ item.result }}
          </li></ul>
        </template>
      </div>
      <div v-if="manual.fields.length === 1 && (manual.identity_options?.chinese || manual.identity_options?.english)">
        <p>请对照原件或检测记录，选择有依据的解释。</p>
        <NButton v-if="manual.identity_options?.chinese" :disabled="busy"
          @click="manualValues[manual.fields[0]] = manual.identity_options.chinese; plan = undefined">
          采用中文标注 · {{ manual.identity_options.chinese }}
        </NButton>
        <NButton v-if="manual.identity_options?.english" :disabled="busy"
          @click="manualValues[manual.fields[0]] = manual.identity_options.english; plan = undefined">
          采用英文标注 · {{ manual.identity_options.english }}
        </NButton>
      </div>
      <label v-for="field in manual.fields" :key="field">{{ fieldNames[field] || field }}
        <small v-if="manualCandidate && candidates[manualCandidate]">
          原文 {{ candidates[manualCandidate].raw_fields[field] || '—' }} ·
          当前解释 {{ candidates[manualCandidate].interpreted_fields[field] || '—' }}
        </small>
        <select v-if="fieldOptions[field]" v-model="manualValues[field]" :disabled="busy">
          <option value="">保持原值</option>
          <option v-for="[value, label] in fieldOptions[field]" :key="value" :value="value">{{ label }}</option>
        </select>
        <textarea v-else-if="field === 'result' || field === 'criterion'" v-model="manualValues[field]" rows="3" :disabled="busy"></textarea>
        <input v-else v-model="manualValues[field]" :disabled="busy">
      </label>
      <details v-if="manual.member_ids.length > 1">
        <summary>共享来源范围 · {{ manual.member_ids.length }} 条候选</summary>
        <p>修改共享身份可保留明确的例外。结果值只能从具体原格单独修正。</p>
        <label v-for="id in manual.member_ids" :key="id">
          <input v-model="exceptions" type="checkbox" :value="id"> 本次跳过 {{ labelFor(id) }}
        </label>
      </details>
      <NButton v-if="manual.fields.length === 1 && ['result', 'criterion'].includes(manual.fields[0]) && activeRevisionText(manual)"
        :disabled="busy" @click="manualValues[manual.fields[0]] = activeRevisionText(manual); plan = undefined">
        带入未划线原文
      </NButton>
      <NButton attr-type="submit" :disabled="busy">预览人工核对</NButton>
    </form>
    <details v-if="selected && batchMembers[selected.group_id]?.length" class="batch-exceptions">
      <summary>明确本次批量范围与例外 · {{ batchMembers[selected.group_id].length }} 条候选</summary>
      <label v-for="id in batchMembers[selected.group_id]" :key="id">
        <input v-model="exceptions" type="checkbox" :value="id" :disabled="busy" @change="plan = undefined"> 本次跳过 {{ labelFor(id) }}
      </label>
      <NButton :disabled="busy" @click="previewWithExceptions">按例外重新预览</NButton>
    </details>
    <template v-if="plan">
      <h3 ref="planHeading">{{ selected?.title }} · {{ plan.changes.length }} 项待核对</h3>
      <p>{{ plan.notice }}</p>
      <div class="matrix"><table>
        <thead><tr><th>原件位置</th><th>建议处理</th><th>原文与预览</th></tr></thead>
        <tbody><tr v-for="change in plan.changes" :key="change.task_id">
          <td>{{ change.evidence.map(e => e.section + ' 行 ' + e.row + ' 列 ' + e.column).join('；') || change.section }}</td>
          <td>{{ change.explanation }} · 影响 {{ change.affected_results }} 条</td>
          <td><div v-for="(field, name) in change.fields" :key="name">{{ name }} · {{ field.before }} → {{ field.after }}</div></td>
        </tr></tbody>
      </table></div>
      <details v-if="plan.exceptions.length"><summary>未纳入的例外 {{ plan.exceptions.length }} 项</summary>
        <ul><li v-for="(item, index) in plan.exceptions" :key="index">
          {{ item.evidence?.map(location => location.section + '!' + cellAddress(location.row, location.column)).join('、') || item.section }}
          · {{ item.reason || '此项须单独核对。' }}
        </li></ul>
      </details>
      <form class="review-actions" @submit.prevent="save">
        <label>本次核对依据 <input v-model="reason" required :disabled="busy" @input="renewRequestKey"></label>
        <NButton attr-type="submit" type="primary" :disabled="busy || !plan.changes.length || !reason.trim()">保存这些来源决定</NButton>
      </form>
    </template>
    <NButton :disabled="busy" @click="emit('cancel')">返回当前原件</NButton>
  </section>
</template>

<style scoped>
.review-actions { display: flex; flex-wrap: wrap; align-items: center; gap: 10px; margin: 12px 0; }
.blocker-list li { margin-bottom: 8px; overflow-wrap: anywhere; }
.original-cells { display: grid; grid-template-columns: max-content 1fr; gap: 4px 12px; }
.original-cells dd { margin: 0; white-space: pre-wrap; }
.batch-exceptions label { display: block; }
.review-actions small { display: block; max-width: 48ch; white-space: pre-wrap; }
</style>
