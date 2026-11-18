<script setup lang="ts">
import { computed, onMounted, ref } from 'vue';
import { NAlert, NButton } from 'naive-ui';
import { api } from './api';

const props = defineProps<{ source: string; section: string; project: string; excludeCandidate?: string; refreshing: boolean }>();
const emit = defineEmits<{ saved: []; cancel: []; history: [candidate: string, approve: boolean]; receive: [] }>();
type Group = { group_id: string; title: string; reason: string; scope: object; decision_count: number };
type ManualTask = { id: string; detail: string; fields: string[]; affected_results: number;
  evidence: { section: string; row: number; column: number }[];
  identity_options?: Record<string, string>;
  guidance?: { question: string; help: string; suggestion_notice?: string;
    name_options?: { value: string; evidence: { location: { row: number; column: number }; time_point: string; result: string }[] }[] } | null };
type Change = { task_id: string; section: string; operation: string; explanation: string; affected_results: number;
  evidence: { section: string; row: number; column: number }[];
  fields: Record<string, { before: unknown; after: unknown }> };
type Plan = { base_revision: number; fingerprint: string; changes: Change[]; exceptions: unknown[]; notice: string };
type Blocker = { id: string; kind: string; section: string; region_summary: string; candidate_id: string | null;
  cells: { coordinate: string; text: string; comment?: string }[];
  tasks: { id: string; detail: string; actions: string[]; exception?: string; proposal: { eligible?: boolean; operation?: string } }[] };
