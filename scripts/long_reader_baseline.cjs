/* Observe current eager rendering. A recorded bottleneck is not an acceptance pass. */
'use strict';
const fs=require('node:fs/promises'),path=require('node:path'),crypto=require('node:crypto');
const {spawnSync}=require('node:child_process');
const {chromium}=require(process.env.PLAYWRIGHT_MODULE||'C:/Users/g2806/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright');
const root=path.resolve(__dirname,'..');
async function main(){
 const output=path.join(root,'artifacts','long-reader-baseline',new Date().toISOString().replace(/[:.]/g,'-'));
 await fs.mkdir(output,{recursive:true});
 const hashes={};for(const name of ['research_workspace/static/bilingual.js','research_workspace/static/bilingual.css'])hashes[name]=crypto.createHash('sha256').update(await fs.readFile(path.join(root,name))).digest('hex');
 const generated=spawnSync(path.join(root,'.venv','Scripts','python.exe'),['-c',"import json;from scripts.freeze_long_reading import synthetic_blocks;print(json.dumps(synthetic_blocks(5000),ensure_ascii=False))"],{cwd:root,encoding:'utf8',env:{...process.env,PYTHONIOENCODING:'utf-8'},windowsHide:true,maxBuffer:16*1024*1024});
 if(generated.status!==0)throw Error('Frozen capacity input generation failed: '+generated.stderr);
 const blocks=JSON.parse(generated.stdout);
 const browser=await chromium.launch({headless:true,executablePath:process.env.BROWSER_EXECUTABLE||'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe'});
 const context=await browser.newContext({viewport:{width:1440,height:1000}}),errors=[],external=[];
 await context.route('**/*',route=>{external.push(route.request().url());return route.abort();});
 await context.tracing.start({screenshots:true,snapshots:true,sources:true});
 const page=await context.newPage();page.on('pageerror',error=>errors.push(String(error)));
 let result;
 try{
  await page.setContent('<!doctype html><meta charset="utf-8"><title>合成长结果基线</title><main id="root"></main>');
  await page.addStyleTag({path:path.join(root,'research_workspace/static/bilingual.css')});
  await page.addScriptTag({path:path.join(root,'research_workspace/static/bilingual.js')});
  await page.evaluate(()=>{window.longTasks=[];new PerformanceObserver(list=>window.longTasks.push(...list.getEntries().map(e=>({start:e.startTime,duration:e.duration})))).observe({type:'longtask',buffered:true});});
  result=await page.evaluate(blocks=>{
   const start=performance.now();
   window.reader=window.ResearchBilingual.render(document.querySelector('#root'),{status:'completed',target_language:'zh',total_blocks:blocks.length,blocks});
   return {render_ms:performance.now()-start,source_body_dom:document.querySelectorAll('.rb-panel-source .rb-block').length,
    translation_body_dom:document.querySelectorAll('.rb-panel-translation .rb-block').length,total_saved_blocks:blocks.length};
  },blocks);
  await page.waitForTimeout(300);
  result.long_tasks=await page.evaluate(()=>window.longTasks);
  await page.screenshot({path:path.join(output,'current-5000.png')});
  result.dom_gate_met=result.source_body_dom<=120&&result.translation_body_dom<=120;
  result.scroll_gate_measured=false;result.scope='Baseline observation only; all blocks supplied directly, no model call or paged frontend claim.';
  result.errors=errors;result.external_requests=external;result.runtime_sha256=hashes;
  for(const [name,hash]of Object.entries(hashes))if(hash!==crypto.createHash('sha256').update(await fs.readFile(path.join(root,name))).digest('hex'))throw Error('Runtime changed during observation');
  if(errors.length||external.length)throw Error('Unexpected browser errors or network');
  await fs.writeFile(path.join(output,'report.json'),JSON.stringify(result,null,2),{flag:'wx'});
  console.log(JSON.stringify({output,...result}));
 }finally{await context.tracing.stop({path:path.join(output,'trace.zip')});await browser.close();}
}
main().catch(error=>{console.error(error);process.exitCode=1;});
