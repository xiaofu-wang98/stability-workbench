// V3 follow-up: consolidate context, progressive disclosure, and explicit comparison setup.
function v3RefineFourComments(){
 document.querySelector('.top-navbar')?.remove();
 const context=document.querySelector('.project-context-bar');context.classList.add('v3-context-refined');
 const main=context.querySelector('.project-main-box');
 main.querySelector('.hub-primary')?.remove();main.querySelector('.hub-divider')?.remove();
 main.querySelector('.v3-switch .hub-eyebrow').textContent='分析检测工作台 / 当前项目';
 const favorite=document.getElementById('workspaceFavorite');const actions=document.createElement('div');actions.className='v3-context-actions';actions.append(favorite,context.querySelector(':scope > .hub-button'));context.append(actions);
 document.querySelector('#tab-intake > .card:first-child > .card-header')?.remove();
 const recordCard=document.querySelector('#tab-intake > .card:nth-child(2)');
 if(recordCard){
  recordCard.id='v3RecentRecords';const header=recordCard.querySelector('.card-header');
  [...header.children].filter(e=>!e.classList.contains('card-title')).forEach(e=>e.remove());
  const body=recordCard.querySelector('tbody');body.id='v3RecentRecordRows';
  const more=document.createElement('button');more.className='v3-text-button';more.id='v3ExpandRecords';more.setAttribute('aria-expanded','false');more.setAttribute('aria-controls',body.id);
  function refresh(open){[...body.rows].forEach((row,i)=>row.hidden=i>0&&!open);more.textContent=open?'收起入库记录 ↑':`展开入库记录（其余 ${Math.max(0,body.rows.length-1)} 条）↓`;more.setAttribute('aria-expanded',String(open));more.hidden=body.rows.length<2;}
  more.onclick=()=>refresh(more.getAttribute('aria-expanded')!=='true');header.append(more);refresh(false);
 }
 const ledgerCard=document.getElementById('ledgerDataTable').closest('.card'),charts=document.getElementById('v3LedgerCharts');
 ledgerCard.querySelector('.card-header > span')?.remove();
 const sectionLabel=charts.previousElementSibling;if(sectionLabel?.classList.contains('v3-label'))sectionLabel.textContent='检测结果';
 charts.before(ledgerCard);charts.insertAdjacentHTML('beforebegin','<div class="v3-label">趋势图</div>');
 const toolbar=document.querySelector('#tab-comparability > .v3-toolbar');toolbar.className='v3-comparison-setup';
 const selector=document.getElementById('v3Study'),pending=document.getElementById('v3PendingCount');
 toolbar.replaceChildren();
 const plan=document.createElement('div');plan.className='v3-plan-row';
 const label=document.createElement('label');label.htmlFor='v3Study';label.textContent='变更研究';plan.append(label,selector);
 const create=document.createElement('button');create.className='v3-text-button';create.textContent='+ 新建变更研究';create.onclick=()=>document.getElementById('v3NewStudyDialog').showModal();plan.append(create);
 const tools=document.createElement('div');tools.className='v3-setup-actions';tools.innerHTML='<div><strong>确认哪些样品参与比较</strong><p>在当前研究中持续补充样品与数据，新增样品先确认分组。</p></div><button class="hub-button hub-primary" onclick="v3OpenGroups()">确认样品分组</button><button class="v3-text-button" onclick="v3OpenStandards(\'study\')">查看评价标准</button>';
 tools.firstElementChild.append(pending);toolbar.append(plan,tools);
 toolbar.before(document.querySelector('#tab-comparability > .mode-segment-bar'));
 const config=actions.querySelector('.hub-button');config.textContent='管理当前项目';config.onclick=()=>{document.getElementById('projectCenter').close();openProjectSettings();};document.querySelector('#projectCenter .hub-pagination').append(config);
 const report=document.querySelector('#tab-intake .ai-intake-card');report.insertAdjacentHTML('afterbegin','<h3 class="v3-preliminary-title">初步解析结果</h3>');
 const dialog=document.getElementById('v3NewStudyDialog');dialog.querySelector('h2').textContent='新建变更研究';
 const name=dialog.querySelector('[name="studyName"]');name.placeholder='例如：500L → 6000L 工艺放大研究';name.previousSibling.textContent='研究名称 / 编号';
 dialog.querySelector('.v3-note').textContent='每项研究独立保存样品分组和评价标准。创建后可持续补充样品，不影响其他研究。';
 dialog.querySelector('button.hub-primary').textContent='下一步：选择样品';
 document.querySelector('#v3GroupDialog .hub-dialog-head p').textContent='为当前研究选择变更前、变更后样品；不确定的保留在“待分组”。';
}
document.addEventListener('DOMContentLoaded',v3RefineFourComments);
