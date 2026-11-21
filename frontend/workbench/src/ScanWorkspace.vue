<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue';
import { NAlert, NButton, NInput, NSelect } from 'naive-ui';
import { api } from './api';

type Summary = { id: string; filename: string; status: string };
type Review = { revision: number; lines: Record<number, string> };
type Page = { page: number; review: Review; lines: { text: string; score: number; polygon: number[][] }[]; tables: { html: string }[] };
type Scan = Summary & { pages: Page[] };
const scans = ref<Summary[]>([]);
const scan = ref<Scan>();
const pageNumber = ref(1);
const error = ref('');
const busy = ref(false);
const selected = computed(() => scan.value?.pages.find(page => page.page === pageNumber.value));
const editing = ref(false);
const corrections = ref<Record<number, string>>({});
type Criterion = { table: number; row: number; column: number; item: string; method: string; sop: string; criterion: string };
const registering = ref(false);
const criteria = ref<Criterion[]>([]);
const material = ref('');
const scope = ref('');
const saved = ref('');
watch([pageNumber, () => scan.value?.id], () => {
  editing.value = false;
  corrections.value = {};
  registering.value = false;
  criteria.value = [];
  saved.value = '';
});
const sourceUrl = computed(() => scan.value ? '/api/v1/pdf-ocr/' + scan.value.id + '/file#page=' + pageNumber.value : '');
const tables = computed(() => selected.value?.tables.map(table => {
  const template = document.createElement('template');
  template.innerHTML = table.html;
  return Array.from(template.content.querySelectorAll<HTMLTableRowElement>('tr'), row =>
    Array.from(row.cells, cell => ({
      text: cell.innerText || cell.textContent || '',
      rows: Math.max(1, cell.rowSpan),
      columns: Math.max(1, cell.colSpan),
    })),
  );
}) ?? []);
const state: Record<string, string> = { archived: '已归档', running: '正在识别', completed: '识别完成', failed: '识别失败', cancelled: '已取消', interrupted: '上次识别中断' };
let timer: ReturnType<typeof setTimeout> | undefined;
let disposed = false;

async function refresh() { scans.value = await api('/pdf-ocr'); }
async function run(action: () => Promise<void>) {
  if (busy.value) return;
  busy.value = true;
  error.value = '';
  try { await action(); }
  catch (e) { error.value = (e as Error).message; }
  finally { busy.value = false; }
}
onMounted(() => run(refresh));
onBeforeUnmount(() => { disposed = true; clearTimeout(timer); });

async function open(id: string) {
  clearTimeout(timer);
  scan.value = await api<Scan>('/pdf-ocr/' + id);
  if (disposed) return;
  if (scan.value.status === 'running') {
    scheduleRefresh(id);
  } else await refresh();
}
function scheduleRefresh(id: string) {
  timer = setTimeout(() => {
    if (disposed || scan.value?.id !== id) return;
    if (busy.value) {
      scheduleRefresh(id);
      return;
    }
    void run(async () => {
      try { await open(id); }
      catch (e) {
        if (!disposed && scan.value?.id === id && scan.value.status === 'running') scheduleRefresh(id);
        throw e;
      }
    });
  }, 2500);
}
function select(id: string) { pageNumber.value = 1; return run(() => open(id)); }
function upload(event: Event) {
  const input = event.target as HTMLInputElement;
  const file = input.files?.[0];
  if (!file) return;
  const body = new FormData();
  body.set('file', file);
  input.value = '';
  pageNumber.value = 1;
  return run(async () => {
    const { id } = await api<{ id: string }>('/pdf-ocr', { method: 'POST', body });
    await open(id);
  });
}
function command(action: 'start' | 'cancel') {
  const id = scan.value?.id;
  if (!id) return;
  return run(async () => {
    await api('/pdf-ocr/' + id + '/' + action, { method: 'POST' });
    await open(id);
  });
}
function beginReview() {
  if (!selected.value) return;
  corrections.value = { ...selected.value.review.lines };
  editing.value = true;
}
function saveReview() {
  const id = scan.value?.id;
  const page = selected.value;
  if (!id || !page) return;
  return run(async () => {
    page.review = await api<Review>('/pdf-ocr/' + id + '/pages/' + page.page + '/review', {
      method: 'PUT', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ revision: page.review.revision, lines: corrections.value }),
    });
    editing.value = false;
  });
}
function addCriterion(table: number, row: number, column: number, recognized: string) {
  if (criteria.value.some(item => item.table === table && item.row === row)) {
    error.value = '此表行已选，可在下方编辑或移除。';
    return;
  }
  error.value = '';
  criteria.value.push({ table, row, column, item: '', method: '', sop: '', criterion: recognized.trim() });
}
function addManualCriterion() {
  const next = 1 + Math.max(0, ...criteria.value.filter(item => item.table === 0).map(item => item.row));
  addCriterion(0, next, 0, '');
}
function saveSpecification() {
  const id = scan.value?.id;
  if (!id || !Number.isInteger(pageNumber.value) || pageNumber.value < 1) return;
  return run(async () => {
    const result = await api<{ id: string }>('/pdf-ocr/' + id + '/specification', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ page: pageNumber.value, manual_page: !selected.value,
        material_scope: material.value, scope: scope.value, rows: criteria.value }),
    });
    saved.value = result.id;
    registering.value = false;
    criteria.value = [];
  });
}
</script>

