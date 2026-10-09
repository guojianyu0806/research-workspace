/* Offline reader panning/sizing checks using existing Playwright/Edge only.
 * Every request is served from a static memory snapshot and synthetic fixtures.
 * No service, credential, model call, upload, or persistent application data is used.
 */
'use strict';
const fs = require('node:fs');
const path = require('node:path');
const crypto = require('node:crypto');
const {execFileSync} = require('node:child_process');
const {chromium} = require(process.env.PLAYWRIGHT_MODULE || 'C:/Users/g2806/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright');

const root = path.resolve(__dirname, '..');
const origin = 'http://127.0.0.1:8765';
const output = path.join(root, 'artifacts/browser', `reader-sizing-${new Date().toISOString().replace(/[:.]/g, '-')}-${crypto.randomBytes(3).toString('hex')}`);
fs.mkdirSync(output, {recursive:true});
const assets = new Map(fs.readdirSync(path.join(root, 'research_workspace/static'), {withFileTypes:true}).filter(entry => entry.isFile()).map(entry => [entry.name, fs.readFileSync(path.join(root, 'research_workspace/static', entry.name))]));
const report = {startedAt:new Date().toISOString(), output, checks:[], screenshots:[], pageErrors:[], consoleErrors:[], blockedRequests:[], source:{gitHead:execFileSync('git', ['rev-parse','HEAD'], {cwd:root, encoding:'utf8', windowsHide:true}).trim(), sha256:{}}, limitations:[
  'Synthetic paper images verify controls and geometry, not PDF rendering fidelity or research/model quality.',
  'The native fullscreen case records the actual browser outcome; fallback cases explicitly disable or reject that API.',
  'Only the existing local Edge browser is exercised; other browser/device support requires separate verification.'
]};
for (const [name,content] of assets) if (/\.(css|html|js)$/.test(name)) report.source.sha256[name] = crypto.createHash('sha256').update(content).digest('hex');
report.source.sha256['scripts/reader_sizing_smoke.cjs'] = crypto.createHash('sha256').update(fs.readFileSync(__filename)).digest('hex');
const run = {id:'controls-run', status:'completed', total_pages:2, pages_done:2, text_pages:2};
const docs = [0,1].map(index => ({id:`controls-doc-${index}`, title:index ? 'SYNTHETIC 第二篇文献' : 'SYNTHETIC 原文阅读缩放与折叠验证 — A very long paper title for responsive controls and readable headings', filename:`SYNTHETIC-controls-${index}.pdf`, page_count:2, created_at:'2026-10-09T08:00:00Z', current_run:run, available_run:run}));
const evidence = {document_id:docs[0].id, run_id:run.id, page:2, block_id:'controls-block', bbox:[.12,.2,.77,.4], text:'Synthetic evidence keeps its position after reading controls change.', document_title:docs[0].title};
let browser;

