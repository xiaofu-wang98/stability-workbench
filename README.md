# 稳研工作台（Stability Workbench）0.1.0 开发版

稳研工作台复用 Stability Data Hub 0.2.0 的数据和解析逻辑。首页可直接进入导入文件、新建研究表、查看项目数据、趋势和交付。研究表支持粘贴结果和单表独占编辑，结果也可从已核对的 Excel 导入。Word 方案可识别矩阵或用于人工建表，交付页可生成确认表和 Word 报告。

Python/FastAPI 提供 Office 解析、业务规则和接口，SQLite 保存来源、研究计划、正式结果与版本。主界面在 `frontend/workbench/`，使用 Vue 3、TypeScript 和 Vite。旧版来源核对页面从 `frontend/v3/` 构建，可在 `/legacy` 访问。代码审查从 [架构说明](ARCHITECTURE.md) 和 [AI 审查指南](AI_REVIEW_GUIDE.md) 开始。

## 当前进度

- 已在隔离库用真实 Word 验证原液与制剂方案建表、标准原文关联、样品登记，以及 COA 在线录入与 Word/Excel 回传。
- 已联验 0M 从正确样品引用放行值、COA 更新后的复核提示、改引新版和旧 Word 报告保留；亦覆盖模板回传冲突、检项停检和正式结果留痕作废。
- 整体 MVP 仍在验收。部分历史 Excel 的方法与子结果冲突需人工确认，原液与制剂标准仍有条款适用性待逐条核对，旧日常库的项目归属与恢复切换仍待演练。实际原件与详细验收日志在本机保存。
- 2026-11-16 审查快照准备期间，全量后端测试 446 项通过，前端类型检查和构建通过。真实文件专项验收在独立库运行。

在 Windows 安装 Python 3.12 和 Node.js 24，从仓库根目录依次运行 `setup.cmd` 和 `start.cmd`。安装脚本通过 `requirements.lock` 和两份 npm 锁文件安装依赖，并构建工作台及旧版页面。工作台默认地址为 `http://127.0.0.1:8772/`。独立验收可使用 `start.cmd --home "隔离数据目录" --no-browser`。

当前数据库使用 schema 16，新增样品身份、COA 版本、研究结果位置和标准子结果引用。验收旧库升级时先备份数据库与原件，再用 `start.cmd --home "副本目录"` 打开。详见 [研究工作表开发记录](docs/workbench-0.1.0-dev.md)。

## Stability Data Hub 0.2.0 核心

0.2.0 改善陌生方案编号、汇报语义与中英文业务备注的识别；备注按真实检项和时间点定位，无法确定的范围仍保留原文供核对。新增查询索引并在事务内复用撤销集合。普通改单值优先显示结果、单位和说明，其余字段按需展开。未引入AI。详见 [0.2.0 发布说明](docs/release-0.2.0.md)。

历史分支 `codex/0.2.0` 从 0.1.5 提交 `a105c2a` 开始，当时数据库升级至 schema 9。开发验收均使用隔离数据目录。

本项目承接V5.6，本轮V5.7功能升级后独立整理为0.1.1，使用全新Git仓库。保留既有页面布局，新增人工工作副本兜底模式，并优化重复刷新。

0.1.1 增加定性双语文字的“完整保留未划线内容”预览确认，保留数值、子结果、方法冲突及其他来源问题的独立核对。保存后按来源索引统计修改，复用未变化候选映射及事务内历史查询；规则、身份关联或候选变化时重新核验。详见 [0.1.1 发布记录](docs/release-0.1.1.md)。

0.1.2 接入真实结果矩阵、正式采用值趋势和同范围导出。展示接口使用SQLite只读连接，不重新解析来源，不修改已确认数据。支持离线图表、批次/条件筛选、特殊值断线及图点溯源。详见 [0.1.2 发布记录](docs/release-0.1.2.md)。

0.1.3 恢复直接点击的批次/条件按钮、默认展开的完整结果表和多检项趋势卡片。保留只读真实数据接线及双状态；原表核对收在次级入口。详见 [0.1.3 发布记录](docs/release-0.1.3.md)。

0.1.4 向全部真实项目开放可比研究与报告中心。研究分组和研究内显式对齐单独留痕，报告从真实结果生成 Word／Excel／PNG；纵轴统一采用按检测项类别确定的概览范围，可切换细节或手动范围。详见 [0.1.4 发布记录](docs/release-0.1.4.md)。

0.1.5 将可比性页改为“选批次、选条件和检项即可成图”，支持未保存研究的只读对比、按需批量分组与显式对齐、当前范围直接导出、看图后保存研究。可比性页趋势优先，完整结果按需读取；检测台账继续表格优先。详见 [0.1.5 发布记录](docs/release-0.1.5.md)。

