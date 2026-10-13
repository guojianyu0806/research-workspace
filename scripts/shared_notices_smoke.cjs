/* Cross-page notification regression using the complete current frontend.
 * All requests, including simulated save/login failures, are fulfilled locally. */
'use strict';
const fs = require('node:fs'), path = require('node:path'), crypto = require('node:crypto'), assert = require('node:assert/strict');
const {chromium} = require(process.env.PLAYWRIGHT_MODULE || 'C:/Users/g2806/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright');
const root = path.resolve(__dirname, '..'), origin = 'http://127.0.0.1:8765';
const output = path.join(root, 'artifacts/browser', `shared-notices-${new Date().toISOString().replace(/[:.]/g,'-')}-${crypto.randomBytes(3).toString('hex')}`);
fs.mkdirSync(output,{recursive:true});
const assets = new Map(fs.readdirSync(path.join(root,'research_workspace/static'),{withFileTypes:true}).filter(e=>e.isFile()).map(({name})=>[name,fs.readFileSync(path.join(root,'research_workspace/static',name))]));
const report = {checks:[],pageErrors:[],unexpectedRequests:[],sha256:{},limitations:['Synthetic API; no real upload, parsing or model call.']};
for(const [name,bytes] of assets) if(/\.(js|css|html)$/.test(name)) report.sha256[name]=crypto.createHash('sha256').update(bytes).digest('hex');
function check(name,ok,details){report.checks.push({name,passed:Boolean(ok),details});assert.ok(ok,name);}
async function main(){
 const browser=await chromium.launch({headless:true,executablePath:process.env.BROWSER_EXECUTABLE||'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe'});
 let fail = '';
 const doc={id:'A',title:'Synthetic paper',filename:'A.pdf',authors:['Fixture'],page_count:2,current_run:{id:'run-A',version:1,status:'completed'},available_run:{id:'run-A',version:1,status:'completed'}};
 const context=await browser.newContext({viewport:{width:1440,height:800}});
 await context.route('**/*',async route=>{
  const req=route.request(),url=new URL(req.url()),p=url.pathname;
  if(url.origin!==origin){report.unexpectedRequests.push(req.url());return route.abort();}
  if((fail&& (p.includes(fail)||req.method()===fail)) || p==='/static/examples/catalog.json') return route.fulfill({status:503,contentType:'application/json',body:JSON.stringify({detail:'SYNTHETIC operation failed'})});
  if(p==='/'||p.startsWith('/static/')){const name=p==='/'?'index.html':path.basename(p);return route.fulfill({contentType:{'.html':'text/html','.js':'text/javascript','.css':'text/css','.svg':'image/svg+xml'}[path.extname(name)],body:assets.get(name)});}
  const fixtures={'/api/account':{user:{mode:'local'}},'/api/capabilities':{mode:'local',max_upload_mb:50,llm:false,intelligence:{configured:false}},'/api/documents':{documents:[doc]},'/api/documents/A':{document:doc},'/api/documents/A/versions':{family:{documents:[]},candidates:[]},'/api/documents/A/structure':{headings:[],references:[]},'/api/documents/A/references/ranked':{included:[],filtered:[]},'/api/datasets':{datasets:[]},'/api/subscriptions':{subscriptions:[]},'/api/recommendations':{items:[]},'/api/graph':{nodes:[],edges:[],unresolved:[]},'/api/model':{configured:false,defaultModel:'deepseek-flash'},'/api/research/tasks':{jobs:[]}};
  if(/\/pages\/\d+\/image$/.test(p))return route.fulfill({contentType:'image/svg+xml',body:'<svg xmlns="http://www.w3.org/2000/svg" width="600" height="800"><rect width="600" height="800" fill="white"/></svg>'});
  if(/\/pages\/\d+$/.test(p))return route.fulfill({contentType:'application/json',body:JSON.stringify({document_id:'A',run_id:'run-A',page:1,blocks:[]})});
  if(!fixtures[p]){report.unexpectedRequests.push(p);return route.abort();}
  return route.fulfill({contentType:'application/json',body:JSON.stringify(fixtures[p])});
 });
 const page=await context.newPage();page.setDefaultTimeout(6000);page.on('pageerror',e=>report.pageErrors.push(String(e)));
 const notices=page.locator('.app-notice:visible');
 const pause=ms=>page.waitForTimeout(ms);
 const measure=target=>target.evaluate(el=>{const r=el.getBoundingClientRect();let x=r.x,y=r.y;for(let p=el.parentElement;p;p=p.parentElement){x+=p.scrollLeft;y+=p.scrollTop;}return {x,y,width:r.width,height:r.height};});
 const moduleButton=name=>page.locator('.module-navigation').getByRole('button',{name,exact:true});
 const visibleToast=()=>page.locator('.app-notice:visible').last();
 async function verify(label,selector,action){
  await page.mouse.move(1350,750);
  const target=page.locator(selector).first(), before=await measure(target);
  await action();await visibleToast().waitFor();
  check(label+' uses floating feedback',await visibleToast().evaluate(el=>getComputedStyle(el.parentElement).position==='fixed'));
  check(label+' does not shift form or controls',JSON.stringify(before)===JSON.stringify(await measure(target)));
  check(label+' leaves legacy notice anchors hidden',await page.locator('.rm-notice:visible,.rs-notice:visible,.ex-notice:visible,.examples-notice:visible,.rb-feedback:visible,#accountStatus:visible').count()===0);
  await visibleToast().getByRole('button',{name:'关闭提示'}).click();
  check(label+' dismisses without shifting controls',JSON.stringify(before)===JSON.stringify(await measure(target)));
 }
 try{
  await page.goto(origin);await page.locator('#connectionState.is-connected').waitFor();
  await moduleButton('文献管理').click();await page.getByRole('button',{name:'保存资料',exact:true}).waitFor();
  fail='PATCH';await verify('metadata save','.rm-document-bar',()=>page.getByRole('button',{name:'保存资料',exact:true}).click());fail='';
  await page.getByRole('tab',{name:'版面结构',exact:true}).click();fail='/layout/';
  await verify('layout analysis','.rm-layout-inspector form',()=>page.getByRole('button',{name:'分析本页结构',exact:true}).click());fail='';
  await page.getByRole('tab',{name:'参考文献筛选',exact:true}).click();await page.getByRole('button',{name:'应用本地筛选',exact:true}).waitFor();
  fail='/references/';await verify('reference filtering','.rr-shell form',()=>page.getByRole('button',{name:'应用本地筛选',exact:true}).click());fail='';
  await moduleButton('问答与写作').click();await page.getByRole('button',{name:'刷新资料与任务',exact:true}).waitFor();
  fail='/research/tasks';await verify('research refresh','.rs-task-form',()=>page.getByRole('button',{name:'刷新资料与任务',exact:true}).click());fail='';
  await moduleButton('发现与追踪').click();await page.getByRole('searchbox',{name:'公开检索关键词'}).fill('fixture');fail='/discover/search';
  await verify('discovery search','.ex-query-row',()=>page.getByRole('button',{name:'检索 arXiv',exact:true}).click());fail='';
  await moduleButton('引用图谱').click();await page.getByRole('button',{name:'更新引用图谱',exact:true}).waitFor();fail='/graph';
  await verify('citation graph','.ex-heading',()=>page.getByRole('button',{name:'更新引用图谱',exact:true}).click());fail='';
  await moduleButton('数据与图表').click();await page.getByRole('button',{name:'刷新数据集',exact:true}).waitFor();fail='/datasets';
  await verify('dataset refresh','.rs-heading',()=>page.getByRole('button',{name:'刷新数据集',exact:true}).click());fail='';
  // Feedback owned by a view is removed on navigation, not shown on the next page.
  fail='/datasets';await page.getByRole('button',{name:'刷新数据集',exact:true}).click();await visibleToast().waitFor();fail='';
  await moduleButton('原文阅读').click();check('leaving a module clears its toast',await notices.count()===0);
  await page.locator('#accountButton').click();await page.locator('#accountForm input[name=name]').fill('fixture');await page.locator('#accountForm input[name=password]').fill('synthetic-only-password');fail='/account/login';
  await verify('account login','#accountForm',()=>page.getByRole('button',{name:'登录',exact:true}).click());fail='';
  await page.locator('#closeAccount').click();await page.locator('#modelSettingsButton').click();await page.locator('.ms-input[type=password]').fill('synthetic-only-key');fail='POST';
  const modelRect=await page.locator('.ms-form').boundingBox();await page.getByRole('button',{name:'保存本次配置',exact:true}).click();await visibleToast().waitFor();
  check('model failure uses modal-owned overlay',await visibleToast().evaluate(el=>Boolean(el.closest('dialog[open]'))));
  check('model failure preserves form layout',JSON.stringify(modelRect)===JSON.stringify(await page.locator('.ms-form').boundingBox()));
  await visibleToast().hover();await pause(2300);check('modal toast remains hoverable beyond timeout',await notices.count()===1);
  check('modal toast is not covered by backdrop',await visibleToast().evaluate(el=>{const r=el.getBoundingClientRect();return el.contains(document.elementFromPoint(r.x+r.width/2,r.y+r.height/2));}));
  await page.mouse.move(1400,780);await pause(2300);check('modal toast dismisses after pointer leaves',await notices.count()===0);
  await page.getByRole('button',{name:'关闭模型设置并清空密钥输入'}).click();fail='';
  await page.locator('#examplesButton').click();await page.locator('#examplesDialog .app-notice.is-error').waitFor();
  check('example errors use modal overlay',await page.locator('#examplesDialog .app-notice').evaluate(el=>getComputedStyle(el.parentElement).position==='fixed'));
  await page.getByRole('button',{name:'关闭演示示例'}).click();check('closing dialog clears its feedback',await notices.count()===0);
  await page.locator('#accountButton').click();await page.locator('#accountForm input[name=name]').fill('fixture');await page.locator('#accountForm input[name=password]').fill('synthetic-only-password');fail='/account/login';
  await page.getByRole('button',{name:'登录',exact:true}).click();await visibleToast().waitFor();await visibleToast().hover();
  await page.screenshot({path:path.join(output,'modal-notice.png')});
  await page.locator('#closeAccount').click();
  check('no script errors or unexpected requests',!report.pageErrors.length&&!report.unexpectedRequests.length,{errors:report.pageErrors,requests:report.unexpectedRequests});
 }catch(error){report.failure=String(error.stack||error);process.exitCode=1;await page.screenshot({path:path.join(output,'failure.png')}).catch(()=>{});}
 finally{report.passed=!report.failure;fs.writeFileSync(path.join(output,'report.json'),JSON.stringify(report,null,2));await browser.close();console.log(JSON.stringify({output,passed:report.passed,checks:report.checks.length,failure:report.failure}));}
}
main().catch(e=>{console.error(e);process.exitCode=1;});