function record(name, passed, evidence = {}) {
  report.checks.push({name, passed:Boolean(passed), ...evidence});
  console.log((passed ? 'PASS ' : 'FAIL ') + name);
}
async function screenshot(page, name) {
  const filename = `${name}.png`;
  await page.screenshot({path:path.join(output, filename), fullPage:true});
  report.screenshots.push(filename);
}
async function settle(page) { await page.waitForTimeout(360); }
async function geometry(page) {
  return page.evaluate(() => {
    const rect = selector => {const r = document.querySelector(selector).getBoundingClientRect(); return {x:r.x,y:r.y,width:r.width,height:r.height,right:r.right,bottom:r.bottom};};
    const paper = document.querySelector('#paperPage'), viewport = document.querySelector('#readerViewport'), style = getComputedStyle(viewport);
    return {paper:rect('#paperPage'), viewport:rect('#readerViewport'), panel:rect('#readerPanel'), workspace:rect('#workspace'), natural:{width:document.querySelector('#pageImage').naturalWidth,height:document.querySelector('#pageImage').naturalHeight}, innerWidth:viewport.clientWidth-parseFloat(style.paddingLeft)-parseFloat(style.paddingRight), innerHeight:viewport.clientHeight-parseFloat(style.paddingTop)-parseFloat(style.paddingBottom), scroll:{left:viewport.scrollLeft,top:viewport.scrollTop,width:viewport.scrollWidth,height:viewport.scrollHeight}, page:document.querySelector('#pageNumber').value, zoom:document.querySelector('#pageZoom').value, paperVisible:!paper.hidden, bodyWidth:document.documentElement.scrollWidth, window:{width:innerWidth,height:innerHeight}};
  });
}
async function state(page) {
  return page.evaluate(() => ({
    classes:document.querySelector('#workspace').className,
    library:document.querySelector('#toggleLibrary').getAttribute('aria-expanded'),
    evidence:document.querySelector('#toggleEvidence').getAttribute('aria-expanded'),
    libraryInert:document.querySelector('#libraryPanel').inert,
    evidenceInert:document.querySelector('#evidencePanel').inert,
    focus:document.body.classList.contains('reader-focus'),
    fullscreen:document.querySelector('#readerPanel').classList.contains('reader-fullscreen'),
    nativeFullscreen:document.fullscreenElement?.id || null,
    active:document.activeElement?.id,
    page:document.querySelector('#pageNumber').value,
    zoom:document.querySelector('#pageZoom').value,
    scrollTop:document.querySelector('#readerViewport').scrollTop,
    scrollLeft:document.querySelector('#readerViewport').scrollLeft
  }));
}
function syntheticPage(page) {
  const width = page === 2 ? 1000 : 600, height = page === 2 ? 560 : 800;
  return `<svg xmlns="http://www.w3.org/2000/svg" width="${width}" height="${height}" viewBox="0 0 ${width} ${height}"><rect width="100%" height="100%" fill="white"/><text x="30" y="45" font-size="20" fill="#203440">SYNTHETIC PAGE ${page}</text>${Array.from({length:18},(_,index)=>`<rect x="30" y="${80+index*23}" width="${width-60-index%3*20}" height="5" fill="#536874"/>`).join('')}</svg>`;
}
async function newPage(size, {fullscreen='native', motion='reduce', empty=false} = {}) {
  const context = await browser.newContext({viewport:size, reducedMotion:motion});
  if (fullscreen !== 'native') await context.addInitScript(mode => {
    Object.defineProperty(Element.prototype, 'requestFullscreen', {configurable:true, value:mode === 'unavailable' ? undefined : function(){return Promise.reject(new DOMException('Synthetic browser fullscreen rejection','NotAllowedError'));}});
  }, fullscreen);
  await context.route('**/*', async route => {
    const request = route.request(), url = new URL(request.url());
    if (url.origin !== origin || !['GET','HEAD'].includes(request.method())) {
      report.blockedRequests.push({method:request.method(), url:url.origin+url.pathname}); return route.abort();
    }
    if (url.pathname === '/' || url.pathname.startsWith('/static/')) {
      const name = url.pathname === '/' ? 'index.html' : path.basename(url.pathname);
      if (!assets.has(name)) {report.blockedRequests.push({url:url.pathname, reason:'unknown static asset'}); return route.fulfill({status:404, body:''});}
      const contentType = {'.html':'text/html; charset=utf-8','.js':'text/javascript; charset=utf-8','.css':'text/css; charset=utf-8','.svg':'image/svg+xml'}[path.extname(name)];
      return route.fulfill({status:200, contentType, body:assets.get(name)});
    }
    let result;
    const pageMatch = url.pathname.match(/\/pages\/(\d+)(\/image)?$/);
    if (pageMatch?.[2]) return route.fulfill({status:200, contentType:'image/svg+xml', body:syntheticPage(Number(pageMatch[1]))});
    if (url.pathname === '/api/account') result = {user:{mode:'local'}};
    else if (url.pathname === '/api/capabilities') result = {mode:'local', max_upload_mb:50, ocr:true, llm:false};
    else if (url.pathname === '/api/documents') result = {documents:empty?[]:docs};
    else if (url.pathname === '/api/search') result = {hits:[{...evidence, score:1, filename:docs[0].filename}]};
    else if (pageMatch) result = {text:evidence.text, blocks:[{id:evidence.block_id,text:evidence.text,bbox:evidence.bbox}]};
    else if (/^\/api\/documents\/[^/]+$/.test(url.pathname)) result = {document:docs[0]};
    else if (url.pathname === '/api/datasets') result = {datasets:[]};
    else {report.blockedRequests.push({url:url.pathname, reason:'unknown synthetic endpoint'}); result = {};}
    return route.fulfill({status:200, contentType:'application/json', body:JSON.stringify(result)});
  });
  const page = await context.newPage();
  page.setDefaultTimeout(7000);
  page.on('pageerror', error => report.pageErrors.push(String(error)));
  page.on('console', message => {if(message.type()==='error') report.consoleErrors.push(message.text());});
  await page.goto(origin);
  await page.locator('#connectionState.is-connected').waitFor();
  await page.waitForFunction(() => document.querySelector('#documentList').getAttribute('aria-busy') === 'false');
  if (!empty) await page.locator('#paperPage:visible').waitFor();
  await settle(page);
  return {page, context};
}

