# 稳研工作台架构

工作台是本机运行的 Python 模块化单体。浏览器经 FastAPI 调用业务服务，业务服务用 SQLite 事务持久化状态，按需生成 Excel、Word 或趋势图。

| 位置 | 职责 |
|---|---|
| `start.py`、`setup.cmd`、`start.cmd` | 创建环境、构建两个界面、选择数据目录并启动应用 |
| `frontend/workbench/src/` | Vue 工作台。主要入口包括导入、建研究表、研究表编辑、质量标准、项目结果、趋势和交付 |
| `frontend/v3/`、`app.html` | 旧版来源核对及兼容页面。修改前者后用 `frontend/v3/build.py` 构建已跟踪的 `app.html` |
| `src/stability_hub/api.py`、`workflow_contracts.py`、`contracts/v1/` | HTTP 入口、请求模型和共享契约 |
| `protocol_preview.py`、`specification_preview.py`、`stability_summary.py`、`xlsx_matrix.py` | 识别 Word 方案与标准、历史汇总表，保留来源和待核对信息 |
| `workflow.py`、`mapping.py`、`identity_associations.py`、`assay_catalog.py` | 导入核对、身份与检项关联、预检和提交 |
| `worksheet_seed.py`、`business_worksheets.py`、`worksheet_roundtrip.py`、`coa.py` | 研究计划草稿和版本、表内编辑、模板差异回传、样品与 COA |
| `result_versions.py`、`result_display.py`、`study_reports.py`、`stability_matrix_report.py` | 结果版本、当前采用值、趋势及报告快照 |
| `storage.py`、`migrations.py`、`scripts/backup.py` | SQLite 初始化和迁移，备份和完整性核对 |
| `tests/` | 用隔离库及合成文档回归业务规则 |

## 关键业务链路

1. **原件到正式结果。** 先保存原件与来源坐标，再解析候选、核对检项和样品身份、执行预检并事务提交。无法确定的身份进入人工核对。正式版本、来源和处理记录支持回查。
2. **方案与标准到研究表。** 选择方案矩阵，按原行、时间列配置检测；用项目、材料和用途核对标准。草稿可分次保存。正式研究计划包含独立的检项行、应检时间点和标准原文引用。
3. **样品到 0M。** 样品身份包括项目、批号、材料类别等。COA 支持网页录入和模板回传。研究表按样品及检项选择可核验的放行版本，旧引用在来源变化后进入待复核。
4. **结果到交付。** 首次检测、复测、更正、采用及作废保留版本。当前矩阵、趋势和新报告使用当前状态。已生成报告保留交付时的证据。

开发验证使用独立数据目录。数据库、真实业务文件以及逐份验收证据在本地保存。更多规则可参阅 [主要规则](docs/主要规则.md) 和 [脚本职责](docs/脚本职责.md)。
