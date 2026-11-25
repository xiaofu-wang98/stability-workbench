export type DomainId = 'stability' | 'forced_degradation' | 'water_system' | 'air_system';
export type ScopeDimension = 'project_batch' | 'facility_loop';

export type IntakeScenarioId =
  | 'stability_source'
  | 'release_coa'
  | 'protocol'
  | 'workbook_return'
  | 'specification'
  | 'scan_pdf';

export type WorksheetKindId =
  | 'stability_study'
  | 'forced_degradation_study'
  | 'water_monitoring'
  | 'air_monitoring';

export type DeliveryGroupId =
  | 'controlled_tables'
  | 'word_reports'
  | 'future_monitoring_reports';

export interface DomainDefinition {
  id: DomainId;
  group: 'product_rd' | 'facility_monitoring';
  groupLabel: string;
  label: string;
  shortLabel: string;
  scopeDimension: ScopeDimension;
  scopeEntityLabel: string;
  status: 'active' | 'planned';
  description: string;
  futureScopeHint: string;
}

export const DOMAIN_DEFINITIONS: DomainDefinition[] = [
  {
    id: 'stability',
    group: 'product_rd',
    groupLabel: '药品研发与放行',
    label: '稳定性研究与放行',
    shortLabel: '稳定性研究',
    scopeDimension: 'project_batch',
    scopeEntityLabel: '项目',
    status: 'active',
    description: '覆盖稳定性方案、质量标准、放行 COA、历史汇总导入、研究表与受控交付。',
    futureScopeHint: '项目编号 · 样品批号 · 研究表',
  },
  {
    id: 'forced_degradation',
    group: 'product_rd',
    groupLabel: '药品研发与放行',
    label: '影响因素试验',
    shortLabel: '影响因素',
    scopeDimension: 'project_batch',
    scopeEntityLabel: '项目',
    status: 'planned',
    description: '高温、高湿、光照、酸碱氧化等强降解考察记录、降解率趋势与质量平衡报告。',
    futureScopeHint: '项目编号 · 样品批号 · 破坏条件',
  },
  {
    id: 'water_system',
    group: 'facility_monitoring',
    groupLabel: '公用设施与环境监测',
    label: '水系统监测',
    shortLabel: '水系统',
    scopeDimension: 'facility_loop',
    scopeEntityLabel: '厂房 / 系统回路',
    status: 'planned',
    description: '纯化水 / 注射用水制水与分配回路取样点监测、警戒/纠偏限趋势与周期回顾报告。',
    futureScopeHint: '厂房设施 · 制水回路 · 取样点',
  },
  {
    id: 'air_system',
    group: 'facility_monitoring',
    groupLabel: '公用设施与环境监测',
    label: '空气系统监测',
    shortLabel: '空气系统',
    scopeDimension: 'facility_loop',
    scopeEntityLabel: '厂房 / 洁净区',
    status: 'planned',
    description: '洁净区环境监测（尘埃粒子/浮游菌/沉降菌/表面微生物）及压缩空气监测趋势与报告。',
    futureScopeHint: '厂房设施 · 洁净区/房间 · 监测点',
  },
];

export interface IntakeScenarioDefinition {
  id: IntakeScenarioId;
  title: string;
  subtitle: string;
  formats: string;
  badge: string;
  nextStepHint: string;
  domain: DomainId;
}

export const STABILITY_INTAKE_SCENARIOS: IntakeScenarioDefinition[] = [
  {
    id: 'stability_source',
    title: '稳定性汇总 Excel',
    subtitle: '导入历史或周期稳定性汇总表，逐表核对争议后写入正式结果',
    formats: '.xlsx',
    badge: '最常用',
    nextStepHint: '入库后自动承接同步研究表与查结果',
    domain: 'stability',
  },
  {
    id: 'release_coa',
    title: '放行结果 / CoA Excel',
    subtitle: '导入批次放行检测数据表，作为 0M 放行引用来源归档',
    formats: '.xlsx',
    badge: '放行数据',
    nextStepHint: '入库后可用于研究表 0M 引用与对比',
    domain: 'stability',
  },
  {
    id: 'protocol',
    title: '稳定性方案 Word',
    subtitle: '自动识别方案考察矩阵、批号与条件，一键生成研究计划',
    formats: '.docx',
    badge: '建表起点',
    nextStepHint: '识别矩阵后直接按表新建研究表',
    domain: 'stability',
  },
  {
    id: 'workbook_return',
    title: '研究表模板回传',
    subtitle: '上传从工作台下载并离线填写的研究表 Excel，差异核对后回写',
    formats: '.xlsx',
    badge: '离线回写',
    nextStepHint: '回写完成可直达生成确认表或看趋势',
    domain: 'stability',
  },
  {
    id: 'specification',
    title: '质量标准 Word',
    subtitle: '归档质量标准原件并提取标准条款表，用于项目检项字典与 COA',
    formats: '.docx',
    badge: '标准源头',
    nextStepHint: '上传后直达核对条款并登记项目字典',
    domain: 'stability',
  },
  {
    id: 'scan_pdf',
    title: '扫描件 PDF (OCR)',
    subtitle: '归档扫描版质量标准或附件 PDF，识别文字表格后登记标准条款',
    formats: '.pdf',
    badge: 'OCR 识别',
    nextStepHint: '归档后进入原件对照识别与条款登记',
    domain: 'stability',
  },
];

export interface WorksheetKindDefinition {
  id: WorksheetKindId;
  domain: DomainId;
  title: string;
  subtitle: string;
  status: 'active' | 'planned';
}

export const WORKSHEET_KINDS: WorksheetKindDefinition[] = [
  {
    id: 'stability_study',
    domain: 'stability',
    title: '稳定性研究表',
    subtitle: '支持长期、加速、强力破坏计划，自动衔接标准条款与 0M 放行',
    status: 'active',
  },
  {
    id: 'forced_degradation_study',
    domain: 'forced_degradation',
    title: '影响因素试验表',
    subtitle: '面向高温、光照、酸碱氧化等破坏条件与质量平衡记录',
    status: 'planned',
  },
  {
    id: 'water_monitoring',
    domain: 'water_system',
    title: '水系统监测表',
    subtitle: '面向纯化水/注射用水回路与取样点的周期监测记录',
    status: 'planned',
  },
  {
    id: 'air_monitoring',
    domain: 'air_system',
    title: '空气系统监测表',
    subtitle: '面向洁净区环境与压缩空气监测点的日常及周期记录',
    status: 'planned',
  },
];

export function getDomainById(id: DomainId): DomainDefinition {
  return DOMAIN_DEFINITIONS.find(d => d.id === id) ?? DOMAIN_DEFINITIONS[0];
}
