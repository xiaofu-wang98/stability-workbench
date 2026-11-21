<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue';
import { NAlert, NButton, NButtonGroup, NInput, NModal, NSelect, NTag, useMessage } from 'naive-ui';
import { api } from './api';
import { cellAddress, sourceFieldLabels as fieldNames, type Revision } from './sourceReviewText';
import type { SourceCandidate } from './CandidateSourceEditor.vue';

const props = defineProps<{
  source: string;
  section: string;
  project: string;
  excludeCandidate?: string;
  refreshing: boolean;
  candidates: Record<string, SourceCandidate>;
  focus?: string;
  refreshToken?: number;
}>();

const emit = defineEmits<{
  saved: [];
  cancel: [];
  history: [candidate: string, approve: boolean];
  receive: [];
  edit: [id: string];
}>();

const message = useMessage();

export type QuickChoice = {
  label: string;
  fields?: Record<string, string>;
  operation?: string;
  default_reason?: string;
  role?: string;
  requires_input?: boolean;
  input_fields?: string[];
};
export type TaskMember = { candidate_id: string; coordinate: string; time_point: string; batch: string; current_value: string; raw_value: string };
export type GuidanceOption = string | { value: string; label?: string };
export type TaskCard = {
  task_id: string;
  code: string;
  category?: string;
  title: string;
  detail_text: string;
  detail?: string;
  exception?: string | null;
  source_cells: { coordinate: string; text: string; comment?: string }[];
  affected_count: number;
  members: TaskMember[];
  fields: string[];
  quick_choices: QuickChoice[];
  manual_entry?: { type: string; label: string; fields?: string[]; candidate_id?: string } | null;
  proposal: { eligible?: boolean; operation?: string; identity_options?: Record<string, string> };
  actions: string[];
  can_exclude_members: boolean;
  needs_history_review?: boolean;
  needs_approval?: boolean;
  primary_candidate_id?: string;
  needs_advanced_tool?: boolean;
  revisions?: Record<string, Revision>;
  guidance?: { title?: string; question?: string; help?: string; name_options?: GuidanceOption[] } | null;
};

type Group = { group_id: string; title: string; reason: string; scope: object; decision_count: number };
type ManualTask = {
  id: string; detail: string; fields: string[]; affected_results: number;
  member_ids: string[];
  evidence: { section: string; row: number; column: number }[];
  identity_options?: Record<string, string>;
  guidance?: { question: string; help: string } | null;
};
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
const taskCards = ref<TaskCard[]>([]);

const cardSelectedChoice = ref<Record<string, number>>({});
const cardReasons = ref<Record<string, string>>({});
const cardCustomFields = ref<Record<string, Record<string, string>>>({});
const cardExceptions = ref<Record<string, string[]>>({});

const identityEnumOptions: Record<string, { value: string; label: string }[]> = {
  sample_type: [
    { value: 'DS', label: 'DS（原液）' },
    { value: 'DP', label: 'DP（制剂）' },
    { value: 'placebo', label: 'placebo（安慰剂）' },
  ],
  study: [
    { value: 'long_term', label: 'long_term（长期）' },
    { value: 'accelerated', label: 'accelerated（加速）' },
    { value: 'stress', label: 'stress（强力破坏）' },
    { value: 'release', label: 'release（放行）' },
  ],
  purpose: [
    { value: 'routine', label: 'routine（常规稳定性）' },
    { value: 'reference', label: 'reference（参考品）' },
    { value: 'unknown', label: 'unknown（未注明）' },
  ],
};

const revisionFieldOrder = ['result', 'test_item', 'criterion', 'subresult', 'method'];

function cardPrimaryRevision(card: TaskCard): Revision | undefined {
  const revs = card.revisions;
  if (!revs) return undefined;
  for (const f of [...(card.fields || []), ...revisionFieldOrder]) {
    if (revs[f]?.segments?.length) return revs[f];
  }
  return Object.values(revs).find(r => r?.segments?.length);
}

function cardRevisionSegments(card: TaskCard) {
  return cardPrimaryRevision(card)?.segments || [];
}

function activeTextForField(card: TaskCard, field: string): string {
  const rev = card.revisions?.[field] || cardPrimaryRevision(card);
  if (!rev) return '';
  if (rev.active_text?.trim()) return rev.active_text.trim();
  return (rev.segments || []).filter(s => !s.struck).map(s => s.text).join('').trim();
}

function guidanceOptionValue(opt: GuidanceOption): string {
  return typeof opt === 'string' ? opt : opt.value;
}

function guidanceOptionLabel(opt: GuidanceOption): string {
  return typeof opt === 'string' ? opt : (opt.label || opt.value);
}

