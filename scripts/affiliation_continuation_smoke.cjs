/* Source-fragment buttons, real saved parse versions and account boundaries. */
'use strict';
const fs=require('node:fs'),path=require('node:path'),assert=require('node:assert/strict');
const {spawn}=require('node:child_process');
const {chromium}=require(process.env.PLAYWRIGHT_MODULE||'C:/Users/g2806/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright');
const root=path.resolve(__dirname,'..');
async function main(){
 const child=spawn(path.join(root,'.venv','Scripts','python.exe'),['-u',path.join(__dirname,'affiliation_continuation_fixture.py')],{cwd:root,windowsHide:true,stdio:['pipe','pipe','pipe']});
 let stderr='',ready,browser,context;
 child.stderr.on('data',data=>stderr+=data);
 const report={checks:[],pageErrors:[],externalRequests:[],scope:'Two existing public PDFs; local offline acceptance, not Pro or human identity certification.'};
 const check=(name,ok)=>{report.checks.push({name,passed:Boolean(ok)});assert.ok(ok,name);};
 try{
  ready=await new Promise((resolve,reject)=>{
   let text='';const timer=setTimeout(()=>reject(Error('Fixture startup timed out')),90000);
   child.stdout.on('data',data=>{text+=data;const line=text.split('\n').find(s=>s.startsWith('{'));if(line){clearTimeout(timer);resolve(JSON.parse(line));}});
   child.once('exit',code=>{clearTimeout(timer);reject(Error('Fixture exited '+code));});
  });
  browser=await chromium.launch({headless:true,executablePath:process.env.BROWSER_EXECUTABLE||'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe'});
  context=await browser.newContext({viewport:{width:1440,height:950}});
  await context.route('**/*',route=>{
   if(new URL(route.request().url()).origin!==ready.origin){report.externalRequests.push(route.request().url());return route.abort();}
   return route.continue();
  });
  const page=await context.newPage();page.setDefaultTimeout(12000);page.on('pageerror',error=>report.pageErrors.push(String(error)));
  const get=async(url,ctx=context)=>{const response=await ctx.request.get(ready.origin+url);try{assert.equal(response.status(),200);return await response.json();}finally{await response.dispose();}};
  await page.goto(ready.origin);await page.locator('#connectionState.is-connected').waitFor();
  await page.locator('.module-navigation').getByRole('button',{name:'文献管理',exact:true}).click();
  for(const doc of ready.documents){
   const structures=[];
   for(const run of [doc.old_run,doc.current_run]){
    const structure=await get(`/api/documents/${doc.id}/structure?run_id=${run}`);
    structures.push(structure);
    const org=structure.front_matter.affiliations.find(o=>o.text===doc.expected);
    check(`${doc.filename} ${run}: full institution and per-part audit`,Boolean(org)&&org.evidence_parts.length===2&&structure.front_matter.audit.source_spans_valid);
    const source=await get(`/api/documents/${doc.id}/pages/1?run_id=${run}`);
    for(const part of org.evidence_parts){
     const block=source.blocks.find(b=>b.id===part.block_id||b.block_id===part.block_id);
     check(`${doc.filename} ${run}: exact saved part ${part.char_start}`,Boolean(block)&&part.run_id===run&&part.document_id===doc.id&&part.text===block.text.slice(part.char_start,part.char_end)&&JSON.stringify(part.bbox)===JSON.stringify(block.bbox));
    }
    const relations=structure.front_matter.relations.filter(r=>r.affiliation_id===org.id);
    check(`${doc.filename} ${run}: each relation includes both institution pieces`,relations.length>0&&relations.every(r=>JSON.stringify(r.evidence.slice(1))===JSON.stringify(org.evidence_parts)));
   }
   check(`${doc.filename}: reparse has new source identifiers`,structures[0].front_matter.affiliations[0].evidence.block_id!==structures[1].front_matter.affiliations[0].evidence.block_id);
   const metadata=await get(`/api/documents/${doc.id}`);
   check(`${doc.filename}: manual catalog authors preserved`,JSON.stringify(metadata.document.authors)===JSON.stringify(['Manually Confirmed Fixture Author']));
   await page.getByRole('combobox',{name:'选择文献，含已归档文献'}).selectOption(doc.id);
   await page.getByRole('tab',{name:'标题与引用',exact:true}).click();
   const panel=page.locator('.rm-front-matter');await panel.locator('summary').waitFor();
   if(await panel.getAttribute('open')===null)await panel.locator('summary').click();
   const current=structures[1].front_matter;
   const org=current.affiliations.find(o=>o.text===doc.expected);
   const author=current.authors.find(a=>a.affiliation_ids.includes(org.id));
   const row=panel.locator('.rm-front-row').filter({hasText:author.name});
   for(let index=0;index<org.evidence_parts.length;index++){
    await row.getByRole('button',{name:`核对机构原文 ${index+1}/2`,exact:true}).click();
    await page.locator('#evidenceHighlight:not([hidden])').waitFor();
    const highlight=await page.locator('#evidenceHighlight').evaluate(n=>({x:parseFloat(n.style.left)/100,y:parseFloat(n.style.top)/100}));
    const part=org.evidence_parts[index];
    check(`${doc.filename}: UI part ${index+1} uses its own source location`,Math.abs(highlight.x-part.bbox[0])<.00001&&Math.abs(highlight.y-part.bbox[1])<.00001&&await page.locator('#pageNumber').inputValue()==='1');
    await page.locator('#readerReturnButton').click();await panel.waitFor({state:'visible'});
    check(`${doc.filename}: returning preserves part buttons and context`,await row.getByRole('button',{name:'核对机构原文 2/2',exact:true}).isVisible());
   }
   await page.screenshot({path:path.join(ready.output,doc.filename+'-desktop.png'),fullPage:false});
  }
  await page.setViewportSize({width:760,height:950});
  check('Narrow layout has no horizontal document overflow',await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1));
  await page.screenshot({path:path.join(ready.output,'institutions-narrow.png'),fullPage:false});
  const outsider=await browser.newContext();
  try{
   const response=await outsider.request.post(ready.origin+'/api/account/register',{data:{name:'IsolatedReviewAccount',password:'synthetic-fixture-password'}});
   check('Synthetic other account registered',response.status()===200);await response.dispose();
   for(const doc of ready.documents){
    for(const run of [doc.old_run,doc.current_run]){
     for(const target of [`/api/documents/${doc.id}/structure?run_id=${run}`,`/api/documents/${doc.id}/pages/1?run_id=${run}`]){
      const result=await outsider.request.get(ready.origin+target);check('Other account cannot access fixed source '+target,result.status()===404);await result.dispose();
     }
    }
   }
  }finally{await outsider.close();}
  check('Browser has no page errors or external requests',!report.pageErrors.length&&!report.externalRequests.length);
 }catch(error){report.failure=String(error);throw error;}
 finally{
  if(context)await context.close();if(browser)await browser.close();
  child.stdin.end('stop\n');
  await new Promise(resolve=>{if(child.exitCode!==null)return resolve();const timer=setTimeout(()=>{child.kill();resolve();},10000);child.once('exit',()=>{clearTimeout(timer);resolve();});});
  if(ready){
   const completion=path.join(ready.output,'completion.json');
   report.checks.push({name:'Main data, originals and fixed history unchanged; server stopped; zero model calls',passed:fs.existsSync(completion)&&child.exitCode===0});
   if(stderr)fs.writeFileSync(path.join(ready.output,'fixture-stderr.txt'),stderr);
   report.passed=!report.failure&&report.checks.every(c=>c.passed);
   fs.writeFileSync(path.join(ready.output,'browser-report.json'),JSON.stringify(report,null,2));
  }
 }
 assert.ok(report.passed,'Acceptance failed; inspect saved report');
 console.log(JSON.stringify({output:ready.output,checks:report.checks.length,passed:report.passed}));
}
main().catch(error=>{console.error(error);process.exitCode=1;});
