# 稳研工作台真实文件验收记录

状态为阶段记录。各项目均在独立的验收数据目录验证。样品材料阶段、放行数值及原件无法唯一确定的解释如用于模拟，均在逐项证据中标出。

## 文件接收与预检

已登记 17 份文件并核对 SHA256。浏览器实际归档 4 份历史 Excel、4 份 Word 方案、2 份 Word 标准和 4 份 PDF。另有 3 份作为对照资料保留。历史 Excel 共识别 11,472 条候选，首轮覆盖 33 张工作表。《稳定性试验数据》首轮使用了临时项目，复查后根据 8 张原表一致标注的项目编号重新上传到 IP318。候选总数按原件去重。

运行证据保存在 `outputs/acceptance/20261117-135525-e99b832f/evidence`。入口分别为 `sources.json`、`browser-intake.json`、`section-review.json` 及 `ip318-correct-project-intake.json`。

## 有正式提交与产物的场景

| 场景 | 实际结果 | 证据 |
| --- | --- | --- |
| IP281 原液加速 | 分别处理批注、删除线及双语身份冲突后正式提交 102 条。建表后下载反读 102 格。第二个独立库用原件开始日期、位置和签署状态复验。 | `ip281-resolve-submit.json`、`ip281-export-readback.json`、`20261117-ip281-header-retest/evidence/ip281-header-export-readback.json` |
| IP275 制剂长期 | 通过模拟批注和辅助区域核对后提交 477 条。结果汇总的版本 ID 与库中完全一致。两个真实批次受控导出分别为 243、234 条，共 67 行原件标准。 | `ip275-submit-export.json`、`ip275-controlled-source-criteria.json` |
| IP180B 制剂强力破坏倒置 | 通过辅助区域、批注及标准删改的模拟核对后，提交 240 条。导出反读的全部 240 个正式版本 ID 与库中一致，涉及三个批次。 | `ip180b-browser-batch-review.json`、`ip180b-submit-export.json` |
| IP318 原液加速 | 浏览器确认同一来源行候选检项后直接提交 184 条。查到 46 条 10D 结果，带同一调查号的相关等电点记录保留不汇报状态。建表后下载验证混合时间轴，另对照原件修正 10D 计划日期并复验。 | `ip318-guided-submit.json`、`ip318-mixed-timepoints-export.json`、`ip318-source-date-corrected.json` |
| IP318 长期 DS（−80℃±10℃） | 原表 113 条结果正式提交，刷新后仍为已提交。两个批次的研究表分别带入原件开始日期 2026-03-28 和 2026-04-16，模拟补录材料阶段后分别导出 57、56 个有结果的单元格，与各研究表采用结果数一致。 | `ip318-long-ds-submit.json`、`ip318-long-ds-export.json` |
| IP318 加速和长期安慰剂 | 两张真实工作表分别提交 24、28 条结果。原件明确标注安慰剂，建表后两项研究保持同一样品身份。模拟补录材料阶段后，两份 Excel 分别反读 24、28 个结果单元格。 | `ip318-remaining-section-review.json`、`ip318-placebo-export.json` |
| IP318 其余四张原表 | 加速 DP、长期 DP、−40℃ 加速 DS、−20℃ 加速 DS 分别提交 178、140、94、90 条结果。隔离验收中按原表处理辅助区域及调查备注，补录模拟材料阶段。八份下载的 Excel 均与对应研究表采用的结果数一致。IP318 八张原表均已覆盖导入和导出。 | `ip318-four-section-decisions.json`、`ip318-four-section-export.json` |
| IP501 制剂 Word 标准与研究表 | 登记 40 条标准并选为当前版本，建立带样品的研究表。回传模拟结果后能在刷新和新下载中反读同一值。 | `ip501-standard-flow.json`、`ip501-worksheet-flow.json`、`ip501-roundtrip.json` |
| IP501 放行及 0M | 采用模拟 COA 第 1 版复制成正式 0M。更新 COA 后，隔离库里的 0M 值、放行版本和样品来源保持可核对。 | `ip501-coa-snapshot.json` |
| IP501 原液 PDF | OCR 保存 10 页，对照 PDF 第 6 页人工核对两条条款后登记，复读两条均保留来源页与原液范围。 | `pdf-standard-ocr.json`、`ip501-ds-pdf-clause-registration.json` |
| IP501 制剂 Word 方案第 8 表 | 浏览器建表返回 HTTP 201，复读确认原方案、表号、样品、22 个源表行映射和 0M 至 36M 的 9 个计划时点。下载填写模板，在 D16 填入模拟值 94.50% 后上传。预览仅识别一格新增，提交生成结果版本。刷新后原方案关联仍有效，再次导出的 Excel D16 与提交值一致。容器密闭完整性测试行按隔离验收模拟决定排除，储存条件 5℃±3℃为模拟补录。 | `ip501-word-protocol-build.json`、`ip501-word-protocol-roundtrip.json`、`ip501-dp-word-protocol-after-return.xlsx` |

