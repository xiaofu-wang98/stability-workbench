<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue';
import { onBeforeRouteLeave, onBeforeRouteUpdate, useRoute, useRouter } from 'vue-router';
import { NAlert, NButton, NSelect } from 'naive-ui';
import { api, type Project, type Worksheet } from './api';
import SpecificationPicker from './SpecificationPicker.vue';

const props = defineProps<{ projects: Project[] }>();
const route = useRoute();
const router = useRouter();
type Item = { id: string; label: string; method: string; unit: string; reporting_basis?: string };
type CustomItem = Item & { result_type: 'numeric' | 'qualitative'; criteria_text: string; specification_links?: { specification_id: string; table: number; row: number }[] };
type RowCriteria = { criteria_text: string; specification_links: NonNullable<CustomItem['specification_links']> };
const customItems = ref<CustomItem[]>([]);
const rowCriteria = ref<Record<string, RowCriteria>>({});
const editingCriteria = ref<{ id: string; value: RowCriteria }>();
const showSpecifications = ref(Boolean(route.query.specification));
const standardTarget = ref<string>();
const standardTexts = ref<Record<string, string>>({});
const standardKey = (link: { specification_id: string; table: number; row: number }) => link.specification_id + ':' + link.table + ':' + link.row;
function criteriaFor(id: string): RowCriteria {
  if (rowCriteria.value[id]) return rowCriteria.value[id];
  const custom = customItems.value.find(item => item.id === id);
  const links = [...(seed.value?.item_standards[id] ?? []), ...(custom?.specification_links ?? [])];
  for (const standard of protocolStandards.value?.found ?? []) {
    if (!chosenProtocolStandards.value.includes(standard.id)) continue;
    for (const link of standard.links) {
      if (link.item_id === id) links.push({ specification_id: standard.id, table: link.table, row: link.row });
    }
  }
  return { criteria_text: custom?.criteria_text ?? '', specification_links: [...new Map(links.map(link => [standardKey(link), link])).values()] };
}
function editCriteria(id: string) {
  const current = criteriaFor(id);
  editingCriteria.value = { id, value: { ...current, specification_links: [...current.specification_links] } };
}
function applyCriteria() {
  const edit = editingCriteria.value;
  if (!edit) return;
  rowCriteria.value[edit.id] = { ...edit.value, criteria_text: edit.value.criteria_text.trim() };
  editingCriteria.value = undefined;
}
async function refreshStandardTexts() {
  const seedIds = Object.values(seed.value?.item_standards ?? {}).flatMap(links => links.map(l => l.specification_id));
  const customIds = customItems.value.flatMap(item => item.specification_links ?? []).map(link => link.specification_id);
  const editedIds = Object.values(rowCriteria.value).flatMap(value => value.specification_links.map(link => link.specification_id));
  const ids = [...new Set([...seedIds, ...customIds, ...editedIds])].filter(Boolean);
  for (const id of ids) {
    const source = await api<{ rows: { table: number; row: number; criterion: string }[] }>('/specifications/' + id);
    for (const row of source.rows) standardTexts.value[id + ':' + row.table + ':' + row.row] = row.criterion;
  }
}
async function addStandardItems(items: Omit<CustomItem, 'id'>[]) {
  if (standardTarget.value) {
    if (!selected.value.includes(standardTarget.value)) { error.value = '目标检项已移除，请重新选择。'; return; }
    const criteria = criteriaFor(standardTarget.value);
    const links = [...criteria.specification_links, ...items.flatMap(item => item.specification_links ?? [])];
    rowCriteria.value[standardTarget.value] = { ...criteria, specification_links: [...new Map(links.map(link => [standardKey(link), link])).values()] };
    standardTarget.value = undefined;
    showSpecifications.value = false;
    try { await refreshStandardTexts(); } catch (e) { error.value = (e as Error).message; }
    return;
  }
  for (const item of items) {
    const link = item.specification_links?.[0];
    if (link) {
      const registered = Object.entries(seed.value?.item_standards ?? {}).filter(([, links]) =>
        links.some(value => standardKey(value) === standardKey(link)));
      if (registered.length) {
        for (const [id] of registered) {
          if (!selected.value.includes(id)) selected.value.push(id);
        }
        continue;
      }
    }
    const existing = link && customItems.value.find(candidate => candidate.specification_links?.some(value => standardKey(value) === standardKey(link)));
    if (existing) { if (!selected.value.includes(existing.id)) selected.value.push(existing.id); continue; }
    const added = { ...item, id: 'local_' + crypto.randomUUID() };
    customItems.value.push(added); selected.value.push(added.id);
  }
  showSpecifications.value = false;
  try { await refreshStandardTexts(); } catch (e) { error.value = (e as Error).message; }
}
const editingItem = ref<CustomItem>();
const editingIndex = ref<number>();
const replacingId = ref<string>();
const removedItem = ref<{ id: string; index: number }>();
type Configuration = { base_revision: number; config: { items: Item[] } };
const project = ref(String(route.query.project || ''));
const configuration = ref<Configuration>();
const selected = ref<string[]>([]);
const error = ref('');
const busy = ref(false);
const reading = ref(false);
const draftForm = ref<HTMLFormElement>();
const resumed = String(route.query.resume || '');
const draftLink = ref('');
const setupReady = ref(false);
let savedPlan = '';
type Draft = { custom_items?: CustomItem[]; row_criteria?: Record<string, RowCriteria>; fields: Record<string, string>; selected: string[]; row_items: Record<number, string | string[]>;
  required: Record<number, string[]>; protocol_draft: string; protocol_table: string; copy_source: string };
const restored = ref<Draft>();
type Matrix = { table: number; caption: string; study: string; warnings: string[];
  conditions: { text: string; location: string; tracked_changes?: boolean }[];
  points: { column: number; amount: number; unit: string }[];
  items: { row: number; label: string; suggested_component_ids?: string[];
    method_evidence?: { table: number; row: number; column: number; raw: string }[];
    cells: { column: number; state: string; raw: string }[] }[] };
const draftId = String(route.query.draft || '');
const protocolLoaded = ref(false);
type ProtocolStandards = { missing: string[];
  references?: { number: string; state: 'missing' | 'ambiguous' | 'unregistered' | 'scope_review' | 'available'; registered_links: number }[];
  found: { number: string; id: string; filename: string; page?: number | null;
  links: { table: number; row: number; item_id: string }[] }[] };
