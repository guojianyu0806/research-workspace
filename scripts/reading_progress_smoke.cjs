/* Offline reader progress checks against the current static application.
 * All HTTP requests are fulfilled from synthetic memory fixtures. Browser
 * contexts are disposable; no live server, model, or document data is used.
 */
'use strict';
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const crypto = require('node:crypto');
const {execFileSync} = require('node:child_process');
const {chromium} = require(process.env.PLAYWRIGHT_MODULE || 'C:/Users/g2806/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright');

const root = path.resolve(__dirname, '..');
const origin = 'http://127.0.0.1:8765';
const prefix = 'research-reader-page:v1:';
const output = path.join(root, 'artifacts/browser', `reading-progress-${new Date().toISOString().replace(/[:.]/g, '-')}-${crypto.randomBytes(3).toString('hex')}`);
fs.mkdirSync(output, {recursive:true});
const assetRoot = path.join(root, 'research_workspace/static');
const assets = new Map(fs.readdirSync(assetRoot, {withFileTypes:true}).filter(entry=>entry.isFile()).map(entry=>[entry.name, fs.readFileSync(path.join(assetRoot, entry.name))]));
const digest = value=>crypto.createHash('sha256').update(value).digest('hex');
const report = {
  startedAt:new Date().toISOString(), output, checks:[], screenshots:[], pageErrors:[], consoleErrors:[], blockedRequests:[],
  source:{gitHead:execFileSync('git', ['rev-parse','HEAD'], {cwd:root, encoding:'utf8', windowsHide:true}).trim(), sha256:{}},
  limitations:[
    'Uses the full current static frontend in disposable Edge contexts with synthetic documents and image responses.',
    'Storage survives reload only within the same disposable context; no real browser profile or application storage is touched.',
    'Does not verify backend persistence, actual PDF fidelity, model quality, or other browser engines.'
  ]
};
for(const [name,content] of assets) if(/\.(html|css|js)$/.test(name)) report.source.sha256[name]=digest(content);
report.source.sha256['scripts/reading_progress_smoke.cjs']=digest(fs.readFileSync(__filename));
let browser;
const makeDoc=(id,pages)=>({id,title:`SYNTHETIC ${id} paper`,filename:`SYNTHETIC-${id}.pdf`,page_count:pages,current_run:{id:`run-${id}`,status:'completed',total_pages:pages,text_pages:pages,pages_done:pages},available_run:{id:`run-${id}`,status:'completed'}});
const syntheticImage=(id,page)=>`<svg xmlns="http://www.w3.org/2000/svg" width="600" height="800" viewBox="0 0 600 800"><rect width="600" height="800" fill="white"/><text x="40" y="70" fill="#183643" font-size="28">SYNTHETIC ${id} PAGE ${page}</text></svg>`;