const plan = ref<Plan>();
const selected = ref<Group>();
const error = ref('');
const processing = ref(false);
const busy = computed(() => props.refreshing || processing.value);
const reason = ref('');
const base = '/previews/' + encodeURIComponent(props.source) + '/v5';
let key = '';

type Summary = { unreceived_candidates: number; pending_receipt: boolean; tasks: number; affected_candidates: number;
  categories: Record<string, { references: number; tasks: number; candidates: number }> };
const summary = ref<Summary>();
const total = ref(0);
const taskCardsTotal = ref(0);
const pageSize = 25;
const positionKey = 'source-review:' + JSON.stringify([props.source, props.section]);
const category = ref(props.focus ? '' : sessionStorage.getItem(positionKey + ':category') || '');
const offset = ref(props.focus ? 0 : Number(sessionStorage.getItem(positionKey + ':offset')) || 0);
const batchMembers = ref<Record<string, string[]>>({});
const batchExcluded = ref<string[]>([]);
const undoBatch = ref<string>();
const projectRevision = ref(0);
const undoModalVisible = ref(false);
const undoReason = ref('');

const categoryLabels: Record<string, string> = {
  '': '全部',
  'source_revision': '删除线／删',
  'deleted_source': '整格删除',
  'source_comment': '批注与解释',
  'identity': '来源身份',
  'identity_ambiguity': '检项歧义',
  'identity_conflict': '双语身份冲突',
  'method_conflict': '方法中英冲突',
  'numeric_format': '数字格式',
  'formula_result': '公式结果',
  'row_exclusion': '整行不采用建议',
  'region': '来源区域',
  'unprojected_region': '未投影区域',
  'history': '历史版本',
  'approval': '待复核',
  'structure': '排版结构',
};

const reasonPills = [
  '原表英文标注笔误，以中文标注为准',
  '以原表英文标注为准',
  '核对未划线正文无误',
  '核对批注确认有效',
  '确认为无效记录，不予采用',
];

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
  const result = await api<{
    groups: Group[]; manual: ManualTask[]; blockers: Blocker[]; summary: Summary; total: number;
    offset: number; batch_members: Record<string, string[]>; undo_batch_id?: string; base_revision: number;
    task_cards?: TaskCard[]; task_cards_total?: number;
  }>(
    base + '/review-actions?' + new URLSearchParams({
      section: props.section, category: category.value, focus,
      limit: String(pageSize), offset: String(offset.value),
    })
  );
  groups.value = result.groups;
  tasks.value = result.manual;
  blockers.value = result.blockers;
  summary.value = result.summary;
  total.value = result.total;
  offset.value = result.offset;
  batchMembers.value = result.batch_members;
  undoBatch.value = result.undo_batch_id;
  projectRevision.value = result.base_revision;

  if (result.task_cards) {
    taskCards.value = result.task_cards;
    for (const c of result.task_cards) {
      if (!Array.isArray(cardExceptions.value[c.task_id])) {
        cardExceptions.value[c.task_id] = [];
      }
      if (!cardCustomFields.value[c.task_id]) {
        cardCustomFields.value[c.task_id] = {};
      }
    }
  }
  taskCardsTotal.value = result.task_cards_total ?? result.task_cards?.length ?? 0;

  sessionStorage.setItem(positionKey + ':category', category.value);
  sessionStorage.setItem(positionKey + ':offset', String(offset.value));
}

function selectCategory(cat: string) {
  category.value = cat;
  offset.value = 0;
  void loadQueue();
}

function prevPage() {
  offset.value = Math.max(0, offset.value - pageSize);
  void loadQueue();
}

function nextPage() {
  offset.value += pageSize;
  void loadQueue();
}

async function previewExcludeCandidate(candidateId: string) {
  const group: Group = {
    group_id: candidateId, title: '核对所选原格的排除范围', reason: '', decision_count: 1,
    scope: { section: props.section, candidate_ids: [candidateId], operation: 'exclude_selected' },
  };
  selected.value = group;
  reason.value = '';
  batchExcluded.value = [];
  key = crypto.randomUUID();
  plan.value = await post('/tasks/preview', group.scope);
}

onMounted(() => run(async () => {
  await loadQueue(props.focus);
  if (props.excludeCandidate) {
    await previewExcludeCandidate(props.excludeCandidate);
  }
}));

watch(() => props.excludeCandidate, (cid) => {
  if (cid) void run(() => previewExcludeCandidate(cid));
});

watch(() => props.focus, (val) => {
  if (val) {
    category.value = '';
    offset.value = 0;
    void run(() => loadQueue(val));
  }
});

watch(() => props.refreshToken, () => {
  void loadQueue();
});

const hasDirtyDraft = computed(() => Object.values(cardSelectedChoice.value).some(v => v !== undefined));

defineExpose({ reload: loadQueue, hasDirtyDraft });

