/* Offline navigation/motion regression against the complete current frontend.
 * Every request is fulfilled by synthetic fixtures; no live service or model. */
'use strict';
const fs = require('node:fs'), path = require('node:path'), crypto = require('node:crypto'), assert = require('node:assert/strict');
const {chromium} = require(process.env.PLAYWRIGHT_MODULE || 'C:/Users/g2806/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright');
const root = path.resolve(__dirname, '..'), origin = 'http://127.0.0.1:8765';
const output = path.join(root, 'artifacts/browser', `navigation-motion-${new Date().toISOString().replace(/[:.]/g,'-')}-${crypto.randomBytes(3).toString('hex')}`);
fs.mkdirSync(output,{recursive:true});
const assets = new Map(fs.readdirSync(path.join(root,'research_workspace/static'),{withFileTypes:true}).filter(entry=>entry.isFile()).map(({name})=>[name,fs.readFileSync(path.join(root,'research_workspace/static',name))]));
const report = {output,checks:[],pageErrors:[],unexpectedRequests:[],sha256:{},limitations:['Synthetic API and source images; verifies frontend navigation, not model output or PDF parsing.']};
for(const [name,bytes] of assets) if(/\.(js|css|html)$/.test(name)) report.sha256[name]=crypto.createHash('sha256').update(bytes).digest('hex');
report.sha256['scripts/navigation_motion_smoke.cjs']=crypto.createHash('sha256').update(fs.readFileSync(__filename)).digest('hex');
const run={id:'run-motion',version:1,status:'completed'};
const docs=['A','B'].map(id=>({id,title:`SYNTHETIC Motion ${id}`,filename:`${id}.pdf`,authors:['Fixture'],page_count:4,current_run:run,available_run:run}));
const evidence=(page,index)=>({document_id:'A',run_id:run.id,page,block_id:`p${page}-b${index}`,bbox:[.1,.1,.8,.3]});
const headings=[1,2,3,4].flatMap(page=>[0,2,4,6,8].map(index=>({title:`Chapter ${page}.${index}`,level:index?2:1,evidence:evidence(page,index)})));
const pageData=page=>({document_id:'A',run_id:run.id,page,status:'completed',width:600,height:800,blocks:Array.from({length:12},(_,index)=>({id:`p${page}-b${index}`,ordinal:index,page,bbox:[.1,.1+index*.05,.8,.14+index*.05],text:`PAGE ${page} BLOCK ${index}. `+'Synthetic research paragraph for checking stable navigation and readable selection. '.repeat(5)}))});
const jobs=['one','two'].map((id,index)=>({id,kind:'qa',status:'completed',created_at:`2026-10-13T00:00:0${index}Z`,events:[],result:{claims:[{text:`Synthetic answer ${id}`,citations:[]}],sources:[],limitations:[]}}));
const holds=new Set(), pending=new Map(), fail=new Set(), reads=[];
function check(name,ok,details){report.checks.push({name,passed:Boolean(ok),details});assert.ok(ok,name);}
async function main(){
 const browser=await chromium.launch({headless:true,executablePath:process.env.BROWSER_EXECUTABLE||'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe'});
 const context=await browser.newContext({viewport:{width:1440,height:800},reducedMotion:'no-preference'});
 await context.route('**/*',async route=>{
  const req=route.request(),url=new URL(req.url()),p=url.pathname;
  if(url.origin!==origin||req.method()!=='GET'){report.unexpectedRequests.push(req.url());return route.abort();}
  if(p==='/'||p.startsWith('/static/')){const name=p==='/'?'index.html':path.basename(p);return route.fulfill({status:200,contentType:{'.html':'text/html','.js':'text/javascript','.css':'text/css','.svg':'image/svg+xml'}[path.extname(name)],body:assets.get(name)});}
  const match=p.match(/\/pages\/(\d+)(\/image)?$/);
  if(match){const number=Number(match[1]);if(match[2])return route.fulfill({contentType:'image/svg+xml',body:'<svg xmlns="http://www.w3.org/2000/svg" width="600" height="800"><rect width="600" height="800" fill="white"/><text x="40" y="80">Synthetic original</text></svg>'});
   reads.push(number);const failure=fail.delete(number);if(holds.has(number))await new Promise(resolve=>pending.set(number,resolve));
   return route.fulfill({status:failure?503:200,contentType:'application/json',body:JSON.stringify(failure?{detail:'Synthetic failure'}:pageData(number))});}
  let value;
  if(p==='/api/account')value={user:{mode:'local'}};
  else if(p==='/api/capabilities')value={mode:'local',max_upload_mb:50,llm:false,intelligence:{configured:false}};
  else if(p==='/api/documents')value={documents:docs};
  else if(/^\/api\/documents\/[^/]+$/.test(p))value={document:docs.find(d=>p.endsWith(d.id))};
  else if(p.endsWith('/versions'))value={family:{documents:[]},candidates:[]};
  else if(p.endsWith('/structure'))value={headings,references:[],citations:[],limitations:[]};
  else if(p==='/api/research/tasks')value={jobs};
  else if(p.startsWith('/api/research/tasks/'))value={job:jobs.find(job=>p.endsWith(job.id))};
  else if(p==='/api/subscriptions')value={subscriptions:[]};
  else if(p==='/api/recommendations')value={items:[]};
  else if(p==='/api/datasets')value={datasets:[]};
  else {report.unexpectedRequests.push(p);return route.abort();}
  return route.fulfill({contentType:'application/json',body:JSON.stringify(value)});
 });
 const page=await context.newPage();page.setDefaultTimeout(8000);page.on('pageerror',error=>report.pageErrors.push(String(error)));
 const chapter=(number,index)=>page.locator('.rm-text-outline').getByRole('button',{name:`Chapter ${number}.${index}`,exact:true});
 const moduleButton=name=>page.locator('.module-navigation').getByRole('button',{name,exact:true});
 async function settled(){await page.waitForTimeout(650);}
 async function loaded(number){await page.waitForFunction(n=>document.querySelector('.rm-block-text')?.textContent.startsWith(`PAGE ${n} `)&&document.querySelector('.rm-page-blocks')?.getAttribute('aria-busy')==='false',number);}
 async function held(number){for(let i=0;i<100&&!pending.has(number);i++)await page.waitForTimeout(10);assert.ok(pending.has(number),'actual deferred request');}
 function release(number){holds.delete(number);const resolve=pending.get(number);assert.ok(resolve,'pending request exists');pending.delete(number);resolve();}
 async function aligned(selector){await settled();return page.locator(selector).evaluate(group=>{const item=group.querySelector('button[aria-selected="true"],button[aria-pressed="true"],button[aria-current]'),marker=group.querySelector('.selection-indicator'),a=item?.getBoundingClientRect(),b=marker?.getBoundingClientRect();return Boolean(a&&b&&!marker.hidden&&Math.abs(a.x-b.x)<1&&Math.abs(a.y-b.y)<1&&Math.abs(a.width-b.width)<1&&Math.abs(a.height-b.height)<1);});}
 try{
  await page.goto(origin);await page.locator('#connectionState.is-connected').waitFor();
  await page.evaluate(()=>window.oldRows=[...document.querySelectorAll('.document-row')]);
  await page.locator('.document-row').nth(1).click();
  check('library selection preserves row DOM',await page.evaluate(()=>oldRows.every((row,i)=>row===document.querySelectorAll('.document-row')[i])));
  check('library selected state updates',await page.locator('.document-row').nth(1).getAttribute('aria-pressed')==='true');
  await moduleButton('文献管理').click();await page.getByRole('combobox',{name:'选择文献，含已归档文献'}).selectOption('A');
  await page.evaluate(()=>window.oldModule=document.querySelector('.rm-shell'));
  await moduleButton('文献管理').click();check('repeated current module does not remount content',await page.evaluate(()=>oldModule===document.querySelector('.rm-shell')));
  check('top navigation indicator aligns',await aligned('.module-navigation'));
  await page.getByRole('tab',{name:'本页文字',exact:true}).click();await loaded(1);
  check('management tab indicator aligns',await aligned('.rm-tabs'));
  await chapter(1,2).click();await settled();
  await page.evaluate(()=>{window.oldOutline=document.querySelector('.rm-text-outline');window.oldList=document.querySelector('.rm-text-list');window.oldImage=document.querySelector('.rm-preview-image');});
  const beforeReads=reads.length;
  const from=await page.locator('.rm-text-outline .selection-indicator').evaluate(el=>new DOMMatrix(getComputedStyle(el).transform).m42);
  await chapter(1,4).click();
  const frames=await page.locator('.rm-text-outline .selection-indicator').evaluate(el=>new Promise(resolve=>{const values=[];let count=0;function sample(){values.push(new DOMMatrix(getComputedStyle(el).transform).m42);if(++count<20)requestAnimationFrame(sample);else resolve(values);}requestAnimationFrame(sample);}));
  const to=frames.at(-1);
  check('chapter marker has intermediate motion frames',frames.some(y=>y>Math.min(from,to)+1&&y<Math.max(from,to)-1),{from,to,frames});
  check('same-page chapter performs no extra page request',reads.length===beforeReads);
  check('same-page text, image and outline retain identity',await page.evaluate(()=>oldOutline===document.querySelector('.rm-text-outline')&&oldList===document.querySelector('.rm-text-list')&&oldImage===document.querySelector('.rm-preview-image')));
  check('chapter indicator finishes at active row',await aligned('.rm-text-outline nav'));
  await page.locator('.rm-block-text').nth(5).click();
  check('reading a paragraph retains its parent chapter marker',await page.locator('.rm-text-outline [aria-current]').textContent()==='Chapter 1.4');
  holds.add(2);holds.add(3);await chapter(2,2).click();await held(2);
  check('pending page keeps chapter navigation interactive',await page.locator('.rm-text-outline').evaluate(el=>!el.closest('[inert]')));
  await chapter(3,4).click();await held(3);release(3);await loaded(3);release(2);await settled();
  check('latest chapter wins reverse-order responses',await page.locator('.rm-text-outline [aria-current]').textContent()==='Chapter 3.4');
  check('cross-page navigation retains outline DOM',await page.evaluate(()=>oldOutline===document.querySelector('.rm-text-outline')));
  holds.add(2);const count=reads.filter(n=>n===2).length;await chapter(2,2).click();await held(2);await chapter(2,6).click();release(2);await loaded(2);await settled();
  check('same pending page is shared between chapter clicks',reads.filter(n=>n===2).length===count+1);
  check('last chapter on shared page wins',await page.locator('.rm-text-outline [aria-current]').textContent()==='Chapter 2.6');
  fail.add(4);holds.add(4);await chapter(4,0).click();await held(4);release(4);await page.waitForFunction(()=>document.querySelector('.rm-selection-hint').classList.contains('is-error'));
  check('failed chapter navigation keeps previous text and selection',await page.locator('.rm-text-outline [aria-current]').textContent()==='Chapter 2.6'&&(await page.locator('.rm-block-text').first().textContent()).startsWith('PAGE 2'));
  await page.emulateMedia({reducedMotion:'reduce'});await chapter(2,4).click();
  check('reduced motion disables selection animation',await page.locator('.rm-text-outline .selection-indicator').evaluate(el=>getComputedStyle(el).transitionDuration.split(',').every(v=>parseFloat(v)===0)));
  check('same-page chapter resets failed target page and error',await page.getByRole('spinbutton',{name:'原文页码'}).inputValue()==='2'&&!(await page.locator('.rm-selection-hint').getAttribute('class')).includes('is-error'));
  check('reduced motion still selects and positions indicator',await aligned('.rm-text-outline nav'));
  await page.emulateMedia({reducedMotion:'no-preference'});
  for(const width of [602,320,1440]){
   await page.setViewportSize({width,height:800});
   check(`chapter indicator aligns after resize ${width}`,await aligned('.rm-text-outline nav'));
   check(`wrapped tabs align after resize ${width}`,await aligned('.rm-tabs'));
   check(`no horizontal overflow ${width}`,await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1));
  }
  await page.getByRole('tab',{name:'资料与版本',exact:true}).click();await page.getByRole('tab',{name:'本页文字',exact:true}).click();
  check('hidden then restored chapter indicator remains aligned',await aligned('.rm-text-outline nav'));
  await page.screenshot({path:path.join(output,'chapters.png')});
  await moduleButton('问答与写作').click();await page.locator('.rs-history-item').nth(1).waitFor();
  await page.evaluate(()=>window.oldHistory=[...document.querySelectorAll('.rs-history-item')]);await page.locator('.rs-history-item').nth(1).click();
  await page.waitForFunction(()=>document.querySelectorAll('.rs-history-item')[1]?.getAttribute('aria-pressed')==='true');
  check('task selection preserves history row identity',await page.evaluate(()=>oldHistory.every((item,i)=>item===document.querySelectorAll('.rs-history-item')[i])));
  await moduleButton('发现与追踪').click();await page.getByRole('button',{name:'本地推荐',exact:true}).click();check('discovery tab indicator aligns',await aligned('.ex-tabs'));
  await page.evaluate(()=>window.oldResult=document.querySelector('.ex-paper-list').firstChild);await page.getByRole('button',{name:'本地推荐',exact:true}).click();
  check('repeated discovery tab retains its result DOM',await page.evaluate(()=>oldResult===document.querySelector('.ex-paper-list').firstChild));
  await page.getByRole('button',{name:'公开检索',exact:true}).click();check('discovery return indicator aligns',await aligned('.ex-tabs'));
  check('no browser script errors',report.pageErrors.length===0,report.pageErrors);
  check('no unexpected or non-read requests',report.unexpectedRequests.length===0,report.unexpectedRequests);
 }catch(error){report.failure=String(error.stack||error);process.exitCode=1;await page.screenshot({path:path.join(output,'failure.png')}).catch(()=>{});}
 finally{for(const resolve of pending.values())resolve();report.passed=!report.failure&&report.checks.every(c=>c.passed);fs.writeFileSync(path.join(output,'report.json'),JSON.stringify(report,null,2));await browser.close();console.log(JSON.stringify({passed:report.passed,checks:report.checks.length,output,failure:report.failure}));}
}
main().catch(error=>{console.error(error);process.exitCode=1;});
