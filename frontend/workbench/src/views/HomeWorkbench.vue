<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue';
import { NAlert, NButton, NEmpty, NSpin, NTag } from 'naive-ui';
import { useWorkbenchContextStore } from '../stores/workbenchContext';
import { useTodoAggregator, type TodoCategory } from '../composables/useTodoAggregator';
import { DOMAIN_DEFINITIONS } from '../domains/domainRegistry';

const ctx = useWorkbenchContextStore();
const todo = useTodoAggregator();

const categoryFilter = ref<'all' | TodoCategory>('all');

const activeProjectQuery = computed(() => (ctx.activeProject ? { project: ctx.activeProject } : undefined));

const workflowStages = computed(() => [
  {
    step: '01',
    title: '导入文件与核对',
    desc: '集中上传稳定性汇总表、放行 CoA、方案 Word、质量标准、扫描 PDF 或回传研究表模板',
    primaryLabel: '进入导入中心',
    primaryTo: { path: '/import', query: activeProjectQuery.value },
    shortcuts: [
      { label: '稳定性汇总 Excel', to: { path: '/import', query: { ...activeProjectQuery.value, mode: 'stability_source' } } },
      { label: '稳定性方案 Word', to: { path: '/import', query: { ...activeProjectQuery.value, mode: 'protocol' } } },
      { label: '研究表回传', to: { path: '/import', query: { ...activeProjectQuery.value, mode: 'workbook_return' } } },
      { label: '扫描 PDF (OCR)', to: { path: '/import', query: { ...activeProjectQuery.value, mode: 'scan_pdf' } } },
    ],
  },
  {
    step: '02',
    title: '标准登记与放行 COA',
    desc: '核对标准条款加入项目检项字典，登记样品批次并在线录入或回传放行 COA',
    primaryLabel: '打开标准与 COA',
    primaryTo: { path: '/specifications', query: activeProjectQuery.value },
    shortcuts: [
      { label: '上传/登记质量标准', to: { path: '/specifications', query: { ...activeProjectQuery.value, focus: 'assays' } } },
      { label: '录入批次放行 COA', to: { path: '/specifications', query: { ...activeProjectQuery.value, focus: 'coa' } } },
    ],
  },
  {
    step: '03',
    title: '建立与维护研究表',
    desc: '按方案矩阵、质量标准或复制旧表结构建表，在线录数、批量关联 0M 与受控核对',
    primaryLabel: '创建新研究表',
    primaryTo: { path: '/new', query: activeProjectQuery.value },
    shortcuts: [
      { label: '从方案矩阵建表', to: { path: '/new', query: { ...activeProjectQuery.value, start: 'protocol' } } },
      { label: '按质量标准建表', to: { path: '/new', query: { ...activeProjectQuery.value, start: 'standard' } } },
      { label: '复制旧表结构', to: { path: '/new', query: { ...activeProjectQuery.value, start: 'copy' } } },
    ],
  },
  {
    step: '04',
    title: '台账查询、趋势与交付',
    desc: '按项目与批次检索全部样品及结果，绘制带标准线的批间趋势图并输出受控交付件',
    primaryLabel: ctx.activeProject ? `进入 ${ctx.activeProject} 台账` : '查看全部项目台账',
    primaryTo: ctx.activeProject ? '/projects/' + encodeURIComponent(ctx.activeProject) : '/projects',
    shortcuts: [
      { label: '批间趋势对比', to: { path: '/trends', query: activeProjectQuery.value } },
      { label: '受控导出与报告', to: { path: '/deliveries', query: activeProjectQuery.value } },
    ],
  },
]);

const filteredTodos = computed(() => {
  if (categoryFilter.value === 'all') return todo.items.value;
  return todo.items.value.filter(item => item.category === categoryFilter.value);
});

const categoryCounts = computed(() => {
  const counts: Record<string, number> = { all: todo.items.value.length };
  for (const item of todo.items.value) {
    counts[item.category] = (counts[item.category] || 0) + 1;
  }
  return counts;
});