const chosenProtocolStandards = ref<string[]>([]);
const referenceMessages = {
  missing: '未找到原件，可暂缺保存。',
  ambiguous: '找到多个可能版本，请核对后选择适用版本。',
  unregistered: '已找到原件，请在质量标准页登记本项目的适用条款。',
  scope_review: '已登记条款，材料或用途待核对。',
  available: '已登记条款，可按检项和方法核对关联。',
};
const copyId = String(route.query.copy || '');
const seedSlot = String(route.query.slot || '');
const seed = ref<{ identity: Partial<Worksheet['identity']> & { sample_id?: string | null }; items: Item[];
  points: { label: string; items: string[] }[]; matrix?: Matrix; protocol_standards?: ProtocolStandards;
  custom_items?: CustomItem[]; protocol_row_items?: Record<number, string[]>;
  standard_candidate_count?: number;
  item_standards: Record<string, { specification_id: string; table: number; row: number }[]>;
  equipment: string; location: string; start_date?: string; source_timepoints?: string[];
  copy?: { id: string; name: string; plan_version: number; has_standards: boolean } }>();
const copied = computed(() => seed.value?.copy);
const protocolStandards = computed(() => seed.value?.protocol_standards);
const matrix = ref<Matrix>();
const suggestedBatch = ref(String(route.query.batch || ''));
const sampleId = String(route.query.sample || '');
const linkedSample = ref<{ id: string; batch: string; sample_type: string; material_stage: string; presentation: string }>();
const rowItems = ref<Record<number, string[]>>({});
const mappedItems = computed(() => [...new Set(Object.values(rowItems.value).flat().filter(id => id !== ':exclude'))]);
const itemOptions = computed(() => {
  const items = new Map((configuration.value?.config.items ?? []).map(item => [item.id, item]));
  for (const item of seed.value?.items ?? []) items.set(item.id, item);
  for (const item of customItems.value) items.set(item.id, item);
  return Array.from(items.values(), i => ({ value: i.id, label: [i.label, i.method, i.reporting_basis].filter(Boolean).join(' · ') }));
});
const pointText = ref('');
const material = ref('');
const formValues = ref<Record<string, string>>({ presentation: '', start_date: '', equipment: '', study_id: '', study: '', orientation: '', purpose: 'unknown', reporting_basis: '', name: '', basis: '' });
const detailsOpen = ref(false);
const conditionValue = ref('');
const locationValue = ref('');
const temperatureOptions = ['-80℃±10℃', '-40℃±10℃', '-40℃±5℃', '5℃±3℃', '25℃±2℃', '40℃±2℃'];
const conditionOptions = ['60%RH±5%RH', '75%RH±5%RH', '避光'];
const locationOptions = ['相城工厂', '常熟工厂', '智享生物技术（苏州）有限公司'];
const conditionParts = computed(() => conditionValue.value.split(/\s*·\s*/).filter(Boolean));
function chooseTemperature(value: string) {
  conditionValue.value = [value, ...conditionParts.value.filter(part => !/^-?\d+(?:\.\d+)?\s*(?:℃|°C)/i.test(part))].join(' · ');
}
function toggleCondition(value: string) {
  const parts = conditionParts.value;
  conditionValue.value = (parts.includes(value) ? parts.filter(part => part !== value)
    : [...parts.filter(part => !value.includes('RH') || !part.includes('RH')), value]).join(' · ');
}

const dismissedCandidates = ref<string[]>([]);
function dismissCandidate(name: string) {
  if (!dismissedCandidates.value.includes(name)) {
    dismissedCandidates.value.push(name);
  }
}
const templatePoints = computed(() => pointText.value.split(/[\s,，、]+/).filter(Boolean));
const excluded = ref<Record<string, string[]>>({});
const commonItems = ['肽图', 'SEC', 'CE-SDS', '电荷变异体', '效价', '生物学活性', '蛋白浓度', '外观', '颜色', '澄清度', 'pH', '内毒素'];
const materialItems: Record<string, string[]> = {
  DS: [...commonItems, '微生物限度'],
  DP: [...commonItems, '渗透压', '可见异物', '不溶性微粒', '无菌'],
};
const projectSpecific = ['DAR', '游离小分子', '未偶联蛋白', '糖谱'];
const freezeDriedItems = ['复溶时间', '水分', '剂量均匀度', '海藻糖含量', '甲硫氨酸含量', '聚山梨酯80含量'];
const injectionDeviceItems = ['给药剂量', '注射时间', '灌装量', '装置外观', '针头', '容器密封完整性'];
const formulationSpecific = [...freezeDriedItems, ...injectionDeviceItems];
const candidateNames = computed(() => materialItems[material.value] ? [
  ...materialItems[material.value], ...projectSpecific, ...(material.value === 'DP' ? formulationSpecific : []),
] : commonItems);
const candidates = computed(() => (configuration.value?.config.items ?? []).filter(item =>
  candidateNames.value.some(name =>
    (item.label + ' ' + item.method).toLowerCase().includes(name.toLowerCase()))));
