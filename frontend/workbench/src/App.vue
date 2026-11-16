<script setup lang="ts">
import { NConfigProvider, zhCN, dateZhCN } from 'naive-ui';
import { themeSettings } from './soybean-settings';

const links = [
  ['/', '工作台'],
  ['/projects', '项目数据'],
  ['/import', '导入文件'],
  ['/trends', '趋势与比较'],
  ['/deliveries', '生成与历史'],
];
</script>

<template>
  <NConfigProvider :locale="zhCN" :date-locale="dateZhCN" :theme-overrides="{ common: { primaryColor: themeSettings.themeColor, primaryColorHover: '#307972', primaryColorPressed: '#174a45', borderRadius: themeSettings.themeRadius + 'px' } }">
    <div class="shell" :style="{ '--sidebar-width': themeSettings.sider.width + 'px' }">
      <aside class="sidebar">
        <RouterLink class="brand" to="/"><span class="brand-mark" aria-hidden="true">▤</span>稳研工作台</RouterLink>
        <nav aria-label="主导航">
          <RouterLink v-for="[path, label] in links" :key="path" :to="path">{{ label }}</RouterLink>
        </nav>
        <div class="local"><span></span>本地工作区</div>
      </aside>
      <main><RouterView /></main>
    </div>
  </NConfigProvider>
</template>