const quickProjects = computed(() => {
  const byCode = new Map(ctx.projects.map(p => [p.project, p]));
  const result: { project: string; name: string; pinned: boolean; recent: boolean }[] = [];
  const added = new Set<string>();

  for (const code of ctx.pinnedProjects) {
    const p = byCode.get(code);
    if (!p || added.has(code)) continue;
    added.add(code);
    result.push({ project: p.project, name: p.name, pinned: true, recent: false });
  }
  for (const item of ctx.recentProjects) {
    const p = byCode.get(item.project);
    if (!p || added.has(item.project)) continue;
    added.add(item.project);
    result.push({ project: p.project, name: p.name, pinned: false, recent: true });
  }
  if (result.length < 6) {
    for (const p of ctx.projects) {
      if (added.has(p.project)) continue;
      added.add(p.project);
      result.push({ project: p.project, name: p.name, pinned: false, recent: false });
      if (result.length >= 6) break;
    }
  }
  return result.slice(0, 6);
});

const plannedDomains = computed(() => DOMAIN_DEFINITIONS.filter(d => d.status === 'planned'));

function deliveryHref(item: { id: string; filename: string }) {
  return '/api/v1/' + (item.filename === 'report.zip' ? 'reports/' : 'exports/') + encodeURIComponent(item.id) + '/file';
}

function reloadTodos() {
  void todo.refresh(ctx.monitoredProjects);
}

onMounted(async () => {
  await ctx.loadProjects();
  reloadTodos();
});

watch(
  () => [ctx.monitoredProjects.join(','), ctx.todoScopeMode],
  () => {
    if (ctx.projectsLoaded) reloadTodos();
  },
);
</script>

