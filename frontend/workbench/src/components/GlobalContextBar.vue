<script setup lang="ts">
import { computed, ref } from 'vue';
import { useRoute, useRouter } from 'vue-router';
import { NButton, NModal, NPopover, NSelect, NTag, type SelectGroupOption, type SelectOption } from 'naive-ui';
import { DOMAIN_DEFINITIONS, getDomainById, type DomainDefinition } from '../domains/domainRegistry';
import { useWorkbenchContextStore } from '../stores/workbenchContext';

const route = useRoute();
const router = useRouter();
const ctx = useWorkbenchContextStore();

const plannedModalDomain = ref<DomainDefinition | null>(null);
const domainPopoverOpen = ref(false);

const currentDomain = computed(() => getDomainById(ctx.activeDomain));

const groupedProjectOptions = computed<(SelectGroupOption | SelectOption)[]>(() => {
  const list = ctx.projects;
  if (!list.length) return [];
  const byCode = new Map(list.map(p => [p.project, p]));
  const toOpt = (code: string) => {
    const p = byCode.get(code);
    return {
      value: code,
      label: p ? (p.name ? `${p.project} · ${p.name}` : p.project) : code,
    };
  };

  const groups: SelectGroupOption[] = [];
  const pinned = ctx.pinnedProjects.filter(code => byCode.has(code));
  if (pinned.length) {
    groups.push({
      type: 'group',
      label: `置顶常用（${pinned.length}）`,
      key: 'pinned',
      children: pinned.map(toOpt),
    });
  }
  const recent = ctx.recentProjects
    .map(item => item.project)
    .filter(code => byCode.has(code) && !pinned.includes(code))
    .slice(0, 6);
  if (recent.length) {
    groups.push({
      type: 'group',
      label: `最近访问（${recent.length}）`,
      key: 'recent',
      children: recent.map(toOpt),
    });
  }
  const used = new Set([...pinned, ...recent]);
  const rest = list.filter(p => !used.has(p.project)).map(p => ({
    value: p.project,
    label: p.name ? `${p.project} · ${p.name}` : p.project,
  }));
  if (rest.length) {
    groups.push({
      type: 'group',
      label: `全部项目（${list.length}）`,
      key: 'all',
      children: rest,
    });
  }
  return groups;
});

function handleProjectSwitch(val: string | null) {
  const next = val || '';
  ctx.setActiveProject(next, Boolean(next));
  const seg = route.path.split('/')[1] || 'home';
  if (seg === 'projects') {
    void router.push(next ? '/projects/' + encodeURIComponent(next) : '/projects');
    return;
  }
  if (['import', 'new', 'specifications', 'trends', 'deliveries'].includes(seg)) {
    const q = { ...route.query } as Record<string, string | undefined>;
    if (next) q.project = next;
    else delete q.project;
    // Clear stale batch/sample/source when switching project in-place
    if (seg === 'import') {
      delete q.source;
      delete q.section;
      delete q.task;
    }
    delete q.batch;
    delete q.sample;
    ctx.clearSecondaryContext();
    void router.replace({ path: route.path, query: q });
  }
}

function selectDomain(domain: DomainDefinition) {
  domainPopoverOpen.value = false;
  if (domain.status === 'planned') {
    plannedModalDomain.value = domain;
    return;
  }
  ctx.setDomain(domain.id);
}

function clearSecondary() {
  ctx.clearSecondaryContext();
  const q = { ...route.query } as Record<string, string | undefined>;
  if (q.batch || q.sample) {
    delete q.batch;
    delete q.sample;
    void router.replace({ path: route.path, query: q });
  }
}
</script>

