/* Offline regression for extracted text → translation → source PDF → return.
 * Uses the real shell, management, task and bilingual modules. All API requests
 * are intercepted with synthetic data; the reader is a navigation protocol
 * fixture. No live model, user document or persistent application data is used. */
'use strict';
const fs = require('node:fs');
const path = require('node:path');
const crypto = require('node:crypto');
const assert = require('node:assert/strict');
const {chromium} = require(process.env.PLAYWRIGHT_MODULE || 'C:/Users/g2806/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright');
const root = path.resolve(__dirname, '..');
const origin = 'http://127.0.0.1:8765';
const output = path.join(root, 'artifacts/browser', `translation-return-${new Date().toISOString().replace(/[:.]/g, '-')}-${crypto.randomBytes(3).toString('hex')}`);
fs.mkdirSync(output, {recursive: true});
const names = ['notices.js','styles.css', 'shell.css', 'manage.css', 'research.css', 'bilingual.css', 'manage.js', 'research.js', 'bilingual.js', 'shell.js'];
const assets = new Map(names.map(name => [name, fs.readFileSync(path.join(root, 'research_workspace/static', name))]));
const report = {output, startedAt: new Date().toISOString(), mode: 'offline real shell/manage/research/bilingual with synthetic reader bridge', checks: [], pageErrors: [], unexpectedRequests: [], sha256: {}, limitations: ['The reader bridge is a protocol fixture. Real PDF rendering, server execution and model quality are outside this test.']};
for (const [name, bytes] of assets) report.sha256[name] = crypto.createHash('sha256').update(bytes).digest('hex');
report.sha256['scripts/translation_return_smoke.cjs'] = crypto.createHash('sha256').update(fs.readFileSync(__filename)).digest('hex');
const documents = [{id: 'fixture-alpha', title: 'Synthetic translation source', filename: 'alpha.pdf', page_count: 3, authors: ['Fixture Author'], created_at: '2026-10-13T00:00:00Z', available_run: {id: 'run-alpha', version: 1, status: 'completed'}, current_run: {id: 'run-alpha', version: 1, status: 'completed'}}];
const paragraphs = Array.from({length: 12}, (_, index) => ({id: `fixture-alpha-2-${index}`, ordinal: index, page: 2, bbox: [.1, .12, .85, .3], text: `Original source paragraph ${index + 1} for page 2.\nThe selected original text must survive the translation and reader round trip. More content for a realistic scrollable extraction workspace.`}));
const jobs = [];
let postMode = 'immediate', pendingPost = null, posts = [], readControl = null;
function newTranslation(payload) {
  const id = `fixture-job-${jobs.length + 1}`;
  const job = {id, kind: 'translate', status: 'completed', created_at: `2026-10-13T00:00:${String(jobs.length).padStart(2, '0')}Z`, updated_at: id, payload, total: 24, progress: 24, events: [], result: {status: 'completed', target_language: 'zh', total_blocks: 24, blocks: Array.from({length: 24}, (_, index) => ({document_id: 'fixture-alpha', run_id: 'run-alpha', page: 2, block_id: `translation-block-${index}`, bbox: [.1, .12, .85, .3], source_text: `Synthetic translated source paragraph ${index + 1}. ` + 'This paragraph provides enough content to exercise both synchronized scrolling columns. '.repeat(4), translated_text: `合成译文第 ${index + 1} 段。` + '这段离线夹具内容用于检查双语滚动和返回位置的恢复，不代表模型实际生成。'.repeat(5)}))}};
  jobs.unshift(job);
  return job;
}
const harness = `<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">${names.filter(name => name.endsWith('.css')).map(name => `<link rel="stylesheet" href="/static/${name}">`).join('')}<style>body{height:100vh;display:flex;flex-direction:column}.module-navigation{flex-shrink:0}#workspace{padding:24px}.mobile-navigation{display:none}</style></head><body>
<header><button id="modelSettingsButton">模型设置</button><button id="accountButton">工作空间</button></header>
<nav class="module-navigation">${['reader', 'manage', 'research', 'discovery', 'graph', 'data'].map(name => `<button data-module="${name}">${name}</button>`).join('')}</nav>
<main id="workspace"><button id="readerReturnButton" hidden>返回文字核对</button><p id="fixtureEvidence">Synthetic reader</p></main><nav class="mobile-navigation"></nav>
<div id="moduleReturnBar" hidden><button id="moduleReturnButton" type="button">返回文字核对</button></div><section id="moduleRoot" class="module-root" hidden></section>
<dialog id="accountDialog"><button id="closeAccount">关闭</button><p id="accountDescription"></p><form id="accountForm"></form><p id="accountStatus"></p><button id="logoutButton">退出</button></dialog>
${['notices.js', 'manage.js', 'bilingual.js', 'research.js'].map(name => `<script src="/static/${name}"></script>`).join('')}
<script>
window.fixture={counts:{manage:{mount:0,destroy:0,suspend:0,resume:0},research:{mount:0,destroy:0,suspend:0,resume:0}},requests:[],evidence:[],mode:'immediate',release:null,alerts:[]};
window.alert=message=>fixture.alerts.push(message);
for(const [global,key] of [['ResearchManage','manage'],['ResearchTasks','research']]){const actual=window[global];window[global]={...actual,mount(root,options){fixture.counts[key].mount++;fixture[key+'Options']=options;const controller=actual.mount(root,options);for(const method of ['destroy','suspend','resume']){const original=controller[method];controller[method]=(...args)=>{fixture.counts[key][method]++;return original?.(...args);};}return controller;}};}
let navigation=0;
window.ResearchWorkspace={
 api:async(url,init)=>{fixture.requests.push({url,method:init?.method||'GET'});const response=await fetch(url,init);const result=await response.json();if(!response.ok)throw Error(result.detail||'Synthetic request failed');return result;},
 invalidateEvidenceNavigation(){navigation++;},
 async onEvidence(evidence){const request=++navigation;if(fixture.mode==='failure')throw Error('Synthetic evidence failure');if(fixture.mode==='deferred')await new Promise(resolve=>{fixture.release=resolve;});if(request!==navigation)return;fixture.evidence.push(evidence);document.querySelector('#fixtureEvidence').textContent=evidence.document_id+' / page '+evidence.page+' / '+evidence.block_id;window.dispatchEvent(new CustomEvent('research:reader'));}
};
function placeholder(root){root.textContent='Synthetic other module';return {ready:Promise.resolve(),destroy(){}};}
window.ResearchExplore={mountDiscovery:placeholder,mountGraph:placeholder};window.ResearchData={mount:placeholder};window.ResearchModelSettings={open(){}};
</script><script src="/static/shell.js"></script></body></html>`;
function check(name, passed, detail) { report.checks.push({name, passed: Boolean(passed), ...(detail === undefined ? {} : {detail})}); assert.ok(passed, name + (detail === undefined ? '' : ': ' + JSON.stringify(detail))); }
async function waitForNode(predicate, message) { const limit = Date.now() + 7000; while (!predicate()) { if (Date.now() > limit) throw Error(message); await new Promise(resolve => setTimeout(resolve, 20)); } }
async function main() {
  const browser = await chromium.launch({headless: true, executablePath: process.env.BROWSER_EXECUTABLE || 'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe'});
  const context = await browser.newContext({viewport: {width: 1100, height: 800}, reducedMotion: 'reduce'});
  const page = await context.newPage(); page.setDefaultTimeout(10000); page.on('pageerror', error => report.pageErrors.push(String(error)));
  await context.route('**/*', async route => {
    const request = route.request(), url = new URL(request.url()), p = url.pathname;
    if (url.origin !== origin) { report.unexpectedRequests.push(request.url()); return route.abort(); }
    if (request.method() === 'POST' && p === '/api/research/tasks') {
      posts.push(request.postDataJSON());
      if (postMode === 'deferred') await new Promise(resolve => { pendingPost = resolve; });
      if (postMode === 'failure') return route.fulfill({status: 503, contentType: 'application/json', body: JSON.stringify({detail: 'Synthetic task creation failure'})});
      return route.fulfill({status: 200, contentType: 'application/json', body: JSON.stringify({job: newTranslation(request.postDataJSON())})});
    }
    if (!['GET', 'HEAD'].includes(request.method())) { report.unexpectedRequests.push(request.method() + ' ' + request.url()); return route.abort(); }
    if (p === '/') return route.fulfill({status: 200, contentType: 'text/html; charset=utf-8', body: harness});
    if (p.startsWith('/static/')) { const name = path.basename(p); if (assets.has(name)) return route.fulfill({status: 200, contentType: name.endsWith('.css') ? 'text/css; charset=utf-8' : 'text/javascript; charset=utf-8', body: assets.get(name)}); }
    let result;
    if (p === '/api/account') result = {user: {mode: 'local'}};
    else if (p === '/api/documents') result = {documents};
    else if (p === '/api/capabilities') result = {intelligence: {configured: true}};
    // Deliberately list older jobs first: the translation origin must select
    // the task returned by POST rather than accidentally relying on ordering.
    else if (p === '/api/research/tasks') result = {jobs: jobs.slice().reverse()};
    else if (p.startsWith('/api/research/tasks/')) {
      const id = p.split('/').at(-1);
      if (readControl?.id === id && readControl.deferNext) {
        readControl.deferNext = false;
        await new Promise(resolve => { readControl.release = resolve; });
        result = {job: readControl.response};
      } else result = {job: jobs.find(job => job.id === id)};
    }
    else if (p.endsWith('/versions')) result = {family: {documents: []}, candidates: []};
    else if (p.endsWith('/structure')) result = {headings: [], references: [], citations: [], limitations: []};
    else {
      const match = p.match(/^\/api\/documents\/fixture-alpha\/pages\/(\d+)(\/image)?$/);
      if (match) {
        if (match[2]) return route.fulfill({status: 200, contentType: 'image/svg+xml', body: '<svg xmlns="http://www.w3.org/2000/svg" width="600" height="800"><rect width="600" height="800" fill="white"/><text x="50" y="50">Synthetic PDF</text></svg>'});
        result = {document_id: 'fixture-alpha', run_id: 'run-alpha', page: Number(match[1]), status: 'completed', blocks: paragraphs.map(block => ({...block, page: Number(match[1])}))};
      }
    }
    if (result) return route.fulfill({status: 200, contentType: 'application/json; charset=utf-8', body: JSON.stringify(result)});
    report.unexpectedRequests.push(request.url()); return route.abort();
  });
  async function selectModule(name) { await page.locator(`[data-module="${name}"]`).click(); }
  async function openText() { await selectModule('manage'); await page.getByRole('tab', {name: '本页文字', exact: true}).click(); await page.locator('.rm-text-block').first().waitFor(); }
  async function read(number) { await page.getByRole('spinbutton', {name: '原文页码'}).fill(String(number)); await page.getByRole('button', {name: '读取本页文字', exact: true}).click(); await page.waitForFunction(number => document.querySelector('.rm-text-block .rm-field-title')?.textContent.includes('第 ' + number + ' 页'), number); }
  async function translate() { await page.locator('.rm-text-block').first().getByRole('button', {name: /^翻译/}).evaluate(button => button.click()); }
  async function translationReady() { await page.locator('.rb-reader').waitFor(); await page.locator('.rb-block').first().waitFor(); }
  async function state() { return page.evaluate(() => ({counts: fixture.counts, requests: fixture.requests.length, module: document.querySelector('[data-module][aria-pressed=true]')?.dataset.module, scroll: document.querySelector('#moduleRoot').scrollTop, selected: window.getSelection()?.rangeCount ? window.getSelection().getRangeAt(0).toString() : '', page: document.querySelector('[aria-label="原文页码"]')?.value, tab: document.querySelector('[role=tab][aria-selected=true]')?.textContent})); }
  async function readerFromTranslation() { await page.locator('.rb-panel-source .rb-evidence').nth(5).evaluate(button => button.click()); await page.locator('#readerReturnButton').waitFor({state: 'visible'}); }
  try {
    await page.goto(origin);
    check('ordinary reader has no temporary return entry', await page.locator('#readerReturnButton').isHidden() && await page.locator('#moduleReturnBar').isHidden());
    await openText(); await read(2); await page.getByRole('checkbox', {name: '保留原文换行', exact: true}).check();
    const savedManage = await page.evaluate(() => {const root = document.querySelector('#moduleRoot'), text = root.querySelector('.rm-block-text'); fixture.text = text; fixture.manageShell = root.firstChild; text.focus({preventScroll: true}); window.getSelection().setBaseAndExtent(text.firstChild, 50, text.firstChild, 12); document.dispatchEvent(new Event('selectionchange')); root.scrollTop = 920; return {scroll: root.scrollTop, selected: window.getSelection().getRangeAt(0).toString()};});
    check('management fixture has meaningful scroll and reverse selection', savedManage.scroll > 400 && savedManage.selected.length === 38, savedManage);
    await translate(); await translationReady();
    check('translation creates exactly one task for the selected source', posts.length === 1 && posts[0].selected_text === savedManage.selected && posts[0].block_ids?.[0] === 'fixture-alpha-2-0' && posts[0].document_id === 'fixture-alpha' && posts[0].run_id === 'run-alpha', posts);
    check('translation result offers return to original text', await page.locator('#moduleReturnBar').isVisible() && await page.locator('#moduleReturnButton').textContent() === '返回文字核对');
    check('newly created task is selected explicitly', (await page.getByRole('link', {name: '导出任务与来源', exact: true}).getAttribute('href')).includes('/fixture-job-1/'));
    await page.screenshot({path: path.join(output, 'translation-return.png')});
    let current = await state();
    check('translation retains management controller instead of destroying it', current.counts.manage.mount === 1 && current.counts.manage.suspend === 1 && current.counts.manage.destroy === 0, current.counts);
    await page.locator('.rb-scroll[data-side="source"]').evaluate(element => {element.scrollTop = 1100;});
    await page.waitForTimeout(100);
    const savedResearch = await page.evaluate(() => {const root = document.querySelector('#moduleRoot'), source = document.querySelector('.rb-scroll[data-side="source"]'), target = document.querySelector('.rb-scroll[data-side="translation"]'); fixture.researchShell = root.firstChild; fixture.bilingual = document.querySelector('.rb-reader'); fixture.sourcePane = source; const text = document.querySelectorAll('.rb-panel-source .rb-text')[5]; fixture.resultText = text; source.focus({preventScroll: true}); window.getSelection().setBaseAndExtent(text.firstChild, 64, text.firstChild, 10); root.scrollTop = 500; return {scroll: root.scrollTop, source: source.scrollTop, translation: target.scrollTop, selected: window.getSelection().getRangeAt(0).toString(), requests: fixture.requests.length};});
    check('task fixture has nonzero bilingual positions', savedResearch.source > 500 && savedResearch.translation > 500, savedResearch);
    await readerFromTranslation();
    check('translation source opens PDF with translation-specific return', await page.locator('#readerReturnButton').textContent() === '返回翻译结果' && await page.locator('#moduleReturnBar').isHidden());
    await page.screenshot({path: path.join(output, 'reader-return.png')});
    await page.setViewportSize({width: 900, height: 740}); await page.waitForTimeout(50);
    await page.setViewportSize({width: 1100, height: 800}); await page.waitForTimeout(50);
    check('nested source navigation forwards exact evidence', await page.evaluate(() => fixture.evidence.at(-1).block_id === 'translation-block-5' && fixture.evidence.at(-1).page === 2));
    current = await state();
    check('nested PDF navigation keeps both controllers alive', current.counts.manage.destroy === 0 && current.counts.research.destroy === 0 && current.counts.research.suspend === 1, current.counts);
    await page.locator('#readerReturnButton').click(); await translationReady(); await page.waitForTimeout(100);
    const returnedResearch = await page.evaluate(() => ({same: document.querySelector('#moduleRoot').firstChild === fixture.researchShell && document.querySelector('.rb-reader') === fixture.bilingual && document.querySelector('.rb-scroll[data-side="source"]') === fixture.sourcePane, scroll: document.querySelector('#moduleRoot').scrollTop, source: document.querySelector('.rb-scroll[data-side="source"]').scrollTop, translation: document.querySelector('.rb-scroll[data-side="translation"]').scrollTop, selected: window.getSelection().toString(), focus: document.activeElement === fixture.sourcePane, requests: fixture.requests.length, status: document.querySelector('.rs-job-status').textContent}));
    check('PDF return restores the same translation DOM and saved result', returnedResearch.same && returnedResearch.status.includes('已完成'), returnedResearch);
    check('PDF return preserves outer and bilingual positions after reader resize', ['scroll', 'source', 'translation'].every(key => Math.abs(returnedResearch[key] - savedResearch[key]) < 2), {before: savedResearch, after: returnedResearch});
    check('PDF return restores selection and focus in translation result', returnedResearch.selected === savedResearch.selected && returnedResearch.focus, returnedResearch);
    check('completed translation return does not refetch or recreate a task', returnedResearch.requests === savedResearch.requests && posts.length === 1, {before: savedResearch.requests, after: returnedResearch.requests, posts: posts.length});
    check('the second return remains available after returning from PDF', await page.locator('#moduleReturnBar').isVisible() && await page.locator('#moduleReturnButton').textContent() === '返回文字核对');
    const anchorBeforeResize = await page.locator('.rb-scroll[data-side="source"]').evaluate(pane => {const block = [...pane.querySelectorAll('.rb-block')].find(item => item.offsetTop + item.offsetHeight > pane.scrollTop + .5); return {id: block.dataset.blockId, fraction: (pane.scrollTop - block.offsetTop) / block.offsetHeight};});
    await readerFromTranslation(); await page.setViewportSize({width: 900, height: 740}); await page.waitForTimeout(50); await page.locator('#readerReturnButton').click(); await translationReady(); await page.waitForTimeout(100);
    const anchorAfterResize = await page.locator('.rb-scroll[data-side="source"]').evaluate(pane => {const block = [...pane.querySelectorAll('.rb-block')].find(item => item.offsetTop + item.offsetHeight > pane.scrollTop + .5); return {id: block.dataset.blockId, fraction: (pane.scrollTop - block.offsetTop) / block.offsetHeight};});
    check('return at a different viewport width preserves the visible source block and fraction', anchorAfterResize.id === anchorBeforeResize.id && Math.abs(anchorAfterResize.fraction - anchorBeforeResize.fraction) < .03, {before: anchorBeforeResize, after: anchorAfterResize});
    await page.screenshot({path: path.join(output, 'resized-translation-return.png')});
    await page.setViewportSize({width: 1100, height: 800}); await page.waitForTimeout(100);
    await page.locator('#moduleReturnButton').click(); await page.locator('.rm-text-block').first().waitFor(); await page.waitForTimeout(50); current = await state();
    check('second return restores original management page and tab', current.module === 'manage' && current.page === '2' && current.tab === '本页文字' && current.counts.manage.mount === 1, current);
    check('second return restores management scroll and reversed selection', Math.abs(current.scroll - savedManage.scroll) < 2 && current.selected === savedManage.selected, {before: savedManage, after: current});
    check('second return restores original DOM focus and line break mode', await page.evaluate(() => document.querySelector('#moduleRoot').firstChild === fixture.manageShell && document.querySelector('.rm-block-text') === fixture.text && document.activeElement === fixture.text) && await page.getByRole('checkbox', {name: '保留原文换行', exact: true}).isChecked());
    check('return chain clears after original text is restored', await page.locator('#moduleReturnBar').isHidden() && await page.locator('#readerReturnButton').isHidden() && posts.length === 1);
    await page.locator('.rm-text-block').first().getByRole('button', {name: '定位原文', exact: true}).evaluate(button => button.click()); await page.locator('#readerReturnButton').waitFor({state: 'visible'});
    check('direct management-to-reader still offers the original return', await page.locator('#readerReturnButton').textContent() === '返回文字核对');
    await page.locator('#readerReturnButton').click(); await page.locator('.rm-text-block').first().waitFor();
    check('direct management return reuses its original controller', (await state()).counts.manage.mount === 1);
    await translate(); await translationReady();
    check('repeated translation selects its new job even with older-first history', (await page.getByRole('link', {name: '导出任务与来源', exact: true}).getAttribute('href')).includes('/fixture-job-2/'));
    await readerFromTranslation();
    const beforeClear = (await state()).counts;
    await selectModule('data'); current = await state();
    check('explicit module navigation disposes both retained levels', current.module === 'data' && current.counts.manage.destroy === beforeClear.manage.destroy + 1 && current.counts.research.destroy === beforeClear.research.destroy + 1 && await page.locator('#moduleReturnBar').isHidden() && await page.locator('#readerReturnButton').isHidden(), {before: beforeClear, after: current.counts});
    await selectModule('research'); await translationReady();
    check('ordinary task navigation does not inherit an old management return', await page.locator('#moduleReturnBar').isHidden());
    await readerFromTranslation(); await page.locator('#readerReturnButton').click(); await translationReady();
    check('a saved translation can independently return from PDF', (await state()).module === 'research' && await page.locator('#moduleReturnBar').isHidden() && posts.length === 2);
    await page.evaluate(() => {fixture.mode = 'failure';}); await page.locator('.rb-panel-source .rb-evidence').first().evaluate(button => button.click()); await page.locator('.app-notice[data-notice-source="rb-feedback"]').waitFor();
    check('failed PDF navigation stays on the result without false return state', (await state()).module === 'research' && await page.locator('#readerReturnButton').isHidden());
    await page.evaluate(() => {fixture.mode = 'deferred'; fixture.oldEvidence = fixture.researchOptions.onEvidence;}); await page.locator('.rb-panel-source .rb-evidence').first().evaluate(button => button.click()); await page.waitForFunction(() => typeof fixture.release === 'function'); await selectModule('data'); await page.evaluate(() => fixture.release()); await page.waitForTimeout(50);
    check('late source response cannot overtake a newer module', (await state()).module === 'data' && await page.locator('#readerReturnButton').isHidden());
    await page.evaluate(() => {fixture.mode = 'immediate'; return fixture.oldEvidence({document_id: 'fixture-alpha', run_id: 'run-alpha', page: 2});});
    check('destroyed task evidence callbacks cannot reopen reader', (await state()).module === 'data');
    await openText(); postMode = 'failure'; await translate(); await page.waitForFunction(() => fixture.alerts.some(message => message.includes('Synthetic task creation failure')) || document.querySelector('.app-notice[data-notice-source="rm-notice"]')?.textContent.includes('Synthetic task creation failure'));
    check('failed translation submission keeps original text accessible', (await state()).module === 'manage' && await page.locator('#moduleReturnBar').isHidden());
    postMode = 'deferred'; await translate(); await page.waitForFunction(() => fixture.requests.filter(item => item.method === 'POST').length === 4); await waitForNode(() => pendingPost, 'Synthetic translation POST did not arrive');
    await page.evaluate(() => {fixture.oldTranslate = fixture.manageOptions.onTranslate;}); await selectModule('data'); postMode = 'immediate'; pendingPost(); pendingPost = null; await page.waitForTimeout(100);
    check('late translation submission does not steal newer navigation', (await state()).module === 'data' && await page.locator('#moduleReturnBar').isHidden() && await page.locator('#readerReturnButton').isHidden());
    const postCount = posts.length; await page.evaluate(() => fixture.oldTranslate({document_id: 'fixture-alpha', run_id: 'run-alpha', page: 2, block_id: 'fixture-alpha-2-0'}));
    check('destroyed management callback cannot create duplicate translation', posts.length === postCount && (await state()).module === 'data');
    const pendingJob = newTranslation({target_language: 'zh'});
    pendingJob.status = 'running'; pendingJob.progress = 12; pendingJob.result.status = 'partial';
    await page.evaluate(id => ResearchShell.activate('research', {jobId: id}), pendingJob.id); await translationReady();
    readControl = {id: pendingJob.id, deferNext: true, release: null, response: {...pendingJob, status: 'completed', progress: 24, updated_at: 'late-read', result: {...pendingJob.result, status: 'completed'}}};
    await waitForNode(() => readControl.release, 'Running task did not issue its scheduled polling GET');
    const beforeSuspend = await page.evaluate(() => { fixture.pendingShell = document.querySelector('#moduleRoot').firstChild; fixture.pendingBilingual = document.querySelector('.rb-reader'); return {requests: fixture.requests.length, status: fixture.pendingShell.querySelector('.rs-job-status').textContent}; });
    await readerFromTranslation(); readControl.release(); readControl.release = null; await page.waitForTimeout(1650);
    const duringSuspend = await page.evaluate(() => ({requests: fixture.requests.length, status: fixture.pendingShell.querySelector('.rs-job-status').textContent, sameResult: fixture.pendingShell.querySelector('.rb-reader') === fixture.pendingBilingual}));
    check('in-flight poll response cannot replace suspended task result', duringSuspend.status === beforeSuspend.status && duringSuspend.status.includes('执行中') && duringSuspend.sameResult, {before: beforeSuspend, after: duringSuspend});
    check('running task polling pauses while viewing its PDF source', duringSuspend.requests === beforeSuspend.requests, {before: beforeSuspend.requests, after: duringSuspend.requests});
    pendingJob.status = 'completed'; pendingJob.progress = 24; pendingJob.result.status = 'completed'; pendingJob.updated_at = 'fresh-resume-read';
    await page.locator('#readerReturnButton').click(); await translationReady();
    await page.waitForFunction(() => document.querySelector('.rs-job-status')?.textContent.includes('已完成'));
    check('pending task polling resumes and loads fresh completion after return', (await state()).requests > duringSuspend.requests && await page.evaluate(() => document.querySelector('#moduleRoot').firstChild === fixture.pendingShell) && posts.length === postCount);
    readControl = null;
    const qaJob = {id: 'fixture-qa', kind: 'qa', status: 'completed', created_at: '2026-10-13T00:01:00Z', updated_at: 'qa-v1', events: [], result: {claims: [{text: 'Synthetic evidence-backed result.', citations: [{evidence_id: 'qa-source', quote: 'Original fixture text'}]}], sources: [{evidence_id: 'qa-source', document_id: 'fixture-alpha', run_id: 'run-alpha', page: 2, block_id: 'fixture-alpha-2-0', document_title: 'Synthetic source'}]}};
    jobs.unshift(qaJob); await page.evaluate(id => ResearchShell.activate('research', {jobId: id}), qaJob.id); await page.locator('.rs-citation').waitFor();
    await page.evaluate(() => { fixture.qaShell = document.querySelector('#moduleRoot').firstChild; });
    await page.locator('.rs-citation').click(); await page.locator('#readerReturnButton').waitFor({state: 'visible'});
    check('non-translation evidence uses a task-result return label', await page.locator('#readerReturnButton').textContent() === '返回任务结果');
    await page.locator('#readerReturnButton').click(); await page.locator('.rs-citation').waitFor();
    check('non-translation task also restores its exact result DOM', await page.evaluate(() => document.querySelector('#moduleRoot').firstChild === fixture.qaShell) && posts.length === postCount);
    check('no browser script errors', report.pageErrors.length === 0, report.pageErrors);
    check('all requests stayed inside the synthetic allowlist', report.unexpectedRequests.length === 0, report.unexpectedRequests);
    await page.screenshot({path: path.join(output, 'completed.png')});
  } catch (error) { report.failure = String(error.stack || error); process.exitCode = 1; try { await page.screenshot({path: path.join(output, 'failure.png')}); } catch {} }
  finally { if (pendingPost) { postMode = 'immediate'; pendingPost(); } if (readControl?.release) readControl.release(); report.finishedAt = new Date().toISOString(); report.passed = !report.failure && report.checks.every(item => item.passed); fs.writeFileSync(path.join(output, 'report.json'), JSON.stringify(report, null, 2) + '\n'); console.log(JSON.stringify({passed: report.passed, checks: report.checks.length, output, failure: report.failure}, null, 2)); await browser.close(); }
}
main().catch(error => {console.error(error); process.exitCode = 1;});