function chooseMaterial(event: Event) {
  if (matrix.value || copied.value || seedSlot) return;
  material.value = (event.target as HTMLSelectElement).value;
  const names = materialItems[material.value];
  if (!names) return;
  const matches = (item: Item, terms: string[]) => terms.some(name =>
    (item.label + ' ' + item.method).toLowerCase().includes(name.toLowerCase()));
  const otherMaterial = material.value === 'DS' ? [...materialItems.DP, ...formulationSpecific] : materialItems.DS;
  selected.value = [...customItems.value.filter(item => selected.value.includes(item.id)).map(item => item.id), ...(configuration.value?.config.items ?? [])
    .filter(item => matches(item, names) || (selected.value.includes(item.id) && !matches(item, otherMaterial)))
    .map(item => item.id)];
}
function insertItem(label = '', method = '', unit = '', index?: number) {
  editingIndex.value = index;
  replacingId.value = undefined;
  editingItem.value = { id: 'local_' + crypto.randomUUID(), label, method, unit, result_type: 'numeric', criteria_text: '' };
}
function applyItem() {
  const item = editingItem.value;
  if (!item || !item.label.trim() || !item.method.trim()) return;
  const next = { ...item, label: item.label.trim(), method: item.method.trim(), unit: item.unit.trim(),
    criteria_text: item.criteria_text.trim() };
  const index = editingIndex.value;
  customItems.value = [...customItems.value.filter(value => value.id !== next.id), next];
  if (index !== undefined && replacingId.value && replacingId.value !== next.id) {
    for (const ids of Object.values(excluded.value)) {
      const at = ids.indexOf(replacingId.value);
      if (at >= 0) ids[at] = next.id;
    }
    for (const ids of Object.values(rowItems.value)) {
      const at = ids.indexOf(replacingId.value);
      if (at >= 0) ids[at] = next.id;
    }
    selected.value.splice(index, 1, next.id);
  } else if (!selected.value.includes(next.id)) {
    if (index === undefined) selected.value.push(next.id);
    else selected.value.splice(index, 0, next.id);
  }
  editingItem.value = undefined;
}
function editItem(id: string, index: number) {
  const local = customItems.value.find(item => item.id === id);
  const item = local ?? configuration.value?.config.items.find(item => item.id === id);
  if (!item) return;
  editingIndex.value = index;
  replacingId.value = id;
  editingItem.value = { ...item, specification_links: local?.specification_links?.map(link => ({ ...link })), id: local ? id : 'local_' + crypto.randomUUID(),
    result_type: ('result_type' in item && item.result_type === 'qualitative') ? 'qualitative' : 'numeric',
    criteria_text: local?.criteria_text ?? '' };
}
function removeItem(id: string, index: number) {
  removedItem.value = { id, index };
  selected.value.splice(index, 1);
}
function undoRemoveItem() {
  const item = removedItem.value;
  if (item && !selected.value.includes(item.id)) selected.value.splice(item.index, 0, item.id);
  removedItem.value = undefined;
}
const purityGroups = {
  nr: { method: '非还原型 CE-SDS 法 / NR CE-SDS Method', labels: ['主峰纯度 / Purity', '总体小峰 / Total Minor Species'] },
  sec: { method: 'SEC-HPLC 法 / SEC-HPLC Method', labels: ['单体 / Monomer', '高分子量组分 / HMW', '低分子量组分 / LMW'] },
};
function insertPurityGroup(group: keyof typeof purityGroups) {
  const { method, labels } = purityGroups[group];
  for (const label of labels) {
    const existing = [...customItems.value, ...(configuration.value?.config.items ?? [])].find(
      item => item.label === label && item.method.toLowerCase().replace(/[\s‑–-]/g, '') === method.toLowerCase().replace(/[\s‑–-]/g, ''));
    if (existing) {
      if (!selected.value.includes(existing.id)) selected.value.push(existing.id);
      continue;
    }
    const item: CustomItem = { id: 'local_' + crypto.randomUUID(), label, method, unit: '%',
      result_type: 'numeric', criteria_text: '' };
    customItems.value.push(item);
    selected.value.push(item.id);
  }
}
function editPoint(index: number, label: string) {
  const points = [...templatePoints.value];
  const old = points[index];
  if (old === label) return;
  if (label) points[index] = label;
  else points.splice(index, 1);
  if (label && excluded.value[old] && !excluded.value[label]) excluded.value[label] = excluded.value[old];
  delete excluded.value[old];
  pointText.value = points.join(', ');
}
function toggleRequired(label: string, item: string, enabled: boolean) {
  const ids = new Set(excluded.value[label] ?? []);
  if (enabled) ids.delete(item);
  else ids.add(item);
  excluded.value[label] = [...ids];
}
function prefillPoints(points: { label: string; items: string[] }[]) {
  pointText.value = points.map(point => point.label).join(', ');
  excluded.value = Object.fromEntries(points.map(point => [point.label,
    selected.value.filter(item => !point.items.includes(item))]));
}

async function loadSource() {
  if (sampleId) {
    try {
      const samples = await api<NonNullable<typeof linkedSample.value>[]>('/projects/' + encodeURIComponent(project.value)
        + '/samples?batch=' + encodeURIComponent(suggestedBatch.value));
      linkedSample.value = samples.find(sample => sample.id === sampleId);
      if (!linkedSample.value) throw new Error('所选样品不存在，请从批号页面重新打开。');
      material.value = linkedSample.value.sample_type;
    } catch (e) { error.value = (e as Error).message; }
  }
  if (!project.value) return;
  try {
    const standard = String(route.query.specification || '');
    const source = seedSlot ? 'results' : copyId ? 'copy' : draftId ? 'protocol' : standard ? 'standard' : 'blank';
    const query = new URLSearchParams({ source, batch: suggestedBatch.value, slot_id: seedSlot, source_id: copyId || draftId || standard });
    if (route.query.table) query.set('table', String(route.query.table));
    const data = await api<NonNullable<typeof seed.value>>('/projects/' + encodeURIComponent(project.value) + '/worksheet-seed?' + query);
    seed.value = data;
    Object.assign(formValues.value, { presentation: linkedSample.value?.presentation ?? data.identity.presentation ?? '', start_date: data.start_date ?? '', equipment: data.equipment ?? '', study_id: data.identity.study_id ?? '', study: data.identity.study ?? '', orientation: data.identity.orientation ?? '', purpose: data.identity.purpose || (linkedSample.value?.material_stage === '参考品' ? 'reference' : 'unknown'), reporting_basis: data.identity.reporting_basis ?? '' });
    material.value = linkedSample.value?.sample_type || data.identity.sample_type || material.value;
    matrix.value = data.matrix;
    customItems.value = data.custom_items ?? [];
    rowItems.value = data.protocol_row_items ?? {};
    await refreshStandardTexts();
    protocolLoaded.value = source === 'protocol';
    suggestedBatch.value = data.identity.batch || '';
    selected.value = data.items.map(item => item.id);
    prefillPoints(data.points);
    conditionValue.value = data.identity.condition || '';
    locationValue.value = data.location;
    const spec = String(route.query.specification || '');
    if (source === 'standard' && data.items.length) {
      const original = await api<{ rows: { table: number; row: number; criterion: string }[] }>('/specifications/' + spec);
      for (const row of original.rows) standardTexts.value[spec + ':' + row.table + ':' + row.row] = row.criterion;
    }
  } catch (e) { error.value = (e as Error).message; }
}
onMounted(async () => {
  await loadSource();
  if (resumed) {
    try {
      const value = await api<Draft>('/projects/' + encodeURIComponent(project.value) + '/worksheet-drafts/' + encodeURIComponent(resumed));
      if (value.protocol_draft !== draftId || value.protocol_table !== String(route.query.table || '') || value.copy_source !== copyId) {
        throw new Error('草稿来源与当前页面不一致，请从项目重新打开。');
      }
      restored.value = value;
    } catch (e) { error.value = (e as Error).message; }
  }
  setupReady.value = true;
});

