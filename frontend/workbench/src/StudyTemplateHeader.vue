<script setup lang="ts">
import { computed } from 'vue';
import type { Worksheet } from './api';
const props = defineProps<{ sheet: Worksheet }>();
const study = computed(() => ({ long_term: '长期试验 / Long-term study', accelerated: '加速试验 / Accelerated study', stress: '强力破坏 / Stress study' }[props.sheet.identity.study] || props.sheet.identity.study));
</script>
<template>
  <div class="study-template-meta" aria-label="稳定性研究信息">
    <div class="field"><b>项目编号<br>Project No.</b><span class="option">{{ sheet.project }}</span></div>
    <div class="field"><b>产品名称<br>Product Name</b><span class="option">{{ sheet.product_name || '待确认' }}</span></div>
    <div class="field"><b>产品批号<br>Product Batch No.</b><span>{{ sheet.identity.batch }}</span></div>
    <div class="field"><b>规格<br>Specification</b><span>{{ sheet.specification_text || '待确认' }}</span></div>
    <div class="field"><b>开始日期<br>Starting Date</b><span>{{ sheet.start_date }}</span></div>
    <div class="field"><b>设备名称及编号</b><span>{{ sheet.equipment || '待确认' }}</span></div>
    <div class="field"><b>稳定性试验方案编号</b><span class="option">{{ sheet.protocol_number || '待确认' }}</span></div>
    <div class="field"><b>稳定性类型</b><span class="option">{{ study }}<br>{{ sheet.identity.sample_type === 'DS' ? '原液 DS' : sheet.identity.sample_type === 'DP' ? '制剂 DP' : sheet.identity.sample_type }}</span></div>
    <div class="field condition"><b>考察条件<br>Study Condition</b><span class="option">{{ sheet.identity.condition }}<br v-if="sheet.identity.orientation">{{ sheet.identity.orientation }}</span></div>
    <div class="field"><b>稳定性开展地点</b><span class="option">{{ sheet.location || '待确认' }}</span></div>
    <div class="field"><b>存放位置</b><span>{{ sheet.storage_location || '待确认' }}</span></div>
  </div>
</template>
<style scoped>
.study-template-meta {
  display: grid;
  grid-template-columns: repeat(4, minmax(0, 1fr));
  gap: 1px;
  background: var(--border);
  border: 1px solid var(--border);
  border-radius: var(--radius-md);
  overflow: hidden;
  margin: 16px 0;
  box-shadow: var(--shadow-card);
}
.field { min-width: 0; display: grid; grid-template-columns: minmax(112px, 34%) minmax(0, 1fr); }
.field b, .field span { padding: 9px 12px; white-space: pre-line; overflow-wrap: anywhere; font-size: 13px; }
.field b { background: var(--bg-subtle); color: var(--text-secondary); font-weight: 600; }
.field span { background: var(--bg-surface); color: var(--text-primary); }
.field .option { background: var(--brand-soft); color: var(--text-primary); }
.condition { grid-column: span 3; }
</style>