## 启动

扫描 PDF 识别使用独立的 Windows Python 环境。运行 `setup-ocr.cmd` 后，从「导入文件 → 识别扫描 PDF」归档原件并开始识别。首次运行需要下载 PaddleOCR 模型，识别在本机执行。识别结果可按页对照原件核对。

Windows / Python 3.12：首次安装双击setup.cmd；日常使用双击start.cmd，打开 http://127.0.0.1:8772/ 。当前目录已安装独立.venv，启动不依赖旧项目目录。

默认业务目录是data/，首次启动没有项目或业务数据。旧V5.6目录及其业务库未迁移、未删除。要携带既有数据，先用scripts/backup.py建立独立副本并核对报告，再用start.cmd --home 副本目录启动；不要覆盖旧库。

在项目目录运行 `.venv\Scripts\python.exe scripts\backup.py --source "原数据目录" --target "新数据目录"`，目标目录须尚未创建。查看目标内 `migration.json`，确认数据库完整性、业务行和文件校验通过后，可用 `start.cmd --home "新数据目录"` 启动。备份会更新文件在新数据目录中的存储路径，迁移到其他位置时可再运行备份命令。Word 方案随数据库保存，确认表、Word 交付和扫描原件随目录复制。备份时仍在识别的扫描件会标为中断，保留已完整写出的识别页。

命令选项：--port 8772、--home 路径、--no-browser、--role technician/senior_technician/project_lead/admin。默认使用 admin 对应的本地操作能力，只监听 127.0.0.1。端口占用时直接退出。

## 人工兜底编辑

在Excel来源的检测台账中点击“人工兜底编辑”。

1. 输入显示范围，如A1:L30；点击/Shift+点击选择矩形。直接改文字，粘贴制表符分隔的数据；可清空、不采用所选、合并、拆分和撤销本地操作。
2. “将所选来源加入结果清单”会沿用已识别身份和上传时公共字段。无法识别的来源也可从原件非空格手动建立结果。
3. 下方结果清单决定实际生成的草稿。可修改检项、时间点、结果、单位、方法及其他身份；“拆成另一结果”保持来源关系，新增结果和时间点必须分别填写。合并多源为一条结果时，在来源格列列出全部原格，保留一条明确结果，删除多余清单行。
4. 写处理依据，先预览修改前后、排除数量、原格问题与各条结果，再勾选核对确认后保存。随后仍通过正常整表预检和入库；历史冲突仍走既有复核流程。

工作副本保存为审计事件，不改冻结Excel文件。清空不移动原坐标；有活跃草稿时表示明确不采用。合并把所选文本集中到左上格；拆分保留左上文本，额外值须人工填写。副本的排版变化不会自动成为数值规则，最终以“拟入库结果”清单为准。

已正式提交的工作表禁止兜底重建。其他上传者的人工重建需要高级复核能力。撤销保存只允许尚无后续保存且未入库的编辑，避免覆盖其他处理。尚未明确的中英文身份与缺失身份继续阻断，不会默认代选。

## 文件与规则

- docs/脚本职责.md：每个后端模块、前端文件、启动与维护脚本的职责。
- docs/主要规则.md：原件、身份、人工编辑、权限、事务、入库、导出和缓存规则。
- docs/验收记录.md：本次验证、性能口径及功能边界。
- contracts/v1/：当前接口Schema、OpenAPI与合成示例；不是业务数据库。
- tests/：必要业务回归；frontend/v3/test_*为隔离浏览器验收，不参与日常启动。

没有带入旧Git历史、历史HTML、旧版本启动器、验收重置工具、真实业务库、原始业务Word/Excel、旧输出、密钥或旧依赖环境。frontend/v3中的v3/v4/v5命名是仍被当前页面调用的源码模块，不是待删除的历史副本；src中的兼容模块也仍参与Office输入、核对和导出。

## 开发验证

```powershell
.venv\Scripts\python.exe -m pip install -e ".[dev]" playwright
.venv\Scripts\python.exe -m pytest -q
npm run --prefix frontend/workbench typecheck
npm run --prefix frontend/workbench build
```

修改旧版界面、接口契约或人工核对逻辑时，可在激活的环境中运行相关检查。

```powershell
python frontend/v3/build.py
python scripts/generate_contracts.py
python frontend/v3/check.py
python -m ruff check src/stability_hub/ tests/test_manual_sheet.py start.py
python -m pytest -q
python frontend/v3/test_manual_sheet_browser.py
python frontend/v3/test_v020_edit_browser.py
python frontend/v3/test_v5_intake_e2e.py
```

浏览器验收需要先安装Chromium：python -m playwright install chromium。测试只使用临时目录或outputs中的隔离副本。data、outputs、.venv及缓存都不进入Git。