async function adjustment(page, visible=true) {
  if (await page.locator('#readerAdjustPopover').isVisible() !== visible) {
    await page.locator('#readerAdjustButton').click(); await settle(page);
  }
}
async function rangeValue(page, selector, value) {
  await adjustment(page);
  const input=page.locator(selector);
  await input.focus();
  if(!await input.evaluate(el=>document.activeElement===el)){
    const detail=await input.evaluate(el=>{const viewport=document.querySelector('#readerViewport'),paper=document.querySelector('#paperPage');return {disabled:el.disabled,active:document.activeElement?.id,scrollWidth:viewport.scrollWidth,clientWidth:viewport.clientWidth,paperWidth:paper.style.width,paperHidden:paper.hidden};});
    throw new Error(`${selector} could not receive keyboard focus: ${JSON.stringify(detail)}`);
  }
  const current=Number(await input.inputValue());
  const step=Number(await input.getAttribute('step') || 1);
  const count=Math.round(Math.abs(value-current)/step), key=value>=current?'ArrowRight':'ArrowLeft';
  if(process.argv.includes('--trace-range'))await page.evaluate(selector=>{
    const range=document.querySelector(selector),viewport=document.querySelector('#readerViewport');
    const trace=type=>({type,value:range.value,left:viewport.scrollLeft,max:viewport.scrollWidth-viewport.clientWidth,behavior:getComputedStyle(viewport).scrollBehavior,time:performance.now()});
    window.readerRangeTrace=[trace('start')];
    const input=()=>window.readerRangeTrace.push(trace('input')),scroll=()=>window.readerRangeTrace.push(trace('scroll'));
    range.addEventListener('input',input);viewport.addEventListener('scroll',scroll);
    window.stopReaderRangeTrace=()=>{range.removeEventListener('input',input);viewport.removeEventListener('scroll',scroll);return window.readerRangeTrace;};
  },selector);
  for(let index=0;index<count;index++)await page.keyboard.press(key);
  await settle(page);
  const trace=process.argv.includes('--trace-range')?await page.evaluate(()=>window.stopReaderRangeTrace()):undefined;
  if(Number(await input.inputValue())!==value){if(trace)report.rangeFailureTrace={selector,value,trace};throw new Error(`${selector} did not accept keyboard value ${value}; ended at ${await input.inputValue()}`);}
}
async function drag(page, selector, dx, dy, at={x:.5,y:.5}) {
  const box=await page.locator(selector).boundingBox();
  if(!box)throw new Error(`No visible drag target ${selector}`);
  const x=box.x+box.width*at.x,y=box.y+box.height*at.y;
  await page.mouse.move(x,y);await page.mouse.down();
  await page.mouse.move(x+dx,y+dy,{steps:12});await page.mouse.up();await settle(page);
}
async function position(page) {
  return page.evaluate(()=>{
    const viewport=document.querySelector('#readerViewport'), paper=document.querySelector('#paperPage');
    const vr=viewport.getBoundingClientRect(),pr=paper.getBoundingClientRect(),style=getComputedStyle(viewport);
    const paddingLeft=parseFloat(style.paddingLeft),paddingRight=parseFloat(style.paddingRight);
    return {left:viewport.scrollLeft,top:viewport.scrollTop,maxLeft:viewport.scrollWidth-viewport.clientWidth,maxTop:viewport.scrollHeight-viewport.clientHeight,paperX:pr.x,paperWidth:pr.width,viewportX:vr.x,viewportWidth:viewport.clientWidth,innerLeft:vr.x+paddingLeft,innerRight:vr.x+viewport.clientWidth-paddingRight,paperRight:pr.right,positionValue:Number(document.querySelector('#readerPositionRange').value),cursor:getComputedStyle(paper).cursor,dragging:viewport.className};
  });
}
async function widths(page) {
  return page.evaluate(()=>{
    const result={};
    for(const id of ['libraryPanel','evidencePanel','readerPanel','workspace']){
      const el=document.getElementById(id),r=el.getBoundingClientRect();result[id]={x:r.x,width:r.width,right:r.right};
    }
    for(const id of ['libraryResizeHandle','evidenceResizeHandle']){
      const el=document.getElementById(id);result[id]={min:Number(el.getAttribute('aria-valuemin')),max:Number(el.getAttribute('aria-valuemax')),now:Number(el.getAttribute('aria-valuenow')),hidden:el.hidden,tabIndex:el.tabIndex};
    }
    result.overflow=document.documentElement.scrollWidth>innerWidth+1;return result;
  });
}
async function headingBounds(page) {
  return page.evaluate(()=>{
    const reader=document.querySelector('#readerPanel').getBoundingClientRect();
    const controls=[...document.querySelectorAll('#readerHeading button')].filter(el=>el.getClientRects().length&&getComputedStyle(el).visibility==='visible').map(el=>{const r=el.getBoundingClientRect();return {id:el.id,left:r.left,right:r.right,top:r.top,bottom:r.bottom};});
    return {reader:{left:reader.left,right:reader.right},controls,escaped:controls.filter(r=>r.left<reader.left-1||r.right>reader.right+1)};
  });
}
async function panningChecks(size) {
  const {page,context}=await newPage(size),prefix=`${size.width}x${size.height}`;
  try {
    await rangeValue(page,'#readerWidthRange',137);
    let box=await geometry(page);
    record(prefix+'-continuous137-percent',Math.abs(box.paper.width/box.innerWidth-1.37)<.015&&box.zoom==='1.37',box);
    const popover=await page.locator('#readerAdjustPopover').boundingBox();
    record(prefix+'-adjustment-popover-in-window',popover.x>=-1&&popover.x+popover.width<=size.width+1&&popover.y>=0&&popover.y+popover.height<=size.height+1,popover);
    await rangeValue(page,'#readerWidthRange',75);
    await rangeValue(page,'#readerPositionRange',0);await adjustment(page,false);
    const left=await position(page);
    await rangeValue(page,'#readerPositionRange',100);await adjustment(page,false);
    const right=await position(page);
    record(prefix+'-small-paper-free-horizontal-position',Math.abs(left.paperX-left.innerLeft)<3&&Math.abs(right.paperRight-right.innerRight)<3&&right.paperX>left.paperX+15,{left,right});
    await rangeValue(page,'#readerPositionRange',50);await adjustment(page,false);
    const centered=await position(page);
    const travel=Math.min(35,(centered.innerRight-centered.innerLeft-centered.paperWidth)/3);
    await drag(page,'#readerViewport',travel,0,{x:.5,y:.35});
    const smallDragged=await position(page);
    record(prefix+'-small-paper-mouse-drag-updates-position',Math.abs(smallDragged.paperX-centered.paperX-travel)<4&&smallDragged.positionValue>centered.positionValue,{centered,smallDragged,travel});

    await rangeValue(page,'#readerWidthRange',200);await adjustment(page,false);
    const enlargedPosition=await position(page);
    record(prefix+'-large-paper-clears-small-paper-offset',Math.abs(enlargedPosition.paperX+enlargedPosition.left-enlargedPosition.innerLeft)<3,enlargedPosition);
    await page.locator('#readerViewport').evaluate(el=>{el.scrollLeft=140;el.scrollTop=150;});await settle(page);
    const before=await position(page);
    await drag(page,'#readerViewport',-65,-70,{x:.5,y:.4});
    const moved=await position(page);
    record(prefix+'-large-paper-pan-left-up',Math.abs(moved.left-before.left-65)<4&&Math.abs(moved.top-before.top-70)<4,{before,moved});
    await drag(page,'#readerViewport',45,40,{x:.5,y:.4});
    const reversed=await position(page);
    record(prefix+'-large-paper-pan-right-down',Math.abs(reversed.left-moved.left+45)<4&&Math.abs(reversed.top-moved.top+40)<4,{moved,reversed});
    await page.mouse.move(5,5);await settle(page);
    const released=await position(page);
    record(prefix+'-mouse-release-stops-panning',!await page.locator('#readerViewport').evaluate(el=>el.classList.contains('is-panning'))&&released.left===reversed.left&&released.top===reversed.top,{reversed,released});

    await page.locator('#readerViewport').evaluate(el=>{el.scrollLeft=1;el.scrollTop=1;});await settle(page);
    await drag(page,'#readerViewport',80,80,{x:.5,y:.4});
    const firstEdge=await position(page);
    record(prefix+'-pan-clamps-first-edges',firstEdge.left===0&&firstEdge.top===0,firstEdge);
    await page.locator('#readerViewport').evaluate(el=>{el.scrollLeft=el.scrollWidth-el.clientWidth-1;el.scrollTop=el.scrollHeight-el.clientHeight-1;});await settle(page);
    await drag(page,'#readerViewport',-80,-80,{x:.5,y:.4});
    const finalEdge=await position(page);
    record(prefix+'-pan-clamps-last-edges',Math.abs(finalEdge.left-finalEdge.maxLeft)<2&&Math.abs(finalEdge.top-finalEdge.maxTop)<2,finalEdge);
    await rangeValue(page,'#readerPositionRange',0);const start=await position(page);
    await rangeValue(page,'#readerPositionRange',100);const end=await position(page);
    record(prefix+'-large-paper-slider-covers-scroll-range',start.left===0&&Math.abs(end.left-end.maxLeft)<2,{start,end});
    await adjustment(page,false);

    await rangeValue(page,'#readerWidthRange',137);await adjustment(page,false);
    await page.locator('#readerFocusButton').click();await settle(page);
    const focused=await geometry(page);
    record(prefix+'-continuous-zoom-survives-focus',focused.zoom==='1.37'&&Math.abs(focused.paper.width/focused.innerWidth-1.37)<.015,focused);
    await page.keyboard.press('Escape');await settle(page);
    const restored=await geometry(page);
    record(prefix+'-continuous-zoom-restored-after-focus',restored.zoom==='1.37'&&Math.abs(restored.paper.width/restored.innerWidth-1.37)<.015,restored);
    await screenshot(page,prefix+'-continuous-zoom');

    await adjustment(page);await page.locator('#readerResetView').click();await settle(page);
    const reset=await geometry(page),resetPosition=await position(page);
    record(prefix+'-reset-fits-width-and-centers',Math.abs(reset.paper.width-reset.innerWidth)<3&&reset.zoom==='1'&&resetPosition.left===0,{reset,resetPosition});
    await adjustment(page);await page.keyboard.press('Escape');await settle(page);
    record(prefix+'-escape-closes-adjustment-and-restores-trigger',!await page.locator('#readerAdjustPopover').isVisible()&&await page.locator('#readerAdjustButton').evaluate(el=>el===document.activeElement),await state(page));
    record(prefix+'-controls-stay-within-window',!(await widths(page)).overflow,await geometry(page));
  }catch(error){record(prefix+'-panning-error',false,{error:String(error.stack||error)});await screenshot(page,prefix+'-panning-failure').catch(()=>{});}
  finally{await context.close();}
}