<template>
  <div class="home-workbench">
    <!-- Zone 1: 4-Stage Workflow Launchpad -->
    <section class="workflow-launchpad" aria-label="核心业务流水线">
      <div class="launchpad-head">
        <div>
          <h2>标准业务流水线</h2>
          <p>按实验室日常作业链路快速进入对应工位，上下文（项目 / 批号 / 样品）自动穿透至下游。</p>
        </div>
        <div v-if="ctx.activeProject" class="active-scope-pill">
          当前聚焦项目：<strong>{{ ctx.activeProject }}</strong>
          <span v-if="ctx.activeProjectMeta?.name">· {{ ctx.activeProjectMeta.name }}</span>
        </div>
      </div>

      <div class="stage-grid">
        <article v-for="stage in workflowStages" :key="stage.step" class="stage-card">
          <div class="stage-card-top">
            <span class="stage-num">STEP {{ stage.step }}</span>
            <h3>{{ stage.title }}</h3>
            <p class="stage-desc">{{ stage.desc }}</p>
          </div>
          <div class="stage-shortcuts">
            <RouterLink v-for="sc in stage.shortcuts" :key="sc.label" :to="sc.to" class="shortcut-chip">
              {{ sc.label }}
            </RouterLink>
          </div>
          <div class="stage-card-foot">
            <RouterLink :to="stage.primaryTo" class="stage-primary-cta">
              {{ stage.primaryLabel }} <span aria-hidden="true">→</span>
            </RouterLink>
          </div>
        </article>
      </div>
    </section>

    <!-- Zone 2 & Zone 3: Desktop 2-Column Main Workbench -->
    <div class="home-main-grid">
      <!-- Bottom-Left: Live Pending & Resume Queue -->
      <section class="panel todo-panel" aria-label="实时待办与续做队列">
        <div class="panel-head-row">
          <div>
            <h2>待办与续做队列</h2>
            <p class="panel-sub">
              <template v-if="ctx.todoScopeMode === 'followed'">
                实时监控全局方案/扫描件 + 个人关注项目（{{ ctx.monitoredProjects.length ? ctx.monitoredProjects.join('、') : '暂无项目' }}）
              </template>
              <template v-else>
                仅监控全局草稿 + 当前项目（{{ ctx.activeProject || '未选定项目' }}）
              </template>
            </p>
          </div>
          <div class="todo-controls">
            <div class="scope-switch" role="group" aria-label="待办监控范围">
              <button
                type="button"
                :class="{ active: ctx.todoScopeMode === 'followed' }"
                @click="ctx.setTodoScopeMode('followed')"
              >
                我关注的项目
              </button>
              <button
                type="button"
                :class="{ active: ctx.todoScopeMode === 'active_only' }"
                @click="ctx.setTodoScopeMode('active_only')"
              >
                仅当前项目
              </button>
            </div>
            <NButton size="small" secondary :loading="todo.loading.value" @click="reloadTodos">刷新</NButton>
          </div>
        </div>

        <div class="todo-filter-strip">
          <button type="button" class="filter-chip" :class="{ active: categoryFilter === 'all' }" @click="categoryFilter = 'all'">
            全部 <span>{{ categoryCounts.all || 0 }}</span>
          </button>
          <button
            v-if="categoryCounts.local_recovery"
            type="button"
            class="filter-chip"
            :class="{ active: categoryFilter === 'local_recovery' }"
            @click="categoryFilter = 'local_recovery'"
          >
            本地未提交 <span>{{ categoryCounts.local_recovery }}</span>
          </button>
          <button
            v-if="categoryCounts.import_draft"
            type="button"
            class="filter-chip"
            :class="{ active: categoryFilter === 'import_draft' }"
            @click="categoryFilter = 'import_draft'"
          >
            未提交导入 <span>{{ categoryCounts.import_draft }}</span>
          </button>
          <button
            v-if="categoryCounts.worksheet_draft"
            type="button"
            class="filter-chip"
            :class="{ active: categoryFilter === 'worksheet_draft' }"
            @click="categoryFilter = 'worksheet_draft'"
          >
            待补全研究表 <span>{{ categoryCounts.worksheet_draft }}</span>
          </button>
          <button
            v-if="categoryCounts.pending_result"
            type="button"
            class="filter-chip"
            :class="{ active: categoryFilter === 'pending_result' }"
            @click="categoryFilter = 'pending_result'"
          >
            待复核结果 <span>{{ categoryCounts.pending_result }}</span>
          </button>
          <button
            v-if="categoryCounts.scan_ocr"
            type="button"
            class="filter-chip"
            :class="{ active: categoryFilter === 'scan_ocr' }"
            @click="categoryFilter = 'scan_ocr'"
          >
            扫描 PDF <span>{{ categoryCounts.scan_ocr }}</span>
          </button>
          <button
            v-if="categoryCounts.protocol_draft"
            type="button"
            class="filter-chip"
            :class="{ active: categoryFilter === 'protocol_draft' }"
            @click="categoryFilter = 'protocol_draft'"
          >
            已上传方案 <span>{{ categoryCounts.protocol_draft }}</span>
          </button>
        </div>

        <NAlert v-if="todo.error.value" type="error" style="margin-bottom: 12px;">{{ todo.error.value }}</NAlert>

        <div v-if="todo.loading.value && !todo.items.value.length" class="todo-loading">
          <NSpin size="small" />
          <span>正在聚合实时待办与未完成草稿…</span>
        </div>

        <ul v-else-if="filteredTodos.length" class="todo-list">
          <li v-for="item in filteredTodos" :key="item.id" class="todo-row">
            <div class="todo-row-main">
              <div class="todo-row-head">
                <NTag
                  size="small"
                  :type="item.severity === 'error' ? 'error' : item.severity === 'warning' ? 'warning' : 'info'"
                >
                  {{ item.categoryLabel }}
                </NTag>
                <span v-if="item.project" class="todo-project-code">{{ item.project }}</span>
                <strong class="todo-title">{{ item.title }}</strong>
              </div>
              <p class="todo-detail">{{ item.detail }}</p>
            </div>
            <RouterLink :to="item.to" class="todo-action-btn">
              {{ item.actionLabel }} →
            </RouterLink>
          </li>
        </ul>

        <div v-else class="todo-empty">
          <NEmpty description="当前监控范围内没有积压的未提交导入或待补全计划。" />
          <div class="todo-empty-actions">
            <RouterLink class="shortcut-chip" to="/import">导入新文件</RouterLink>
            <RouterLink class="shortcut-chip" to="/projects">前往全部项目台账</RouterLink>
          </div>
        </div>
      </section>

      <!-- Bottom-Right: Pinned/Recent Projects + Recent Sheets + Recent Deliveries -->
      <div class="home-side-column">
        <section class="panel side-widget" aria-label="置顶与最近项目">
          <div class="panel-head-row compact">
            <div>
              <h2>常用与最近项目</h2>
              <p class="panel-sub">星标置顶的项目将自动纳入左侧待办监控</p>
            </div>
            <RouterLink class="view-all-link" to="/projects">
              全部项目（{{ ctx.projects.length }}）→
            </RouterLink>
          </div>

          <ul v-if="quickProjects.length" class="quick-project-list">
            <li v-for="p in quickProjects" :key="p.project" class="quick-project-row">
              <button
                type="button"
                class="mini-pin-btn"
                :class="{ pinned: ctx.isPinned(p.project) }"
                :title="ctx.isPinned(p.project) ? '取消置顶' : '置顶关注'"
                @click="ctx.togglePin(p.project)"
              >
                ★
              </button>
              <RouterLink class="qp-info" :to="'/projects/' + encodeURIComponent(p.project)">
                <span class="qp-code">{{ p.project }}</span>
                <span class="qp-name">{{ p.name || '未填写名称' }}</span>
              </RouterLink>
              <div class="qp-actions">
                <NTag v-if="p.pinned" size="tiny" type="warning">置顶</NTag>
                <NTag v-else-if="p.recent" size="tiny" type="info">最近</NTag>
                <RouterLink class="qp-mini-link" :to="{ path: '/import', query: { project: p.project } }">导入</RouterLink>
                <RouterLink class="qp-mini-link" :to="{ path: '/new', query: { project: p.project } }">建表</RouterLink>
              </div>
            </li>
          </ul>
          <p v-else class="muted" style="margin: 12px 0 0;">
            当前数据目录还没有项目。可前往 <RouterLink to="/import">导入中心</RouterLink> 或 <RouterLink to="/projects">项目台账</RouterLink> 新建项目。
          </p>

          <div v-if="ctx.recentSheets.length" class="recent-sheets-block">
            <div class="sub-block-title">最近打开的研究表</div>
            <div class="recent-sheet-chips">
              <RouterLink
                v-for="ws in ctx.recentSheets.slice(0, 4)"
                :key="ws.id"
                :to="'/worksheets/' + encodeURIComponent(ws.id)"
                class="recent-sheet-chip"
              >
                <span class="rs-proj">{{ ws.project }}</span>
                <span class="rs-name">{{ ws.name }}</span>
              </RouterLink>
            </div>
          </div>
        </section>

        <section class="panel side-widget" aria-label="最近交付记录">
          <div class="panel-head-row compact">
            <h2>最近交付动态</h2>
            <RouterLink class="view-all-link" to="/deliveries">交付历史 →</RouterLink>
          </div>
          <ul v-if="todo.recentDeliveries.value.length" class="delivery-mini-list">
            <li v-for="item in todo.recentDeliveries.value.slice(0, 5)" :key="item.id">
              <div class="deliv-main">
                <span class="deliv-proj">{{ item.project }}</span>
                <a :href="deliveryHref(item)" class="deliv-name">{{ item.filename }}</a>
                <span v-if="item.report_version" class="deliv-ver">v{{ item.report_version }}</span>
              </div>
              <span class="deliv-time">{{ item.created_at }}</span>
            </li>
          </ul>
          <p v-else class="muted" style="margin: 10px 0 0;">暂无报告或受控表格生成记录。</p>
        </section>
      </div>
    </div>

    <!-- Future Multi-Domain Roadmap Strip (Subtle & Extensible) -->
    <section class="future-domains-strip" aria-label="扩展业务域预留">
      <div class="future-strip-label">工作台统一架构已预留扩展域</div>
      <div class="future-domain-pills">
        <div v-for="d in plannedDomains" :key="d.id" class="future-domain-card">
          <div class="fd-head">
            <strong>{{ d.label }}</strong>
            <NTag size="tiny">规划预留</NTag>
          </div>
          <span>维度：{{ d.futureScopeHint }}</span>
        </div>
      </div>
    </section>
  </div>
