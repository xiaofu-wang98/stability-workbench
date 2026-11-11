# 稳研工作台 0.1.0 开发记录

代码基线为 Stability Data Hub `codex/0.2.0` 的 `e554f8c`。研究工作表在 `codex/stability-workbench-0.1.0` 开发。现有解析、正式采用查询、趋势和 Office 输出仍由 Stability 核心提供。

## 研究工作表

- `business_worksheets.py` 管理计划、行与时间点 ID、版面、编辑占用和工作表 revision。计划延长需要明确选择本表的时间点和应检项目，保存新计划版本及依据。
- `result_versions.py` 处理网页写入。首次填值采用，更正保留旧版本和原因并更新采用，复测保留独立检测身份并等待显式采用。原件导入继续使用现有复核规则。两种写入共用检测、结果版本、正式采用和查询。
- 研究表按已知业务身份查询旧结果。当前身份支持项目、材料类别、批次、规格、方案、条件、用途、报告体系、检项、方法和时间点。放置方向单独填写，复用既有解析器生成的条件身份，例如 `25℃ · 正置`。只匹配相同完整条件。旧结果缺失或模糊的方向需要业务核对。
- 编辑占用由后端校验。保存、计划延长、对应槽位的来源提交和采用复核都检查占用。占用 90 秒到期，浏览器每 25 秒续期。结果和版面同事务保存。工作表保存检查工作表 revision 和修改单元格的结果版本。管理员强制释放、离线草稿恢复、工作表移动和删除待实现。
- 新增导出可从空计划或有结果的计划生成 Excel 确认表。文件使用冻结计划和采用结果，反读通过后登记交付快照。已交付文件保持原字节。模板当前提供基本字段、应检范围与人工总结行。正式报告模板、多标准评价、事件关联和内网身份接入待完成。

## 构建与验证

```powershell
npm ci --prefix frontend/v3/univer
npm run --prefix frontend/v3/univer build
python frontend/v3/build.py
python scripts/generate_contracts.py
python -m pytest -q
python frontend/v3/check.py
python frontend/v3/test_business_worksheets_browser.py
```

表格组件只在打开研究表时加载，构建产物由本地应用提供。浏览器验收使用 Playwright Chromium，测试项目和数据库由脚本在临时目录创建。日常使用前需在隔离数据副本完成真实样例复核。
