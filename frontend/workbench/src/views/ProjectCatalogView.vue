<script setup lang="ts">
import { computed, ref, watch } from 'vue';
import { useRouter } from 'vue-router';
import { NAlert, NButton, NInput, NTag } from 'naive-ui';
import { api, type Project } from '../api';
import { useWorkbenchContextStore } from '../stores/workbenchContext';

const props = defineProps<{ projects: Project[] }>();
const emit = defineEmits<{ created: [string] }>();
const router = useRouter();
const ctx = useWorkbenchContextStore();

const search = ref('');
const filterTab = ref<'all' | 'pinned' | 'recent'>('all');
const pageIndex = ref(1);
const pageSize = 30;
const creating = ref(false);
const createBusy = ref(false);
const createError = ref('');
const newCode = ref('');
const newName = ref('');

const recentOrder = computed(() => new Map(ctx.recentProjects.map((r, i) => [r.project, i])));

const filtered = computed(() => {
  const kw = search.value.trim().toLowerCase();
  let list = props.projects;
  if (filterTab.value === 'pinned') {
    list = list.filter(p => ctx.isPinned(p.project));
  } else if (filterTab.value === 'recent') {
    list = list
      .filter(p => recentOrder.value.has(p.project))
      .sort((a, b) => (recentOrder.value.get(a.project) ?? 999) - (recentOrder.value.get(b.project) ?? 999));
  }
  if (kw) {
    list = list.filter(p => (p.project + ' ' + p.name).toLowerCase().includes(kw));
  }
  return list;
});

const totalPages = computed(() => Math.max(1, Math.ceil(filtered.value.length / pageSize)));
const pagedProjects = computed(() => {
  const start = (pageIndex.value - 1) * pageSize;
  return filtered.value.slice(start, start + pageSize);
});

watch([search, filterTab], () => {
  pageIndex.value = 1;
});

async function handleCreate() {
  if (!newCode.value.trim() || !newName.value.trim() || createBusy.value) return;
  createBusy.value = true;
  createError.value = '';
  try {
    const code = newCode.value.trim();
    await api('/projects', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ project: code, name: newName.value.trim() }),
    });
    await ctx.loadProjects(true);
    ctx.setActiveProject(code, true);
    newCode.value = '';
    newName.value = '';
    creating.value = false;
    emit('created', code);
    await router.push('/projects/' + encodeURIComponent(code));
  } catch (e) {
    createError.value = (e as Error).message;
  } finally {
    createBusy.value = false;
  }
}
</script>

<template>
  <section class="panel catalog-panel">
    <div class="catalog-head">
      <div>
        <h2>项目目录</h2>
        <p class="catalog-sub">共 {{ projects.length }} 个项目 · 星标置顶的常用项目将自动纳入首页个人待办监控</p>
      </div>
      <div class="catalog-head-actions">
        <NButton type="primary" size="small" @click="creating = !creating">
          {{ creating ? '收起新建项目' : '+ 新建项目' }}
        </NButton>
      </div>
    </div>

    <form v-if="creating" class="create-box" @submit.prevent="handleCreate">
      <NAlert v-if="createError" type="error" style="grid-column: 1 / -1;">{{ createError }}</NAlert>
      <label>项目编号 <input v-model.trim="newCode" required placeholder="例如：IP501" :disabled="createBusy"></label>
      <label>项目名称 <input v-model.trim="newName" required placeholder="例如：某单抗稳定性研究" :disabled="createBusy"></label>
      <NButton attr-type="submit" type="primary" :loading="createBusy">创建并进入台账</NButton>
    </form>

    <div class="catalog-toolbar">
      <NInput
        v-model:value="search"
        placeholder="搜索项目编号或名称（支持 300+ 项目快速过滤）"
        aria-label="搜索项目"
        clearable
        style="max-width: 420px;"
      />
      <div class="catalog-tabs" role="tablist" aria-label="项目筛选">
        <button type="button" :class="{ active: filterTab === 'all' }" @click="filterTab = 'all'">
          全部（{{ projects.length }}）
        </button>
        <button type="button" :class="{ active: filterTab === 'pinned' }" @click="filterTab = 'pinned'">
          置顶收藏（{{ ctx.pinnedProjects.length }}）
        </button>
        <button type="button" :class="{ active: filterTab === 'recent' }" @click="filterTab = 'recent'">
          最近访问（{{ ctx.recentProjects.length }}）
        </button>
      </div>
      <span class="section-count">当前匹配 {{ filtered.length }} 个项目</span>
    </div>

    <div v-if="pagedProjects.length" class="catalog-table-wrap">
      <table class="catalog-table">
        <thead>
          <tr>
            <th style="width: 56px;">置顶</th>
            <th style="width: 180px;">项目编号</th>
            <th>项目名称</th>
            <th style="width: 120px;">状态标记</th>
            <th style="width: 340px;">快捷作业</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="p in pagedProjects" :key="p.project">
            <td>
              <button
                type="button"
                class="star-btn"
                :class="{ pinned: ctx.isPinned(p.project) }"
                :title="ctx.isPinned(p.project) ? '取消置顶' : '置顶常用'"
                @click="ctx.togglePin(p.project)"
              >
                ★
              </button>
            </td>
            <td>
              <RouterLink class="project-code-link" :to="'/projects/' + encodeURIComponent(p.project)">
                {{ p.project }}
              </RouterLink>
            </td>
            <td>{{ p.name || '—' }}</td>
            <td>
              <NTag v-if="ctx.activeProject === p.project" size="small" type="info">当前聚焦</NTag>
              <NTag v-else-if="ctx.isPinned(p.project)" size="small" type="warning">已置顶</NTag>
              <span v-else class="muted">—</span>
            </td>
            <td>
              <div class="row-links">
                <RouterLink class="row-pill primary" :to="'/projects/' + encodeURIComponent(p.project)">进入台账</RouterLink>
                <RouterLink class="row-pill" :to="{ path: '/import', query: { project: p.project } }">导入</RouterLink>
                <RouterLink class="row-pill" :to="{ path: '/new', query: { project: p.project } }">建研究表</RouterLink>
                <RouterLink class="row-pill" :to="{ path: '/trends', query: { project: p.project } }">趋势</RouterLink>
                <RouterLink class="row-pill" :to="{ path: '/deliveries', query: { project: p.project } }">交付</RouterLink>
              </div>
            </td>
          </tr>
        </tbody>
      </table>
    </div>
    <p v-else class="muted" style="margin-top: 16px;">
      {{ projects.length ? '没有匹配项目。' : '当前数据目录还没有项目。' }}
    </p>

    <div v-if="totalPages > 1" class="pagination-bar">
      <NButton size="small" :disabled="pageIndex <= 1" @click="pageIndex--">上一页</NButton>
      <span>第 {{ pageIndex }} / {{ totalPages }} 页（共 {{ filtered.length }} 个）</span>
      <NButton size="small" :disabled="pageIndex >= totalPages" @click="pageIndex++">下一页</NButton>
    </div>
  </section>
