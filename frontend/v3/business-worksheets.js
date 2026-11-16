(() => {
  const sheetPath = id => '/worksheets/' + encodeURIComponent(id);
  const request = (path, body) => window.v4Api.sourceRequest(path, body === undefined ? {} : {
    method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body),
  });
  const clientKey = () => 'workbench:' + crypto.randomUUID();
  let dialog, ui, projectPath, sheet, editor, lease, timer, baseline, retry, asset, busy = false;

  function status(text, error = false) {
    ui.status.textContent = text;
    ui.status.style.color = error ? '#a12b24' : '';
  }

  function actions() {
    ui.edit.disabled = busy || !sheet?.editable || !!lease || !!sheet?.occupied_by;
    for (const button of [ui.save, ui.extend, ui.merge, ui.retest]) button.disabled = busy || !lease || !editor;
    ui.export.disabled = busy || !sheet;
    for (const control of [ui.select, ui.close]) control.disabled = busy;
    ui.new.disabled = busy || !!lease;
  }

  async function run(action) {
    if (busy) return;
    busy = true;
    actions();
    try { await action(); }
    catch (error) { status(error.message, true); }
    finally { busy = false; actions(); }
  }

  function stopEditing() {
    clearInterval(timer);
    lease = null;
    editor?.setEditable(false);
    actions();
  }

  async function release() {
    if (!lease) return;
    const token = lease;
    stopEditing();
    await request(sheetPath(sheet.id) + '/lease/release', { token });
  }

  function loadEditor() {
    if (!asset) {
      const stylesheet = document.createElement('link');
      stylesheet.rel = 'stylesheet';
      stylesheet.href = '/assets/worksheet/editor.css';
      const stylesReady = new Promise((resolve, reject) => {
        stylesheet.onload = resolve;
        stylesheet.onerror = reject;
        document.head.append(stylesheet);
      });
      asset = Promise.all([import('/assets/worksheet/editor.js'), stylesReady]).then(([module]) => module).catch(error => {
        stylesheet.remove();
        asset = null;
        throw new Error('工作表编辑器未能加载，请检查组件构建。', { cause: error });
      });
    }
    return asset;
  }

  async function openSheet(id) {
    const next = await request(sheetPath(id));
    if (editor && sheet?.id === id && next.revision === sheet.revision &&
        JSON.stringify(next.cells) === JSON.stringify(sheet.cells)) {
      sheet = next;
      editor.setEditable(!!lease);
      status(lease ? '正在编辑' : sheet.occupied_by ? sheet.occupied_by + '正在编辑' : '只读');
      return;
    }
    const { mountWorksheet } = await loadEditor();
    editor?.dispose();
    ui.grid.replaceChildren();
    sheet = next;
    retry = null;
    const existing = new Map(sheet.cells.map(cell => [cell.row_id + ':' + cell.point_id, cell]));
    const values = [
      [sheet.name, ...sheet.points.map(() => '')],
      ['检项 / 单位', ...sheet.points.map(point => point.label + '\n' + point.planned_date)],
      ...sheet.rows.map(row => [
        row.label + (row.unit ? ' / ' + row.unit : ''),
        ...sheet.points.map(point => {
          const cell = existing.get(row.id + ':' + point.id);
          if (cell) return cell.raw;
          return point.required_row_ids.includes(row.id) ? '' : '—';
        }),
      ]),
    ];
    editor = mountWorksheet(ui.grid, values, sheet.layout, !!lease, {
      onChange: () => { if (lease && !busy) status('有未保存的结果改动。'); },
      onError: message => status(message, true),
      canEdit: (r, c) => sheet.points[c - 1]?.required_row_ids.includes(sheet.rows[r - 2]?.id),
    });
    baseline = editor.snapshot();
    ui.meta.textContent = [sheet.identity.batch, sheet.identity.study_id, sheet.identity.condition,
      sheet.identity.orientation, '计划版本 ' + sheet.plan_version].filter(Boolean).join(' · ');
    const alternates = sheet.cells.filter(cell => cell.display.result_version_id !== cell.adopted?.result_version_id);
    ui.pending.textContent = alternates.map(cell => {
      const row = sheet.rows.find(item => item.id === cell.row_id);
      const point = sheet.points.find(item => item.id === cell.point_id);
      return row.label + ' ' + point.label + ' 最新记录 ' + cell.display.text +
        '，正式采用 ' + (cell.adopted?.text || '尚无');
    }).join('\n');
    const occupied = sheet.occupied_by ? sheet.occupied_by + '正在编辑' : '只读';
    status(lease ? '正在编辑' : occupied);
  }

  async function reloadList(id) {
    const sheets = await request(projectPath);
    ui.select.replaceChildren(new Option('选择工作表', ''), ...sheets.map(item => new Option(item.name, item.id)));
    if (id) {
      ui.select.value = id;
      await openSheet(id);
    } else {
      status(sheets.length ? '请选择工作表。' : '此项目还没有研究工作表。');
    }
  }

  function points(text, items) {
    return text.split(/[\s,，、]+/).filter(Boolean).map(label => {
      const match = /^(\d+)([MD])$/i.exec(label);
      if (!match) throw new Error('时间点格式示例为 0M、3M 或 14D。');
      return { amount: Number(match[1]), unit: match[2].toUpperCase(), required_items: items };
    });
  }

  async function save() {
    const snapshot = editor.snapshot();
    const changes = [];
    let corrections = 0;
    const existing = new Map(sheet.cells.map(cell => [cell.row_id + ':' + cell.point_id, cell]));
    snapshot.rows.forEach((row, r) => row.forEach((value, c) => {
      if (value === baseline.rows[r][c]) return;
      if (r < 2 || c === 0) throw new Error('请还原标题、日期或检项标签中的改动。');
      const item = sheet.rows[r - 2], point = sheet.points[c - 1];
      if (!point.required_row_ids.includes(item.id)) throw new Error('粘贴包含非应检单元格，请还原该范围。');
      if (!value.trim()) throw new Error('结果不能通过清空撤销，请还原该单元格。');
      const cell = existing.get(item.id + ':' + point.id);
      if (cell) corrections++;
      changes.push({ row_id: item.id, point_id: point.id, value,
        intent: cell ? 'correction' : 'initial', expected_version_id: cell?.adopted?.result_version_id || null });
    }));
    const signature = JSON.stringify(snapshot);
    if (signature === JSON.stringify(baseline)) {
      status('没有待保存的改动。');
      return;
    }
    if (retry?.signature !== signature) {
      const reason = corrections ? prompt('将更正 ' + corrections + ' 个已有结果。请输入本次更正原因。')?.trim() : '';
      if (corrections && !reason) return;
      retry = { signature, command: { idempotency_key: clientKey(), token: lease, revision: sheet.revision,
        reason, changes, layout: snapshot.layout } };
    }
    await request(sheetPath(sheet.id) + '/save', retry.command);
    try {
      await openSheet(sheet.id);
      status('已保存。');
    } catch {
      status('服务端已保存。请关闭并重新打开工作表，以读取最新版本。', true);
      editor?.setEditable(false);
    }
  }

  async function retest() {
    if (JSON.stringify(editor.snapshot()) !== JSON.stringify(baseline)) throw new Error('请先保存表格改动。');
    const range = editor.selection();
    const row = sheet.rows[range?.startRow - 2], point = sheet.points[range?.startColumn - 1];
    if (!row || !point || range.startRow !== range.endRow || range.startColumn !== range.endColumn) {
      throw new Error('请选择一个已有结果的单元格。');
    }
    const existing = sheet.cells.find(cell => cell.row_id === row.id && cell.point_id === point.id);
    if (!existing || !point.required_row_ids.includes(row.id)) throw new Error('此格没有可复测的应检结果。');
    const value = prompt(row.label + ' ' + point.label + ' · 新增复测结果');
    if (!value?.trim()) return;
    const reason = prompt('请输入新增检测说明。');
    if (!reason?.trim()) return;
    await request(sheetPath(sheet.id) + '/save', {
      token: lease, revision: sheet.revision, idempotency_key: clientKey(), reason,
      changes: [{ row_id: row.id, point_id: point.id, value, intent: 'retest',
        expected_version_id: existing.display.result_version_id }],
    });
    await openSheet(sheet.id);
    status('已保存独立复测。正式采用值保持原值。');
  }

  async function edit() {
    const id = sheet.id;
    const result = await request(sheetPath(id) + '/lease', {});
    lease = result.token;
    try { await openSheet(id); }
    catch (error) { await release(); throw error; }
    const token = lease;
    timer = setInterval(async () => {
      try { await request(sheetPath(id) + '/lease/renew', { token }); }
      catch (error) {
        if (token !== lease) return;
        if (error.status === 409) stopEditing();
        status('编辑占用续期失败，尚未保存的内容仍在此页面。' + error.message, true);
      }
    }, 25000);
  }

  function showForm(extend, items) {
    ui.form.hidden = false;
    ui.identity.hidden = extend;
    for (const input of ui.identity.querySelectorAll('input,select')) input.disabled = extend;
    ui.items.replaceChildren(...items.map(item => new Option(item.label, item.item_id || item.id)));
    ui.form.reset();
    ui.form.dataset.extend = String(extend);
    ui.form.querySelector('[name=basis]').required = extend;
    ui.form.querySelector('[name=points]').focus();
  }

  async function submitPlan() {
    const form = new FormData(ui.form);
    const items = form.getAll('items');
    const planned = points(form.get('points'), items);
    const basis = form.get('basis').trim();
    if (ui.form.dataset.extend === 'true') {
      if (JSON.stringify(editor.snapshot()) !== JSON.stringify(baseline)) {
        throw new Error('请先保存表格改动，再延长计划。');
      }
      await request(sheetPath(sheet.id) + '/plan', {
        token: lease, revision: sheet.revision, idempotency_key: clientKey(), points: planned, basis,
      });
      await openSheet(sheet.id);
      status('已保存新计划版本。');
    } else {
      const identity = Object.fromEntries(['batch', 'sample_type', 'presentation', 'study', 'study_id', 'condition', 'orientation']
        .map(key => [key, form.get(key)]));
      const created = await request(projectPath, {
        identity, idempotency_key: clientKey(), name: form.get('name'), start_date: form.get('start_date'),
        items, points: planned, basis,
      });
      await reloadList(created.id);
    }
    ui.form.hidden = true;
  }

  document.addEventListener('DOMContentLoaded', () => {
    if (window.v4Api?.mode !== 'real') return;
    const trigger = document.createElement('button');
    trigger.className = 'hub-button';
    trigger.textContent = '研究工作表';
    document.querySelector('.nav-tabs').append(trigger);
    dialog = document.createElement('dialog');
    dialog.className = 'business-worksheet-dialog';
    dialog.innerHTML = `
      <div class="business-worksheet-controls">
        <strong>研究工作表</strong>
        <select data-ui="select" aria-label="工作表"></select>
        <button type="button" class="hub-button" data-ui="new">新建</button>
        <button type="button" class="hub-button" data-ui="edit">编辑</button>
        <button type="button" class="hub-button hub-primary" data-ui="save">保存</button>
        <button type="button" class="hub-button" data-ui="retest">新增复测</button>
        <button type="button" class="hub-button" data-ui="extend">延长计划</button>
        <button type="button" class="hub-button" data-ui="merge">合并或拆分标题</button>
        <button type="button" class="hub-button" data-ui="export">下载确认表</button>
        <button type="button" class="hub-button" data-ui="close">关闭</button>
      </div>
      <p data-ui="meta"></p><p data-ui="status" role="status" aria-live="polite"></p>
      <p data-ui="pending" class="business-worksheet-pending"></p>
      <form data-ui="form" class="business-worksheet-form" hidden>
        <fieldset data-ui="identity">
          <label>工作表名称<input name="name" required></label>
          <label>批号<input name="batch" required></label>
          <label>材料类别<select name="sample_type"><option>DS</option><option>DP</option><option>placebo</option></select></label>
          <label>规格<input name="presentation" required></label>
          <label>研究类型<select name="study"><option value="long_term">长期</option><option value="accelerated">加速</option><option value="stress">应力</option></select></label>
          <label>方案编号<input name="study_id" required></label>
          <label>条件<input name="condition" required></label>
          <label>放置方向<select name="orientation"><option value="">未指定</option><option>正置</option><option>倒置</option><option>平放</option></select></label>
          <label>开始日期<input name="start_date" type="date" required></label>
        </fieldset>
        <label>时间点<input name="points" placeholder="0M, 3M, 6M" required></label>
        <label>为上述时间点明确选择应检项目<select name="items" data-ui="items" size="4" multiple required></select></label>
        <label>方案依据<input name="basis"></label>
        <button class="hub-button hub-primary">保存计划</button>
        <button type="button" class="hub-button" data-ui="cancel">取消</button>
      </form>
      <div data-ui="grid" class="business-worksheet-grid"></div>`;
    document.body.append(dialog);
    ui = Object.fromEntries([...dialog.querySelectorAll('[data-ui]')].map(el => [el.dataset.ui, el]));
    const canLeave = () => {
      if (!editor) return true;
      try { if (JSON.stringify(editor.snapshot()) === JSON.stringify(baseline)) return true; }
      catch { /* The editor contains unsaved changes. */ }
      return confirm('仍有未保存的改动。确认放弃？');
    };
    trigger.onclick = () => run(async () => {
      dialog.showModal();
      if (!projectRegistry.some(item => item.id === currentProjectKey)) throw new Error('请先进入一个已建立的项目。');
      projectPath = '/projects/' + encodeURIComponent(currentProjectKey) + '/worksheets';
      ui.new.hidden = !window.v4Api.can('confirm_fact');
      ui.export.hidden = !window.v4Api.can('export');
      await reloadList();
    });
    ui.select.onchange = () => run(async () => {
      const id = ui.select.value;
      if (!canLeave()) { ui.select.value = sheet.id; return; }
      await release();
      if (id) await openSheet(id);
      else {
        editor?.dispose(); editor = sheet = null;
        ui.grid.replaceChildren(); ui.meta.textContent = ui.pending.textContent = '';
      }
    });
    ui.edit.onclick = () => run(edit);
    ui.save.onclick = () => run(save);
    ui.retest.onclick = () => run(retest);
    ui.export.onclick = () => run(async () => {
      if (lease && JSON.stringify(editor.snapshot()) !== JSON.stringify(baseline)) {
        throw new Error('请先保存工作表改动，再导出确认表。');
      }
      const params = new URLSearchParams({ revision: sheet.revision, selection_id: sheet.selection_id });
      const result = await request(sheetPath(sheet.id) + '/confirmation?' + params, {});
      const download = document.createElement('a');
      download.href = result.download_url;
      download.download = 'confirmation.xlsx';
      download.click();
      status('已生成确认表。');
    });
    ui.extend.onclick = () => showForm(true, sheet.rows);
    ui.merge.onclick = () => run(() => editor.mergeTitle(!editor.snapshot().layout.title_merge_to));
    ui.new.onclick = () => run(async () => {
      const { config } = await window.v4Api.getConfiguration(currentProjectKey);
      showForm(false, config.items);
    });
    ui.cancel.onclick = () => { ui.form.hidden = true; };
    ui.form.onsubmit = event => { event.preventDefault(); run(submitPlan); };
    ui.close.onclick = () => run(async () => {
      if (!canLeave()) return;
      try { await release(); }
      finally {
        editor?.dispose(); editor = sheet = null;
        ui.grid.replaceChildren();
        ui.meta.textContent = ui.pending.textContent = '';
        ui.form.hidden = true;
        dialog.close();
      }
    });
    dialog.addEventListener('cancel', event => { event.preventDefault(); ui.close.click(); });
  });
})();
