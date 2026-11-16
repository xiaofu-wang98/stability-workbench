<script setup lang="ts">
import { computed, nextTick, onMounted, ref, watch } from 'vue';
import { useRoute, useRouter } from 'vue-router';
import { NAlert, NButton, NSelect } from 'naive-ui';
import { api, type Project } from './api';
import ImportSourceReview from './ImportSourceReview.vue';
import StudyWorkbook from './StudyWorkbook.vue';

const props = defineProps<{ projects: Project[] }>();
const route = useRoute();
const router = useRouter();
type Result = { result: { raw_text: string }; key: { batch: string; item: string; method: string; time_label?: string; month?: number; day?: number } };
type Candidate = { id: string; lifecycle: string; received: boolean; detail: string; status: string; location: { section: string; row: number; column: number };
  logical_row?: number | null; source_span?: [number, number, number, number] | null;
  raw_fields: Record<string, string>; record?: Result; existing_record?: Result; allowed_actions: string[]; candidate_revision: number; detection_id: string };
type Preview = { preview_id: string; project: string; filename: string; base_revision: number; duplicate_source?: boolean; sheet_confirmation_required: boolean; candidates: Candidate[] };
type Preflight = { section: string; base_revision: number; fingerprint: string; can_commit: boolean; state: string; blocker_reasons: string[];
  unresolved: string[]; review_required: string[];
  counts: Record<string, number>; historical_count: number;
  previous_versions: Record<string, string>; adopted_versions: Record<string, string>; decision_fingerprints: Record<string, string> };
type Protocol = {
  draft_id: string;
  batches: { table: number; row: number; raw: string; batch: string }[];
  matrices: { table: number; study: string | null; caption: string; warnings: string[];
    conditions: { text: string; location: string; tracked_changes?: boolean }[];
    points: { column: number; amount: number; unit: string }[];
    items: { row: number; label: string; cells: { column: number; raw: string; state: string }[] }[] }[];
  release_references: { paragraph: number; text: string }[];
};
const projects = ref([...props.projects]);
const project = ref(String(route.query.project || ''));
const role = ref(route.query.sample ? 'stability_source' : '');
const protocol = ref<Protocol>();
const planDrafts = ref<{ id: string; filename: string }[]>([]);
const defaults = ref<Record<string, string>>(route.query.batch ? { batch: String(route.query.batch) } : {});
const file = ref<File>();
const preview = ref<Preview>();
const summaries = ref<{ preview_id: string; filename: string; draft: number }[]>([]);
const section = ref<string | null>(null);
const sections = ref<string[]>([]);
const preflight = ref<Preflight>();
const linkedSheets = ref<{ id: string; name: string }[]>([]);
const suggestions = ref<string[]>([]);
const error = ref('');
const status = ref('');
const busy = ref(false);
const reviewLimit = ref(100);
const onlyReview = ref(false);
const reviewSources = ref(false);
const excludeCandidate = ref<string>();
const sourceReviewPanel = ref<HTMLElement>();
type ConflictAction = 'keep_old' | 'correction' | 'retest' | 'exclude';
const resolving = ref<Candidate>();
const conflictAction = ref<ConflictAction | ''>('');
const conflictReason = ref('');
const conflictOptions = computed(() => {
  const id = resolving.value?.id;
  const check = preflight.value;
  if (!id || !check) return [];
  const result: { value: ConflictAction; label: string }[] = [];
  if (check.adopted_versions[id] && check.adopted_versions[id] === check.previous_versions[id]) {
    result.push({ value: 'keep_old', label: '保留当前采用值' }, { value: 'correction', label: '更正并采用本次值' });
  }
  if (check.previous_versions[id]) result.push({ value: 'retest', label: '登记独立复测，保留当前采用值' });
  result.push({ value: 'exclude', label: '排除此条来源记录' });
  return result;
});
let retry: { signature: string; key: string } | undefined;
let uploadKey = '';

