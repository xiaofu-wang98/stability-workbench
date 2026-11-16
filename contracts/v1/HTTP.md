# 0.1.0 人工工作副本与轻量刷新

现有工作表契约保留；类型以v5-schema.json和openapi.json为准。

GET /api/v1/previews/{id}/ledger-matrix?after_revision=N：当权限隔离的服务端快照与项目revision一致，且原件哈希仍通过时，返回not_modified=true、base_revision和preview_id；前端保留当前状态并跳过重绘。没有可用快照时返回完整结果；有基线但数据变化时沿用精确候选增量。完整缓存还原为独立对象，不共享调用方可变字典。

GET /api/v1/previews/{id}/v5/manual-sheet?section=工作表：返回冻结格与已保存工作副本、合并范围、上传公共字段、当前base_revision和最近可见edit_id。只读角色可以读取；保存仍需confirm_fact。

POST .../v5/manual-sheet/preview：ManualSheetScope包含section、cells（坐标到工作文本）、merges（完整当前合并列表）、candidate_ids（明确被替换/不采用的原草稿）和results（每条sources原件非空坐标数组及fields最终解释）。返回每条绑定来源、全部原格问题、修改前后、排除数、base_revision与fingerprint；不写业务数据。

POST .../v5/manual-sheet：在DraftCommand的base_revision、idempotency_key、reason之外，提交scope、fingerprint、reviewed=true。需confirm_fact；对其他上传者的人工编辑/重建还需review_duplicate。工作表已提交、范围占用、来源变更、指纹过期、身份矛盾未解决会拒绝。事务内记录工作副本、明确问题决定、新草稿、原草稿退出及来源接收；不创建正式事实。

POST .../v5/manual-sheet/undo：DraftCommand加edit_id，必须是没有后续保存的未提交编辑；撤销整批，保留原有已解决来源决定。已正式入库、有后续修改或无权限时拒绝。幂等重试不重复操作。

限制：最多4000个改单元格、1000个结果、每条最多100个非空来源；每个合并最多4000格，总合并最多40000格。坐标须为大写Excel坐标。原件没有的空格不作为事实来源。角色和内部ID不能由客户端自行授予或填写。