function selectChoice(card: TaskCard, idx: number) {
  cardSelectedChoice.value[card.task_id] = idx;
  const choice = card.quick_choices[idx];
  cardReasons.value[card.task_id] = choice.default_reason || cardReasons.value[card.task_id] || '';
  cardCustomFields.value[card.task_id] = { ...(choice.fields || {}) };
}

function selectGuidanceOption(card: TaskCard, opt: GuidanceOption) {
  const val = guidanceOptionValue(opt);
  const existingIdx = card.quick_choices.findIndex(q => q.fields?.test_item === val);
  if (existingIdx !== -1) {
    selectChoice(card, existingIdx);
    return;
  }
  switchToManual(card);
  cardCustomFields.value[card.task_id] = { ...(cardCustomFields.value[card.task_id] || {}), test_item: val };
  if (!cardReasons.value[card.task_id]?.trim()) {
    cardReasons.value[card.task_id] = `核对说明，指定分项为${val}`;
  }
}

function switchToManual(card: TaskCard) {
  if (!card.fields.length && card.primary_candidate_id) {
    emit('edit', card.primary_candidate_id);
    return;
  }
  let mIdx = card.quick_choices.findIndex(q => q.operation === 'manual' && q.requires_input);
  if (mIdx === -1) {
    mIdx = card.quick_choices.findIndex(q => q.operation === 'manual' && !Object.keys(q.fields || {}).length);
  }
  if (mIdx === -1) {
    card.quick_choices.push({
      label: '填写人工解释',
      operation: 'manual',
      requires_input: true,
      fields: {},
      default_reason: '核对原件后人工输入解释',
    });
    mIdx = card.quick_choices.length - 1;
  }
  selectChoice(card, mIdx);
}

function canOfferManualButton(card: TaskCard): boolean {
  if (card.quick_choices.some(q => q.operation === 'manual' && q.requires_input)) return false;
  return card.actions.includes('manual') || card.actions.includes('edit') || Boolean(card.exception);
}

async function applyTaskCard(card: TaskCard) {
  const choiceIdx = cardSelectedChoice.value[card.task_id];
  if (choiceIdx === undefined) {
    message.warning('请先点击上方快捷选项或输入人工解释');
    return;
  }
  const choice = card.quick_choices[choiceIdx];
  const op = (choice?.operation as any) || 'manual';
  const rawFields = cardCustomFields.value[card.task_id] || choice?.fields || {};
  const fields = op === 'manual'
    ? Object.fromEntries(Object.entries(rawFields).filter(([, v]) => typeof v === 'string' && v.trim() !== ''))
    : rawFields;
  if (op === 'manual' && !Object.keys(fields).length) {
    message.warning('请填写至少一个需要确认的字段值');
    return;
  }
  const reasonText = cardReasons.value[card.task_id]?.trim() || choice?.default_reason || '核对原件后保存决定';
  const rawExcluded = cardExceptions.value[card.task_id];
  const excluded = Array.isArray(rawExcluded) ? rawExcluded : [];

  await run(async () => {
    const scope: Record<string, unknown> = {
      section: props.section,
      task_ids: [card.task_id],
      operation: op,
      fields,
      exclude_candidate_ids: excluded,
    };
    if (op === 'classify') {
      scope.role = choice?.role || 'auxiliary';
    }
    const previewResult = await post<{ base_revision: number; fingerprint: string; changes: any[] }>('/tasks/preview', scope);
    if (!previewResult.changes?.length) {
      throw new Error('未检测到需要生效的改动，请核对所选选项或参数。');
    }
    await post('/tasks/apply', {
      base_revision: previewResult.base_revision,
      fingerprint: previewResult.fingerprint,
      reason: reasonText,
      idempotency_key: crypto.randomUUID(),
      scope,
    });
    if (excluded.length === 0) {
      resolvedCardsHistory.value.push(card);
    }
    delete cardSelectedChoice.value[card.task_id];
    cardExceptions.value[card.task_id] = [];
    const prevCount = taskCardsTotal.value || activeCards.value.length;
    const newCount = excluded.length > 0 ? prevCount : Math.max(0, prevCount - 1);
    const resolvedCount = Math.max(0, card.affected_count - excluded.length);
    const coord = card.source_cells[0]?.coordinate || '来源格';
    try {
      message.success(`已保存 ${coord}（合并解决 ${resolvedCount} 条），剩余待核对：${prevCount} → ${newCount}`);
    } catch {}
    emit('saved');
  });
}

const resolvedCardsHistory = ref<TaskCard[]>([]);
// Authoritative source is always loadQueue()'s taskCards; never filter by a client-side blacklist.
const activeCards = computed(() => taskCards.value);
const resolvedCards = computed(() => {
  const seen = new Set<string>();
  const list: TaskCard[] = [];
  for (const c of resolvedCardsHistory.value) {
    if (!seen.has(c.task_id)) {
      seen.add(c.task_id);
      list.push(c);
    }
  }
  return list;
});