watch([setupReady, draftForm], async ([ready, form]) => {
  const draft = restored.value;
  if (!ready || !form) return;
  if (!draft) {
    savedPlan = planSignature(form);
    return;
  }
  Object.assign(formValues.value, draft.fields);
  customItems.value = draft.custom_items ?? [];
  rowCriteria.value = draft.row_criteria ?? {};
  try { await refreshStandardTexts(); } catch (e) { error.value = (e as Error).message; }
  selected.value = draft.selected;
  rowItems.value = Object.fromEntries(Object.entries(draft.row_items).map(([row, items]) => [row, Array.isArray(items) ? items : [items]]));
  {
    pointText.value = draft.fields.points ?? (Object.entries(draft.fields).filter(([key]) => /^point_\d+$/.test(key)).map(([, label]) => label).join(", ") || pointText.value);
    conditionValue.value = draft.fields.condition ?? '';
    locationValue.value = draft.fields.location ?? '';
    material.value = draft.fields.sample_type ?? '';
    excluded.value = {};
    templatePoints.value.forEach((label, index) => {
      if (draft.required[index + 1]) {
        excluded.value[label] = draft.selected.filter(item => !draft.required[index + 1].includes(item));
      }
    });
  }
  await nextTick();
  for (const [name, value] of Object.entries(draft.fields)) {
    const element = form.elements.namedItem(name);
    if (element instanceof RadioNodeList) element.value = value;
    else if (element instanceof HTMLInputElement && element.type === 'checkbox') element.checked = Boolean(value);
    else if (element instanceof HTMLInputElement || element instanceof HTMLSelectElement || element instanceof HTMLTextAreaElement) element.value = value;
  }
  savedPlan = planSignature(form);
}, { flush: 'post' });
function availableSuggestions(row: Matrix['items'][number]) {
  return (row.suggested_component_ids ?? []).filter(id => !Object.entries(rowItems.value).some(
    ([number, items]) => Number(number) !== row.row && items.includes(id),
  ));
}
function mapRow(row: Matrix['items'][number], items: string[]) {
  const previous = rowItems.value[row.row] || [];
  if (!items.length) delete rowItems.value[row.row];
  else rowItems.value[row.row] = items.includes(':exclude') ? [':exclude'] : items;
  selected.value = [...new Set([...selected.value.filter(id => !previous.includes(id)), ...mappedItems.value])];
  for (const point of matrix.value?.points ?? []) {
    const cell = row.cells.find(value => value.column === point.column);
    for (const id of items.filter(item => item !== ':exclude')) {
      toggleRequired(point.amount + point.unit, id, cell?.state === 'required' || cell?.state === 'release_reference');
    }
  }
}
function excludeUnmappedRows() {
  for (const row of matrix.value?.items ?? []) {
    if (!rowItems.value[row.row]) rowItems.value[row.row] = [':exclude'];
  }
}
let lastAttempt: { body: string; key: string } | undefined;

function post<T>(path: string, body: unknown) {
  return api<T>(path, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) });
}

async function loadItems() {
  configuration.value = await api<Configuration>('/projects/' + encodeURIComponent(project.value) + '/configuration');
}

watch(project, async (value, _, cleanup) => {
  const controller = new AbortController();
  cleanup(() => controller.abort());
  error.value = '';
  selected.value = [];
  customItems.value = [];
  rowCriteria.value = {};
  editingCriteria.value = undefined;
  removedItem.value = undefined;
  editingItem.value = undefined;
  rowItems.value = {};
  configuration.value = undefined;
  reading.value = Boolean(value);
  if (!value) return;
  try {
    configuration.value = await api('/projects/' + encodeURIComponent(value) + '/configuration', { signal: controller.signal });
  } catch (e) {
    if (!controller.signal.aborted) error.value = (e as Error).message;
  } finally {
    if (!controller.signal.aborted) reading.value = false;
  }
}, { immediate: true });

async function perform(action: () => Promise<void>) {
  busy.value = true;
  error.value = '';
  try { await action(); }
  catch (e) { error.value = (e as Error).message; }
  finally { busy.value = false; }
}

function openInvalidDetails(event: Event) {
  if (event.target instanceof HTMLElement && event.target.closest('.study-details')) detailsOpen.value = true;
}
function readFields(form: HTMLFormElement) {
  return Object.fromEntries([...new FormData(form)].map(([key, value]) => [key, String(value).trim()]));
}
const fields = (event: Event) => readFields(event.target as HTMLFormElement);

function planSignature(form: HTMLFormElement) {
  return JSON.stringify([readFields(form), selected.value, rowItems.value, excluded.value, customItems.value, chosenProtocolStandards.value, rowCriteria.value]);
}
function hasUnsavedPlan() {
  return Boolean(savedPlan && draftForm.value && savedPlan !== planSignature(draftForm.value));
}
function confirmLeavingPlan() {
  return !hasUnsavedPlan() || window.confirm('研究计划有未保存的改动。确定离开吗？');
}
onBeforeRouteLeave(confirmLeavingPlan);
onBeforeRouteUpdate(confirmLeavingPlan);
function switchProject(value: string) {
  if (value === project.value) return;
  return router.replace({ path: '/new', query: { ...route.query, project: value } });
}
function beforeUnload(event: BeforeUnloadEvent) {
  if (!hasUnsavedPlan()) return;
  event.preventDefault();
  event.returnValue = '';
}
onMounted(() => window.addEventListener('beforeunload', beforeUnload));
onBeforeUnmount(() => window.removeEventListener('beforeunload', beforeUnload));

function saveDraft() {
  const form = draftForm.value;
  if (!form || !project.value) return;
  const criteria = selected.value.flatMap(id => {
    const value = criteriaFor(id);
    return rowCriteria.value[id] || value.specification_links.length || value.criteria_text ? [[id, value]] : [];
  });
  const draft: Draft = { custom_items: customItems.value.filter(item => selected.value.includes(item.id)), fields: readFields(form), selected: selected.value, row_items: rowItems.value,
    row_criteria: Object.fromEntries(criteria),
    required: Object.fromEntries(templatePoints.value.map((label, i) =>
      [i + 1, selected.value.filter(item => !excluded.value[label]?.includes(item))])), protocol_draft: draftId, protocol_table: String(route.query.table || ''), copy_source: copyId };
  return perform(async () => {
    const signature = planSignature(form);
    const result = await post<{ id: string }>('/projects/' + encodeURIComponent(project.value) + '/worksheet-drafts', draft);
    savedPlan = signature;
    draftLink.value = router.resolve({ path: '/new', query: { ...route.query, project: project.value, resume: result.id } }).href;
  });
}

function createProject(event: Event) {
  const data = fields(event);
  if (!confirmLeavingPlan()) return;
  return perform(async () => {
    await post('/projects', data);
    savedPlan = '';
    await switchProject(data.project);
  });
}

function addItems(event: Event) {
  const data = fields(event);
  return perform(async () => {
    const items = data.items.split(/\r?\n/).filter(line => line.trim()).map(line => {
      const [label, method, unit = '', type = '数值', ...extra] = line.split('\t').map(value => value.trim());
      if (!label || !method || extra.length || !['数值', '文字'].includes(type)) {
        throw new Error('每行填写检项和方法，可再填单位与结果类型。各列用 Tab 分隔，结果类型填写数值或文字。');
      }
      return { label, method, unit, result_type: type === '文字' ? 'qualitative' : 'numeric' };
    });
    const previous = new Set(configuration.value?.config.items.map(item => item.id));
    await post('/projects/' + encodeURIComponent(project.value) + '/items', {
      base_revision: configuration.value?.base_revision,
      idempotency_key: crypto.randomUUID(), reason: '新建研究表时添加检项', items,
    });
    await loadItems();
    selected.value = [...new Set([...selected.value, ...(configuration.value?.config.items ?? [])
      .filter(item => !previous.has(item.id)).map(item => item.id)])];
    (event.target as HTMLFormElement).reset();
  });
}

