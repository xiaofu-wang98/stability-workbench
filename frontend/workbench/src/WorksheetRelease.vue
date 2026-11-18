<script setup lang="ts">
import { computed, ref, watch } from 'vue';
import { NAlert, NButton, NCheckbox, NInput, NSelect, NSpace, NTabPane, NTabs, NTag } from 'naive-ui';
import { api, type CoaZeroBatchPreview, type Worksheet } from './api';

const props = defineProps<{ sheet: Worksheet; busy: boolean }>();
const emit = defineEmits<{
  save: [object];
  copy: [object];
  'batch-copy': [object];
  cancel: [];
}>();

const activeTab = ref<'batch' | 'single'>('batch');

// --- Batch 0M State ---
const batchDate = ref('');
const batchBasis = ref('');
const batchBasisOptions = [
  '核对 COA 原件与放行日期，批量录入 0M',
  '复核放行检验报告后批量确认采用',
];
const batchPreview = ref<CoaZeroBatchPreview>();
const previewLoading = ref(false);
const previewError = ref('');
const selectedRowIds = ref<string[]>([]);

function clearBatchPreview() {
  batchPreview.value = undefined;
  selectedRowIds.value = [];
  previewError.value = '';
}

const reasonLabels: Record<string, string> = {
  release_reference_exists: '已设置放行引用',
  result_history_exists: '已有检测结果历史',
  multiple_coa_matches: '匹配到多份标准条款',
  invalid_coa_value: 'COA 数值格式不合法',
  no_applicable_coa: '无适用的 COA 条款',
  sample_missing: '研究表未绑定样品',
  standard_scope_conflict: '标准适用范围冲突',
  worksheet_slot_conflict: '工作表格位冲突',
  worksheet_occupied: '格位已被占用',
};

async function loadBatchPreview() {
  if (previewLoading.value || !batchDate.value) return;
  clearBatchPreview();
  previewLoading.value = true;
  try {
    const res = await api<CoaZeroBatchPreview>(
      '/worksheets/' + encodeURIComponent(props.sheet.id) + '/copy-coa-to-0m/batch/preview',
      {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ release_completed: batchDate.value }),
      }
    );
    batchPreview.value = res;
  } catch (e) {
    previewError.value = (e as Error).message;
  } finally {
    previewLoading.value = false;
  }
}

watch(batchDate, clearBatchPreview);

const matchedRows = computed(() => batchPreview.value?.rows.filter(r => r.status === 'matched') || []);
const existingRows = computed(() => batchPreview.value?.rows.filter(r => r.status === 'existing') || []);
const conflictRows = computed(() => batchPreview.value?.rows.filter(r => r.status === 'conflict') || []);
const unmatchedRows = computed(() => batchPreview.value?.rows.filter(r => r.status === 'unmatched') || []);

const allMatchedSelected = computed({
  get: () => matchedRows.value.length > 0 && selectedRowIds.value.length === matchedRows.value.length,
  set: (val: boolean) => {
    if (val) {
      selectedRowIds.value = matchedRows.value.map(r => r.row_id);
    } else {
      selectedRowIds.value = [];
    }
  },
});

function toggleRow(rowId: string) {
  const idx = selectedRowIds.value.indexOf(rowId);
  if (idx >= 0) selectedRowIds.value.splice(idx, 1);
  else selectedRowIds.value.push(rowId);
}

function confirmBatchCopy() {
  if (batchPreview.value?.release_completed !== batchDate.value || !selectedRowIds.value.length || !batchBasis.value.trim()) return;
  emit('batch-copy', {
    release_completed: batchDate.value,
    preview_fingerprint: batchPreview.value.preview_fingerprint,
    row_ids: selectedRowIds.value,
    basis: batchBasis.value.trim(),
  });
}

// --- Single Row State (preserved for full backward compatibility) ---
const row = ref<string | null>(null);
const chosen = ref<string | null>(null);
const singleDate = ref('');
const singleBasis = ref('');
const singleLoading = ref(false);
const singleError = ref('');
type Choice = { value: string; label: string; coa_specification_id?: string; coa_version?: number; fingerprint?: string };
const intent = ref<'correction' | 'retest' | 'keep'>('keep');
const selected = computed(() => choices.value.find(item => item.value === chosen.value));
const adopted = computed(() => props.sheet.cells.find(cell => cell.row_id === row.value &&
  props.sheet.points.some(point => point.id === cell.point_id && point.label === '0M'))?.adopted);