async function sidebarChecks(size) {
  const {page,context}=await newPage(size),prefix=`${size.width}x${size.height}`,compact=size.width<1000;
  try {
    if(compact){await page.locator('#toggleLibrary').click();await settle(page);}
    let before=await widths(page);
    await drag(page,'#libraryResizeHandle',compact?-45:60,0);
    let after=await widths(page);
    record(prefix+'-library-drag-resizes',compact?after.libraryPanel.width<before.libraryPanel.width-20:after.libraryPanel.width>before.libraryPanel.width+20,{before,after});
    await page.locator('#libraryResizeHandle').focus();await page.keyboard.press('ArrowRight');await settle(page);
    const keyed=await widths(page);
    record(prefix+'-library-keyboard-resizes',keyed.libraryPanel.width>after.libraryPanel.width&&keyed.libraryPanel.width<=keyed.libraryResizeHandle.max+1,{after,keyed});
    const saved=keyed.libraryPanel.width;
    await page.locator('#closeLibrary').click();await settle(page);
    await page.locator('#toggleLibrary').click();await settle(page);
    const reopened=await widths(page);
    record(prefix+'-library-width-kept-after-collapse',Math.abs(reopened.libraryPanel.width-saved)<2,{saved,reopened});
    await page.locator('#libraryResizeHandle').focus();await page.keyboard.press('Home');await settle(page);
    const minimum=await widths(page);
    record(prefix+'-library-minimum-enforced',Math.abs(minimum.libraryPanel.width-minimum.libraryResizeHandle.min)<2&&minimum.libraryPanel.width>=Math.min(240,size.width-36),minimum);
    await page.keyboard.press('End');await settle(page);
    const maximum=await widths(page);
    record(prefix+'-library-maximum-preserves-reading',Math.abs(maximum.libraryPanel.width-maximum.libraryResizeHandle.max)<2&&!maximum.overflow&&(compact?maximum.libraryPanel.width<=maximum.workspace.width-35:maximum.readerPanel.width>=419),maximum);
    if(!compact){const heading=await headingBounds(page);record(prefix+'-420-canvas-heading-controls-fit',!heading.escaped.length,heading);}
    await page.locator('#libraryResizeHandle').dblclick();await settle(page);
    const reset=await widths(page);
    record(prefix+'-library-double-click-restores-default',Math.abs(reset.libraryPanel.width-Math.min(compact?340:240,reset.libraryResizeHandle.max))<2,reset);

    if(compact){await page.locator('#closeLibrary').click();await settle(page);}
    await page.locator('#toggleEvidence').click();await settle(page);
    before=await widths(page);
    await drag(page,'#evidenceResizeHandle',compact?45:-60,0);
    after=await widths(page);
    record(prefix+'-evidence-drag-resizes',compact?after.evidencePanel.width<before.evidencePanel.width-20:after.evidencePanel.width>before.evidencePanel.width+20,{before,after});
    const evidenceKeyDirection=after.evidencePanel.width>=after.evidenceResizeHandle.max-1?'ArrowRight':'ArrowLeft';
    await page.locator('#evidenceResizeHandle').focus();await page.keyboard.press(evidenceKeyDirection);await settle(page);
    const evidenceKey=await widths(page);
    record(prefix+'-evidence-keyboard-follows-divider-direction',(evidenceKeyDirection==='ArrowRight'?evidenceKey.evidencePanel.width<after.evidencePanel.width:evidenceKey.evidencePanel.width>after.evidencePanel.width)&&evidenceKey.evidencePanel.width<=evidenceKey.evidenceResizeHandle.max+1,{after,evidenceKey,evidenceKeyDirection});
    const evidenceSaved=evidenceKey.evidencePanel.width;
    await page.locator('#closeEvidence').click();await settle(page);
    await page.locator('#toggleEvidence').click();await settle(page);
    const evidenceReopened=await widths(page);
    record(prefix+'-evidence-width-kept-after-collapse',Math.abs(evidenceReopened.evidencePanel.width-evidenceSaved)<2,{evidenceSaved,evidenceReopened});
    await page.locator('#evidenceResizeHandle').focus();await page.keyboard.press('End');await settle(page);
    const evidenceMax=await widths(page);
    record(prefix+'-evidence-maximum-preserves-reading',Math.abs(evidenceMax.evidencePanel.width-evidenceMax.evidenceResizeHandle.max)<2&&!evidenceMax.overflow&&(compact?evidenceMax.evidencePanel.width<=evidenceMax.workspace.width-35:evidenceMax.readerPanel.width>=419),evidenceMax);
    await page.locator('#evidenceResizeHandle').dblclick();await settle(page);
    const evidenceReset=await widths(page);
    record(prefix+'-evidence-double-click-restores-default',Math.abs(evidenceReset.evidencePanel.width-Math.min(compact?340:300,evidenceReset.evidenceResizeHandle.max))<2,evidenceReset);
    await screenshot(page,prefix+'-resized-sidebars');
  }catch(error){record(prefix+'-sidebar-error',false,{error:String(error.stack||error)});await screenshot(page,prefix+'-sidebar-failure').catch(()=>{});}
  finally{await context.close();}
}

