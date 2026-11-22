<script setup lang="ts">
import { computed, nextTick, onMounted, ref } from 'vue';
import { useRoute } from 'vue-router';
import { NAlert, NButton, NInput, NTag } from 'naive-ui';
import { api } from './api';
import { useWorkbenchContextStore } from './stores/workbenchContext';
import ActionNextStepBanner from './components/ActionNextStepBanner.vue';
import CoaEditor from './CoaEditor.vue';
import ConfirmAssays from './ConfirmAssays.vue';

const route = useRoute();
const ctx = useWorkbenchContextStore();
type Summary = { id: string; filename: string };
type Specification = Summary & { scope: string; material_scope: string; linked_projects?: { project: string; count: number; is_current: number }[]; rows: {
  table: number; row: number; column: number; page?: number; category: string; item: string;
  method: string; sop: string; criterion: string;
}[] };
const list = ref<Summary[]>([]);
const specSearch = ref('');
const selected = ref<Specification>();
const justUploaded = ref(false);
const error = ref('');
const busy = ref(false);
const materials: Record<string, string> = { reference: '参考品', DS: '原液', DP: '制剂', placebo: '安慰剂' };
const activeProjectCode = computed(() => String(route.query.project || ctx.activeProject || ''));
const filteredList = computed(() => {
  const kw = specSearch.value.trim().toLowerCase();
  if (!kw) return list.value;
  return list.value.filter(item => item.filename.toLowerCase().includes(kw));
});

async function run(action: () => Promise<void>) {
  if (busy.value) return;
  busy.value = true;
  error.value = '';
  try { await action(); }
  catch (e) { error.value = (e as Error).message; }
  finally { busy.value = false; }
}
async function refresh() {
  list.value = await api('/specifications');
}
function scrollToAnchor(id: string) {
  void nextTick(() => {
    document.getElementById(id)?.scrollIntoView({ behavior: 'smooth', block: 'start' });
  });
}
onMounted(() => run(async () => {
  await refresh();
  const requested = String(route.query.specification || '');
  const onlyStandard = list.value.length === 1 ? list.value[0].id : '';
  const id = requested || (route.query.sample ? onlyStandard : list.value[0]?.id);
  if (id) await readSpecification(id);
  const focus = String(route.query.focus || '');
  if (focus === 'assays') scrollToAnchor('spec-step-assays');
  else if (focus === 'coa') scrollToAnchor('spec-step-coa');
}));
async function readSpecification(id: string) {
  selected.value = await api('/specifications/' + encodeURIComponent(id));
}
function open(id: string) {
  justUploaded.value = false;
  return run(() => readSpecification(id));
}
function makeCurrent(project: string) {
  return run(async () => {
    const item = selected.value;
    if (!item || !window.confirm(`将「${item.filename}」设为 ${project} 的当前${materials[item.material_scope]}标准？`)) return;
    const path = '/projects/' + encodeURIComponent(project);
    const config = await api<{ base_revision: number }>(path + '/configuration');
    await api(path + '/current-specifications', { method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ specification_id: item.id, revision: config.base_revision, idempotency_key: crypto.randomUUID() }) });
    selected.value = await api('/specifications/' + encodeURIComponent(item.id));
  });
}
function upload(event: Event) {
  const input = event.target as HTMLInputElement;
  const file = input.files?.[0];
  if (!file) return;
  const form = new FormData();
  form.set('file', file);
  input.value = '';
  return run(async () => {
    selected.value = await api('/specifications', { method: 'POST', body: form });
    justUploaded.value = true;
    await refresh();
  });
}
</script>