</template>

<style scoped>
.catalog-panel { margin-top: 0; }
.catalog-head {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 16px;
  margin-bottom: 14px;
}
.catalog-head h2 { margin: 0 0 4px; }
.catalog-sub { margin: 0; font-size: 13.5px; color: var(--text-secondary); }
.create-box {
  display: grid;
  grid-template-columns: 220px minmax(240px, 1fr) auto;
  gap: 12px;
  align-items: end;
  padding: 14px 16px;
  margin-bottom: 16px;
  border-radius: var(--radius-md);
  background: var(--bg-subtle);
  border: 1px solid var(--border);
}
.create-box label { display: grid; gap: 5px; font-size: 13px; font-weight: 600; color: var(--text-secondary); }

.catalog-toolbar {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 12px;
  margin-bottom: 14px;
}
.catalog-tabs {
  display: inline-flex;
  padding: 2px;
  background: var(--bg-subtle);
  border: 1px solid var(--border);
  border-radius: var(--radius-sm);
}
.catalog-tabs button {
  border: none;
  background: transparent;
  padding: 5px 12px;
  font: inherit;
  font-size: 13px;
  font-weight: 500;
  color: var(--text-secondary);
  border-radius: 4px;
  cursor: pointer;
}
.catalog-tabs button.active {
  background: var(--bg-surface);
  color: var(--brand);
  font-weight: 600;
  box-shadow: 0 1px 2px rgba(15, 23, 42, 0.08);
}
.section-count { margin-left: auto; font-size: 13px; color: var(--text-muted); font-weight: 500; }

.catalog-table-wrap {
  border: 1px solid var(--border);
  border-radius: var(--radius-md);
  overflow: hidden;
}
.catalog-table th, .catalog-table td { min-width: 0; vertical-align: middle; }
.star-btn {
  border: none;
  background: transparent;
  color: #cbd5e1;
  font-size: 18px;
  cursor: pointer;
  line-height: 1;
}
.star-btn:hover, .star-btn.pinned { color: #d97706; }
.project-code-link { font-family: var(--font-mono); font-size: 14.5px; font-weight: 700; }
.row-links { display: flex; flex-wrap: wrap; gap: 6px; }
.row-pill {
  font-size: 12.5px;
  font-weight: 500;
  padding: 3px 9px;
  border-radius: var(--radius-sm);
  border: 1px solid var(--border);
  background: var(--bg-subtle);
  color: var(--text-secondary);
}
.row-pill:hover { text-decoration: none; border-color: var(--brand-border); background: var(--brand-soft); color: var(--brand); }
.row-pill.primary { background: var(--brand-soft); border-color: var(--brand-border); color: var(--brand); font-weight: 600; }
.row-pill.primary:hover { background: var(--brand); color: #ffffff; }

.pagination-bar {
  display: flex;
  align-items: center;
  justify-content: flex-end;
  gap: 12px;
  margin-top: 14px;
  font-size: 13.5px;
  color: var(--text-secondary);
}
</style>
