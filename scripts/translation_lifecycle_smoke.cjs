/* Actual shell/tasks/bilingual/reader, real PDF source, synthetic saved targets. */
'use strict';
const fs=require('node:fs/promises'),path=require('node:path'),assert=require('node:assert/strict'),crypto=require('node:crypto');
const {spawn}=require('node:child_process');
const {chromium}=require(process.env.PLAYWRIGHT_MODULE||'C:/Users/g2806/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright');
const root=path.resolve(__dirname,'..');
async function main(){
 const child=spawn(path.join(root,'.venv/Scripts/python.exe'),['-u',path.join(__dirname,'translation_lifecycle_fixture.py')],{cwd:root,windowsHide:true,stdio:['pipe','pipe','pipe']});
 let ready,browser,context,page,stderr='',buffer='',sequence=0,resolveReady,rejectReady;
 const waiting=new Map(),report={checks:[],errors:[],external:[],positions:[],requests:[],scope:'Real local API, actual PDF reader and saved-record lifecycle; synthetic targets, no model language-quality claim.'};
 const initial=new Promise((resolve,reject)=>{resolveReady=resolve;rejectReady=reject;});
 child.stderr.on('data',data=>stderr+=data);
 child.stdout.on('data',data=>{buffer+=data;while(buffer.includes('\n')){const end=buffer.indexOf('\n'),line=buffer.slice(0,end);buffer=buffer.slice(end+1);try{const value=JSON.parse(line);if(value.type==='ready')resolveReady(value);else if(value.type==='ack'){const waiter=waiting.get(value.seq);waiting.delete(value.seq);if(value.ok)waiter?.resolve(value);else waiter?.reject(Error(value.error));}}catch(error){rejectReady(error);}}});
 child.once('exit',code=>{rejectReady(Error('Fixture exited '+code+' '+stderr));for(const waiter of waiting.values())waiter.reject(Error('Fixture stopped'));});
 const check=(name,passed,detail)=>{report.checks.push({name,passed:Boolean(passed),...(detail===undefined?{}:{detail})});assert.ok(passed,name);};
 async function command(caseId,action){const seq=++sequence;return new Promise((resolve,reject)=>{waiting.set(seq,{resolve,reject});child.stdin.write(JSON.stringify({seq,case:caseId,action})+'\n');});}
 async function scenario(name,fn){try{await fn();}catch(error){report.errors.push(name+': '+String(error));if(ready&&page)await page.screenshot({path:path.join(ready.output,name+'-failure.png')}).catch(()=>{});}}
 try{
  let timer;ready=await Promise.race([initial,new Promise((_,reject)=>{timer=setTimeout(()=>reject(Error('Fixture startup timeout')),60000);})]);clearTimeout(timer);
  browser=await chromium.launch({headless:true,executablePath:process.env.BROWSER_EXECUTABLE||'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe'});
  context=await browser.newContext({viewport:{width:1440,height:1000}});await context.tracing.start({screenshots:true,snapshots:true,sources:true});
  await context.route('**/*',route=>{if(new URL(route.request().url()).origin!==ready.origin){report.external.push(route.request().url());return route.abort();}return route.continue();});
  check('synthetic owner login',(await context.request.post(ready.origin+'/api/account/login',{data:{name:'LifecycleAlice',password:'synthetic-lifecycle-password-123'}})).ok());
  page=await context.newPage();page.setDefaultTimeout(12000);page.on('pageerror',error=>report.errors.push('pageerror '+String(error)));
  page.on('request',request=>{const u=new URL(request.url());if(u.pathname.startsWith('/api/research/tasks/'))report.requests.push({path:u.pathname,search:u.search,method:request.method()});});
  await page.goto(ready.origin);await page.waitForFunction(()=>window.ResearchShell&&window.ResearchBilingual);
  await page.evaluate(()=>{const actual=ResearchBilingual;window.lifecycleRenders=0;window.ResearchBilingual={render(...args){lifecycleRenders++;const c=actual.render(...args);window.lifecycleReader=c;return c;}};});
  async function count(total){await page.waitForFunction(total=>document.querySelector('.rb-count')?.textContent.includes(`${total} 个`),total);}
  async function mount(job){await page.evaluate(id=>ResearchShell.activate('research',{jobId:id}),job.job_id);await count(job.total);await page.waitForFunction(()=>document.querySelector('.rb-block:not(.rb-block-loading)'));}
  async function settle(ordinal,fraction){await page.waitForFunction(({ordinal,fraction})=>['source','translation'].every(side=>{const pane=document.querySelector(`.rb-panel-${side} .rb-scroll`),b=pane?.querySelector(`.rb-block[data-ordinal="${ordinal}"]:not(.rb-block-loading)`);return b&&Math.abs(pane.scrollTop-b.offsetTop-fraction*b.getBoundingClientRect().height)<=pane.clientHeight*.1;}),{ordinal,fraction});await page.evaluate(()=>new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve))));}
  async function jump(job,ordinal,fraction=.37){check(job.id+' explicit current ordinal '+ordinal,await page.evaluate(({id,ordinal,fraction})=>lifecycleReader.syncTo(id,{ordinal,side:'translation',fraction}),{id:job.block_ids[ordinal],ordinal,fraction}));await settle(ordinal,fraction);}
  async function position(label){const value=await page.evaluate(()=>({anchor:lifecycleReader.getPosition(),panes:Object.fromEntries(['source','translation'].map(side=>{const p=document.querySelector(`.rb-panel-${side} .rb-scroll`),a=lifecycleReader.getPosition(),b=p.querySelector(`[data-ordinal="${a.ordinal}"]`);return[side,{ordinal:a.ordinal,dom:p.querySelectorAll('.rb-block').length,error:b?Math.abs(p.scrollTop-b.offsetTop-a.fraction*b.getBoundingClientRect().height):null,height:p.clientHeight}];}))}));report.positions.push({label,...value});return value;}
  const valid=ready.jobs.find(j=>j.id==='valid');
  await scenario('valid-growth-and-return',async()=>{
   await mount(valid);await jump(valid,137);const old=await position('unique-before');
   const changed=await command('valid','prepend');await page.getByRole('button',{name:'刷新资料与任务',exact:true}).click();await count(changed.total);await settle(140,.37);
   const after=await position('unique-after');check('unique ID follows insertion rather than old ordinal',after.anchor.block_id===old.anchor.block_id&&after.anchor.ordinal===140);
   await jump({...valid,block_ids:changed.block_ids},140,0);
   const sourceResponse=await context.request.get(ready.origin+`/api/research/tasks/${valid.job_id}/translation-blocks?offset=140&limit=1`),source=(await sourceResponse.json()).blocks[0];
   await page.locator('.rb-panel-source [data-ordinal="140"] .rb-evidence').scrollIntoViewIfNeeded();const saved=await position('before-real-source');
   await page.locator('.rb-panel-source [data-ordinal="140"] .rb-evidence').click();
   await page.waitForFunction(({did,n})=>{const image=document.querySelector('#pageImage');return !document.querySelector('#workspace').hidden&&ResearchWorkspace.currentDocument()?.id===did&&Number(document.querySelector('#pageNumber').value)===n&&image&&!image.hidden&&image.complete&&image.naturalWidth>0&&new URL(image.src).pathname===`/api/documents/${did}/pages/${n}/image`;},{did:source.document_id,n:source.page});
   check('real PDF reader opens exact physical page',await page.locator('#readerReturnButton').isVisible());
   report.original_view={document_id:source.document_id,run_id:source.run_id,block_id:source.block_id,page:source.page,bbox:source.bbox,image:await page.locator('#pageImage').getAttribute('src')};
   const actualImage=await page.locator('#pageImage').evaluate(async img=>{const canvas=document.createElement('canvas');canvas.width=img.naturalWidth;canvas.height=img.naturalHeight;canvas.getContext('2d').drawImage(img,0,0);const pixels=canvas.getContext('2d').getImageData(0,0,canvas.width,canvas.height).data;return{width:canvas.width,height:canvas.height,rgba_sha256:Array.from(new Uint8Array(await crypto.subtle.digest('SHA-256',pixels))).map(v=>v.toString(16).padStart(2,'0')).join('')};});
   const imageResponse=await context.request.get(new URL(report.original_view.image,ready.origin).href);check('original view actual PNG response',imageResponse.ok());const imageBytes=await imageResponse.body();await fs.writeFile(path.join(ready.output,'original-view.png'),imageBytes,{flag:'wx'});Object.assign(report.original_view,actualImage,{png_sha256:crypto.createHash('sha256').update(imageBytes).digest('hex'),file:'original-view.png'});
   await page.locator('#readerReturnButton').click();await settle(saved.anchor.ordinal,saved.anchor.fraction);check('real reader return keeps translation source identity',(await position('after-real-source')).anchor.block_id===saved.anchor.block_id);
   for(const ordinal of [3,4,5,6,7,8,9,10]){
    await jump({...valid,block_ids:changed.block_ids},ordinal,0);
    const response=await context.request.get(ready.origin+`/api/research/tasks/${valid.job_id}/translation-blocks?offset=${ordinal}&limit=1`),record=(await response.json()).blocks[0];
    check('literal saved target retained '+ordinal,await page.locator(`.rb-panel-translation [data-ordinal="${ordinal}"] .rb-text`).textContent()===record.translated_text);
   }
   check('literal markup cannot create elements or execute scripts',await page.evaluate(()=>!window.fixtureXss&&!document.querySelector('.rb-text img,.rb-text script')));
   await jump({...valid,block_ids:changed.block_ids},140,.37);
   for(const width of [1024,760,390,1440]){await page.setViewportSize({width,height:1000});await settle(140,.37);const p=await position('viewport-'+width);check('mixed viewport geometry '+width,p.panes.source.dom<=120&&p.panes.translation.dom<=120&&await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1));}
   await page.screenshot({path:path.join(ready.output,'mixed-desktop.png')});
  });
  await scenario('legacy-ambiguous-restore',async()=>{
   const job=ready.jobs.find(j=>j.id==='legacy');await mount(job);await jump(job,137);const old=await position('legacy-before');
   check('missing ID is marked non-restorable',old.anchor.ambiguous_block_id===true);
   const changed=await command('legacy','prepend');await page.getByRole('button',{name:'刷新资料与任务',exact:true}).click();await count(changed.total);await settle(0,0);
   check('missing ID never restores an unrelated old ordinal',(await position('legacy-after')).anchor.ordinal===0);
   check('legacy source buttons disabled',await page.locator('.rb-panel-source [data-ordinal="0"] .rb-evidence').isDisabled());
  });
  await scenario('duplicate-restore',async()=>{
   const job=ready.jobs.find(j=>j.id==='duplicate');await mount(job);await jump(job,137);
   check('duplicate source cannot navigate',await page.locator('.rb-panel-source [data-ordinal="137"] .rb-evidence').isDisabled());
   const changed=await command('duplicate','prepend');await page.getByRole('button',{name:'刷新资料与任务',exact:true}).click();await count(changed.total);await settle(0,0);check('duplicate identity never chooses a different occurrence',(await position('duplicate-after')).anchor.ordinal===0);
  });
  await scenario('progress-replacement-and-cancel',async()=>{
   const job=ready.jobs.find(j=>j.id==='progress');await mount(job);await jump(job,42);const before=await position('progress-before');
   let rejectedRevision=false;
   await page.route(url=>url.pathname===`/api/research/tasks/${job.job_id}/translation-blocks`&&url.searchParams.get('include_index')==='true',async route=>{
    rejectedRevision=true;
    await route.fulfill({status:409,json:{detail:'译文保存版本已变化，请重新读取摘要。'}});
   },{times:1});
   const appended=await command('progress','append');await count(appended.total);await settle(42,.37);
   check('a stale translation revision is re-read without stopping progress',rejectedRevision);
   check('polling appended checkpoints keeps original block',(await position('progress-appended')).anchor.block_id===before.anchor.block_id);
   const replaced=await command('progress','replace');await page.waitForFunction(id=>lifecycleReader.getPosition()?.block_id===id&&lifecycleReader.getPosition()?.ordinal===159,before.anchor.block_id);await settle(159,.37);
   check('replaced checkpoint keeps source identity after row-order change',replaced.total===160);
   const cancellation=page.waitForResponse(r=>r.request().method()==='POST'&&new URL(r.url()).pathname.endsWith('/cancel'));
   await page.getByRole('button',{name:'取消任务',exact:true}).click();const response=await cancellation,body=await response.json();
   const done=await command('progress','cancel_done');await count(done.total);await page.waitForFunction(()=>document.querySelector('.rs-job-status')?.textContent.includes('已取消'));
   await settle(159,.37);check('cancelled job keeps saved text and reading position',(await position('progress-cancelled')).anchor.block_id===before.anchor.block_id);
   report.cancel_response={search:new URL(response.url()).search,completed_items:body.job?.completed_items?.length,result_deferred:body.job?.result_deferred,items_deferred:body.job?.items_deferred};
   check('cancel action does not download the entire saved body',body.job?.items_deferred===true&&body.job.completed_items.length===0&&new URL(response.url()).searchParams.get('include_result')==='false');
  });
  const other=await browser.newContext();await other.request.post(ready.origin+'/api/account/login',{data:{name:'LifecycleBob',password:'synthetic-lifecycle-password-456'}});
  check('other account cannot read task body',(await other.request.get(ready.origin+`/api/research/tasks/${valid.job_id}/translation-blocks`)).status()===404);
  check('other account cannot read original document',(await other.request.get(ready.origin+`/api/documents/${report.original_view?.document_id||'foreign'}`)).status()===404);await other.close();
  check('no external requests',report.external.length===0);
 }catch(error){report.errors.push(String(error));}
 finally{
  if(context&&ready)await context.tracing.stop({path:path.join(ready.output,'trace.zip')});if(browser)await browser.close();
  const stopped=new Promise(resolve=>{if(child.exitCode!==null)return resolve(child.exitCode);child.once('exit',resolve);});child.stdin.end('stop\n');let timer;const code=await Promise.race([stopped,new Promise(resolve=>{timer=setTimeout(()=>resolve('timeout'),20000);})]);clearTimeout(timer);
  if(code==='timeout'){child.kill();await stopped;report.errors.push('Owned fixture stop timeout');}else if(code!==0)report.errors.push('Fixture exit '+code+' '+stderr);
  if(ready){report.completion=JSON.parse(await fs.readFile(path.join(ready.output,'completion.json'),'utf8'));report.driver_sha256=crypto.createHash('sha256').update(await fs.readFile(__filename)).digest('hex');report.passed=!report.errors.length&&report.checks.every(c=>c.passed)&&report.completion.stopped&&report.completion.main_unchanged&&report.completion.original_source_rows_and_pdfs_unchanged&&report.completion.runtime_unchanged&&report.completion.model_calls===0;await fs.writeFile(path.join(ready.output,'report.json'),JSON.stringify(report,null,2),{flag:'wx'});console.log(JSON.stringify({output:ready.output,passed:report.passed,checks:report.checks.length,errors:report.errors}));}
 }
 if(!report.passed)process.exitCode=1;
}
main().catch(error=>{console.error(error);process.exitCode=1;});
