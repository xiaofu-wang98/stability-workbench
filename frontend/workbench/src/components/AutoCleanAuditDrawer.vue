<script setup lang="ts">
import { ref, watch } from 'vue';
import { NButton, NCollapse, NCollapseItem, NInput, NModal, NSelect, NTag, useMessage } from 'naive-ui';
import { api } from '../api';

type AuditSample = {
  rule_code: string;
  rule_label: string;
  field: string;
  section: string;
  row: number;
  column: number;
  raw_value: string;
  cleaned_value: string;
  reason: string;
};
type CleaningAuditPayload = {
  total: number;
  by_rule: { rule_code: string; rule_label: string; count: number }[];
  samples: AuditSample[];
};

const props = defineProps<{
  previewId: string;
  section?: string | null;
  project: string;
}>();

const message = useMessage();
const audit = ref<CleaningAuditPayload | null>(null);
const promoteModal = ref(false);
const promoting = ref(false);
const promoteForm = ref({
  name_zh: '',
  name_en: '',
  method: '',
  unit: '',
  category_code: 'purity_impurity',
  aliases_text: '',
});

const categoryOptions = [
  { label: '1.鉴别 / Identification', value: 'identification' },
  { label: '2.纯度和杂质 / Purity and Impurities', value: 'purity_impurity' },
  { label: '3.效价 / Potency', value: 'potency' },
  { label: '4.含量 / Content', value: 'content' },
  { label: '5.安全性 / Safety', value: 'safety' },
  { label: '6.理化与其它 / General Tests', value: 'physicochemical_other' },
];

async function loadAudit() {
  if (!props.previewId) {
    audit.value = null;
    return;
  }
  try {
    const qs = props.section ? '?section=' + encodeURIComponent(props.section) : '';
    audit.value = await api<CleaningAuditPayload>(
      '/previews/' + encodeURIComponent(props.previewId) + '/cleaning-audit' + qs,
    );
  } catch {
    audit.value = null;
  }
}

async function submitPromote() {
  if (!promoteForm.value.name_zh.trim() || !props.project) {
    message.warning('请填写中文标准检项名称');
    return;
  }
  promoting.value = true;
  try {
    const aliases = promoteForm.value.aliases_text
      .split(/[,，、\n]/)
      .map(s => s.trim())
      .filter(Boolean);
    await api('/projects/' + encodeURIComponent(props.project) + '/catalog/promote', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        name_zh: promoteForm.value.name_zh.trim(),
        name_en: promoteForm.value.name_en.trim(),
        method: promoteForm.value.method.trim(),
        unit: promoteForm.value.unit.trim(),
        category_code: promoteForm.value.category_code,
        aliases,
        also_add_to_project: true,
      }),
    });
    message.success(`已收入项目与平台总词典：${promoteForm.value.name_zh.trim()}`);
    promoteModal.value = false;
    promoteForm.value = { name_zh: '', name_en: '', method: '', unit: '', category_code: 'purity_impurity', aliases_text: '' };
  } catch (e) {
    message.error((e as Error).message);
  } finally {
    promoting.value = false;
  }
}

watch(() => [props.previewId, props.section], () => { void loadAudit(); }, { immediate: true });
</script>

<template>
  <div v-if="audit && (audit.total > 0 || project)" class="audit-strip">
    <div class="audit-strip-header">
      <div class="audit-badges">
        <NTag type="success" size="small" :bordered="false">
          已自动规范 {{ audit.total }} 项（原值留痕，不阻塞入库）
        </NTag>
        <NTag v-for="rule in audit.by_rule" :key="rule.rule_code" size="small" :bordered="false">
          {{ rule.rule_label }}：{{ rule.count }}
        </NTag>
      </div>
      <NButton v-if="project" size="tiny" secondary type="primary" @click="promoteModal = true">
        + 未收录检项一键收入词典
      </NButton>
    </div>

    <NCollapse v-if="audit.samples.length" style="margin-top: 8px;">
      <NCollapseItem :title="`展开查看已自动规范明细（展示前 ${audit.samples.length} 条原值 → 规范值对照）`" name="samples">
        <div class="audit-table-wrap">
          <table class="audit-table">
            <thead>
              <tr>
                <th>位置</th>
                <th>清洗规则</th>
                <th>原文（已留痕）</th>
                <th>规范解析值</th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="(s, idx) in audit.samples" :key="idx">
                <td>R{{ s.row }}C{{ s.column }} ({{ s.field }})</td>
                <td>{{ s.rule_label }}</td>
                <td class="mono-raw">{{ s.raw_value }}</td>
                <td class="mono-clean">{{ s.cleaned_value }}</td>
              </tr>
            </tbody>
          </table>
        </div>
      </NCollapseItem>
    </NCollapse>

    <NModal v-model:show="promoteModal" preset="card" title="一键收入本项目词典并提升至平台总词典" style="width: 500px;">
      <div class="promote-form">
        <label>一级大类</label>
        <NSelect v-model:value="promoteForm.category_code" :options="categoryOptions" />
        <label>标准中文检项名（必填）</label>
        <NInput v-model:value="promoteForm.name_zh" placeholder="例如：聚山梨酯80含量" />
        <label>英文名（可选）</label>
        <NInput v-model:value="promoteForm.name_en" placeholder="例如：Polysorbate 80 Content" />
        <label>分析方法（可选）</label>
        <NInput v-model:value="promoteForm.method" placeholder="例如：HPLC-CAD法" />
        <label>标准单位（可选）</label>
        <NInput v-model:value="promoteForm.unit" placeholder="例如：mg/mL" />
        <label>原表别名（逗号分隔，下次导入自动归一化命中）</label>
        <NInput v-model:value="promoteForm.aliases_text" placeholder="例如：PS80, 吐温80" />
      </div>
      <template #footer>
        <div style="display: flex; justify-content: flex-end; gap: 8px;">
          <NButton @click="promoteModal = false">取消</NButton>
          <NButton type="primary" :loading="promoting" @click="submitPromote">确认收入词典</NButton>
        </div>
      </template>
    </NModal>
  </div>
</template>

<style scoped>
.audit-strip {
  background: #f8fafc;
  border: 1px solid #cbd5e1;
  border-radius: 8px;
  padding: 10px 14px;
  margin-bottom: 12px;
}
.audit-strip-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  flex-wrap: wrap;
  gap: 8px;
}
.audit-badges {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
  align-items: center;
}
.audit-table-wrap {
  max-height: 240px;
  overflow: auto;
  background: #ffffff;
  border: 1px solid #e2e8f0;
  border-radius: 6px;
}
.audit-table {
  width: 100%;
  border-collapse: collapse;
  font-size: 12.5px;
}
.audit-table th, .audit-table td {
  padding: 6px 10px;
  border-bottom: 1px solid #f1f5f9;
  text-align: left;
}
.audit-table th {
  background: #f8fafc;
  color: #475569;
}
.mono-raw {
  color: #64748b;
  font-family: ui-monospace, SFMono-Regular, monospace;
}
.mono-clean {
  color: #15803d;
  font-weight: 600;
  font-family: ui-monospace, SFMono-Regular, monospace;
}
.promote-form {
  display: flex;
  flex-direction: column;
  gap: 8px;
}
.promote-form label {
  font-size: 13px;
  font-weight: 600;
  color: #334155;
  margin-top: 2px;
}
</style>
