<script setup lang="ts">
import { computed, onMounted, ref } from 'vue';
import {
  NAlert,
  NButton,
  NCard,
  NCollapse,
  NCollapseItem,
  NEmpty,
  NInput,
  NModal,
  NSelect,
  NSpin,
  NTabPane,
  NTabs,
  NTag,
  useMessage,
} from 'naive-ui';
import { api, triggerDownload } from '../api';

type PlatformCategory = { code: string; display_order: number; name_zh: string; name_en: string; description: string };
type PlatformSubcategory = { code: string; category_code: string; display_order: number; name_zh: string; name_en: string };
type PlatformAlias = { id: string; raw_alias: string; norm_signature: string; locale: string; source: string };
type PlatformMethod = { id: string; code: string; name_zh: string; name_en: string; method_family: string; aliases: PlatformAlias[] };
type PlatformComponent = { id: string; code: string; name_zh: string; name_en: string; default_unit: string; value_kind: string; aliases: PlatformAlias[] };
type PlatformAssay = {
  id: string;
  code: string;
  category_code: string;
  category_name_zh: string;
  category_order: number;
  subcategory_code: string;
  subcategory_name_zh: string;
  name_zh: string;
  name_en: string;
  default_unit: string;
  pharmacopoeia_scope: string;
  modality_tags: string[];
  is_builtin: number;
  methods: PlatformMethod[];
  components: PlatformComponent[];
  aliases: PlatformAlias[];
};
type CatalogPayload = {
  catalog_version: string;
  unit_standard: string;
  categories: PlatformCategory[];
  subcategories: PlatformSubcategory[];
  assays: PlatformAssay[];
  total_assays: number;
};
type TemplateSpec = {
  id: string;
  name: string;
  category: string;
  format: 'xlsx' | 'docx';
  filename: string;
  reference: string;
  summary: string;
  minimal_changes: string[];
};

const message = useMessage();
const activeTab = ref<'catalog' | 'templates'>('catalog');
const loading = ref(false);
const error = ref('');

const catalog = ref<CatalogPayload | null>(null);
const selectedCategory = ref('');
const selectedSubcategory = ref('');
const searchQuery = ref('');

const templates = ref<TemplateSpec[]>([]);
const guideMarkdown = ref('');

// Inline add alias modal
const aliasModalVisible = ref(false);
const aliasTarget = ref<{ entity_type: string; entity_id: string; label: string } | null>(null);
const newAliasText = ref('');
const savingAlias = ref(false);

// Create platform assay modal
const createModalVisible = ref(false);
const savingAssay = ref(false);
const newAssay = ref({
  category_code: 'purity_impurity',
  subcategory_code: '',
  name_zh: '',
  name_en: '',
  default_unit: '%',
  method_zh: '',
  component_zh: '',
  aliases_text: '',
});

const subcategoryOptions = computed(() => {
  const subs = catalog.value?.subcategories || [];
  const filtered = selectedCategory.value ? subs.filter(s => s.category_code === selectedCategory.value) : subs;
  return [{ label: '全部二级细类', value: '' }, ...filtered.map(s => ({ label: s.name_zh, value: s.code }))];
});

const createSubcategoryOptions = computed(() => {
  const subs = (catalog.value?.subcategories || []).filter(s => s.category_code === newAssay.value.category_code);
  return [{ label: '无（可选）', value: '' }, ...subs.map(s => ({ label: s.name_zh, value: s.code }))];
});

const categorySelectOptions = computed(() =>
  (catalog.value?.categories || []).map(c => ({ label: `${c.name_zh} / ${c.name_en}`, value: c.code })),
);

async function loadCatalog() {
  loading.value = true;
  error.value = '';
  try {
    const params = new URLSearchParams();
    if (selectedCategory.value) params.set('category_code', selectedCategory.value);
    if (selectedSubcategory.value) params.set('subcategory_code', selectedSubcategory.value);
    if (searchQuery.value.trim()) params.set('query', searchQuery.value.trim());
    const qs = params.toString();
    catalog.value = await api<CatalogPayload>('/catalog/platform' + (qs ? '?' + qs : ''));
  } catch (e) {
    error.value = (e as Error).message;
  } finally {
    loading.value = false;
  }
}