const path = computed(() => '/previews/' + encodeURIComponent(preview.value?.preview_id ?? ''));
const reviewUrl = computed(() => preview.value ? router.resolve({ path: '/import', query: {
  project: preview.value.project, source: preview.value.preview_id, section: section.value || undefined,
} }).href : '');
const candidates = computed(() => preview.value?.candidates.filter(c =>
  !preview.value?.sheet_confirmation_required || c.location.section === section.value) ?? []);
const initial = computed(() => candidates.value.filter(c => c.lifecycle === 'draft' && c.allowed_actions.includes('confirm_initial')));
const unreceived = computed(() => candidates.value.filter(c => c.lifecycle === 'draft' && !c.received));
const unresolvedIssues = computed(() => preflight.value?.unresolved.filter(
  item => item !== 'not_received' && !item.startsWith('not_received:'),
) ?? []);
const reviewCandidates = computed(() => {
  const check = preflight.value;
  if (!check) return [];
  const ids = new Set(check.review_required);
  for (const issue of unresolvedIssues.value) {
    const separator = issue.indexOf(':');
    if (separator !== -1) ids.add(issue.slice(separator + 1));
  }
  return candidates.value.filter(candidate => ids.has(candidate.id));
});
const visibleCandidates = computed(() => onlyReview.value ? reviewCandidates.value : candidates.value);
const preflightCountLabels: Record<string, string> = {
  initial: '首次入库', repeat_source: '同值补充来源', keep_old: '保留旧值', correction: '更正',
  retest: '复测', exclude: '排除', blank: '空格',
};
const preflightCounts = computed(() => Object.entries(preflight.value?.counts ?? {})
  .filter(([key, count]) => count && preflightCountLabels[key])
  .map(([key, count]) => preflightCountLabels[key] + ' ' + count));