function save(event: Event) {
  const data = fields(event);
  return perform(async () => {
    if (draftId && !protocolLoaded.value) throw new Error('请重新打开原方案。');
    if (copyId && !copied.value) throw new Error('请重新打开来源研究表。');
    if (seedSlot && !seed.value) throw new Error('请重新从项目数据打开采用结果。');
    if (sampleId && !linkedSample.value) throw new Error('请从批号页面重新选择样品。');
    if (!templatePoints.value.length) throw new Error('请添加至少一个时间点。');
    if (matrix.value && !data.reviewed) throw new Error('请先核对方案和应检范围。');
    if (matrix.value && matrix.value.items.some(row => !rowItems.value[row.row]?.some(id => id === ':exclude' || selected.value.includes(id)))) {
      throw new Error('请为每个原方案行选择当前研究的检项，或明确不纳入。');
    }
    const items = selected.value;
    if (!items.length) throw new Error('请选择至少一个应检项目。');
    const incomplete = customItems.value.find(item => items.includes(item.id) && (!item.label.trim() || !item.method.trim()));
    if (incomplete) throw new Error('请编辑检项“' + (incomplete.label || '未命名') + '”，补全名称和检测方法；也可先保存草稿。');
    const points = templatePoints.value.map(text => {
      const match = /^(\d+)([MD])$/i.exec(text);
      if (!match) throw new Error('请使用 0M、3M 或 7D 这样的时间点格式。');
      return { amount: Number(match[1]), unit: match[2].toUpperCase(),
        required_items: items.filter(item => !excluded.value[text]?.includes(item)) };
    });
    const { start_date, basis } = data;
    const name = data.name || data.batch;
    const identity = Object.fromEntries(['batch', 'sample_type', 'presentation', 'study', 'study_id', 'condition', 'orientation', 'purpose', 'reporting_basis'].map(key => [key, data[key]]));
    const item_standards: Record<string, { specification_id: string; table: number; row: number }[]> = {};
    for (const [id, links] of Object.entries(seed.value?.item_standards || {})) {
      if (items.includes(id)) item_standards[id] = [...links];
    }
    for (const standard of protocolStandards.value?.found || []) {
      if (!chosenProtocolStandards.value.includes(standard.id)) continue;
      for (const link of standard.links) {
        if (items.includes(link.item_id)) (item_standards[link.item_id] ??= []).push({
          specification_id: standard.id, table: link.table, row: link.row,
        });
      }
    }
    const body = { name, start_date, basis, identity, items, points, custom_items: customItems.value.filter(item => items.includes(item.id)), equipment: data.equipment, location: data.location,
      row_criteria: Object.fromEntries(Object.entries(rowCriteria.value).filter(([id]) => items.includes(id))),
      item_standards: Object.fromEntries(Object.entries(item_standards).map(([id, links]) =>
        [id, [...new Map(links.map(link => [standardKey(link), link])).values()]])),
      ...(seedSlot ? { seed_slot_id: seedSlot } : {}),
      ...(linkedSample.value || seed.value?.identity.sample_id ? { sample_id: linkedSample.value?.id || seed.value?.identity.sample_id } : {}),
      ...(resumed ? { worksheet_draft_id: resumed } : {}),
      ...(draftId ? { protocol_draft_id: draftId, protocol_table: matrix.value?.table,
        protocol_row_items: Object.fromEntries(Object.entries(rowItems.value).map(([row, ids]) =>
          [row, ids.filter(id => items.includes(id))])) } : {}),
      ...(copied.value ? { copy_source_id: copyId, copy_source_plan_version: copied.value.plan_version,
        copy_standards: Boolean(data.copy_standards) } : {}) };
    const fingerprint = JSON.stringify([project.value, body]);
    if (lastAttempt?.body !== fingerprint) lastAttempt = { body: fingerprint, key: crypto.randomUUID() };
    const result = await post<{ id: string }>('/projects/' + encodeURIComponent(project.value) + '/worksheets', {
      ...body, idempotency_key: lastAttempt.key,
    });
    savedPlan = '';
    await router.push('/worksheets/' + encodeURIComponent(result.id));
  });
}
</script>