</template>

<style scoped>
.home-workbench {
  display: grid;
  gap: 22px;
}

.workflow-launchpad {
  background: var(--bg-surface);
  border: 1px solid var(--border);
  border-radius: var(--radius-lg);
  padding: 22px 26px;
  box-shadow: var(--shadow-card);
}
.launchpad-head {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 16px;
  margin-bottom: 18px;
}
.launchpad-head h2 {
  margin: 0 0 4px;
  font-size: 18px;
}
.launchpad-head p {
  margin: 0;
  color: var(--text-secondary);
  font-size: 14px;
}
.active-scope-pill {
  padding: 6px 14px;
  border-radius: 999px;
  background: var(--brand-soft);
  border: 1px solid var(--brand-border);
  color: var(--brand);
  font-size: 13.5px;
  white-space: nowrap;
}

.stage-grid {
  display: grid;
  grid-template-columns: repeat(4, minmax(0, 1fr));
  gap: 16px;
}
.stage-card {
  display: flex;
  flex-direction: column;
  justify-content: space-between;
  gap: 14px;
  padding: 18px 20px;
  border-radius: var(--radius-md);
  border: 1px solid var(--border);
  background: var(--bg-subtle);
  transition: border-color 0.15s ease, box-shadow 0.15s ease, background-color 0.15s ease;
}
.stage-card:hover {
  background: var(--bg-surface);
  border-color: var(--brand-border);
  box-shadow: var(--shadow-raised);
}
.stage-num {
  display: inline-block;
  font-family: var(--font-mono);
  font-size: 11.5px;
  font-weight: 700;
  color: var(--brand);
  background: var(--brand-soft);
  border: 1px solid var(--brand-border);
  padding: 2px 8px;
  border-radius: 999px;
  margin-bottom: 10px;
}
.stage-card h3 {
  margin: 0 0 6px;
  font-size: 16.5px;
  color: var(--text-primary);
}
.stage-desc {
  margin: 0;
  font-size: 13.5px;
  color: var(--text-secondary);
  line-height: 1.55;
}
.stage-shortcuts {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
}
.shortcut-chip {
  display: inline-flex;
  align-items: center;
  padding: 4px 10px;
  font-size: 12.5px;
  font-weight: 500;
  color: var(--text-secondary);
  background: var(--bg-surface);
  border: 1px solid var(--border);
  border-radius: var(--radius-sm);
  transition: all 0.15s ease;
}
.shortcut-chip:hover {
  text-decoration: none;
  color: var(--brand);
  border-color: var(--brand-border);
  background: var(--brand-soft);
}
.stage-card-foot {
  padding-top: 12px;
  border-top: 1px solid var(--border);
}
.stage-primary-cta {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  font-size: 14.5px;
  font-weight: 600;
  color: var(--brand);
}
.stage-primary-cta:hover {
  color: var(--brand-hover);
  text-decoration: none;
}