<template>
  <NAlert v-if="error" type="error" role="alert">{{ error }}</NAlert>
  <section class="panel">
    <h2>扫描原件</h2>
    <label>选择 PDF <input type="file" accept=".pdf,application/pdf" :disabled="busy" @change="upload"></label>
    <ul class="list"><li v-for="item in scans" :key="item.id">
      <NButton text :disabled="busy" @click="select(item.id)">{{ item.filename }}</NButton>
      <span>{{ state[item.status] }}</span>
    </li></ul>
  </section>
  <section v-if="scan" class="panel">
    <h2>{{ scan.filename }}</h2>
    <p role="status">{{ state[scan.status] }} · 已读取 {{ scan.pages.length }} 页</p>
    <div class="scan-controls">
      <a :href="sourceUrl" target="_blank" rel="noopener">打开原件第 {{ pageNumber }} 页</a>
      <NButton v-if="scan.status === 'running'" :disabled="busy" @click="command('cancel')">取消识别</NButton>
      <NButton v-else-if="scan.status !== 'completed'" type="primary" :disabled="busy" @click="command('start')">开始识别</NButton>
    </div>
    <NAlert v-if="scan.status === 'failed'" type="error">识别失败。可以重试，或查看本地识别日志。</NAlert>
    <template v-if="scan.pages.length">
      <p>识别结果待与原件核对。</p>
      <NSelect :value="pageNumber" :disabled="busy || editing || registering" :options="scan.pages.map(page => ({ value: page.page, label: '第 ' + page.page + ' 页' }))" @update:value="pageNumber = $event" />
    </template>
    <label v-else>原件页码 <input v-model.number="pageNumber" type="number" min="1" step="1" :disabled="busy || registering"></label>
      <NButton v-if="!registering" :disabled="busy || editing || !Number.isInteger(pageNumber) || pageNumber < 1" @click="registering = true">登记本页标准条款</NButton>
      <p v-if="registering">{{ selected ? '可从识别表格选择标准值，也可对照 PDF 原件手动填写。' : '请打开 PDF 原件，核对页码并逐条录入。' }}</p>
      <p v-if="saved" role="status">标准条款已登记。 <RouterLink to="/specifications">打开质量标准库</RouterLink></p>
      <div class="scan-review">
      <iframe :key="sourceUrl" :src="sourceUrl" :title="'PDF 原件第 ' + pageNumber + ' 页'" loading="lazy" />
      <div class="scan-findings">
      <details v-if="selected" :open="editing || !tables.length">
        <summary>文字与位置</summary>
        <div v-if="scan.status === 'completed'" class="scan-controls">
          <NButton v-if="!editing" :disabled="busy" @click="beginReview">核对文字</NButton>
          <template v-else>
            <NButton type="primary" :loading="busy" @click="saveReview">保存本页核对</NButton>
            <NButton :disabled="busy" @click="editing = false">取消</NButton>
          </template>
          <span v-if="selected.review.revision">核对版本 {{ selected.review.revision }}</span>
        </div>
        <ul><li v-for="(line, index) in selected.lines" :key="index">
          <NInput v-if="editing" :value="corrections[index] ?? line.text" :disabled="busy" :maxlength="4000"
            :aria-label="'第 ' + (index + 1) + ' 行核对文字'" @update:value="corrections[index] = $event" />
          <template v-else>{{ selected.review.lines[index] ?? line.text }}</template>
          <small> · {{ Math.round(line.score * 100) }}% · {{ line.polygon[0]?.join(', ') }}</small>
          <small v-if="selected.review.lines[index] !== undefined"> · 原识别 {{ line.text }}</small>
        </li></ul>
      </details>
      <details v-for="(table, index) in tables" :key="index" :open="index === 0">
        <summary>识别表格 {{ index + 1 }}</summary>
        <div class="matrix"><table><tbody><tr v-for="(row, r) in table" :key="r">
          <td v-for="(cell, c) in row" :key="c" :rowspan="cell.rows" :colspan="cell.columns">
            <NButton v-if="registering && cell.text.trim()" text :disabled="busy" @click="addCriterion(index + 1, r + 1, c + 1, cell.text)">{{ cell.text }}</NButton>
            <template v-else>{{ cell.text }}</template>
          </td>
        </tr></tbody></table></div>
      </details>
      </div>
      </div>
      <form v-if="registering" class="scan-criteria" @submit.prevent="saveSpecification">
        <h3>核对待登记条款</h3>
        <label>适用对象 <select v-model="material" required :disabled="busy">
          <option value="">请选择</option><option value="DS">原液</option><option value="DP">制剂</option>
          <option value="reference">参考品</option><option value="placebo">安慰剂</option>
        </select></label>
        <label>适用范围原文或核对说明 <textarea v-model="scope" required maxlength="1000" :disabled="busy"></textarea></label>
        <fieldset v-for="(item, index) in criteria" :key="item.table + ':' + item.row" :disabled="busy">
          <legend v-if="item.table">表 {{ item.table }} · 行 {{ item.row }} · 单元格 {{ item.column }}</legend>
          <legend v-else>第 {{ pageNumber }} 页 · 人工录入条款 {{ item.row }}</legend>
          <label>检项 <input v-model="item.item" required maxlength="500"></label>
          <label>方法 <input v-model="item.method" required maxlength="500"></label>
          <label>SOP 编号 <input v-model="item.sop" maxlength="200"></label>
          <label>标准原文 <textarea v-model="item.criterion" required maxlength="4000"></textarea></label>
          <NButton @click="criteria.splice(index, 1)">移除条款</NButton>
        </fieldset>
        <div class="scan-controls">
          <NButton :disabled="busy || criteria.length >= 100" @click="addManualCriterion">手动添加条款</NButton>
          <NButton attr-type="submit" type="primary" :loading="busy" :disabled="!criteria.length">核对无误，登记条款</NButton>
          <NButton :disabled="busy" @click="registering = false; criteria = []">取消</NButton>
        </div>
      </form>
  </section>
</template>

<style scoped>
.scan-controls { display: flex; align-items: center; gap: 14px; margin-bottom: 14px; }
details { margin-top: 14px; }
.scan-review { display: grid; grid-template-columns: minmax(360px, 1fr) minmax(360px, 1fr); gap: 18px; margin-top: 14px; }
.scan-review iframe { width: 100%; min-height: 550px; height: 70vh; border: 1px solid var(--border); border-radius: var(--radius-md); }
.scan-findings { min-width: 0; max-height: 70vh; overflow: auto; }
.scan-criteria, .scan-criteria fieldset { display: grid; gap: 8px; max-width: 720px; margin-top: 12px; border-color: var(--border); border-radius: var(--radius-md); }
.scan-criteria label { display: grid; gap: 4px; font-size: 13px; font-weight: 500; color: var(--text-secondary); }
</style>
