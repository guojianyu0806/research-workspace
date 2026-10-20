/* Actual frozen PDFs and API; failures before the fix are retained as failures. */
'use strict';
const fs=require('node:fs/promises'),path=require('node:path'),crypto=require('node:crypto'),assert=require('node:assert/strict');
const {spawn,spawnSync}=require('node:child_process');
const {chromium}=require(process.env.PLAYWRIGHT_MODULE||'C:/Users/g2806/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright');
const root=path.resolve(__dirname,'..');
async function main(){
 const child=spawn(path.join(root,'.venv','Scripts','python.exe'),['-u',path.join(__dirname,'book_browser_fixture.py'),process.argv[2]],{cwd:root,windowsHide:true,stdio:['pipe','pipe','pipe']});
 let ready,browser,context,page,release=null,hold=false,faultScale=null,stderr='';child.stderr.on('data',data=>stderr+=data);
 const report={checks:[],errors:[],external:[],images:[],fault_requests:[],scope:'Actual derived oversized/rotated PDFs and manage UI; geometry/return checks, not model or OCR quality.',driver_sha256:crypto.createHash('sha256').update(await fs.readFile(__filename)).digest('hex')};
 function rawSnapshot(){
  const code="from pathlib import Path\nimport sys,json,sqlite3,hashlib\nroot=Path(sys.argv[1]).resolve()\nassert root.is_relative_to(Path('artifacts/book-reader-ui').resolve())\nresult={}\nfor path in sorted((root/'workspace').rglob('library.sqlite3')):\n with sqlite3.connect(path.as_uri()+'?mode=ro',uri=True) as db:\n  result[str(path.relative_to(root))]={table:hashlib.sha256(json.dumps(db.execute('SELECT * FROM '+table+' ORDER BY rowid').fetchall(),ensure_ascii=False,separators=(',',':')).encode()).hexdigest() for table in ('documents','parse_runs','pages','blocks')}\nresult['originals']={str(path.relative_to(root)):hashlib.sha256(path.read_bytes()).hexdigest() for path in sorted((root/'workspace').rglob('originals/*.pdf'))}\nprint(json.dumps(result,sort_keys=True))";
  const run=spawnSync(path.join(root,'.venv','Scripts','python.exe'),['-c',code,ready.output],{cwd:root,windowsHide:true,encoding:'utf8'});
  if(run.status!==0)throw Error(run.stderr);return JSON.parse(run.stdout);
 }
 const check=(name,ok,detail)=>{report.checks.push({name,passed:Boolean(ok),...(detail===undefined?{}:{detail})});assert.ok(ok,name);};
 async function scenario(name,fn){try{await fn();}catch(error){report.errors.push(name+': '+String(error));if(ready&&page)await page.screenshot({path:path.join(ready.output,name+'-failure.png')}).catch(()=>{});}finally{if(page)await page.getByRole('button',{name:'关闭预览',exact:true}).evaluateAll(buttons=>buttons.forEach(b=>b.click())).catch(()=>{});}}
 try{
  ready=await new Promise((resolve,reject)=>{let text='';const timer=setTimeout(()=>reject(Error('Fixture startup timeout')),90000);child.stdout.on('data',data=>{text+=data;const end=text.indexOf('\n');if(end<0)return;clearTimeout(timer);try{resolve(JSON.parse(text.slice(0,end)));}catch{reject(Error('Invalid fixture frame'));}});child.once('exit',code=>{clearTimeout(timer);reject(Error('Fixture exit '+code+' '+stderr));});});
  browser=await chromium.launch({headless:true,executablePath:process.env.BROWSER_EXECUTABLE||'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe'});
  context=await browser.newContext({viewport:{width:1440,height:1000}});await context.tracing.start({snapshots:true,screenshots:true,sources:true});
  await context.route('**/*',route=>{if(new URL(route.request().url()).origin!==ready.origin){report.external.push(route.request().url());return route.abort();}return route.continue();});
  check('synthetic owner login',(await context.request.post(ready.origin+'/api/account/login',{data:{name:'BookUIAlice',password:'local-book-ui-pass-123'}})).ok());
  page=await context.newPage();page.setDefaultTimeout(10000);page.on('pageerror',error=>report.errors.push('pageerror: '+String(error)));
  report.data_before=rawSnapshot();
  await page.goto(ready.origin);await page.waitForFunction(()=>window.ResearchWorkspace);
  await page.locator('[data-module="manage"]').click();
  const large=ready.derived.find(d=>d.path==='oversize-native.pdf'),rotated=ready.derived.find(d=>d.path==='rotated-native.pdf');
  async function read(did,number){
   const select=page.getByLabel('选择文献，含已归档文献',{exact:true});if(await select.inputValue()!==did)await select.selectOption(did);
   await page.getByRole('tab',{name:'本页文字',exact:true}).click();
   await page.getByRole('spinbutton',{name:'原文页码',exact:true}).fill(String(number));await page.getByRole('button',{name:'读取本页文字',exact:true}).click();
   await page.waitForFunction(number=>document.querySelector('.rm-text-block .rm-field-title')?.textContent.includes(`第 ${number} 页`)&&document.querySelector('.rm-page-blocks')?.getAttribute('aria-busy')!=='true',number);
  }
  async function capture(selector,label,did,number){
   const value=await page.locator(selector).evaluate(async image=>{const canvas=document.createElement('canvas');canvas.width=image.naturalWidth;canvas.height=image.naturalHeight;canvas.getContext('2d').drawImage(image,0,0);const bytes=canvas.getContext('2d').getImageData(0,0,canvas.width,canvas.height).data;return{src:image.src,width:image.naturalWidth,height:image.naturalHeight,rgba_sha256:Array.from(new Uint8Array(await crypto.subtle.digest('SHA-256',bytes))).map(v=>v.toString(16).padStart(2,'0')).join('')};});
   const response=await context.request.get(value.src);check(label+' real PNG response',response.ok());const bytes=await response.body();
   await fs.writeFile(path.join(ready.output,label+'.png'),bytes,{flag:'wx'});report.images.push({label,document_id:did,page:number,...value,scale:Number(new URL(value.src).searchParams.get('scale')),file:label+'.png',png_sha256:crypto.createHash('sha256').update(bytes).digest('hex')});
  }
  for(const [number,thumbScale,modalScale]of [[1,.5,1],[2,.25,.25]])await scenario('large-'+number,async()=>{
   await read(large.id,number);await page.waitForFunction(()=>{const img=document.querySelector('.rm-preview-image');return img&&!img.hidden&&img.complete&&img.naturalWidth>0;});
   await capture('.rm-preview-image','thumb-'+number,large.id,number);
   check('thumbnail scale '+number,report.images.at(-1).scale===thumbScale&&report.images.at(-1).width*report.images.at(-1).height<=16000000);
   await page.locator('.rm-preview-block').nth(3).evaluate(button=>button.click());
   const saved=await page.evaluate(()=>{const root=document.querySelector('#moduleRoot'),text=root.querySelectorAll('.rm-block-text')[3];root.scrollTop=520;const range=document.createRange();range.setStart(text.firstChild,0);range.setEnd(text.firstChild,Math.min(24,text.firstChild.length));const selection=getSelection();selection.removeAllRanges();selection.addRange(range);window.largePreviewShell=root.firstChild;return{scroll:root.scrollTop,selection:selection.toString(),paragraph:text.textContent};});
   check('meaningful saved source state '+number,saved.scroll>100&&saved.selection.length>0);
   await page.locator('.rm-preview-page').evaluate(button=>button.click());
   await page.waitForFunction(()=>{const img=document.querySelector('.rm-source-image');return img&&!img.hidden&&img.complete&&img.naturalWidth>0;});
   await capture('.rm-source-image','modal-'+number,large.id,number);
   check('modal scale '+number,report.images.at(-1).scale===modalScale&&report.images.at(-1).width*report.images.at(-1).height<=16000000);
   const source=await context.request.get(ready.origin+`/api/documents/${large.id}/pages/${number}`),box=(await source.json()).blocks[3].bbox;
   const geometry=await page.locator('.rm-source-paper').evaluate((paper,box)=>{const p=paper.getBoundingClientRect(),h=paper.querySelector('.rm-source-highlight').getBoundingClientRect();return{maxError:Math.max(Math.abs(h.left-p.left-p.width*box[0]),Math.abs(h.top-p.top-p.height*box[1]),Math.abs(h.width-p.width*(box[2]-box[0])),Math.abs(h.height-p.height*(box[3]-box[1]))),width:p.width,height:p.height};},box);
   check('modal source box matches original normalized coordinates '+number,geometry.maxError<3,geometry);
   await page.getByLabel('预览缩放',{exact:true}).selectOption('2');check('CSS zoom keeps bounded raster '+number,await page.locator('.rm-source-image').evaluate(img=>img.naturalWidth*img.naturalHeight<=16000000));
   for(const width of [760,390,1440]){await page.setViewportSize({width,height:1000});await page.waitForTimeout(60);const bounds=await page.locator('.rm-source-dialog').evaluate(dialog=>{const box=dialog.getBoundingClientRect();return{left:box.left,right:box.right,top:box.top,bottom:box.bottom,width:innerWidth,height:innerHeight,overflow:document.documentElement.scrollWidth-innerWidth};});check(`large ${number} modal frame within ${width}`,bounds.left>=-1&&bounds.right<=width+1&&bounds.top>=-1&&bounds.bottom<=1001&&bounds.overflow<=1,bounds);}
   await page.screenshot({path:path.join(ready.output,'modal-page-'+number+'.png')});
   await page.getByRole('button',{name:'关闭预览',exact:true}).click();
   const restored=await page.evaluate(()=>{const root=document.querySelector('#moduleRoot');return{same:root.firstChild===largePreviewShell,scroll:root.scrollTop,selection:getSelection().toString(),paragraph:root.querySelectorAll('.rm-block-text')[3].textContent};});
   check('closing restores original paragraph and selection '+number,restored.same&&restored.paragraph===saved.paragraph&&restored.selection===saved.selection);
   check('closing restores source scroll '+number,Math.abs(restored.scroll-saved.scroll)<3,{saved:saved.scroll,restored:restored.scroll});
  });
  await scenario('too-large',async()=>{await read(large.id,3);await page.waitForFunction(()=>document.querySelector('.rm-preview-info')?.textContent.includes('安全预览'));check('oversize limit leaves body readable',await page.locator('.rm-block-text').count()>0&&await page.locator('.rm-preview-image').isHidden());check('permanent geometry error has no retry loop',await page.getByRole('button',{name:'重试预览',exact:true}).isHidden());});
  await scenario('rotated',async()=>{await read(rotated.id,1);await page.waitForFunction(()=>document.querySelector('.rm-preview-image')?.naturalWidth>0&&!document.querySelector('.rm-preview-image').hidden);await capture('.rm-preview-image','rotated-thumb',rotated.id,1);await page.locator('.rm-preview-page').evaluate(button=>button.click());await page.waitForFunction(()=>document.querySelector('.rm-source-image')?.naturalWidth>0&&!document.querySelector('.rm-source-image').hidden);await capture('.rm-source-image','rotated-modal',rotated.id,1);check('rotated modal keeps landscape geometry',await page.locator('.rm-source-image').evaluate(img=>img.naturalWidth>img.naturalHeight));});
  await context.route(/\/image\?/,async route=>{
   const url=new URL(route.request().url()),scale=Number(url.searchParams.get('scale'));
   if(url.origin!==ready.origin)return route.abort();
   if(url.pathname.includes(`/documents/${large.id}/pages/1/image`)){
    if(faultScale===scale){report.fault_requests.push({scale,status:503});return route.fulfill({status:503,contentType:'application/json',body:JSON.stringify({detail:'Controlled local preview transport failure'})});}
    if(hold&&scale===1){hold=false;await new Promise(resolve=>{release=resolve;});}
   }
   return route.continue();
  });
  await scenario('transport-retry',async()=>{
   faultScale=.5;await read(large.id,1);await page.getByRole('button',{name:'重试预览',exact:true}).waitFor({state:'visible'});
   const count=report.fault_requests.length;await page.waitForTimeout(180);check('thumbnail transport failure has no automatic retry storm',report.fault_requests.length===count);
   faultScale=null;await page.getByRole('button',{name:'重试预览',exact:true}).click();await page.waitForFunction(()=>document.querySelector('.rm-preview-image')?.naturalWidth>0&&!document.querySelector('.rm-preview-image').hidden);check('explicit thumbnail retry loads real page',true);
   faultScale=1;await page.locator('.rm-preview-page').evaluate(button=>button.click());await page.getByRole('button',{name:'重试大图',exact:true}).waitFor({state:'visible'});
   faultScale=null;await page.getByRole('button',{name:'重试大图',exact:true}).click();await page.waitForFunction(()=>document.querySelector('.rm-source-image')?.naturalWidth>0&&!document.querySelector('.rm-source-image').hidden);check('explicit modal retry uses actual safe raster',Number(new URL(await page.locator('.rm-source-image').getAttribute('src'),ready.origin).searchParams.get('scale'))===1);
  });
  await scenario('late-modal',async()=>{
   // A fresh context ensures this is a genuinely pending first image load.
   // Already decoded memory-cache images cannot exercise a delayed response.
   await context.tracing.stop({path:path.join(ready.output,'large-preview-main-trace.zip')});await context.close();
   context=await browser.newContext({viewport:{width:1440,height:1000}});await context.tracing.start({snapshots:true,screenshots:true,sources:true});
   await context.route('**/*',async route=>{
    const url=new URL(route.request().url());if(url.origin!==ready.origin){report.external.push(route.request().url());return route.abort();}
    if(hold&&url.pathname.includes(`/documents/${large.id}/pages/1/image`)&&Number(url.searchParams.get('scale'))===1){hold=false;await new Promise(resolve=>{release=resolve;});}
    return route.continue();
   });
   await context.request.post(ready.origin+'/api/account/login',{data:{name:'BookUIAlice',password:'local-book-ui-pass-123'}});
   page=await context.newPage();page.setDefaultTimeout(10000);page.on('pageerror',error=>report.errors.push('pageerror: '+String(error)));
   await page.goto(ready.origin);await page.waitForFunction(()=>window.ResearchWorkspace);await page.locator('[data-module="manage"]').click();await read(large.id,1);
   hold=true;release=null;await page.locator('.rm-preview-page').evaluate(button=>button.click());
   const limit=Date.now()+5000;while(!release){if(Date.now()>limit)throw Error('Expected pending real image request');await new Promise(resolve=>setTimeout(resolve,20));}
   await page.getByRole('button',{name:'关闭预览',exact:true}).click();await read(large.id,2);
   const delivered=page.waitForResponse(response=>{const u=new URL(response.url());return u.pathname.includes(`/documents/${large.id}/pages/1/image`)&&Number(u.searchParams.get('scale'))===1;});
   release();release=null;const response=await delivered;await response.finished();await page.waitForTimeout(100);
   check('closed-dialog late response actually arrived',response.status()===200);
   check('late image cannot reopen closed dialog',await page.locator('.rm-source-dialog').count()===0);
   check('late image cannot overwrite current source page',(await page.locator('.rm-preview-caption').textContent()).includes('第 2 页')&&new URL(await page.locator('.rm-preview-image').getAttribute('src'),ready.origin).pathname.includes('/pages/2/image'));
  });
  check('no external requests',report.external.length===0);
 }catch(error){report.errors.push(String(error));}
 finally{
  // Unblock a held test-only response even if an assertion failed.
  if(typeof release==='function')release();
  if(context&&ready)await context.tracing.stop({path:path.join(ready.output,'large-preview-trace.zip')});if(browser)await browser.close();
  if(ready&&report.data_before){report.data_after=rawSnapshot();check('all document/run/page/block rows and original files unchanged',JSON.stringify(report.data_before)===JSON.stringify(report.data_after));}
  const stopped=new Promise(resolve=>{if(child.exitCode!==null)return resolve(child.exitCode);child.once('exit',resolve);});child.stdin.end('stop\n');let timer;const code=await Promise.race([stopped,new Promise(resolve=>{timer=setTimeout(()=>resolve('timeout'),20000);})]);clearTimeout(timer);
  if(code==='timeout'){child.kill();await stopped;report.errors.push('Fixture stop timeout');}else if(code!==0)report.errors.push('Fixture exit '+code+' '+stderr);
  if(ready){report.completion=JSON.parse(await fs.readFile(path.join(ready.output,'completion.json'),'utf8'));report.passed=report.errors.length===0&&report.checks.every(c=>c.passed)&&report.completion.stopped&&report.completion.main_unchanged&&report.completion.book_history_unchanged&&report.completion.runtime_unchanged&&report.completion.model_calls===0;await fs.writeFile(path.join(ready.output,'large-preview-report.json'),JSON.stringify(report,null,2),{flag:'wx'});console.log(JSON.stringify({output:ready.output,passed:report.passed,checks:report.checks.length,errors:report.errors}));}
 }
 if(!report.passed)process.exitCode=1;
}
main().catch(error=>{console.error(error);process.exitCode=1;});