function inspect(group: Group) {
  return run(async () => {
    plan.value = undefined;
    selected.value = group;
    reason.value = '';
    batchExcluded.value = [];
    key = crypto.randomUUID();
    plan.value = await post('/tasks/preview', group.scope);
  });
}

async function toggleBatchException(cid: string) {
  if (batchExcluded.value.includes(cid)) {
    batchExcluded.value = batchExcluded.value.filter(id => id !== cid);
  } else {
    batchExcluded.value.push(cid);
  }
  if (selected.value) {
    const scope = { ...selected.value.scope, exclude_candidate_ids: batchExcluded.value };
    plan.value = await post('/tasks/preview', scope);
  }
}

function saveBatch() {
  if (!selected.value || !plan.value?.changes.length || !reason.value.trim()) return;
  const scope = { ...selected.value.scope, exclude_candidate_ids: batchExcluded.value };
  const { base_revision, fingerprint } = plan.value;
  return run(async () => {
    await post('/tasks/apply', { scope, base_revision, fingerprint, reason: reason.value.trim(), idempotency_key: key });
    try { message.success('已批量应用所选处理'); } catch {}
    plan.value = undefined;
    selected.value = undefined;
    batchExcluded.value = [];
    emit('saved');
  });
}

function openUndoModal() {
  undoReason.value = '撤销上次来源批量处理';
  undoModalVisible.value = true;
}
function confirmUndo() {
  if (!undoReason.value.trim() || !undoBatch.value) return;
  return run(async () => {
    await post('/tasks/undo', {
      batch_id: undoBatch.value,
      base_revision: projectRevision.value,
      idempotency_key: crypto.randomUUID(),
      reason: undoReason.value.trim(),
    });
    undoModalVisible.value = false;
    resolvedCardsHistory.value = [];
    try { message.success('已撤销上一次批量处理'); } catch {}
    emit('saved');
  });
}
</script>

