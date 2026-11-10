/* Real loopback HTTP + current UI; deterministic fixture, never a paid model. */
'use strict';
const fs=require('node:fs'), path=require('node:path'), assert=require('node:assert/strict');
const {spawn}=require('node:child_process');
const {chromium}=require(process.env.PLAYWRIGHT_MODULE||'C:/Users/g2806/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright');
const root=path.resolve(__dirname,'..');
const output=path.join(root,'artifacts','model-stream',new Date().toISOString().replace(/[:.]/g,'-'));
fs.mkdirSync(output,{recursive:true});
const report={checks:[],pageErrors:[],unexpectedRequests:[],limitations:['Deterministic local HTTP fixture; not Pro quality or official dataset acceptance.']};
function check(name,passed,details){report.checks.push({name,passed:Boolean(passed),details});assert.ok(passed,name);}
async function main(){
 const child=spawn(path.join(root,'.venv','Scripts','python.exe'),['-u',path.join(__dirname,'stream_fixture_server.py'),'--data-dir',path.join(output,'workspace')],{cwd:root,windowsHide:true,stdio:['pipe','pipe','pipe']});
 let stderr='';child.stderr.on('data',chunk=>stderr+=chunk.toString());
 const ready=await new Promise((resolve,reject)=>{let data='';const timer=setTimeout(()=>reject(new Error('Fixture timeout: '+stderr)),20000);
  child.stdout.on('data',chunk=>{data+=chunk;const line=data.split('\n').find(s=>s.startsWith('{'));if(line){clearTimeout(timer);resolve(JSON.parse(line));}});
  child.on('exit',code=>{clearTimeout(timer);reject(new Error('Fixture exited '+code+': '+stderr));});});
 const browser=await chromium.launch({headless:true,executablePath:process.env.BROWSER_EXECUTABLE||'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe'});
 try{
  const context=await browser.newContext({viewport:{width:1440,height:900}});
  await context.route('**/*',route=>{if(new URL(route.request().url()).origin!==ready.origin){report.unexpectedRequests.push(route.request().url());return route.abort();}return route.continue();});
  const page=await context.newPage();page.setDefaultTimeout(10000);page.on('pageerror',e=>report.pageErrors.push(String(e)));
  await page.goto(ready.origin);await page.locator('#connectionState.is-connected').waitFor();
  await page.locator('.module-navigation').getByRole('button',{name:'问答与写作',exact:true}).click();
  await page.getByRole('button',{name:'刷新资料与任务',exact:true}).waitFor();
  const api=async(url,body)=>{const r=await context.request.fetch(ready.origin+url,body?{method:'POST',data:body}:{});assert.ok(r.ok(),await r.text());return r.json();};
  const create=async body=>{const {job}=await api('/api/research/tasks',body);await page.getByRole('button',{name:'刷新资料与任务',exact:true}).click();await page.locator(`.rs-history-item[data-job-id="${job.id}"]`).click();return job;};
  const preview=page.locator('.rs-stream-preview');
  const phase=page.locator('.rs-task-phase');
  await preview.evaluate(panel=>{
   const text=panel.querySelector('pre');
   window.fixturePreviewLengths=[];
   new MutationObserver(()=>{
    if(!panel.hidden&&text.textContent.length)window.fixturePreviewLengths.push(text.textContent.length);
   }).observe(text,{childList:true,characterData:true,subtree:true});
  });
  const waitFinal=async(id,status='completed')=>{let saved;for(let i=0;i<200;i++){saved=(await api('/api/research/tasks/'+id)).job;if(!['queued','running'].includes(saved.status))break;await page.waitForTimeout(100);}assert.equal(saved.status,status,saved.error);return saved;};
  const qa=await create({kind:'qa',document_ids:[ready.documents[0]],question:'What architecture does the model use?'});
  await preview.waitFor({state:'visible'});
  await phase.getByText('正在根据原文生成回答',{exact:true}).waitFor({state:'visible'});
  const early=(await api('/api/research/tasks/'+qa.id)).job;
  check('Preview appears before final answer and completion',early.status==='running'&&early.result===null);
  check('Task phase follows a live receipt and elapsed time, without a guessed total',
    early.events.some(event=>event.stage==='model_call'&&event.message.includes('AnswerPayload'))&&
    (await phase.innerText()).includes('已用')&&await page.locator('.rs-progress').isHidden());
  await page.waitForFunction(()=>window.fixturePreviewLengths.some(length=>length>window.fixturePreviewLengths[0]));
  check('Actual deltas grow while request is in progress',true);
  check('Unverified preview has no active citation links or HTML',await preview.locator('a,button,img,b').count()===0);
  check('Private reasoning absent from live page',!(await page.locator('body').innerText()).includes('PRIVATE_FIXTURE_REASONING'));
  await page.screenshot({path:path.join(output,'qa-streaming.png'),fullPage:false});
  const complete=await waitFinal(qa.id);
  await preview.waitFor({state:'hidden'});
  await phase.waitFor({state:'hidden'});
  check('Final answer retains original quote, source and passed audit',complete.result.grounding.status==='passed'&&complete.result.sources[0].document_id===ready.documents[0]&&complete.result.claims[0].citations[0].quote==='The model uses an encoder and a decoder.');
  check('Only final result persists after completion',complete.stream===null&&complete.result.claims.length===1);
  check('Receipt tracks streamed and audit calls',complete.model_calls.length===2&&complete.usage.total_tokens===50);
  const translation=await create({kind:'translate_text',text:'Vector retrieval improves search.','target_language':'zh'});
  await preview.waitFor({state:'visible'});
  await page.locator('.rs-history-item[data-job-id="'+qa.id+'"]').click();
  await preview.waitFor({state:'hidden'});
  check('Switching tasks hides unrelated draft',await preview.locator('pre').innerText()==='');
  await page.locator('.rs-history-item[data-job-id="'+translation.id+'"]').click();
  await preview.waitFor({state:'visible'});
  check('Returning task restores its own snapshot',(await preview.innerText()).includes('待审核草稿'));
  const navigation=page.locator('.module-navigation');
  await navigation.getByRole('button',{name:'原文阅读',exact:true}).click();
  await page.waitForTimeout(150);
  await navigation.getByRole('button',{name:'问答与写作',exact:true}).click();
  const translated=await waitFinal(translation.id);await preview.waitFor({state:'hidden'});
  check('Suspend/resume still receives final translation',translated.result.translated_text.includes('<b>这是纯文本。</b>'));
  check('Translated markup is never executed',await page.locator('.rs-result b').count()===0);
  const broken=await create({kind:'qa',document_ids:[ready.documents[0]],question:'fixture_disconnect What architecture does the model use?'});
  await preview.waitFor({state:'visible'});
  const failed=await waitFinal(broken.id,'failed');await preview.waitFor({state:'hidden'});
  check('Interrupted stream never becomes a result or automatic retry',failed.result===null&&failed.model_calls.length===1&&failed.usage.unknown_outcomes===1);
  const exported=await context.request.get(ready.origin+'/api/research/tasks/'+broken.id+'/export');
  check('Failed draft is absent from export',!(await exported.text()).includes('The model uses an encoder and a decoder.'));
  await page.setViewportSize({width:760,height:900});
  const cancelJob=await create({kind:'qa',document_ids:[ready.documents[0]],question:'What architecture does the model use?'});
  await preview.waitFor({state:'visible'});await page.getByRole('button',{name:'取消任务',exact:true}).click();
  const cancelled=await waitFinal(cancelJob.id,'cancelled');await preview.waitFor({state:'hidden'});
  check('Cancellation discards draft and stops further calls',cancelled.result===null&&cancelled.model_calls.length===1);
  check('Narrow viewport has no horizontal document overflow',await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1));
  await page.screenshot({path:path.join(output,'narrow-cancelled.png'),fullPage:false});
  const receipt=await api('/fixture/receipts');report.receipts=receipt.calls;
  check('All requests remained local with zero paid calls',receipt.paid_calls===0&&report.unexpectedRequests.length===0);
  check('No browser script errors',report.pageErrors.length===0,report.pageErrors);
 }finally{
  await browser.close();child.stdin.end('stop\n');
  await new Promise(resolve=>{if(child.exitCode!==null)return resolve();const t=setTimeout(()=>{child.kill();resolve();},8000);child.once('exit',()=>{clearTimeout(t);resolve();});});
  fs.writeFileSync(path.join(output,'report.json'),JSON.stringify(report,null,2));
  if(stderr)fs.writeFileSync(path.join(output,'fixture-stderr.txt'),stderr);
 }
 console.log(JSON.stringify({output,checks:report.checks.length,passed:report.checks.every(c=>c.passed)},null,2));
}
main().catch(error=>{console.error(error);process.exitCode=1;});
