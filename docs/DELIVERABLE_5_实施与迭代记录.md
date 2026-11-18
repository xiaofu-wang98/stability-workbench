# 稳研工作台交付物五：实施与迭代记录

## 1. 实施范围与工程概况
- **工作区路径**：`D:\stability-workbench`
- **代码变动统计**：修改文件 44 个，累计新增与重构代码 `+10,263` 行，精简删除 `-1,883` 行。
- **新增核心业务模块**：
  - `src/stability_hub/assay_catalog.py`（检验项目标准化签名与别名字典）
  - `src/stability_hub/coa.py`（COA 放行结果管理与 0M 快照提取）
  - `src/stability_hub/specification_limits.py`（标准开闭区间解析与超限判定）
  - `src/stability_hub/stability_matrix_report.py`（常熟受控版式生成与多批次堆叠）
  - `src/stability_hub/worksheet_roundtrip.py`（填报 Excel 增量比对与子集回传）
  - `src/stability_hub/worksheet_seed.py`（历史 Excel 确认后同一事务批量建表）
- **测试矩阵**：
  - 全量后端自动化用例达 455 项（包含单元测试、集成测试、契约视图与隔离迁移测试）。
  - 执行指令：`.venv\Scripts\python.exe -m pytest -q`，**455 passed in 115s (100% 成功)**。
- **前端工程**：
  - 前端位于 `frontend/workbench`，采用 Vue 3 + Naive UI + TypeScript + Vite 架构。
  - 类型检查指令：`npm run typecheck`（`vue-tsc --noEmit`），**0 错误**。
  - 生产构建指令：`npm run build`，成功产出生产包于 `frontend/workbench/dist`。

---

## 2. 数据库迁移历程 (Schema Migrations)
系统严格遵循“小步单事务、不可篡改历史迁移”的原则：
1. **v18 迁移**：
   - 落地 `workflow_invalidated_versions` 作废事件审计表。
   - 落地 `project_aliases` 项目别名映射表。
2. **v19 迁移**：
   - 落地 `business_worksheets.deleted_at` 与 `sample_entities.deleted_at` 软删除字段。
   - 落地 `workflow_projects.purpose` 字段（约束为 `'formal'` 或 `'sandbox'`）。
3. **v20 迁移**：
   - 落地 `current_specifications` 表，显式维护 `(project, material_scope) -> specification_id` 的当前有效版本关系。

---

## 3. 本地验证与复现执行命令手册

### 3.1 环境初始化
```cmd
:: 1. 进入工作目录
cd /d D:\stability-workbench

:: 2. 初始化 Python 虚拟环境与依赖
python -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements.lock
.venv\Scripts\python.exe -m pip install -e . --no-deps

:: 3. 构建前端工程 (如已有 dist 可跳过)
cd frontend\workbench
npm ci
npm run build
cd ..\..
.venv\Scripts\python.exe frontend\v3\build.py
```

### 3.2 运行自动化回归测试
```cmd
:: 执行全量测试 (455 项)
.venv\Scripts\python.exe -m pytest -q
```

### 3.3 启动本地应用
```cmd
:: 启动本地服务并自动打开浏览器 (默认端口 8772，数据保存在 data/ 目录下)
.venv\Scripts\python.exe start.py --port 8772

:: 或使用隔离测试目录启动
.venv\Scripts\python.exe start.py --home outputs/my-test --port 8872
```

---

## 4. 回退与容灾策略 (Rollback & Disaster Recovery)
1. **数据库单事务保护**：
   - 所有迁移和批量导入均处于显式事务 `BEGIN ... COMMIT` 中，任意步骤失败自动执行 `ROLLBACK`，避免产生脏数据或半成品表。
2. **冷备与快照恢复**：
   - `scripts/backup.py` 提供备份与完整性校验能力。
   - 发生严重业务误操作时，可直接指定 `--home` 参数切换到最新通过校验的冷备份目录重新启动应用，不影响原有历史归档。
