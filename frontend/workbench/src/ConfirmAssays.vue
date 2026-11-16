<script setup lang="ts">
import { onMounted, ref } from 'vue';
import { NAlert, NButton, NSelect } from 'naive-ui';
import { api, type Project } from './api';

type Row = {
  table: number;
  row: number;
  item: string;
  category: string;
  method: string;
  criterion: string;
  item_zh?: string;
  item_en?: string;
  method_zh?: string;
  method_en?: string;
  component?: string;
  component_en?: string;
  unit?: string;
  result_type?: 'numeric' | 'qualitative';
};

const props = defineProps<{ specification: { id: string; filename?: string; rows: Row[] } }>();
const emit = defineEmits<{ registered: [] }>();
const projects = ref<Project[]>([]);
const project = ref('');
const error = ref('');
const message = ref('');
const busy = ref(false);

function bilingual(value: string) {
  if (!value) return ['', ''];
  const cleaned = value.replace(/\n+/g, ' ').trim();
  const lastZh = [...cleaned.matchAll(/[\u4e00-\u9fa5]/g)];
  if (lastZh.length > 0) {
    const last = lastZh[lastZh.length - 1];
    const pos = (last.index ?? 0) + 1;
    const zh = cleaned.slice(0, pos).trim();
    const en = cleaned.slice(pos).trim();
    return [zh, en];
  }
  return [cleaned, cleaned];
}

const allSelected = ref(true);

const items = ref(props.specification.rows.map(row => {
  const [defaultAssayZh, defaultAssayEn] = bilingual(row.item || row.category);
  const assay_zh = row.item_zh || defaultAssayZh || '';
  const assay_en = row.item_en || defaultAssayEn || '';
  const [defaultMethodZh] = bilingual(row.method || '');
  const method = row.method_zh || defaultMethodZh || row.method || '';
  const comp_zh = row.component || assay_zh || '';
  const comp_en = row.component_en || assay_en || '';
  let unit = row.unit || '';
  let result_type = row.result_type || 'numeric';

  // 行业领域字典兜底填充（若解析未抓取到）
  if (!unit) {
    if (/纯度|SEC|CE-SDS|CEX|变异体|活性|峰/i.test(assay_zh) || /%/i.test(row.criterion)) unit = '%';
    else if (/DNA|HCP|蛋白A|残留/i.test(assay_zh) || /ppm/i.test(row.criterion)) unit = 'ppm';
    else if (/浓度|含量/i.test(assay_zh) || /mg\/ml/i.test(row.criterion)) unit = 'mg/ml';
    else if (/内毒素/i.test(assay_zh) || /EU/i.test(row.criterion)) unit = 'EU/ml';
    else if (/微生物/i.test(assay_zh) || /cfu/i.test(row.criterion)) unit = 'cfu/10ml';
    else if (/渗透压/i.test(assay_zh) || /mOsmol/i.test(row.criterion)) unit = 'mOsmol/kg';
  }
  if (/比色液|黄色|浊度|外观|颜色|澄清度|图谱一致|肽图|无菌/i.test(row.criterion || assay_zh)) {
    if (!/mg|ppm|EU|%|cfu|NTU|\~|～/i.test(row.criterion || '')) {
      result_type = 'qualitative';
    }
  }

  return {
    table: row.table,
    row: row.row,
    selected: true,
    assay_zh,
    assay_en,
    component_zh: comp_zh,
    component_en: comp_en,
    method,
    method_version: '',
    unit,
    result_type,
  };
}));

function toggleAll() {
  items.value.forEach(item => { item.selected = allSelected.value; });
}

onMounted(async () => {
  try {
    projects.value = await api('/projects');
    // 智能自动匹配项目代码
    if (!project.value) {
      const fn = (props.specification.filename || '').toUpperCase();
      const matches = projects.value.filter(p => {
        const pCode = p.project.toUpperCase();
        return fn.split(/[^A-Z0-9]+/).some(token => token === pCode || token.replace(/^IP/, '') === pCode.replace(/^IP/, ''));
      });
      if (matches.length === 1) {
        project.value = matches[0].project;
      } else if (projects.value.length === 1) {
        project.value = projects.value[0].project;
      }
    }
  } catch (e) {
    error.value = (e as Error).message;
  }
});

async function confirm() {
  error.value = '';
  message.value = '';
  if (!project.value) {
    error.value = '请先在上方下拉框中选择适用项目（例如 IP501 / 501）！';
    return;
  }
  busy.value = true;
  try {
    const components = items.value.filter(item => item.selected)
      .map(({ selected, ...item }) => ({ ...item, method_version: item.method_version || 'not_reported' }));
    if (components.some(item => !item.assay_zh.trim() || !item.component_zh.trim() || !item.method.trim() || !item.result_type)) {
      throw new Error('请补齐所选条款的检项、子结果、方法和结果类型。');
    }
    const base = '/projects/' + encodeURIComponent(project.value);
    const current = await api<{ base_revision: number }>(base + '/configuration');
    await api(base + '/specifications/' + props.specification.id + '/components', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ revision: current.base_revision, components, idempotency_key: crypto.randomUUID() }),
    });
    message.value = `已关联 ${components.length} 条标准至项目 ${project.value}，二级子结果可在稳定性研究表中自动复用。`;
    emit('registered');
  } catch (e) {
    error.value = (e as Error).message;
  } finally {
    busy.value = false;
  }
}
</script>

