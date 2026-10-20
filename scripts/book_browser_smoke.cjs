'use strict';
const fs=require('node:fs/promises'),path=require('node:path'),assert=require('node:assert/strict'),crypto=require('node:crypto');
const {spawn,spawnSync}=require('node:child_process');
const {chromium}=require(process.env.PLAYWRIGHT_MODULE||'C:/Users/g2806/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright');
const root=path.resolve(__dirname,'..');
async function main(){
 const child=spawn(path.join(root,'.venv','Scripts','python.exe'),['-u',path.join(__dirname,'book_browser_fixture.py'),process.argv[2]],{cwd:root,windowsHide:true,stdio:['pipe','pipe','pipe']});
 let ready,browser,context,page,failure,stderr='';child.stderr.on('data',data=>stderr+=data);
 const report={checks:[],images:[],memory:[],errors:[],external:[],scope:'Actual original/derived PDFs, API and reader. No model calls, no language-quality claim.'};
 const check=(name,ok,detail)=>{report.checks.push({name,passed:Boolean(ok),...(detail===undefined?{}:{detail})});assert.ok(ok,name);};
 try{
  ready=await new Promise((resolve,reject)=>{let text='';const timer=setTimeout(()=>reject(Error('Book UI fixture timeout')),90000);child.stdout.on('data',data=>{text+=data;const end=text.indexOf('\n');if(end<0)return;clearTimeout(timer);try{resolve(JSON.parse(text.slice(0,end)));}catch{reject(Error('Invalid fixture frame'));}});child.once('exit',code=>{clearTimeout(timer);reject(Error('Book UI fixture exit '+code+' '+stderr));});});
  browser=await chromium.launch({headless:true,ignoreDefaultArgs:['--hide-scrollbars'],executablePath:process.env.BROWSER_EXECUTABLE||'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe'});
  context=await browser.newContext({viewport:{width:1440,height:1000}});
  await context.route('**/*',route=>{if(new URL(route.request().url()).origin!==ready.origin){report.external.push(route.request().url());return route.abort();}return route.continue();});
  await context.tracing.start({screenshots:true,snapshots:false,sources:true});
  check('synthetic UI login',(await context.request.post(ready.origin+'/api/account/login',{data:{name:'BookUIAlice',password:'local-book-ui-pass-123'}})).ok());
  page=await context.newPage();page.setDefaultTimeout(20000);page.on('pageerror',error=>report.errors.push(String(error)));
  await page.goto(ready.origin);await page.waitForFunction(()=>window.ResearchWorkspace);
  const cdp=await context.newCDPSession(page),system=await browser.newBrowserCDPSession();
  async function memory(number){
   await cdp.send('HeapProfiler.collectGarbage');await page.waitForTimeout(200);
   const processes=(await system.send('SystemInfo.getProcessInfo')).processInfo.map(p=>p.id);
   const result=spawnSync(path.join(root,'.venv','Scripts','python.exe'),['-c','import sys,json;from scripts.local_process_stats import rss;print(json.dumps({p:rss(int(p)) for p in sys.argv[1:]}))',...processes.map(String)],{cwd:root,windowsHide:true,encoding:'utf8'});
   if(result.status!==0)throw Error(result.stderr);
   const values=JSON.parse(result.stdout);report.memory.push({page:number,rss_bytes:Object.values(values).reduce((a,b)=>a+b,0),processes:values,dom:await cdp.send('Memory.getDOMCounters')});
  }
  async function go(did,number,runId){
   await page.evaluate(({did,number,runId})=>window.ResearchWorkspace.onEvidence({document_id:did,page:number,...(runId?{run_id:runId}:{})}),{did,number,runId});
   await page.waitForFunction(({did,number})=>document.querySelector('#pageImage').getAttribute('src')?.includes(`/documents/${did}/pages/${number}/image`)&&!document.querySelector('#paperPage').hidden&&document.querySelector('#pageImage').naturalWidth>0,{did,number});
  }
  async function capture(did,number,label){
   const observed=await page.evaluate(async()=>{const image=document.querySelector('#pageImage'),canvas=document.createElement('canvas');canvas.width=image.naturalWidth;canvas.height=image.naturalHeight;canvas.getContext('2d').drawImage(image,0,0);const bytes=canvas.getContext('2d').getImageData(0,0,canvas.width,canvas.height).data;return{src:image.getAttribute('src'),width:image.naturalWidth,height:image.naturalHeight,scale:Number(image.dataset.renderScale),rgba_sha256:Array.from(new Uint8Array(await crypto.subtle.digest('SHA-256',bytes))).map(v=>v.toString(16).padStart(2,'0')).join('')};});
   const response=await context.request.get(new URL(observed.src,ready.origin).href);check(label+' real PNG response',response.ok());
   const body=await response.body(),file=label+'.png';await fs.writeFile(path.join(ready.output,file),body,{flag:'wx'});
   report.images.push({label,document_id:did,page:number,...observed,file,png_sha256:crypto.createHash('sha256').update(body).digest('hex')});
  }
  const selected=new Set([1,2,4,10,12,20,73,168,271,311,399,541]);
  for(let number=1;number<=541;number++){
   await go(ready.document_id,number,ready.current_run_id);
   if(selected.has(number))await capture(ready.document_id,number,'book-'+number);
   check('original physical page '+number,await page.locator('#pageNumber').inputValue()===String(number));
   if([1,100,300,541].includes(number))await memory(number);
   if(number%100===0)console.log(JSON.stringify({event:'reader-pages',done:number}));
  }
  const warm=report.memory.find(v=>v.page===100),last=report.memory.find(v=>v.page===541);
  check('image resident growth bounded after 100 pages',last.rss_bytes-warm.rss_bytes<512*1024**2,{growth:last.rss_bytes-warm.rss_bytes,warm:warm.rss_bytes,final:last.rss_bytes});
  check('image DOM does not grow with 541 pages',last.dom.nodes-warm.dom.nodes<1000,{warm:warm.dom,final:last.dom});
  await page.locator('#firstPage').click();await page.waitForFunction(()=>document.querySelector('#pageNumber').value==='1');check('first page button',await page.locator('#firstPage').isDisabled());
  await page.locator('#lastPage').click();await page.waitForFunction(()=>document.querySelector('#pageNumber').value==='541');check('last page button',await page.locator('#lastPage').isDisabled());
  await go(ready.document_id,271,ready.current_run_id);await go(ready.other_document_id,3);await page.locator('.document-row').filter({hasText:'UnderstandingDeepLearning'}).click();
  await page.waitForFunction(()=>document.querySelector('#pageNumber').value==='271');check('per-document reading page restored',true);
  await page.locator('#pageZoom').selectOption('2');await page.waitForTimeout(100);
  const viewport=page.locator('#readerViewport'),before=await viewport.evaluate(v=>({top:v.scrollTop,left:v.scrollLeft}));
  const bounds=await viewport.boundingBox();await page.mouse.move(bounds.x+bounds.width*.7,bounds.y+bounds.height*.7);await page.mouse.down();await page.mouse.move(bounds.x+bounds.width*.3,bounds.y+bounds.height*.3,{steps:15});await page.mouse.up();
  const after=await viewport.evaluate(v=>({top:v.scrollTop,left:v.scrollLeft}));check('actual mouse pans zoomed original',after.top>before.top+20||after.left>before.left+20,{before,after});
  await page.locator('#readerFullscreenButton').click();await page.waitForTimeout(100);check('fullscreen reading entered',await page.evaluate(()=>!!document.fullscreenElement||document.querySelector('#workspace').classList.contains('reader-fullscreen')));
  await page.locator('#readerFullscreenButton').click();await page.waitForTimeout(100);
  await page.locator('#pageZoom').selectOption('1');
  await go(ready.document_id,73,ready.historical_run_id);
  const source=await context.request.get(ready.origin+`/api/documents/${ready.document_id}/pages/73?run_id=${ready.historical_run_id}`);
  const block=(await source.json()).blocks.find(b=>b.text.length>100);
  await page.evaluate(e=>window.ResearchWorkspace.onEvidence(e),{document_id:ready.document_id,run_id:ready.historical_run_id,page:73,block_id:block.id,bbox:block.bbox});
  await page.locator('#evidenceHighlight').waitFor({state:'visible'});check('historical citation highlight visible',true);
  await page.screenshot({path:path.join(ready.output,'historical-source.png')});
  for(const derived of ready.derived){
   if(derived.path==='rotated-native.pdf'){
    for(const number of [1,2]){await go(derived.id,number);await capture(derived.id,number,'rotated-'+number);check('rotated page landscape '+number,report.images.at(-1).width>report.images.at(-1).height);}
   }else{
    for(const [number,scale]of [[1,1],[2,.25]]){await go(derived.id,number);await capture(derived.id,number,'oversize-'+number);check('large page uses bounded scale '+number,report.images.at(-1).scale===scale&&report.images.at(-1).width*report.images.at(-1).height<=16000000);}
    await page.evaluate(did=>window.ResearchWorkspace.onEvidence({document_id:did,page:3}),derived.id);await page.locator('#pageError').waitFor({state:'visible'});
    check('unrenderable geometry is explicit and old image hidden',(await page.locator('#pageErrorText').textContent()).includes('安全渲染上限')&&await page.locator('#paperPage').isHidden());
   }
  }
  check('no script errors or external requests',!report.errors.length&&!report.external.length);report.passed=true;
 }catch(error){failure=error;report.errors.push(String(error));report.passed=false;if(page&&ready)await page.screenshot({path:path.join(ready.output,'failure.png')}).catch(()=>{});}
 finally{
  if(context&&ready)await context.tracing.stop({path:path.join(ready.output,'trace.zip')});if(browser)await browser.close();
  const stopped=new Promise(resolve=>{if(child.exitCode!==null)return resolve(child.exitCode);child.once('exit',resolve);});child.stdin.end('stop\n');let timer;
  const code=await Promise.race([stopped,new Promise(resolve=>{timer=setTimeout(()=>resolve('timeout'),20000);})]);clearTimeout(timer);
  if(code==='timeout'){child.kill();await stopped;report.errors.push('Fixture stop timeout');report.passed=false;}else if(code!==0){report.errors.push('Fixture exit '+code+' '+stderr);report.passed=false;}
  if(ready){report.completion=JSON.parse(await fs.readFile(path.join(ready.output,'completion.json'),'utf8'));if(!report.completion.stopped||!report.completion.main_unchanged||!report.completion.book_history_unchanged||report.completion.model_calls||!report.completion.runtime_unchanged)report.passed=false;await fs.writeFile(path.join(ready.output,'report.json'),JSON.stringify(report,null,2),{flag:'wx'});console.log(JSON.stringify({output:ready.output,passed:report.passed,checks:report.checks.length,errors:report.errors}));}
 }
 if(failure)throw failure;if(!report.passed)throw Error('Book UI acceptance failed');
}
main().catch(error=>{console.error(error);process.exitCode=1;});