## 仍需验证

常熟受控汇总复验对照 IP275 制剂长期源表的两个真实批次。核对了批号、开始日期、储存位置、长期选择框、签署标记及对应日期，标准行数分别为 34 和 33。修复后，第一批次 1M 模拟事件的编号和说明均出现在该时点备注格，第二批次未带入该备注。受控汇总将方法并入检项栏，与历史源表独立方法列的版式有差异。证据见 `evidence/ip275-controlled-layout-retest.json`。

按 IP281 制剂长期原表复验新版受控汇总。新增双语表头及考察类型、温度、湿度、放置方式和地点选择标记，月份列按数字、点分日期和签署状态分行。IP281 一个批次及 IP275 两个批次的签署日期和状态均与各自原表相符。IP275 同页批次间保留两个空行，模拟备注仍落在 1M。新版是通用受控格式，单元格位置与原件的个别合并区域仍有差异。证据见 `evidence/controlled-bilingual-template-retest.json`。

IP180B 九张真实原表均已正式提交。项目 Excel 的 `Records` 与 `Sources` 按来源 ID 逐条关联，九张表的计数分别与提交记录一致，合计 2,909 条。DS 加速原表 H51 经页面选择保留未划线的 5NTU。DP 长期倒置表 H27 原格仅写有“CCIT相城检测”，同行缺少检项、方法及检测值。隔离模拟核对记录了排除此格的依据，冻结原件保留该说明，项目导出验证 H27 未成为正式结果。证据见 `ip180b-all-section-decisions.json`、`ip180b-ds-active-revision.json`、`ip180b-dp-inverted-note.json` 和 `ip180b-project-final-export.json`。

IP275 的 DP 长期、DP 加速、ADC 长期和 ADC 加速原表，按来源 ID 回查项目 Excel，分别导出 477、256、362、200 条，合计 1,295 条，与正式提交记录一致。两个 ADC 表的 0M 放行来源说明按隔离模拟核对留存在冻结原件。DS 两表按 C9 已独立写明非还原型、D11 标注 HC%+LC% 的原件信息，模拟采用 C11 中文“还原型”，正式方法仍须检测记录确认。两张参考品表模拟选长期，单抗参考品模拟映射为 DS。四表分别提交 321、160、42、40 条。项目范围已读到 1,858 条，但 Excel 生成返回 HTTP 409 `verification_failed`，来源解释反读存在差异，尚未通过导出。证据见 `ip275-ds-long-method.json`、`ip275-ds-accelerated-method.json`、`ip275-reference-mab.json`、`ip275-reference-ds.json` 和 `ip275-project-final-export.json`。

IP275 导出复验通过。Excel 反读重放方法和标准修正时重新推导检项身份，沿用已提交的隔离数据完成导出。按来源 ID 核对，八张表的条数分别与提交记录一致，合计 1,858 条，其中 82 条保留参考品用途。失败与复验分别保存在 `ip275-project-final-export.json` 和 `ip275-project-repaired-export.json`。

IP281 八张原表均已提交。项目 Excel 按来源 ID 逐条核对，八张表的计数分别与提交记录一致，共 4,849 条，其中 74 条保留参考品用途。参考品表按批号所示来源模拟选为 DS，正式类别以批准资料为准。证据见 `ip281-all-section-decisions.json`、`ip281-tail-decisions.json`、`ip281-reference.json` 和 `ip281-project-export-recheck.json`。

四份历史 Excel 的 33 张原表均已覆盖正式提交和导出计数核对。原液 PDF 已人工登记两条条款，剩余条款、参考品标准及其他 PDF 路径待验证。