<template>
  <section class="panel import-source-review" tabindex="-1">
    <div class="review-top-bar">
      <div class="top-meta">
        <h2>工作表来源就地核对 · {{ section }}</h2>
        <span class="counts-badge">
          待核对独立任务 {{ taskCardsTotal || activeCards.length }} 项 · 影响 {{ summary?.affected_candidates ?? 0 }} 条结果
        </span>
      </div>
      <div class="top-actions">
        <NButton v-for="group in groups" :key="group.group_id" secondary type="warning" size="small" :disabled="busy" @click="inspect(group)">
          批量处理：{{ group.title }} ({{ group.decision_count }})
        </NButton>
        <NButton v-if="undoBatch" size="small" secondary type="error" :disabled="busy" @click="openUndoModal">
          撤销上一次批量处理
        </NButton>
        <NButton size="small" :disabled="busy" @click="emit('cancel')">返回原件概要</NButton>
      </div>
    </div>

    <!-- Category Filter Bar -->
    <div v-if="summary?.categories && Object.keys(summary.categories).length > 1" class="category-filter-bar">
      <span class="filter-label">分类筛选：</span>
      <div class="category-pills">
        <NButton
          size="small"
          :type="category === '' ? 'primary' : 'default'"
          :secondary="category !== ''"
          @click="selectCategory('')"
        >全部 ({{ summary?.tasks ?? total }})</NButton>
        <NButton
          v-for="(info, cat) in summary.categories"
          :key="cat"
          size="small"
          :type="category === cat ? 'primary' : 'default'"
          :secondary="category !== cat"
          @click="selectCategory(cat)"
        >
          {{ categoryLabels[cat] || cat }} ({{ info.tasks }})
        </NButton>
      </div>
    </div>

    <!-- Unreceived Alert -->
    <NAlert v-if="summary?.pending_receipt || summary?.unreceived_candidates" type="warning" class="unreceived-banner" style="margin-bottom: 12px;">
      <div style="display: flex; justify-content: space-between; align-items: center; width: 100%;">
        <span>当前工作表尚有 {{ summary.unreceived_candidates || '若干' }} 条候选结果未接收供核对</span>
        <NButton size="small" type="warning" @click="emit('receive')">立即接收候选</NButton>
      </div>
    </NAlert>

    <NAlert v-if="error" type="error" role="alert" style="margin-bottom: 12px;">{{ error }}</NAlert>

    <!-- Pagination Bar (Top) -->
    <div v-if="taskCardsTotal > pageSize || offset > 0" class="pagination-bar">
      <span>第 {{ Math.floor(offset / pageSize) + 1 }} / {{ Math.ceil(taskCardsTotal / pageSize) || 1 }} 页 (共 {{ taskCardsTotal }} 项待核对任务)</span>
      <NButtonGroup>
        <NButton size="small" :disabled="offset <= 0 || busy" @click="prevPage">上一页</NButton>
        <NButton size="small" :disabled="offset + pageSize >= taskCardsTotal || busy" @click="nextPage">下一页</NButton>
      </NButtonGroup>
    </div>

    <!-- Top batch plan preview modal/section if active -->
    <div v-if="plan" class="batch-preview-box">
      <h3>批量处理预览 · {{ selected?.title }} ({{ plan.changes.length }} 项变动)</h3>
      <p>{{ plan.notice }}</p>
      <div class="matrix">
        <table>
          <thead><tr><th>原件位置</th><th>处理说明</th><th>字段变动</th></tr></thead>
          <tbody>
            <tr v-for="change in plan.changes" :key="change.task_id">
              <td>{{ change.evidence.map(e => e.section + '!' + cellAddress(e.row, e.column)).join('；') || change.section }}</td>
              <td>{{ change.explanation }} · 影响 {{ change.affected_results }} 条</td>
              <td><div v-for="(field, name) in change.fields" :key="name">{{ name }} · {{ field.before }} → {{ field.after }}</div></td>
            </tr>
          </tbody>
        </table>
      </div>
      <div v-if="batchMembers[selected?.group_id || '']?.length > 1" class="batch-members-box">
        <details>
          <summary>排除个别候选（例外设置，已排除 {{ batchExcluded.length }} 项）</summary>
          <div class="member-checkboxes">
            <label v-for="cid in batchMembers[selected?.group_id || '']" :key="cid">
              <input type="checkbox" :checked="batchExcluded.includes(cid)" @change="toggleBatchException(cid)">
              排除此项：{{ cid }}
            </label>
          </div>
        </details>
      </div>
      <div class="batch-confirm-row">
        <label>本次处理依据：
          <NInput v-model:value="reason" placeholder="请输入核对依据..." :disabled="busy" style="min-width: 240px;" />
        </label>
        <NButton type="primary" :disabled="busy || !plan.changes.length || !reason.trim()" :loading="busy" @click="saveBatch">
          确认应用批量决定
        </NButton>
        <NButton text :disabled="busy" @click="plan = undefined; selected = undefined;">取消</NButton>
      </div>
    </div>

    <!-- Main List: One Root-Cell = One Card -->
    <div class="task-cards-list">
      <div v-for="card in activeCards" :key="card.task_id" class="task-card">
        <div class="card-header">
          <div class="card-badges">
            <span class="coord-badge">{{ card.source_cells[0]?.coordinate || '来源格' }}</span>
            <NTag size="small" type="warning">{{ card.code === 'identity_conflict' ? '双语冲突' : card.code === 'source_revision' ? '划线修订' : card.code === 'source_comment' ? '批注' : '待核对' }}</NTag>
            <NTag size="small" type="info">影响 {{ card.affected_count }} 个时间点：{{ card.members.map(m => m.time_point).join(' / ') }}</NTag>
            <NButton v-if="card.needs_history_review" size="tiny" secondary type="warning" @click="emit('history', card.primary_candidate_id || '', card.needs_approval || false)">
              {{ card.needs_approval ? '复核历史采用' : '查看历史冲突' }}
            </NButton>
            <a v-if="card.needs_advanced_tool" class="advanced-tool-link" :href="'/legacy?view=ledger&project=' + encodeURIComponent(props.project) + '&upload=' + encodeURIComponent(props.source) + '&sheet=' + encodeURIComponent(props.section) + '&task=' + encodeURIComponent(card.task_id)">
              在原表处理工具打开本项
            </a>
            <NButton v-if="card.primary_candidate_id" size="tiny" secondary text @click="emit('edit', card.primary_candidate_id)">
              单格抽屉修改
            </NButton>
          </div>
          <h3 class="card-title">{{ card.title }}</h3>
        </div>

        <div class="card-body">
          <div class="source-evidence-box">
            <div class="evidence-text">
              <template v-if="cardRevisionSegments(card).length">
                <span v-for="(seg, sIdx) in cardRevisionSegments(card)" :key="sIdx">
                  <del v-if="seg.struck" class="struck-text">{{ seg.text }}</del>
                  <span v-else>{{ seg.text }}</span>
                </span>
              </template>
              <template v-else>
                {{ card.source_cells[0]?.text || '（原件单元格为空或无法直接读取）' }}
              </template>
            </div>
            <div v-if="card.source_cells[0]?.comment" class="evidence-comment">
              <strong>批注：</strong>{{ card.source_cells[0].comment }}
            </div>
          </div>
          <p class="card-detail">{{ card.detail_text }}</p>

          <NAlert v-if="card.exception" type="warning" class="card-exception-alert">
            须人工处理：{{ card.exception }}
          </NAlert>

          <div v-if="card.guidance" class="card-guidance-box">
            <div v-if="card.guidance.question" class="guidance-question">{{ card.guidance.question }}</div>
            <div v-if="card.guidance.help" class="guidance-help">{{ card.guidance.help }}</div>
            <div v-if="card.guidance.name_options?.length" class="guidance-options">
              <span class="guidance-opt-label">可选分项：</span>
              <NButton
                v-for="(opt, oIdx) in card.guidance.name_options"
                :key="oIdx"
                size="tiny"
                secondary
                type="info"
                :disabled="busy"
                @click="selectGuidanceOption(card, opt)"
              >
                {{ guidanceOptionLabel(opt) }}
              </NButton>
            </div>
          </div>

          <!-- Quick Choice Buttons -->
          <div class="quick-choices-bar">
            <span class="choice-prompt">快捷选择：</span>
            <NButtonGroup v-if="card.quick_choices.length">
              <NButton
                v-for="(choice, idx) in card.quick_choices"
                :key="idx"
                size="small"
                :type="cardSelectedChoice[card.task_id] === idx ? 'primary' : 'default'"
                :disabled="busy"
                @click="selectChoice(card, idx)"
              >
                {{ choice.label }}
              </NButton>
            </NButtonGroup>
            <NButton
              v-if="canOfferManualButton(card)"
              size="small"
              secondary
              type="info"
              :disabled="busy"
              @click="switchToManual(card)"
            >
              填写人工解释
            </NButton>
            <NButton
              v-else-if="!card.quick_choices.length && card.primary_candidate_id"
              size="small"
              secondary
              type="info"
              :disabled="busy"
              @click="emit('edit', card.primary_candidate_id)"
            >
              打开单格抽屉修改
            </NButton>
          </div>

          <!-- Edit & Reason Box within Card -->
          <div v-if="cardSelectedChoice[card.task_id] !== undefined" class="in-card-edit">
            <template v-if="card.quick_choices[cardSelectedChoice[card.task_id]]?.operation === 'manual'">
              <div v-for="f in card.fields" :key="f" class="field-input-row">
                <label>{{ fieldNames[f] || f }}</label>
                <NSelect
                  v-if="identityEnumOptions[f]"
                  :value="cardCustomFields[card.task_id][f] || null"
                  :options="identityEnumOptions[f]"
                  :placeholder="'请选择' + (fieldNames[f] || f)"
                  :disabled="busy"
                  clearable
                  @update:value="val => { cardCustomFields[card.task_id][f] = val || ''; }"
                />
                <NInput
                  v-else
                  v-model:value="cardCustomFields[card.task_id][f]"
                  :placeholder="fieldNames[f] || f"
                  :disabled="busy"
                />
                <NButton
                  v-if="!identityEnumOptions[f] && activeTextForField(card, f)"
                  size="tiny"
                  secondary
                  type="info"
                  :disabled="busy"
                  @click="cardCustomFields[card.task_id][f] = activeTextForField(card, f)"
                >
                  带入未划线原文
                </NButton>
              </div>
              <div v-if="card.fields.includes('result') && card.affected_count > 1" class="manual-result-warn">
                <span>结果值涉及多个时间点，须点击‘打开单格抽屉’逐格修改：</span>
                <NButton size="tiny" type="primary" secondary @click="emit('edit', card.primary_candidate_id || '')">打开单格抽屉</NButton>
              </div>
            </template>
            <template v-else>
              <div v-if="Object.keys(cardCustomFields[card.task_id] || {}).length" class="readonly-fields-preview">
                <span v-for="(v, k) in cardCustomFields[card.task_id]" :key="k">
                  {{ fieldNames[k] || k }}: <strong>{{ v }}</strong>
                </span>
              </div>
              <div class="switch-manual-row">
                <NButton text size="tiny" type="info" @click="switchToManual(card)">改为人工输入解释</NButton>
              </div>
            </template>

            <div class="reason-input-row">
              <label>核对依据（必填）</label>
              <NInput v-model:value="cardReasons[card.task_id]" placeholder="输入或点击下方快捷依据..." :disabled="busy" />
              <div class="capsules-row">
                <NButton v-for="pill in reasonPills" :key="pill" size="tiny" secondary type="info" :disabled="busy" @click="cardReasons[card.task_id] = pill">
                  {{ pill }}
                </NButton>
              </div>
            </div>

            <details v-if="card.can_exclude_members" class="exceptions-box">
              <summary>排除个别时间点（例外设置）</summary>
              <div class="member-checkboxes">
                <label v-for="m in card.members" :key="m.candidate_id">
                  <input type="checkbox" :value="m.candidate_id" v-model="cardExceptions[card.task_id]">
                  跳过 {{ m.time_point }}（行 {{ m.coordinate }} · 原值 {{ m.raw_value }}）
                </label>
              </div>
            </details>

            <div class="card-submit-row">
              <NButton type="primary" size="small" :disabled="busy || !cardReasons[card.task_id]?.trim()" :loading="busy" @click="applyTaskCard(card)">
                一次确认保存此项
              </NButton>
            </div>
          </div>
        </div>
      </div>

      <NAlert v-if="!activeCards.length && !taskCards.length" type="info">
        当前工作表没有待核对的来源任务，已全部就绪。
      </NAlert>
    </div>

    <!-- Pagination Bar (Bottom) -->
    <div v-if="taskCardsTotal > pageSize" class="pagination-bar" style="margin-top: 16px;">
      <span>第 {{ Math.floor(offset / pageSize) + 1 }} / {{ Math.ceil(taskCardsTotal / pageSize) || 1 }} 页</span>
      <NButtonGroup>
        <NButton size="small" :disabled="offset <= 0 || busy" @click="prevPage">上一页</NButton>
        <NButton size="small" :disabled="offset + pageSize >= taskCardsTotal || busy" @click="nextPage">下一页</NButton>
      </NButtonGroup>
    </div>

    <!-- Resolved group at bottom -->
    <details v-if="resolvedCards.length" class="resolved-group">
      <summary>本次已处理 ({{ resolvedCards.length }}) 项任务</summary>
      <div v-for="card in resolvedCards" :key="card.task_id" class="resolved-strip">
        <span class="resolved-text">✓ 已核对 · {{ card.source_cells[0]?.coordinate }} · {{ card.title }} (解决 {{ card.affected_count }} 条)</span>
      </div>
    </details>

    <!-- Modal for Undo -->
    <NModal v-model:show="undoModalVisible" preset="dialog" title="撤销上一次来源批量处理" positive-text="确认撤销" negative-text="取消" @positive-click="confirmUndo">
      <div style="margin-top: 12px;">
        <label>撤销依据：
          <NInput v-model:value="undoReason" placeholder="请输入撤销依据..." />
        </label>
        <div style="margin-top: 8px; display: flex; gap: 4px; flex-wrap: wrap;">
          <NButton size="tiny" secondary @click="undoReason = '核对发现前次批量处理有误，需撤回重核'">前次处理有误</NButton>
          <NButton size="tiny" secondary @click="undoReason = '恢复初始未处理状态重新核对'">重置重新核对</NButton>
        </div>
      </div>
    </NModal>
  </section>