<template>
  <NAlert v-if="error" type="error" role="alert">{{ error }}</NAlert>
  <section v-if="!configuration" class="panel entry-panel">
    <h2>选择项目</h2>
    <p style="margin: 0 0 8px 0; color: #606266; font-size: 13px;">
      共 {{ projects.length }} 个项目可用，支持输入项目编号（如 501 / IP281）或名称关键字模糊检索：
    </p>
    <NSelect :value="project || null" :disabled="busy || Boolean(copyId || resumed || seedSlot)" placeholder="输入项目编号或名称快速搜索 (如 501 / IP281)..." aria-label="项目"
      filterable clearable
      @update:value="switchProject"
      :options="projects.map(p => ({ value: p.project, label: p.project + (p.name ? ' · ' + p.name : '') }))" />
    <details v-if="!copyId && !resumed && !seedSlot" :open="projects.length === 0" style="margin-top: 10px;">
      <summary>新建项目</summary>
      <form class="fields" @submit.prevent="createProject">
        <label>项目编号 <input name="project" required></label>
        <label>项目名称 <input name="name" required></label>
        <NButton attr-type="submit" :loading="busy">创建项目</NButton>
      </form>
    </details>
  </section>
  <section v-if="copied" class="panel">
    来源研究表 <RouterLink :to="'/worksheets/' + copied.id">{{ copied.name }}</RouterLink> · 计划版本 {{ copied.plan_version }}
  </section>
  <template v-if="configuration && !reading">
    <p v-if="protocolLoaded && !matrix"><a :href="'/api/v1/plan-drafts/' + draftId + '/file'">下载方案原件</a> · 对照原件填写检项和时间点。</p>
    <details v-if="matrix" class="panel">
      <summary>核对原方案检项</summary>
      <a :href="'/api/v1/plan-drafts/' + draftId + '/file'">下载方案原件</a>
      <NButton :disabled="busy || matrix.items.every(row => rowItems[row.row])" @click="excludeUnmappedRows">未映射行不纳入本表</NButton>
      <p v-for="warning in matrix.warnings" :key="warning" class="pending">{{ warning }}</p>
      <p v-if="matrix.warnings.some(warning => warning.includes('相对湿度'))" class="pending">请在研究资料的计划依据中注明采用的原件位置及理由。可先保存草稿。</p>
      <div v-for="standard in protocolStandards?.found" :key="standard.number + standard.id">
        <label><input v-model="chosenProtocolStandards" type="checkbox" :value="standard.id" :disabled="!standard.links.length">
          引用 {{ standard.filename }}<template v-if="standard.page"> · PDF 第 {{ standard.page }} 页</template></label>
        <span v-if="!standard.links.length"> · 请先到质量标准页确认条款对应的项目检项。</span>
      </div>
      <details v-if="matrix.conditions.length">
        <summary>查看方案中的考察条件</summary>
        <p v-for="condition in matrix.conditions" :key="condition.location">{{ condition.location }} · {{ condition.text }}</p>
      </details>
      <div class="matrix"><table>
        <thead><tr><th>原方案检项</th><th>项目检项</th><th v-for="point in matrix.points" :key="point.column">{{ point.amount }}{{ point.unit }}</th></tr></thead>
        <tbody><tr v-for="row in matrix.items" :key="row.row">
          <th scope="row">{{ row.label || '未识别检项' }}
            <details v-if="row.method_evidence?.length">
              <summary>查看原件方法依据</summary>
              <p v-for="source in row.method_evidence" :key="source.table + ':' + source.row + ':' + source.column">
                表 {{ source.table }} · 行 {{ source.row }} · 列 {{ source.column }}<br>{{ source.raw }}
              </p>
            </details>
          </th>
          <td><NSelect class="matrix-mapping" :aria-label="'原方案第 ' + row.row + ' 行对应检项'"
            multiple filterable :value="rowItems[row.row] || []" :disabled="busy" placeholder="选择检项或子结果"
            :options="[{ value: ':exclude', label: '不纳入本表' }, ...itemOptions.map(item => ({ ...item,
              disabled: Object.entries(rowItems).some(([r, ids]) => Number(r) !== row.row && ids.includes(item.value)) }))]"
            @update:value="mapRow(row, $event)" />
            <NButton v-if="availableSuggestions(row).length" text :disabled="busy"
              @click="mapRow(row, availableSuggestions(row))">带入标准匹配建议（{{ availableSuggestions(row).length }}）</NButton>
          </td>
          <td v-for="cell in row.cells" :key="cell.column">{{ cell.raw || '待核对' }}</td>
        </tr></tbody>
      </table></div>
    </details>
    <ul v-if="protocolStandards?.references?.length" class="list">
      <li v-for="reference in protocolStandards.references" :key="reference.number" :class="{ pending: reference.state !== 'available' }">
        方案引用 {{ reference.number }} · {{ referenceMessages[reference.state] }}
        <span v-if="reference.registered_links">已登记 {{ reference.registered_links }} 处关联。</span>
      </li>
    </ul>
    <p v-if="seed?.source_timepoints?.length" class="pending">已带入原表时间点 {{ seed.source_timepoints.join('、') }}。历史空格的应检状态请对照方案核对。</p>
    <section class="panel entry-panel">
      <h2>稳定性研究通用表</h2>
      <form ref="draftForm" class="fields ip281-template" @submit.prevent="save" @invalid.capture="openInvalidDetails">
        <div class="template-header-value"><b>项目编号</b><NSelect :value="project || null" aria-label="项目" filterable clearable placeholder="输入项目编号或名称搜索..." :disabled="busy || Boolean(resumed || sampleId || copyId || seedSlot)" @update:value="switchProject"
          :options="projects.map(p => ({ value: p.project, label: p.project + (p.name ? ' · ' + p.name : '') }))" /></div>
        <div class="template-header-value"><b>产品名称</b><span>{{ props.projects.find(item => item.project === project)?.name }}</span></div>
        <label>批号 <input name="batch" v-model="suggestedBatch" :readonly="Boolean(linkedSample)" required placeholder="例如：20260301"></label>
        <label>材料类别 <select name="sample_type" v-model="material" @change="chooseMaterial" required><option value="">请选择</option><option value="DS">原液</option><option value="DP">制剂</option><option value="placebo">安慰剂</option></select></label>
          <label>规格 <input name="presentation" v-model="formValues.presentation" required></label>
          <label>开始日期 <input name="start_date" type="date" v-model="formValues.start_date" required></label>
          <label>设备名称及编号 <input name="equipment" v-model="formValues.equipment"></label>
          <label>方案编号或内部研究名称 <input name="study_id" v-model="formValues.study_id" required></label>
          <fieldset class="header-options wide">
            <legend>稳定性类型</legend>
            <label v-for="[value, label] in [['long_term', '长期'], ['accelerated', '加速'], ['stress', '强力破坏']]" :key="value">
              <input type="radio" name="study" :value="value" v-model="formValues.study" required>{{ label }}
            </label>
          </fieldset>
          <fieldset class="header-options wide">
            <legend>考察条件</legend>
            <div class="header-choices">
              <button v-for="value in temperatureOptions" :key="value" type="button"
                :aria-pressed="conditionParts.includes(value)" @click="chooseTemperature(value)">{{ value }}</button>
              <label v-for="value in conditionOptions" :key="value">
                <input type="checkbox" :checked="conditionParts.includes(value)" @change="toggleCondition(value)">{{ value }}
              </label>
            </div>
            <label class="header-custom">条件 <input name="condition" v-model="conditionValue" required placeholder="可直接修改或填写其他条件"></label>
            <div class="header-choices" role="group" aria-label="放置方向">
              <label v-for="value in ['', '正置', '倒置', '平放']" :key="value">
                <input type="radio" name="orientation" :value="value" v-model="formValues.orientation">{{ value || '方向未注明' }}
              </label>
            </div>
          </fieldset>
          <fieldset class="header-options wide">
            <legend>稳定性开展地点</legend>
            <div class="header-choices">
              <button v-for="value in locationOptions" :key="value" type="button"
                :aria-pressed="locationValue === value" @click="locationValue = value">{{ value }}</button>
            </div>
            <label class="header-custom">地点 <input name="location" v-model="locationValue" placeholder="可直接填写其他地点"></label>
          </fieldset>
        <div class="wide template-table">
          <p class="template-legend"><span>固定表头</span><span>可选或可改</span><span>填写区</span></p>
          <div class="template-options">
            <NSelect v-model:value="selected" multiple filterable aria-label="应检项目" placeholder="添加检验项目"
              :options="itemOptions" />
            <NButton v-if="(configuration?.config.items ?? []).some(item => !selected.includes(item.id))"
              type="primary"
              @click="selected = [...new Set([...selected, ...(configuration?.config.items ?? []).map(item => item.id)])]">
              一键添加项目全部字典检项 ({{ (configuration?.config.items ?? []).length }}项)
            </NButton>
            <NButton v-if="candidates.some(item => !selected.includes(item.id))" 
              @click="selected = [...new Set([...selected, ...candidates.map(item => item.id)])]">添加候选检项</NButton>
            <NButton @click="standardTarget = undefined; showSpecifications = !showSpecifications">引用质量标准</NButton>
            <NButton @click="insertItem()">插入检项行</NButton>
            <NButton @click="insertPurityGroup('nr')">加入 NR CE-SDS 两行</NButton>
            <NButton @click="insertPurityGroup('sec')">加入 SEC-HPLC 三行</NButton>
            <NButton v-if="removedItem" @click="undoRemoveItem">撤销移除</NButton>
            <NButton @click="pointText += (pointText ? ', ' : '') + ([0,3,6,9,12,18,24,36].map(n => n + 'M').find(label => !templatePoints.includes(label)) || (Math.max(0, ...templatePoints.map(label => Number.parseInt(label) || 0)) + 1) + 'M')">添加时间点</NButton>
          </div>
          <p v-if="seed?.standard_candidate_count && !seed.items.length">
            此标准已识别 {{ seed.standard_candidate_count }} 条候选条款，当前项目没有已确认的关联检项。请在下方对照原文选择本表条款，或返回质量标准页登记项目检项。
          </p>
          <p v-if="showSpecifications && standardTarget">将条款关联到：{{ itemOptions.find(item => item.value === standardTarget)?.label }}</p>
          <SpecificationPicker :link-only="Boolean(standardTarget)" v-if="showSpecifications" :initial="String(route.query.specification || '')"
            :project="project" :material="material" :purpose="formValues.purpose" @add="addStandardItems" @close="showSpecifications = false" />
          <div v-if="editingItem" class="item-editor">
            <label>检项名称 <input v-model="editingItem.label"></label>
            <label>检测方法 <input v-model="editingItem.method"></label>
            <label>单位 <input v-model="editingItem.unit"></label>
            <label>结果类型 <select v-model="editingItem.result_type"><option value="numeric">数值</option><option value="qualitative">文字</option></select></label>
            <div v-for="(link, index) in editingItem.specification_links" :key="standardKey(link)" class="wide">引用标准：{{ standardTexts[standardKey(link)] }} <NButton text @click="editingItem.specification_links?.splice(index, 1)">移除引用</NButton></div>
            <label class="wide">可接受标准 <textarea v-model="editingItem.criteria_text" rows="2" maxlength="4000" placeholder="填写本结果行的适用标准，多条可分行"></textarea></label>
            <div class="wide entry-actions">
              <NButton type="primary" :disabled="!editingItem.label.trim() || !editingItem.method.trim()" @click="applyItem">应用到表格</NButton>
              <NButton @click="editingItem = undefined">取消</NButton>
            </div>
          </div>
          <details v-if="candidateNames.length" class="template-candidates">
            <summary>{{ material === 'DS' ? '原液' : material === 'DP' ? '制剂' : '通用' }}常用检项</summary>
            <p>{{ (materialItems[material] ?? commonItems).join('、') }}。</p>
            <p v-if="material === 'DS' || material === 'DP'">ADC 项目按需添加：{{ projectSpecific.join('、') }}。</p>
            <template v-if="material === 'DP'">
              <p>冻干制剂按需添加：{{ freezeDriedItems.join('、') }}。</p>
              <p>预充笔按需添加：{{ injectionDeviceItems.join('、') }}。</p>
            </template>
            <p>可在表中配置候选检项，或直接插入检项行。</p>
          </details>
          <div class="matrix"><table>
            <thead><tr><th rowspan="2">检验项目</th><th rowspan="2">可接受标准</th>
              <th :colspan="Math.max(1, templatePoints.length)">考察时间</th></tr>
              <tr><th v-for="(label, index) in templatePoints" :key="index" class="template-option">
                <input :aria-label="'时间点 ' + (index + 1)" :value="label"
                  @change="editPoint(index, ($event.target as HTMLInputElement).value.trim().toUpperCase())">
                <NButton text :aria-label="'移除时间点 ' + label" @click="editPoint(index, '')">移除</NButton>
              </th></tr></thead>
            <tbody>
            <tr v-if="!selected.length && !candidateNames.length">
              <th scope="row" class="template-option">检验项目</th>
              <td class="template-option">选择原液或制剂查看候选</td>
              <td v-for="(_, index) in templatePoints" :key="index"></td>
            </tr>
            <tr v-for="name in candidateNames.filter(name => !dismissedCandidates.includes(name) && !candidates.some(item => (item.label + ' ' + item.method).toLowerCase().includes(name.toLowerCase())))" :key="'candidate-' + name">
              <th scope="row" class="template-option">{{ name }}
                <div class="row-actions">
                  <NButton text size="tiny" style="color: #909399;" :aria-label="'不纳入候选 ' + name" @click="dismissCandidate(name)">忽略/删除</NButton>
                </div>
              </th>
              <td class="template-option">
                <NButton text @click="insertItem(name, name === '肽图' ? 'RP-UPLC 法 / RP-UPLC Method' : '')">配置此检项</NButton>
                <NButton text size="tiny" style="color: #909399; margin-left: 8px;" @click="dismissCandidate(name)">不纳入</NButton>
              </td>
              <td v-for="(_, index) in templatePoints" :key="index"></td>
            </tr>
            <tr v-for="(id, index) in selected" :key="id">
              <th class="template-option">{{ itemOptions.find(item => item.value === id)?.label }}
                <div class="row-actions">
                  <NButton text @click="editItem(id, index)">编辑</NButton>
                  <NButton text @click="insertItem('', '', '', index + 1)">下方插入</NButton>
                  <NButton text :aria-label="'移除检项 ' + id" @click="removeItem(id, index)">移除</NButton>
                </div></th>
              <td class="template-option">
                <div class="criterion-text">{{ criteriaFor(id).criteria_text }}</div>
                <p v-for="link in criteriaFor(id).specification_links" :key="standardKey(link)" class="criterion-text">{{ standardTexts[standardKey(link)] }} <a :href="'/api/v1/specifications/' + link.specification_id + '/file'">标准原件</a></p>
                <template v-if="editingCriteria?.id === id">
                  <label>可接受标准 <textarea v-model="editingCriteria.value.criteria_text" rows="2" maxlength="4000"></textarea></label>
                  <div v-for="(link, linkIndex) in editingCriteria.value.specification_links" :key="standardKey(link)">
                    {{ standardTexts[standardKey(link)] }}
                    <NButton text @click="editingCriteria.value.specification_links.splice(linkIndex, 1)">移除引用</NButton>
                  </div>
                  <NButton @click="applyCriteria">应用标准</NButton>
                  <NButton @click="editingCriteria = undefined">取消</NButton>
                </template>
                <NButton v-else text @click="editCriteria(id)">编辑标准</NButton>
                <NButton text @click="standardTarget = id; showSpecifications = true">关联标准条款</NButton>
              </td>
              <td v-for="(label, index) in templatePoints" :key="index">
                <label class="required-cell"><input type="checkbox" :checked="!excluded[label]?.includes(id)"
                  @change="toggleRequired(label, id, ($event.target as HTMLInputElement).checked)">
                  {{ excluded[label]?.includes(id) ? '不要求' : '应检' }}</label>
              </td>
            </tr></tbody>
          </table></div>
        </div>
        <details class="wide study-details" :open="detailsOpen">
          <summary>研究资料 · 正式建表前补齐</summary>
          <div class="fields">
            <label>时间点 <input name="points" v-model="pointText" placeholder="0M, 3M, 6M"></label>
            <label>工作表名称 <input name="name" v-model="formValues.name" placeholder="默认使用批号"></label>
            <label>样品用途 <select name="purpose" v-model="formValues.purpose"><option value="unknown">未注明</option><option value="routine">常规样品</option><option value="reference">参考品</option></select></label>
            <label>报告体系 <input name="reporting_basis" v-model="formValues.reporting_basis"></label>
            <label class="wide">计划依据 <input name="basis" v-model="formValues.basis"></label>
          </div>
        </details>
        <label v-if="matrix" class="wide review"><input name="reviewed" type="checkbox" required>已核对原件、研究条件和各时间点的应检项目。</label>
        <label v-if="seed?.source_timepoints?.length" class="wide review"><input name="reviewed_history" type="checkbox" required>已对照原件或方案核对各时间点的应检范围。</label>
        <label v-if="copied?.has_standards" class="wide review"><input name="copy_standards" type="checkbox">已核对标准仍适用，沿用来源表标准</label>
        <div class="wide entry-actions">
          <NButton attr-type="submit" type="primary" :loading="busy">创建研究表</NButton>
          <NButton :disabled="busy || !setupReady" @click="saveDraft">保存草稿</NButton>
        </div>
        <p v-if="draftLink" role="status">待补全计划已保存。<a :href="draftLink">重新打开已保存版本</a></p>
      </form>
    </section>
    <section class="panel entry-panel" >
      <details>
        <summary>添加项目检项</summary>
        <form class="fields" @submit.prevent="addItems">
          <label class="wide">粘贴检项、方法、单位、结果类型
            <textarea name="items" required rows="4" placeholder="从 Excel 复制，每行一个检项，单位和结果类型可省略"></textarea>
          </label>
          <NButton attr-type="submit" :loading="busy">添加检项</NButton>
        </form>
      </details>
    </section>
  </template>
