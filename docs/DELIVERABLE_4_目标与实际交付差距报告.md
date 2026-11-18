# 稳研工作台交付物四：目标与实际交付差距报告 (Gap Analysis)

## 1. 总体进度与状态评级

| 目标分类 | 规划需求点 | 当前实现状态 | 验收状态 | 剩余差距与阻碍说明 |
|---|---|---|---|---|
| **一、项目、样品与历史贯通** | 样品联合键匹配 (项目+阶段+批号+规格+用途) | 已实现 (`coa.py`, `identity_associations.py`) | 已验收通过 | 无重大缺口。 |
| | 正式研究表必须绑定样品，缺失时提示补齐 | 已实现 (`business_worksheets.py`) | 已验收通过 | 前端在缺失时提供新建样品引导。 |
| | 项目别名映射（如 501 ↔ IP501） | 已实现 (`project_aliases` 表) | 已验收通过 | 禁止全局盲目删前缀。 |
| | 历史 Excel 确认后同一事务补齐建表与映射 | 已实现 (`worksheet_seed.py`) | 核心批次已验收 | IP281/275/180B/318 已通；非典型未命名工作表仍需人工判断。 |
| | TEST 沙盒隔离（禁止历史导入向沙盒提交） | 已实现 (`workflow_projects.purpose`) | 已验收通过 | 沙盒模式下禁止历史汇总表直接导入。 |
| **二、清空、删除与单机编辑** | 网页单元格 Delete 清空留痕作废，格子可补填 | 已实现 (`result_versions.py`) | 已验收通过 | 记录作废人、时间、原因，撤销采用，支持补填。 |
| | Excel 上传空白默认不等于删除 | 已实现 (`worksheet_roundtrip.py`) | 已验收通过 | 空白视为未填，防止意外全表清空。 |
| | 检项行/研究表/样品软删除与影响清单展示 | 已实现 (`archive-impact` + `DELETE` 接口) | 后端已验收 | 前端交互需要更丰富的级联影响确认弹窗。 |
| | 90秒租约自动接管与并发保存保护 | 已实现 (`business_worksheets.py`) | 已验收通过 | 同操作员接管，多端保存校验 revision。 |
| **三、COA、0M 与增量回传** | 从指定 COA 版本快照复制到 0M | 已实现 (`copy-coa-to-0m` 接口) | 已验收通过 | 来源记录完整，后续 COA 变动不影响 0M。 |
| | 目标格冲突决策（更正/复测/保留原值） | 已实现 (`worksheet_commit.py`) | 已验收通过 | 同值跳过，不同值需显式决策。 |
| | 模板增量回传原子事务与子集提交 | 已实现 (`worksheet_roundtrip.py`) | 已验收通过 | 允许先提交正常项，未决冲突保留。 |
| **四、标准、时间点与受控交付** | 多版本标准维护与项目显式绑定有效版本 | 已实现 (`current_specifications` 表) | 已验收通过 | v20 迁移落地，切换不重写历史。 |
| | 上下限数值边界解析与前端浅红超限提示 | 已实现 (`specification_limits.py`) | 已验收通过 | 开闭区间正确计算，不确定结果显示待判断。 |
| | 时间点状态（空/已签/已签报告）与排序 | 已实现 (`xlsx_matrix.py`, `study_reports.py`) | 已验收通过 | 同单位按数值排序，跨单位按实际计划日期。 |
| | 常熟受控版式多批次纵向堆叠导出 | 已实现 (`stability_matrix_report.py`) | 已验收通过 | 批次间空两行，反读完全一致。 |
| | 可比性多批次比较表与 Word 趋势图 | 已实现 (`comparability.py`, `trend_export.py`) | 已验收通过 | 多批次共享事实版本。 |
| **五、接口、迁移与页面闭环** | 数据库迁移平滑接续 (v18 → v19 → v20) | 已实现 (`migrations.py`) | 已验证通过 | 单事务安全回滚。 |
| | 全套自动化测试回归 | 已实现 (`tests/`) | 455 项全部通过 | 100% 绿色。 |
| | 前端现代化工作台构建 | 已实现 (`frontend/workbench/dist`) | 编译完全成功 | vue-tsc 0 报错，vite 打包成功。 |

---

## 2. 核心代码位置与运行证据映射

1. **样品联合匹配与 0M 快照复制**：
   - 代码位置：[src/stability_hub/coa.py](file:///D:/stability-workbench/src/stability_hub/coa.py#L45-L120), [src/stability_hub/api.py](file:///D:/stability-workbench/src/stability_hub/api.py#L309-L330)
   - 运行证据：`outputs/acceptance/20261117-135525-e99b832f/evidence/ip501-coa-snapshot.json`
2. **单元格作废留痕与软删除**：
   - 代码位置：[src/stability_hub/result_versions.py](file:///D:/stability-workbench/src/stability_hub/result_versions.py#L180-L240), [src/stability_hub/business_worksheets.py](file:///D:/stability-workbench/src/stability_hub/business_worksheets.py#L320-L410)
   - 运行证据：`tests/test_business_worksheets.py::test_archive_worksheet_and_impact`
3. **常熟受控版式纵向堆叠导出**：
   - 代码位置：[src/stability_hub/stability_matrix_report.py](file:///D:/stability-workbench/src/stability_hub/stability_matrix_report.py#L80-L260)
   - 运行证据：`outputs/acceptance/20261117-135525-e99b832f/evidence/ip275-two-real-batches-controlled.json`
4. **标准限度判定与超限警示**：
   - 代码位置：[src/stability_hub/specification_limits.py](file:///D:/stability-workbench/src/stability_hub/specification_limits.py#L1-L150)
   - 运行证据：`tests/test_specification_limits.py`
5. **历史数据批量建表与分 Sheet 确认**：
   - 代码位置：[src/stability_hub/worksheet_seed.py](file:///D:/stability-workbench/src/stability_hub/worksheet_seed.py#L50-L160), [src/stability_hub/api.py](file:///D:/stability-workbench/src/stability_hub/api.py#L1126-L1140)
   - 运行证据：`outputs/acceptance/20261117-135525-e99b832f/evidence/ip281-history-to-study.json`

---

## 3. 仍存差距与待后续迭代项 (Gaps & Next Steps)

1. **非结构化 PDF 方案与标准的批量处理**：
   - 目前仅对 PDF 原件完成归档与基于本地轻量 OCR 的单页抽验（IP501 原液标准第 6 页 2 条条款）。根据用户关于“轻量化、不打包庞大 OCR 环境”的指示，PDF 复杂排版的全面自动解析应保持人工辅助核对模式，不列为本次必须全自动化的范围。
2. **历史 Excel 中非标准时间点的日期推导**：
   - 在某些历史表（如 IP318 中的 10D、2W），原件缺少明确的开试日期与计划取样日期。系统目前将其置为“待补充状态”，需研究员在工作台界面手工补录开始日期后方可精确排序。
3. **旧版前端向导与极少数非典型 Sheet 的裁决体验**：
   - 统计发现历史文件中仍有若干包含复杂辅助图表的工作表（见 `review-gap-inventory.json`），导入时会触发较多手动确认项。未来可进一步扩展特定样式规则以减少人工交互步数。