</template>

<style scoped>
.import-source-review { padding: 20px 24px; background: var(--bg-surface); }
.review-top-bar {
  display: flex;
  justify-content: space-between;
  align-items: flex-start;
  gap: 16px;
  margin-bottom: 14px;
  padding-bottom: 14px;
  border-bottom: 1px solid var(--border);
}
.top-meta h2 { margin: 0 0 4px; font-size: 16px; font-weight: 700; color: var(--text-primary); }
.counts-badge { font-size: 13px; color: var(--text-secondary); font-weight: 500; font-variant-numeric: tabular-nums; }
.top-actions { display: flex; gap: 8px; flex-wrap: wrap; align-items: center; }

.category-filter-bar {
  display: flex;
  align-items: center;
  gap: 10px;
  margin-bottom: 14px;
  padding: 10px 14px;
  background: var(--bg-subtle);
  border: 1px solid var(--border);
  border-radius: var(--radius-md);
  flex-wrap: wrap;
}
.filter-label { font-size: 12.5px; font-weight: 600; color: var(--text-secondary); }
.category-pills { display: flex; gap: 6px; flex-wrap: wrap; }

.pagination-bar {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 14px;
  padding: 8px 12px;
  background: var(--bg-subtle);
  border: 1px solid var(--border);
  border-radius: var(--radius-sm);
  font-size: 13px;
  color: var(--text-secondary);
  font-variant-numeric: tabular-nums;
}

