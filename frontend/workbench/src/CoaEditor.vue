<script setup lang="ts">
import { computed, onMounted, ref } from 'vue';
import { useRouter } from 'vue-router';
import { NAlert, NButton, NSelect } from 'naive-ui';
import { api, archiveStudyObject, triggerDownload, type Project, type WorksheetSummary } from './api';
import { useWorkbenchContextStore } from './stores/workbenchContext';
import ActionNextStepBanner, { type NextStepAction } from './components/ActionNextStepBanner.vue';

type Row = { table: number; row: number; category: string; item: string; method: string; sop: string; criterion: string };
type Sample = { id: string; batch: string; material_stage: string; sample_type: string; presentation: string;
  packaging: string; purpose: string };
type State = { version: number; revision: number; values: Record<string, string>; word_supported: boolean };
type Preview = State & { upload_id: string; existing: Record<string, string> };
const props = defineProps<{
  specification: { id: string; rows: Row[]; material_scope: string };
  initialProject?: string;
  initialBatch?: string;
  initialSampleId?: string;
}>();
const router = useRouter();
const ctx = useWorkbenchContextStore();
const projects = ref<Project[]>([]);
const project = ref('');
const batch = ref('');
const samples = ref<Sample[]>([]);
const selected = ref<Sample>();
const matchingSheets = ref<WorksheetSummary[]>([]);
const justSavedCoa = ref(false);
const searched = ref(false);
const creating = ref(false);
const attributes = ref({ material_stage: '', sample_type: '', presentation: '', packaging: '', purpose: '' });
const state = ref<State>();
const edits = ref<Record<string, string>>({});
const upload = ref<Preview>();
const busy = ref(false);
const error = ref('');
const message = ref('');
const key = (row: Row) => row.table + ':' + row.row;
const samplePath = () => '/projects/' + encodeURIComponent(project.value) + '/samples';
const coaPath = () => '/samples/' + selected.value!.id + '/coa/' + props.specification.id;

async function run(action: () => Promise<void>) {
  if (busy.value) return;
  busy.value = true;
  error.value = '';
  message.value = '';
  try { await action(); }
  catch (e) { error.value = (e as Error).message; }
  finally { busy.value = false; }
}

function downloadCoa(format: 'docx' | 'xlsx') {
  if (!selected.value) return;
  const url = '/api/v1' + coaPath() + '/file' + (format === 'xlsx' ? '?format=xlsx' : '');
  triggerDownload(url);
}

onMounted(() => run(async () => {
  projects.value = await api('/projects');
  if (props.initialProject) {
    project.value = props.initialProject;
  }
  if (props.initialBatch) {
    batch.value = props.initialBatch;
  }
  if (project.value && batch.value) {
    await searchSamples(props.initialSampleId);
  }
}));
function removeSample(sample: Sample) {
  return run(async () => {
    if (!await archiveStudyObject('/samples/' + encodeURIComponent(sample.id), '样品')) return;
    samples.value = samples.value.filter(item => item.id !== sample.id);
    if (selected.value?.id === sample.id) {
      selected.value = undefined;
      state.value = undefined;
      upload.value = undefined;
      edits.value = {};
    }
    message.value = '已删除样品并保留历史。';
  });
}
function reset() {
  selected.value = undefined;
  state.value = undefined;
  upload.value = undefined;
  creating.value = false;
  searched.value = false;
  samples.value = [];
  matchingSheets.value = [];
  justSavedCoa.value = false;
}
async function searchSamples(sampleId?: string) {
  if (!project.value || !batch.value.trim()) throw new Error('请先选择项目并填写样品批号。');
  reset();
  samples.value = await api(samplePath() + '?batch=' + encodeURIComponent(batch.value.trim()));
  searched.value = true;
  const target = sampleId ? samples.value.find(sample => sample.id === sampleId)
    : samples.value.length === 1 ? samples.value[0] : undefined;
  if (target) await open(target);
  else if (sampleId) throw new Error('快捷入口指定的样品不在该项目和批号下，请核对样品。');
}
function search() {
  return run(() => searchSamples());
}
async function loadMatchingSheets(sample: Sample) {
  try {
    const all = await api<WorksheetSummary[]>('/projects/' + encodeURIComponent(project.value) + '/worksheets');
    matchingSheets.value = all.filter(ws => ws.sample_id === sample.id || (!ws.sample_id && ws.batch === sample.batch));
  } catch {
    matchingSheets.value = [];
  }
}
async function open(sample: Sample) {
  selected.value = sample;
  state.value = undefined;
  upload.value = undefined;
  justSavedCoa.value = false;
  const data = await api<State>(coaPath());
  state.value = data;
  edits.value = { ...data.values };
  void loadMatchingSheets(sample);
}
function create() {
  return run(async () => {
    const sample = await api<Sample>(samplePath(), { method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ batch: batch.value, ...attributes.value }) });
    creating.value = false;
    if (!samples.value.some(item => item.id === sample.id)) samples.value.push(sample);
    message.value = `样品 ${sample.batch} 已登记。`;
    try {
      await open(sample);
    } catch (e) {
      throw new Error(`无法打开 COA。${(e as Error).message} 可从样品列表重新选择。`);
    }
  });
}
function read(event: Event) {
  const input = event.target as HTMLInputElement;
  const file = input.files?.[0];
  input.value = '';
  if (!file) return;
  return run(async () => {
    const form = new FormData();
    form.set('file', file);
    upload.value = await api(coaPath() + '/preview', { method: 'POST', body: form });
  });
}
function save() {
  return run(async () => {
    const base = upload.value || state.value!;
    const data = await api<State & { changed: number }>(coaPath(), { method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ revision: base.revision, version: base.version, values: upload.value?.values || edits.value,
        upload_id: upload.value?.upload_id || '', idempotency_key: crypto.randomUUID() }) });
    state.value = { ...state.value!, ...data };
    edits.value = { ...data.values };
    upload.value = undefined;
    message.value = data.changed ? `已保存 ${data.changed} 条放行结果，COA 版本 ${data.version}。` : '没有变化，沿用当前版本。';
    justSavedCoa.value = true;
    if (selected.value) await loadMatchingSheets(selected.value);
  });
}

