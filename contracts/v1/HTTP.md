# 0.1.0 人工工作副本与轻量刷新

现有工作表契约保留；类型以v5-schema.json和openapi.json为准。

GET /api/v1/previews/{id}/ledger-matrix?after_revision=N：当权限隔离的服务端快照与项目revision一致，且原件哈希仍通过时，返回not_modified=true、base_revision和preview_id；前端保留当前状态并跳过重绘。没有可用快照时返回完整结果；有基线但数据变化时沿用精确候选增量。完整缓存还原为独立对象，不共享调用方可变字典。

GET /api/v1/previews/{id}/v5/manual-sheet?section=工作表：返回冻结格与已保存工作副本、合并范围、上传公共字段、当前base_revision和最近可见edit_id。只读角色可以读取；保存仍需confirm_fact。

POST .../v5/manual-sheet/preview：ManualSheetScope包含section、cells（坐标到工作文本）、merges（完整当前合并列表）、candidate_ids（明确被替换/不采用的原草稿）和results（每条sources原件非空坐标数组及fields最终解释）。返回每条绑定来源、全部原格问题、修改前后、排除数、base_revision与fingerprint；不写业务数据。

POST .../v5/manual-sheet：在DraftCommand的base_revision、idempotency_key、reason之外，提交scope、fingerprint、reviewed=true。需confirm_fact；对其他上传者的人工编辑/重建还需review_duplicate。工作表已提交、范围占用、来源变更、指纹过期、身份矛盾未解决会拒绝。事务内记录工作副本、明确问题决定、新草稿、原草稿退出及来源接收；不创建正式事实。

POST .../v5/manual-sheet/undo：DraftCommand加edit_id，必须是没有后续保存的未提交编辑；撤销整批，保留原有已解决来源决定。已正式入库、有后续修改或无权限时拒绝。幂等重试不重复操作。

限制：最多4000个改单元格、1000个结果、每条最多100个非空来源；每个合并最多4000格，总合并最多40000格。坐标须为大写Excel坐标。原件没有的空格不作为事实来源。角色和内部ID不能由客户端自行授予或填写。

## 研究工作台

- 历史文件预览可发送 `Upload-Progress-Id` 请求头。处理期间可轮询 `GET /api/v1/projects/{project}/preview-progress/{progress_id}`，读取解析阶段、已解析工作表数和候选数。最终成功或失败以原上传请求为准。
- 工作台可用 `compact=true` 上传并读取归档回执，再通过 `GET /api/v1/previews/{id}/workspace?section=...` 读取当前表候选和工作表目录。省略范围时打开首表；原预览接口仍提供全量候选。
- 工作表正式提交可返回 `worksheet_sync`。只处理已采用结果。缺少开始日期或材料阶段时返回需补齐的范围。已提交记录可调用 `POST /api/v1/previews/{id}/sync-worksheets` 重新核对同步状态。
- 研究表 Excel 回传可提交 `coordinates` 选择子集，并按原格指定 `keep`、`correct`、`retest`。响应返回本次处理及仍待核对坐标。未传 `coordinates` 时按原有整份回传范围处理。
- 研究表或样品删除先请求对象的 `archive-impact`，随后提交 `DELETE`，请求体含确认后的影响指纹、原因和幂等键。检项行通过 `GET /worksheets/{id}/remove-row?row_id=...` 预览，再通过同路径的 `POST` 提交。
- 研究表的 `copy-coa-to-0m` 使用当前放行选项指纹及预期采用版本，保留放行来源。时间点的 `point-metadata` 保存计划日期与签署文字。
- 历史建表的预填返回带明确标签的表头建议、来源原格和 `metadata_fingerprint`。自动建表保留这些表头建议待确认。明确提交历史表头值时须附 `basis`，采用来源建议时可提交该指纹以记录已核验原格。
- `GET /api/v1/worksheets/{sheet_id}/header-metadata` 只读预览当前研究范围的冻结原件表头。`POST` 提交研究表头、编辑租约、revision、幂等键及依据。新增字段省略时保留旧值，显式空字符串可清空。有效变更增加计划版本并记录变更前后值。产品正式名称、设备、方案编号、交付规格、开展地点与存放位置保存在计划版本中，不改变研究身份。
- `POST /api/v1/projects/{project}/controlled-workbook` 使用项目 revision 与所选研究表 ID 冻结并生成可下载的受控汇总 Excel。
- `GET/POST /api/v1/projects/{project}/current-specifications` 查询或明确设置已确认的当前适用标准。采用值可携带 `standard_assessment` 供网页提示。