.home-main-grid {
  display: grid;
  grid-template-columns: minmax(0, 1.55fr) minmax(380px, 1fr);
  gap: 20px;
  align-items: start;
}
.todo-panel, .side-widget {
  margin-top: 0;
}
.home-side-column {
  display: grid;
  gap: 20px;
}
.panel-head-row {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 12px;
  margin-bottom: 14px;
}
.panel-head-row.compact {
  align-items: center;
  margin-bottom: 12px;
}
.panel-head-row h2 {
  margin: 0;
  font-size: 17.5px;
}
.panel-sub {
  margin: 4px 0 0;
  font-size: 13px;
  color: var(--text-muted);
}
.todo-controls {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-shrink: 0;
}
.scope-switch {
  display: inline-flex;
  padding: 2px;
  background: var(--bg-subtle);
  border: 1px solid var(--border);
  border-radius: var(--radius-sm);
}
.scope-switch button {
  border: none;
  background: transparent;
  padding: 4px 10px;
  font: inherit;
  font-size: 12.5px;
  font-weight: 500;
  color: var(--text-secondary);
  border-radius: 4px;
  cursor: pointer;
}
.scope-switch button.active {
  background: var(--bg-surface);
  color: var(--brand);
  font-weight: 600;
  box-shadow: 0 1px 2px rgba(15, 23, 42, 0.08);
}

