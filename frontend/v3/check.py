from pathlib import Path
import os,hashlib
from playwright.sync_api import sync_playwright,expect
root=Path(__file__).resolve().parents[2];out=root/'outputs/dashboard-v3-check'
out.mkdir(parents=True,exist_ok=True)
with sync_playwright() as p:
    exe=next((Path(os.environ['LOCALAPPDATA'])/'ms-playwright').glob('chromium_headless_shell-*/chrome-headless-shell-win64/chrome-headless-shell.exe'))
    browser=p.chromium.launch(headless=True,executable_path=str(exe));page=browser.new_page(viewport={'width':1440,'height':1000});errors=[]
    page.on('pageerror',lambda e:errors.append(str(e)))
    page.goto((root/'app.html').as_uri(),wait_until='networkidle')
    expect(page.locator('.ai-intake-card')).to_be_visible()
    page.screenshot(path=str(out/'01-intake.png'))
    page.get_by_role('button',name='检测台账',exact=True).click()
    page.wait_for_timeout(150)
    expect(page.locator('#ledgerBannerGrid')).to_contain_text('生产日期')
    expect(page.locator('#ledgerBannerGrid')).to_contain_text('未关联')
    page.screenshot(path=str(out/'02-ledger.png'))
    page.get_by_role('button',name='关联标准',exact=True).click()
    page.get_by_text('登记标准版本',exact=True).click()
    form=page.locator('#v3StandardForm')
    for key,val in [('code','V3-QS'),('version','01'),('source','标准文件'),('limits','SEC 单体 ≥95%')]:form.locator(f'[name="{key}"]').fill(val)
    page.get_by_role('button',name='登记版本',exact=True).click()
    sid=page.evaluate("v3State.standards.find(s=>s.code==='V3-QS').id")
    page.get_by_label('关联质量标准',exact=True).select_option(sid)
    page.get_by_role('button',name='关闭标准管理',exact=True).click()
    expect(page.locator('#ledgerBannerGrid')).to_contain_text('V3-QS')
    page.locator('#btnLedgerSample_ES20250401').click()
    expect(page.locator('#ledgerBannerGrid')).to_contain_text('未关联')
    page.get_by_role('button',name='可比性研究',exact=True).click()
    page.wait_for_timeout(100)
    expect(page.locator('#relMergedTable')).to_be_visible()
    expect(page.locator('#v3OrientationNote')).to_contain_text('6 批 · 检测项目为行')
    expect(page.locator('#v3ReleaseCharts')).not_to_have_attribute('open','')
    page.screenshot(path=str(out/'03-release.png'))
    page.get_by_role('button',name='按批次查看',exact=True).click()
    assert page.locator('#relMergedBody tr').count()==8
    page.locator('#v3ReleaseCharts summary').click()
    page.wait_for_function('Object.keys(relCharts).length === 4')
    assert page.evaluate('Object.keys(relCharts).length')==4
    page.locator('#btnPillarStability').click()
    page.wait_for_timeout(150)
    assert page.locator('#compMergedBody tr').count()==60
    assert page.evaluate("document.getElementById('compMergedTable').getBoundingClientRect().top < document.getElementById('compCanvas_sec_mono').getBoundingClientRect().top")
    page.screenshot(path=str(out/'04-stability.png'))
    page.get_by_role('button',name='确认样品分组',exact=True).click()
    page.locator('#v3GroupSearch').fill('C202401001A')
    page.locator('.v3-group-pick').check()
    page.locator('#v3MoveRole').select_option('post')
    page.get_by_role('button',name='应用分组').click()
    assert page.evaluate("v3Role('C202401001A')")=='post'
    page.get_by_role('button',name='撤回',exact=True).click()
    assert page.evaluate("v3Role('C202401001A')")=='pre'
    page.screenshot(path=str(out/'05-grouping.png'))
    page.get_by_role('button',name='关闭样品分组').click()
    initial=page.evaluate('v3Study().id')
    page.get_by_role('button',name='+ 新建变更研究',exact=True).click()
    page.locator('[name="studyName"]').fill('新工艺研究')
    page.get_by_role('button',name='下一步：选择样品',exact=True).click()
    assert page.evaluate("v3Role('C202401001A')")=='pending'
    page.get_by_role('button',name='关闭样品分组').click()
    expect(page.locator('#compMergedBody')).to_contain_text('请从已分组')
    page.locator('#v3Study').select_option(initial)
    assert page.evaluate("v3Role('C202401001A')")=='pre'
    page.get_by_role('button',name='数据接收',exact=True).click()
    page.locator('#v3FileInput').set_input_files({'name':'sample.xlsx','mimeType':'application/octet-stream','buffer':b'not parsed'})
    expect(page.locator('#v3FileSelection')).to_contain_text('sample.xlsx')
    expect(page.locator('#v3FileSelection')).to_contain_text('5 项待处理')
    page.get_by_role('button',name='可比性研究',exact=True).click()
    page.get_by_role('button',name='确认样品分组',exact=True).click()
    page.get_by_text('登记待分组样品',exact=True).click()
    page.locator('#v3GroupDialog [name="batch"]').fill('NEW-01')
    page.locator('#v3GroupDialog [name="spec"]').fill('1mg (0.5ml)')
    page.get_by_role('button',name='加入待分组',exact=True).click()
    assert page.evaluate("v3Role('NEW-01')")=='pending'
    page.get_by_role('button',name='关闭样品分组').click()
    page.reload(wait_until='networkidle')
    assert page.evaluate("v3Role('NEW-01')")=='pending'
    # All orientations: 6 samples keep columns, >8 rows, manual override wins.
    page.get_by_role('button',name='可比性研究',exact=True).click()
    page.evaluate("v3Orientation='batches';renderReleaseMergedTable(['A','B','C','D'],['E','F','G','H','I'])")
    expect(page.locator('#v3OrientationNote')).to_contain_text('批次为行')
    page.evaluate("v3Orientation='items';renderReleaseMergedTable(['A','B','C','D'],['E','F','G','H','I'])")
    expect(page.locator('#v3OrientationNote')).to_contain_text('检测项目为行')
    page.evaluate('renderReleasePillar()')
    page.set_viewport_size({'width':829,'height':748})
    page.wait_for_timeout(350)
    page.screenshot(path=str(out/'06-829px.png'))
    assert page.evaluate('document.documentElement.scrollWidth<=innerWidth')
    page.set_viewport_size({'width':390,'height':844})
    assert page.evaluate('document.documentElement.scrollWidth<=innerWidth')
    # Network-independent table rendering.
    off=browser.new_page();off.route('**/chart.umd.min.js',lambda r:r.abort());off.on('pageerror',lambda e:errors.append(str(e)))
    off.goto((root/'app.html').as_uri(),wait_until='networkidle')
    off.get_by_role('button',name='可比性研究',exact=True).click();expect(off.locator('#relMergedBody tr')).to_have_count(14)
    # Exercise the actual V5.6 conflict component with synthetic source members.
    page.evaluate("""() => {
        let node=document.getElementById('v5Form')||document.createElement('div');
        node.id='v5Form';document.body.append(node);
        const ui=v5Review; ui.busy=false;ui.sheet='Synthetic';
        ui.preview={candidates:[{id:'synthetic',location:{row:2,column:4},
            raw_fields:{test_item:'Synthetic assay',time_point:'0M',result:'2.7'},
            interpreted_fields:{time_point:'0M',result:'2.7'},corrections:{}}]};
        window.conflictTask={id:'method-task',fields:['method'],members:[{candidate_id:'synthetic'}],
            detail:'Synthetic opposing labels',proposal:{identity_options:{chinese:'R CE-SDS',english:'NR CE-SDS'}}};
        ui.previewScope=(scope)=>{window.capturedConflictScope=scope;};
        ui.identityConflict(window.conflictTask);
    }""")
    conflict=page.locator('#v5ActiveForm')
    expect(conflict.locator('[name="identityChoice"]')).to_have_value('')
    expect(conflict.locator('.btn-primary')).to_be_disabled()
    conflict.locator('[data-quick-identity="chinese"]').click()
    expect(conflict.locator('[name="identityChoice"]')).to_have_value('chinese')
    expect(conflict.locator('[name="identityValue"]')).to_have_value('R CE-SDS')
    conflict.locator('[data-quick-identity="english"]').click()
    expect(conflict.locator('[name="identityValue"]')).to_have_value('NR CE-SDS')
    assert page.evaluate('window.capturedConflictScope===undefined')
    conflict.locator('.btn-primary').click()
    assert page.evaluate('window.capturedConflictScope') == {
        'section':'Synthetic','task_ids':['method-task'],'operation':'manual','fields':{'method':'NR CE-SDS'}}
    assert not errors,errors
    # Pin the accepted 215ae6d template, ignoring checkout line endings and trailing whitespace.
    assert hashlib.sha256(('\n'.join(line.rstrip() for line in (root/'frontend/v3/template.html').read_text(encoding='utf-8').splitlines()).rstrip()+'\n').encode('utf-8')).hexdigest()=='b39c6ad73c266701e27d06733e718f20e95de5b40ce3dc7e77d08bdbe85a8569'
    browser.close();print('PASS: baseline unchanged, original layout retained, standards, study grouping/undo, upload pending, 6/9 sample orientation, full stability table above charts, persistence, desktop/mobile, offline tables; no JavaScript errors.')