async function fullscreenPanChecks(mode) {
  const {page,context}=await newPage({width:1280,height:800},{fullscreen:mode});
  try {
    await rangeValue(page,'#readerWidthRange',200);await adjustment(page,false);
    await page.locator('#readerFullscreenButton').click();await settle(page);
    await page.locator('#readerViewport').evaluate(el=>{el.scrollLeft=100;el.scrollTop=100;});await settle(page);
    const before=await position(page);
    await drag(page,'#readerViewport',-90,-75,{x:.5,y:.4});
    const after=await position(page),full=await state(page);
    record(mode+'-fullscreen-pan-both-axes',full.fullscreen&&Math.abs(after.left-before.left-90)<4&&Math.abs(after.top-before.top-75)<4,{before,after,full});
    await rangeValue(page,'#readerWidthRange',137);await adjustment(page,false);
    const custom=await geometry(page);
    record(mode+'-fullscreen-continuous-width',custom.zoom==='1.37'&&Math.abs(custom.paper.width/custom.innerWidth-1.37)<.015,custom);
    await screenshot(page,mode+'-fullscreen-pan');
    await page.keyboard.press('Escape');await settle(page);
    const exited=await state(page);
    record(mode+'-fullscreen-exit-restores-view',!exited.fullscreen&&!exited.nativeFullscreen&&exited.zoom==='2',exited);
  }catch(error){record(mode+'-fullscreen-pan-error',false,{error:String(error.stack||error)});await screenshot(page,mode+'-fullscreen-failure').catch(()=>{});}
  finally{await context.close();}
}