<template>
  <div class="global-context-bar" role="region" aria-label="全局上下文栏">
    <div class="ctx-left">
      <NPopover v-model:show="domainPopoverOpen" trigger="click" placement="bottom-start" :style="{ padding: '8px' }">
        <template #trigger>
          <button type="button" class="domain-pill" aria-label="切换业务域">
            <span class="domain-dot" aria-hidden="true"></span>
            <span class="domain-name">{{ currentDomain.label }}</span>
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
              <polyline points="6 9 12 15 18 9" />
            </svg>
          </button>
        </template>
        <div class="domain-menu">
          <div class="domain-menu-group">药品研发与放行（项目 / 批次维度）</div>
          <button
            v-for="item in DOMAIN_DEFINITIONS.filter(d => d.group === 'product_rd')"
            :key="item.id"
            type="button"
            class="domain-menu-item"
            :class="{ 'is-active': item.id === ctx.activeDomain, 'is-planned': item.status === 'planned' }"
            @click="selectDomain(item)"
          >
            <div class="domain-menu-row">
              <strong>{{ item.label }}</strong>
              <NTag v-if="item.status === 'active'" type="info" size="tiny">当前启用</NTag>
              <NTag v-else size="tiny">规划预留</NTag>
            </div>
            <small>{{ item.description }}</small>
          </button>

          <div class="domain-menu-group">公用设施与环境监测（厂房 / 回路 / 取样点维度）</div>
          <button
            v-for="item in DOMAIN_DEFINITIONS.filter(d => d.group === 'facility_monitoring')"
            :key="item.id"
            type="button"
            class="domain-menu-item"
            :class="{ 'is-planned': item.status === 'planned' }"
            @click="selectDomain(item)"
          >
            <div class="domain-menu-row">
              <strong>{{ item.label }}</strong>
              <NTag size="tiny">规划预留</NTag>
            </div>
            <small>{{ item.description }}</small>
          </button>
        </div>
      </NPopover>

      <span class="ctx-divider" aria-hidden="true"></span>

      <div class="ctx-project-picker">
        <span class="ctx-label">当前项目</span>
        <div class="ctx-select-wrap">
          <NSelect
            :value="ctx.activeProject || null"
            :options="groupedProjectOptions"
            :loading="ctx.projectsLoading"
            filterable
            clearable
            size="small"
            aria-label="全局切换项目"
            placeholder="快速搜索或切换项目（置顶 / 最近 / 全部）..."
            @update:value="handleProjectSwitch"
          />
        </div>
        <button
          v-if="ctx.activeProject"
          type="button"
          class="pin-btn"
          :class="{ 'is-pinned': ctx.isPinned(ctx.activeProject) }"
          :title="ctx.isPinned(ctx.activeProject) ? '取消置顶常用项目' : '置顶为常用项目（纳入首页个人待办）'"
          :aria-label="ctx.isPinned(ctx.activeProject) ? '取消置顶当前项目' : '置顶当前项目'"
          @click="ctx.togglePin(ctx.activeProject)"
        >
          <svg width="15" height="15" viewBox="0 0 24 24" :fill="ctx.isPinned(ctx.activeProject) ? 'currentColor' : 'none'" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
            <polygon points="12 2 15.09 8.26 22 9.27 17 14.14 18.18 21.02 12 17.77 5.82 21.02 7 14.14 2 9.27 8.91 8.26 12 2" />
          </svg>
        </button>
        <RouterLink
          v-if="ctx.activeProject"
          class="ctx-quick-link"
          :to="'/projects/' + encodeURIComponent(ctx.activeProject)"
        >
          项目台账
        </RouterLink>
      </div>
    </div>

    <div class="ctx-right">
      <div v-if="ctx.activeBatch || ctx.activeSheet" class="ctx-crumbs">
        <span v-if="ctx.activeBatch" class="ctx-crumb">批号：{{ ctx.activeBatch }}</span>
        <RouterLink v-if="ctx.activeSheet" class="ctx-crumb is-link" :to="'/worksheets/' + encodeURIComponent(ctx.activeSheet.id)">
          研究表：{{ ctx.activeSheet.name }}
        </RouterLink>
        <button type="button" class="ctx-clear-btn" title="清除当前批号/研究表上下文" @click="clearSecondary">×</button>
      </div>
      <RouterLink class="ctx-catalog-link" to="/projects">
        全部项目（{{ ctx.projects.length }}）
      </RouterLink>
    </div>

    <NModal :show="Boolean(plannedModalDomain)" preset="card" :title="(plannedModalDomain?.label || '') + ' · 架构预留说明'" style="max-width: 520px;" @update:show="val => { if (!val) plannedModalDomain = null; }">
      <template v-if="plannedModalDomain">
        <p style="margin: 0 0 10px; font-size: 14.5px; color: var(--text-primary);">{{ plannedModalDomain.description }}</p>
        <div class="planned-detail-box">
          <div><strong>归属维度：</strong>{{ plannedModalDomain.futureScopeHint }}</div>
          <div><strong>统一复用骨架：</strong>数据导入核对 → 新建研究/监测表 → 台账与趋势警戒线 → 受控导出与周期报告</div>
          <div><strong>当前状态：</strong>前端全局导航、上下文域抽象与入口槽位已就绪，待后端接入该监测数据模型后即可启用。</div>
        </div>
        <div style="margin-top: 16px; display: flex; justify-content: flex-end;">
          <NButton type="primary" size="small" @click="plannedModalDomain = null">返回稳定性研究</NButton>
        </div>
      </template>
    </NModal>
  </div>
</template>