const choices = ref<Choice[]>([]);
const singleRows = computed(() => props.sheet.rows.filter(item => props.sheet.points.some(
  point => point.label === '0M' && point.required_row_ids.includes(item.id))));
const previous = computed(() => row.value ? props.sheet.release_references?.[row.value] : undefined);
const pending = computed(() => row.value ? props.sheet.pending_release_references?.[row.value] : undefined);

watch(row, async (id, _, cleanup) => {
  const controller = new AbortController();
  cleanup(() => controller.abort());
  intent.value = 'keep';
  choices.value = [];
  chosen.value = null;
  singleDate.value = '';
  singleError.value = '';
  singleLoading.value = Boolean(id);
  const item = singleRows.value.find(item => item.id === id);
  if (!item) return;
  try {
    const query = new URLSearchParams({ study: 'release', batch: props.sheet.identity.batch,
      item: item.item_id, method: item.method, limit: '1000' });
    const result = await api<{ cells: { slot_key: { presentation: string; study_id: string };
      adopted: { text: string; result_version_id: string } | null }[] }>(
      '/projects/' + encodeURIComponent(props.sheet.project) + '/result-cells?' + query, { signal: controller.signal });
    const coa = await api<{ specification_id: string; version: number; filename: string; value: string; fingerprint: string }[]>(
      '/worksheets/' + encodeURIComponent(props.sheet.id) + '/coa-release-options?row_id=' + encodeURIComponent(item.id),
      { signal: controller.signal });
    if (controller.signal.aborted) return;
    choices.value = [...result.cells.flatMap(cell => cell.adopted ? [{
      value: cell.adopted.result_version_id,
      label: [cell.adopted.text, cell.slot_key.presentation, cell.slot_key.study_id].filter(Boolean).join(' · '),
    }] : []), ...coa.map(option => ({
      value: option.fingerprint, fingerprint: option.fingerprint,
      label: [option.value, option.filename, 'COA 版本 ' + option.version].join(' · '),
      coa_specification_id: option.specification_id, coa_version: option.version,
    }))];
  } catch (e) {
    if (!controller.signal.aborted) singleError.value = (e as Error).message;
  } finally {
    if (!controller.signal.aborted) singleLoading.value = false;
  }
});

function copySingle() {
  if (!row.value || !selected.value?.fingerprint) return;
  emit('copy', { row_id: row.value, coa_fingerprint: selected.value.fingerprint, basis: singleBasis.value.trim(),
    expected_version_id: adopted.value?.result_version_id ?? null, intent: adopted.value ? intent.value : 'initial' });
}

function saveSingle(remove = false) {
  if (!row.value || !singleBasis.value.trim() || (!remove && (!chosen.value || !singleDate.value))) return;
  const option = choices.value.find(item => item.value === chosen.value);
  emit('save', { row_id: row.value, basis: singleBasis.value.trim(),
    ...(!remove && option?.coa_specification_id ? {
      coa_specification_id: option.coa_specification_id, coa_version: option.coa_version,
    } : { result_version_id: remove ? null : chosen.value }), release_completed: remove ? null : singleDate.value });
}
</script>