</template>

<style scoped>
details { margin-top: 16px; }
summary { cursor: pointer; }
.fields { display: grid; grid-template-columns: repeat(auto-fit, minmax(min(100%, 210px), 1fr)); gap: 16px; align-items: end; margin-top: 12px; }
label { display: grid; gap: 5px; }
.point-scope { display: grid; gap: 6px; }
.matrix-mapping { width: 210px; max-width: 60vw; }
.review { display: flex; align-items: center; }
.wide { grid-column: 1 / -1; }

.template-header-value { display: grid; gap: 5px; background: #9ec4e2; padding: 9px; border-radius: 5px; }
.template-header-value span { background: #d5e7f4; padding: 7px 10px; border-radius: 7px; }
.ip281-template > label { padding: 5px 8px; background: #9ec4e2; border-radius: 5px; color: #102d49; }
.ip281-template > label select, .template-option { background: #d5e7f4; }
.template-table { min-width: 0; }
.item-editor { display: grid; grid-template-columns: repeat(auto-fit, minmax(160px, 1fr)); gap: 9px; padding: 12px; margin-bottom: 10px; background: #d5e7f4; border-radius: 6px; }
.row-actions { display: flex; flex-wrap: wrap; gap: 4px 9px; }
.criterion-text { white-space: pre-wrap; }
.header-options { min-width: 0; margin: 0; padding: 8px 12px 10px; border: 1px solid #adc9de; border-radius: 6px; background: #d5e7f4; }
.header-options legend { padding: 3px 8px; border-radius: 4px; background: #9ec4e2; font-weight: 650; }
.header-options > label { display: inline-flex; align-items: center; margin-right: 16px; }
.header-choices { display: flex; align-items: center; flex-wrap: wrap; gap: 6px 12px; }
.header-choices label { display: inline-flex; align-items: center; }
.header-options input:is([type=checkbox], [type=radio]) { width: auto; min-height: auto; accent-color: #22645f; }
.header-choices button { font: inherit; color: inherit; background: white; border: 1px solid #adc9de; border-radius: 5px; padding: 4px 9px; cursor: pointer; }
.header-choices button[aria-pressed=true] { background: #285575; color: white; border-color: #285575; }
.header-options .header-custom { display: grid; grid-template-columns: auto minmax(0, 1fr); align-items: center; margin: 6px 0; max-width: 680px; }
.template-legend { display: flex; gap: 6px; margin: 4px 0 10px; }
.template-legend span { padding: 3px 8px; border: 1px solid #b8cddd; border-radius: 4px; background: white; }
.template-legend span:first-child { background: #9ec4e2; }
.template-legend span:nth-child(2) { background: #d5e7f4; }
.study-details { margin-top: 0; }
.study-details > .fields { margin-top: 10px; }
.study-details > summary { padding: 8px 0; }

.template-item-settings { padding: 10px 16px; margin-top: 12px; }
.template-item-settings details { margin: 0; }
.ip281-template { gap: 8px; grid-template-columns: repeat(4, minmax(0, 1fr)); }
.ip281-template > label { padding: 5px 8px; }
@media (max-width: 850px) {
  .ip281-template { grid-template-columns: repeat(2, minmax(0, 1fr)); }
}
@media (max-width: 460px) {
  .ip281-template { grid-template-columns: minmax(0, 1fr); }
}
.template-candidates { margin: 4px 0 12px; }
.template-options { display: flex; flex-wrap: wrap; gap: 8px; margin-bottom: 10px; }
.template-options .n-select { width: min(100%, 390px); }
.template-table thead th { background: #9ec4e2; }
.template-table th.template-option, .template-table .template-option { background: #d5e7f4; }
.template-table td { background-color: white; }
.template-table td.template-option { background-color: #d5e7f4; }
.required-cell { display: flex; align-items: center; font-weight: normal; }
</style>