async function check(name, action){
  try{const evidence=await action();report.checks.push({name,passed:true,...evidence});console.log(`PASS ${name}`);}
  catch(error){report.checks.push({name,passed:false,error:String(error.stack||error)});console.log(`FAIL ${name}: ${error.message}`);throw error;}
}
async function fixture({seed={},storageFailure=null,width=1440,docs=[makeDoc('A',8),makeDoc('B',12),makeDoc('C',1),makeDoc('Z',0)]}={}){
  const context=await browser.newContext({viewport:{width,height:900},reducedMotion:'reduce',storageState:{cookies:[],origins:[{origin,localStorage:Object.entries(seed).map(([name,value])=>({name,value:String(value)}))}]}});
  if(storageFailure) await context.addInitScript(({prefix,mode})=>{
    for(const method of (mode==='read' ? ['getItem'] : mode==='write' ? ['setItem'] : ['getItem','setItem'])){
      const original=Storage.prototype[method];
      Storage.prototype[method]=function(key,...values){if(String(key).startsWith(prefix))throw new DOMException('Synthetic storage unavailable','SecurityError');return original.call(this,key,...values);};
    }
  },{prefix,mode:storageFailure});
  const f={context,docs:structuredClone(docs),requests:[],holds:new Map(),pending:new Map()};
  f.hold=key=>f.holds.set(key,true);
  f.release=async key=>{f.holds.delete(key);const resume=f.pending.get(key);assert.ok(resume,`Deferred ${key} actually requested`);f.pending.delete(key);resume();};
  await context.route('**/*',async route=>{
    const request=route.request(),url=new URL(request.url());
    if(url.origin!==origin||!['GET','HEAD'].includes(request.method())){report.blockedRequests.push({url:url.origin+url.pathname,method:request.method()});return route.abort();}
    f.requests.push(url.pathname+url.search);
    if(url.pathname==='/'||url.pathname.startsWith('/static/')){
      const name=url.pathname==='/'?'index.html':path.basename(url.pathname);
      if(!assets.has(name)){report.blockedRequests.push({url:url.pathname,reason:'unknown asset'});return route.fulfill({status:404,body:''});}
      return route.fulfill({status:200,contentType:{'.html':'text/html; charset=utf-8','.js':'text/javascript; charset=utf-8','.css':'text/css; charset=utf-8','.svg':'image/svg+xml'}[path.extname(name)],body:assets.get(name)});
    }
    const match=url.pathname.match(/^\/api\/documents\/([^/]+)\/pages\/(\d+)(\/image)?$/);
    if(match){
      const [,id,number,image]=match,key=`${id}:${number}:${image?'image':'text'}`;
      if(f.holds.has(key))await new Promise(resolve=>f.pending.set(key,resolve));
      if(image)return route.fulfill({status:200,contentType:'image/svg+xml',body:syntheticImage(id,number)});
      return route.fulfill({status:200,contentType:'application/json',body:JSON.stringify({text:`SYNTHETIC ${id} page ${number}`,blocks:[{id:`block-${id}-${number}`,text:`SYNTHETIC ${id} page ${number}`,bbox:[.1,.1,.6,.3]}]})});
    }
    let result;
    if(url.pathname==='/api/account')result={user:{mode:'local'}};
    else if(url.pathname==='/api/capabilities')result={mode:'local',max_upload_mb:50,ocr:true,llm:false};
    else if(url.pathname==='/api/documents')result={documents:f.docs};
    else if(url.pathname==='/api/datasets')result={datasets:[]};
    else if(/^\/api\/documents\/[^/]+$/.test(url.pathname))result={document:f.docs.find(doc=>doc.id===decodeURIComponent(url.pathname.split('/').at(-1)))};
    else{report.blockedRequests.push({url:url.pathname,reason:'unknown endpoint'});result={};}
    return route.fulfill({status:200,contentType:'application/json',body:JSON.stringify(result)});
  });
  f.page=await context.newPage();f.page.setDefaultTimeout(7000);
  f.page.on('pageerror',error=>report.pageErrors.push(String(error)));
  f.page.on('console',message=>{if(message.type()==='error')report.consoleErrors.push(message.text());});
  await f.page.goto(origin);await f.page.locator('#connectionState.is-connected').waitFor();
  if(f.docs.length)await f.page.locator('#paperPage:visible').waitFor();
  return f;
}
async function reading(page){return page.evaluate(()=>({id:window.ResearchWorkspace.currentDocument()?.id,page:Number(document.querySelector('#pageNumber').value),image:document.querySelector('#pageImage').getAttribute('src'),alt:document.querySelector('#pageImage').alt,loading:!document.querySelector('#pageLoading').hidden,firstDisabled:document.querySelector('#firstPage').disabled,previousDisabled:document.querySelector('#previousPage').disabled,nextDisabled:document.querySelector('#nextPage').disabled,lastDisabled:document.querySelector('#lastPage').disabled,total:document.querySelector('#pageTotal').textContent,highlight:!document.querySelector('#evidenceHighlight').hidden}));}
async function expected(page,id,number,{image=true}={}){
  await page.waitForFunction(({id,number,image})=>window.ResearchWorkspace.currentDocument()?.id===id&&Number(document.querySelector('#pageNumber').value)===number&&(!image||(!document.querySelector('#paperPage').hidden&&document.querySelector('#pageImage').getAttribute('src')?.includes(`/documents/${id}/pages/${number}/image`))),{id,number,image});
  return reading(page);
}
async function select(page,id){
  if(!await page.locator('#documentList').isVisible()||await page.locator('#libraryPanel').evaluate(el=>el.inert))await page.locator('#toggleLibrary').click();
  await page.locator('.document-row').filter({hasText:`SYNTHETIC ${id} paper`}).click();
}
async function turn(page,number){await page.locator('#pageNumber').fill(String(number));await page.locator('#pageForm').evaluate(el=>el.requestSubmit());}
async function stored(page,id){return page.evaluate(key=>localStorage.getItem(key),prefix+id);}
async function scenario(name,action){let f;try{f=await fixture();await action(f);}catch(error){if(!report.checks.some(check=>!check.passed&&check.error?.includes(error.message)))report.checks.push({name,passed:false,error:String(error.stack||error)});}finally{if(f)await f.context.close();}}