<style scoped>
.global-context-bar {
  position: sticky;
  top: 0;
  z-index: 20;
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 16px;
  min-height: 54px;
  padding: 8px 36px;
  background: rgba(255, 255, 255, 0.96);
  backdrop-filter: blur(6px);
  border-bottom: 1px solid var(--border);
}
.ctx-left {
  display: flex;
  align-items: center;
  gap: 12px;
  min-width: 0;
  flex: 1;
}
.domain-pill {
  display: inline-flex;
  align-items: center;
  gap: 8px;
  height: 34px;
  padding: 0 12px;
  border-radius: var(--radius-sm);
  border: 1px solid var(--brand-border);
  background: var(--brand-soft);
  color: var(--brand);
  font: inherit;
  font-size: 13.5px;
  font-weight: 600;
  cursor: pointer;
  white-space: nowrap;
  transition: all 0.15s ease;
}
.domain-pill:hover {
  background: #e2eefb;
  border-color: var(--brand);
}
.domain-dot {
  width: 8px;
  height: 8px;
  border-radius: 999px;
  background: var(--brand);
}
.ctx-divider {
  width: 1px;
  height: 20px;
  background: var(--border);
  flex-shrink: 0;
}
.ctx-project-picker {
  display: flex;
  align-items: center;
  gap: 8px;
  min-width: 0;
}
.ctx-label {
  font-size: 13px;
  font-weight: 600;
  color: var(--text-secondary);
  white-space: nowrap;
}
.ctx-select-wrap {
  width: 300px;
}
.pin-btn {
  display: grid;
  place-items: center;
  width: 32px;
  height: 32px;
  border-radius: var(--radius-sm);
  border: 1px solid var(--border);
  background: var(--bg-surface);
  color: var(--text-muted);
  cursor: pointer;
  transition: all 0.15s ease;
}
.pin-btn:hover {
  border-color: var(--warning-border);
  color: #d97706;
  background: var(--warning-bg);
}
.pin-btn.is-pinned {
  border-color: var(--warning-border);
  color: #d97706;
  background: var(--warning-bg);
}
.ctx-quick-link {
  font-size: 13px;
  font-weight: 600;
  color: var(--brand);
  padding: 4px 10px;
  border-radius: var(--radius-sm);
  white-space: nowrap;
}
.ctx-quick-link:hover {
  background: var(--brand-soft);
  text-decoration: none;
}

.ctx-right {
  display: flex;
  align-items: center;
  gap: 12px;
  flex-shrink: 0;
}
.ctx-crumbs {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  padding: 3px 8px;
  background: var(--bg-subtle);
  border: 1px solid var(--border);
  border-radius: var(--radius-sm);
  max-width: 380px;
}
.ctx-crumb {
  font-size: 12.5px;
  color: var(--text-secondary);
  font-family: var(--font-mono);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
  max-width: 170px;
}
.ctx-crumb.is-link {
  color: var(--brand);
  font-family: var(--font-sans);
  font-weight: 500;
}
.ctx-clear-btn {
  border: none;
  background: transparent;
  color: var(--text-muted);
  cursor: pointer;
  font-size: 15px;
  line-height: 1;
  padding: 0 3px;
}
.ctx-clear-btn:hover { color: var(--text-primary); }

.ctx-catalog-link {
  font-size: 13px;
  font-weight: 500;
  color: var(--text-secondary);
  padding: 5px 10px;
  border-radius: var(--radius-sm);
  border: 1px solid var(--border);
  background: var(--bg-surface);
  white-space: nowrap;
}
.ctx-catalog-link:hover {
  color: var(--brand);
  border-color: var(--brand-border);
  background: var(--brand-soft);
  text-decoration: none;
}

.domain-menu {
  width: 360px;
  display: grid;
  gap: 6px;
}
.domain-menu-group {
  font-size: 11.5px;
  font-weight: 600;
  color: var(--text-muted);
  padding: 6px 8px 2px;
}
.domain-menu-item {
  display: grid;
  gap: 4px;
  text-align: left;
  padding: 10px 12px;
  border-radius: var(--radius-md);
  border: 1px solid transparent;
  background: transparent;
  cursor: pointer;
  font: inherit;
  transition: all 0.15s ease;
}
.domain-menu-item:hover {
  background: var(--bg-subtle);
  border-color: var(--border);
}
.domain-menu-item.is-active {
  background: var(--brand-soft);
  border-color: var(--brand-border);
}
.domain-menu-item.is-planned {
  opacity: 0.78;
}
.domain-menu-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  font-size: 14px;
  color: var(--text-primary);
}
.domain-menu-item small {
  font-size: 12.5px;
  color: var(--text-secondary);
  line-height: 1.45;
}
.planned-detail-box {
  display: grid;
  gap: 8px;
  padding: 12px 14px;
  border-radius: var(--radius-md);
  background: var(--bg-subtle);
  border: 1px solid var(--border);
  font-size: 13.5px;
  color: var(--text-secondary);
  line-height: 1.55;
}
</style>