.todo-filter-strip {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  margin-bottom: 14px;
  padding-bottom: 12px;
  border-bottom: 1px solid var(--border);
}
.filter-chip {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  padding: 4px 11px;
  border-radius: 999px;
  border: 1px solid var(--border);
  background: var(--bg-surface);
  color: var(--text-secondary);
  font: inherit;
  font-size: 13px;
  font-weight: 500;
  cursor: pointer;
  transition: all 0.15s ease;
}
.filter-chip span {
  font-family: var(--font-mono);
  font-size: 12px;
  color: var(--text-muted);
}
.filter-chip.active {
  background: var(--brand-soft);
  border-color: var(--brand-border);
  color: var(--brand);
  font-weight: 600;
}
.filter-chip.active span { color: var(--brand); }

.todo-loading {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 28px 12px;
  color: var(--text-muted);
  font-size: 14px;
}

.todo-list {
  list-style: none;
  margin: 0;
  padding: 0;
  display: grid;
  gap: 10px;
}
.todo-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 16px;
  padding: 14px 16px;
  border-radius: var(--radius-md);
  border: 1px solid var(--border);
  background: var(--bg-subtle);
  transition: border-color 0.15s ease, background-color 0.15s ease;
}
.todo-row:hover {
  background: var(--bg-surface);
  border-color: var(--brand-border);
}
.todo-row-main {
  min-width: 0;
  flex: 1;
}
.todo-row-head {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
}
.todo-project-code {
  font-family: var(--font-mono);
  font-size: 12.5px;
  font-weight: 700;
  color: var(--brand);
  background: var(--brand-soft);
  padding: 1px 7px;
  border-radius: 4px;
}
.todo-title {
  font-size: 14.5px;
  color: var(--text-primary);
  overflow-wrap: anywhere;
}
.todo-detail {
  margin: 6px 0 0;
  font-size: 13px;
  color: var(--text-secondary);
}
.todo-action-btn {
  flex-shrink: 0;
  display: inline-flex;
  align-items: center;
  padding: 7px 14px;
  border-radius: var(--radius-sm);
  border: 1px solid var(--brand-border);
  background: var(--bg-surface);
  color: var(--brand);
  font-size: 13.5px;
  font-weight: 600;
  white-space: nowrap;
  transition: all 0.15s ease;
}
.todo-action-btn:hover {
  text-decoration: none;
  background: var(--brand);
  border-color: var(--brand);
  color: #ffffff;
}
.todo-empty {
  padding: 28px 12px 16px;
  text-align: center;
}
.todo-empty-actions {
  display: flex;
  justify-content: center;
  gap: 10px;
  margin-top: 14px;
}

