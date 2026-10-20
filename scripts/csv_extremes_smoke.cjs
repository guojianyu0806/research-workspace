/* Synthetic frozen boundaries, real local APIs and browser typography. */
'use strict';
const fs=require('node:fs'),path=require('node:path'),assert=require('node:assert/strict');
const {spawn,spawnSync}=require('node:child_process');
const {chromium}=require(process.env.PLAYWRIGHT_MODULE||'C:/Users/g2806/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright');
const root=path.resolve(__dirname,'..'),python=path.join(root,'.venv','Scripts','python.exe');
async function main(){
 const child=spawn(python,['-u',path.join(__dirname,'csv_extremes_fixture.py')],{cwd:root,windowsHide:true,stdio:['pipe','pipe','pipe']});
 let ready,browser,context,stderr='';child.stderr.on('data',data=>stderr+=data);
 const report={checks:[],errors:[],external:[],textBounds:[],scope:'30 synthetic CSV contracts, local API and four viewport widths. No Pro/official data.'};
 const check=(name,passed)=>{report.checks.push({name,passed:Boolean(passed)});assert.ok(passed,name);};
 try{
  ready=await new Promise((resolve,reject)=>{let text='';const timer=setTimeout(()=>reject(Error('Fixture timeout')),30000);child.stdout.on('data',data=>{text+=data;const row=text.split('\n').find(s=>s.startsWith('{'));if(row){clearTimeout(timer);resolve(JSON.parse(row));}});child.once('exit',code=>{clearTimeout(timer);reject(Error('Fixture exited '+code));});});
  browser=await chromium.launch({headless:true,executablePath:process.env.BROWSER_EXECUTABLE||'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe'});
  context=await browser.newContext({viewport:{width:1440,height:950}});
  await context.route('**/*',route=>{if(new URL(route.request().url()).origin!==ready.origin){report.external.push(route.request().url());return route.abort();}return route.continue();});
  const page=await context.newPage(),probe=await context.newPage();page.setDefaultTimeout(12000);
  page.on('pageerror',e=>report.errors.push(String(e)));probe.on('dialog',async d=>{report.errors.push('Unexpected script dialog');await d.dismiss();});
  const datasets=new Map(),charts=new Map();
  for(const c of ready.cases){
   const uploaded=await context.request.post(ready.origin+'/api/datasets',{multipart:{file:{name:c.path,mimeType:'text/csv',buffer:fs.readFileSync(path.join(ready.fixture,c.path))}}});
   try{
    check(c.id+' import status matches contract',c.import_accepted?uploaded.status()===201:uploaded.status()===400);
    if(!c.import_accepted)continue;
    const data=(await uploaded.json()).dataset;datasets.set(c.id,data);
    check(c.id+' full row count and original SHA',data.row_count===c.row_count&&data.sha256===c.sha256);
    const made=await context.request.post(ready.origin+`/api/datasets/${data.id}/charts`,{data:c.options});
    try{
     check(c.id+' chart accepts full scope or explicitly rejects',c.chart_accepted?made.status()===200:made.status()===400);
     if(!c.chart_accepted)continue;
     const chart=(await made.json()).chart;charts.set(c.id,chart);
     const folder=path.join(ready.output,c.id);fs.mkdirSync(folder);
     const exported={};for(const format of ['svg','py','json']){
      const response=await context.request.get(ready.origin+`/api/charts/${chart.id}/${format}`);
      try{check(c.id+' '+format+' export successful',response.status()===200);exported[format]=await response.text();fs.writeFileSync(path.join(folder,(format==='svg'?'served.':'chart.')+format),exported[format]);}finally{await response.dispose();}
     }
     check(c.id+' JSON keeps complete fixed plotting data',JSON.stringify(JSON.parse(exported.json))===JSON.stringify(chart));
     const run=spawnSync(python,['-I',path.join(folder,'chart.py')],{cwd:folder,windowsHide:true,encoding:'utf8'});
     check(c.id+' standalone script matches served SVG',run.status===0&&fs.readFileSync(path.join(folder,'chart.svg'),'utf8')===exported.svg);
     // Preserve first SVG; the script must reject replacing it on rerun.
     const rerun=spawnSync(python,['-I',path.join(folder,'chart.py')],{cwd:folder,windowsHide:true});
     check(c.id+' rerun preserves existing SVG',rerun.status!==0&&fs.readFileSync(path.join(folder,'chart.svg'),'utf8')===exported.svg);
     if(chart.spec.kind!=='radar3d'){
      await probe.setContent(exported.svg);await probe.evaluate(()=>document.fonts.ready);
      const bounds=await probe.evaluate(()=>{
       const svg=document.querySelector('svg'),v=svg.viewBox.baseVal;
       return [...svg.querySelectorAll('text[data-role]')].map(n=>{const b=n.getBBox();return {role:n.dataset.role,text:n.textContent,bbox:[b.x,b.y,b.width,b.height],font:Number(n.getAttribute('font-size')),inside:b.x>=-.5&&b.y>=-.5&&b.x+b.width<=v.width+.5&&b.y+b.height<=v.height+.5};});
      });
      report.textBounds.push({case:c.id,bounds});
      check(c.id+' actual browser glyph bounds remain inside canvas at >=14px',bounds.length>0&&bounds.every(b=>b.inside&&b.font>=14));
      const labels=bounds.filter(b=>b.role==='category').sort((a,b)=>a.bbox[0]-b.bbox[0]);
      check(c.id+' category label boxes do not overlap',labels.every((b,i)=>i===0||labels[i-1].bbox[0]+labels[i-1].bbox[2]<=b.bbox[0]+1));
     }
    }finally{await made.dispose();}
   }finally{await uploaded.dispose();}
  }
  await page.goto(ready.origin);await page.locator('#connectionState.is-connected').waitFor();
  await page.locator('.module-navigation').getByRole('button',{name:'数据与图表',exact:true}).click();
  for(const id of ['long-chinese','rows-500','huge-normalized','markup-as-data']){
   const c=ready.cases.find(c=>c.id===id),data=datasets.get(id);
   await page.getByRole('combobox',{name:'已导入数据集',exact:true}).selectOption(data.id);
   await page.getByRole('heading',{name:c.path,exact:true}).waitFor();
   await page.getByRole('combobox',{name:'图表类型',exact:true}).selectOption(c.options.kind);
   await page.getByRole('combobox',{name:'横轴字段',exact:true}).selectOption(c.options.x);
   const choices=page.locator('.rs-y-choices input');
   for(let i=0;i<await choices.count();i++){
    const input=choices.nth(i);if(await input.isDisabled())continue;
    if(c.options.y.includes(await input.inputValue()))await input.check();else await input.uncheck();
   }
   await page.getByRole('textbox',{name:'图表标题',exact:true}).fill(c.options.title);
   await page.getByRole('checkbox',{name:'各数值列按Min–Max归一化',exact:true}).setChecked(c.options.normalize);
   const units=page.locator('.rs-unit-fields input');
   for(let i=0;i<await units.count();i++)await units.nth(i).fill(c.options.units[c.options.y[i]]||'');
   await page.getByRole('button',{name:'生成图表与图注',exact:true}).click();
   const output=page.locator('.rs-chart-output:not([hidden])');await output.waitFor();
   const values=output.locator('.rs-plot-values');await values.locator('summary').click();
   await values.locator('tbody tr').first().waitFor();
   check(id+' complete data table retains all accepted records',await values.locator('tbody tr').count()===(id==='rows-500'?500:3-(id==='markup-as-data'?1:0)));
   for(const width of [1440,1024,760,390]){
    await page.setViewportSize({width,height:950});
    const viewport=output.locator('.rs-chart-scroll');await viewport.scrollIntoViewIfNeeded();
    const sizes=await viewport.evaluate(n=>({client:n.clientWidth,scroll:n.scrollWidth,image:n.querySelector('img').getBoundingClientRect().width}));
    check(id+' '+width+' local plot scroll and readable native image',sizes.client>0&&sizes.image>=charts.get(id).spec.layout.width-.5&&sizes.scroll>=Math.ceil(sizes.image)-1);
    check(id+' '+width+' page has no horizontal overflow',await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1));
    await page.screenshot({path:path.join(ready.output,id+'-'+width+'.png'),fullPage:false});
   }
  }
  const outsider=await browser.newContext();try{
   const registered=await outsider.request.post(ready.origin+'/api/account/register',{data:{name:'IsolatedChartReader',password:'synthetic-only-password'}});check('Other synthetic account registered',registered.status()===200);await registered.dispose();
   for(const id of ['long-chinese','rows-500']){
    for(const target of [`/api/datasets/${datasets.get(id).id}`,...['svg','py','json'].map(f=>`/api/charts/${charts.get(id).id}/${f}`)]){
     const response=await outsider.request.get(ready.origin+target);check('Other account cannot read '+target,response.status()===404);await response.dispose();
    }
   }
  }finally{await outsider.close();}
  check('No unexpected page script or external traffic',!report.errors.length&&!report.external.length);
 }catch(error){report.failure=String(error);throw error;}
 finally{
  if(context)await context.close();if(browser)await browser.close();child.stdin.end('stop\n');
  await new Promise(resolve=>{if(child.exitCode!==null)return resolve();const timer=setTimeout(()=>{child.kill();resolve();},10000);child.once('exit',()=>{clearTimeout(timer);resolve();});});
  if(ready){report.checks.push({name:'Main data unchanged; zero model calls; server stopped',passed:child.exitCode===0&&fs.existsSync(path.join(ready.output,'completion.json'))});report.passed=!report.failure&&report.checks.every(c=>c.passed);fs.writeFileSync(path.join(ready.output,'browser-report.json'),JSON.stringify(report,null,2));if(stderr)fs.writeFileSync(path.join(ready.output,'fixture-stderr.txt'),stderr);}
 }
 assert.ok(report.passed,'Acceptance did not pass');console.log(JSON.stringify({output:ready.output,checks:report.checks.length,passed:true}));
}
main().catch(e=>{console.error(e);process.exitCode=1;});
