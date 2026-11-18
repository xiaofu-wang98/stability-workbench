<script setup lang="ts">
import { provide, ref, watch } from 'vue';
import { useRoute } from 'vue-router';
import { NConfigProvider, zhCN, dateZhCN } from 'naive-ui';
import { themeSettings } from './soybean-settings';

const route = useRoute();
const activeProject = ref('');
provide('workbenchProject', activeProject);
watch(() => [route.params.project, route.query.project], ([pathProject, queryProject]) => {
  const project = pathProject || queryProject;
  if (typeof project === 'string' && project) activeProject.value = project;
}, { immediate: true });

const links = [
  ['/', '工作台'],
  ['/new', '新建研究表'],
  ['/projects', '项目数据'],
  ['/specifications', '质量标准与 COA'],
  ['/import', '导入文件'],
  ['/trends', '趋势与比较'],
  ['/deliveries', '生成与历史'],
];
function destination(path: string) {
  if (!activeProject.value || path === '/') return path;
  return path === '/projects' ? path + '/' + encodeURIComponent(activeProject.value)
    : { path, query: { project: activeProject.value } };
}
</script>

<template>
  <NConfigProvider :locale="zhCN" :date-locale="dateZhCN" :theme-overrides="{ common: { primaryColor: themeSettings.themeColor, primaryColorHover: '#307972', primaryColorPressed: '#174a45', borderRadius: themeSettings.themeRadius + 'px' } }">
    <div class="shell" :style="{ '--sidebar-width': themeSettings.sider.width + 'px' }">
      <aside class="sidebar">
        <RouterLink class="brand" to="/"><span class="brand-mark" aria-hidden="true">▤</span>稳研工作台</RouterLink>
        <section v-if="activeProject" class="sidebar-project" aria-label="当前项目">
          <span>当前项目</span>
          <RouterLink :to="destination('/projects')">{{ activeProject }}</RouterLink>
          <RouterLink :to="{ path: '/projects' }">切换项目</RouterLink>
        </section>
        <nav aria-label="主导航">
          <RouterLink v-for="[path, label] in links" :key="path" :to="destination(path)">{{ label }}</RouterLink>
        </nav>
        <div class="local"><span></span>本地工作区</div>
      </aside>
      <main><RouterView /></main>
    </div>
  </NConfigProvider>
</template>