.batch-preview-box {
  background: var(--warning-bg);
  border: 1px solid var(--warning-border);
  border-radius: var(--radius-md);
  padding: 16px 18px;
  margin-bottom: 16px;
}
.batch-preview-box h3 { margin: 0 0 6px; color: var(--warning-fg); }
.batch-members-box {
  margin-top: 10px;
  border: 1px solid var(--warning-border);
  border-radius: var(--radius-sm);
  padding: 8px 12px;
  background: var(--bg-surface);
}
.batch-confirm-row { display: flex; align-items: center; gap: 12px; margin-top: 14px; flex-wrap: wrap; }

.task-cards-list { display: flex; flex-direction: column; gap: 14px; }
.task-card {
  background: var(--bg-surface);
  border: 1px solid var(--border);
  border-radius: 10px;
  padding: 16px 20px;
  box-shadow: var(--shadow-card);
  transition: border-color 0.15s ease, box-shadow 0.15s ease;
}
.task-card:hover { border-color: var(--border-strong); box-shadow: var(--shadow-raised); }

.card-header { display: flex; flex-direction: column; gap: 8px; margin-bottom: 12px; padding-bottom: 10px; border-bottom: 1px solid var(--border); }
.card-badges { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; }
.coord-badge {
  background: var(--brand-soft);
  color: var(--brand);
  border: 1px solid var(--brand-border);
  font-weight: 700;
  font-family: var(--font-mono);
  padding: 2px 8px;
  border-radius: var(--radius-sm);
  font-size: 12.5px;
}
.advanced-tool-link {
  font-size: 12.5px;
  font-weight: 500;
  color: var(--brand);
  padding: 2px 8px;
  border-radius: var(--radius-sm);
  background: var(--brand-soft);
}
.advanced-tool-link:hover { text-decoration: underline; }
.card-title { margin: 0; font-size: 15px; font-weight: 600; color: var(--text-primary); }