.view-all-link {
  font-size: 13.5px;
  font-weight: 600;
  color: var(--brand);
  white-space: nowrap;
}
.quick-project-list {
  list-style: none;
  margin: 0;
  padding: 0;
  display: grid;
}
.quick-project-row {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 10px 4px;
  border-top: 1px solid var(--border);
}
.quick-project-row:first-child { border-top: none; }
.mini-pin-btn {
  border: none;
  background: transparent;
  color: #cbd5e1;
  font-size: 16px;
  cursor: pointer;
  padding: 0 2px;
  line-height: 1;
}
.mini-pin-btn:hover, .mini-pin-btn.pinned { color: #d97706; }
.qp-info {
  display: flex;
  align-items: baseline;
  gap: 8px;
  min-width: 0;
  flex: 1;
}
.qp-info:hover { text-decoration: none; }
.qp-code {
  font-family: var(--font-mono);
  font-size: 14px;
  font-weight: 700;
  color: var(--brand);
  flex-shrink: 0;
}
.qp-name {
  font-size: 13.5px;
  color: var(--text-secondary);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}
.qp-actions {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-shrink: 0;
}
.qp-mini-link {
  font-size: 12.5px;
  color: var(--text-secondary);
  padding: 2px 7px;
  border-radius: 4px;
  border: 1px solid var(--border);
  background: var(--bg-subtle);
}
.qp-mini-link:hover {
  text-decoration: none;
  color: var(--brand);
  border-color: var(--brand-border);
  background: var(--brand-soft);
}

.recent-sheets-block {
  margin-top: 14px;
  padding-top: 12px;
  border-top: 1px dashed var(--border);
}
.sub-block-title {
  font-size: 12.5px;
  font-weight: 600;
  color: var(--text-muted);
  margin-bottom: 8px;
}
.recent-sheet-chips {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
}
.recent-sheet-chip {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  padding: 4px 10px;
  border-radius: var(--radius-sm);
  border: 1px solid var(--border);
  background: var(--bg-subtle);
  font-size: 12.5px;
  color: var(--text-primary);
}
.recent-sheet-chip:hover {
  text-decoration: none;
  border-color: var(--brand-border);
  background: var(--brand-soft);
}
.rs-proj {
  font-family: var(--font-mono);
  font-weight: 700;
  color: var(--brand);
}

.delivery-mini-list {
  list-style: none;
  margin: 0;
  padding: 0;
  display: grid;
}
.delivery-mini-list li {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 10px;
  padding: 9px 2px;
  border-top: 1px solid var(--border);
  font-size: 13.5px;
}
.delivery-mini-list li:first-child { border-top: none; }
.deliv-main {
  display: flex;
  align-items: center;
  gap: 8px;
  min-width: 0;
}
.deliv-proj {
  font-family: var(--font-mono);
  font-size: 12px;
  font-weight: 700;
  color: var(--text-secondary);
  background: var(--bg-subtle);
  padding: 1px 6px;
  border-radius: 4px;
  flex-shrink: 0;
}
.deliv-name {
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
  font-weight: 500;
}
.deliv-ver {
  font-size: 12px;
  color: var(--text-muted);
  flex-shrink: 0;
}
.deliv-time {
  font-size: 12px;
  color: var(--text-muted);
  font-variant-numeric: tabular-nums;
  flex-shrink: 0;
}

.future-domains-strip {
  display: flex;
  align-items: center;
  gap: 16px;
  padding: 14px 20px;
  border-radius: var(--radius-lg);
  border: 1px dashed var(--border-strong);
  background: rgba(255, 255, 255, 0.7);
}
.future-strip-label {
  font-size: 13px;
  font-weight: 600;
  color: var(--text-muted);
  white-space: nowrap;
}
.future-domain-pills {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: 12px;
  flex: 1;
}
.future-domain-card {
  padding: 10px 14px;
  border-radius: var(--radius-md);
  border: 1px solid var(--border);
  background: var(--bg-surface);
  display: grid;
  gap: 4px;
}
.fd-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  font-size: 13.5px;
  color: var(--text-secondary);
}
.future-domain-card span {
  font-size: 12px;
  color: var(--text-muted);
}
</style>