async function coreChecks(){await scenario('core-reader-progress',async f=>{
  const p=f.page;
  await check('new-paper-starts-first-and-first-controls-disabled',async()=>{const s=await expected(p,'A',1);assert.ok(s.firstDisabled&&s.previousDisabled&&!s.nextDisabled&&!s.lastDisabled);return s;});
  await turn(p,3);await expected(p,'A',3);await select(p,'B');await expected(p,'B',1);await turn(p,5);await expected(p,'B',5);
  await check('switch-A3-B5-A-restores-third-page',async()=>{await select(p,'A');const s=await expected(p,'A',3);assert.equal(await stored(p,'A'),'3');assert.equal(await stored(p,'B'),'5');return s;});
  await check('reselecting-current-document-keeps-page',async()=>{await select(p,'A');return expected(p,'A',3);});
  await check('switching-back-to-B-restores-fifth-page',async()=>{await select(p,'B');return expected(p,'B',5);});
  await check('independent-pages-survive-reload',async()=>{await p.reload();await expected(p,'A',3);await select(p,'B');const s=await expected(p,'B',5);return s;});
  await check('explicit-evidence-page-overrides-remembered-position',async()=>{await p.evaluate(()=>window.ResearchWorkspace.onEvidence({document_id:'A',run_id:'historical-A',page:7,block_id:'historical-block',bbox:[.1,.2,.6,.4]}));const s=await expected(p,'A',7);assert.ok(s.highlight);assert.equal(await stored(p,'A'),'7');assert.ok(f.requests.some(url=>url.includes('/documents/A/pages/7?run_id=historical-A')));return s;});
  await check('ordinary-return-remembers-evidence-page-with-current-run',async()=>{await select(p,'B');await expected(p,'B',5);await select(p,'A');const s=await expected(p,'A',7);assert.equal(s.highlight,false);assert.ok(f.requests.some(url=>url.includes('/documents/A/pages/7?run_id=run-A')));return s;});
  await check('last-page-button-jumps-to-end-and-disables-forward',async()=>{await p.locator('#lastPage').click();const s=await expected(p,'A',8);assert.ok(!s.firstDisabled&&!s.previousDisabled&&s.nextDisabled&&s.lastDisabled);assert.equal(await stored(p,'A'),'8');return s;});
  await check('first-page-button-jumps-to-start-and-disables-back',async()=>{await p.locator('#firstPage').click();const s=await expected(p,'A',1);assert.ok(s.firstDisabled&&s.previousDisabled&&!s.nextDisabled&&!s.lastDisabled);assert.equal(await stored(p,'A'),'1');return s;});
  await check('normal-previous-and-next-also-update-progress',async()=>{await p.locator('#nextPage').click();await expected(p,'A',2);assert.equal(await stored(p,'A'),'2');await p.locator('#previousPage').click();const s=await expected(p,'A',1);assert.equal(await stored(p,'A'),'1');return s;});
  await check('invalid-page-entry-keeps-existing-progress',async()=>{await turn(p,3);await expected(p,'A',3);for(const value of ['0','99','2.5']){await p.locator('#pageNumber').fill(value);await p.locator('#pageNumber').dispatchEvent('change');assert.equal(await p.locator('#pageNumber').inputValue(),'3');assert.equal(await stored(p,'A'),'3');}return reading(p);});
  await check('single-page-paper-disables-all-navigation',async()=>{await select(p,'C');const s=await expected(p,'C',1);assert.ok(s.firstDisabled&&s.previousDisabled&&s.nextDisabled&&s.lastDisabled);return s;});
  await check('zero-page-paper-disables-navigation',async()=>{await select(p,'Z');const s=await expected(p,'Z',1,{image:false});assert.ok(s.firstDisabled&&s.previousDisabled&&s.nextDisabled&&s.lastDisabled);return s;});
  await check('same-title-different-document-id-has-independent-progress',async()=>{f.docs.push({...makeDoc('A-copy',8),title:'SYNTHETIC A paper',filename:'SYNTHETIC-A.pdf'});await p.evaluate(()=>window.ResearchWorkspace.refreshLibrary());await p.locator('.document-row').filter({hasText:'SYNTHETIC A paper'}).last().click();const s=await expected(p,'A-copy',1);assert.equal(await stored(p,'A'),'3');return s;});
});}