async function loadTemplates() {
  try {
    const [tplRes, guideRes] = await Promise.all([
      api<{ templates: TemplateSpec[] }>('/templates'),
      api<{ markdown: string }>('/templates/guide'),
    ]);
    templates.value = tplRes.templates;
    guideMarkdown.value = guideRes.markdown;
  } catch (e) {
    error.value = (e as Error).message;
  }
}

function selectCategory(code: string) {
  selectedCategory.value = code;
  selectedSubcategory.value = '';
  void loadCatalog();
}

function openAliasModal(assay: PlatformAssay) {
  aliasTarget.value = { entity_type: 'assay', entity_id: assay.id, label: assay.name_zh };
  newAliasText.value = '';
  aliasModalVisible.value = true;
}

async function submitAlias() {
  if (!aliasTarget.value || !newAliasText.value.trim()) return;
  savingAlias.value = true;
  try {
    await api('/catalog/platform/aliases', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        entity_type: aliasTarget.value.entity_type,
        entity_id: aliasTarget.value.entity_id,
        raw_alias: newAliasText.value.trim(),
      }),
    });
    message.success(`已为「${aliasTarget.value.label}」添加别名：${newAliasText.value.trim()}`);
    aliasModalVisible.value = false;
    await loadCatalog();
  } catch (e) {
    message.error((e as Error).message);
  } finally {
    savingAlias.value = false;
  }
}

async function submitNewAssay() {
  if (!newAssay.value.name_zh.trim()) {
    message.warning('请填写中文标准检项名称');
    return;
  }
  savingAssay.value = true;
  try {
    const aliases = newAssay.value.aliases_text
      .split(/[,，、\n]/)
      .map(s => s.trim())
      .filter(Boolean);
    await api('/catalog/platform/assays', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        category_code: newAssay.value.category_code,
        subcategory_code: newAssay.value.subcategory_code,
        name_zh: newAssay.value.name_zh.trim(),
        name_en: newAssay.value.name_en.trim(),
        default_unit: newAssay.value.default_unit.trim(),
        methods: newAssay.value.method_zh.trim() ? [{ name_zh: newAssay.value.method_zh.trim() }] : [],
        components: newAssay.value.component_zh.trim()
          ? [{ name_zh: newAssay.value.component_zh.trim(), unit: newAssay.value.default_unit.trim() }]
          : [],
        aliases,
      }),
    });
    message.success(`已新增平台标准检项：${newAssay.value.name_zh.trim()}`);
    createModalVisible.value = false;
    newAssay.value = {
      category_code: 'purity_impurity',
      subcategory_code: '',
      name_zh: '',
      name_en: '',
      default_unit: '%',
      method_zh: '',
      component_zh: '',
      aliases_text: '',
    };
    await loadCatalog();
  } catch (e) {
    message.error((e as Error).message);
  } finally {
    savingAssay.value = false;
  }
}

function downloadTpl(tpl: TemplateSpec) {
  triggerDownload('/api/v1/templates/' + encodeURIComponent(tpl.id) + '/download', tpl.filename);
  message.success(`正在下载：${tpl.filename}`);
}

onMounted(() => {
  void loadCatalog();
  void loadTemplates();
});
</script>