const projectPath = () => '/projects/' + encodeURIComponent(project.value);
function post<T>(url: string, body: unknown) {
  return api<T>(url, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) });
}
async function run(action: () => Promise<void>) {
  if (busy.value) return;
  busy.value = true;
  error.value = '';
  status.value = '';
  try { await action(); }
  catch (e) { error.value = (e as Error).message; }
  finally { busy.value = false; }
}
async function list() {
  if (project.value) summaries.value = await api(projectPath() + '/previews?limit=50');
}
watch(project, () => {
  summaries.value = [];
  uploadKey = '';
  if (!busy.value && project.value) void run(list);
});
function selectFile(selected?: File) {
  file.value = selected;
  suggestions.value = [];
  uploadKey = '';
  protocol.value = undefined;
  error.value = '';
  status.value = '';
}
function choose(event: Event) {
  selectFile((event.target as HTMLInputElement).files?.[0]);
}
function fromWorkbook(selected: File) {
  role.value = 'stability_source';
  selectFile(selected);
}
function inspectResult() {
  if (!file.value || !['stability_source', 'release_coa'].includes(role.value)) return;
  const form = new FormData();
  form.set('file', file.value);
  return run(async () => {
    const inspected = await api<{ projects: string[] }>('/intake/inspect', { method: 'POST', body: form });
    suggestions.value = inspected.projects;
    if (!project.value && inspected.projects.length === 1 &&
        projects.value.some(p => p.project === inspected.projects[0])) project.value = inspected.projects[0];
    await list();
    if (!inspected.projects.length) status.value = '未识别到项目编号，请选择或新建目标项目。';
  });
}
async function createProject(event: Event) {
  const data = new FormData(event.target as HTMLFormElement);
  await run(async () => {
    const id = String(data.get('project')).trim();
    await post('/projects', { project: id, name: String(data.get('name')).trim() });
    projects.value = await api('/projects');
    project.value = id;
    await list();
  });
}
async function selectSection(value: string | null) {
  section.value = value;
  preflight.value = undefined;
  resolving.value = undefined;
  reviewSources.value = false;
  excludeCandidate.value = undefined;
  reviewLimit.value = 100;
  onlyReview.value = false;
  linkedSheets.value = [];
  if (value) {
    const query = new URLSearchParams({ section: value });
    preflight.value = await api(path.value + '/v5/preflight?' + query);
    if (candidates.value.some(candidate => candidate.lifecycle === 'submitted')) {
      linkedSheets.value = await api(path.value + '/worksheets?' + query);
    }
  }
}
async function open(id: string, selected?: string) {
  preview.value = await api('/previews/' + encodeURIComponent(id));
  section.value = null;
  preflight.value = undefined;
  linkedSheets.value = [];
  reviewLimit.value = 100;
  if (preview.value?.sheet_confirmation_required) {
    const sheets = await api<{ section: string }[]>(path.value + '/v5/sheets');
    sections.value = sheets.map(sheet => sheet.section);
    if (selected && !sections.value.includes(selected)) throw new Error('链接中的工作表已无法定位，请重新选择工作表。');
    await selectSection(selected || sections.value[0] || null);
  } else if (candidates.value.some(candidate => candidate.lifecycle === 'submitted')) {
    linkedSheets.value = await api(path.value + '/worksheets');
  }
}
onMounted(() => {
  const source = String(route.query.source || '');
  const selected = String(route.query.section || '');
  if (source) void run(() => open(source, selected || undefined));
  else if (project.value) void run(list);
});
function upload() {
  if (!file.value || !project.value || !['stability_source', 'release_coa'].includes(role.value)) return;
  const form = new FormData();
  form.set('file', file.value);
  form.set('context', JSON.stringify({ role: role.value, ...(route.query.sample ? { sample_id: String(route.query.sample) } : {}), defaults: Object.fromEntries(
    Object.entries(defaults.value).map(([key, value]) => [key, value.trim()]).filter(([, value]) => value),
  ) }));
  uploadKey ||= crypto.randomUUID();
  return run(async () => {
    const result = await api<Preview>(projectPath() + '/previews', {
      method: 'POST', headers: { 'Idempotency-Key': uploadKey }, body: form,
    });
    await open(result.preview_id);
    status.value = result.duplicate_source ? '该原件已归档，已打开原有导入记录。' : '已归档原件并生成候选记录。请核对每张工作表的状态。';
  });
}
function parseProtocol() {
  if (!file.value) return;
  const form = new FormData();
  form.set('file', file.value);
  return run(async () => {
    protocol.value = await api('/intake/protocol', { method: 'POST', body: form });
    planDrafts.value = await api('/plan-drafts');
    status.value = '方案解析完成。请在下方选择矩阵建表；未识别矩阵也可以对照原方案手动建表。';
  });
}
function changeRole() {
  uploadKey = '';
  protocol.value = undefined;
  suggestions.value = [];
  error.value = '';
  status.value = '';
  if (role.value === 'protocol') void run(async () => { planDrafts.value = await api('/plan-drafts'); });
}
function receiveSheet() {
  const source = preview.value;
  const check = preflight.value;
  if (!source || !check || !unreceived.value.length) return;
  const candidateIds = unreceived.value.map(candidate => candidate.id);
  return run(async () => {
    await submit(source.preview_id, '/receive', {
      base_revision: check.base_revision, defer_confirmation: true, candidate_ids: candidateIds,
    });
    await open(source.preview_id, check.section);
    status.value = `已接收 ${candidateIds.length} 条候选供核对，正式结果尚未写入。`;
  });
}
async function commit() {
  const source = preview.value;
  if (!source) return;
  await run(async () => {
    const check = preflight.value;
    if (source.sheet_confirmation_required && !check?.can_commit) return;
    const reason = '在导入页核对来源、研究身份和结果后提交';
    const command = check ? {
      section: check.section, base_revision: check.base_revision, fingerprint: check.fingerprint, reason,
    } : {
      base_revision: source.base_revision, items: initial.value.map(c => ({
        candidate_id: c.id, candidate_revision: c.candidate_revision, detection_id: c.detection_id,
        identity_confirmed: true, intent: 'initial', reason,
      })),
    };
    const result = await submit<{ result_version_ids: string[] }>(source.preview_id, check ? '/v5/commit' : '/confirm', command);
    try { await open(source.preview_id, check?.section); }
    catch { status.value = '已写入结果。请重新打开此导入记录读取最新状态。'; return; }
    status.value = `本次写入 ${result.result_version_ids.length} 条结果版本。`;
  });
}
function submit<T = unknown>(id: string, suffix: string, command: object) {
  const signature = JSON.stringify([id, suffix, command]);
  if (retry?.signature !== signature) retry = { signature, key: crypto.randomUUID() };
  return post<T>('/previews/' + encodeURIComponent(id) + suffix, { ...command, idempotency_key: retry.key });
}
function openConflict(candidate: Candidate) {
  resolving.value = candidate;
  conflictAction.value = '';
  conflictReason.value = '';
}
async function previewCellExclusion(candidate: Candidate) {
  excludeCandidate.value = candidate.id;
  reviewSources.value = true;
  await nextTick();
  sourceReviewPanel.value?.scrollIntoView({ block: 'start' });
}
function saveConflict() {
  const candidate = resolving.value;
  const action = conflictAction.value;
  const reason = conflictReason.value.trim();
  const source = preview.value;
  const check = preflight.value;
  if (!candidate || !action || !reason || !source || !check) return;
  const previous = action === 'exclude' ? null : (action === 'retest' ? check.previous_versions[candidate.id] :
    check.adopted_versions[candidate.id]);
  return run(async () => {
    await submit(source.preview_id, '/v5/decisions', {
      base_revision: check.base_revision, candidate_id: candidate.id, candidate_revision: candidate.candidate_revision,
      action, reason, previous_version_id: previous,
    });
    await open(source.preview_id, check.section);
    if (action !== 'exclude') {
      const updated = preflight.value;
      const fingerprint = updated?.decision_fingerprints[candidate.id];
      if (!updated || !fingerprint) {
        status.value = '处理选择已保存。请刷新核对当前来源。';
        return;
      }
      try {
        await submit(source.preview_id, '/v5/review', {
          base_revision: updated.base_revision, candidate_id: candidate.id,
          decision_fingerprint: fingerprint, action: action === 'correction' ? 'adopt' : 'confirm_fact', reason,
        });
      } catch (e) {
        resolving.value = candidate;
        throw e;
      }
      await open(source.preview_id, check.section);
    }
    status.value = '已记录处理选择，写入整张表后生效。';
  });
}
function time(record?: Result) {
  const key = record?.key;
  if (!key) return '';
  return key.time_label || (key.month != null ? key.month + 'M' : key.day != null ? key.day + 'D' : '放行');
}
</script>