async function storageChecks(){
  for(const value of ['garbage','0','-2','2.5','Infinity','9007199254740992','{"page":3}']){
    let f;try{f=await fixture({seed:{[prefix+'A']:value}});await check(`invalid-storage-${value}-falls-back-safely`,async()=>expected(f.page,'A',1));}finally{if(f)await f.context.close();}
  }
  for(const mode of ['read','write','both']){
    let f;try{f=await fixture({storageFailure:mode});await check(`storage-${mode}-failure-keeps-session-progress`,async()=>{await turn(f.page,3);await expected(f.page,'A',3);await select(f.page,'B');await expected(f.page,'B',1);await turn(f.page,5);await expected(f.page,'B',5);await select(f.page,'A');const s=await expected(f.page,'A',3);await select(f.page,'B');await expected(f.page,'B',5);return s;});}finally{if(f)await f.context.close();}
  }
  let f;try{f=await fixture({seed:{[prefix+'A']:'80'}});await check('restored-page-is-clamped-to-document-total',async()=>{const s=await expected(f.page,'A',8);assert.equal(await stored(f.page,'A'),'8');return s;});}finally{if(f)await f.context.close();}
}

async function changedTotalChecks(){await scenario('changed-total',async f=>{
  const p=f.page;await turn(p,7);await expected(p,'A',7);
  await check('refreshed-shorter-document-clamps-visible-and-saved-page',async()=>{f.docs[0].page_count=2;await p.evaluate(()=>window.ResearchWorkspace.refreshLibrary());const s=await expected(p,'A',2);assert.ok(s.lastDisabled&&s.nextDisabled);assert.equal(await stored(p,'A'),'2');return s;});
  await check('clamped-progress-survives-switch-and-reload',async()=>{await select(p,'B');await expected(p,'B',1);await select(p,'A');await expected(p,'A',2);await p.reload();return expected(p,'A',2);});
  await check('expanded-page-count-reenables-last-and-jumps-current-total',async()=>{f.docs[0].page_count=10;await p.evaluate(()=>window.ResearchWorkspace.refreshLibrary());assert.equal(await p.locator('#lastPage').isDisabled(),false);await p.locator('#lastPage').click();const s=await expected(p,'A',10);assert.equal(await stored(p,'A'),'10');return s;});
});}

async function raceChecks(){await scenario('rapid-switch-and-image-races',async f=>{
  const p=f.page;
  f.hold('A:4:image');f.hold('A:4:text');await turn(p,4);await expected(p,'A',4,{image:false});
  await p.waitForTimeout(50);await select(p,'B');await expected(p,'B',1);await turn(p,5);await expected(p,'B',5);
  await check('accepted-loading-page-is-remembered-before-image-finishes',async()=>{assert.equal(await stored(p,'A'),'4');assert.equal(await stored(p,'B'),'5');return reading(p);});
  await check('late-A-image-and-text-cannot-overwrite-current-B',async()=>{await f.release('A:4:text');await f.release('A:4:image');await p.waitForTimeout(150);const s=await expected(p,'B',5);assert.equal(await stored(p,'A'),'4');assert.equal(await stored(p,'B'),'5');assert.ok(s.alt.includes('B paper'));return s;});
  await check('return-to-A-restores-page-selected-during-loading',async()=>{await select(p,'A');return expected(p,'A',4);});
  f.hold('A:6:image');f.hold('A:6:text');await turn(p,6);await expected(p,'A',6,{image:false});await p.waitForTimeout(50);await turn(p,3);await expected(p,'A',3);
  await check('late-older-page-cannot-override-newer-page-or-memory',async()=>{await f.release('A:6:image');await f.release('A:6:text');await p.waitForTimeout(150);const s=await expected(p,'A',3);assert.equal(await stored(p,'A'),'3');return s;});
  f.hold('A:7:image');await turn(p,7);await expected(p,'A',7,{image:false});await p.waitForTimeout(50);await select(p,'B');await expected(p,'B',5);await select(p,'A');await expected(p,'A',7,{image:false});
  await check('rapid-A-B-A-select-keeps-latest-restored-page',async()=>{await f.release('A:7:image');const s=await expected(p,'A',7);assert.equal(await stored(p,'A'),'7');return s;});
});}

