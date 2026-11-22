import { computed, ref } from 'vue';
import { defineStore } from 'pinia';
import { api, type Project } from '../api';
import type { DomainId } from '../domains/domainRegistry';

const STORAGE_KEY = 'stability-workbench:context:v1';

export interface RecentProjectEntry {
  project: string;
  name: string;
  visitedAt: number;
}

export interface RecentSheetEntry {
  id: string;
  name: string;
  project: string;
  batch?: string;
  condition?: string;
  visitedAt: number;
}

interface PersistedState {
  activeDomain?: DomainId;
  activeProject?: string;
  pinnedProjects?: string[];
  recentProjects?: RecentProjectEntry[];
  recentSheets?: RecentSheetEntry[];
  todoScopeMode?: 'followed' | 'active_only';
}

function readPersisted(): PersistedState {
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    if (!raw) return {};
    return JSON.parse(raw) as PersistedState;
  } catch {
    return {};
  }
}

export const useWorkbenchContextStore = defineStore('workbenchContext', () => {
  const initial = readPersisted();

  const projects = ref<Project[]>([]);
  const projectsLoaded = ref(false);
  const projectsLoading = ref(false);

  const activeDomain = ref<DomainId>(initial.activeDomain || 'stability');
  const activeProject = ref<string>(initial.activeProject || '');
  const activeBatch = ref<string>('');
  const activeSampleId = ref<string>('');
  const activeSheet = ref<{ id: string; name: string; project: string; batch?: string } | null>(null);

  const pinnedProjects = ref<string[]>(Array.isArray(initial.pinnedProjects) ? initial.pinnedProjects : []);
  const recentProjects = ref<RecentProjectEntry[]>(Array.isArray(initial.recentProjects) ? initial.recentProjects : []);
  const recentSheets = ref<RecentSheetEntry[]>(Array.isArray(initial.recentSheets) ? initial.recentSheets : []);
  const todoScopeMode = ref<'followed' | 'active_only'>(initial.todoScopeMode || 'followed');
  const justCreatedSheetId = ref<string>('');
  const pendingWorksheetAction = ref<{ sheetId: string; action: 'coa0m' } | null>(null);

  function persist() {
    try {
      const payload: PersistedState = {
        activeDomain: activeDomain.value,
        activeProject: activeProject.value,
        pinnedProjects: pinnedProjects.value.slice(0, 20),
        recentProjects: recentProjects.value.slice(0, 12),
        recentSheets: recentSheets.value.slice(0, 8),
        todoScopeMode: todoScopeMode.value,
      };
      localStorage.setItem(STORAGE_KEY, JSON.stringify(payload));
    } catch {
      // Ignore storage quota / privacy mode errors.
    }
  }

  async function loadProjects(force = false) {
    if (projectsLoading.value) return projects.value;
    if (projectsLoaded.value && !force) return projects.value;
    projectsLoading.value = true;
    try {
      const data = await api<Project[]>('/projects');
      projects.value = data;
      projectsLoaded.value = true;
      // Keep activeProject valid if database switched/reset
      if (activeProject.value && !data.some(p => p.project === activeProject.value)) {
        activeProject.value = '';
      }
      // Re-hydrate names in recentProjects
      const nameMap = new Map(data.map(p => [p.project, p.name]));
      recentProjects.value = recentProjects.value
        .filter(item => nameMap.has(item.project))
        .map(item => ({ ...item, name: nameMap.get(item.project) || item.name }));
      pinnedProjects.value = pinnedProjects.value.filter(code => nameMap.has(code));
      persist();
      return data;
    } finally {
      projectsLoading.value = false;
    }
  }

  function setProjectsSnapshot(list: Project[]) {
    projects.value = list;
    projectsLoaded.value = true;
    const nameMap = new Map(list.map(p => [p.project, p.name]));
    if (activeProject.value && !nameMap.has(activeProject.value)) {
      activeProject.value = '';
    }
    recentProjects.value = recentProjects.value
      .filter(item => nameMap.has(item.project))
      .map(item => ({ ...item, name: nameMap.get(item.project) || item.name }));
    pinnedProjects.value = pinnedProjects.value.filter(code => nameMap.has(code));
    persist();
  }

  function setActiveProject(code: string, recordVisit = true) {
    const clean = (code || '').trim();
    if (activeProject.value !== clean) {
      activeProject.value = clean;
      if (!clean) {
        activeBatch.value = '';
        activeSampleId.value = '';
        activeSheet.value = null;
      }
    }
    if (clean && recordVisit) {
      recordProjectVisit(clean);
    } else {
      persist();
    }
  }

  function recordProjectVisit(code: string, explicitName?: string) {
    const clean = (code || '').trim();
    if (!clean) return;
    const matched = projects.value.find(p => p.project === clean);
    const name = explicitName ?? matched?.name ?? '';
    recentProjects.value = [
      { project: clean, name, visitedAt: Date.now() },
      ...recentProjects.value.filter(item => item.project !== clean),
    ].slice(0, 12);
    persist();
  }

  function recordSheetVisit(sheet: { id: string; name: string; project: string; batch?: string; condition?: string }) {
    activeSheet.value = { id: sheet.id, name: sheet.name, project: sheet.project, batch: sheet.batch };
    if (sheet.project) setActiveProject(sheet.project, true);
    if (sheet.batch) activeBatch.value = sheet.batch;
    recentSheets.value = [
      { ...sheet, visitedAt: Date.now() },
      ...recentSheets.value.filter(item => item.id !== sheet.id),
    ].slice(0, 8);
    persist();
  }

  function setSecondaryContext(opts: { batch?: string; sampleId?: string; sheet?: { id: string; name: string; project: string; batch?: string } | null }) {
    if (opts.batch !== undefined) activeBatch.value = opts.batch;
    if (opts.sampleId !== undefined) activeSampleId.value = opts.sampleId;
    if (opts.sheet !== undefined) activeSheet.value = opts.sheet;
  }

  function clearSecondaryContext() {
    activeBatch.value = '';
    activeSampleId.value = '';
    activeSheet.value = null;
  }

  function togglePin(code: string) {
    const clean = code.trim();
    if (!clean) return;
    if (pinnedProjects.value.includes(clean)) {
      pinnedProjects.value = pinnedProjects.value.filter(item => item !== clean);
    } else {
      pinnedProjects.value = [clean, ...pinnedProjects.value].slice(0, 20);
    }
    persist();
  }

  function isPinned(code: string) {
    return pinnedProjects.value.includes(code);
  }

  function setTodoScopeMode(mode: 'followed' | 'active_only') {
    todoScopeMode.value = mode;
    persist();
  }

  function setDomain(domain: DomainId) {
    activeDomain.value = domain;
    persist();
  }

  function markJustCreatedSheet(sheetId: string) {
    justCreatedSheetId.value = sheetId;
  }

  function consumeJustCreatedSheet(sheetId: string) {
    if (justCreatedSheetId.value === sheetId) {
      justCreatedSheetId.value = '';
      return true;
    }
    return false;
  }

  function setPendingWorksheetAction(sheetId: string, action: 'coa0m') {
    pendingWorksheetAction.value = { sheetId, action };
  }

  function consumePendingWorksheetAction(sheetId: string) {
    if (pendingWorksheetAction.value?.sheetId === sheetId) {
      const act = pendingWorksheetAction.value.action;
      pendingWorksheetAction.value = null;
      return act;
    }
    return null;
  }

  const activeProjectMeta = computed(() =>
    projects.value.find(p => p.project === activeProject.value) ?? null,
  );

  const monitoredProjects = computed(() => {
    const valid = new Set(projects.value.map(p => p.project));
    if (todoScopeMode.value === 'active_only') {
      return activeProject.value && (!valid.size || valid.has(activeProject.value)) ? [activeProject.value] : [];
    }
    const ordered: string[] = [];
    const push = (code: string) => {
      if (!code || ordered.includes(code)) return;
      if (valid.size && !valid.has(code)) return;
      ordered.push(code);
    };
    if (activeProject.value) push(activeProject.value);
    for (const code of pinnedProjects.value) push(code);
    for (const entry of recentProjects.value) push(entry.project);
    // If brand new user has zero pinned/recent, seed with up to 3 projects so home todo isn't blind
    if (!ordered.length) {
      for (const p of projects.value.slice(0, 3)) push(p.project);
    }
    return ordered.slice(0, 5);
  });

  return {
    projects,
    projectsLoaded,
    projectsLoading,
    activeDomain,
    activeProject,
    activeProjectMeta,
    activeBatch,
    activeSampleId,
    activeSheet,
    pinnedProjects,
    recentProjects,
    recentSheets,
    todoScopeMode,
    justCreatedSheetId,
    pendingWorksheetAction,
    monitoredProjects,
    loadProjects,
    setProjectsSnapshot,
    setActiveProject,
    recordProjectVisit,
    recordSheetVisit,
    setSecondaryContext,
    clearSecondaryContext,
    togglePin,
    isPinned,
    setTodoScopeMode,
    setDomain,
    markJustCreatedSheet,
    consumeJustCreatedSheet,
    setPendingWorksheetAction,
    consumePendingWorksheetAction,
  };
});