<template>
  <details class="panel" open>
    <summary style="font-weight: bold; cursor: pointer;">确认条款并加入项目检项字典</summary>
    <NAlert v-if="error" type="error" role="alert" style="margin-top: 8px;">{{ error }}</NAlert>
    <NAlert v-if="!project" type="warning" role="alert" style="margin-top: 8px;">
      ⚠️ 请先在下方选择适用项目（如 {{ projects.map(p => p.project).join(', ') || 'IP501' }}），以激活确认入库功能。
    </NAlert>
    <p v-if="message" role="status" style="color: #67c23a; font-weight: bold; margin-top: 8px;">{{ message }}</p>
    <form @submit.prevent="confirm" style="margin-top: 8px;">
      <div style="display: flex; align-items: center; gap: 12px; margin-bottom: 12px; background: #fafafa; padding: 10px 14px; border-radius: 6px; border: 1px solid #ebeef5;">
        <span style="font-weight: bold; white-space: nowrap;">适用项目：</span>
        <div style="min-width: 280px; max-width: 420px; flex: 1;">
          <NSelect
            v-model:value="project"
            filterable
            clearable
            placeholder="输入项目编号或名称快速检索 (如 501 / IP281)..."
            :options="projects.map(item => ({ value: item.project, label: item.project + (item.name ? ' · ' + item.name : '') }))"
          />
        </div>
        <span v-if="project" style="color: #67c23a; font-size: 13px; white-space: nowrap;">✓ 已选定项目：{{ project }}</span>
        <span v-else style="color: #e6a23c; font-size: 13px; white-space: nowrap;">⚠️ 必须选择项目方可入库字典</span>
      </div>
      <div class="matrix">
        <table>
          <thead>
            <tr>
              <th><label><input type="checkbox" v-model="allSelected" @change="toggleAll"> 标准条款（全选/取消）</label></th>
              <th>检项组 (中/英)</th>
              <th>子结果 (中/英)</th>
              <th>检测方法及版本</th>
              <th>单位及结果类型</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="(item, index) in items" :key="item.table + ':' + item.row">
              <td style="max-width: 320px;">
                <label style="font-weight: bold; display: block; margin-bottom: 4px;">
                  <input v-model="item.selected" type="checkbox"> 表 {{ item.table }} · 行 {{ item.row }}
                </label>
                <div style="white-space: pre-wrap; font-size: 12px; color: #606266; line-height: 1.4;">
                  {{ props.specification.rows[index]?.criterion }}
                </div>
              </td>
              <td>
                <input v-model="item.assay_zh" aria-label="检项名称" placeholder="中文检项" style="margin-bottom: 4px; width: 100%;">
                <input v-model="item.assay_en" aria-label="检项英文名" placeholder="English Assay" style="width: 100%;">
              </td>
              <td>
                <input v-model="item.component_zh" aria-label="子结果名称" placeholder="中文子结果" style="margin-bottom: 4px; width: 100%;">
                <input v-model="item.component_en" aria-label="子结果英文名" placeholder="English Component" style="width: 100%;">
              </td>
              <td>
                <input v-model="item.method" aria-label="方法" placeholder="检测方法" style="margin-bottom: 4px; width: 100%;">
                <input v-model="item.method_version" aria-label="方法版本" placeholder="版本(默认not_reported)" style="width: 100%;">
              </td>
              <td>
                <input v-model="item.unit" aria-label="单位" placeholder="单位(如 %, ppm, mg/ml)" style="margin-bottom: 4px; width: 100%;">
                <select v-model="item.result_type" aria-label="结果类型" style="width: 100%; padding: 4px;">
                  <option value="">选择类型</option>
                  <option value="numeric">数值 (numeric)</option>
                  <option value="qualitative">文字 (qualitative)</option>
                </select>
              </td>
            </tr>
          </tbody>
        </table>
      </div>
      <div style="margin-top: 14px; display: flex; align-items: center; gap: 14px;">
        <NButton attr-type="submit" :type="project ? 'primary' : 'default'" :disabled="busy || !items.some(item => item.selected)">
          {{ !project ? '请先在上方选择适用项目' : `确认所选 ${items.filter(i => i.selected).length} 条标准并入库` }}
        </NButton>
        <span v-if="!project" style="color: #e6a23c; font-size: 13px;">⚠️ 请在上方指定项目（如 IP501 / 501）后点击提交</span>
      </div>
    </form>
  </details>
</template>