<template>
  <NAlert v-if="error" type="error" role="alert">{{ error }}</NAlert>
  <p v-if="status" role="status">{{ status }}</p>
  <section v-if="!preview" class="panel import-panel entry-panel upload-panel">
    <h2>导入文件</h2>
    <p>历史稳定性汇总表请选择「稳定性结果」。从工作台下载后填写的 Excel 可在下方回传。</p>
    <div class="upload-links">
      <RouterLink to="/specifications">质量标准 Word：导入、查看或用于建表</RouterLink>
      <RouterLink to="/scans">识别扫描 PDF</RouterLink>
    </div>
    <label class="file-picker">
      <span>选择来源文件 <small>Excel 或 Word</small></span>
      <input type="file" accept=".xlsx,.docx" :disabled="busy" @change="choose">
    </label>
    <p v-if="file">已选择 {{ file.name }}</p>
    <p v-if="suggestions.length">文件建议项目 {{ suggestions.join('、') }}</p>
    <div class="upload-project">
      <span id="upload-project-label">目标项目</span>
      <NSelect v-model:value="project" aria-labelledby="upload-project-label" :disabled="busy" filterable clearable placeholder="输入项目代码或名称快速检索 (如 501 / IP281)..."
        :options="projects.map(p => ({ value: p.project, label: p.project + (p.name ? ' · ' + p.name : '') }))" />
    </div>
    <label>文件用途
      <select v-model="role" :disabled="busy" @change="changeRole">
        <option value="">请选择文件用途</option>
        <option value="stability_source">稳定性结果</option>
        <option value="release_coa">放行结果／CoA</option>
        <option value="protocol">稳定性方案 Word</option>
      </select>
    </label>
    <details :open="!projects.length">
      <summary>新建项目</summary>
      <form @submit.prevent="createProject">
        <label>项目编号 <input name="project" :value="suggestions.length === 1 ? suggestions[0] : ''" required></label>
        <label>项目名称 <input name="name" required></label>
        <NButton attr-type="submit" :loading="busy">创建项目</NButton>
      </form>
    </details>
    <details v-if="role && role !== 'protocol'">
      <summary>补充原件未注明的身份</summary>
      <div class="identity-fields" @input="uploadKey = ''" @change="uploadKey = ''">
        <label>批号 <input v-model="defaults.batch" :disabled="busy"></label>
        <label>规格 <input v-model="defaults.presentation" :disabled="busy"></label>
        <label>方案编号 <input v-model="defaults.study_id" :disabled="busy"></label>
        <label>条件 <input v-model="defaults.condition" :disabled="busy"></label>
        <label>材料类别 <select v-model="defaults.sample_type" :disabled="busy"><option value="">沿用原件</option><option value="DS">原液</option><option value="DP">制剂</option><option value="placebo">安慰剂</option></select></label>
        <label>考察类型 <select v-model="defaults.study" :disabled="busy"><option value="">沿用原件</option><option value="long_term">长期</option><option value="accelerated">加速</option><option value="stress">强力破坏</option></select></label>
      </div>
    </details>
    <p role="status">{{ busy ? "正在读取文件，请稍候…" : !file ? "选择来源文件。" : !role ? "请选择文件用途。" : role !== "protocol" && !project ? "请选择目标项目，或根据原件查找项目。" : "文件已准备好，可以开始解析。" }}</p>
    <div class="entry-actions">
      <NButton v-if="role === 'protocol'" type="primary" :loading="busy" :disabled="busy || !file" @click="parseProtocol">解析方案</NButton>
      <template v-else-if="role">
        <NButton type="primary" :loading="busy" :disabled="busy || !file || !project" @click="upload">解析并核对</NButton>
        <NButton :disabled="busy || !file" @click="inspectResult">根据原件查找项目</NButton>
      </template>
    </div>
    <ul v-if="summaries.length" class="list">
      <li v-for="item in summaries" :key="item.preview_id">
        <NButton text :disabled="busy" @click="run(() => open(item.preview_id))">{{ item.filename }}</NButton>
        <span>{{ item.draft }} 条尚未提交</span>
      </li>
    </ul>
    <ul v-if="role === 'protocol' && planDrafts.length" class="list">
      <li v-for="draft in planDrafts" :key="draft.id">
        <NButton text :disabled="busy" @click="run(async () => { protocol = await api<Protocol>('/plan-drafts/' + draft.id); })">{{ draft.filename }}</NButton>
      </li>
    </ul>
  </section>
  <details v-if="!preview" class="panel">
    <summary>回传从工作台下载的研究表 Excel</summary>
    <StudyWorkbook :disabled="busy" @historical-source="fromWorkbook" />
  </details>
  <template v-if="protocol && !preview">
    <section class="panel">
      <h2>方案识别预览</h2>
      <a :href="'/api/v1/plan-drafts/' + protocol.draft_id + '/file'">下载方案原件</a>
      <p v-if="!protocol.matrices.length">未识别到考察矩阵。方案原件已保留，可对照原件填写计划。</p>
      <RouterLink :to="{ path: '/new', query: { project: project || undefined, draft: protocol.draft_id } }">按原方案手动建表</RouterLink>
      <p>批号 {{ protocol.batches.map(batch => batch.batch || batch.raw + '（待补全）').join('、') || '未识别' }}</p>
      <details v-if="protocol.release_references.length">
        <summary>查看引用放行的原文说明</summary>
        <p v-for="item in protocol.release_references" :key="item.paragraph" class="source-text">{{ item.text }}</p>
      </details>
    </section>
    <section v-for="matrix in protocol.matrices" :key="matrix.table" class="panel">
      <h2>原件表 {{ matrix.table }} · {{ matrix.caption || '考察矩阵' }}</h2>
      <RouterLink :to="{ path: '/new', query: { project: project || undefined, draft: protocol.draft_id, table: matrix.table } }">按此矩阵新建研究表</RouterLink>
      <NAlert v-if="matrix.warnings.length" type="warning"><ul><li v-for="warning in matrix.warnings" :key="warning">{{ warning }}</li></ul></NAlert>
      <ul><li v-for="condition in matrix.conditions" :key="condition.location" class="source-text">{{ condition.location }} {{ condition.text }}{{ condition.tracked_changes ? '（含修订）' : '' }}</li></ul>
      <div class="matrix"><table>
        <thead><tr><th>原件行／检项</th><th v-for="point in matrix.points" :key="point.column">{{ point.amount }}{{ point.unit }}<br>列 {{ point.column }}</th></tr></thead>
        <tbody><tr v-for="item in matrix.items" :key="item.row"><th scope="row">{{ item.row }} · {{ item.label }}</th>
          <td v-for="cell in item.cells" :key="cell.column" :class="{ pending: cell.state === 'unclear' }">{{ cell.raw || '待核对' }}</td>
        </tr></tbody>
      </table></div>
    </section>
  </template>
  <template v-if="preview">
    <section class="panel import-panel import-review">
      <h2>{{ preview.filename }}</h2>
      <div>目标项目 {{ preview.project }} · <a :href="'/api/v1' + path + '/file'">下载原件</a></div>
      <a :href="reviewUrl" target="_blank" rel="noopener">在新标签打开当前核对范围</a>
      <NButton :disabled="busy" @click="run(async () => { preview = undefined; await list(); })">返回导入列表</NButton>
      <NSelect v-if="preview.sheet_confirmation_required" filterable :value="section" :disabled="busy" placeholder="选择工作表"
        :options="sections.map(s => ({ label: s, value: s }))" @update:value="value => run(() => selectSection(value))" />
      <NAlert v-if="!candidates.length" type="warning">当前范围没有识别到结果。请确认文件用途、切换工作表，或使用来源处理工具对照原件补充。<a :href="'/legacy?view=ledger&project=' + encodeURIComponent(preview.project)">打开来源处理工具</a></NAlert>
      <div>当前范围 {{ candidates.length }} 条 · 尚未提交 {{ candidates.filter(c => c.lifecycle === 'draft').length }} 条</div>
      <NAlert v-if="preflight && !preflight.can_commit && preflight.state !== 'committed'" type="warning">
        <span v-if="preflight.blocker_reasons.length">{{ preflight.blocker_reasons.join('；') }}</span>
        <p v-if="unreceived.length">有 {{ unreceived.length }} 条候选尚未接收。</p>
        <p v-if="unresolvedIssues.length">还有 {{ unresolvedIssues.length }} 项需要核对。请对照原表处理后再写入。</p>
        <span v-if="preflight.review_required.length">{{ preflight.review_required.length }} 条结果的处理决定待复核。</span>
        <span v-if="!unreceived.length && !preflight.blocker_reasons.length && !unresolvedIssues.length && !preflight.review_required.length">此工作表还未通过写入预检。</span>
        <NButton text :disabled="busy" @click="reviewSources = true">查看本表来源核对建议</NButton>
        <a :href="'/legacy?view=ledger&project=' + encodeURIComponent(preview.project)">更多来源处理工具</a>
      </NAlert>
      <p v-if="preflight?.state === 'committed'">此工作表已写入。</p>
      <p v-if="preflight && (preflight.state === 'ready' || preflight.state === 'committed') && preflightCounts.length">
        {{ preflight.state === 'ready' ? '待提交范围' : '已提交范围' }}：{{ preflightCounts.join(' · ') }}
      </p>
      <NAlert v-if="!preview.sheet_confirmation_required && candidates.some(c => c.lifecycle === 'draft' && !c.allowed_actions.includes('confirm_initial'))" type="warning">
        部分记录需要进一步核对，当前按钮只提交可写入的首次结果。
        <a :href="'/legacy?project=' + encodeURIComponent(preview.project)">打开来源核对工具</a>
      </NAlert>
      <div class="entry-actions import-review-actions">
        <NButton v-if="preflight && unreceived.length" :disabled="busy" @click="receiveSheet">接收 {{ unreceived.length }} 条候选供核对</NButton>
        <NButton type="primary" :loading="busy" :disabled="preview.sheet_confirmation_required ? !preflight?.can_commit || preflight.state === 'committed' : !initial.length"
          @click="commit">{{ preview.sheet_confirmation_required ? '核对无误，写入本工作表' : '核对无误，写入 ' + initial.length + ' 条首次结果' }}</NButton>
        <RouterLink :to="'/projects/' + encodeURIComponent(preview.project)">打开项目数据</RouterLink>
        <RouterLink v-for="sheet in linkedSheets" :key="sheet.id" :to="'/worksheets/' + encodeURIComponent(sheet.id)">打开研究表 {{ sheet.name }}</RouterLink>
      </div>
    </section>
    <div v-if="reviewSources && section" ref="sourceReviewPanel">
    <ImportSourceReview :key="[preview.preview_id, section, preflight?.base_revision, excludeCandidate].join(':')"
      :source="preview.preview_id" :section="section" :exclude-candidate="excludeCandidate" @cancel="reviewSources = false; excludeCandidate = undefined"
      @saved="run(async () => {
        if (preview) await open(preview.preview_id, section || undefined);
        reviewSources = Boolean(preflight && !preflight.can_commit);
        status = '已保存来源决定，请核对更新后的预检。';
      })" />
    </div>
    <section v-if="resolving" class="panel import-panel">
      <h2>核对变动结果</h2>
      <p>{{ resolving.location.section }} · 第 {{ resolving.location.row }} 行 · {{ resolving.record?.key.item }}</p>
      <p>原有值 {{ resolving.existing_record?.result.raw_text }}　 本次值 {{ resolving.record?.result.raw_text }}</p>
      <form class="conflict-form" @submit.prevent="saveConflict">
        <label>处理方式 <select v-model="conflictAction" required :disabled="busy">
          <option value="">请选择</option><option v-for="option in conflictOptions" :key="option.value" :value="option.value">{{ option.label }}</option>
        </select></label>
        <label>核对依据 <input v-model="conflictReason" required :disabled="busy"></label>
        <NButton attr-type="submit" type="primary" :disabled="busy || !conflictAction || !conflictReason.trim()">保存并核对选择</NButton>
        <NButton :disabled="busy" @click="resolving = undefined">取消</NButton>
      </form>
    </section>
    <section class="panel matrix">
      <label v-if="preflight"><input v-model="onlyReview" type="checkbox" @change="reviewLimit = 100">
        仅显示待核对结果（{{ reviewCandidates.length }}）
      </label>
      <p v-if="onlyReview && !visibleCandidates.length">没有待核对的结果行。工作表级问题请查看上方预检。</p>
      <table>
        <thead><tr><th>原件位置</th><th>批号</th><th>检项与方法</th><th>时间点</th><th>原有值</th><th>本次值</th><th>状态</th></tr></thead>
        <tbody><tr v-for="c in visibleCandidates.slice(0, reviewLimit)" :key="c.id">
          <td>{{ c.location.section }} · 行 {{ c.location.row }} 列 {{ c.location.column }}
            <div v-if="c.source_span">合并原格覆盖第 {{ c.source_span[0] }}–{{ c.source_span[2] }} 行，当前对应第 {{ c.logical_row ?? c.location.row }} 行。</div>
          </td><td>{{ c.record?.key.batch ?? c.raw_fields.batch }}</td>
          <td>{{ c.record?.key.item ?? c.raw_fields.test_item }} {{ c.record?.key.method ?? c.raw_fields.method }}</td>
          <td>{{ time(c.record) || c.raw_fields.time_point }}</td>
          <td>{{ c.existing_record?.result.raw_text }}</td><td>{{ c.record?.result.raw_text ?? c.raw_fields.result }}</td>
          <td>{{ c.lifecycle === 'submitted' ? '已提交' : c.lifecycle === 'withdrawn' ? '已排除' : c.detail }}
            <NButton v-if="preflight && c.received && c.lifecycle === 'draft' && c.status === 'different_value' && c.allowed_actions.includes('save_decision')"
              text :disabled="busy" @click="openConflict(c)">处理变动</NButton>
            <NButton v-if="preflight && c.received && c.lifecycle === 'draft' && c.status === 'needs_review'"
              text :disabled="busy" @click="previewCellExclusion(c)">预览排除此原格</NButton>
          </td>
        </tr></tbody>
      </table>
      <NButton v-if="visibleCandidates.length > reviewLimit" @click="reviewLimit += 100">显示更多</NButton>
    </section>
  </template>
