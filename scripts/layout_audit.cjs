/* Local UI layout audit. Existing Playwright/Edge only; installs nothing.
 * --live-baseline reads a local service and blocks all writes/external requests.
 * The default audit supplies synthetic data through browser routes and never
 * contacts the service or reads browser credentials/storage.
 * --shell-only reuses those routes for six-module frame consistency checks.
 * Every run preserves fresh screenshots and JSON under artifacts/browser.
 */
'use strict';
const fs = require('node:fs');
const path = require('node:path');
const crypto = require('node:crypto');
const {execFileSync} = require('node:child_process');
const {chromium} = require(process.env.PLAYWRIGHT_MODULE || 'C:/Users/g2806/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright');

const root = path.resolve(__dirname, '..');
const live = process.argv.includes('--live-baseline');
const shellOnly = process.argv.includes('--shell-only');
if (live && shellOnly) throw new Error('--shell-only uses offline fixtures; it cannot be combined with --live-baseline.');
const origin = new URL(process.env.BASE_URL || 'http://127.0.0.1:8765').origin;
if (!['127.0.0.1', 'localhost', '[::1]'].includes(new URL(origin).hostname)) throw new Error('Only local origins are supported.');
const output = path.join(root, 'artifacts/browser', `layout-${shellOnly ? 'shell' : live ? 'baseline' : 'fixture'}-${new Date().toISOString().replace(/[:.]/g, '-')}-${crypto.randomBytes(3).toString('hex')}`);
fs.mkdirSync(output, {recursive:true});
const report = {startedAt:new Date().toISOString(),output,mode:live?'read-only live baseline':'offline synthetic fixture',snapshots:[],behaviorChecks:[],pageErrors:[],consoleErrors:[],blockedRequests:[],source:{gitHead:execFileSync('git',['rev-parse','HEAD'],{cwd:root,encoding:'utf8',windowsHide:true}).trim(),sha256:{}},limitations:[
  'Automated layout checks do not verify model/research quality or replace visual inspection.',
  'Raster PDF content and SVG labels are excluded from UI text font-size checks.',
  'Contrast checks use computable flat CSS backgrounds; images/gradients and native select menus require visual inspection.',
  'Whitespace is a heuristic advisory; intentional empty states and reading canvases are not failures.'
]};
if(shellOnly){report.mode='offline shell consistency fixture';report.limitations.push('Shell mode checks shared frame geometry and styling across modules; it does not repeat the complete module content, reader interaction, or model quality suite.');}
const assets=new Map(fs.readdirSync(path.join(root,'research_workspace/static')).map(name=>[name,fs.readFileSync(path.join(root,'research_workspace/static',name))]));
for(const [name,content] of assets) if(/\.(css|html|js)$/.test(name)) report.source.sha256[name]=crypto.createHash('sha256').update(content).digest('hex');
report.source.sha256['scripts/layout_audit.cjs']=crypto.createHash('sha256').update(fs.readFileSync(__filename)).digest('hex');
let filled=false,accountMode=false,jobsPresent=true,datasetsPresent=true;
const longTitle='SYNTHETIC 布局测试：跨文献证据核对、长标题换行与研究任务可读性验证 — LongUnbrokenResearchIdentifier_ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789';
const prose='这段内容是布局测试数据，不代表真实科研结果。用于检查较长的中文段落、英文术语 Retrieval-Augmented Generation、数值 0.875 与原文证据是否保持清晰，避免按钮、说明与正文发生重叠。';
const timestamp='2026-10-09T07:00:00Z';
const run={id:'fixture-run',status:'completed',total_pages:3,pages_done:3,text_pages:3};
const docs=[0,1,2].map(index=>({id:'fixture-doc-'+index,title:index===0?longTitle:'SYNTHETIC 参考论文 '+index,filename:'SYNTHETIC-layout-'+index+'.pdf',authors:['Alice Fixture','李测试'],page_count:3,created_at:timestamp,current_run:run,available_run:run,sha256:'f'.repeat(64)}));
const evidence={document_id:docs[0].id,run_id:run.id,page:1,block_id:'block-0',evidence_id:'evidence-0',bbox:[.1,.12,.88,.3],text:prose,document_title:longTitle};
const reference={id:'reference-0',label:'1',text:longTitle+'. Example Journal, 2024. DOI: 10.0000/synthetic-layout-test.',evidence,citation_ids:['citation-0']};
const jobs=[
 {id:'job-qa',kind:'qa',result:{claims:[{text:prose.repeat(3),citations:[{evidence_id:evidence.evidence_id,quote:prose}]}],sources:[evidence],limitations:['合成夹具只用于排版，不作为模型效果证据。']}},
 {id:'job-translate',kind:'translate',result:{status:'partial',target_language:'zh',total_blocks:3,missing_pages:[{page:3,status:'needs_ocr'}],blocks:[0,1].map(index=>({...evidence,block_id:'block-'+index,source_text:'Synthetic English source text for layout and scroll verification. '.repeat(12),translated_text:prose.repeat(4)}))}},
 {id:'job-draft',kind:'draft',result:{sections:[{heading:'Abstract · 待验证研究框架',claims:[{text:prose.repeat(2),citations:[]}],proposed_work:prose}],references:[{title:longTitle,filename:docs[0].filename}],architecture:{nodes:[{id:'input',label:'合成输入'},{id:'output',label:'布局检查'}],edges:[{source:'input',target:'output'}]}}},
 {id:'job-future',kind:'future_work',result:{future_verification:{status:'evidence_insufficient',notice:prose,queries:[{query_id:'q1',query:'synthetic research interface layout long query '.repeat(3),status:'failed',reason:'合成网络状态，仅用于长错误说明排版。'+prose,retrieved_at:timestamp}],directions:[],sources:[],errors:[{code:'fixture',message:prose}]},sections:[{heading:'合成候选方向',proposed_work:prose,claims:[]}]}}
].map(job=>({...job,status:'completed',created_at:timestamp,updated_at:timestamp,events:[{created_at:timestamp,message:'合成任务执行记录，用于排版检查。'}],completed_items:[],progress:0,total:0}));
const headers=['metric_identifier_with_a_long_column_name','score_A','score_B','score_C'];
const dataset={id:'fixture-dataset',filename:'SYNTHETIC_长文件名和四列数值排版测试.csv',created_at:timestamp,row_count:3,encoding:'UTF-8',headers,columns:headers.map((name,index)=>({name,type:index?'number':'string',missing:0,numeric_count:index?3:0})),rows:[['accuracy',.72,.86,.78],['recall',.81,.73,.69],['LongUnbrokenMetricName_ABCDEFGHIJKLMNOPQRSTUVWXYZ',.68,.79,.91]]};
const papers=[{id:'fixture-paper',title:longTitle,authors:['Alice Fixture','Bob Fixture','李测试'],summary:prose.repeat(5),published:timestamp,retrieved_at:timestamp,source:'arxiv',source_url:'https://arxiv.org/abs/2401.00001',pdf_url:'https://arxiv.org/pdf/2401.00001',fulltext_status:'metadata_only',categories:['cs.CL','cs.AI'],code_links:[],feedback:0}];
const pageSvg='<svg xmlns="http://www.w3.org/2000/svg" width="600" height="800" viewBox="0 0 600 800"><rect width="600" height="800" fill="white"/><text x="45" y="65" font-size="24" fill="#173e41">SYNTHETIC PDF PAGE</text><text x="45" y="95" font-size="14" fill="#173e41">Layout fixture only — not academic evidence</text>'+Array.from({length:20},(_,i)=>`<rect x="45" y="${140+i*24}" width="${460-i%4*25}" height="5" fill="#667577"/>`).join('')+'</svg>';
function responseFor(url,method){
  const p=url.pathname;
  if(p==='/api/capabilities')return {mode:'local',ocr:true,llm:filled,max_upload_mb:50,intelligence:{configured:filled}};
  if(p==='/api/health')return {status:'ok',worker_running:true};
  if(p==='/api/account')return {user:accountMode?{mode:'account',name:'LongAccount_ABCDEFGHIJKLMNOPQRSTUVWXYZ12'}:{mode:'local'}};
  if(p==='/api/model')return {configured:false,defaultModel:'deepseek-flash'};
  if(p==='/api/documents')return {documents:filled?docs:[]};
  if(/^\/api\/documents\/[^/]+$/.test(p))return {document:docs.find(doc=>p.endsWith(doc.id))||docs[0]};
  if(p.endsWith('/versions'))return {family:{documents:docs.slice(0,2),current_document_id:docs[0].id},candidates:[{document:docs[2],reason:'same_arxiv_id',body_similarity:.82}]};
  if(p.endsWith('/structure'))return {headings:[{title:'1 Introduction · '+longTitle,level:1,evidence},{title:'2 Methods',level:1,evidence}],references:[reference],citations:[{id:'citation-0',marker:'[1]',reference_ids:[reference.id],evidence}]};
  if(p.endsWith('/diff'))return {changes:[{kind:'replace',before:evidence,after:{...evidence,text:prose.repeat(3)}}]};
  if(p.includes('/layout/pages/'))return {...evidence,status:'completed',metadata_candidates:{title:[{text:longTitle,evidence}],authors:[{text:'Alice Fixture, 李测试',evidence}],affiliations:[{text:'SYNTHETIC Institute for interface layout',evidence}]},tables:[{row_count:2,column_count:2,evidence,cells:[{row:0,column:0,text:'Method',evidence},{row:0,column:1,text:'Score',evidence},{row:1,column:0,text:'Synthetic Baseline',evidence},{row:1,column:1,text:'0.86',evidence}]}],formulas:[{text:'x = y + 2',latex:'x = y + 2',evidence}],figures:[],captions:[],reading_order:{columns:2,region_ids:[],limitations:prose},limitations:[prose]};
  if(p.endsWith('/references/ranked')){const row={reference_id:reference.id,label:'1',score:3,included:true,text:reference.text,reference,reasons:[{detail:prose,points:3}],contexts:[{marker:'[1]',citation_evidence:evidence,context_evidence:evidence}]};return {total_references:1,included:[row],filtered:[],items:[row]};}
  if(/\/pages\/\d+$/.test(p))return {...evidence,text:prose,blocks:[{id:'block-0',ordinal:0,text:prose.repeat(2),bbox:evidence.bbox,page:1},{id:'block-1',ordinal:1,text:longTitle,bbox:[.1,.4,.8,.6],page:1}]};
  if(p==='/api/search')return {hits:filled?[{...evidence,score:3,filename:docs[0].filename,snippet:prose,text:prose}]:[]};
  if(p==='/api/research/tasks')return {jobs:filled&&jobsPresent?jobs:[]};
  if(p.startsWith('/api/research/tasks/'))return {job:jobs.find(job=>p.endsWith(job.id))||jobs[0]};
  if(p==='/api/datasets')return {datasets:filled&&datasetsPresent?[dataset]:[]};
  if(p.endsWith('/charts'))return {chart:{id:'fixture-chart',spec:{title:'SYNTHETIC 数据图表排版结果'},caption:prose,transformations:{dropped_csv_rows:[],minmax_normalized:true}}};
  if(p.startsWith('/api/datasets/'))return {dataset};
  if(p==='/api/subscriptions')return {subscriptions:filled?[{id:'fixture-subscription',query:longTitle,enabled:true,interval_hours:24,field:'all',last_checked_at:timestamp,next_check_at:timestamp,last_error:'合成状态：上次请求没有完成，等待下次检查。'+prose}]:[]};
  if(p==='/api/recommendations'||p==='/api/discover/search')return {items:filled?papers:[],retrieved_at:timestamp,cached:true};
  if(p==='/api/graph')return {nodes:filled?docs.map((doc,index)=>({id:doc.id,document_id:doc.id,title:doc.title,kind:'local_document',fulltext_available:true,pagerank:.33,rank:index+1,evidence:[evidence]})):[],edges:filled?[{id:'fixture-edge',source:docs[0].id,target:docs[1].id,match_method:'doi',evidence:[evidence]}]:[],generated_at:timestamp,unresolved_count:filled?1:0,unresolved:filled?[{document_id:docs[0].id,reference_text:longTitle,reason:prose,evidence:[evidence]}]:[]};
  report.blockedRequests.push({method,path:p,reason:'unhandled fixture'});return {detail:'Unhandled fixture '+p};
}

