# Stability Workbench — Design System (`DESIGN.md`)

> Desktop-only (1440–1920px, min 1280px) Pharmaceutical Stability SaaS UI specification for `frontend/workbench/`.
> Zero new npm packages: built strictly on `naive-ui`, CSS custom properties, and inline 16px/20px SVGs.

## 1. Design Tokens

```yaml
viewport:
  mode: desktop_only
  target_width: 1440px..1920px
  min_width: 1280px
  sidebar_width: 240px
  content_max_width: 1640px
  mobile_media_queries: forbidden

colors:
  canvas: "#f6f8fb"           # Main workspace background
  surface: "#ffffff"          # Primary card & sidebar surface
  surface_subtle: "#f8fafc"   # Table headers, evidence boxes, secondary strips
  surface_hover: "#f1f5f9"    # Row & nav hover state
  border: "#e2e8f0"           # Hairline 1px card & divider border
  border_strong: "#cbd5e1"    # Form controls & emphasized boundaries
  text_primary: "#0f172a"     # Headings, values, active labels
  text_secondary: "#475569"   # Descriptions, table metadata
  text_muted: "#64748b"       # Hints, timestamps, empty states
  brand: "#1e5aa8"            # Clinical cobalt (replaces legacy #153532/#22645f)
  brand_hover: "#17488a"
  brand_pressed: "#12396e"
  brand_soft: "#eff5fc"       # Active nav fill, project badge, focused pill
  brand_ring: "rgba(30, 90, 168, 0.18)"
  success: { fg: "#15803d", bg: "#f0fdf4", border: "#bbf7d0" }
  warning: { fg: "#b45309", bg: "#fffbeb", border: "#fde68a" }
  danger:  { fg: "#dc2626", bg: "#fef2f2", border: "#fecaca" }
  info:    { fg: "#1e5aa8", bg: "#eff5fc", border: "#bfdbfe" }

typography:
  font_sans: '"Inter", "Segoe UI", "PingFang SC", "Microsoft YaHei", sans-serif'
  font_mono: '"Cascadia Code", "JetBrains Mono", Consolas, monospace'
  numeric_variant: tabular-nums
  scale:
    page_title: "26px / 1.3 / 700 / -0.02em"
    section_title: "18px / 1.4 / 600"
    card_title: "15.5px / 1.4 / 600"
    body: "14.5px / 1.6 / 400"
    caption: "13px / 1.45 / 500"

elevation_and_radius:
  radius_sm: 6px    # Inputs, badges, inline buttons
  radius_md: 8px    # Cards, alerts, evidence blocks
  radius_lg: 12px   # Primary panels, upload dropzone
  shadow_card: "0 1px 2px rgba(15, 23, 42, 0.04)"
  shadow_raised: "0 4px 12px rgba(15, 23, 42, 0.06)"

spacing:
  page_padding: "24px 36px 40px"
  card_padding: "20px 24px"
  section_gap: 20px
  control_height: 36px
```

## 2. Shell, Global Context & Multi-Domain Standards

- **Left Sidebar (`240px`, fixed height `100vh`)**: Pure `#ffffff` background with `1px solid #e2e8f0` right border.
  - **Brand Header**: Clean `20×20` inline SVG lab-flask mark in `#eff5fc` rounded tile + `稳研工作台` title + subtitle.
  - **Action-Oriented Pipeline Nav**: Grouped into `日常作业流` (`工作台`, `导入文件`, `质量标准与 COA`, `新建研究表`) and `台账与交付` (`项目数据`, `趋势与比较`, `生成与历史`), plus secondary `扫描 PDF 识别 (OCR)` link. Preserves all Playwright-tested exact link labels.
- **Sticky Global Context Bar (`GlobalContextBar.vue`)**: Top of right workspace frame (`54px` height):
  - **Domain Switcher (`domainRegistry.ts`)**: `稳定性研究与放行` (active) + `影响因素试验` / `水系统监测` / `空气系统监测` (planned extensible slots).
  - **In-Place Project Switcher**: Filterable select grouped by `置顶常用` / `最近访问` / `全部项目`, star-pin toggle, and secondary context breadcrumbs (Batch / Worksheet).
- **Unified Post-Action Handoff (`ActionNextStepBanner.vue`)**: Every major write/upload/save renders an inline clinical-green receipt card with numbered `1/2/3` next-step links that carry full project/batch/sample/sheet context downstream.
- **Home Workbench (`HomeWorkbench.vue`)**: 3-zone desktop command center (Top: 4-Stage Workflow Launchpad; Bottom-Left: Live Pending & Resume Queue via `useTodoAggregator.ts`; Bottom-Right: Pinned/Recent Projects + Recent Deliveries). Full 300+ project catalog lives in `ProjectCatalogView.vue` (`/projects`).
- **Page Header**: Full-width clean header strip separated by `1px solid #e2e8f0`, `26px` title, subtitle/metadata inline.

## 3. Core Workspace Layout Standards

- **Import Upload Page (`ImportWorkspace.vue`, `!preview`)**: Desktop 2-column grid `minmax(0, 1.35fr) minmax(380px, 0.95fr)` (`gap: 20px`):
  - **Left Column (Primary Action)**: Prominent dashed dropzone/file picker (`#f8fafc` + `#cbd5e1`), project selector + role select in a clean 2-column row, collapsible `补充原件未注明的身份` / `新建项目` disclosures, and clear primary action bar.
  - **Right Column (Context & History)**: Historical import previews list, `StudyWorkbook` round-trip upload card, and quick navigation links.
- **Import Review Page (`ImportWorkspace.vue`, `preview`)**:
  - **Top 5-Step Horizontal Stepper**: `1. 选择工作表 → 2. 接收候选 → 3. 核对争议 → 4. 写入入库 → 5. 同步/查看结果` with clear done/active/pending visual states.
  - **Action Hierarchy**: Primary sheet selector + progress pill + primary CTA (`接收候选` / `核对无误，写入本工作表`) cleanly separated from secondary links (`下载原件`, `打开项目数据`, `打开生成与交付`).
  - **Task Cards (`ImportSourceReview.vue`) & Drawer (`CandidateSourceEditor.vue`)**: Clear 4-zone structure (header badges & coordinate pill → monospace `.source-evidence-box` with `<del>` strike-through → exception/guidance alerts → quick choices, manual input & reason footer).
- **Unified Forms & Tables (All High-Frequency Views)**:
  - Style all native `<input>`, `<select>`, `<textarea>` to `36px` height, `1px solid #cbd5e1`, `6px` radius, `#1e5aa8` focus ring.
  - Data tables use `#f8fafc` sticky headers, `10px 14px` comfortable row padding, `tabular-nums`, subtle row hover `#f8fafc`, and `NTag` status pills.

## 4. Do's & Don'ts

- **Do** keep all Playwright-tested text labels, button names, `aria-label`s, IDs (`#sourceReviewPanel`, `#project-results-view`), and class hooks (`.task-card`, `.candidate-drawer`, `.sheet-select-row`, `.row-actions`, `.orig-card`, `.source-evidence-box`) intact.
- **Do** remove legacy `@media (max-width: ...)` mobile branches and obsolete `#153532` / `#22645f` dark-green hex values.
- **Do** keep every change strictly inside `frontend/workbench/src/` presentation/CSS/layout.
- **Don't** touch any backend Python code, API endpoints, state-machine logic, or compliance confirmation flows.
- **Don't** add new npm packages or heavy gradients/shadows that compete with scientific data legibility.
