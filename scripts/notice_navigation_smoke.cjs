/* Offline browser regression: transient notices and navigation geometry.
 * All requests use synthetic fixtures. Invalid file inputs are rejected locally. */
'use strict';
const fs = require('node:fs'), path = require('node:path'), crypto = require('node:crypto'), assert = require('node:assert/strict');
const {chromium} = require(process.env.PLAYWRIGHT_MODULE || 'C:/Users/g2806/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright');
const root = path.resolve(__dirname, '..'), origin = 'http://127.0.0.1:8765';
const output = path.join(root, 'artifacts/browser', `notice-navigation-${new Date().toISOString().replace(/[:.]/g,'-')}-${crypto.randomBytes(3).toString('hex')}`);
fs.mkdirSync(output,{recursive:true});
const assets = new Map(fs.readdirSync(path.join(root,'research_workspace/static'),{withFileTypes:true}).filter(e=>e.isFile()).map(({name})=>[name,fs.readFileSync(path.join(root,'research_workspace/static',name))]));
const report = {checks:[],pageErrors:[],unexpectedRequests:[],sha256:{},limitations:['Synthetic API; no real upload, parsing or model call.']};
for(const [name,bytes] of assets) if(/\.(js|css|html)$/.test(name)) report.sha256[name]=crypto.createHash('sha256').update(bytes).digest('hex');
function check(name,ok,details){report.checks.push({name,passed:Boolean(ok),details});assert.ok(ok,name);}
async function main(){
 const browser=await chromium.launch({headless:true,executablePath:process.env.BROWSER_EXECUTABLE||'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe'});
 const context=await browser.newContext({viewport:{width:1440,height:800}});
 await context.route('**/*',async route=>{
  const req=route.request(),url=new URL(req.url()),p=url.pathname;
  if(url.origin!==origin||req.method()!=='GET'){report.unexpectedRequests.push(req.url());return route.abort();}
  if(p==='/'||p.startsWith('/static/')){const name=p==='/'?'index.html':path.basename(p);return route.fulfill({contentType:{'.html':'text/html','.js':'text/javascript','.css':'text/css','.svg':'image/svg+xml'}[path.extname(name)],body:assets.get(name)});}
  const fixtures={'/api/account':{user:{mode:'local'}},'/api/capabilities':{mode:'local',max_upload_mb:50,llm:false,intelligence:{configured:false}},'/api/documents':{documents:[]},'/api/research/tasks':{jobs:[]}};
  if(!fixtures[p]){report.unexpectedRequests.push(p);return route.abort();}
  return route.fulfill({contentType:'application/json',body:JSON.stringify(fixtures[p])});
 });
 const page=await context.newPage();page.setDefaultTimeout(6000);page.on('pageerror',e=>report.pageErrors.push(String(e)));
 const notices=page.locator('.app-notice:visible');
 const trigger=async(name='SYNTHETIC_notice.txt')=>{await page.locator('#fileInput').setInputFiles({name,mimeType:'text/plain',buffer:Buffer.from('Local validation fixture')});await notices.last().waitFor();};
 const frame=()=>page.evaluate(()=>Object.fromEntries(['.app-header','.module-navigation','#workspace','#readerPanel'].map(selector=>{const r=document.querySelector(selector).getBoundingClientRect();return [selector,[r.x,r.y,r.width,r.height]];})));
 const pause=ms=>page.waitForTimeout(ms);
 try{
  await page.goto(origin);await page.locator('#connectionState.is-connected').waitFor();await page.mouse.move(1300,700);
  const before=await frame();await trigger();
  check('notice appears without moving or resizing the frame',JSON.stringify(before)===JSON.stringify(await frame()));
  check('notice uses a fixed overlay and status announcement',await page.locator('#appNotice').evaluate(el=>getComputedStyle(el.parentElement).position==='fixed'&&el.getAttribute('role')==='status'));
  await pause(1000);check('notice remains before two seconds',await notices.count()===1);
  await pause(1300);check('notice disappears after approximately two seconds',await notices.count()===0);
  check('dismissal preserves frame geometry',JSON.stringify(before)===JSON.stringify(await frame()));
  await trigger('SYNTHETIC_hover.txt');await page.locator('#appNotice').hover();await pause(2300);
  check('hover keeps notice beyond normal expiry',await notices.count()===1);
  await page.mouse.move(1300,700);await pause(1100);check('leaving starts a fresh two-second interval',await notices.count()===1);
  await page.locator('#appNotice').hover();await pause(1300);check('reentering cancels pending dismissal',await notices.count()===1);
  await page.mouse.move(1300,700);await pause(2300);check('notice expires after final pointer leave',await notices.count()===0);
  await trigger('SYNTHETIC_focus.txt');await page.locator('#dismissNotice').focus();await pause(2300);
  check('keyboard focus also pauses expiry',await notices.count()===1);
  await page.locator('#documentFilter').focus();await pause(2300);check('expiry resumes when focus leaves',await notices.count()===0);
  await trigger('SYNTHETIC_first.txt');await page.locator('#appNotice').hover();await trigger('SYNTHETIC_second.txt');
  check('simultaneous notices stack independently',await notices.count()===2);
  check('stack does not duplicate element IDs',await page.evaluate(()=>{const ids=[...document.querySelectorAll('.notice-stack [id]')].map(el=>el.id);return ids.length===new Set(ids).size;}));
  check('stack does not change frame geometry',JSON.stringify(before)===JSON.stringify(await frame()));
  await pause(2300);check('hovered card survives while other card expires',await notices.count()===1&&(await notices.textContent()).includes('SYNTHETIC_first'));
  await page.locator('#dismissNotice').click();check('close button dismisses immediately',await notices.count()===0);
  await page.mouse.move(1300,700);await trigger('SYNTHETIC_reuse.txt');await pause(2300);check('reused card starts a new timer',await notices.count()===0);
  for(const width of [1900,1440,602,320]){
   await page.setViewportSize({width,height:800});await page.mouse.move(width-2,780);await trigger('SYNTHETIC_Long_'+'ABCDEFG'.repeat(20)+'.txt');
   check(`long notice fits ${width}`,await notices.evaluateAll(els=>els.every(el=>{const r=el.getBoundingClientRect();return r.left>=0&&r.right<=innerWidth&&el.scrollWidth<=el.clientWidth+1;})));
   await page.locator('#dismissNotice').click();
   for(const name of ['文献管理','原文阅读']){
    await page.locator('.module-navigation').getByRole('button',{name,exact:true}).click();await pause(350);
    const metrics=await page.locator('.module-navigation').evaluate(nav=>{
     const b=nav.getBoundingClientRect(),buttons=[...nav.querySelectorAll('button')],r=buttons.map(el=>el.getBoundingClientRect()),selected=nav.querySelector('[aria-pressed=true]').getBoundingClientRect(),m=nav.querySelector('.selection-indicator').getBoundingClientRect();
     return {heights:r.map(x=>x.height),top:r.map(x=>x.top-b.top),bottom:r.map(x=>b.bottom-1-x.bottom),gaps:r.slice(1).map((x,i)=>x.left-r[i].right),aligned:['x','y','width','height'].every(k=>Math.abs(selected[k]-m[k])<1),selected:buttons.filter(el=>el.getAttribute('aria-pressed')==='true').length};
    });
    check(`uniform navigation edges and gaps ${width} ${name}`,metrics.heights.every(h=>h===metrics.heights[0])&&metrics.top.every(t=>Math.abs(t-metrics.bottom[0])<=1)&&metrics.gaps.every(g=>Math.abs(g-4)<1),metrics);
    check(`single aligned selection ${width} ${name}`,metrics.aligned&&metrics.selected===1);
   }
   check(`no page horizontal overflow ${width}`,await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1));
  }
  await page.setViewportSize({width:1440,height:800});await page.emulateMedia({reducedMotion:'reduce'});await trigger();await page.locator('#appNotice').hover();
  check('reduced motion disables toast entry animation',await page.locator('#appNotice').evaluate(el=>getComputedStyle(el).animationName==='none'));
  await page.screenshot({path:path.join(output,'floating-notice.png')});await page.locator('#dismissNotice').click();
  check('no script errors or unexpected requests',!report.pageErrors.length&&!report.unexpectedRequests.length,{errors:report.pageErrors,requests:report.unexpectedRequests});
 }catch(error){report.failure=String(error.stack||error);process.exitCode=1;await page.screenshot({path:path.join(output,'failure.png')}).catch(()=>{});}
 finally{report.passed=!report.failure;fs.writeFileSync(path.join(output,'report.json'),JSON.stringify(report,null,2));await browser.close();console.log(JSON.stringify({output,passed:report.passed,checks:report.checks.length,failure:report.failure}));}
}
main().catch(e=>{console.error(e);process.exitCode=1;});