function measure(){
  const dialog=document.querySelector('dialog[open]');
  const isVisible=el=>{if(!el.getClientRects().length||el.closest('[hidden],.visually-hidden,svg'))return false;if(dialog&&!dialog.contains(el))return false;for(let item=el;item;item=item.parentElement){const s=getComputedStyle(item);if(s.visibility==='hidden'||s.display==='none'||Number(s.opacity)===0)return false;}return true;};
  const id=el=>el.id?'#'+el.id:el.tagName.toLowerCase()+[...el.classList].slice(0,3).map(c=>'.'+c).join('');
  const rgba=value=>{const numbers=value.match(/[\d.]+/g)?.map(Number);return numbers?.length>=3?[numbers[0],numbers[1],numbers[2],numbers[3]??1]:null;};
  const over=(front,back)=>[0,1,2].map(i=>front[i]*front[3]+back[i]*(1-front[3])).concat(1);
  const lum=color=>color.slice(0,3).map(n=>n/255).map(n=>n<=.04045?n/12.92:((n+.055)/1.055)**2.4).reduce((sum,n,i)=>sum+n*[.2126,.7152,.0722][i],0);
  const background=el=>{let chain=[];for(let item=el;item;item=item.parentElement){const s=getComputedStyle(item);if(s.backgroundImage!=='none')return null;chain.unshift(rgba(s.backgroundColor));}return chain.reduce((back,front)=>front?over(front,back):back,[255,255,255,1]);};
  const smallText=[],contrastFailures=[],contrastUnmeasured=[],horizontalOverflow=[],controlTextOverflow=[],whitespaceAdvisories=[],svgLabels=[],svgLabelFailures=[],graphLabelOverlaps=[],graphHiddenLabels=[];
  const textElements=[...document.querySelectorAll('body *')].filter(el=>isVisible(el)&&!['SCRIPT','STYLE','OPTION','PROGRESS'].includes(el.tagName)&&(el.matches('input,select,textarea')||[...el.childNodes].some(n=>n.nodeType===3&&n.textContent.trim())));
  for(const el of textElements){
    const s=getComputedStyle(el),size=parseFloat(s.fontSize),text=(el.matches('input,textarea')?el.getAttribute('placeholder'):el.textContent)?.trim().slice(0,100)||el.getAttribute('aria-label')||'';
    if(!text)continue;
    const entry={selector:id(el),text,fontSize:size};if(size<13.99)smallText.push(entry);
    const control=el.closest('button');
    if(control&&control!==el){const box=control.getBoundingClientRect(),textBox=el.getBoundingClientRect();if(textBox.top<box.top-1||textBox.bottom>box.bottom+1||textBox.left<box.left-1||textBox.right>box.right+1)controlTextOverflow.push({...entry,control:id(control),controlHeight:box.height,textTop:textBox.top-box.top,textBottom:textBox.bottom-box.top});}
    if(el.matches(':disabled')||el.closest(':disabled'))continue;
    const bg=background(el),fg=rgba(s.color);
    if(!bg||!fg){contrastUnmeasured.push(entry);continue;}
    const required=size>=24||(size>=18.66&&parseInt(s.fontWeight)>=700)?3:4.5;
    function checkColor(color,foreground,extra={}){const a=lum(over(color,bg)),b=lum(bg),ratio=(Math.max(a,b)+.05)/(Math.min(a,b)+.05);if(ratio+.02<required)contrastFailures.push({...entry,...extra,foreground,background:bg.slice(0,3),ratio:Math.round(ratio*100)/100,required});}
    checkColor(fg,s.color);
    if(el.matches('input[placeholder],textarea[placeholder]')){const ps=getComputedStyle(el,'::placeholder'),color=rgba(ps.color);if(color){color[3]*=Number(ps.opacity);checkColor(color,ps.color,{selector:id(el)+'::placeholder'});}}
  }
  const intendedHorizontalScroll='.module-navigation,.rm-tabs,.rs-table-scroll,#readerViewport,.rb-scroll,.ex-graph-host';
  for(const el of document.querySelectorAll('body *')){if(!isVisible(el))continue;const rect=el.getBoundingClientRect();if(rect.width<1)continue;if(rect.left<-1||rect.right>innerWidth+1){let clipped=false;for(let ancestor=el.parentElement;ancestor;ancestor=ancestor.parentElement){if(ancestor.matches(intendedHorizontalScroll)&&/auto|scroll|hidden|clip/.test(getComputedStyle(ancestor).overflowX)){clipped=true;break;}}if(!clipped)horizontalOverflow.push({selector:id(el),left:rect.left,right:rect.right,width:rect.width});}}
  for(const el of document.querySelectorAll('.rs-card,.rm-card,.ex-subscription-panel,.ex-graph-details,.rs-task-result')){if(!isVisible(el)||el.offsetHeight<300)continue;const rect=el.getBoundingClientRect(),children=[...el.children].filter(isVisible),bottom=children.reduce((max,ch)=>Math.max(max,ch.getBoundingClientRect().bottom),rect.top),gap=rect.bottom-bottom;if(gap>180&&gap>rect.height*.35)whitespaceAdvisories.push({selector:id(el),height:rect.height,unusedBottom:Math.round(gap)});}
  const svgVisible=el=>el&&el.getClientRects().length&&!el.closest('[hidden]')&&getComputedStyle(el).visibility==='visible'&&Number(getComputedStyle(el).opacity)>0;
  for(const el of document.querySelectorAll('svg text'))if(svgVisible(el)){const fontSize=parseFloat(getComputedStyle(el).fontSize),matrix=el.getScreenCTM(),screenFontSize=fontSize*(matrix?Math.hypot(matrix.c,matrix.d):1),entry={selector:id(el),fontSize,screenFontSize:Math.round(screenFontSize*100)/100,text:el.textContent.slice(0,100)};svgLabels.push(entry);if(screenFontSize<13.9)svgLabelFailures.push(entry);}
  const graphNodes=[...document.querySelectorAll('.ex-network-node')].filter(el=>el.getClientRects().length&&!el.closest('[hidden]')).map(group=>({id:group.dataset.nodeId,label:group.querySelector('.ex-node-label'),circle:group.querySelector('circle'),rank:group.querySelector('.ex-node-rank')}));
  const visibleLabels=graphNodes.filter(node=>svgVisible(node.label));
  for(const node of graphNodes)if(node.label&&!svgVisible(node.label))graphHiddenLabels.push({nodeId:node.id,text:node.label.textContent});
  function overlaps(first,second){const a=first.getBoundingClientRect(),b=second.getBoundingClientRect(),width=Math.min(a.right,b.right)-Math.max(a.left,b.left),height=Math.min(a.bottom,b.bottom)-Math.max(a.top,b.top);return width>1&&height>1?{width:Math.round(width*100)/100,height:Math.round(height*100)/100}:null;}
  for(const [index,label] of visibleLabels.entries()){
    for(const target of graphNodes){if(target.id===label.id)continue;for(const kind of ['circle','rank']){if(!svgVisible(target[kind]))continue;const area=overlaps(label.label,target[kind]);if(area)graphLabelOverlaps.push({nodeId:label.id,otherNodeId:target.id,kind,...area});}}
    for(const other of visibleLabels.slice(index+1)){const area=overlaps(label.label,other.label);if(area)graphLabelOverlaps.push({nodeId:label.id,otherNodeId:other.id,kind:'label',...area});}
  }
  const dialogBounds=dialog?(()=>{const r=dialog.getBoundingClientRect();return {left:r.left,top:r.top,right:r.right,bottom:r.bottom,internallyScrollable:dialog.scrollHeight>dialog.clientHeight};})():null;
  return {viewport:{width:innerWidth,height:innerHeight},scrollWidth:document.documentElement.scrollWidth,bodyScrollWidth:document.body.scrollWidth,smallText,contrastFailures,contrastUnmeasured,horizontalOverflow,controlTextOverflow,whitespaceAdvisories,svgLabels,svgLabelFailures,graphLabelOverlaps,graphHiddenLabels,dialogBounds,checkedTextCount:textElements.length};
}