const coaNextSteps = computed<NextStepAction[]>(() => {
  if (!selected.value || !project.value) return [];
  const steps: NextStepAction[] = [];
  if (matchingSheets.value.length) {
    const first = matchingSheets.value[0];
    steps.push({
      label: `1. 前往研究表「${first.name}」批量复制到 0M`,
      primary: true,
      onClick: () => {
        ctx.setPendingWorksheetAction(first.id, 'coa0m');
        void router.push('/worksheets/' + encodeURIComponent(first.id));
      },
    });
    steps.push({
      label: '2. 为此样品新建另一张研究表',
      to: { path: '/new', query: { project: project.value, sample: selected.value.id, batch: selected.value.batch, specification: props.specification.id } },
    });
  } else {
    steps.push({
      label: `1. 为样品「${selected.value.batch}」新建稳定性研究表`,
      primary: true,
      to: { path: '/new', query: { project: project.value, sample: selected.value.id, batch: selected.value.batch, specification: props.specification.id } },
    });
  }
  if (state.value?.word_supported) {
    steps.push({ label: '下载 COA Word', onClick: () => downloadCoa('docx') });
  }
  steps.push({ label: '下载 COA Excel', onClick: () => downloadCoa('xlsx') });
  steps.push({ label: '查看项目台账', to: '/projects/' + encodeURIComponent(project.value) });
  return steps;
});
</script>

