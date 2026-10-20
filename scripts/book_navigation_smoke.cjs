/* Actual book chapter -> extracted text -> original PDF -> restored text state. */
'use strict';
const fs=require('node:fs/promises'),path=require('node:path'),crypto=require('node:crypto'),assert=require('node:assert/strict');
const {spawn}=require('node:child_process');
const {chromium}=require(process.env.PLAYWRIGHT_MODULE||'C:/Users/g2806/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright');
const root=path.resolve(__dirname,'..');
async function main(){
 const child=spawn(path.join(root,'.venv','Scripts','python.exe'),['-u',path.join(__dirname,'book_browser_fixture.py'),process.argv[2]],{cwd:root,windowsHide:true,stdio:['pipe','pipe','pipe']});
 let ready,browser,context,page,failure,stderr='';child.stderr.on('data',data=>stderr+=data);
 const report={checks:[],errors:[],external:[],driver_sha256:crypto.createHash('sha256').update(await fs.readFile(__filename)).digest('hex'),scope:'Supplemental actual book navigation only; complete 541-page sweep is a separate accepted run.'};
 const check=(name,ok,detail)=>{report.checks.push({name,passed:Boolean(ok),...(detail===undefined?{}:{detail})});assert.ok(ok,name);};
 try{
  ready=await new Promise((resolve,reject)=>{let text='';const timer=setTimeout(()=>reject(Error('Fixture timeout')),90000);child.stdout.on('data',data=>{text+=data;const end=text.indexOf('\n');if(end<0)return;clearTimeout(timer);try{resolve(JSON.parse(text.slice(0,end)));}catch{reject(Error('Invalid fixture frame'));}});child.once('exit',code=>{clearTimeout(timer);reject(Error('Fixture exit '+code+' '+stderr));});});
  browser=await chromium.launch({headless:true,executablePath:process.env.BROWSER_EXECUTABLE||'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe'});
  context=await browser.newContext({viewport:{width:1440,height:1000}});await context.tracing.start({snapshots:true,screenshots:true,sources:true});
  await context.route('**/*',route=>{if(new URL(route.request().url()).origin!==ready.origin){report.external.push(route.request().url());return route.abort();}return route.continue();});
  check('synthetic login',(await context.request.post(ready.origin+'/api/account/login',{data:{name:'BookUIAlice',password:'local-book-ui-pass-123'}})).ok());
  page=await context.newPage();page.setDefaultTimeout(20000);page.on('pageerror',error=>report.errors.push(String(error)));
  await page.goto(ready.origin);await page.waitForFunction(()=>window.ResearchWorkspace);
  await page.evaluate(did=>window.ResearchWorkspace.onEvidence({document_id:did,page:73}),ready.document_id);
  await page.locator('[data-module="manage"]').click();
  await page.getByLabel('选择文献，含已归档文献',{exact:true}).selectOption(ready.document_id);
  await page.getByRole('tab',{name:'本页文字',exact:true}).click();
  await page.locator('.rm-text-outline .rm-outline-item').first().waitFor({state:'attached'});
  if(!await page.locator('.rm-text-outline').evaluate(details=>details.open))await page.locator('.rm-text-outline > summary').click();
  await page.locator('.rm-text-outline .rm-outline-item').first().waitFor();
  const outline=await context.request.get(ready.origin+`/api/documents/${ready.document_id}/structure?run_id=${ready.current_run_id}`);
  const headings=(await outline.json()).headings.filter(h=>h.evidence?.page>20&&h.evidence?.page<500);
  check('real book has source-bound navigable chapters',headings.length>0);
  const chosen=headings[Math.floor(headings.length/2)],evidence=chosen.evidence;
  const id=evidence.block_id||evidence.id;
  await page.locator(`.rm-text-outline .rm-outline-item[data-block-id="${id}"]`).evaluate(button=>button.click());
  await page.waitForFunction(({id,number})=>document.querySelector('.rm-page-number')?.value===String(number)&&document.querySelector('.rm-text-outline .rm-outline-item[aria-current="location"]')?.dataset.blockId===id,{id,number:evidence.page});
  const rawPage=await context.request.get(ready.origin+`/api/documents/${ready.document_id}/pages/${evidence.page}?run_id=${ready.current_run_id}`);
  const original=(await rawPage.json()).blocks.find(b=>b.id===id);
  check('chapter targets exact extracted source block',(await page.locator('.rm-text-block.is-previewed .rm-block-text').textContent())===original.text);
  check('chapter stays in extracted-text module',await page.locator('[data-module="manage"]').getAttribute('aria-pressed')==='true');
  await page.screenshot({path:path.join(ready.output,'chapter-text.png')});
  await page.getByRole('spinbutton',{name:'原文页码',exact:true}).fill('73');
  await page.getByRole('button',{name:'读取本页文字',exact:true}).click();
  await page.waitForFunction(()=>document.querySelector('.rm-text-block .rm-field-title')?.textContent.includes('第 73 页'));
  const saved=await page.evaluate(()=>{const root=document.querySelector('#moduleRoot');root.scrollTop=640;window.bookNavShell=root.firstChild;return{scroll:root.scrollTop,text:root.querySelectorAll('.rm-block-text')[5].textContent};});
  check('meaningful text scroll before source navigation',saved.scroll>200,saved);
  await page.locator('.rm-text-block').nth(5).getByRole('button',{name:'定位原文',exact:true}).evaluate(button=>button.click());
  await page.locator('#readerReturnButton').waitFor({state:'visible'});await page.locator('#evidenceHighlight').waitFor({state:'visible'});
  check('actual PDF source is the same book and page',await page.evaluate(did=>ResearchWorkspace.currentDocument()?.id===did&&document.querySelector('#pageNumber').value==='73',ready.document_id));
  check('source return entry is text-specific',(await page.locator('#readerReturnButton').textContent()).includes('返回文字核对'));
  await page.screenshot({path:path.join(ready.output,'chapter-original.png')});
  await page.locator('#readerReturnButton').click();await page.getByRole('tab',{name:'本页文字',exact:true}).waitFor();
  const restored=await page.evaluate(()=>{const root=document.querySelector('#moduleRoot');return{sameShell:root.firstChild===bookNavShell,scroll:root.scrollTop,page:root.querySelector('.rm-page-number').value,text:root.querySelectorAll('.rm-block-text')[5].textContent};});
  check('text return retains controller DOM and exact paragraph',restored.sameShell&&restored.page==='73'&&restored.text===saved.text,restored);
  check('text return restores scroll position',Math.abs(restored.scroll-saved.scroll)<3,{saved:saved.scroll,restored:restored.scroll});
  await page.screenshot({path:path.join(ready.output,'chapter-return.png')});
  check('no script errors or external requests',!report.errors.length&&!report.external.length);report.passed=true;
 }catch(error){failure=error;report.errors.push(String(error));report.passed=false;if(page&&ready)await page.screenshot({path:path.join(ready.output,'navigation-failure.png')}).catch(()=>{});}
 finally{
  if(context&&ready)await context.tracing.stop({path:path.join(ready.output,'navigation-trace.zip')});if(browser)await browser.close();
  const stopped=new Promise(resolve=>{if(child.exitCode!==null)return resolve(child.exitCode);child.once('exit',resolve);});child.stdin.end('stop\n');let timer;
  const code=await Promise.race([stopped,new Promise(resolve=>{timer=setTimeout(()=>resolve('timeout'),20000);})]);clearTimeout(timer);
  if(code==='timeout'){child.kill();await stopped;report.errors.push('Fixture stop timeout');report.passed=false;}else if(code!==0){report.errors.push('Fixture exit '+code+' '+stderr);report.passed=false;}
  if(ready){report.completion=JSON.parse(await fs.readFile(path.join(ready.output,'completion.json'),'utf8'));if(!report.completion.stopped||!report.completion.main_unchanged||!report.completion.book_history_unchanged||report.completion.model_calls||!report.completion.runtime_unchanged)report.passed=false;await fs.writeFile(path.join(ready.output,'navigation-report.json'),JSON.stringify(report,null,2),{flag:'wx'});console.log(JSON.stringify({output:ready.output,passed:report.passed,checks:report.checks.length,errors:report.errors}));}
 }
 if(failure)throw failure;if(!report.passed)throw Error('Book navigation acceptance failed');
}
main().catch(error=>{console.error(error);process.exitCode=1;});