async function audit(page,name) {
  await page.waitForTimeout(120);
  const metrics = await page.evaluate(measure);
  const filename = name+'.png';
  await page.screenshot({path:path.join(output,filename),fullPage:true});
  const dialogOutside=metrics.dialogBounds&&(metrics.dialogBounds.left<0||metrics.dialogBounds.top<0||metrics.dialogBounds.right>metrics.viewport.width+1||metrics.dialogBounds.bottom>metrics.viewport.height+1);
  report.snapshots.push({name,screenshot:filename,...metrics,passed:metrics.scrollWidth<=metrics.viewport.width+1&&metrics.smallText.length===0&&metrics.contrastFailures.length===0&&metrics.horizontalOverflow.length===0&&metrics.controlTextOverflow.length===0&&metrics.svgLabelFailures.length===0&&metrics.graphLabelOverlaps.length===0&&!dialogOutside});
  console.log('SNAPSHOT '+name);
}

function behaviorCheck(name, passed, evidence={}) {
  report.behaviorChecks.push({name,passed,...evidence});
  console.log('BEHAVIOR '+name+' '+(passed?'PASS':'FAIL'));
}

async function taskNavigation(page,prefix) {
  const form=page.locator('.rs-task-form'),detail=page.locator('.rs-task-detail');
  const compact=await page.locator('.rs-task-shortcuts').isVisible();
  if(!compact)return;
  const geometry=await page.evaluate(()=>{
    const form=document.querySelector('.rs-task-form').getBoundingClientRect(),detail=document.querySelector('.rs-task-detail').getBoundingClientRect();
    return {resultTop:detail.top,resultBottom:detail.bottom,formTop:form.top};
  });
  behaviorCheck(prefix+'-result-before-form',geometry.resultTop<geometry.formTop&&geometry.resultBottom<=geometry.formTop+1,geometry);
  await page.getByRole('button',{name:'新建任务',exact:true}).click();
  const formScroll=await form.evaluate(el=>{const r=el.getBoundingClientRect(),root=document.querySelector('#moduleRoot').getBoundingClientRect();return {top:r.top,rootTop:root.top,visible:r.top>=root.top-1&&r.top<innerHeight,focused:el.contains(document.activeElement)};});
  behaviorCheck(prefix+'-new-task-scroll',formScroll.visible&&formScroll.focused,formScroll);
  await form.getByLabel('研究问题',{exact:true}).fill('SYNTHETIC 未提交的研究问题：切换结果、输入和历史时必须保留。');
  await form.locator('.rs-document-choice input').first().check();
  const readInput=()=>form.evaluate(el=>({kind:el.querySelector('select').value,prompt:el.querySelector('textarea').value,selected:[...el.querySelectorAll('.rs-document-choice input:checked')].map(item=>item.value)}));
  const before=await readInput();
  await page.getByRole('button',{name:'返回任务结果',exact:true}).click();
  const resultScroll=await detail.evaluate(el=>{const r=el.getBoundingClientRect(),root=document.querySelector('#moduleRoot').getBoundingClientRect();return {top:r.top,rootTop:root.top,visible:r.top>=root.top-1&&r.top<innerHeight,focused:document.activeElement===el};});
  behaviorCheck(prefix+'-return-result-scroll',resultScroll.visible&&resultScroll.focused,resultScroll);
  await page.getByRole('button',{name:'新建任务',exact:true}).click();
  const after=await readInput();
  behaviorCheck(prefix+'-navigation-keeps-input',JSON.stringify(before)===JSON.stringify(after),{before,after});
  await page.getByRole('button',{name:'返回任务结果',exact:true}).click();
  await audit(page,prefix+'-research-return-result');
}