<template>
  <div class="catalog-hub">
    <NAlert v-if="error" type="error" :bordered="false" style="margin-bottom: 14px;">{{ error }}</NAlert>

    <NTabs v-model:value="activeTab" type="segment">
      <NTabPane name="catalog" tab="平台标准总词典（6大类）">
        <div class="hub-toolbar">
          <div class="cat-pills">
            <button
              type="button"
              class="cat-pill"
              :class="{ active: selectedCategory === '' }"
              @click="selectCategory('')"
            >
              全部大类 ({{ catalog?.total_assays ?? 0 }})
            </button>
            <button
              v-for="cat in catalog?.categories || []"
              :key="cat.code"
              type="button"
              class="cat-pill"
              :class="{ active: selectedCategory === cat.code }"
              @click="selectCategory(cat.code)"
            >
              {{ cat.name_zh }}
            </button>
          </div>
          <div class="hub-search-row">
            <NSelect
              v-model:value="selectedSubcategory"
              :options="subcategoryOptions"
              style="width: 200px;"
              @update:value="loadCatalog"
            />
            <NInput
              v-model:value="searchQuery"
              placeholder="搜索中英文检项、分析方法、缩写别名（如 nrCE、SEC、DAR）..."
              clearable
              style="max-width: 360px;"
              @keyup.enter="loadCatalog"
              @clear="loadCatalog"
            />
            <NButton type="primary" ghost @click="loadCatalog">检索</NButton>
            <NButton type="primary" @click="createModalVisible = true">+ 新增平台标准检项</NButton>
          </div>
        </div>

        <div class="hub-meta-bar" v-if="catalog">
          <NTag size="small" type="info" :bordered="false">词典版本：{{ catalog.catalog_version }}</NTag>
          <NTag size="small" type="success" :bordered="false">单位规范：{{ catalog.unit_standard }}</NTag>
          <span class="hub-meta-hint">所有新项目自动继承本平台总词典；导入历史表、质量标准、CoA 与研究表共享同一归一化匹配引擎。</span>
        </div>

        <NSpin :show="loading">
          <div v-if="catalog && catalog.assays.length" class="catalog-table-wrap">
            <table class="catalog-table">
              <thead>
                <tr>
                  <th style="width: 155px;">一级大类 / 二级细类</th>
                  <th style="width: 210px;">标准检项（中 / 英）</th>
                  <th style="width: 190px;">推荐分析方法</th>
                  <th style="width: 210px;">标准子检项 &amp; 单位</th>
                  <th>已收录别名（归一化匹配）</th>
                  <th style="width: 100px;">操作</th>
                </tr>
              </thead>
              <tbody>
                <tr v-for="assay in catalog.assays" :key="assay.id">
                  <td>
                    <div class="cat-badge">{{ assay.category_name_zh }}</div>
                    <div v-if="assay.subcategory_name_zh" class="subcat-label">{{ assay.subcategory_name_zh }}</div>
                  </td>
                  <td>
                    <div class="assay-zh">{{ assay.name_zh }}</div>
                    <div class="assay-en">{{ assay.name_en }}</div>
                  </td>
                  <td>
                    <div v-for="m in assay.methods" :key="m.id" class="method-line">
                      <span>{{ m.name_zh }}</span>
                      <span v-if="m.name_en" class="muted-sub"> · {{ m.name_en }}</span>
                    </div>
                  </td>
                  <td>
                    <div v-for="c in assay.components" :key="c.id" class="comp-line">
                      <span>{{ c.name_zh }}</span>
                      <NTag v-if="c.default_unit" size="tiny" :bordered="false" style="margin-left: 6px;">{{ c.default_unit }}</NTag>
                    </div>
                    <NTag v-if="!assay.components.length && assay.default_unit" size="tiny" :bordered="false">
                      默认单位：{{ assay.default_unit }}
                    </NTag>
                  </td>
                  <td>
                    <div class="alias-chips">
                      <span v-for="al in assay.aliases.slice(0, 8)" :key="al.id" class="alias-chip">{{ al.raw_alias }}</span>
                      <span v-if="assay.aliases.length > 8" class="alias-more">+{{ assay.aliases.length - 8 }}</span>
                    </div>
                  </td>
                  <td>
                    <NButton size="tiny" secondary @click="openAliasModal(assay)">+ 别名</NButton>
                  </td>
                </tr>
              </tbody>
            </table>
          </div>
          <NEmpty v-else description="没有匹配的平台标准检项，可点击右上角新增。" style="padding: 36px 0;" />
        </NSpin>
      </NTabPane>

      <NTabPane name="templates" tab="最小改动标准模板库（7份受控模板）">
        <div class="tpl-banner">
          <div>
            <strong>最小改动标准模板库（SME / QC 主管审阅发布版）</strong>
            <p class="tpl-banner-sub">
              保留公司现有 QC-运营受控文件与 2026 近期表格习惯，仅落地 T1~T5 五处高收益轻量拆格；旧版历史汇总表仍保留完整向后兼容解析。
            </p>
          </div>
        </div>

        <div class="tpl-grid">
          <NCard v-for="tpl in templates" :key="tpl.id" size="small" class="tpl-card">
            <div class="tpl-head">
              <div>
                <NTag size="small" :type="tpl.format === 'xlsx' ? 'success' : 'info'" :bordered="false">
                  {{ tpl.category }} · .{{ tpl.format }}
                </NTag>
                <h3 class="tpl-title">{{ tpl.name }}</h3>
              </div>
              <NButton type="primary" size="small" @click="downloadTpl(tpl)">下载模板</NButton>
            </div>
            <p class="tpl-ref">参考基准：{{ tpl.reference }}</p>
            <p class="tpl-summary">{{ tpl.summary }}</p>
            <ul class="tpl-changes">
              <li v-for="(item, idx) in tpl.minimal_changes" :key="idx">{{ item }}</li>
            </ul>
          </NCard>
        </div>

        <NCard size="small" style="margin-top: 16px;" v-if="guideMarkdown">
          <NCollapse>
            <NCollapseItem title="查看《标准模板最小改动填表说明》（含 T1~T5 拆格规范与 SOP(G)-RQA-0-001 单位要求）" name="guide">
              <pre class="guide-pre">{{ guideMarkdown }}</pre>
            </NCollapseItem>
          </NCollapse>
        </NCard>
      </NTabPane>
    </NTabs>

    <NModal v-model:show="aliasModalVisible" preset="card" title="添加平台词典别名" style="width: 460px;">
      <p style="margin: 0 0 10px; color: #475569;">
        目标标准检项：<strong>{{ aliasTarget?.label }}</strong>
      </p>
      <NInput v-model:value="newAliasText" placeholder="输入新别名或缩写（如 nrCE-SDS、高聚体等）" @keyup.enter="submitAlias" />
      <template #footer>
        <div style="display: flex; justify-content: flex-end; gap: 8px;">
          <NButton @click="aliasModalVisible = false">取消</NButton>
          <NButton type="primary" :loading="savingAlias" @click="submitAlias">保存别名</NButton>
        </div>
      </template>
    </NModal>

    <NModal v-model:show="createModalVisible" preset="card" title="新增平台标准检项" style="width: 540px;">
      <div class="modal-form">
        <label>一级大类（必填）</label>
        <NSelect v-model:value="newAssay.category_code" :options="categorySelectOptions" />
        <label>二级细类（可选）</label>
        <NSelect v-model:value="newAssay.subcategory_code" :options="createSubcategoryOptions" />
        <label>中文标准检项名（必填）</label>
        <NInput v-model:value="newAssay.name_zh" placeholder="例如：游离巯基含量" />
        <label>英文标准检项名</label>
        <NInput v-model:value="newAssay.name_en" placeholder="例如：Free Thiol Content" />
        <label>推荐分析方法</label>
        <NInput v-model:value="newAssay.method_zh" placeholder="例如：Ellman法" />
        <label>默认子检项名称</label>
        <NInput v-model:value="newAssay.component_zh" placeholder="留空则默认与检项同名" />
        <label>默认标准单位（SOP(G)-RQA-0-001）</label>
        <NInput v-model:value="newAssay.default_unit" placeholder="例如：%、mg/mL、mOsmol/kg、EU/mL" />
        <label>常用别名（用逗号分隔）</label>
        <NInput v-model:value="newAssay.aliases_text" placeholder="例如：Free Thiol, 游离巯基" />
      </div>
      <template #footer>
        <div style="display: flex; justify-content: flex-end; gap: 8px;">
          <NButton @click="createModalVisible = false">取消</NButton>
          <NButton type="primary" :loading="savingAssay" @click="submitNewAssay">确认新增</NButton>
        </div>
      </template>
    </NModal>
  </div>
