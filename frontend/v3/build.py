from pathlib import Path
import re
import argparse
base=Path(__file__).resolve().parent
root=base.parents[1]
parser=argparse.ArgumentParser(description='Rebuild the preserved V3 dashboard.')
parser.add_argument('--output',type=Path,default=root/'app.html')
args=parser.parse_args()
s=(base/'template.html').read_text(encoding='utf-8')
s=s.replace('<script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.0/dist/chart.umd.min.js"></script>',
            '<script>\n'+(base/'vendor/chart.umd.js').read_text(encoding='utf-8')+'\n</script>')
replacements={
'分析检测部数智工作台 · Analytical Testing Hub':'稳研工作台 · 0.1.0 开发版',
'stability-hub-projects-v1':'stability-hub-projects-v3-layout',
'📋 支柱一：出厂放行全项质量可比性':'放行检验结果比较',
'⏱️ 支柱二：稳定性降解行为与耐受性可比性':'稳定性比较',
'批分析合并表头':'批次检验结果',
'同图叠加坡度':'结果与趋势',
'变更前 (500L 临床批) vs 变更后 (6000L PPQ/商业化批) · 全项检验数据合并大表与 Mean/Range 统计':'比较所选批次的检测结果、质量标准与接受限度',
'同一条件跨批次同图叠加对比 · 涵盖长期/加速/40℃应激 11项官方CQA衰减动力学曲线与斜率一致性':'按考察条件汇总时间点数据，结合趋势图开展评价',
'出厂放行质量可比性 · 变更前后工艺批次筛选控制台':'放行检验 · 样品选择',
'稳定性降解行为可比性 · 考察条件与参试批次控制台':'稳定性研究 · 条件与样品选择',
'供试样品全项检定台账 · 单样生命周期追踪与方案标准对照':'样品检测台账 · 信息、标准与检测结果',
'供试品检测台账全项数据矩阵 · 与注册质量标准逐项对照':'样品检测结果与接受限度',
'全生命周期检定台账与注册质量标准逐项对照大表':'检测结果与接受限度',
'注册质量标准对照 (Acceptance Criteria)':'质量标准与接受限度',
'注册质量标准对照':'质量标准与接受限度',
'依苏帕格鲁肽α 工艺变更前后出厂放行全项质量可比性汇总表 (合并表头)':'放行检验结果汇总',
'依苏帕格鲁肽α 工艺变更前后稳定性降解速率与可比性评价表':'稳定性结果汇总',
'📈 变更前后样品同图叠加衰减动力学曲线 (11 项官方 CQA，对齐 CTD 图 54~图 64)':'稳定性趋势图 · 按检测项目比较',
'🟣 虚线: 变更前 (500L) | 🔵 实线: 变更后 (6000L) | 🔴 虚线: 质量标准限度':'虚线：变更前 · 实线：变更后',
'检验数据摄取与智能任务分析':'检测数据接收与结果核对',
'1. 供试样品批号：':'样品批号：',
'2. 考察场景环境：':'考察条件：',
'40℃ 高温高湿破坏应激':'40℃ 应力试验',
'出厂放行 COA 检验基准':'放行检验',
'PPQ 质量可比性研究分析报告 · 模块化自由拼装生成器':'质量可比性研究报告 · 内容选择',
'法规依据：ICH Q5E · CDE 生物制品上市后变更指导原则 (3.2.S.2.6)':'',
'📥 拼装导出可比性报告':'📥 导出可比性研究报告',
'正在按选定模块拼装生成':'正在根据所选内容生成',
'任务 1：合流增量写入【项目检测总台账】':'任务 1：核对并登记检测结果',
'任务 2：执行放行与历史批次【一致性核验】':'任务 2：比较历史批次放行结果',
'将 14 项结果归档，执行缺项保护与防覆盖检测':'核对项目、批号与检测结果后入账',
'最新入库检测流水记录':'最近入库记录',
'保留完整文件哈希与溯源槽位映射':'',
'供试样品实体信息':'样品信息',
'实体批次':'样品批次',
}
for a,b in replacements.items():s=s.replace(a,b)
# Extract the existing data models. No additional report batches are introduced.
a=s.index('  const releaseItems = [');b=s.index('\n\n  releaseItems.forEach',a)
release=s[a:b].replace('  const releaseItems','const v3ReleaseItems',1)
a=s.index('  let timepoints =',s.index('function renderComparabilityPillar()'))
b=s.index('\n  Object.keys(cqaGenerators)',a)
model='function v3StabilityModel(){\n'+s[a:b]+'\nreturn {timepoints,cqaGenerators};\n}\n'
s=s.replace('</style>',(base/'v3.css').read_text(encoding='utf-8')+'\n</style>',1)
s=s.replace('</body>','<script>\n'+release+'\n'+model+(base/'v3.js').read_text(encoding='utf-8')+'\n</script>\n</body>')
s=s.replace('</style>',(base/'four-comments.css').read_text(encoding='utf-8')+'\n</style>',1).replace('</body>','<script>\n'+(base/'four-comments.js').read_text(encoding='utf-8')+'\n</script>\n</body>')
v4_scripts = '\n'.join([
    (base/'v4-api.js').read_text(encoding='utf-8'),
    (base/'v4-source-tables.js').read_text(encoding='utf-8'),
    (base/'v4-source-drawer.js').read_text(encoding='utf-8'),
    (base/'v4-intake.js').read_text(encoding='utf-8'),
    (base/'v4-result-cell.js').read_text(encoding='utf-8'),
    (base/'v4-intake-simple.js').read_text(encoding='utf-8'),
    (base/'v45-ledger.js').read_text(encoding='utf-8'),
    (base/'v4-ledger-review.js').read_text(encoding='utf-8'),
    (base/'v5-review.js').read_text(encoding='utf-8'),
    (base/'v5-tools.js').read_text(encoding='utf-8'),
    (base/'v52-worksheet.js').read_text(encoding='utf-8'),
    (base/'manual-sheet.js').read_text(encoding='utf-8'),
    (base/'result-display.js').read_text(encoding='utf-8'),
    (base/'research-reports.js').read_text(encoding='utf-8'),
    (base/'quick-comparison.js').read_text(encoding='utf-8'),
    (base/'business-worksheets.js').read_text(encoding='utf-8'),
])
s=s.replace('</style>',(base/'v4.css').read_text(encoding='utf-8')+'\n</style>',1).replace('</body>','<script>\n'+v4_scripts+'\n</script>\n</body>')
s=s.replace('</style>',(base/'v5.css').read_text(encoding='utf-8')+'\n</style>',1)
s=re.sub(r'<title>.*?</title>','<title>稳研工作台 · Stability Workbench 0.1.0 开发版</title>',s,count=1,flags=re.S)
s='\n'.join(line.rstrip() for line in s.splitlines())+'\n'
args.output.parent.mkdir(parents=True,exist_ok=True)
args.output.write_text(s,encoding='utf-8')
print('Built Stability Workbench 0.1.0 development dashboard from V3.')
