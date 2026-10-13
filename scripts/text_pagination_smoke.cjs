/* Offline regression for text-page loading and scroll stability. Runs the real
 * manage module with synthetic long/short pages. Does not contact the service,
 * models or persisted data. Existing Playwright and Edge only; no installation.
 * --baseline records the original loading behavior without expecting a fix.
 */
'use strict';
const fs = require('node:fs');
const path = require('node:path');
const crypto = require('node:crypto');
const assert = require('node:assert/strict');
const {execFileSync} = require('node:child_process');
const {chromium} = require(process.env.PLAYWRIGHT_MODULE || 'C:/Users/g2806/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright');
const root = path.resolve(__dirname, '..');
const origin = 'http://127.0.0.1:8765';
const baseline = process.argv.includes('--baseline');
const output = path.join(root, 'artifacts/browser', `text-pagination-${baseline?'baseline-':''}${new Date().toISOString().replace(/[:.]/g,'-')}-${crypto.randomBytes(3).toString('hex')}`);
fs.mkdirSync(output,{recursive:true});
const names = ['notices.js','styles.css','shell.css','manage.css','manage.js'];
const assets = new Map(names.map(name=>[name,fs.readFileSync(path.join(root,'research_workspace/static',name))]));
const report = {startedAt:new Date().toISOString(),output,baseline,checks:[],screenshots:[],pageErrors:[],unexpectedRequests:[],source:{gitHead:execFileSync('git',['rev-parse','HEAD'],{cwd:root,encoding:'utf8',windowsHide:true}).trim(),sha256:{}},limitations:['Synthetic pages test rendering and navigation only; no PDF extraction, model request or persisted user data is involved.']};
for(const [name,body] of assets) report.source.sha256[name]=crypto.createHash('sha256').update(body).digest('hex');
report.source.sha256['scripts/text_pagination_smoke.cjs']=crypto.createHash('sha256').update(fs.readFileSync(__filename)).digest('hex');
const doc={id:'fixture-pagination',title:'Synthetic text pagination',filename:'synthetic.pdf',authors:['Fixture'],page_count:5,created_at:'2026-10-13T00:00:00Z',available_run:{id:'run-pagination',version:1,status:'completed'},current_run:{id:'run-pagination',version:1,status:'completed'}};
function pageData(number){
  const count=number===3?1:18;
  const blocks=Array.from({length:count},(_,index)=>({id:`p${number}-b${index}`,page:number,ordinal:index,bbox:[.1,.12+(index%5)*.12,.9,.21+(index%5)*.12],text:`PAGE ${number} BLOCK ${index+1}. `+(number===3?'Short page.':'A synthetic paragraph preserves enough readable content to reproduce a long extracted PDF page. ').repeat(number===3?1:8)}));
  return {document_id:doc.id,run_id:'run-pagination',page:number,status:'completed',width:600,height:800,blocks,text:blocks.map(b=>b.text).join('\n\n')};
}
const structure={headings:[{id:'fixture-chapter',title:'Jump to page four paragraph nine',level:1,evidence:{document_id:doc.id,run_id:'run-pagination',page:4,block_id:'p4-b8',bbox:[.1,.48,.9,.57]}}],references:[],citations:[],limitations:[]};
const svg='<svg xmlns="http://www.w3.org/2000/svg" width="600" height="800"><rect width="600" height="800" fill="white"/><text x="50" y="100" fill="#173e41">Synthetic source page</text></svg>';
const harness=`<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">${names.filter(n=>n.endsWith('.css')).map(n=>`<link rel="stylesheet" href="/static/${n}">`).join('')}</head><body><main id="moduleRoot" class="module-root"></main><script src="/static/notices.js"></script><script src="/static/manage.js"></script><script>
window.fixtureTranslations=[];window.fixtureEvidence=[];
window.fixtureController=ResearchManage.mount(document.querySelector('#moduleRoot'),{api:async(url,init)=>{if(init?.method&&init.method!=='GET')throw Error('Writes forbidden');const r=await fetch(url);if(!r.ok)throw Error('Synthetic page request failed');return r.json();},onTranslate:p=>fixtureTranslations.push(p),onEvidence:p=>fixtureEvidence.push(p)});window.fixtureReady=fixtureController.ready;
</script></body></html>`;
let holdPages=new Set(),failPages=new Set();
const pending=[];
const requests=[];
function check(name,passed,detail){report.checks.push({name,passed:Boolean(passed),...(detail===undefined?{}:{detail})});if(!process.argv.includes('--collect'))assert.ok(passed,`${name}: ${JSON.stringify(detail)}`);}
async function main(){
  const browser=await chromium.launch({headless:true,executablePath:process.env.BROWSER_EXECUTABLE||'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe'});
  const context=await browser.newContext({viewport:{width:1440,height:800},reducedMotion:'reduce'});
  const page=await context.newPage();page.setDefaultTimeout(10000);
  page.on('pageerror',e=>report.pageErrors.push(String(e)));
  await context.route('**/*',async route=>{
    const request=route.request(),url=new URL(request.url()),p=url.pathname;
    if(url.origin!==origin||!['GET','HEAD'].includes(request.method())){report.unexpectedRequests.push(request.url());return route.abort();}
    if(p==='/')return route.fulfill({status:200,contentType:'text/html; charset=utf-8',body:harness});
    if(p.startsWith('/static/')&&assets.has(path.basename(p)))return route.fulfill({status:200,contentType:p.endsWith('.css')?'text/css':'text/javascript',body:assets.get(path.basename(p))});
    let value;
    if(p==='/api/documents')value={documents:[doc]};
    else if(p.endsWith('/versions'))value={family:{documents:[]},candidates:[]};
    else if(p.endsWith('/structure'))value=structure;
    else {
      const match=p.match(/^\/api\/documents\/fixture-pagination\/pages\/(\d+)(\/image)?$/);
      if(match){
        const number=Number(match[1]);
        if(match[2])return route.fulfill({status:200,contentType:'image/svg+xml',body:svg});
        requests.push(number);
        const fail=failPages.delete(number);
        const send=()=>route.fulfill({status:fail?503:200,contentType:'application/json',body:JSON.stringify(fail?{detail:'Synthetic page request failed'}:pageData(number))});
        if(holdPages.has(number)){pending.push({number,send});return;}
        return send();
      }
      report.unexpectedRequests.push(request.url());return route.abort();
    }
    return route.fulfill({status:200,contentType:'application/json',body:JSON.stringify(value)});
  });
  const input=()=>page.getByRole('spinbutton',{name:'原文页码',exact:true});
  const firstText=()=>page.locator('.rm-block-text').first();
  async function loaded(number){await page.waitForFunction(number=>document.querySelector('.rm-block-text')?.textContent.startsWith(`PAGE ${number} BLOCK 1.`),number);await settle();}
  async function settle(){await page.evaluate(()=>new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve))));}
  async function geometry(){return page.evaluate(()=>{const root=document.querySelector('#moduleRoot'),form=document.querySelector('.rm-page-form');return {top:root.scrollTop,height:root.scrollHeight,client:root.clientHeight,toolbar:form?.getBoundingClientRect().top,bodyHeight:document.querySelector('.rm-page-blocks')?.getBoundingClientRect().height,focus:document.activeElement?.getAttribute('aria-label')||document.activeElement?.textContent,text:document.querySelector('.rm-block-text')?.textContent,blocks:document.querySelectorAll('.rm-text-block').length,selection:getSelection().toString(),bodyBusy:document.querySelector('.rm-page-blocks')?.getAttribute('aria-busy')};});}
  async function position(){await page.evaluate(()=>{const root=document.querySelector('#moduleRoot'),form=document.querySelector('.rm-page-form');root.scrollTop+=form.getBoundingClientRect().top-root.getBoundingClientRect().top-80;});await settle();return geometry();}
  async function submit(number){await input().fill(String(number));await input().press('Enter');}
  async function programmaticSubmit(number){await page.evaluate(number=>{const input=document.querySelector('.rm-page-number');input.value=number;document.querySelector('.rm-page-form').requestSubmit();},number);}
  async function held(number){await page.waitForFunction(number=>Number(document.querySelector('.rm-page-number').value)===number,number);for(let i=0;i<100&&!pending.some(p=>p.number===number);i++)await new Promise(r=>setTimeout(r,10));assert.ok(pending.some(p=>p.number===number),`held page ${number} request recorded`);await settle();}
  async function release(number){const index=pending.findIndex(p=>p.number===number);assert.ok(index>=0,`pending page ${number}`);await pending.splice(index,1)[0].send();}
  async function screenshot(name){const filename=path.join(output,name);await page.screenshot({path:filename});report.screenshots.push(filename);}
  async function reopen(){holdPages.clear();failPages.clear();await page.goto(origin);await page.evaluate(()=>fixtureReady);await page.getByRole('tab',{name:'本页文字',exact:true}).click();await loaded(1);}
  try{
    await reopen();
    const before=await position();
    holdPages.add(2);await submit(2);await held(2);const during=await geometry();
    if(baseline){
      report.reproduction={before,during,scrollDelta:during.top-before.top,toolbarDelta:during.toolbar-before.toolbar,oldContentRetained:during.text===before.text};
      await screenshot('baseline-loading.png');await release(2);await loaded(2);report.reproduction.after=await geometry();
      check('baseline demonstrates loading-time content collapse',during.blocks===0&&during.top<before.top-20,report.reproduction);
    }else{
      check('slow request retains the previous page and scroll position',during.text===before.text&&during.blocks===before.blocks&&Math.abs(during.top-before.top)<=1&&Math.abs(during.toolbar-before.toolbar)<=1,{before,during});
      const locked=await page.locator('.rm-page-blocks').evaluate(body=>body.inert||Boolean(body.closest('[inert]'))||[...body.querySelectorAll('button')].every(button=>button.disabled));
      check('loading old page cannot trigger translation or source actions',locked);
      check('loading state is exposed to assistive technology',await page.locator('[aria-busy="true"]').count()>0);
      await release(2);await loaded(2);const after=await geometry();
      check('completed long-page replacement preserves toolbar position',Math.abs(after.top-before.top)<=1&&Math.abs(after.toolbar-before.toolbar)<=1,{before,after});
      check('keyboard submission keeps page input focus',await input().evaluate(el=>el===document.activeElement));
      check('completed page is no longer busy or inert',await page.locator('.rm-page-blocks').evaluate(body=>!body.inert&&body.getAttribute('aria-busy')!=='true'));
      holdPages.clear();

      // Repeated Enter on one in-flight target should not send duplicate reads.
      const repeatCount=requests.filter(n=>n===4).length;holdPages.add(4);await submit(4);await held(4);await submit(4);await settle();
      check('repeated submission coalesces the same in-flight page request',requests.filter(n=>n===4).length===repeatCount+1,{before:repeatCount,after:requests.filter(n=>n===4).length});
      await page.evaluate(()=>{document.querySelector('#moduleRoot').scrollTop+=420;});const movedDuring=await geometry();await release(4);await loaded(4);
      const movedAfter=await geometry();check('completion preserves the latest user scroll made during loading',Math.abs(movedAfter.top-movedDuring.top)<=1,{movedDuring,movedAfter});holdPages.clear();await position();

      // Hold two independent responses, then deliver them in reverse order.
      holdPages.add(4);holdPages.add(5);await submit(4);await held(4);await submit(5);await held(5);
      await release(5);await loaded(5);const newest=await geometry();await release(4);await settle();await settle();
      const stale=await geometry();check('older late response cannot replace newer page or move scroll',stale.text===newest.text&&Math.abs(stale.top-newest.top)<=1&&await input().inputValue()==='5',{newest,stale});holdPages.clear();

      // A failed request must keep the current page and permit a real retry.
      await position();
      await page.evaluate(()=>{const text=document.querySelector('.rm-block-text').firstChild,r=document.createRange();text.parentElement.focus({preventScroll:true});r.setStart(text,0);r.setEnd(text,12);getSelection().removeAllRanges();getSelection().addRange(r);document.dispatchEvent(new Event('selectionchange'));});
      const beforeFailure=await geometry();failPages.add(2);holdPages.add(2);await programmaticSubmit(2);await held(2);await release(2);
      await page.waitForFunction(()=>!document.querySelector('.rm-page-blocks')?.inert);await settle();
      const failed=await geometry();check('failed next page preserves readable previous page and scroll',failed.text===beforeFailure.text&&Math.abs(failed.top-beforeFailure.top)<=1,{beforeFailure,failed});
      check('failed request preserves the existing text selection',failed.selection===beforeFailure.selection&&failed.selection.length>0,{before:beforeFailure.selection,after:failed.selection});
      check('failure visibly reports an error',await page.locator('.is-error,.rm-warning,[role="alert"]').filter({hasText:/失败|无法|failed/i}).count()>0);
      failPages.add(2);failPages.add(4);holdPages.add(4);await programmaticSubmit(2);await held(2);await programmaticSubmit(4);await held(4);await release(2);await settle();
      check('older failed request cannot unlock a newer pending page',await page.locator('.rm-page-blocks').evaluate(body=>body.inert&&body.getAttribute('aria-busy')==='true'));
      await release(4);await page.waitForFunction(()=>!document.querySelector('.rm-page-blocks')?.inert);await settle();
      check('consecutive failed requests retain the original selection', (await geometry()).selection===beforeFailure.selection&&await page.getByRole('button',{name:'翻译所选文字',exact:true}).count()===1);
      holdPages.delete(4);
      failPages.add(2);await programmaticSubmit(2);await held(2);
      await input().click();await input().press('Control+A');await release(2);
      await page.waitForFunction(()=>!document.querySelector('.rm-page-blocks')?.inert);await settle();
      const cancelledSelection=await geometry(),selectedButtons=await page.getByRole('button',{name:'翻译所选文字',exact:true}).count();
      const cancelledRange=await page.evaluate(()=>{const s=getSelection();return{anchor:s.anchorNode?.parentElement?.className,insideBody:document.querySelector('.rm-page-blocks').contains(s.anchorNode),rangeText:s.rangeCount?s.getRangeAt(0).toString():''};});
      // Chromium Selection.toString() also exposes the selected numeric input
      // value after Ctrl+A; that new input selection is intentionally retained.
      check('failure does not resurrect a selection cancelled by toolbar interaction',!cancelledRange.insideBody&&selectedButtons===0,{selection:cancelledSelection.selection,selectedButtons,focus:cancelledSelection.focus,range:cancelledRange});
      holdPages.clear();await submit(2);await loaded(2);check('same requested page can be retried after failure',await input().inputValue()==='2'&&requests.filter(n=>n===2).length>=3);

      await page.evaluate(()=>{const text=document.querySelector('.rm-block-text').firstChild,r=document.createRange();text.parentElement.focus({preventScroll:true});r.setStart(text,0);r.setEnd(text,12);getSelection().removeAllRanges();getSelection().addRange(r);document.dispatchEvent(new Event('selectionchange'));});
      check('text selection exists before changing pages',(await geometry()).selection.length>0);
      await programmaticSubmit(4);await loaded(4);check('changing pages clears stale text selection and translation selection',!(await geometry()).selection&&await page.getByRole('button',{name:'翻译所选文字',exact:true}).count()===0);

      // Near the bottom, a genuinely shorter replacement can only clamp to its
      // actual bottom. Reserving the old height after completion would be wrong.
      await page.evaluate(()=>{const root=document.querySelector('#moduleRoot');root.scrollTop=root.scrollHeight-root.clientHeight-25;});const longBottom=await geometry();
      holdPages.add(3);await programmaticSubmit(3);await held(3);const shortPending=await geometry();
      check('long-to-short load holds old height until response',Math.abs(shortPending.top-longBottom.top)<=1&&shortPending.height===longBottom.height,{longBottom,shortPending});
      await release(3);await loaded(3);const short=await geometry();
      check('short page clamps naturally without forced scroll to top',short.top>0&&Math.abs(short.top-Math.min(longBottom.top,short.height-short.client))<=1,{longBottom,short});
      check('short page leaves no old long-page spacer',short.height<longBottom.height/2&&await page.locator('.rm-page-blocks').evaluate(body=>!body.style.minHeight||body.style.minHeight==='0px'),{longBottom,short});holdPages.clear();

      await position();holdPages.add(2);await submit(2);await held(2);
      await page.getByRole('tab',{name:'资料与版本',exact:true}).click();const tabBefore=await geometry();await release(2);await settle();await settle();const tabAfter=await geometry();
      check('late text response does not change the active tab or its scroll',await page.getByRole('tab',{name:'资料与版本',exact:true}).getAttribute('aria-selected')==='true'&&Math.abs(tabAfter.top-tabBefore.top)<=1,{tabBefore,tabAfter});holdPages.clear();
      await page.getByRole('tab',{name:'本页文字',exact:true}).click();await loaded(2);
      const outline=page.locator('.rm-text-outline');if(!await outline.evaluate(el=>el.open))await outline.locator('summary').click();
      await outline.getByRole('button',{name:/Jump to page four paragraph nine/}).click();await loaded(4);await settle();
      const target=await page.locator('.rm-block-text').nth(8).evaluate(el=>{const r=el.getBoundingClientRect();return{focused:el===document.activeElement,top:r.top,bottom:r.bottom,viewport:innerHeight,previewed:el.closest('.rm-text-block').classList.contains('is-previewed')};});
      check('explicit chapter navigation still focuses and reveals its paragraph',target.focused&&target.previewed&&target.top<target.viewport&&target.bottom>0,target);

      for(const width of [1440,602,320]){
        await page.setViewportSize({width,height:800});await programmaticSubmit(1);await loaded(1);await position();
        const responsiveBefore=await geometry();holdPages.add(2);await submit(2);await held(2);const responsiveDuring=await geometry();
        check(`viewport ${width}: pending page keeps scroll and toolbar`,Math.abs(responsiveBefore.top-responsiveDuring.top)<=1&&Math.abs(responsiveBefore.toolbar-responsiveDuring.toolbar)<=1,{responsiveBefore,responsiveDuring});
        await release(2);await loaded(2);const responsiveAfter=await geometry();
        check(`viewport ${width}: resolved page keeps keyboard focus and scroll`,Math.abs(responsiveAfter.top-responsiveBefore.top)<=1&&await input().evaluate(el=>el===document.activeElement),{responsiveBefore,responsiveAfter});
        const sizing=await page.evaluate(()=>{const root=document.querySelector('#moduleRoot');return{viewport:innerWidth,document:document.documentElement.scrollWidth,module:root.scrollWidth,client:root.clientWidth};});
        check(`viewport ${width}: no horizontal overflow`,sizing.document<=width+1&&sizing.module<=sizing.client+1,sizing);
        await screenshot(`pagination-${width}.png`);holdPages.clear();
      }
      holdPages.add(5);await submit(5);await held(5);
      await page.evaluate(()=>{fixtureController.suspend();document.querySelector('.rm-shell').hidden=true;const replacement=document.createElement('section');replacement.id='fixtureOtherModule';replacement.style.height='3000px';replacement.textContent='Synthetic suspended-module destination';document.querySelector('#moduleRoot').append(replacement);document.querySelector('#moduleRoot').scrollTop=230;});
      await release(5);await settle();await settle();
      check('suspended module late response does not restore its former scroll',await page.locator('#moduleRoot').evaluate(root=>root.scrollTop===230&&Boolean(root.querySelector('#fixtureOtherModule'))));
      await page.evaluate(()=>{document.querySelector('#fixtureOtherModule').remove();document.querySelector('.rm-shell').hidden=false;fixtureController.resume();});holdPages.clear();
      holdPages.add(4);await submit(4);await held(4);
      await page.evaluate(()=>{fixtureController.destroy();const root=document.querySelector('#moduleRoot');root.innerHTML='<section style="height:3000px">Synthetic replacement module</section>';root.scrollTop=230;});
      await release(4);await settle();await settle();
      check('destroyed module late response cannot reclaim DOM or scroll',await page.locator('#moduleRoot').evaluate(root=>root.textContent==='Synthetic replacement module'&&root.scrollTop===230));
      check('no browser script errors',report.pageErrors.length===0,report.pageErrors);
      check('no service/model/write or unexpected requests',report.unexpectedRequests.length===0,report.unexpectedRequests);
    }
  }catch(error){report.failure=String(error.stack||error);process.exitCode=1;try{await screenshot('failure.png');}catch{}}
  finally{
    holdPages.clear();for(const response of pending.splice(0))try{await response.send();}catch{}
    report.finishedAt=new Date().toISOString();report.passed=!report.failure&&report.checks.every(c=>c.passed);report.requests=requests;
    if(!report.passed)process.exitCode=1;
    fs.writeFileSync(path.join(output,'report.json'),JSON.stringify(report,null,2)+'\n');
    console.log(JSON.stringify({passed:report.passed,baseline,checks:report.checks.length,failed:report.checks.filter(c=>!c.passed),output,error:report.failure},null,2));await browser.close();
  }
}
main().catch(error=>{console.error(error);process.exitCode=1;});