const groups = ref<Group[]>([]);
const tasks = ref<ManualTask[]>([]);
const blockers = ref<Blocker[]>([]);
const manual = ref<ManualTask>();
const manualValues = ref<Record<string, string>>({});
const visibleTasks = ref(20);
const fieldNames: Record<string, string> = { batch: '批号', presentation: '规格', sample_type: '材料类别',
  study: '考察类型', study_id: '方案编号', condition: '考察条件', test_item: '检项', method: '方法',
  criterion: '标准', result: '结果', unit: '单位', purpose: '用途', reporting_basis: '报告体系' };
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
onMounted(() => run(async () => {
  const result = await api<{ groups: Group[]; manual: ManualTask[]; blockers: Blocker[] }>(
    base + '/review-actions?' + new URLSearchParams({ section: props.section }));
  groups.value = result.groups;
  tasks.value = result.manual;
  blockers.value = result.blockers;
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

const blockerLabels: Record<string, string> = { region: '区域用途未确认', unprojected_region: '结果区尚未映射',
  candidate: '原格结果或身份待核对', not_received: '来源尚未接收', approval: '决定待复核',
  stale_decision: '原决定已过期', missing_intent: '与历史结果不同', previous_changed: '历史版本已变化' };
const actionLabels: Record<string, string> = { classify: '确认区域用途', region_parse: '重解析区域', manual: '人工核对',
  edit: '明确身份', structure: '核对合并或拆分', decision: '核对历史结果', approve: '复核决定',
  keep_active: '核对未划线正文', keep_body: '核对保留正文', exclude: '核对不采用依据',
  receive: '接收来源', keep_single: '核对合并排版', refresh: '刷新预检' };

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
  return inspect(group);
}
function describeTask(detail: string) {
  if (!detail.startsWith('{')) return detail;
  try {
    const revision = JSON.parse(detail) as { active_text?: string; segments?: { struck: boolean; text: string }[] };
    if (!Array.isArray(revision.segments)) return detail;
    const deleted = revision.segments.filter(segment => segment.struck).map(segment => segment.text.trim()).filter(Boolean);
    return '原格含删除线。保留文字：' + (revision.active_text?.trim() || '空白') +
      (deleted.length ? '。划掉的文字：' + deleted.join('、') : '') + '。请对照原件确认。';
  } catch {
    return detail;
  }
}

function chooseManual(task: ManualTask) {
  manual.value = task;
  manualValues.value = {};
  plan.value = undefined;
  selected.value = undefined;
}
function activeRevisionText(task: ManualTask) {
  try {
    const revision = JSON.parse(task.detail) as { active_text?: unknown; segments?: unknown };
    return Array.isArray(revision.segments) && typeof revision.active_text === 'string' ? revision.active_text : '';
  } catch {
    return '';
  }
}
function inspectManual() {
  const task = manual.value;
  if (!task) return;
  const fields = Object.fromEntries(task.fields.map(field => [field, manualValues.value[field]?.trim() || ''])
    .filter(([, value]) => value));
  if (!Object.keys(fields).length) { error.value = '请填写至少一个需要明确的字段。'; return; }
  return inspect({ group_id: task.id, title: '人工核对', reason: '', decision_count: 1,
    scope: { section: props.section, task_ids: [task.id], operation: 'manual', fields } });
}
function inspect(group: Group) {
  return run(async () => {
    plan.value = undefined;
    selected.value = group;
    reason.value = '';
    key = crypto.randomUUID();
    plan.value = await post('/tasks/preview', group.scope);
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
    <h3>预检阻断 · {{ blockers.length }} 项</h3>
    <ol class="blocker-list">
      <li v-for="blocker in blockers" :key="blocker.id" class="source-blocker" :data-blocker="blocker.id">
        <details>
          <summary>{{ blockerLabels[blocker.kind] || blocker.kind }} · {{ blocker.section }}<template v-if="blocker.cells.length">
            · {{ blocker.cells.map(cell => cell.coordinate).join('、') }}</template></summary>
          <p v-if="blocker.region_summary">{{ blocker.region_summary }}</p>
          <dl class="original-cells"><template v-for="cell in blocker.cells" :key="cell.coordinate">
            <dt>{{ cell.coordinate }}</dt><dd>{{ cell.text || '空白' }}<div v-if="cell.comment">原件批注：{{ cell.comment }}</div></dd>
          </template></dl>
          <div v-for="task in blocker.tasks" :key="task.id">
            <p>{{ describeTask(task.detail) }}</p>
            <p v-if="task.exception">{{ task.exception }}</p>
            <p v-if="task.actions.length">可处理方式 · {{ task.actions.map(action => actionLabels[action] || action).join('、') }}</p>
            <NButton v-if="blocker.kind === 'not_received'" text :disabled="busy" @click="emit('receive')">接收来源</NButton>
            <NButton v-else-if="['approval', 'missing_intent', 'stale_decision', 'previous_changed'].includes(blocker.kind) && blocker.candidate_id"
              text :disabled="busy" @click="emit('history', blocker.candidate_id, blocker.kind === 'approval')">核对历史决定</NButton>
            <template v-else>
              <NButton v-if="task.proposal.eligible && !task.exception && blocker.kind !== 'unprojected_region'"
                text :disabled="busy" @click="inspectTask(blocker, task)">{{ blocker.kind === 'region' ? '预览确认辅助用途' : '预览本项处理' }}</NButton>
              <NButton v-if="tasks.find(item => item.id === task.id)" text :disabled="busy"
                @click="chooseManual(tasks.find(item => item.id === task.id)!)">人工核对本项</NButton>
              <a v-if="task.actions.some(action => ['region_parse', 'structure', 'manual'].includes(action))"
                :href="advanced(task.id)">在原表处理工具打开本项</a>
            </template>
          </div>
        </details>
      </li>
    </ol>
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
    <form v-if="manual" class="review-actions" @submit.prevent="inspectManual" @input="plan = undefined" @change="plan = undefined">
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
        <select v-if="fieldOptions[field]" v-model="manualValues[field]" :disabled="busy">
          <option value="">保持原值</option>
          <option v-for="[value, label] in fieldOptions[field]" :key="value" :value="value">{{ label }}</option>
        </select>
        <textarea v-else-if="field === 'result' || field === 'criterion'" v-model="manualValues[field]" rows="3" :disabled="busy"></textarea>
        <input v-else v-model="manualValues[field]" :disabled="busy">
      </label>
      <NButton v-if="manual.fields.length === 1 && ['result', 'criterion'].includes(manual.fields[0]) && activeRevisionText(manual)"
        :disabled="busy" @click="manualValues[manual.fields[0]] = activeRevisionText(manual); plan = undefined">
        带入未划线原文
      </NButton>
      <NButton attr-type="submit" :disabled="busy">预览人工核对</NButton>
    </form>
    <template v-if="plan">
      <h3>{{ selected?.title }} · {{ plan.changes.length }} 项待核对</h3>
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
        <pre>{{ JSON.stringify(plan.exceptions, null, 2) }}</pre>
      </details>
      <form class="review-actions" @submit.prevent="save">
        <label>本次核对依据 <input v-model="reason" required :disabled="busy" @input="renewRequestKey"></label>
        <NButton attr-type="submit" type="primary" :disabled="busy || !plan.changes.length || !reason.trim()">保存这些来源决定</NButton>
      </form>
    </template>
    <NButton :disabled="busy" @click="emit('cancel')">关闭来源核对</NButton>
  </section>
</template>

<style scoped>
.review-actions { display: flex; flex-wrap: wrap; align-items: center; gap: 10px; margin: 12px 0; }
.blocker-list li { margin-bottom: 8px; overflow-wrap: anywhere; }
.original-cells { display: grid; grid-template-columns: max-content 1fr; gap: 4px 12px; }
.original-cells dd { margin: 0; white-space: pre-wrap; }
pre { white-space: pre-wrap; }
</style>
