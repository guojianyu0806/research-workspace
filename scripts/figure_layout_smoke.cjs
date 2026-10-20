'use strict';
const fs=require('node:fs'),path=require('node:path'),assert=require('node:assert/strict');
const {spawn,spawnSync}=require('node:child_process');
const {chromium}=require(process.env.PLAYWRIGHT_MODULE||'C:/Users/g2806/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright');
const root=path.resolve(__dirname,'..'),python=path.join(root,'.venv','Scripts','python.exe');
async function main(){
 const child=spawn(python,['-u',path.join(__dirname,'figure_layout_fixture.py')],{cwd:root,windowsHide:true,stdio:['pipe','pipe','pipe']});
 let ready,browser,context,stderr='';child.stderr.on('data',data=>stderr+=data);
 const report={checks:[],errors:[],external:[],bounds:[],scope:'30 frozen synthetic radar/diagram contracts; no model/provider calls.'};
 const check=(name,passed)=>{report.checks.push({name,passed:Boolean(passed)});assert.ok(passed,name);};
 try{
  ready=await new Promise((resolve,reject)=>{let text='';const timer=setTimeout(()=>reject(Error('Fixture timeout')),30000);child.stdout.on('data',data=>{text+=data;const end=text.indexOf('\n');if(end<0)return;clearTimeout(timer);try{resolve(JSON.parse(text.slice(0,end)));}catch{reject(Error('Invalid completed fixture startup frame'));}});child.once('exit',code=>{clearTimeout(timer);reject(Error('Fixture exited '+code));});});
  ready.cases=JSON.parse(fs.readFileSync(path.join(ready.fixture,'manifest.json'),'utf8')).cases;
  browser=await chromium.launch({headless:true,executablePath:process.env.BROWSER_EXECUTABLE||'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe'});
  context=await browser.newContext({viewport:{width:1440,height:950}});
  await context.route('**/*',route=>{if(new URL(route.request().url()).origin!==ready.origin){report.external.push(route.request().url());return route.abort();}return route.continue();});
  const page=await context.newPage(),probe=await context.newPage();page.setDefaultTimeout(15000);
  for(const p of [page,probe]){p.on('pageerror',e=>report.errors.push(String(e)));p.on('dialog',async d=>{report.errors.push('Unexpected dialog');await d.dismiss();});}
  const records=new Map();
  for(const c of ready.cases){
   const p=c.payload,folder=path.join(ready.output,c.id);let svg,script,record;
   if(c.kind==='radar'){
    const quote=v=>'"'+String(v).replaceAll('"','""')+'"';
    const rows=[['metric',...p.series.map(s=>s.name)],...p.labels.map((label,i)=>[label,...p.series.map(s=>s.values[i])])];
    const csv=Buffer.from(rows.map(r=>r.map(quote).join(',')).join('\n'),'utf8');
    const imported=await context.request.post(ready.origin+'/api/datasets',{multipart:{file:{name:c.id+'.csv',mimeType:'text/csv',buffer:csv}}});
    let dataset;try{check(c.id+' CSV import succeeds',imported.status()===201);dataset=(await imported.json()).dataset;}finally{await imported.dispose();}
    const response=await context.request.post(ready.origin+`/api/datasets/${dataset.id}/charts`,{data:{kind:'radar3d',x:'metric',y:p.series.map(s=>s.name),units:Object.fromEntries(p.series.map(s=>[s.name,s.unit])),title:p.title,normalize:p.normalize,yaw:p.yaw,elevation:p.elevation}});
    try{
     check(c.id+' explicit validation result',c.accepted?response.status()===200:response.status()===(c.id==='invalid-yaw'||c.id==='invalid-elevation'?422:400));
     if(!c.accepted)continue;record=(await response.json()).chart;record.fixture_dataset_id=dataset.id;
    }finally{await response.dispose();}
    for(const format of ['svg','py']){const exported=await context.request.get(ready.origin+`/api/charts/${record.id}/${format}`);try{check(c.id+' '+format+' export',exported.status()===200);if(format==='svg')svg=await exported.text();else script=await exported.text();}finally{await exported.dispose();}}
   }else{
    const response=await context.request.post(ready.origin+'/api/diagrams',{data:p});
    try{check(c.id+' explicit validation result',c.accepted?response.status()===200:response.status()===400);if(!c.accepted)continue;svg=await response.text();}finally{await response.dispose();}
    const exported=await context.request.post(ready.origin+'/api/diagrams/script',{data:p});
    try{check(c.id+' script and version exported',exported.status()===200);record=await exported.json();script=record.script;check(c.id+' explicit diagram-v2',record.layout_version==='diagram-v2');}finally{await exported.dispose();}
   }
   fs.mkdirSync(folder);fs.writeFileSync(path.join(folder,'served.svg'),svg);fs.writeFileSync(path.join(folder,'reproduce.py'),script);fs.writeFileSync(path.join(folder,'record.json'),JSON.stringify(record,null,2));records.set(c.id,record);
   const output=c.kind==='radar'?'chart.svg':'architecture.svg';
   const run=spawnSync(python,['-I',path.join(folder,'reproduce.py')],{cwd:folder,windowsHide:true,encoding:'utf8'});
   check(c.id+' standalone byte reproduction',run.status===0&&fs.readFileSync(path.join(folder,output),'utf8')===svg);
   const rerun=spawnSync(python,['-I',path.join(folder,'reproduce.py')],{cwd:folder,windowsHide:true});
   check(c.id+' existing output preserved',rerun.status!==0&&fs.readFileSync(path.join(folder,output),'utf8')===svg);
   await probe.setContent(svg);await probe.evaluate(()=>document.fonts.ready);
   const boxes=await probe.evaluate(()=>{
    const svg=document.querySelector('svg'),v=svg.viewBox.baseVal;
    return [...svg.querySelectorAll('text')].map(n=>{const b=n.getBBox();return {role:n.dataset.role,text:n.textContent,bbox:[b.x,b.y,b.width,b.height],font:Number(n.getAttribute('font-size')),inside:b.x>=-.5&&b.y>=-.5&&b.x+b.width<=v.width+.5&&b.y+b.height<=v.height+.5};});
   });report.bounds.push({id:c.id,boxes});
   check(c.id+' actual full text bounds and readable font',boxes.length>0&&boxes.every(b=>b.inside&&b.font>=14));
   let overlap=false;for(let i=0;i<boxes.length;i++)for(let j=0;j<i;j++){const a=boxes[i].bbox,b=boxes[j].bbox;if(Math.min(a[0]+a[2],b[0]+b[2])-Math.max(a[0],b[0])>1&&Math.min(a[1]+a[3],b[1]+b[3])-Math.max(a[1],b[1])>1)overlap=true;}
   check(c.id+' text blocks do not overlap',!overlap);
  }
  await page.goto(ready.origin);await page.locator('#connectionState.is-connected').waitFor();
  await page.locator('.module-navigation').getByRole('button',{name:'数据与图表',exact:true}).click();
  const c=ready.cases.find(c=>c.id==='camera--90-75'),p=c.payload,selected=records.get(c.id);
  await page.getByRole('combobox',{name:'已导入数据集',exact:true}).selectOption(selected.fixture_dataset_id);
  await page.getByRole('heading',{name:c.id+'.csv',exact:true}).waitFor();
  await page.getByRole('combobox',{name:'图表类型',exact:true}).selectOption('radar3d');
  const choices=page.locator('.rs-y-choices input');for(let i=0;i<await choices.count();i++){const input=choices.nth(i);if(await input.isDisabled())continue;const value=await input.inputValue();await input.setChecked(p.series.some(s=>s.name===value));}
  await page.getByRole('textbox',{name:'图表标题',exact:true}).fill(p.title);
  await page.getByRole('spinbutton',{name:'雷达图方位角（度）',exact:true}).fill(String(p.yaw));
  await page.getByRole('spinbutton',{name:'雷达图仰角（度）',exact:true}).fill(String(p.elevation));
  const units=page.locator('.rs-unit-fields input');for(let i=0;i<await units.count();i++)await units.nth(i).fill(p.series[i].unit);
  await page.getByRole('button',{name:'生成图表与图注',exact:true}).click();
  const chartOutput=page.locator('.rs-chart-output:not([hidden])');await chartOutput.waitFor();
  const details=chartOutput.locator('.rs-plot-values');await details.locator('summary').click();await details.locator('tbody tr').first().waitFor();
  check('Radar complete data table retains all twelve axes',await details.locator('tbody tr').count()===12);
  async function widths(region,prefix,nativeWidth){
   for(const width of [1440,1024,760,390]){
    await page.setViewportSize({width,height:950});await region.scrollIntoViewIfNeeded();
    const sizes=await region.evaluate(n=>({client:n.clientWidth,scroll:n.scrollWidth,image:n.querySelector('img').getBoundingClientRect().width}));
    check(prefix+' '+width+' native readable image and local scrolling',sizes.image>=nativeWidth-.5&&sizes.scroll>=Math.ceil(sizes.image)-1);
    check(prefix+' '+width+' no page overflow',await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1));
    await page.screenshot({path:path.join(ready.output,prefix+'-'+width+'.png'),fullPage:false});
   }
  }
  await widths(chartOutput.locator('.rs-chart-scroll'),'radar',1200);
  const diagramCase=ready.cases.find(c=>c.id==='thirty-nodes');
  await page.getByRole('textbox',{name:'节点JSON数组 · 每项含id和label',exact:true}).fill(JSON.stringify(diagramCase.payload.nodes));
  await page.getByRole('textbox',{name:'连接JSON数组 · 每项含source和target',exact:true}).fill(JSON.stringify(diagramCase.payload.edges));
  await page.getByRole('button',{name:'渲染结构图',exact:true}).click();
  const diagram=page.locator('.rs-diagram-form .rs-chart-scroll');await diagram.waitFor();
  await widths(diagram,'diagram',records.get('thirty-nodes').layout.width);
  const downloading=page.waitForEvent('download');await page.locator('.rs-diagram-form').getByRole('link',{name:'下载节点与连接JSON',exact:true}).click();
  const downloaded=await downloading,file=path.join(ready.output,'diagram-ui.json');await downloaded.saveAs(file);
  const exported=JSON.parse(fs.readFileSync(file,'utf8'));
  check('Downloaded diagram JSON retains original nodes, edges and layout version',exported.layout_version==='diagram-v2'&&JSON.stringify(exported.nodes)===JSON.stringify(diagramCase.payload.nodes)&&JSON.stringify(exported.edges)===JSON.stringify(diagramCase.payload.edges));
  const outsider=await browser.newContext();try{
   const login=await outsider.request.post(ready.origin+'/api/account/register',{data:{name:'FigureIsolationReader',password:'synthetic-only-password'}});check('Other synthetic account registered',login.status()===200);await login.dispose();
   for(const target of [`/api/datasets/${selected.fixture_dataset_id}`,...['svg','py','json'].map(f=>`/api/charts/${selected.id}/${f}`)]){
    const response=await outsider.request.get(ready.origin+target);check('Other account cannot read '+target,response.status()===404);await response.dispose();
   }
  }finally{await outsider.close();}
  check('No browser errors, dialogs or external traffic',!report.errors.length&&!report.external.length);
 }catch(error){report.failure=String(error);throw error;}
 finally{
  if(context)await context.close();if(browser)await browser.close();child.stdin.end('stop\n');
  await new Promise(resolve=>{if(child.exitCode!==null)return resolve();const timer=setTimeout(()=>{child.kill();resolve();},10000);child.once('exit',()=>{clearTimeout(timer);resolve();});});
  if(ready){report.checks.push({name:'Main selected data unchanged, no model calls, fixture stopped',passed:child.exitCode===0&&fs.existsSync(path.join(ready.output,'completion.json'))});report.passed=!report.failure&&report.checks.every(c=>c.passed);fs.writeFileSync(path.join(ready.output,'browser-report.json'),JSON.stringify(report,null,2));if(stderr)fs.writeFileSync(path.join(ready.output,'fixture-stderr.txt'),stderr);}
 }
 assert.ok(report.passed);console.log(JSON.stringify({output:ready.output,checks:report.checks.length,passed:true}));
}
main().catch(e=>{console.error(e);process.exitCode=1;});