</template>

<style scoped>
.catalog-hub {
  display: flex;
  flex-direction: column;
  gap: 14px;
}
.hub-toolbar {
  display: flex;
  flex-direction: column;
  gap: 12px;
  margin-top: 12px;
  background: #ffffff;
  border: 1px solid #e2e8f0;
  border-radius: 10px;
  padding: 14px 16px;
}
.cat-pills {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
}
.cat-pill {
  border: 1px solid #cbd5e1;
  background: #f8fafc;
  color: #334155;
  border-radius: 999px;
  padding: 5px 13px;
  font-size: 13px;
  cursor: pointer;
  transition: all 0.15s ease;
}
.cat-pill:hover {
  border-color: #1e5aa8;
  color: #1e5aa8;
}
.cat-pill.active {
  background: #1e5aa8;
  border-color: #1e5aa8;
  color: #ffffff;
  font-weight: 600;
}
.hub-search-row {
  display: flex;
  flex-wrap: wrap;
  gap: 10px;
  align-items: center;
}
.hub-meta-bar {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 10px;
  margin: 12px 0;
}
.hub-meta-hint {
  font-size: 13px;
  color: #64748b;
}
.catalog-table-wrap {
  background: #ffffff;
  border: 1px solid #e2e8f0;
  border-radius: 10px;
  overflow: auto;
}
.catalog-table {
  width: 100%;
  border-collapse: collapse;
  font-size: 13.5px;
}
.catalog-table th {
  text-align: left;
  background: #f8fafc;
  color: #334155;
  font-weight: 600;
  padding: 10px 12px;
  border-bottom: 1px solid #e2e8f0;
}
.catalog-table td {
  padding: 10px 12px;
  border-bottom: 1px solid #f1f5f9;
  vertical-align: top;
}
.cat-badge {
  font-weight: 600;
  color: #1e3a5f;
}
.subcat-label {
  font-size: 12px;
  color: #64748b;
  margin-top: 2px;
}
.assay-zh {
  font-weight: 600;
  color: #0f172a;
}
.assay-en {
  font-size: 12px;
  color: #64748b;
  margin-top: 2px;
}
.method-line, .comp-line {
  margin-bottom: 3px;
}
.muted-sub {
  color: #64748b;
  font-size: 12px;
}
.alias-chips {
  display: flex;
  flex-wrap: wrap;
  gap: 5px;
}
.alias-chip {
  background: #f1f5f9;
  color: #334155;
  border-radius: 4px;
  padding: 2px 7px;
  font-size: 12px;
}
.alias-more {
  color: #64748b;
  font-size: 12px;
  align-self: center;
}
.tpl-banner {
  margin: 12px 0;
  padding: 14px 16px;
  background: #f0f7ff;
  border: 1px solid #bfdbfe;
  border-radius: 10px;
}
.tpl-banner-sub {
  margin: 4px 0 0;
  font-size: 13px;
  color: #334155;
}
.tpl-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(420px, 1fr));
  gap: 14px;
}
.tpl-card {
  border-radius: 10px;
}
.tpl-head {
  display: flex;
  justify-content: space-between;
  align-items: flex-start;
  gap: 12px;
}
.tpl-title {
  margin: 6px 0 4px;
  font-size: 15px;
  color: #0f172a;
}
.tpl-ref {
  margin: 0 0 6px;
  font-size: 12px;
  color: #64748b;
}
.tpl-summary {
  margin: 0 0 8px;
  font-size: 13px;
  color: #334155;
}
.tpl-changes {
  margin: 0;
  padding-left: 18px;
  font-size: 12.5px;
  color: #1e293b;
}
.tpl-changes li {
  margin-bottom: 4px;
}
.guide-pre {
  white-space: pre-wrap;
  font-family: inherit;
  font-size: 13px;
  line-height: 1.65;
  color: #1e293b;
  margin: 0;
}
.modal-form {
  display: flex;
  flex-direction: column;
  gap: 8px;
}
.modal-form label {
  font-size: 13px;
  font-weight: 600;
  color: #334155;
  margin-top: 4px;
}
</style>