IP117 加速 Word 方案第 10 表在恢复副本建表。页面同时展示条件表第 5 表第 3 行的 60%RH±10%RH，以及正文第 41 段的 25%RH±10%RH。空白依据提交返回 422，模拟采用条件表并写明出处后返回 201。复读核对了依据、20 个检项和 0M、1M、2M、3M、6M 五个时点。下载模板后回传无单位模拟结果 96.20，再次导出反读一致。该检项尚未配置单位，带百分号的值提交被拒。正式湿度、检项单位及引用标准待批准资料确认。证据见 `evidence/ip117-condition-conflict.json`、`evidence/ip117-percent-without-unit.json` 及 `evidence/ip117-accelerated-plan-roundtrip.json`。

恢复演练使用已完成历史导入的隔离库创建备份，共复制 4 份历史原件、29 份既有导出与 4 份扫描原件。启动恢复副本后核对外键，下载既有交付并核对 SHA256。浏览器重新生成 IP318 的 851 条结果，导出快照的原件路径全部指向恢复副本。证据见 `restore-rehearsal/migration.json` 与 `evidence/restored-ip318-export.json`。

旧版 v45 审查库副本从数据库结构版本 5 迁移至 20，四个正式项目、两份原件的 SHA256 和对应导入预览均复核通过。新版启动时另建立了 `TEST` 沙盒。证据见 `restore-v45-review/migration.json` 与 `evidence/restore-v45-review.json`。

IP501 Word 方案研究表在恢复库再次回传 Excel。重复上传原填表被识别为未变化。修改旧模板的 D16 后预览显示冲突，填写依据并提交模拟更正后产生新版本。再次上传相同文件未生成版本，导出反读 D16 为 95.25%。证据见 `evidence/ip501-consecutive-workbook-return.json`。

在同一恢复副本中移除 IP501 检项。浏览器确认框列出受影响的 95.25% 结果。提交后复读确认研究方案关联与旧计划留痕，并核对数据库中原结果与更正结果均已作废。证据见 `evidence/ip501-row-removal.json`。

四份 Word 方案的八张矩阵均已覆盖建表、下载、模拟结果回传和再次导出。新增六张矩阵逐表核对原方案、原表行映射、条件、检项总数与时点，导出反读各表的模拟结果。未定批号及未核实的材料资料和开始日期在计划依据注明为模拟。另将 IP501 同批同样品的长期填表误上传到加速研究，预览返回 409 `worksheet_file_scope`，两个研究的采用版本经复读一致。证据见 `evidence/remaining-word-matrices.json`、`evidence/remaining-word-roundtrips.json` 与 `evidence/ip501-cross-study-file-guard.json`。

IP180B 与 IP281 两份长期 24M 扫描确认表均已从浏览器关联到同批次的历史研究。两份 Excel 反读了原件名和 SHA256，并核对批号和 24M 列。PDF 由归档入口单独下载，内容哈希与 Excel 中所列值一致。建表的材料阶段按隔离验收模拟补录。IP501 正式方案 PDF 也已下载复核，与冻结原件逐字节相同。证据见 `evidence/confirmation-scanned-source.json` 和 `evidence/ip501-pdf-protocol-archive-download.json`。

恢复相关改动后全量后端回归通过 455 项。

IP501 参考品 Word 标准的 23 条已在恢复副本登记并设为参考品当前标准，制剂当前标准仍为原版本。首轮建表发现选择原液类别会附加八个通用检项。修复后从参考品标准重新建表，研究表恰有 23 个标准检项，条款坐标与原文逐条匹配，导出的 Excel 逐条读回全部 23 条。材料阶段填写「参考品」，样品资料为隔离模拟。证据见 `evidence/ip501-reference-standard-flow.json` 与 `evidence/ip501-reference-worksheet-retest-flow.json`。

IP501 原液 PDF 第 6 页已核对的两条标准在恢复副本登记为原液当前标准，制剂和参考品各自的版本仍可查询。浏览器以两条标准建立模拟样品的原液研究表并下载模板。确认表 Excel 逐条读回标准原文与 PDF 第 6 页来源，从标准入口下载的 PDF 通过原件哈希核对。其余条款仍待核对。证据见 `evidence/ip501-pdf-standard-flow.json` 与 `evidence/ip501-pdf-worksheet-flow.json`。
