<script setup lang="ts">
import { onMounted, ref } from 'vue';
import { useRoute } from 'vue-router';
import { NAlert, NButton } from 'naive-ui';
import { api } from './api';
import CoaEditor from './CoaEditor.vue';
import ConfirmAssays from './ConfirmAssays.vue';

const route = useRoute();
type Summary = { id: string; filename: string };
type Specification = Summary & { scope: string; material_scope: string; linked_projects?: { project: string; count: number; is_current: number }[]; rows: {
  table: number; row: number; column: number; page?: number; category: string; item: string;
  method: string; sop: string; criterion: string;
}[] };
const list = ref<Summary[]>([]);
const selected = ref<Specification>();
const error = ref('');
const busy = ref(false);
const materials: Record<string, string> = { reference: '参考品', DS: '原液', DP: '制剂', placebo: '安慰剂' };

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
onMounted(() => run(async () => {
  await refresh();
  const requested = String(route.query.specification || '');
  const onlyStandard = list.value.length === 1 ? list.value[0].id : '';
  const id = requested || (route.query.sample ? onlyStandard : list.value[0]?.id);
  if (id) await readSpecification(id);
}));
async function readSpecification(id: string) {
  selected.value = await api('/specifications/' + encodeURIComponent(id));
}
function open(id: string) {
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
    await refresh();
  });
}
</script>

<template>
  <NAlert v-if="error" type="error" role="alert">{{ error }}</NAlert>
  <section class="panel">
    <h2>质量标准原件</h2>
    <p>选择 Word 后保存原件并生成待核对条款。核对并选择适用项目后加入检项字典，再用于研究表与 COA；上传原件不等于已确认项目检项。</p>
    <label>选择 Word 标准 <input type="file" accept=".docx" :disabled="busy" @change="upload"></label>
    <p v-if="busy" role="status">正在读取质量标准…</p>
    <ul class="list">
      <li v-for="item in list" :key="item.id"><NButton text :disabled="busy" @click="open(item.id)">{{ item.filename }}</NButton></li>
    </ul>
  </section>
  <section v-if="selected" class="panel">
    <h2>{{ selected.filename }}</h2>
    <p v-if="!busy" role="status">已保存原件，识别到 {{ selected.rows.length }} 条标准。下一步：核对下方检项、方法和标准原文，再用于建表。</p>
    <RouterLink v-if="selected.rows.length" :to="{ path: '/new', query: { project: route.query.project, specification: selected.id } }">使用此标准新建研究表</RouterLink>
    <p v-else>没有识别到标准条款。请核对 Word 表格，或在研究表中手工填写标准。</p>
    <p>适用对象 {{ materials[selected.material_scope] || '待核对' }}</p>
    <p>当前条款归属：{{ selected.linked_projects?.map(item => `${item.project}（${item.count} 条）`).join('、') || '尚未确认到项目' }}。</p>
    <ul v-if="materials[selected.material_scope] && selected.linked_projects?.length">
      <li v-for="link in selected.linked_projects" :key="link.project">
        {{ link.project }}
        <span v-if="link.is_current">当前适用标准</span>
        <NButton v-else text :disabled="busy" @click="makeCurrent(link.project)">设为当前适用标准</NButton>
      </li>
    </ul>
    <p class="source-scope">{{ selected.scope }}</p>
    <a :href="'/api/v1/specifications/' + selected.id + '/file'">下载标准原件</a>
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
  <ConfirmAssays v-if="selected?.rows.length" :key="'assays-' + selected.id" :specification="selected"
    :initial-project="String(route.query.project || '')" @registered="open(selected.id)" />
  <CoaEditor v-if="selected?.rows.length" :key="selected.id" :specification="selected" :initial-project="String(route.query.project || '')" :initial-batch="String(route.query.batch || '')" :initial-sample-id="String(route.query.sample || '')" />
</template>

<style scoped>
.source-scope { white-space: pre-wrap; }
.matrix { margin-top: 14px; }
</style>