async function viewportChecks(){
  for(const width of [1440,602,320]){
    let f;try{f=await fixture({width});await check(`${width}-first-last-controls-visible-readable-and-not-overflowing`,async()=>{
      const data=await f.page.evaluate(()=>{
        const ids=['firstPage','previousPage','pageNumber','nextPage','lastPage'];
        return {window:innerWidth,body:document.documentElement.scrollWidth,toolbar:document.querySelector('#readerToolbar').getBoundingClientRect().toJSON(),controls:ids.map(id=>{const el=document.getElementById(id),r=el.getBoundingClientRect(),style=getComputedStyle(el);return {id,text:el.textContent.trim(),x:r.x,right:r.right,y:r.y,bottom:r.bottom,width:r.width,height:r.height,font:parseFloat(style.fontSize),textFits:el.scrollWidth<=el.clientWidth+1,visible:!!el.getClientRects().length};})};
      });
      assert.ok(data.body<=data.window+1);for(const c of data.controls){assert.ok(c.visible&&c.width>0&&c.height>=28,`${c.id} is visible and clickable`);assert.ok(c.x>=-1&&c.right<=data.window+1,`${c.id} stays inside viewport`);assert.ok(c.font>=12&&c.textFits,`${c.id} text remains readable`);}
      assert.equal(data.controls[0].text,'第一页');assert.equal(data.controls[4].text,'最后一页');
      await f.page.locator('#lastPage').click();await expected(f.page,'A',8);await f.page.locator('#firstPage').click();await expected(f.page,'A',1);
      const filename=`reader-progress-${width}.png`;await f.page.screenshot({path:path.join(output,filename),fullPage:true});report.screenshots.push(filename);return data;
    });}finally{if(f)await f.context.close();}
  }
}

(async()=>{
  try{browser=await chromium.launch({headless:true,executablePath:process.env.BROWSER_EXECUTABLE||'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe'});
    for(const group of [coreChecks,storageChecks,changedTotalChecks,raceChecks,viewportChecks])try{await group();}catch(error){if(!report.checks.some(check=>!check.passed&&check.error?.includes(error.message)))report.checks.push({name:group.name,passed:false,error:String(error.stack||error)});}
    await check('static-snapshot-matches-final-source',async()=>{for(const [name,content]of assets)if(/\.(html|css|js)$/.test(name))assert.equal(digest(fs.readFileSync(path.join(assetRoot,name))),digest(content),`${name} changed during run`);});
  }finally{
    report.finishedAt=new Date().toISOString();report.passed=!!report.checks.length&&report.checks.every(check=>check.passed)&&!report.pageErrors.length&&!report.consoleErrors.length&&!report.blockedRequests.length;
    report.summary={checks:report.checks.length,failed:report.checks.filter(check=>!check.passed).map(check=>check.name),pageErrors:report.pageErrors.length,consoleErrors:report.consoleErrors.length,blockedRequests:report.blockedRequests.length};
    fs.writeFileSync(path.join(output,'report.json'),JSON.stringify(report,null,2),{flag:'wx'});if(browser)await browser.close();console.log(JSON.stringify({output,passed:report.passed,summary:report.summary},null,2));if(!report.passed)process.exitCode=1;
  }
})().catch(error=>{console.error(error);process.exitCode=1;});