<template>
  <section class="panel">
    <h2>样品放行结果（COA）</h2>
    <NAlert v-if="error" type="error" role="alert">{{ error }}</NAlert>
    <p v-if="message" role="status">{{ message }}</p>
    <ActionNextStepBanner
      v-if="justSavedCoa && selected"
      :title="`放行 COA 已保存 · ${selected.batch}（版本 ${state?.version || 1}）`"
      :summary="matchingSheets.length
        ? `检测到该样品已关联 ${matchingSheets.length} 张稳定性研究表，可直接跳转将放行结果一键批量复制到 0M。`
        : '该样品尚未建立稳定性研究表，可直接带入当前样品与标准新建研究表。'"
      :steps="coaNextSteps"
      :dismissible="true"
      @dismiss="justSavedCoa = false"
    />
    <form @submit.prevent="search">
      <div style="display: inline-flex; align-items: center; gap: 8px;">
        <span style="font-weight: bold;">项目</span>
        <div style="min-width: 260px; max-width: 360px;">
          <NSelect v-model:value="project" aria-label="项目" :disabled="busy" filterable clearable
            placeholder="输入项目代码或名称快速搜索..."
            :options="projects.map(item => ({ value: item.project, label: item.project + (item.name ? ' · ' + item.name : '') }))"
            @update:value="reset" />
        </div>
      </div>
      <label> 样品批号 <input v-model.trim="batch" maxlength="250" required :disabled="busy" @input="reset"></label>
      <NButton attr-type="submit" :disabled="busy || !project || !batch.trim()">查找样品</NButton>
    </form>
    <div v-if="searched">
      <p v-if="!samples.length">此项目下尚未登记该批号。</p>
      <ul v-else>
        <li v-for="sample in samples" :key="sample.id">
          <NButton text :disabled="busy" @click="run(() => open(sample))">
            {{ [sample.batch, sample.material_stage, sample.sample_type, sample.presentation, sample.packaging, sample.purpose].filter(Boolean).join(' · ') }}
          </NButton>
          <NButton text type="error" :disabled="busy" @click="removeSample(sample)">删除样品</NButton>
        </li>
      </ul>
      <NButton v-if="!creating" :disabled="busy" @click="creating = true">另建样品对象</NButton>
      <form v-else class="sample-form" @submit.prevent="create">
        <label>材料阶段 <input v-model.trim="attributes.material_stage" required placeholder="如裸抗、ADC、参考品"></label>
        <label>样品类型 <select v-model="attributes.sample_type" required>
          <option value="">请选择</option><option>DS</option><option>DP</option><option value="placebo">安慰剂</option>
        </select></label>
        <label>规格 <input v-model.trim="attributes.presentation"></label>
        <label>包装 <input v-model.trim="attributes.packaging"></label>
        <label>用途 <input v-model.trim="attributes.purpose" required></label>
        <NButton attr-type="submit" :disabled="busy">登记并打开 COA</NButton>
      </form>
    </div>
    <template v-if="selected && state">
      <p>项目 <RouterLink :to="'/projects/' + encodeURIComponent(project)">{{ project }}</RouterLink></p>
      <p>当前样品 {{ selected.batch }} · {{ selected.material_stage }} · {{ selected.sample_type }} · {{ selected.presentation }} · {{ selected.packaging }} · {{ selected.purpose }}</p>
      <p>COA 版本 {{ state.version || '未填写' }}。留空会保留已保存的结果。</p>
      <div class="coa-actions">
        <NButton v-if="state.word_supported" size="small" @click="downloadCoa('docx')">下载 COA Word</NButton>
        <a v-if="state.word_supported" :href="'/api/v1' + coaPath() + '/file'">Word 备用链接</a>
        <NButton size="small" @click="downloadCoa('xlsx')">下载 COA Excel</NButton>
        <a :href="'/api/v1' + coaPath() + '/file?format=xlsx'">Excel 备用链接</a>
        <label>上传填写后的 COA <input type="file" :accept="state.word_supported ? '.docx,.xlsx' : '.xlsx'" :disabled="busy" @change="read"></label>
      </div>
      <p v-if="!state.word_supported">该标准来自扫描件，可填写放行结果。保留 Word 原版式的下载和回传需要 Word 标准。</p>
      <p v-if="upload" role="status">请核对上传的结果。单元格会显示原有值及拟提交值。</p>
      <div class="matrix">
        <table>
          <thead><tr><th>类别与检项</th><th>方法</th><th>SOP 编号</th><th>标准原文</th><th>{{ selected.batch }}</th></tr></thead>
          <tbody><tr v-for="row in specification.rows" :key="key(row)">
            <td>{{ [row.category, row.item].filter(Boolean).join('\n') }}</td><td>{{ row.method }}</td>
            <td>{{ row.sop }}</td><td>{{ row.criterion }}</td>
            <td v-if="upload">
              <template v-if="upload.values[key(row)] && upload.values[key(row)] !== upload.existing[key(row)]">
                {{ upload.existing[key(row)] || '空白' }} → {{ upload.values[key(row)] }}
              </template>
              <template v-else>{{ upload.existing[key(row)] || '空白' }}</template>
            </td>
            <td v-else><textarea v-model="edits[key(row)]" :aria-label="row.item + ' ' + row.criterion + ' 放行结果'"
              rows="2" maxlength="4000" :disabled="busy"></textarea></td>
          </tr></tbody>
        </table>
      </div>
      <NButton type="primary" :disabled="busy" @click="save">{{ upload ? '确认写入上传结果' : '保存放行结果' }}</NButton>
      <NButton v-if="upload" :disabled="busy" @click="upload = undefined">取消上传</NButton>
    </template>
  </section>
</template>

<style scoped>
.sample-form, .coa-actions { display: flex; flex-wrap: wrap; align-items: end; gap: 12px; margin: 12px 0; }
.sample-form label { display: grid; gap: 4px; font-size: 13px; font-weight: 600; color: var(--text-secondary); }
.matrix { margin: 14px 0; }
textarea { min-width: 10rem; resize: vertical; }
</style>