async function datasetVisibility(page,name,expected) {
  const visible=await page.locator('.rs-chart-form').isVisible(),emptyVisible=await page.locator('.rs-chart-empty').isVisible();
  behaviorCheck(name,visible===expected&&emptyVisible!==expected,{chartFormVisible:visible,emptyHintVisible:emptyVisible,expectedChartFormVisible:expected});
}

function measureShell(){
  const rect=el=>{const b=el.getBoundingClientRect();return {x:b.x,y:b.y,width:b.width,height:b.height};};
  const style=el=>{const s=getComputedStyle(el);return {fontSize:s.fontSize,lineHeight:s.lineHeight,fontFamily:s.fontFamily,fontWeight:s.fontWeight,padding:s.padding,background:s.backgroundColor,color:s.color,borderBottomWidth:s.borderBottomWidth};};
  const header=document.querySelector('.app-header'),nav=document.querySelector('.module-navigation');
  const content=document.querySelector('#workspace:not([hidden]),#moduleRoot:not([hidden])');
  const moduleShell=content.id==='moduleRoot'?content.firstElementChild:null;
  const moduleHeading=moduleShell?.querySelector(':scope > header h2');
  return {viewport:{width:innerWidth,height:innerHeight},header:rect(header),headerStyle:style(header),navigation:rect(nav),navigationStyle:style(nav),navigationScrollLeft:nav.scrollLeft,buttons:[...nav.querySelectorAll('[data-module]')].map(el=>({module:el.dataset.module,...rect(el),x:el.getBoundingClientRect().x+nav.scrollLeft,style:style(el)})),content:rect(content),moduleShell:moduleShell?{...rect(moduleShell),padding:getComputedStyle(moduleShell).padding}:null,moduleHeading:moduleHeading?{...rect(moduleHeading),style:style(moduleHeading)}:null,headerControls:[...header.querySelectorAll('button')].map(el=>({id:el.id,...rect(el),style:style(el),scrollWidth:el.scrollWidth,clientWidth:el.clientWidth})),documentWidth:document.documentElement.scrollWidth,bodyWidth:document.body.scrollWidth,bodyScrollTop:document.scrollingElement.scrollTop,focus:document.body.classList.contains('reader-focus')};
}

