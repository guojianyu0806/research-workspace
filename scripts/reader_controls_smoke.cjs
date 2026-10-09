/* Offline reader interaction checks using existing Playwright/Edge only.
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
const output = path.join(root, 'artifacts/browser', `reader-controls-${new Date().toISOString().replace(/[:.]/g, '-')}-${crypto.randomBytes(3).toString('hex')}`);
fs.mkdirSync(output, {recursive:true});
const assets = new Map(fs.readdirSync(path.join(root, 'research_workspace/static')).map(name => [name, fs.readFileSync(path.join(root, 'research_workspace/static', name))]));
const report = {startedAt:new Date().toISOString(), output, checks:[], screenshots:[], pageErrors:[], consoleErrors:[], blockedRequests:[], source:{gitHead:execFileSync('git', ['rev-parse','HEAD'], {cwd:root, encoding:'utf8', windowsHide:true}).trim(), sha256:{}}, limitations:[
  'Synthetic paper images verify controls and geometry, not PDF rendering fidelity or research/model quality.',
  'The native fullscreen case records the actual browser outcome; fallback cases explicitly disable or reject that API.',
  'Only the existing local Edge browser is exercised; other browser/device support requires separate verification.'
]};
for (const [name,content] of assets) if (/\.(css|html|js)$/.test(name)) report.source.sha256[name] = crypto.createHash('sha256').update(content).digest('hex');
report.source.sha256['scripts/reader_controls_smoke.cjs'] = crypto.createHash('sha256').update(fs.readFileSync(__filename)).digest('hex');
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
async function focusLoop(page, selector) {
  const count=await page.locator(selector).evaluate(scope=>[...scope.querySelectorAll('button,a[href],input,select,textarea,[tabindex]')].filter(el=>!el.disabled&&el.tabIndex>=0&&!el.closest('[inert]')&&el.getClientRects().length&&getComputedStyle(el).visibility==='visible').length);
  const escapes=[];
  for(const key of ['Tab','Shift+Tab'])for(let index=0;index<count+2;index++){
    await page.keyboard.press(key);
    const position=await page.evaluate(scopeSelector=>({inside:document.querySelector(scopeSelector).contains(document.activeElement),active:document.activeElement?.id||document.activeElement?.tagName}),selector);
    if(!position.inside)escapes.push({key,index,...position});
  }
  return {focusableCount:count,escapes};
}
function sameView(before, after) { return before.page === after.page && before.zoom === after.zoom; }
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

async function viewportChecks(size) {
  const {page, context} = await newPage(size);
  const prefix = `${size.width}x${size.height}`;
  try {
    let current = await state(page), initial = await geometry(page);
    record(prefix+'-reading-first-default', current.library === (size.width>=1000?'true':'false') && current.evidence==='false' && current.evidenceInert && initial.paperVisible && initial.paper.y<initial.window.height && initial.bodyWidth<=initial.window.width+1, {current, initial});
    record(prefix+'-fit-width-fills-canvas', Math.abs(initial.paper.width-initial.innerWidth)<3, initial);

    await page.locator('#zoomInButton').click(); await settle(page);
    const enlarged = await geometry(page);
    record(prefix+'-zoom-in', enlarged.paper.width>initial.paper.width*1.05 && enlarged.zoom!=='1', enlarged);
    await page.locator('#zoomOutButton').click(); await settle(page);
    const reduced = await geometry(page);
    record(prefix+'-zoom-out', reduced.paper.width<enlarged.paper.width && Math.abs(reduced.paper.width-initial.paper.width)<3, reduced);
    await page.locator('#pageZoom').selectOption('2'); await settle(page);
    const double = await geometry(page);
    record(prefix+'-zoom200-ratio', Math.abs(double.paper.width / double.innerWidth - 2)<.02, double);
    const left = await page.locator('#readerViewport').evaluate(el => {el.scrollLeft=0;return {viewportLeft:el.getBoundingClientRect().left,paperLeft:document.querySelector('#paperPage').getBoundingClientRect().left};});
    record(prefix+'-zoom200-left-edge', left.paperLeft>=left.viewportLeft-1, left);

    await page.locator('#pageZoom').selectOption('page'); await settle(page);
    const fit = await geometry(page);
    record(prefix+'-fit-page-both-axes', fit.paper.width<=fit.innerWidth+2 && fit.paper.height<=fit.innerHeight+2 && fit.paper.height>20, fit);
    await page.locator('#nextPage').click(); await page.waitForFunction(() => document.querySelector('#pageImage').src.includes('/pages/2/image')); await settle(page);
    const landscape = await geometry(page);
    record(prefix+'-fit-page-landscape-recalculated', landscape.page==='2' && landscape.natural.width>landscape.natural.height && landscape.paper.width<=landscape.innerWidth+2 && landscape.paper.height<=landscape.innerHeight+2 && landscape.paper.width/landscape.paper.height>1.7, landscape);
    await page.locator('#pageZoom').selectOption('1'); await settle(page);

    const beforeFold = await geometry(page);
    if ((await state(page)).library==='true') await page.locator('#toggleLibrary').click();
    else {await page.locator('#toggleLibrary').click();await settle(page);}
    await settle(page);
    let side = await state(page), folded = await geometry(page);
    if (size.width>=1000) {
      record(prefix+'-library-collapse-expands-paper', side.library==='false' && side.libraryInert && folded.paper.width>beforeFold.paper.width+120 && folded.page===beforeFold.page, {side,beforeFold,folded});
      await page.locator('#toggleLibrary').click(); await settle(page);
    } else {
      const drawer = await page.locator('#libraryPanel').boundingBox();
      record(prefix+'-library-drawer-overlays-paper', side.library==='true' && !side.libraryInert && Math.abs(folded.panel.width-beforeFold.panel.width)<3 && drawer.x>=-1 && drawer.x+drawer.width<=size.width+1, {side,drawer,beforeFold,folded});
      await page.locator('#closeLibrary').click(); await settle(page);
      side = await state(page);
      record(prefix+'-library-close-restores-controls', side.library==='false' && side.libraryInert && side.active==='toggleLibrary', side);
    }
    await page.locator('#toggleEvidence').click(); await settle(page);
    side = await state(page);
    record(prefix+'-evidence-open', side.evidence==='true' && !side.evidenceInert && await page.locator('#searchQuery').isVisible(), side);
    await page.locator('#searchQuery').fill('Synthetic');
    await page.locator('#searchButton').click();
    await page.locator('.hit-card').first().click();
    await page.locator('#evidenceHighlight:visible').waitFor(); await settle(page);
    const evidenceState = await page.evaluate(() => {const highlight=document.querySelector('#evidenceHighlight').getBoundingClientRect(),paper=document.querySelector('#paperPage').getBoundingClientRect();return {page:document.querySelector('#pageNumber').value,left:(highlight.x-paper.x)/paper.width,top:(highlight.y-paper.y)/paper.height,width:highlight.width/paper.width,height:highlight.height/paper.height};});
    record(prefix+'-evidence-keeps-relative-region', evidenceState.page==='2' && Math.abs(evidenceState.left-.12)<.01 && Math.abs(evidenceState.top-.2)<.01 && Math.abs(evidenceState.width-.65)<.01, evidenceState);
    if ((await state(page)).evidence==='true') {await page.locator('#closeEvidence').click();await settle(page);}

    await page.locator('#readerInfoToggle').click(); await settle(page);
    const expandedInfo = await page.locator('#readerInfo').evaluate(el => ({height:el.getBoundingClientRect().height,inert:el.inert,hidden:el.hidden}));
    record(prefix+'-information-expand', expandedInfo.height>0 && !expandedInfo.inert && !expandedInfo.hidden, expandedInfo);
    await page.locator('#readerInfoToggle').click(); await settle(page);
    const collapsedInfo = await page.locator('#readerInfo').evaluate(el => ({height:el.getBoundingClientRect().height,inert:el.inert,hidden:el.hidden}));
    record(prefix+'-information-collapse', collapsedInfo.height<2 || collapsedInfo.inert || collapsedInfo.hidden, collapsedInfo);

    await page.locator('#pageZoom').selectOption('2'); await settle(page);
    await page.locator('#readerViewport').evaluate(el => {el.scrollTop=90;el.scrollLeft=60;});
    const beforeFocus = await state(page), beforeFocusGeometry = await geometry(page);
    await page.locator('#readerFocusButton').click(); await settle(page);
    const focused = await state(page), focusedGeometry = await geometry(page);
    record(prefix+'-focus-maximizes-reading', focused.focus && sameView(beforeFocus,focused) && focusedGeometry.panel.width>=beforeFocusGeometry.panel.width-1 && focusedGeometry.viewport.height>beforeFocusGeometry.viewport.height+30, {before:beforeFocus,focused,beforeGeometry:beforeFocusGeometry,focusedGeometry});
    await screenshot(page,prefix+'-focus');
    await page.keyboard.press('Escape'); await settle(page);
    const afterFocus = await state(page);
    record(prefix+'-focus-escape-restores', !afterFocus.focus && sameView(beforeFocus,afterFocus) && afterFocus.library===beforeFocus.library && afterFocus.evidence===beforeFocus.evidence && Math.abs(afterFocus.scrollTop-beforeFocus.scrollTop)<3 && Math.abs(afterFocus.scrollLeft-beforeFocus.scrollLeft)<3 && afterFocus.active==='readerFocusButton', {beforeFocus,afterFocus});
    await page.locator('#pageZoom').selectOption('1'); await settle(page);
    const resizedWidth = size.width>=1000 ? size.width-151 : size.width+61;
    await page.setViewportSize({width:resizedWidth,height:size.height-73}); await settle(page);
    const resized = await geometry(page);
    record(prefix+'-resize-recomputes-fit', Math.abs(resized.paper.width-resized.innerWidth)<3 && resized.bodyWidth<=resized.window.width+1 && resized.page==='2', resized);
    await screenshot(page,prefix+'-resized-fit');
  } catch (error) {
    record(prefix+'-unexpected-error',false,{error:String(error.stack||error)});
    await screenshot(page,prefix+'-failure').catch(()=>{});
  } finally {await context.close();}
}

async function fullscreenChecks(mode) {
  const {page,context}=await newPage({width:1280,height:800},{fullscreen:mode});
  try {
    await page.locator('#nextPage').click();await page.waitForFunction(()=>document.querySelector('#pageImage').src.includes('/pages/2/image'));
    await page.locator('#pageZoom').selectOption('2');await settle(page);
    await page.locator('#readerViewport').evaluate(el=>{el.scrollTop=80;el.scrollLeft=50;});
    const before=await state(page);
    await page.locator('#readerFullscreenButton').click();await settle(page);
    const full=await state(page), box=await geometry(page);
    record(mode+'-fullscreen-fills-window',full.fullscreen && sameView(before,full) && box.panel.x<=1 && box.panel.y<=1 && box.panel.width>=box.window.width-2 && box.panel.height>=box.window.height-2,{before,full,box});
    if(mode!=='native')record(mode+'-fullscreen-fallback',full.fullscreen && !full.nativeFullscreen,full);
    else record(mode+'-fullscreen-browser-outcome',Boolean(full.nativeFullscreen)||full.fullscreen,{nativeFullscreenElement:full.nativeFullscreen,fallbackUsed:!full.nativeFullscreen});
    if(mode!=='native'){
      record(mode+'-fullscreen-initial-focus',await page.locator('#readerPanel').evaluate(el=>el.contains(document.activeElement)),{active:full.active});
      const keys=await focusLoop(page,'#readerPanel');
      record(mode+'-fullscreen-keyboard-contained',keys.focusableCount>0&&!keys.escapes.length,keys);
    }
    await screenshot(page,mode+'-fullscreen');
    await page.locator('#readerFullscreenButton').click();await settle(page);
    const restored=await state(page);
    record(mode+'-fullscreen-button-restores',!restored.fullscreen && !restored.nativeFullscreen && sameView(before,restored) && before.library===restored.library && before.evidence===restored.evidence && Math.abs(restored.scrollTop-before.scrollTop)<3 && Math.abs(restored.scrollLeft-before.scrollLeft)<3 && restored.active==='readerFullscreenButton',{before,restored});
    await page.locator('#readerFullscreenButton').click();await settle(page);
    await page.locator('#previousPage').click();await page.waitForFunction(()=>document.querySelector('#pageImage').src.includes('/pages/1/image'));await settle(page);
    await page.keyboard.press('Escape');await settle(page);
    const escaped=await state(page);
    record(mode+'-fullscreen-escape-keeps-new-page',!escaped.fullscreen && !escaped.nativeFullscreen && escaped.page==='1' && escaped.zoom==='2' && escaped.active==='readerFullscreenButton',escaped);
  }catch(error){record(mode+'-fullscreen-unexpected-error',false,{error:String(error.stack||error)});await screenshot(page,mode+'-fullscreen-failure').catch(()=>{});}
  finally {await context.close();}
}

async function animationChecks(motion) {
  const {page,context}=await newPage({width:1440,height:900},{motion});
  try {
    const before=await page.locator('#libraryPanel').boundingBox();
    await page.locator('#toggleLibrary').click();
    const style=await page.evaluate(()=>({workspaceTransition:getComputedStyle(document.querySelector('#workspace')).transitionDuration,libraryTransition:getComputedStyle(document.querySelector('#libraryPanel')).transitionDuration,reduce:matchMedia('(prefers-reduced-motion: reduce)').matches}));
    await settle(page);
    const after=await page.locator('#libraryPanel').boundingBox();
    const duration=Math.max(...(style.workspaceTransition+','+style.libraryTransition).split(',').map(value=>parseFloat(value)*(value.trim().endsWith('ms')?1:1000)));
    record(motion+'-collapse-animation-preference',motion==='reduce'?duration<=1:duration>=100,{before,after,style,durationMs:duration});
  }catch(error){record(motion+'-animation-error',false,{error:String(error.stack||error)});}
  finally {await context.close();}
}

async function shortcutChecks() {
  let {page,context}=await newPage({width:1440,height:900});
  try {
    await page.locator('#pageZoom').selectOption('0.5');await settle(page);
    record('zoom-minimum-disables-minus',await page.locator('#zoomOutButton').isDisabled()&&!await page.locator('#zoomInButton').isDisabled(),await state(page));
    await page.locator('#pageZoom').selectOption('3');await settle(page);
    record('zoom-maximum-disables-plus',await page.locator('#zoomInButton').isDisabled()&&!await page.locator('#zoomOutButton').isDisabled(),await state(page));
    await page.locator('#pageZoom').selectOption('1');await settle(page);
    const viewport=await page.locator('#readerViewport').boundingBox();
    await page.mouse.move(viewport.x+viewport.width/2,viewport.y+viewport.height/2);
    await page.keyboard.down('Control');await page.mouse.wheel(0,-150);await page.keyboard.up('Control');await settle(page);
    const afterCtrl=await state(page),afterCtrlGeometry=await geometry(page);
    record('control-wheel-zooms-paper',Number(afterCtrl.zoom)>1&&afterCtrlGeometry.window.width===1440,{afterCtrl,afterCtrlGeometry});
    await page.mouse.wheel(0,150);await settle(page);
    record('normal-wheel-preserves-zoom',(await state(page)).zoom===afterCtrl.zoom,await state(page));
    await page.locator('#readerFocusButton').click();await settle(page);
    await page.locator('#readerFullscreenButton').click();await settle(page);
    await page.locator('#readerFullscreenButton').click();await settle(page);
    const nested=await state(page);
    record('fullscreen-from-focus-restores-focus',nested.focus&&!nested.fullscreen&&!nested.nativeFullscreen,nested);
    await page.keyboard.press('Escape');await settle(page);
    record('nested-focus-escape-restores-workspace',!(await state(page)).focus,await state(page));
  }catch(error){record('zoom-shortcuts-error',false,{error:String(error.stack||error)});}
  finally{await context.close();}
  ({page,context}=await newPage({width:602,height:884}));
  try {
    await page.locator('#toggleLibrary').click();await settle(page);
    record('drawer-library-initial-focus',await page.locator('#libraryPanel').evaluate(el=>el.contains(document.activeElement)),await state(page));
    const libraryKeys=await focusLoop(page,'#libraryPanel');
    record('drawer-library-keyboard-contained',libraryKeys.focusableCount>0&&!libraryKeys.escapes.length,libraryKeys);
    await page.keyboard.press('Escape');await settle(page);
    let closed=await state(page);
    record('drawer-escape-restores-trigger',closed.library==='false'&&closed.libraryInert&&closed.active==='toggleLibrary',closed);
    await page.locator('#toggleLibrary').click();await settle(page);
    const backdrop=await page.locator('#readerDrawerBackdrop').boundingBox();
    await page.mouse.click(backdrop.x+backdrop.width-5,backdrop.y+backdrop.height/2);await settle(page);
    closed=await state(page);
    record('drawer-backdrop-closes-library',closed.library==='false'&&closed.libraryInert&&closed.active==='toggleLibrary',closed);
    await page.locator('#toggleEvidence').click();await settle(page);
    record('drawer-evidence-initial-focus',await page.locator('#evidencePanel').evaluate(el=>el.contains(document.activeElement)),await state(page));
    const evidenceKeys=await focusLoop(page,'#evidencePanel');
    record('drawer-evidence-keyboard-contained',evidenceKeys.focusableCount>0&&!evidenceKeys.escapes.length,evidenceKeys);
    const backdropRight=await page.locator('#readerDrawerBackdrop').boundingBox();
    await page.mouse.click(backdropRight.x+5,backdropRight.y+backdropRight.height/2);await settle(page);
    closed=await state(page);
    record('drawer-backdrop-closes-evidence',closed.evidence==='false'&&closed.evidenceInert&&closed.active==='toggleEvidence',closed);
  }catch(error){record('drawer-shortcuts-error',false,{error:String(error.stack||error)});}
  finally{await context.close();}
}

async function main(){
  browser=await chromium.launch({headless:true,executablePath:process.env.BROWSER_EXECUTABLE||'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe'});
  try {
    const requestedViewport=process.argv.find(argument=>argument.startsWith('--viewport='))?.slice('--viewport='.length);
    const shortcutsOnly=process.argv.includes('--shortcuts');
    const sizes=[{width:1440,height:1000},{width:1280,height:600},{width:1024,height:768},{width:1000,height:700},{width:999,height:768},{width:602,height:884},{width:390,height:844},{width:320,height:720}];
    if(requestedViewport&&!sizes.some(size=>`${size.width}x${size.height}`===requestedViewport))throw new Error('Choose one of the existing --viewport=WIDTHxHEIGHT cases.');
    if(!shortcutsOnly)for(const size of sizes.filter(size=>!requestedViewport||`${size.width}x${size.height}`===requestedViewport))await viewportChecks(size);
    if(!requestedViewport&&!shortcutsOnly){
      for(const mode of ['native','unavailable','rejected'])await fullscreenChecks(mode);
      for(const motion of ['no-preference','reduce'])await animationChecks(motion);
    }
    if(!requestedViewport)await shortcutChecks();
  }finally{
    report.passed=report.checks.length>0 && report.checks.every(item=>item.passed) && !report.pageErrors.length && !report.consoleErrors.length && !report.blockedRequests.length;
    report.finishedAt=new Date().toISOString();report.summary={checks:report.checks.length,failed:report.checks.filter(item=>!item.passed).map(item=>item.name),pageErrors:report.pageErrors.length,consoleErrors:report.consoleErrors.length,blockedRequests:report.blockedRequests.length};
    fs.writeFileSync(path.join(output,'report.json'),JSON.stringify(report,null,2),{flag:'wx'});
    await browser.close();console.log(JSON.stringify({output,passed:report.passed,summary:report.summary}));if(!report.passed)process.exitCode=1;
  }
}
main().catch(error=>{console.error(error);process.exitCode=1;});