.card-body { display: flex; flex-direction: column; gap: 12px; }
.source-evidence-box {
  background: var(--bg-subtle);
  border: 1px solid var(--border);
  border-left: 3px solid var(--brand);
  border-radius: var(--radius-sm);
  padding: 10px 14px;
}
.evidence-text { font-family: var(--font-mono); font-size: 13.5px; line-height: 1.55; color: var(--text-primary); white-space: pre-wrap; font-weight: 500; }
.struck-text { color: var(--danger-fg); text-decoration: line-through; background-color: var(--danger-bg); padding: 1px 4px; border-radius: 3px; }
.evidence-comment { margin-top: 6px; padding-top: 6px; border-top: 1px dashed var(--border); font-size: 12.5px; color: var(--warning-fg); }

.card-detail { margin: 0; font-size: 13.5px; color: var(--text-secondary); line-height: 1.5; }
.card-exception-alert { margin-top: 2px; }
.card-guidance-box {
  background: var(--brand-soft);
  border: 1px solid var(--brand-border);
  border-radius: var(--radius-sm);
  padding: 10px 14px;
  display: flex;
  flex-direction: column;
  gap: 6px;
  font-size: 13px;
  color: var(--text-primary);
}
.guidance-question { font-weight: 600; color: var(--brand); }
.guidance-help { color: var(--text-secondary); font-size: 12.5px; }
.guidance-options { display: flex; align-items: center; gap: 6px; flex-wrap: wrap; }
.guidance-opt-label { font-size: 12.5px; font-weight: 600; color: var(--brand); }

.quick-choices-bar { display: flex; align-items: center; gap: 10px; margin-top: 2px; flex-wrap: wrap; }
.choice-prompt { font-size: 13px; font-weight: 600; color: var(--text-secondary); }

.in-card-edit {
  background: var(--bg-subtle);
  border: 1px solid var(--border);
  border-radius: var(--radius-md);
  padding: 14px 16px;
  margin-top: 4px;
  display: flex;
  flex-direction: column;
  gap: 12px;
}
.readonly-fields-preview { font-size: 13px; color: var(--text-primary); display: flex; gap: 14px; flex-wrap: wrap; }
.switch-manual-row { margin-bottom: 2px; }
.manual-result-warn { font-size: 12.5px; color: var(--warning-fg); display: flex; align-items: center; gap: 8px; }
.field-input-row { display: flex; align-items: center; gap: 10px; }
.field-input-row label { min-width: 72px; font-size: 13px; font-weight: 600; color: var(--text-secondary); }
.reason-input-row label { font-size: 13px; font-weight: 600; color: var(--text-secondary); margin-bottom: 6px; display: block; }
.capsules-row { display: flex; flex-wrap: wrap; gap: 6px; margin-top: 8px; }

.exceptions-box { border: 1px solid var(--border); border-radius: var(--radius-sm); padding: 8px 12px; background: var(--bg-surface); }
.exceptions-box summary { font-size: 12.5px; color: var(--text-secondary); }
.member-checkboxes { display: flex; flex-direction: column; gap: 5px; margin-top: 8px; font-size: 12.5px; color: var(--text-primary); }
.card-submit-row { display: flex; justify-content: flex-end; margin-top: 4px; }

.resolved-group { margin-top: 20px; border: 1px solid var(--success-border); background: var(--success-bg); border-radius: var(--radius-md); padding: 10px 14px; }
.resolved-group summary { font-size: 13px; font-weight: 600; color: var(--success-fg); }
.resolved-strip { display: flex; justify-content: space-between; align-items: center; padding: 6px 10px; background: var(--bg-surface); border: 1px solid var(--success-border); border-radius: var(--radius-sm); margin-top: 6px; font-size: 13px; }
.resolved-text { color: var(--success-fg); font-weight: 500; }
</style>