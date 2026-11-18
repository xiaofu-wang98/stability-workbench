<script setup lang="ts">
import type { HeaderField, HeaderMetadataPreview } from './api';

defineProps<{ preview: HeaderMetadataPreview }>();
const labels: Record<HeaderField, string> = { equipment: '设备名称及编号', location: '稳定性开展地点' };
</script>

<template>
  <details>
    <summary>查看历史表头来源</summary>
    <div v-for="field in (['equipment', 'location'] as const)" :key="field">
      <p><strong>{{ labels[field] }}</strong> · {{ preview[field] || '待确认' }}</p>
      <ul v-if="preview.metadata_sources[field].length">
        <li v-for="source in preview.metadata_sources[field]" :key="[source.sha256, source.sheet, source.cell].join(':')">
          {{ source.filename }} · {{ source.sheet }}!{{ source.cell }}
          <div style="white-space: pre-line">{{ source.raw }}</div>
        </li>
      </ul>
      <p v-else>未找到明确标注的来源。</p>
    </div>
  </details>
</template>