</template>

<style scoped>
.import-panel { display: grid; gap: 12px; max-width: 800px; justify-items: start; }
.import-review { max-width: none; }
.import-review-actions { width: 100%; }
.import-review h2 { max-width: 100%; overflow-wrap: anywhere; }
.import-review :deep(.n-alert) { width: 100%; }
.import-review :deep(.n-alert p) { margin: 6px 0; }
.import-panel :deep(.n-select) { width: min(100%, 480px); }
form { display: flex; flex-wrap: wrap; gap: 10px; margin: 10px 0; }
.identity-fields { display: grid; grid-template-columns: repeat(auto-fit, minmax(170px, 1fr)); gap: 8px; margin: 10px 0; }
.identity-fields label { display: grid; gap: 3px; }
.source-text { white-space: pre-wrap; }
.conflict-form { display: grid; gap: 10px; }
.conflict-form label { display: grid; gap: 4px; }
.upload-panel { grid-template-columns: repeat(2, minmax(0, 1fr)); max-width: 900px; align-items: start; gap: 18px; }
.upload-panel > :is(.file-picker, .entry-actions, .list, details, p) { grid-column: 1 / -1; width: 100%; }
.upload-panel > h2 { margin: 0; }
.upload-panel form { align-items: end; }
.upload-panel form label { display: grid; gap: 6px; }
.upload-links { grid-column: 1 / -1; justify-self: start; padding: 12px; background: #eef5fa; border-radius: 8px; display: flex; flex-wrap: wrap; gap: 6px 16px; font-size: 13px; }
.upload-project, .upload-panel > label { display: grid; gap: 6px; width: 100%; }
.upload-panel > .file-picker { padding: 18px; background: #f4faf8; border: 1px dashed #8eb9ae; border-radius: 10px; gap: 10px; }
.file-picker small { margin-left: 8px; font-weight: 400; color: #526a65; }
.file-picker input { max-width: 100%; }
.file-picker input::file-selector-button { padding: 7px 12px; margin-right: 10px; border: 1px solid #b7d2ca; border-radius: 6px; color: #174f45; background: white; cursor: pointer; font: inherit; }
@media (max-width: 650px) {
  .upload-panel { grid-template-columns: minmax(0, 1fr); }
  .upload-links { justify-self: start; }
}
</style>