<template>
  <NAlert v-if="error" type="error" role="alert" style="margin-bottom: 14px;">{{ error }}</NAlert>

  <div class="spec-master-detail">
    <!-- Left Rail: Specification Library + Upload -->
    <aside class="panel spec-sidebar" aria-label="质量标准原件清单">
      <div class="spec-rail-head">
        <h2>质量标准原件</h2>
        <NTag size="small" type="info">{{ list.length }} 份</NTag>
      </div>
      <p class="spec-rail-desc">上传 Word 标准并核对条款后加入项目检项字典，再用于研究表与放行 COA。</p>

      <label class="spec-upload-box">
        <span>选择 Word 标准</span>
        <input type="file" accept=".docx" :disabled="busy" @change="upload">
      </label>
      <p v-if="busy" role="status" class="muted" style="margin: 8px 0 0;">正在读取质量标准…</p>

      <NInput v-if="list.length > 5" v-model:value="specSearch" size="small" clearable placeholder="搜索标准文件名..." style="margin-top: 12px;" />

      <ul class="list spec-list">
        <li v-for="item in filteredList" :key="item.id" :class="{ 'is-selected': selected?.id === item.id }">
          <NButton text :type="selected?.id === item.id ? 'primary' : 'default'" :disabled="busy" @click="open(item.id)">
            {{ item.filename }}
          </NButton>
        </li>
      </ul>
      <p v-if="!list.length" class="muted" style="margin-top: 10px;">暂未上传质量标准原件。</p>
    </aside>

    <!-- Right Main Workspace: 3-Step Workflow Flow (All steps remain mounted for full flow & test compatibility) -->
    <div class="spec-workspace">
      <template v-if="selected">
        <nav class="spec-flow-stepper" aria-label="标准与 COA 处理步骤">
          <button type="button" class="flow-step-btn" @click="scrollToAnchor('spec-step-clauses')">
            <span class="step-badge">1</span>
            <span>标准条款（{{ selected.rows.length }} 条）</span>
          </button>
          <span class="flow-arrow" aria-hidden="true">→</span>
          <button type="button" class="flow-step-btn" @click="scrollToAnchor('spec-step-assays')">
            <span class="step-badge">2</span>
            <span>确认加入项目检项字典</span>
          </button>
          <span class="flow-arrow" aria-hidden="true">→</span>
          <button type="button" class="flow-step-btn" @click="scrollToAnchor('spec-step-coa')">
            <span class="step-badge">3</span>
            <span>样品登记与放行 COA</span>
          </button>
        </nav>

        <ActionNextStepBanner
          v-if="justUploaded"
          :title="'质量标准已归档 · ' + selected.filename"
          :summary="`已识别 ${selected.rows.length} 条条款。推荐按顺序：先确认加入项目检项字典，再录入放行 COA 或直接用于建研究表。`"
          :dismissible="true"
          @dismiss="justUploaded = false"
          :steps="[
            { label: '1. 前往确认项目检项字典', primary: true, onClick: () => scrollToAnchor('spec-step-assays') },
            { label: '2. 录入批次放行 COA', onClick: () => scrollToAnchor('spec-step-coa') },
            ...(selected.rows.length ? [{ label: '3. 按本标准建研究表', to: { path: '/new', query: { project: activeProjectCode || undefined, specification: selected.id } } }] : []),
          ]"
        />

        <section id="spec-step-clauses" class="panel spec-detail-card">
          <div class="spec-detail-head">
            <div>
              <h2>{{ selected.filename }}</h2>
              <p v-if="!busy" role="status" class="spec-status-line">
                已保存原件，识别到 {{ selected.rows.length }} 条标准。下一步：核对下方检项、方法和标准原文，再用于建表。
              </p>
            </div>
            <div class="spec-head-actions">
              <a class="header-ghost-link" :href="'/api/v1/specifications/' + selected.id + '/file'">下载标准原件</a>
              <RouterLink
                v-if="selected.rows.length"
                class="spec-primary-link"
                :to="{ path: '/new', query: { project: activeProjectCode || undefined, specification: selected.id } }"
              >
                使用此标准新建研究表
              </RouterLink>
            </div>
          </div>

          <p v-if="!selected.rows.length" class="pending">没有识别到标准条款。请核对 Word 表格，或在研究表中手工填写标准。</p>

          <div class="spec-meta-strip">
            <span><strong>适用对象：</strong>{{ materials[selected.material_scope] || '待核对' }}</span>
            <span><strong>当前条款归属：</strong>{{ selected.linked_projects?.map(item => `${item.project}（${item.count} 条）`).join('、') || '尚未确认到项目' }}</span>
          </div>

          <ul v-if="materials[selected.material_scope] && selected.linked_projects?.length" class="current-spec-list">
            <li v-for="link in selected.linked_projects" :key="link.project">
              <strong class="mono-code">{{ link.project }}</strong>
              <NTag v-if="link.is_current" type="success" size="small">当前适用标准</NTag>
              <NButton v-else size="tiny" type="primary" secondary :disabled="busy" @click="makeCurrent(link.project)">设为当前适用标准</NButton>
            </li>
          </ul>

          <p v-if="selected.scope" class="source-scope">{{ selected.scope }}</p>

          <div class="matrix">
            <table>
              <thead><tr><th>原件位置</th><th>类别与检项</th><th>方法</th><th>SOP 编号</th><th>标准原文</th></tr></thead>
              <tbody><tr v-for="row in selected.rows" :key="row.table + ':' + row.row">
                <td><span v-if="row.page">PDF 第 {{ row.page }} 页 · </span><template v-if="row.table">表 {{ row.table }} · 行 {{ row.row }} · 列 {{ row.column }}</template><template v-else>人工录入条款 {{ row.row }}</template></td>
                <td>{{ [row.category, row.item].filter(Boolean).join('\n') }}</td><td>{{ row.method }}</td>
                <td>{{ row.sop }}</td><td>{{ row.criterion }}</td>
              </tr></tbody>
            </table>
          </div>
        </section>

        <div id="spec-step-assays">
          <ConfirmAssays
            v-if="selected?.rows.length"
            :key="'assays-' + selected.id"
            :specification="selected"
            :initial-project="activeProjectCode"
            @registered="open(selected.id)"
            @go-coa="scrollToAnchor('spec-step-coa')"
          />
        </div>

        <div id="spec-step-coa">
          <CoaEditor
            v-if="selected?.rows.length"
            :key="selected.id"
            :specification="selected"
            :initial-project="String(route.query.project || '')"
            :initial-batch="String(route.query.batch || '')"
            :initial-sample-id="String(route.query.sample || '')"
          />
        </div>
      </template>

      <section v-else class="panel spec-empty-panel">
        <h2>选择或上传质量标准原件</h2>
        <p class="muted">在左侧选择已有质量标准，或上传新的 Word 质量标准原件，即可开始「登记检项字典 → 录入放行 COA → 关联研究表 0M」全流程。</p>
      </section>
    </div>
  </div>
