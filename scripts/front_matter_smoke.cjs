/* Current UI + real local HTTP on isolated copies of three public PDFs. */
'use strict';
const fs=require('node:fs'),path=require('node:path'),assert=require('node:assert/strict');
const {spawn}=require('node:child_process');
const {chromium}=require(process.env.PLAYWRIGHT_MODULE||'C:/Users/g2806/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright');
const root=path.resolve(__dirname,'..');
async function main(){
 const child=spawn(path.join(root,'.venv','Scripts','python.exe'),['-u',path.join(__dirname,'front_matter_acceptance.py'),'--serve'],{cwd:root,windowsHide:true,stdio:['pipe','pipe','pipe']});
 let stderr='';child.stderr.on('data',data=>stderr+=data);
 const ready=await new Promise((resolve,reject)=>{let text='';const timer=setTimeout(()=>reject(Error('Fixture timeout: '+stderr)),45000);child.stdout.on('data',data=>{text+=data;const line=text.split('\n').find(s=>s.startsWith('{'));if(line){clearTimeout(timer);resolve(JSON.parse(line));}});child.on('exit',code=>{clearTimeout(timer);reject(Error('Fixture exit '+code+': '+stderr));});});
 const report={checks:[],pageErrors:[],externalRequests:[],scope:'Existing public samples, isolated loopback HTTP; no Pro requests.'};
 const check=(name,ok)=>{report.checks.push({name,passed:Boolean(ok)});assert.ok(ok,name);};
 const browser=await chromium.launch({headless:true,executablePath:process.env.BROWSER_EXECUTABLE||'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe'});
 try{
  const context=await browser.newContext({viewport:{width:1440,height:950}});
  await context.route('**/*',route=>{if(new URL(route.request().url()).origin!==ready.origin){report.externalRequests.push(route.request().url());return route.abort();}return route.continue();});
  const page=await context.newPage();page.setDefaultTimeout(10000);page.on('pageerror',error=>report.pageErrors.push(String(error)));
  await page.goto(ready.origin);await page.locator('#connectionState.is-connected').waitFor();
  const moduleButton=name=>page.locator('.module-navigation').getByRole('button',{name,exact:true});
  await moduleButton('文献管理').click();
  const selected=ready.documents.find(d=>d.filename==='1706.03762v7.pdf');
  await page.getByRole('combobox',{name:'选择文献，含已归档文献'}).selectOption(selected.id);
  await page.getByRole('tab',{name:'标题与引用',exact:true}).click();
  const panel=page.locator('.rm-front-matter');await panel.locator('summary').waitFor();
  check('Metadata stays compact until expanded',!(await panel.getAttribute('open')));
  await panel.locator('summary').click();
  check('Eight actual author names displayed',await panel.locator('.rm-front-list>li').count()===8);
  const illia=panel.locator('.rm-front-row').filter({hasText:'Illia Polosukhin'});
  check('No institution fabricated for email-only author',(await illia.innerText()).includes('机构未确认')&&await illia.getByRole('button',{name:'核对机构原文',exact:true}).count()===0);
  check('Source audit is distinguished from human verification',(await panel.innerText()).includes('不等于已人工确认'));
  const result=await (await context.request.get(ready.origin+'/api/documents/'+selected.id+'/structure')).json();
  const ashish=result.front_matter.authors.find(a=>a.name==='Ashish Vaswani');
  const row=panel.locator('.rm-front-row').filter({hasText:'Ashish Vaswani'});
  await row.getByRole('button',{name:'核对姓名原文',exact:true}).click();
  await page.locator('#evidenceHighlight:not([hidden])').waitFor();
  check('Name source opens current original page',await page.locator('#pageNumber').inputValue()==='1');
  const highlight=await page.locator('#evidenceHighlight').evaluate(node=>({left:parseFloat(node.style.left)/100,top:parseFloat(node.style.top)/100,width:parseFloat(node.style.width)/100,height:parseFloat(node.style.height)/100}));
  check('Highlight equals saved author block coordinates',Math.abs(highlight.left-ashish.evidence.bbox[0])<.00001&&Math.abs(highlight.top-ashish.evidence.bbox[1])<.00001);
  const back=page.locator('#readerReturnButton');await back.click();await panel.waitFor({state:'visible'});
  check('Return restores author metadata panel',await panel.locator('.rm-front-list').isVisible());
  await panel.locator('.rm-front-row').filter({hasText:'Ashish Vaswani'}).getByRole('button',{name:'核对机构原文',exact:true}).click();
  await page.locator('#evidenceHighlight:not([hidden])').waitFor();await back.click();
  check('Affiliation source also supports return',await panel.isVisible());
  await page.screenshot({path:path.join(ready.output,'authors-desktop.png'),fullPage:false});
  const bert=ready.documents.find(d=>d.title.startsWith('BERT:'));
  await page.getByRole('combobox',{name:'选择文献，含已归档文献'}).selectOption(bert.id);
  await panel.locator('summary').filter({hasText:'4 位作者候选'}).waitFor();await panel.locator('summary').click();
  check('Switching document renders only its four authors',await panel.locator('.rm-front-list>li').count()===4&&!(await panel.innerText()).includes('Vaswani'));
  check('Shared affiliation is rendered for each BERT author',await panel.getByRole('button',{name:'核对机构原文',exact:true}).count()===4);
  await page.setViewportSize({width:760,height:950});
  check('Narrow layout has no document overflow',await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1));
  await page.screenshot({path:path.join(ready.output,'authors-narrow.png'),fullPage:false});
  check('No browser errors or external traffic',!report.pageErrors.length&&!report.externalRequests.length);
 }catch(error){report.failure=String(error);throw error;}finally{
  await browser.close();child.stdin.end('stop\n');
  await new Promise(resolve=>{if(child.exitCode!==null)return resolve();const timer=setTimeout(()=>{child.kill();resolve();},8000);child.once('exit',()=>{clearTimeout(timer);resolve();});});
  check('Main records and original PDFs remain unchanged',fs.existsSync(path.join(ready.output,'main-unchanged.json')));
  fs.writeFileSync(path.join(ready.output,'browser-report.json'),JSON.stringify(report,null,2));
  if(stderr)fs.writeFileSync(path.join(ready.output,'fixture-stderr.txt'),stderr);
 }
 console.log(JSON.stringify({output:ready.output,checks:report.checks.length,passed:true}));
}
main().catch(error=>{console.error(error);process.exitCode=1;});