<template>
  <div class="release-panel panel">
    <div class="panel-header">
      <h2>从 COA 复制到 0M</h2>
      <NButton quaternary size="small" @click="emit('cancel')">关闭</NButton>
    </div>

    <NTabs v-model:value="activeTab" type="segment">
      <NTabPane name="batch" tab="批量复制 0M（原子操作）">
        <NAlert v-if="previewError" type="error" class="mb-3">{{ previewError }}</NAlert>

        <div class="batch-controls">
          <label class="date-label">
            放行检测完成日期
            <input v-model="batchDate" type="date" :max="sheet.start_date" :disabled="busy || previewLoading" />
          </label>
          <NButton :loading="previewLoading" :disabled="busy || !batchDate" @click="loadBatchPreview">
            刷新匹配预览
          </NButton>
        </div>

        <div v-if="batchPreview" class="preview-summary">
          <NSpace align="center">
            <span>共 {{ batchPreview.rows.length }} 项 0M 检项：</span>
            <NTag type="success" size="small">可复制 {{ matchedRows.length }} 项</NTag>
            <NTag v-if="existingRows.length" type="info" size="small">已有采用值 {{ existingRows.length }} 项</NTag>
            <NTag v-if="conflictRows.length" type="error" size="small">冲突 {{ conflictRows.length }} 项</NTag>
            <NTag v-if="unmatchedRows.length" type="warning" size="small">未匹配 {{ unmatchedRows.length }} 项</NTag>
          </NSpace>
        </div>

        <div v-if="batchPreview" class="batch-table-container">
          <table class="batch-table">
            <thead>
              <tr>
                <th style="width: 44px">
                  <NCheckbox v-model:checked="allMatchedSelected" :disabled="busy || !matchedRows.length" />
                </th>
                <th>检验项目</th>
                <th>检测方法 / 单位</th>
                <th style="width: 110px">匹配状态</th>
                <th>状态说明 / 冲突原因</th>
                <th>原采用值</th>
                <th>拟录入 COA 新值</th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="r in batchPreview.rows" :key="r.row_id" :class="{ 'row-matched': r.status === 'matched', 'row-conflict': r.status === 'conflict' }">
                <td>
                  <NCheckbox
                    :checked="selectedRowIds.includes(r.row_id)"
                    :disabled="busy || r.status !== 'matched'"
                    @update:checked="toggleRow(r.row_id)"
                  />
                </td>
                <td><strong>{{ r.label }}</strong></td>
                <td>{{ [r.method, r.unit].filter(Boolean).join(' · ') || '—' }}</td>
                <td>
                  <NTag v-if="r.status === 'matched'" type="success" size="small">匹配待复制</NTag>
                  <NTag v-else-if="r.status === 'existing'" type="info" size="small">已有采用值</NTag>
                  <NTag v-else-if="r.status === 'conflict'" type="error" size="small">存在冲突</NTag>
                  <NTag v-else type="warning" size="small">未匹配</NTag>
                </td>
                <td>
                  <span v-if="r.reason" :class="{ 'conflict-text': r.status === 'conflict' }">
                    {{ reasonLabels[r.reason] || r.reason }}
                  </span>
                  <span v-else class="muted">—</span>
                </td>
                <td>{{ r.adopted_value || '—' }}</td>
                <td>
                  <template v-if="r.candidates.length">
                    <span class="new-value">{{ r.candidates[0].value }}</span>
                    <small class="muted-source">{{ r.candidates[0].filename }} (v{{ r.candidates[0].version }})</small>
                  </template>
                  <span v-else class="muted">—</span>
                </td>
              </tr>
            </tbody>
          </table>
        </div>

        <div v-if="batchPreview" class="batch-submit-section">
          <label class="basis-label">
            批量处理依据（必填）
            <NInput v-model:value="batchBasis" placeholder="请说明本次批量复制依据" :disabled="busy" />
          </label>
          <div class="quick-reasons">
            <span class="quick-title">快捷依据：</span>
            <NButton v-for="opt in batchBasisOptions" :key="opt" size="tiny" tertiary @click="batchBasis = opt">
              {{ opt }}
            </NButton>
          </div>

          <div class="actions-bar">
            <NButton
              type="primary"
              size="medium"
              :disabled="busy || previewLoading || selectedRowIds.length === 0 || !batchBasis.trim()"
              @click="confirmBatchCopy"
            >
              一次确认批量复制（所选 {{ selectedRowIds.length }} 项）
            </NButton>
            <NButton :disabled="busy" @click="emit('cancel')">取消</NButton>
          </div>
        </div>
      </NTabPane>

      <NTabPane name="single" tab="单项放行引用 / 维护">
        <form class="single-form" @submit.prevent="selected?.fingerprint ? copySingle() : saveSingle()">
          <NAlert v-if="singleError" type="error" class="mb-3">{{ singleError }}</NAlert>
          <label>选择 0M 应检项目
            <NSelect v-model:value="row" placeholder="选择 0M 应检项目" :disabled="busy"
              :options="singleRows.map(item => ({ value: item.id, label: item.label + ' · ' + item.method }))" />
          </label>
          <p v-if="previous">
            <a v-if="previous.coa?.source_id" :href="'/api/v1/coa-sources/' + previous.coa.source_id + '/file'">下载所引 COA 原件 · 版本 {{ previous.coa.version }}</a>
            <span v-else-if="previous.coa">在线录入的 COA · 版本 {{ previous.coa.version }}</span>
            <span v-else>已引用版本 {{ previous.result_version_id }}</span>，放行完成日期 {{ previous.release_completed }}
          </p>
          <p v-if="pending">方案表 {{ pending.table }} · 行 {{ pending.row }} · 列 {{ pending.column }}：{{ pending.raw }}</p>
          <label v-if="row">选择已采用的放行结果
            <NSelect v-model:value="chosen" placeholder="选择已采用的放行结果" :disabled="busy || !row" :loading="singleLoading" :options="choices" />
          </label>
          <label v-if="!selected?.fingerprint && row">放行检测完成日期
            <input v-model="singleDate" type="date" :max="sheet.start_date" :disabled="busy">
          </label>
          <label v-if="row">处理说明
            <input v-model="singleBasis" :disabled="busy" maxlength="2000">
          </label>
          <div v-if="row" class="buttons">
            <label v-if="selected?.fingerprint && adopted">当前值 {{ adopted.text }}
              <select v-model="intent" :disabled="busy">
                <option value="keep">保留原值</option>
                <option value="correction">更正</option>
                <option value="retest">记录复测</option>
              </select>
            </label>
            <NButton v-if="selected?.fingerprint" attr-type="submit" type="primary"
              :disabled="busy || (!!adopted && intent !== 'keep' && !singleBasis.trim())">确认复制</NButton>
            <NButton v-else attr-type="submit" type="primary" :disabled="busy || !chosen || !singleDate || !singleBasis.trim()">确认引用</NButton>
            <NButton v-if="previous" :disabled="busy || !singleBasis.trim()" @click="saveSingle(true)">解除引用</NButton>
            <NButton :disabled="busy" @click="emit('cancel')">取消</NButton>
          </div>
        </form>
      </NTabPane>
    </NTabs>
  </div>