</template>

<style scoped>
.spec-master-detail {
  display: grid;
  grid-template-columns: 300px minmax(0, 1fr);
  gap: 20px;
  align-items: start;
}
.spec-sidebar {
  margin-top: 0;
  position: sticky;
  top: 72px;
  max-height: calc(100vh - 96px);
  overflow-y: auto;
  padding: 18px 18px;
}
.spec-rail-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 8px;
}
.spec-rail-head h2 { margin: 0; font-size: 16.5px; }
.spec-rail-desc { margin: 0 0 12px; font-size: 13px; color: var(--text-secondary); line-height: 1.5; }
.spec-upload-box {
  display: grid;
  gap: 6px;
  padding: 12px;
  border-radius: var(--radius-md);
  border: 1.5px dashed var(--border-strong);
  background: var(--bg-subtle);
  font-size: 13.5px;
  font-weight: 600;
  color: var(--text-primary);
  cursor: pointer;
}
.spec-upload-box:hover { border-color: var(--brand); background: var(--brand-soft); }
.spec-upload-box input { width: 100%; font-size: 12.5px; }
.spec-list li { padding: 8px 8px; border-radius: var(--radius-sm); }
.spec-list li.is-selected { background: var(--brand-soft); font-weight: 600; }
.spec-list :deep(.n-button) { text-align: left; white-space: normal; height: auto; line-height: 1.4; }

.spec-workspace { min-width: 0; display: grid; gap: 18px; }
.spec-flow-stepper {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 12px 18px;
  background: var(--bg-surface);
  border: 1px solid var(--border);
  border-radius: var(--radius-lg);
  box-shadow: var(--shadow-card);
}
.flow-step-btn {
  display: inline-flex;
  align-items: center;
  gap: 8px;
  padding: 6px 12px;
  border-radius: var(--radius-sm);
  border: 1px solid var(--border);
  background: var(--bg-subtle);
  color: var(--text-primary);
  font: inherit;
  font-size: 13.5px;
  font-weight: 600;
  cursor: pointer;
  transition: all 0.15s ease;
}
.flow-step-btn:hover { border-color: var(--brand); background: var(--brand-soft); color: var(--brand); }
.step-badge {
  width: 22px;
  height: 22px;
  border-radius: 999px;
  display: grid;
  place-items: center;
  background: var(--brand);
  color: #ffffff;
  font-size: 12px;
  font-weight: 700;
}
.flow-arrow { color: var(--text-muted); font-weight: 600; }

.spec-detail-card, .spec-empty-panel { margin-top: 0; }
.spec-detail-head {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 16px;
  margin-bottom: 12px;
}
.spec-detail-head h2 { margin: 0 0 6px; font-size: 18px; overflow-wrap: anywhere; }
.spec-status-line { margin: 0; font-size: 13.5px; color: var(--text-secondary); }
.spec-head-actions { display: flex; align-items: center; gap: 10px; flex-shrink: 0; }
.header-ghost-link {
  font-size: 13.5px;
  font-weight: 500;
  color: var(--text-secondary);
  padding: 6px 12px;
  border-radius: var(--radius-sm);
  border: 1px solid var(--border);
  background: var(--bg-surface);
}
.header-ghost-link:hover { color: var(--brand); border-color: var(--brand-border); background: var(--brand-soft); text-decoration: none; }
.spec-primary-link {
  padding: 6px 14px;
  border-radius: var(--radius-sm);
  background: var(--brand);
  color: #ffffff;
  font-size: 13.5px;
  font-weight: 600;
}
.spec-primary-link:hover { background: var(--brand-hover); color: #ffffff; text-decoration: none; }

.spec-meta-strip {
  display: flex;
  flex-wrap: wrap;
  gap: 10px 24px;
  padding: 10px 14px;
  border-radius: var(--radius-md);
  background: var(--bg-subtle);
  border: 1px solid var(--border);
  font-size: 13.5px;
  margin-bottom: 10px;
}
.current-spec-list {
  list-style: none;
  padding: 0;
  margin: 0 0 12px;
  display: flex;
  flex-wrap: wrap;
  gap: 10px;
}
.current-spec-list li {
  display: inline-flex;
  align-items: center;
  gap: 8px;
  padding: 5px 10px;
  border-radius: var(--radius-sm);
  border: 1px solid var(--border);
  background: var(--bg-surface);
}
.mono-code { font-family: var(--font-mono); color: var(--brand); }
.source-scope { white-space: pre-wrap; font-size: 13.5px; color: var(--text-secondary); }
.matrix { margin-top: 14px; }
</style>
