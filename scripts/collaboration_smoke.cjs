/* Real local HTTP, five synthetic accounts, current UI; no paid service. */
'use strict';
const fs=require('node:fs'),path=require('node:path'),assert=require('node:assert/strict');
const {spawn}=require('node:child_process');
const {chromium}=require(process.env.PLAYWRIGHT_MODULE||'C:/Users/g2806/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright');
const root=path.resolve(__dirname,'..'),output=path.join(root,'artifacts','collaboration',new Date().toISOString().replace(/[:.]/g,'-'));
fs.mkdirSync(output,{recursive:true});
const report={checks:[],pageErrors:[],externalRequests:[],limitations:['Synthetic local papers/accounts; not Pro or recommendation-quality acceptance.']};
function check(name,ok,details){report.checks.push({name,passed:Boolean(ok),details});assert.ok(ok,name);}
async function main(){
 const child=spawn(path.join(root,'.venv','Scripts','python.exe'),['-u',path.join(__dirname,'collaboration_fixture_server.py'),'--data-dir',path.join(output,'workspace')],{cwd:root,windowsHide:true,stdio:['pipe','pipe','pipe']});
 let stderr='';child.stderr.on('data',data=>stderr+=data.toString());
 const ready=await new Promise((resolve,reject)=>{let text='';const timer=setTimeout(()=>reject(new Error(stderr||'Fixture timeout')),25000);child.stdout.on('data',data=>{text+=data;const line=text.split('\n').find(s=>s.startsWith('{'));if(line){clearTimeout(timer);resolve(JSON.parse(line));}});child.on('exit',code=>{clearTimeout(timer);reject(new Error('Fixture exited '+code+': '+stderr));});});
 const browser=await chromium.launch({headless:true,executablePath:process.env.BROWSER_EXECUTABLE||'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe'});
 try{
  const contexts=[];
  async function account(name){const ctx=await browser.newContext({viewport:{width:1440,height:900}});contexts.push(ctx);await ctx.route('**/*',route=>{if(new URL(route.request().url()).origin!==ready.origin){report.externalRequests.push(route.request().url());return route.abort();}return route.continue();});
   if(name){const response=await ctx.request.post(ready.origin+'/api/account/login',{data:{name,password:'fixture-only-password'}});assert.ok(response.ok());}return ctx;}
  const alice=await account('FixtureAlice'),bob=await account('FixtureBob'),dan=await account('FixtureDan'),eve=await account('FixtureEve'),guest=await account();
  async function api(ctx,url,body){const response=await ctx.request.fetch(ready.origin+url,body===undefined?{}:{method:'PUT',data:body});assert.ok(response.ok(),await response.text());return response.json();}
  const page=await alice.newPage();page.on('pageerror',error=>report.pageErrors.push(String(error)));page.setDefaultTimeout(10000);
  await page.goto(ready.origin);await page.locator('#connectionState.is-connected').waitFor();
  await page.locator('.module-navigation').getByRole('button',{name:'发现与追踪',exact:true}).click();
  await page.getByRole('button',{name:'本地推荐',exact:true}).click();
  const panel=page.locator('.ex-collaboration');
  await panel.getByRole('button',{name:'加入协同推荐',exact:true}).waitFor();
  check('Consent is off and sharing scope visible before join',(await panel.innerText()).includes('协同推荐未加入')&&(await panel.innerText()).includes('仅共享公开论文标识'));
  const before=await api(alice,'/api/recommendations');const candidate=ready.papers[2];
  const baseline=before.items.find(p=>p.id===candidate).score;
  await panel.getByRole('button',{name:'加入协同推荐',exact:true}).click();
  await panel.getByRole('button',{name:'退出并清除共享反馈',exact:true}).waitFor();
  await page.waitForFunction(()=>document.querySelector('.ex-collaboration')?.textContent.includes('已结合群体反馈'));
  const joined=await api(alice,'/api/recommendations');
  check('Actual collaborative score changes candidate ordering',joined.collaboration.status==='applied'&&joined.items.find(p=>p.id===candidate).score===baseline+.75);
  check('Current space metadata remains local',joined.items.every(p=>p.document_id===null));
  await page.screenshot({path:path.join(output,'joined-desktop.png'),fullPage:false});
  const bobResult=await api(bob,'/api/recommendations');
  const serialized=JSON.stringify(bobResult);
  check('Other account cannot see private identifiers or text',![ready.private_document,ready.private_task,ready.private_marker,'ALICE_PRIVATE_SUBSCRIPTION','FixtureAlice'].some(value=>serialized.includes(value)));
  for(const route of ['/api/documents/'+ready.private_document,'/api/research/tasks/'+ready.private_task,'/api/research/tasks/'+ready.private_task+'/export']){
   check('Private route remains isolated: '+route.split('/')[2],(await bob.request.get(ready.origin+route)).status()===404);
  }
  const outsider=await api(eve,'/api/recommendations');
  check('Nonparticipating user stays on personal recommendation',!outsider.collaboration.enabled&&outsider.collaboration.status==='disabled');
  check('Default space cannot join',(await guest.request.put(ready.origin+'/api/recommendations/collaboration',{data:{enabled:true}})).status()===403);
  await api(dan,'/api/recommendations/collaboration',{enabled:false});
  await page.getByRole('button',{name:'更新推荐',exact:true}).click();
  await page.waitForFunction(()=>document.querySelector('.ex-collaboration')?.textContent.includes('群组不足'));
  const insufficient=await api(alice,'/api/recommendations');
  check('Withdrawal below threshold restores baseline',insufficient.collaboration.status==='insufficient_cohort'&&insufficient.items.find(p=>p.id===candidate).score===baseline);
  await api(dan,'/api/recommendations/collaboration',{enabled:true});
  const disliked=await alice.request.post(ready.origin+'/api/discover/feedback/'+candidate,{data:{value:-1}});assert.ok(disliked.ok());
  const privatePreference=await api(alice,'/api/recommendations');
  check('Own negative feedback overrides group boost',privatePreference.items.find(p=>p.id===candidate).feedback===-1&&!privatePreference.items.find(p=>p.id===candidate).reasons.some(r=>r.includes('群组')));
  await panel.getByRole('button',{name:'退出并清除共享反馈',exact:true}).click();
  await panel.getByRole('button',{name:'加入协同推荐',exact:true}).waitFor();
  const withdrawn=await api(alice,'/api/recommendations');
  check('Withdraw clears projection while keeping private preference',!withdrawn.collaboration.enabled&&withdrawn.collaboration.shared_feedback_count===0&&withdrawn.items.find(p=>p.id===candidate).feedback===-1);
  await page.reload();await page.locator('.module-navigation').getByRole('button',{name:'发现与追踪',exact:true}).click();await page.getByRole('button',{name:'本地推荐',exact:true}).click();
  await page.locator('.ex-collaboration').getByRole('button',{name:'加入协同推荐',exact:true}).waitFor();
  check('Withdrawal survives reload',!(await api(alice,'/api/recommendations')).collaboration.enabled);
  await page.setViewportSize({width:760,height:900});
  check('Narrow layout does not overflow',await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1));
  await page.screenshot({path:path.join(output,'withdrawn-narrow.png'),fullPage:false});
  check('No external browser traffic or script errors',report.externalRequests.length===0&&report.pageErrors.length===0);
 }catch(error){report.failure=String(error);throw error;}finally{
  await browser.close();child.stdin.end('stop\n');
  await new Promise(resolve=>{if(child.exitCode!==null)return resolve();const timer=setTimeout(()=>{child.kill();resolve();},8000);child.once('exit',()=>{clearTimeout(timer);resolve();});});
  if(fs.existsSync(path.join(output,'fixture-state.json'))){report.fixture=JSON.parse(fs.readFileSync(path.join(output,'fixture-state.json'),'utf8'));
   report.checks.push({name:'Private documents and tasks unchanged with zero model calls',passed:report.fixture.original_records_unchanged&&report.fixture.model_calls===0});
   report.checks.push({name:'Shared schema and content exclude private data',passed:JSON.stringify(report.fixture.shared_fields)===JSON.stringify(['user_id','paper_id','value'])&&report.fixture.private_marker_absent&&report.fixture.private_ids_absent&&report.fixture.password_absent});}
  fs.writeFileSync(path.join(output,'report.json'),JSON.stringify(report,null,2));if(stderr)fs.writeFileSync(path.join(output,'fixture-stderr.txt'),stderr);
 }
 assert.ok(report.checks.every(check=>check.passed),'Final fixture integrity checks');
 console.log(JSON.stringify({output,checks:report.checks.length,passed:report.checks.every(c=>c.passed)},null,2));
}
main().catch(error=>{console.error(error);process.exitCode=1;});
