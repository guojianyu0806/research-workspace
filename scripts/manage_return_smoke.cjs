/* Offline integration regression for manage → reader → original text context.
 * Every request is fulfilled with synthetic data; no running service, model,
 * credentials, account changes, or persisted user data is used. */
'use strict';
const fs = require('node:fs');
const path = require('node:path');
const crypto = require('node:crypto');
const assert = require('node:assert/strict');
const {chromium} = require(process.env.PLAYWRIGHT_MODULE || 'C:/Users/g2806/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright');
const root = path.resolve(__dirname,'..');
const origin = 'http://127.0.0.1:8765';
const output = path.join(root,'artifacts/browser',`manage-return-${new Date().toISOString().replace(/[:.]/g,'-')}-${crypto.randomBytes(3).toString('hex')}`);
fs.mkdirSync(output,{recursive:true});
const names = ['notices.js','styles.css','shell.css','manage.css','shell.js','manage.js'];
const assets = new Map(names.map(name=>[name,fs.readFileSync(path.join(root,'research_workspace/static',name))]));
const report = {output,startedAt:new Date().toISOString(),mode:'offline actual manage/shell with synthetic reader bridge',checks:[],pageErrors:[],unexpectedRequests:[],sha256:{},limitations:['The reader bridge is a protocol fixture. Actual PDF rendering and model quality are outside this test.']};
for(const [name,content] of assets) report.sha256[name]=crypto.createHash('sha256').update(content).digest('hex');
report.sha256['scripts/manage_return_smoke.cjs']=crypto.createHash('sha256').update(fs.readFileSync(__filename)).digest('hex');
const documents = ['alpha','beta'].map(name=>({id:`fixture-${name}`,title:`Synthetic ${name}`,filename:`${name}.pdf`,page_count:3,authors:['Fixture Author'],created_at:'2026-10-13T00:00:00Z',available_run:{id:`run-${name}`,version:1,status:'completed'},current_run:{id:`run-${name}`,version:1,status:'completed'}}));
const harness = `<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">${names.filter(name=>name.endsWith('.css')).map(name=>`<link rel="stylesheet" href="/static/${name}">`).join('')}<style>body{height:100vh;display:flex;flex-direction:column}.module-navigation{flex-shrink:0}#workspace{padding:24px}.mobile-navigation{display:none}</style></head><body>
<header><button id="modelSettingsButton">模型设置</button><button id="accountButton">工作空间</button></header>
<nav class="module-navigation">${['reader','manage','research','discovery','graph','data'].map(name=>`<button data-module="${name}">${name}</button>`).join('')}</nav>
<main id="workspace"><button id="readerReturnButton" hidden>返回文字核对</button><p id="fixtureEvidence">Synthetic reader</p></main><nav class="mobile-navigation"></nav><section id="moduleRoot" class="module-root" hidden></section>
<dialog id="accountDialog"><button id="closeAccount">关闭</button><p id="accountDescription"></p><form id="accountForm"></form><p id="accountStatus"></p><button id="logoutButton">退出</button></dialog>
<script src="/static/notices.js"></script><script src="/static/manage.js"></script><script>
window.fixture={mounts:0,destroys:0,suspends:0,resumes:0,requests:[],evidence:[],mode:'immediate',release:null};
let navigation=0;
const actualMount=ResearchManage.mount;
ResearchManage.mount=(root,options)=>{fixture.mounts++;fixture.options=options;const controller=actualMount(root,options);for(const name of ['destroy','suspend','resume']){const original=controller[name];controller[name]=(...args)=>{fixture[name==='destroy'?'destroys':name==='suspend'?'suspends':'resumes']++;return original?.(...args);};}return controller;};
window.ResearchWorkspace={
 api:async(url,init)=>{if(init?.method && init.method!=='GET')throw Error('Fixture forbids writes');fixture.requests.push(url);const response=await fetch(url);if(!response.ok)throw Error('Synthetic request failed');return response.json();},
 invalidateEvidenceNavigation(){navigation++;},
 async onEvidence(evidence){const request=++navigation;if(fixture.mode==='failure')throw Error('Synthetic evidence failure');if(fixture.mode==='deferred')await new Promise(resolve=>{fixture.release=resolve;});if(request!==navigation)return;fixture.evidence.push(evidence);document.querySelector('#fixtureEvidence').textContent=evidence.document_id+' / page '+evidence.page+' / '+evidence.block_id;window.dispatchEvent(new CustomEvent('research:reader'));}
};
function placeholder(root){root.textContent='Synthetic other module';return {ready:Promise.resolve(),destroy(){}};}
window.ResearchTasks={mount:placeholder};window.ResearchExplore={mountDiscovery:placeholder,mountGraph:placeholder};window.ResearchData={mount:placeholder};window.ResearchModelSettings={open(){}};
</script><script src="/static/shell.js"></script></body></html>`;
function check(name,passed,detail){report.checks.push({name,passed:Boolean(passed),...(detail===undefined?{}:{detail})});assert.ok(passed,name+(detail===undefined?'':': '+JSON.stringify(detail)));}
async function main(){
 const browser=await chromium.launch({headless:true,executablePath:process.env.BROWSER_EXECUTABLE || 'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe'});
 const context=await browser.newContext({viewport:{width:1024,height:800},reducedMotion:'reduce'});
 const page=await context.newPage();page.setDefaultTimeout(10000);page.on('pageerror',error=>report.pageErrors.push(String(error)));
 await context.route('**/*',async route=>{
  const request=route.request(),url=new URL(request.url()),p=url.pathname;
  if(url.origin!==origin || !['GET','HEAD'].includes(request.method())){report.unexpectedRequests.push(request.url());return route.abort();}
  if(p==='/')return route.fulfill({status:200,contentType:'text/html; charset=utf-8',body:harness});
  if(p.startsWith('/static/')){const name=path.basename(p);if(assets.has(name))return route.fulfill({status:200,contentType:name.endsWith('.css')?'text/css; charset=utf-8':'text/javascript; charset=utf-8',body:assets.get(name)});}
  let result;
  if(p==='/api/account')result={user:{mode:'local'}};
  else if(p==='/api/documents')result={documents};
  else if(p.endsWith('/versions'))result={family:{documents:[]},candidates:[]};
  else if(p.endsWith('/structure'))result={headings:[],references:[],citations:[],limitations:[]};
  else {
   const match=p.match(/^\/api\/documents\/(fixture-(?:alpha|beta))\/pages\/(\d+)(\/image)?$/);
   if(match){const [,documentId,number,image]=match;
    if(image)return route.fulfill({status:200,contentType:'image/svg+xml',body:'<svg xmlns="http://www.w3.org/2000/svg" width="600" height="800"><rect width="600" height="800" fill="white"/><text x="50" y="50">Synthetic PDF</text></svg>'});
    result={document_id:documentId,run_id:documentId.replace('fixture','run'),page:Number(number),status:'completed',blocks:Array.from({length:12},(_,index)=>({id:`${documentId}-${number}-${index}`,ordinal:index,page:Number(number),bbox:[.1,.12,.85,.3],text:`Original source paragraph ${index+1} for page ${number}.\nThe selected original text must survive the reader round trip. More content for a realistic scrollable extraction workspace.`}))};
   }
  }
  if(result)return route.fulfill({status:200,contentType:'application/json; charset=utf-8',body:JSON.stringify(result)});
  report.unexpectedRequests.push(request.url());return route.abort();
 });
 const rootNode=page.locator('#moduleRoot');
 const sourceButton=()=>page.locator('.rm-text-block').first().getByRole('button',{name:'定位原文',exact:true});
 async function selectModule(name){await page.locator(`[data-module="${name}"]`).click();}
 async function openText(){await selectModule('manage');await page.getByRole('tab',{name:'本页文字',exact:true}).click();await page.locator('.rm-text-block').first().waitFor();}
 async function read(number){await page.getByRole('spinbutton',{name:'原文页码'}).fill(String(number));await page.getByRole('button',{name:'读取本页文字',exact:true}).click();await page.waitForFunction(number=>document.querySelector('.rm-text-block .rm-field-title')?.textContent.includes('第 '+number+' 页'),number);}
 async function openSource(){await sourceButton().evaluate(button=>button.click());await page.locator('#readerReturnButton').waitFor({state:'visible'});}
 async function snapshot(){return page.evaluate(()=>({mounts:fixture.mounts,destroys:fixture.destroys,suspends:fixture.suspends,resumes:fixture.resumes,requests:fixture.requests.length,module:document.querySelector('[data-module][aria-pressed=true]')?.dataset.module,scroll:document.querySelector('#moduleRoot').scrollTop,selected:window.getSelection()?.rangeCount?window.getSelection().getRangeAt(0).toString():'',page:document.querySelector('[aria-label="原文页码"]')?.value,tab:document.querySelector('[role=tab][aria-selected=true]')?.textContent}));}
 try{
  await page.goto(origin);check('ordinary reader has no return context',await page.locator('#readerReturnButton').isHidden());
  await openText();await read(2);await page.getByRole('checkbox',{name:'保留原文换行',exact:true}).check();
  const saved=await page.evaluate(()=>{const root=document.querySelector('#moduleRoot');const text=root.querySelector('.rm-block-text');window.fixtureText=text;window.fixtureShell=root.firstChild;text.focus({preventScroll:true});window.getSelection().setBaseAndExtent(text.firstChild,50,text.firstChild,12);document.dispatchEvent(new Event('selectionchange'));root.scrollTop=920;return {scroll:root.scrollTop,selected:window.getSelection().getRangeAt(0).toString(),requests:fixture.requests.length};});
  check('fixture has meaningful scroll and reversed text selection',saved.scroll>400 && saved.selected.length===38,saved);
  await openSource();let state=await snapshot();
  check('source opens reader and suspends without destroying',state.module==='reader' && state.suspends===1 && state.destroys===0,state);
  check('text origin gives a specific return label',await page.locator('#readerReturnButton').textContent()==='返回文字核对');
  check('source evidence still identifies matching page block',await page.evaluate(()=>fixture.evidence[0].document_id==='fixture-alpha' && fixture.evidence[0].page===2 && fixture.evidence[0].block_id==='fixture-alpha-2-0'));
  await page.locator('#readerReturnButton').click();await page.waitForFunction(()=>document.querySelector('#moduleRoot').scrollTop>400);state=await snapshot();
  check('return reuses the same document tab page and controller',state.module==='manage' && state.mounts===1 && state.resumes===1 && state.tab==='本页文字' && state.page==='2',state);
  check('return restores precise scroll position',Math.abs(state.scroll-saved.scroll)<2,{before:saved.scroll,after:state.scroll});
  check('return preserves text selection and source newline',state.selected===saved.selected,{before:saved.selected,after:state.selected});
  check('return keeps DOM identity focus and line mode',await page.evaluate(()=>document.querySelector('#moduleRoot').firstChild===fixtureShell && document.querySelector('.rm-block-text')===fixtureText && document.activeElement===fixtureText) && await page.getByRole('checkbox',{name:'保留原文换行',exact:true}).isChecked());
  check('return needs no document or page refetch',state.requests===saved.requests,{before:saved.requests,after:state.requests});
  await openSource();await page.locator('#readerReturnButton').click();state=await snapshot();
  check('repeated round trips still reuse retained controller',state.mounts===1 && state.suspends===2 && state.resumes===2 && state.destroys===0,state);
  await openSource();await selectModule('research');state=await snapshot();
  check('another module destroys retained manage context',state.module==='research' && state.destroys===1 && await page.locator('#readerReturnButton').isHidden(),state);
  await openText();check('entering management normally builds a fresh instance',(await snapshot()).mounts===2);
  await page.evaluate(()=>fixture.mode='failure');await sourceButton().evaluate(button=>button.click());await page.waitForFunction(()=>document.querySelector('.app-notice[data-notice-source="rm-notice"]')?.textContent.includes('Synthetic evidence failure'));
  check('failed source navigation stays in the current management page',(await snapshot()).module==='manage' && await page.locator('#readerReturnButton').isHidden());
  await page.evaluate(()=>{fixture.mode='deferred';fixture.oldEvidence=fixture.options.onEvidence;});await sourceButton().evaluate(button=>button.click());await page.waitForFunction(()=>typeof fixture.release==='function');
  await selectModule('data');await page.evaluate(()=>fixture.release());await page.waitForTimeout(50);
  check('late source response cannot overtake a newer module',(await snapshot()).module==='data' && await page.locator('#readerReturnButton').isHidden());
  await page.evaluate(()=>{fixture.mode='immediate';return fixture.oldEvidence({document_id:'fixture-alpha',page:1});});
  check('destroyed management callbacks cannot navigate',(await snapshot()).module==='data' && await page.locator('#readerReturnButton').isHidden());
  await page.evaluate(()=>fixture.mode='immediate');await openText();await openSource();await selectModule('reader');
  check('explicit reader navigation clears temporary return context',await page.locator('#readerReturnButton').isHidden());
  await selectModule('manage');await page.getByRole('tab',{name:'资料与版本',exact:true}).waitFor();
  await page.evaluate(()=>fixture.options.onEvidence({document_id:'fixture-alpha',run_id:'run-alpha',page:1}));
  check('non-text source uses the management return label',await page.locator('#readerReturnButton').textContent()==='返回文献管理');
  await page.locator('#readerReturnButton').click();check('non-text source restores its original tab',(await snapshot()).tab==='资料与版本');
  check('no browser script errors',report.pageErrors.length===0,report.pageErrors);
  check('no external or write requests',report.unexpectedRequests.length===0,report.unexpectedRequests);
 }catch(error){report.failure=String(error.stack||error);process.exitCode=1;try{await page.screenshot({path:path.join(output,'failure.png')});}catch{}}
 finally{report.finishedAt=new Date().toISOString();report.passed=!report.failure&&report.checks.every(item=>item.passed);fs.writeFileSync(path.join(output,'report.json'),JSON.stringify(report,null,2)+'\n');console.log(JSON.stringify({passed:report.passed,checks:report.checks.length,output,failure:report.failure},null,2));await browser.close();}
}
main().catch(error=>{console.error(error);process.exitCode=1;});
