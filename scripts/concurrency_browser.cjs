/* Five real simultaneous browser contexts, one login container per actor. */
'use strict';
const fs=require('node:fs'),path=require('node:path'),assert=require('node:assert/strict');
const {chromium}=require(process.env.PLAYWRIGHT_MODULE||'C:/Users/g2806/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright');
const input=JSON.parse(fs.readFileSync(process.argv[2],'utf8')),out=input.output;
const report={contexts:[],errors:[],externalRequests:[],checks:[]};let stopped=false;
function safeError(error){
 return String(error).split('Call log:')[0].split('\n').filter(line=>!/(cookie|authorization|password)/i.test(line)).join('\n');
}
async function main(){
 const browser=await chromium.launch({headless:true,executablePath:process.env.BROWSER_EXECUTABLE||'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe'});
 const all=[];
 try{
  await Promise.all(input.actors.map(async actor=>{
   const ctx=await browser.newContext({viewport:{width:1280,height:900}});all.push(ctx);
   await ctx.route('**/*',route=>{if(new URL(route.request().url()).origin!==input.origin){report.externalRequests.push(route.request().url());return route.abort();}return route.continue();});
   const login=await ctx.request.post(input.origin+'/api/account/login',{data:{name:actor.name,password:'local-concurrency-test-password'}});
   try{assert.ok(login.ok());}finally{await login.dispose();}
   const page=await ctx.newPage();page.setDefaultTimeout(12000);page.on('pageerror',error=>{report.errors.push({actor:actor.name,error:safeError(error)});stopped=true;});
   const entry={name:actor.name,cycles:0,first_at:null,last_at:null,pause_intervals:[],disposed_api_responses:1,disposed_js_handles:0};report.contexts.push(entry);let activePause=null;
   await ctx.tracing.start({screenshots:true,snapshots:true});
   await page.goto(input.origin);await page.locator('#connectionState.is-connected').waitFor();
   const moduleButton=name=>page.locator('.module-navigation').getByRole('button',{name,exact:true});
   while(!stopped&&!fs.existsSync(path.join(out,'browser-stop'))){
    const pauseFile=path.join(out,'browser-pause');
    let token=null;try{token=fs.readFileSync(pauseFile,'utf8');}catch(error){if(error.code!=='ENOENT')throw error;}
    if(token!==null){
     if(!activePause||activePause.token!==token){
      if(activePause)activePause.end_at=Date.now();
      activePause={token,start_at:Date.now(),end_at:null};entry.pause_intervals.push(activePause);
      fs.writeFileSync(path.join(out,actor.name+'-browser-paused.json'),JSON.stringify({token,paused_at:activePause.start_at}));
     }
     await new Promise(resolve=>setTimeout(resolve,100));continue;
    }
    if(activePause){activePause.end_at=Date.now();activePause=null;}
    if(!entry.first_at)entry.first_at=Date.now();
    await moduleButton('文献管理').click();
    await page.getByRole('combobox',{name:'选择文献，含已归档文献'}).selectOption(actor.document_id);
    await page.getByRole('tab',{name:'本页文字',exact:true}).click();
    const selected=await page.waitForFunction(marker=>[...document.querySelectorAll('.rm-block-text')].some(n=>n.textContent.includes(marker)),actor.marker);
    await selected.dispose();
    entry.disposed_js_handles++;
    const text=await page.locator('.rm-page-blocks').innerText();
    assert.ok(text.includes(actor.marker),'Own private text rendered');
    assert.ok(!input.actors.filter(a=>a.name!==actor.name).some(a=>text.includes(a.marker)),'No foreign private text in current document');
    const row=page.locator('.rm-text-block').filter({hasText:'Stable historical source value = 42.'}).first();
    await row.getByRole('button',{name:'定位原文',exact:true}).click();
    await page.locator('#evidenceHighlight:not([hidden])').waitFor();
    const box=await page.locator('#evidenceHighlight').evaluate(node=>[parseFloat(node.style.left)/100,parseFloat(node.style.top)/100]);
    assert.ok(Math.abs(box[0]-actor.evidence.bbox[0])<.00001&&Math.abs(box[1]-actor.evidence.bbox[1])<.00001,'Original highlight matches source coordinates');
    await page.locator('#readerReturnButton').click();
    await page.locator('.rm-page-blocks').waitFor({state:'visible'});
    assert.ok((await page.locator('.rm-page-blocks').innerText()).includes(actor.marker),'Return preserves account and document');
    await page.getByRole('tab',{name:'标题与引用',exact:true}).click();
    await page.locator('.rm-front-matter').waitFor();
    const ds=await ctx.request.get(input.origin+'/api/datasets/'+actor.dataset_id);
    try{assert.ok(ds.ok());assert.equal((await ds.json()).dataset.rows[0][0],actor.marker);}finally{await ds.dispose();entry.disposed_api_responses++;}
    entry.cycles++;entry.last_at=Date.now();
    if(entry.cycles===1){
     await page.screenshot({path:path.join(out,actor.name+'-first.png')});
     await ctx.tracing.stop({path:path.join(out,actor.name+'-first-trace.zip')});
    }
    const progressFile=path.join(out,actor.name+'-browser-progress.json');
    fs.writeFileSync(progressFile+'.pending',JSON.stringify(entry));fs.renameSync(progressFile+'.pending',progressFile);
    await page.waitForTimeout(input.quick?100:3000);
   }
   if(activePause)activePause.end_at=Date.now();
   await page.screenshot({path:path.join(out,actor.name+'-last.png')});
  }).map(task=>task.catch(error=>{stopped=true;report.errors.push({error:safeError(error)});throw error;})));
 }catch(error){report.failure=safeError(error);}finally{
  for(const context of all)await context.close().catch(()=>{});
  await browser.close();
  const overlap=report.contexts.length===5?(Math.min(...report.contexts.map(c=>c.last_at||0))-Math.max(...report.contexts.map(c=>c.first_at||Date.now())))/1000:0;
  report.overlap_seconds=overlap;
  report.checks.push({name:'Five independent live browser contexts',passed:report.contexts.length===5&&report.contexts.every(c=>c.cycles>=2)});
  report.checks.push({name:'All contexts overlap throughout sustained workload',passed:overlap>=(input.quick?5:2700)});
  report.checks.push({name:'No script errors or external traffic',passed:!report.errors.length&&!report.externalRequests.length});
  report.checks.push({name:'Every response body and returned JS handle explicitly disposed',passed:report.contexts.every(c=>c.disposed_api_responses===c.cycles+1&&c.disposed_js_handles===c.cycles)});
  report.passed=report.checks.every(c=>c.passed)&&!report.failure;
  fs.writeFileSync(path.join(out,'browser-report.json'),JSON.stringify(report,null,2));
 }
 console.log(JSON.stringify({passed:report.passed,contexts:report.contexts.length,overlap_seconds:report.overlap_seconds}));
 if(!report.passed)process.exitCode=1;
}
main().catch(error=>{console.error(safeError(error));process.exitCode=1;});