async function shellAudit(page,ready,module){
  const modules=['reader','manage','research','discovery','graph','data'];
  const near=(a,b)=>Math.abs(a-b)<=1;
  const matchingRect=(a,b,keys=['x','y','width','height'])=>keys.every(key=>near(a[key],b[key]));
  function moduleHeadingCheck(name,actual,baseline){
    if(!actual.moduleHeading||!baseline?.moduleHeading)return;
    const shellMatches=actual.moduleShell.padding===baseline.moduleShell.padding&&matchingRect(actual.moduleShell,baseline.moduleShell,['x','y','width']);
    const headingMatches=matchingRect(actual.moduleHeading,baseline.moduleHeading,['x','y'])&&['fontSize','fontWeight','fontFamily','lineHeight','color'].every(key=>actual.moduleHeading.style[key]===baseline.moduleHeading.style[key]);
    behaviorCheck(name+'-module-heading-consistent',shellMatches&&headingMatches,{shellMatches,headingMatches,baseline:{shell:baseline.moduleShell,heading:baseline.moduleHeading},actual:{shell:actual.moduleShell,heading:actual.moduleHeading}});
  }
  async function sample(name,baseline){
    await page.waitForTimeout(160);
    const metrics=await page.evaluate(measureShell),filename=name+'.png';
    await page.screenshot({path:path.join(output,filename),fullPage:false});
    const controlsFit=metrics.headerControls.every(button=>button.x>=-1&&button.x+button.width<=metrics.viewport.width+1&&button.y>=metrics.header.y-1&&button.y+button.height<=metrics.header.y+metrics.header.height+1);
    const noOverflow=metrics.documentWidth<=metrics.viewport.width+1&&metrics.bodyWidth<=metrics.viewport.width+1&&metrics.bodyScrollTop===0;
    behaviorCheck(name+'-frame-within-viewport',noOverflow&&controlsFit,{documentWidth:metrics.documentWidth,bodyWidth:metrics.bodyWidth,bodyScrollTop:metrics.bodyScrollTop,viewport:metrics.viewport,headerControls:metrics.headerControls});
    let consistent=true;
    if(baseline){
      const headerMatches=matchingRect(metrics.header,baseline.header)&&JSON.stringify(metrics.headerStyle)===JSON.stringify(baseline.headerStyle);
      const navMatches=matchingRect(metrics.navigation,baseline.navigation)&&JSON.stringify(metrics.navigationStyle)===JSON.stringify(baseline.navigationStyle);
      const contentMatches=matchingRect(metrics.content,baseline.content,['x','y','width']);
      const controlsMatch=metrics.headerControls.every((control,index)=>matchingRect(control,baseline.headerControls[index])&&JSON.stringify(control.style)===JSON.stringify(baseline.headerControls[index].style));
      const buttonsMatch=metrics.buttons.every((button,index)=>matchingRect(button,baseline.buttons[index])&&['fontSize','lineHeight','fontFamily','fontWeight','padding','borderBottomWidth'].every(key=>button.style[key]===baseline.buttons[index].style[key]));
      consistent=headerMatches&&navMatches&&contentMatches&&buttonsMatch&&controlsMatch;
      behaviorCheck(name+'-shared-frame-consistent',consistent,{headerMatches,navMatches,contentMatches,buttonsMatch,controlsMatch,baseline,actual:metrics});
    }
    report.snapshots.push({name,screenshot:filename,shell:metrics,passed:noOverflow&&controlsFit&&consistent});
    console.log('SNAPSHOT '+name);
    return metrics;
  }
  for(const width of [1440,1024,602,390,320]){
    await page.setViewportSize({width,height:width>=1024?900:844});
    for(const content of (width===1440||width===320?[false,true]:[true])){
      filled=content;accountMode=false;await ready();
      if(filled)await page.locator('#pageImage:visible').waitFor();
      const prefix=width+'-'+(filled?'content':'empty');
      let baseline,headingBaseline;
      for(const name of modules){await module(name);const actual=await sample(prefix+'-'+name,baseline);baseline ||= actual;moduleHeadingCheck(prefix+'-'+name,actual,headingBaseline);if(actual.moduleHeading)headingBaseline ||= actual;}
      // Compare against reader again after every module has mounted once.
      await module('reader');const beforeFocus=await page.evaluate(measureShell);
      await page.locator('#readerFocusButton').click();
      await page.waitForFunction(()=>document.body.classList.contains('reader-focus'));
      await page.keyboard.press('Escape');
      await page.waitForFunction(()=>!document.body.classList.contains('reader-focus'));
      await sample(prefix+'-focus-exit',beforeFocus);
      await module('manage');await sample(prefix+'-focus-exit-manage',beforeFocus);
      if(width<1000){
        await page.locator('[data-module="data"]').click();
        const before=await page.locator('.module-navigation').evaluate(el=>el.scrollLeft);
        await page.locator('[data-module="graph"]').click();
        const afterGraph=await page.locator('.module-navigation').evaluate(el=>el.scrollLeft);
        await page.locator('[data-module="data"]').click();
        const afterData=await page.locator('.module-navigation').evaluate(el=>el.scrollLeft);
        behaviorCheck(prefix+'-navigation-preserves-horizontal-position',near(before,afterGraph)&&near(before,afterData),{before,afterGraph,afterData});
      }
    }
  }
  accountMode=true;filled=true;
  for(const width of [602,320]){
    await page.setViewportSize({width,height:844});await ready();
    await page.waitForFunction(()=>document.querySelector('#accountButton').textContent.startsWith('LongAccount_'));
    let baseline,headingBaseline;
    for(const name of modules){await module(name);const actual=await sample(width+'-long-account-'+name,baseline);baseline ||= actual;moduleHeadingCheck(width+'-long-account-'+name,actual,headingBaseline);if(actual.moduleHeading)headingBaseline ||= actual;}
  }
}