</template>

<style scoped>
.release-panel {
  background: #ffffff;
  border: 1px solid #c9ded7;
  border-radius: 8px;
  padding: 16px;
  margin: 14px 0;
}
.panel-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 12px;
}
.panel-header h2 {
  margin: 0;
  font-size: 16px;
  color: #174a45;
}
.mb-3 {
  margin-bottom: 12px;
}
.batch-controls {
  display: flex;
  align-items: center;
  gap: 14px;
  margin: 12px 0;
}
.date-label {
  display: flex;
  align-items: center;
  gap: 8px;
  font-weight: 500;
}
.date-label input {
  font: inherit;
  padding: 5px 8px;
  border: 1px solid #aabdb8;
  border-radius: 4px;
}
.preview-summary {
  margin: 10px 0;
  padding: 8px 12px;
  background: #f4f8f6;
  border-radius: 6px;
}
.batch-table-container {
  max-height: 420px;
  overflow-y: auto;
  border: 1px solid #dbe5e2;
  border-radius: 6px;
  margin: 12px 0;
}
.batch-table {
  width: 100%;
  border-collapse: collapse;
  font-size: 13px;
}
.batch-table th, .batch-table td {
  padding: 8px 10px;
  border-bottom: 1px solid #e7eeec;
  text-align: left;
}
.batch-table thead th {
  background: #eef6f3;
  color: #1a4a40;
  position: sticky;
  top: 0;
  z-index: 1;
}
.row-matched {
  background: #fbfdfc;
}
.row-conflict {
  background: #fdf5f5;
}
.conflict-text {
  color: #c53030;
  font-weight: 500;
}
.new-value {
  color: #18794e;
  font-weight: 600;
  display: block;
}
.muted-source {
  color: #718096;
  font-size: 11px;
}
.batch-submit-section {
  display: grid;
  gap: 10px;
  margin-top: 14px;
  padding-top: 12px;
  border-top: 1px solid #e2ece8;
}
.basis-label {
  display: grid;
  gap: 4px;
  font-weight: 500;
}
.quick-reasons {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 6px;
}
.quick-title {
  font-size: 12px;
  color: #637772;
}
.actions-bar {
  display: flex;
  gap: 10px;
  margin-top: 8px;
}
.single-form {
  display: grid;
  gap: 12px;
  margin-top: 10px;
}
.single-form label {
  display: grid;
  gap: 4px;
}
.single-form input, .single-form select {
  font: inherit;
  padding: 6px;
  border: 1px solid #aabdb8;
  border-radius: 4px;
}
.buttons {
  display: flex;
  gap: 10px;
  align-items: center;
}
</style>