async function layoutPersistenceChecks() {
  const {page,context}=await newPage({width:1440,height:1000});
  try {
    await drag(page,'#libraryResizeHandle',120,0);
    await page.locator('#toggleEvidence').click();await settle(page);
    await drag(page,'#evidenceResizeHandle',-40,0);
    const preferred=await widths(page);
    await page.setViewportSize({width:1000,height:700});await settle(page);
    const constrained=await widths(page);
    record('desktop-resize-clamps-panels-keeps-readable-canvas',constrained.readerPanel.width>=419&&!constrained.overflow,constrained);
    await page.setViewportSize({width:602,height:884});await settle(page);
    await page.setViewportSize({width:1440,height:1000});await settle(page);
    const restored=await widths(page);
    record('window-resize-preserves-preferred-panel-widths',Math.abs(restored.libraryPanel.width-preferred.libraryPanel.width)<2&&Math.abs(restored.evidencePanel.width-preferred.evidencePanel.width)<2,{preferred,restored});
    await page.reload();await page.locator('#paperPage:visible').waitFor();await settle(page);
    await page.locator('#toggleEvidence').click();await settle(page);
    const reloaded=await widths(page);
    record('reload-restores-panel-width-preferences',Math.abs(reloaded.libraryPanel.width-preferred.libraryPanel.width)<2&&Math.abs(reloaded.evidencePanel.width-preferred.evidencePanel.width)<2,{preferred,reloaded});
  }catch(error){record('layout-preference-error',false,{error:String(error.stack||error)});await screenshot(page,'layout-preference-failure').catch(()=>{});}
  finally{await context.close();}
}