async function main(){
  const browser=await chromium.launch({headless:true,executablePath:process.env.BROWSER_EXECUTABLE||'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe'});
  const context=await browser.newContext({viewport:{width:1440,height:1000},reducedMotion:'reduce'});
  const page=await context.newPage();page.setDefaultTimeout(15000);
  page.on('pageerror',error=>report.pageErrors.push(String(error)));
  page.on('console',message=>{if(message.type()==='error')report.consoleErrors.push(message.text());});
  await context.route('**/*',async route=>{
    const request=route.request(),url=new URL(request.url());
    const fixtureWrite=!live&&request.method()==='POST'&&['/api/datasets/fixture-dataset/charts','/api/diagrams'].includes(url.pathname);
    if(url.origin!==origin||(!['GET','HEAD'].includes(request.method())&&!fixtureWrite)){report.blockedRequests.push({method:request.method(),path:url.origin+url.pathname});return route.abort();}
    if(live)return route.continue();
    if(url.pathname==='/'||url.pathname.startsWith('/static/')){
      const name=url.pathname==='/'?'index.html':path.basename(url.pathname);
      if(!assets.has(name))return route.fulfill({status:404,body:''});
      const contentType={'.html':'text/html; charset=utf-8','.css':'text/css; charset=utf-8','.js':'text/javascript; charset=utf-8','.svg':'image/svg+xml'}[path.extname(name)];
      return route.fulfill({status:200,contentType,body:assets.get(name)});
    }
    if(url.pathname.endsWith('/image')||url.pathname.endsWith('/svg')||url.pathname==='/api/diagrams')return route.fulfill({status:200,contentType:'image/svg+xml',body:pageSvg});
    return route.fulfill({status:200,contentType:'application/json; charset=utf-8',body:JSON.stringify(responseFor(url,request.method()))});
  });
  try{
    let moduleEntries=0;
    async function ready(){await page.goto(origin);await page.locator('#connectionState.is-connected').waitFor();await page.waitForFunction(()=>document.querySelector('#documentList')?.getAttribute('aria-busy')==='false');}
    async function module(name){
      await page.locator(`[data-module="${name}"]`).click();if(name!=='reader')await page.locator('#moduleRoot > section').waitFor();await page.waitForTimeout(100);
      if(name!=='reader'){
        const position=await page.locator('#moduleRoot').evaluate(el=>({scrollTop:el.scrollTop,scrollLeft:el.scrollLeft,viewportWidth:innerWidth}));
        behaviorCheck('module-entry-'+(++moduleEntries)+'-'+name,position.scrollTop===0&&position.scrollLeft===0,position);
      }
    }
    if(shellOnly){
      await shellAudit(page,ready,module);
      report.passed=report.pageErrors.length===0&&report.consoleErrors.length===0&&report.blockedRequests.length===0&&report.snapshots.every(snapshot=>snapshot.passed)&&report.behaviorChecks.every(check=>check.passed);
      if(!report.passed)process.exitCode=1;
      return;
    }
    const sizes=live?[{width:1440,height:1000}]:[{width:1440,height:1000},{width:1280,height:600},{width:1024,height:768},{width:602,height:884},{width:390,height:844},{width:320,height:720}];
    for(const size of sizes){
      await page.setViewportSize(size);
      for(const content of (live?[false]:[false,true])){
        filled=content;await ready();const prefix=`${size.width}-${live?'live':content?'content':'empty'}`;
        if(!live&&filled){await page.locator('#fileInput').setInputFiles({name:'SYNTHETIC_InvalidUpload_'+'ABCDEFGHIJ'.repeat(16)+'.txt',mimeType:'text/plain',buffer:Buffer.from('Synthetic filename layout test; rejected locally before any upload.')});await page.locator('#appNotice:visible').waitFor();await audit(page,prefix+'-long-error-notice');await page.locator('#dismissNotice').click();}
        for(const name of ['reader','manage','research','discovery','graph','data']){
          await module(name);await audit(page,prefix+'-'+name);
          if(live)continue;
          if(name==='reader'&&filled&&size.width===602){
            await page.locator('#pageImage:visible').waitFor();
            const preview=await page.evaluate(()=>{const reader=document.querySelector('#readerPanel').getBoundingClientRect(),image=document.querySelector('#pageImage').getBoundingClientRect(),workspace=document.querySelector('#workspace').getBoundingClientRect();return {readerWidth:reader.width,workspaceWidth:workspace.width,pdfTop:image.top,viewportHeight:innerHeight,pdfVisible:image.width>0&&image.height>0,libraryExpanded:document.querySelector('#toggleLibrary').getAttribute('aria-expanded'),evidenceExpanded:document.querySelector('#toggleEvidence').getAttribute('aria-expanded')};});
            behaviorCheck(prefix+'-reading-first-full-width',preview.pdfVisible&&preview.pdfTop<preview.viewportHeight&&Math.abs(preview.readerWidth-preview.workspaceWidth)<3&&preview.libraryExpanded==='false'&&preview.evidenceExpanded==='false',preview);
          }
          if(name==='reader'){
            if(await page.locator('#toggleLibrary').getAttribute('aria-expanded')!=='true')await page.locator('#toggleLibrary').click();
            await audit(page,prefix+'-reader-library');
            await page.locator('#closeLibrary').click();
            await page.locator('#toggleEvidence').click();await audit(page,prefix+'-reader-evidence');
            await page.locator('#closeEvidence').click();
          }
          if(name==='reader'&&filled){await page.locator('#paperPage:visible').waitFor();await page.getByLabel('页面缩放').selectOption('2');await audit(page,prefix+'-reader-zoom200');const edge=await page.locator('#readerViewport').evaluate(el=>{el.scrollLeft=0;const box=el.getBoundingClientRect(),paper=document.querySelector('#paperPage').getBoundingClientRect();return {viewportLeft:box.left,paperLeft:paper.left,pass:paper.left>=box.left-1};});report.snapshots.at(-1).zoomLeftEdge=edge;if(!edge.pass)report.snapshots.at(-1).passed=false;await page.getByLabel('页面缩放').selectOption('1');}
          if(name==='manage'&&filled){for(const label of ['标题与引用','本页文字','版面结构','参考文献筛选']){await page.getByRole('tab',{name:label,exact:true}).click();if(label==='版面结构'){await page.getByRole('button',{name:'分析本页结构',exact:true}).click();await page.getByText('标题、作者与机构候选',{exact:true}).waitFor();}await audit(page,prefix+'-manage-'+{'标题与引用':'structure','本页文字':'text','版面结构':'layout','参考文献筛选':'references'}[label]);}}
          if(name==='research'){
            if(!filled)behaviorCheck(prefix+'-no-task-hides-result',!await page.locator('.rs-task-detail').isVisible());
            else {
              await page.locator('.rs-task-layout.rs-has-task').waitFor();
              await taskNavigation(page,prefix);
              const compact=await page.locator('.rs-task-shortcuts').isVisible();
              if(compact)await page.getByRole('button',{name:'任务历史',exact:true}).click();
              else await page.locator('.rs-history').evaluate(el=>el.scrollIntoView({block:'start'}));
              await audit(page,prefix+'-research-history');
              for(const label of ['文献翻译','论文框架','未来方向']){
                await page.locator('.rs-history-item').filter({hasText:label}).click();
                await page.waitForFunction(expected=>document.querySelector('.rs-task-detail>.rs-card-title')?.textContent===expected,label);
                if(compact){
                  const selection=await page.locator('.rs-task-detail').evaluate(el=>{const box=el.getBoundingClientRect(),root=document.querySelector('#moduleRoot').getBoundingClientRect();return {heading:el.querySelector('.rs-card-title').textContent,selectedHistory:document.querySelector('.rs-history-item.is-selected strong')?.textContent,top:box.top,rootTop:root.top,visible:box.top>=root.top-1&&box.top<innerHeight,focused:document.activeElement===el};});
                  behaviorCheck(prefix+'-history-select-'+label,selection.heading===label&&selection.selectedHistory===label&&selection.visible&&selection.focused,selection);
                }else await page.locator('.rs-task-detail').evaluate(el=>el.scrollIntoView({block:'start'}));
                await audit(page,prefix+'-research-'+{'文献翻译':'translation','论文框架':'draft','未来方向':'future'}[label]);
              }
            }
          }
          if(name==='discovery'&&filled){await page.getByRole('button',{name:'本地推荐',exact:true}).click();await page.locator('.ex-paper-card').waitFor();await audit(page,prefix+'-discovery-recommendations');}
          if(name==='graph'&&filled){
            const identities=await page.locator('.ex-network-node').evaluateAll(nodes=>nodes.map(node=>({id:node.dataset.nodeId,title:node.querySelector('title')?.textContent,aria:node.getAttribute('aria-label')})));
            if(docs.some(doc=>!identities.some(item=>item.id===doc.id&&item.title===doc.title&&item.aria?.includes(doc.title))))throw new Error('Graph nodes must retain complete titles and accessible names.');
            await page.locator('.ex-network-node circle').first().click();
            if(await page.locator('.ex-graph-selection h3').textContent()!==docs[0].title)throw new Error('Graph detail must preserve the selected document full title.');
            await audit(page,prefix+'-graph-selected');
            for(let step=0;step<3;step++)await page.getByRole('button',{name:'缩小图谱',exact:true}).click();
            await audit(page,prefix+'-graph-zoom-out');await page.getByRole('button',{name:'复位',exact:true}).click();
          }
          if(name==='data'){
            await datasetVisibility(page,prefix+'-dataset-controls',filled);
            if(filled){
              if(size.width===602){
                datasetsPresent=false;await page.getByRole('button',{name:'刷新数据集',exact:true}).click();await page.locator('.rs-chart-empty:visible').waitFor();
                await datasetVisibility(page,prefix+'-dataset-removed',false);await audit(page,prefix+'-data-dataset-removed');
                datasetsPresent=true;await page.getByRole('button',{name:'刷新数据集',exact:true}).click();await page.locator('.rs-chart-form:visible').waitFor();
                await datasetVisibility(page,prefix+'-dataset-restored',true);await audit(page,prefix+'-data-dataset-restored');
              }
              await page.getByLabel(/^图表类型/).selectOption('radar3d');await audit(page,prefix+'-data-radar-controls');await page.locator('.rs-y-choices input[value="score_A"]').check();await page.locator('.rs-y-choices input[value="score_B"]').check();await page.getByRole('button',{name:'生成图表与图注',exact:true}).click();await page.locator('.rs-chart-output img').waitFor();await page.locator('.rs-chart-output').evaluate(el=>el.scrollIntoView({block:'start'}));await audit(page,prefix+'-data-chart-output');
            }
          }
        }
        if(!live){await page.locator('#accountButton').click();await page.locator('#accountDialog[open]').waitFor();await audit(page,prefix+'-account-dialog');await page.locator('#closeAccount').click();await page.locator('#modelSettingsButton').click();await page.locator('.ms-dialog[open]').waitFor();await audit(page,prefix+'-model-dialog');await page.getByRole('button',{name:'关闭模型设置并清空密钥输入'}).click();}
      }
      if(!live){filled=true;jobsPresent=false;await ready();await module('research');await audit(page,size.width+'-documents-without-jobs-research');behaviorCheck(size.width+'-documents-without-jobs-hides-result',!await page.locator('.rs-task-detail').isVisible());jobsPresent=true;}
    }
    if(!live){await page.setViewportSize({width:844,height:390});await ready();await page.locator('#accountButton').click();await audit(page,'844x390-account-dialog');await page.locator('#closeAccount').click();await page.locator('#modelSettingsButton').click();await audit(page,'844x390-model-dialog');await page.getByRole('button',{name:'关闭模型设置并清空密钥输入'}).click();accountMode=true;for(const width of [1440,602,560,320]){await page.setViewportSize({width,height:844});await ready();await audit(page,width+'-long-account-header');await page.locator('#accountButton').click();await audit(page,width+'-long-account-dialog');await page.locator('#closeAccount').click();}}
    report.passed=report.pageErrors.length===0&&report.consoleErrors.length===0&&report.blockedRequests.length===0&&report.snapshots.every(snapshot=>snapshot.passed)&&report.behaviorChecks.every(check=>check.passed);
    if(!live&&!report.passed)process.exitCode=1;
  }catch(error){report.passed=false;report.failure=String(error.stack||error);await audit(page,'failure').catch(()=>{});process.exitCode=1;}
  finally{report.finishedAt=new Date().toISOString();report.summary={scenarios:report.snapshots.length,failedScenarios:report.snapshots.filter(item=>!item.passed).map(item=>item.name),behaviorChecks:report.behaviorChecks.length,failedBehaviorChecks:report.behaviorChecks.filter(item=>!item.passed).map(item=>item.name)};for(const key of ['smallText','contrastFailures','horizontalOverflow','controlTextOverflow','contrastUnmeasured','whitespaceAdvisories','svgLabels','svgLabelFailures','graphLabelOverlaps','graphHiddenLabels'])report.summary[key]=report.snapshots.reduce((sum,item)=>sum+(item[key]?.length||0),0);fs.writeFileSync(path.join(output,'report.json'),JSON.stringify(report,null,2),{flag:'wx'});await browser.close();console.log(JSON.stringify({output,passed:report.passed,snapshots:report.snapshots.length,behaviorChecks:report.behaviorChecks.length,failure:report.failure}));}
}
main().catch(error=>{console.error(error);process.exitCode=1;});
