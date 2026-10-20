'use strict';
const fs=require('node:fs/promises'),path=require('node:path'),assert=require('node:assert/strict');
const {spawn}=require('node:child_process');
const {chromium}=require(process.env.PLAYWRIGHT_MODULE||'C:/Users/g2806/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright');
const root=path.resolve(__dirname,'..');
async function main(){
 const child=spawn(path.join(root,'.venv','Scripts','python.exe'),['-u',path.join(__dirname,'long_reader_fixture.py')],{cwd:root,windowsHide:true,stdio:['pipe','pipe','pipe']});
 let ready,browser,context,page,stderr='',failure;
 child.stderr.on('data',data=>stderr+=data);
 const report={checks:[],positions:[],native_scrolls:[],reflows:[],requests:[],errors:[],external:[],scope:'Actual API and task/bilingual UI; saved content compatibility and synthetic capacity, no model quality or actual PDF reader claim.'};
 const check=(name,passed,detail)=>{report.checks.push({name,passed:Boolean(passed),...(detail===undefined?{}:{detail})});assert.ok(passed,name);};
 try{
  ready=await new Promise((resolve,reject)=>{let text='';const timer=setTimeout(()=>reject(Error('Fixture startup timeout')),30000);child.stdout.on('data',data=>{text+=data;const end=text.indexOf('\n');if(end<0)return;clearTimeout(timer);try{resolve(JSON.parse(text.slice(0,end)));}catch{reject(Error('Invalid fixture frame'));}});child.once('exit',code=>{clearTimeout(timer);reject(Error('Fixture exit '+code));});});
  browser=await chromium.launch({headless:true,ignoreDefaultArgs:['--hide-scrollbars'],executablePath:process.env.BROWSER_EXECUTABLE||'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe'});
  context=await browser.newContext({viewport:{width:1440,height:1000}});
  await context.route('**/*',route=>{if(new URL(route.request().url()).origin!==ready.origin){report.external.push(route.request().url());return route.abort();}return route.continue();});
  await context.tracing.start({snapshots:true,screenshots:true,sources:true});
  check('synthetic owner login',(await context.request.post(ready.origin+'/api/account/login',{data:{name:'LongReaderAlice',password:'synthetic-long-reader-123'}})).ok());
  page=await context.newPage();page.setDefaultTimeout(15000);
  page.on('pageerror',error=>report.errors.push(String(error)));
  page.on('request',request=>{const url=new URL(request.url());if(url.pathname.startsWith('/api/research/tasks/'))report.requests.push({path:url.pathname,search:url.search,method:request.method()});});
  await page.goto(ready.origin);await page.waitForFunction(()=>window.ResearchTasks&&window.ResearchBilingual);
  await page.evaluate(()=>{
   const actual=window.ResearchBilingual;window.ResearchBilingual={render(...args){const controller=actual.render(...args);window.testReader=controller;return controller;}};
   window.testEvidence=[];window.testLongTasks=[];
   new PerformanceObserver(list=>testLongTasks.push(...list.getEntries().map(e=>({start:e.startTime,duration:e.duration})))).observe({type:'longtask',buffered:true});
  });
  async function mount(job){
   await page.evaluate(async job=>{
    window.testTasks?.destroy();document.querySelector('#workspace').hidden=true;
    const container=document.querySelector('#moduleRoot');container.hidden=false;document.body.classList.add('module-active');
    window.testTasks=window.ResearchTasks.mount(container,{initialJobId:job.job_id,onEvidence:evidence=>{testEvidence.push(evidence);testTasks.suspend();window.testReturnAnchor=testReader.getPosition();}});
    await testTasks.ready;
   },job);
   await page.waitForFunction(()=>document.querySelectorAll('.rb-reader').length===1&&document.querySelector('.rb-block:not(.rb-block-loading)'));
  }
  async function settle(ordinal,fraction){
   await page.waitForFunction(({ordinal,fraction})=>['source','translation'].every(side=>{
    const pane=document.querySelector(`.rb-panel-${side} .rb-scroll`),block=pane?.querySelector(`.rb-block[data-ordinal="${ordinal}"]:not(.rb-block-loading)`);
    if(!block)return false;
    return Math.abs(pane.scrollTop-block.offsetTop-fraction*block.getBoundingClientRect().height)<=pane.clientHeight*.1;
   }),{ordinal,fraction});
   await page.evaluate(()=>new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve))));
  }
  async function sample(ordinal,fraction){
   return page.evaluate(({ordinal,fraction})=>Object.fromEntries(['source','translation'].map(side=>{
    const pane=document.querySelector(`.rb-panel-${side} .rb-scroll`),block=pane.querySelector(`[data-ordinal="${ordinal}"]`);
    return[side,{ordinal:Number(block.dataset.ordinal),dom:pane.querySelectorAll('.rb-block').length,
     error:Math.abs(pane.scrollTop-block.offsetTop-fraction*block.getBoundingClientRect().height),height:pane.clientHeight}];
   })),{ordinal,fraction});
  }
  async function jump(job,ordinal,side,fraction=.37){
   check(`${job.id} ${side} jump ${ordinal} accepted`,await page.evaluate(({id,side,fraction})=>testReader.syncTo(id,{side,fraction}),{id:job.block_ids[ordinal],side,fraction}));
   await settle(ordinal,fraction);
   const value=await sample(ordinal,fraction);
   check(`${job.id} ${side} DOM bounded ${ordinal}`,value.source.dom<=120&&value.translation.dom<=120);
   report.positions.push({case:job.id,side,ordinal,fraction,...value});
  }
  const long=ready.jobs.find(j=>j.id==='mixed-5000');await mount(long);
  const begin=await page.evaluate(()=>performance.now());
  const jumpCount=process.argv.includes('--drag-only')?0:100;report.jumps_per_direction=jumpCount;
  for(const side of ['source','translation'])for(let i=0;i<jumpCount;i++){
   const ordinal=(i*733+(side==='translation'?199:0))%5000;
   await jump(long,ordinal,side);
   // A native scroll event drives synchronization independently of syncTo.
   await page.evaluate(({ordinal,side})=>{const pane=document.querySelector(`.rb-panel-${side} .rb-scroll`),block=pane.querySelector(`[data-ordinal="${ordinal}"]`);pane.dispatchEvent(new Event('pointerdown'));pane.scrollTop=block.offsetTop+block.getBoundingClientRect().height*.61;},{ordinal,side});
   await settle(ordinal,.61);
   report.native_scrolls.push({case:long.id,side,ordinal,fraction:.61,...await sample(ordinal,.61)});
   if(i%50===49)console.log(JSON.stringify({phase:'bidirectional-jumps',side,completed:i+1}));
  }
  const end=await page.evaluate(()=>performance.now());
  report.scroll_long_tasks=await page.evaluate(({begin,end})=>testLongTasks.filter(task=>task.start>=begin&&task.start<=end),{begin,end});
  check('jump and native-scroll interval long tasks <=200ms',report.scroll_long_tasks.every(task=>task.duration<=200),report.scroll_long_tasks);
  await jump(long,899,'source');
  await page.evaluate(()=>{for(const text of document.querySelectorAll('.rb-text')){text.style.fontSize='21px';text.style.lineHeight='1.7';}});await settle(899,.37);
  check('font reflow retains same block and fraction',true);
  report.reflows.push({kind:'font',ordinal:899,fraction:.37,...await sample(899,.37)});
  await page.evaluate(async()=>{const block=document.querySelector('.rb-panel-source [data-ordinal="899"]'),image=new Image(),url=URL.createObjectURL(new Blob(['<svg xmlns="http://www.w3.org/2000/svg" width="400" height="600"><rect width="400" height="600" fill="gray"/></svg>'],{type:'image/svg+xml'}));image.src=url;image.style.width='100%';image.style.height='600px';block.append(image);try{await image.decode();}finally{URL.revokeObjectURL(url);}});
  await settle(899,.37);check('late image reflow retains same block and fraction',true);
  report.reflows.push({kind:'image',ordinal:899,fraction:.37,...await sample(899,.37)});
  for(const width of [1024,760,390,1440]){await page.setViewportSize({width,height:1000});await settle(899,.37);check(`viewport ${width} anchor retained`,true);report.reflows.push({kind:'viewport-'+width,ordinal:899,fraction:.37,...await sample(899,.37)});if(width===390)await page.screenshot({path:path.join(ready.output,'long-mobile.png')});}
  await page.locator('.rb-panel-source [data-ordinal="899"] .rb-evidence').evaluate(button=>button.click());
  check('source callback exact block',await page.evaluate(id=>testEvidence.at(-1)?.block_id===id,long.block_ids[899]));
  await page.evaluate(()=>testTasks.resume());await settle(899,.37);check('source return restores reading anchor',true);
  // Real mouse drag on the scrollbar, in addition to programmatic jumps/wheel events.
  await jump(long,0,'source',0);
  await page.locator('.rb-panel-source .rb-scroll').scrollIntoViewIfNeeded();
  const box=await page.locator('.rb-panel-source .rb-scroll').boundingBox();
  const gutter=await page.locator('.rb-panel-source .rb-scroll').evaluate(pane=>pane.offsetWidth-pane.clientWidth);
  check('native scrollbar has a reserved visible gutter',gutter>0,{gutter});
  const dragX=box.x+box.width-gutter/2;
  await page.screenshot({path:path.join(ready.output,'before-drag.png')});
  await page.mouse.move(dragX,box.y+18);await page.mouse.down();await page.mouse.move(dragX,box.y+box.height*.8,{steps:35});await page.mouse.up();
  await page.waitForTimeout(300);
  const dragged=await page.evaluate(()=>({position:testReader.getPosition(),top:document.querySelector('.rb-panel-source .rb-scroll').scrollTop}));
  check('mouse scrollbar drag moved through long result',dragged.top>5000,dragged);
  if(dragged.position)await settle(dragged.position.ordinal,dragged.position.fraction);
  await page.screenshot({path:path.join(ready.output,'long-desktop.png')});
  for(const job of ready.jobs.filter(j=>j.id!=='mixed-5000')){
   await mount(job);
   if(job.id==='duplicate-1000'){
    check('ambiguous public lookup rejected',await page.evaluate(id=>!testReader.syncTo(id),job.block_ids[1]));
    await page.evaluate(({id,ordinal})=>testReader.syncTo(id,{ordinal}),{id:job.block_ids[999],ordinal:999});
    await settle(999,0);
    check('duplicate text retained with disabled evidence',await page.locator('.rb-panel-source [data-ordinal="999"] .rb-evidence').isDisabled());
   }else{
    await jump(job,job.total-1,'translation');
    if(job.id==='wrong-run-1000')check('wrong run cannot navigate',await page.locator('.rb-panel-source [data-ordinal="999"] .rb-evidence').isDisabled());
    if(job.id==='missing-5000'){
     await jump(job,4995,'source');
     check('missing translation is explicit',(await page.locator('.rb-panel-translation [data-ordinal="4995"] .rb-text').textContent()).includes('尚无已保存译文'));
    }
   }
  }
  const faultJob=ready.jobs.find(j=>j.id==='mixed-1000');
  let fault=null,faultCount=0;
  await context.route(/\/translation-blocks\?/,route=>{
   const url=new URL(route.request().url());
   if(fault&&Number(url.searchParams.get('offset'))===fault.offset){
    const current=fault;fault=null;faultCount++;
    return route.fulfill({status:current.status,contentType:'application/json',body:JSON.stringify({detail:current.status===409?'译文保存版本已变化，请刷新任务。':'受控本地分页故障'})});
   }
   return route.continue();
  });
  await mount(faultJob);fault={offset:700,status:503};
  await page.evaluate(()=>testReader.syncTo('synthetic-block-00733',{fraction:.37}));
  await page.locator('.rb-retry').first().waitFor();
  await page.waitForTimeout(120);check('failed page does not retry automatically',faultCount===1);
  await page.locator('.rb-retry').first().evaluate(button=>button.click());await settle(733,.37);check('explicit retry restores exact saved block',true);
  await mount(faultJob);fault={offset:500,status:409};
  await page.evaluate(()=>testReader.syncTo('synthetic-block-00533',{fraction:.37}));
  await page.waitForFunction(()=>document.querySelector('.rb-block-loading .rb-text')?.textContent.includes('保存版本已变化'));
  check('stale version offers refresh rather than ineffective retry',await page.locator('.rb-retry').count()===0);
  await page.getByRole('button',{name:'刷新资料与任务',exact:true}).click();await settle(533,.37);
  check('explicit refresh recovers stale page and reading anchor',true);
  check('no automatic full-result GET',report.requests.every(r=>r.method!=='GET'||!/^\/api\/research\/tasks\/[^/]+$/.test(r.path)||r.search.includes('include_result=false')));
  check('body API page bounds',report.requests.filter(r=>r.path.endsWith('/translation-blocks')).every(r=>Number(new URLSearchParams(r.search).get('limit'))<=200));
  check('no script errors or external requests',!report.errors.length&&!report.external.length);
  report.passed=true;
 }catch(error){failure=error;report.errors.push(String(error));report.passed=false;if(page&&ready)await page.screenshot({path:path.join(ready.output,'failure.png')}).catch(()=>{});}
 finally{
  if(context&&ready)await context.tracing.stop({path:path.join(ready.output,'trace.zip')});
  if(browser)await browser.close();
  const stopped=new Promise(resolve=>{if(child.exitCode!==null)return resolve(child.exitCode);child.once('exit',resolve);});
  child.stdin.end('stop\n');
  let stopTimer;
  const code=await Promise.race([stopped,new Promise(resolve=>{stopTimer=setTimeout(()=>resolve('timeout'),15000);})]);
  clearTimeout(stopTimer);
  if(code==='timeout'){child.kill();await stopped;report.errors.push('Fixture stop timeout');report.passed=false;}
  else if(code!==0){report.errors.push('Fixture exit '+code+' '+stderr);report.passed=false;}
  if(ready){report.completion=JSON.parse(await fs.readFile(path.join(ready.output,'completion.json'),'utf8'));if(!report.completion.stopped||!report.completion.main_unchanged||!report.completion.runtime_unchanged||report.completion.model_calls)report.passed=false;await fs.writeFile(path.join(ready.output,'report.json'),JSON.stringify(report,null,2),{flag:'wx'});console.log(JSON.stringify({output:ready.output,passed:report.passed,checks:report.checks.length,errors:report.errors}));}
 }
 if(failure)throw failure;if(!report.passed)throw Error('Long reader acceptance failed');
}
main().catch(error=>{console.error(error);process.exitCode=1;});
