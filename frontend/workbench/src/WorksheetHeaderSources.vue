<script setup lang="ts">
import { headerFieldLabels, type HeaderField, type HeaderMetadataPreview } from './api';

defineProps<{ preview: HeaderMetadataPreview }>();
const fields = Object.keys(headerFieldLabels) as HeaderField[];
</script>

<template>
  <details>
    <summary>查看历史表头来源</summary>
    <div v-for="field in fields" :key="field">
      <p><strong>{{ headerFieldLabels[field] }}</strong> · {{ preview[field] || '待确认' }}</p>
      <ul v-if="preview.metadata_sources[field].length">
        <li v-for="source in preview.metadata_sources[field]" :key="[source.sha256, source.sheet, source.cell].join(':')">
          {{ source.filename }} · {{ source.sheet }}!{{ source.cell }}
          <small v-if="source.reference_only">（原表标题，仅供核对）</small>
          <div style="white-space: pre-line">{{ source.raw }}</div>
        </li>
      </ul>
      <p v-else>未找到明确标注的来源。</p>
    </div>
  </details>
</template>