async function fractionChecks(size) {
  const {page,context}=await newPage(size),prefix=`${size.width}x${size.height}`;
  try {
    await rangeValue(page,'#readerWidthRange',101);
    const box=await geometry(page);
    record(prefix+'-small-overflow-width101',Math.abs(box.paper.width/box.innerWidth-1.01)<.005,box);
    await rangeValue(page,'#readerPositionRange',50);
    await rangeValue(page,'#readerPositionRange',0);const left=await position(page);
    await rangeValue(page,'#readerPositionRange',100);const right=await position(page);
    record(prefix+'-small-overflow-keyboard-reaches-both-edges',left.positionValue===0&&left.left===0&&right.positionValue===100&&Math.abs(right.left-right.maxLeft)<1,{left,right});
  }catch(error){record(prefix+'-small-overflow-error',false,{error:String(error.stack||error)});await screenshot(page,prefix+'-small-overflow-failure').catch(()=>{});}
  finally{await context.close();}
}

async function main(){
  browser=await chromium.launch({headless:true,executablePath:process.env.BROWSER_EXECUTABLE||'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe'});
  try {
    const only=process.argv.find(arg=>arg.startsWith('--only='))?.slice(7);
    const selected=process.argv.find(arg=>arg.startsWith('--viewport='))?.slice(11);
    const selectedSizes=sizes=>sizes.filter(size=>!selected||`${size.width}x${size.height}`===selected);
    if(!only||only==='pan')for(const size of selectedSizes([{width:1440,height:1000},{width:602,height:884},{width:390,height:844},{width:320,height:720}]))await panningChecks(size);
    if(!only||only==='sidebars')for(const size of selectedSizes([{width:1440,height:1000},{width:1000,height:700},{width:602,height:884},{width:390,height:844},{width:320,height:720}]))await sidebarChecks(size);
    if(!only||only==='sidebars')await layoutPersistenceChecks();
    if(!only||only==='fullscreen')for(const mode of ['native','rejected'])await fullscreenPanChecks(mode);
    if(!only||only==='fraction')for(const size of [{width:1440,height:1000},{width:320,height:720}])await fractionChecks(size);
  }finally{
    report.passed=report.checks.length>0&&report.checks.every(item=>item.passed)&&!report.pageErrors.length&&!report.consoleErrors.length&&!report.blockedRequests.length;
    report.finishedAt=new Date().toISOString();report.summary={checks:report.checks.length,failed:report.checks.filter(item=>!item.passed).map(item=>item.name),pageErrors:report.pageErrors.length,consoleErrors:report.consoleErrors.length,blockedRequests:report.blockedRequests.length};
    fs.writeFileSync(path.join(output,'report.json'),JSON.stringify(report,null,2),{flag:'wx'});
    await browser.close();console.log(JSON.stringify({output,passed:report.passed,summary:report.summary}));if(!report.passed)process.exitCode=1;
  }
}
main().catch(error=>{console.error(error);process.exitCode=1;});
