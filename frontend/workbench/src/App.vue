<script setup lang="ts">
import { provide, ref, watch } from 'vue';
import { useRoute } from 'vue-router';
import { NConfigProvider, NMessageProvider, zhCN, dateZhCN, type GlobalThemeOverrides } from 'naive-ui';
import { themeSettings } from './soybean-settings';

const route = useRoute();
const activeProject = ref('');
provide('workbenchProject', activeProject);
watch(() => [route.params.project, route.query.project], ([pathProject, queryProject]) => {
  const project = pathProject || queryProject;
  if (typeof project === 'string' && project) activeProject.value = project;
}, { immediate: true });

const themeOverrides: GlobalThemeOverrides = {
  common: {
    primaryColor: themeSettings.themeColor,
    primaryColorHover: themeSettings.themeColorHover,
    primaryColorPressed: themeSettings.themeColorPressed,
    primaryColorSuppl: themeSettings.themeColorHover,
    infoColor: '#1e5aa8',
    infoColorHover: '#17488a',
    infoColorPressed: '#12396e',
    successColor: '#15803d',
    warningColor: '#b45309',
    errorColor: '#dc2626',
    borderRadius: themeSettings.themeRadius + 'px',
    borderColor: '#e2e8f0',
    textColorBase: '#0f172a',
    textColor1: '#0f172a',
    textColor2: '#334155',
    textColor3: '#64748b',
    fontFamily: '"Inter", "Segoe UI", "PingFang SC", "Microsoft YaHei", sans-serif',
  },
  Card: {
    borderRadius: '10px',
    borderColor: '#e2e8f0',
  },
  Button: {
    borderRadiusMedium: '6px',
    borderRadiusSmall: '6px',
  },
  Input: {
    borderRadius: '6px',
  },
};

type NavItem = { path: string; label: string; icon: string };
const navGroups: { title: string; items: NavItem[] }[] = [
  {
    title: '概览与研究',
    items: [
      { path: '/', label: '工作台', icon: 'home' },
      { path: '/new', label: '新建研究表', icon: 'plus' },
      { path: '/projects', label: '项目数据', icon: 'folder' },
    ],
  },
  {
    title: '数据与标准',
    items: [
      { path: '/specifications', label: '质量标准与 COA', icon: 'shield' },
      { path: '/import', label: '导入文件', icon: 'upload' },
    ],
  },
  {
    title: '分析与交付',
    items: [
      { path: '/trends', label: '趋势与比较', icon: 'trend' },
      { path: '/deliveries', label: '生成与历史', icon: 'package' },
    ],
  },
];

function destination(path: string) {
  if (!activeProject.value || path === '/') return path;
  return path === '/projects' ? path + '/' + encodeURIComponent(activeProject.value)
    : { path, query: { project: activeProject.value } };
}
</script>

<template>
  <NConfigProvider :locale="zhCN" :date-locale="dateZhCN" :theme-overrides="themeOverrides">
    <NMessageProvider placement="top-right">
      <div class="shell" :style="{ '--sidebar-width': themeSettings.sider.width + 'px' }">
        <aside class="sidebar">
          <RouterLink class="brand" to="/">
            <span class="brand-mark" aria-hidden="true">
              <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
                <path d="M9 3h6" /><path d="M10 3v6.2L5.2 17.8A2 2 0 0 0 6.9 21h10.2a2 2 0 0 0 1.7-3.2L14 9.2V3" />
                <path d="M8.5 14h7" />
              </svg>
            </span>
            稳研工作台
          </RouterLink>

          <section v-if="activeProject" class="sidebar-project" aria-label="当前项目">
            <div class="sidebar-project-head">
              <span>当前项目</span>
            </div>
            <RouterLink class="sidebar-project-code" :to="destination('/projects')">{{ activeProject }}</RouterLink>
            <RouterLink class="sidebar-project-switch" :to="{ path: '/projects' }">切换项目</RouterLink>
          </section>

          <nav aria-label="主导航">
            <template v-for="group in navGroups" :key="group.title">
              <div class="nav-group-label">{{ group.title }}</div>
              <RouterLink v-for="item in group.items" :key="item.path" :to="destination(item.path)">
                <svg v-if="item.icon === 'home'" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
                  <rect x="3" y="3" width="7" height="7" rx="1" /><rect x="14" y="3" width="7" height="7" rx="1" /><rect x="14" y="14" width="7" height="7" rx="1" /><rect x="3" y="14" width="7" height="7" rx="1" />
                </svg>
                <svg v-else-if="item.icon === 'plus'" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
                  <rect x="3" y="3" width="18" height="18" rx="2" /><path d="M12 8v8" /><path d="M8 12h8" />
                </svg>
                <svg v-else-if="item.icon === 'folder'" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
                  <path d="M3 7a2 2 0 0 1 2-2h4l2 2h8a2 2 0 0 1 2 2v8a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V7z" />
                </svg>
                <svg v-else-if="item.icon === 'shield'" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
                  <path d="M12 3l7 3v6c0 4.5-3 7.8-7 9-4-1.2-7-4.5-7-9V6l7-3z" /><path d="M9 12l2 2 4-4" />
                </svg>
                <svg v-else-if="item.icon === 'upload'" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
                  <path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4" /><polyline points="17 8 12 3 7 8" /><line x1="12" y1="3" x2="12" y2="15" />
                </svg>
                <svg v-else-if="item.icon === 'trend'" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
                  <polyline points="22 7 13.5 15.5 8.5 10.5 2 17" /><polyline points="16 7 22 7 22 13" />
                </svg>
                <svg v-else width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
                  <path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z" /><polyline points="14 2 14 8 20 8" /><line x1="8" y1="13" x2="16" y2="13" /><line x1="8" y1="17" x2="13" y2="17" />
                </svg>
                {{ item.label }}
              </RouterLink>
            </template>
          </nav>

          <div class="local"><span></span>本地工作区</div>
        </aside>
        <main><RouterView /></main>
      </div>
    </NMessageProvider>
  </NConfigProvider>
</template>
