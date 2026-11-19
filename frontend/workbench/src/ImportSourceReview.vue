<script setup lang="ts">
import { computed, nextTick, onMounted, ref, watch } from 'vue';
import { NAlert, NButton, NButtonGroup, NInput, NModal, NTag, useMessage } from 'naive-ui';
import { api } from './api';
import { cellAddress, sourceFieldLabels as fieldNames, sourceRevision, sourceReviewText as describeTask } from './sourceReviewText';
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

export type QuickChoice = { label: string; fields?: Record<string, string>; operation?: string; default_reason?: string };
export type TaskMember = { candidate_id: string; coordinate: string; time_point: string; batch: string; current_value: string; raw_value: string };
export type TaskCard = {
  task_id: string;
  code: string;
  category?: string;
  title: string;
  detail_text: string;
  source_cells: { coordinate: string; text: string; comment?: string }[];
  affected_count: number;
  members: TaskMember[];
  fields: string[];
  quick_choices: QuickChoice[];
  proposal: { eligible?: boolean; operation?: string; identity_options?: Record<string, string> };
  actions: string[];
  can_exclude_members: boolean;
  needs_history_review?: boolean;
  needs_approval?: boolean;
  primary_candidate_id?: string;
  needs_advanced_tool?: boolean;
  revisions?: { result?: { segments?: { text: string; struck?: boolean }[] } };
  guidance?: { question?: string; help?: string; name_options?: string[] } | null;
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
const resolvedTaskIds = ref<Set<string>>(new Set());

const cardSelectedChoice = ref<Record<string, number>>({});
const cardReasons = ref<Record<string, string>>({});
const cardCustomFields = ref<Record<string, Record<string, string>>>({});
const cardExceptions = ref<Record<string, string[]>>({});

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
  'identity_conflict': '双语身份冲突',
  'method_conflict': '方法中英冲突',
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

function switchToManual(card: TaskCard) {
  let mIdx = card.quick_choices.findIndex(q => q.operation === 'manual');
  if (mIdx === -1) {
    card.quick_choices.push({
      label: '人工输入解释',
      operation: 'manual',
      fields: Object.fromEntries(card.fields.map(f => [f, ''])),
      default_reason: '核对原件后人工输入解释',
    });
    mIdx = card.quick_choices.length - 1;
  }
  selectChoice(card, mIdx);
}

async function applyTaskCard(card: TaskCard) {
  const choiceIdx = cardSelectedChoice.value[card.task_id];
  if (choiceIdx === undefined) {
    message.warning('请先点击上方快捷选项或输入人工解释');
    return;
  }
  const choice = card.quick_choices[choiceIdx];
  const op = (choice?.operation as any) || 'manual';
  const fields = cardCustomFields.value[card.task_id] || choice?.fields || {};
  const reasonText = cardReasons.value[card.task_id]?.trim() || choice?.default_reason || '核对原件后保存决定';
  const rawExcluded = cardExceptions.value[card.task_id];
  const excluded = Array.isArray(rawExcluded) ? rawExcluded : [];

  await run(async () => {
    const scope = {
      section: props.section,
      task_ids: [card.task_id],
      operation: op,
      fields,
      exclude_candidate_ids: excluded,
    };
    const previewResult = await post<{ base_revision: number; fingerprint: string; changes: any[] }>('/tasks/preview', scope);
    if (!previewResult.changes?.length && op !== 'exclude') {
      throw new Error('未检测到需要生效的改动，请核对所选选项或参数。');
    }
    await post('/tasks/apply', {
      base_revision: previewResult.base_revision,
      fingerprint: previewResult.fingerprint,
      reason: reasonText,
      idempotency_key: crypto.randomUUID(),
      scope,
    });
    resolvedTaskIds.value.add(card.task_id);
    resolvedCardsHistory.value.push(card);
    delete cardSelectedChoice.value[card.task_id];
    const prevCount = taskCardsTotal.value || activeCards.value.length;
    const newCount = Math.max(0, prevCount - 1);
    const coord = card.source_cells[0]?.coordinate || '来源格';
    try {
      message.success(`已保存 ${coord}（合并解决 ${card.affected_count} 条），剩余待核对：${prevCount} → ${newCount}`);
    } catch {}
    emit('saved');
  });
}

const resolvedCardsHistory = ref<TaskCard[]>([]);
const activeCards = computed(() => taskCards.value.filter(c => !resolvedTaskIds.value.has(c.task_id)));
const resolvedCards = computed(() => {
  const fromHistory = resolvedCardsHistory.value;
  const current = taskCards.value.filter(c => resolvedTaskIds.value.has(c.task_id));
  const seen = new Set<string>();
  const list: TaskCard[] = [];
  for (const c of [...fromHistory, ...current]) {
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
            <a v-if="card.needs_advanced_tool" class="advanced-tool-link" :href="'#/legacy?view=ledger&project=' + encodeURIComponent(props.project) + '&upload=' + encodeURIComponent(props.source) + '&sheet=' + encodeURIComponent(props.section) + '&task=' + encodeURIComponent(card.task_id)">
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
              <template v-if="card.revisions?.result?.segments?.length">
                <span v-for="(seg, sIdx) in card.revisions.result.segments" :key="sIdx">
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

          <!-- Quick Choice Buttons -->
          <div class="quick-choices-bar">
            <span class="choice-prompt">快捷选择：</span>
            <NButtonGroup>
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
          </div>

          <!-- Edit & Reason Box within Card -->
          <div v-if="cardSelectedChoice[card.task_id] !== undefined" class="in-card-edit">
            <template v-if="card.quick_choices[cardSelectedChoice[card.task_id]]?.operation === 'manual'">
              <div v-for="f in card.fields" :key="f" class="field-input-row">
                <label>{{ fieldNames[f] || f }}</label>
                <NInput v-model:value="cardCustomFields[card.task_id][f]" :placeholder="fieldNames[f]" :disabled="busy" />
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
.import-source-review { padding: 16px; background: #ffffff; }
.review-top-bar { display: flex; justify-content: space-between; align-items: flex-start; margin-bottom: 12px; padding-bottom: 12px; border-bottom: 1px solid #e2e8f0; }
.top-meta h2 { margin: 0 0 4px; font-size: 1.15rem; color: #0f172a; }
.counts-badge { font-size: 0.85rem; color: #475569; font-weight: 500; }
.top-actions { display: flex; gap: 8px; flex-wrap: wrap; }
.category-filter-bar { display: flex; align-items: center; gap: 8px; margin-bottom: 12px; flex-wrap: wrap; }
.filter-label { font-size: 0.88rem; font-weight: 600; color: #334155; }
.category-pills { display: flex; gap: 6px; flex-wrap: wrap; }
.pagination-bar { display: flex; justify-content: space-between; align-items: center; margin-bottom: 12px; font-size: 0.88rem; color: #64748b; }
.batch-preview-box { background: #fffbeb; border: 1px solid #fde68a; border-radius: 6px; padding: 12px; margin-bottom: 16px; }
.batch-members-box { margin-top: 8px; border: 1px solid #fde68a; border-radius: 4px; padding: 6px 10px; background: #ffffff; }
.batch-confirm-row { display: flex; align-items: center; gap: 12px; margin-top: 12px; }
.task-cards-list { display: flex; flex-direction: column; gap: 16px; }
.task-card { background: #ffffff; border: 1px solid #cbd5e1; border-radius: 8px; padding: 16px; box-shadow: 0 1px 3px rgba(0,0,0,0.05); }
.card-header { display: flex; flex-direction: column; gap: 6px; margin-bottom: 12px; }
.card-badges { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; }
.coord-badge { background: #e0f2fe; color: #0284c7; font-weight: 600; font-family: monospace; padding: 2px 8px; border-radius: 4px; font-size: 0.9rem; }
.advanced-tool-link { font-size: 0.85rem; color: #0284c7; text-decoration: underline; margin-left: 4px; }
.card-title { margin: 0; font-size: 1.05rem; color: #1e293b; }
.card-body { display: flex; flex-direction: column; gap: 12px; }
.source-evidence-box { background: #f8fafc; border: 1px solid #e2e8f0; border-radius: 6px; padding: 10px 14px; }
.evidence-text { font-family: monospace; font-size: 0.95rem; line-height: 1.5; color: #0f172a; white-space: pre-wrap; font-weight: 500; }
.struck-text { color: #d03050; text-decoration: line-through; background-color: #fef0f0; padding: 0 2px; border-radius: 2px; }
.evidence-comment { margin-top: 6px; font-size: 0.85rem; color: #b45309; }
.card-detail { margin: 0; font-size: 0.9rem; color: #475569; line-height: 1.4; }
.quick-choices-bar { display: flex; align-items: center; gap: 10px; margin-top: 4px; flex-wrap: wrap; }
.choice-prompt { font-size: 0.88rem; font-weight: 600; color: #334155; }
.in-card-edit { background: #f8fafc; border: 1px solid #e2e8f0; border-radius: 6px; padding: 12px; margin-top: 8px; display: flex; flex-direction: column; gap: 10px; }
.readonly-fields-preview { font-size: 0.88rem; color: #334155; display: flex; gap: 12px; }
.switch-manual-row { margin-bottom: 4px; }
.manual-result-warn { font-size: 0.85rem; color: #b45309; display: flex; align-items: center; gap: 8px; }
.field-input-row { display: flex; align-items: center; gap: 10px; }
.field-input-row label { min-width: 60px; font-size: 0.88rem; font-weight: 600; color: #475569; }
.reason-input-row label { font-size: 0.88rem; font-weight: 600; color: #475569; margin-bottom: 4px; display: block; }
.capsules-row { display: flex; flex-wrap: wrap; gap: 6px; margin-top: 6px; }
.exceptions-box { border: 1px solid #e2e8f0; border-radius: 4px; padding: 6px 10px; }
.exceptions-box summary { font-size: 0.85rem; cursor: pointer; color: #475569; }
.member-checkboxes { display: flex; flex-direction: column; gap: 4px; margin-top: 6px; font-size: 0.85rem; }
.card-submit-row { display: flex; justify-content: flex-end; margin-top: 6px; }
.resolved-group { margin-top: 20px; border: 1px solid #e2e8f0; border-radius: 6px; padding: 10px; }
.resolved-group summary { cursor: pointer; font-size: 0.9rem; font-weight: 600; color: #16a34a; }
.resolved-strip { display: flex; justify-content: space-between; align-items: center; padding: 6px 10px; background: #f0fdf4; border-radius: 4px; margin-top: 6px; font-size: 0.88rem; }
.resolved-text { color: #15803d; }
</style>