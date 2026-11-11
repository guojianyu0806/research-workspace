/* Actual shell/task modules and app.js evidence bridge; synthetic APIs and PDF view only. */
'use strict';
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const crypto = require('node:crypto');
const {execFileSync} = require('node:child_process');
const {chromium} = require(process.env.PLAYWRIGHT_MODULE || 'C:/Users/g2806/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright');
const root = path.resolve(__dirname, '..'), staticDir = path.join(root, 'research_workspace/static');
const baseline = process.argv.includes('--baseline');
const researchSource = baseline ? execFileSync('git', ['show', 'HEAD:research_workspace/static/research.js'], {cwd: root, encoding: 'utf8', windowsHide: true})
  : fs.readFileSync(path.join(staticDir, 'research.js'), 'utf8');
const app = fs.readFileSync(path.join(staticDir, 'app.js'), 'utf8');
const start = app.indexOf('  window.ResearchWorkspace = {'), end = app.indexOf('\n  initialize();', start);
assert.ok(start >= 0 && end > start, 'extract the current, actual application evidence bridge');
const bridge = app.slice(start, end);
const output = path.join(root, 'artifacts/browser', `task-evidence-origin-${new Date().toISOString().replace(/[:.]/g, '-')}-${crypto.randomBytes(3).toString('hex')}`);
fs.mkdirSync(output, {recursive: true});
const report = {passed: false, baseline, checks: [], pageErrors: [], networkRequests: [], output,
  sourceSha256: {app: crypto.createHash('sha256').update(app).digest('hex'), research: crypto.createHash('sha256').update(researchSource).digest('hex')},
  limitations: ['Real shell, research, bilingual and extracted app bridge; synthetic saved tasks and deferred APIs.',
    'PDF rendering is replaced by an identity recorder. No server, real model or external request is used.']};

async function mount(browser, {kind = 'qa', running = false} = {}) {
  const page = await browser.newPage({viewport: {width: 1360, height: 880}});
  page.setDefaultTimeout(6000);
  page.on('pageerror', error => report.pageErrors.push(String(error)));
  await page.route('**/*', route => { report.networkRequests.push(route.request().url()); return route.abort(); });
  await page.setContent(`<header><button id="modelSettingsButton">Model</button><button id="accountButton">Account</button></header>
    <nav>${['reader', 'research'].map(name => `<button data-module="${name}">${name}</button>`).join('')}</nav>
    <main id="workspace"><button id="readerReturnButton" hidden>Return</button></main><nav class="mobile-navigation"></nav>
    <div id="moduleReturnBar" hidden><button id="moduleReturnButton">Return</button></div><section id="moduleRoot" hidden></section>
    <dialog id="accountDialog"><button id="closeAccount">Close</button><p id="accountDescription"></p><form id="accountForm"></form><p id="accountStatus"></p><button id="logoutButton">Logout</button></dialog>`);
  for (const file of ['research.css', 'bilingual.css']) await page.addStyleTag({path: path.join(staticDir, file)});
  for (const file of ['notices.js', 'bilingual.js']) await page.addScriptTag({path: path.join(staticDir, file)});
  await page.addScriptTag({content: researchSource});
  await page.evaluate(({kind, running}) => {
    window.fixture = {kind, running, opened: [], calls: [], streams: [], holdB: false, holdPost: false};
    window.EventSource = class extends EventTarget {
      constructor(url) { super(); this.url = url; fixture.streams.push(this); }
      close() { this.closed = true; }
    };
  }, {kind, running});
  await page.addScriptTag({content: `
    let navigationRequest = 0;
    const docs = {A:{id:'doc-A',title:'Archived A',page_count:2,available_run:{id:'run-A'},archived_at:'2026-11-01'},
      B:{id:'doc-B',title:'Current B',page_count:2,available_run:{id:'run-B'}}};
    const state = {documents:[docs.B],selectedId:'doc-B'};
    function makeJob(id,kind='qa') {
      const doc = id === 'A' ? docs.A : docs.B;
      const source = {evidence_id:'E_'+id,document_id:doc.id,document_title:doc.title,run_id:doc.available_run.id,
        block_id:'block-'+id,page:2,bbox:[.1,.1,.9,.2],text:'Quote '+id};
      const result = {status:'answered',claims:[{text:'Result '+id,citations:[{evidence_id:source.evidence_id,quote:source.text}]}],sources:[source]};
      if(kind==='translate') Object.assign(result,{status:'completed',target_language:'zh',total_blocks:1,
        blocks:[{...source,source_text:'Source A',translated_text:'Translation A'}]});
      if(kind==='agent_research') Object.assign(result,{evidence:[source],agent_graphs:[{
        nodes:[{id:'node-A',document_id:'doc-A',run_id:'run-A',title:'Paper A'},{id:'node-B',document_id:'doc-B',title:'Paper B'}],
        edges:[{source:'node-A',target:'node-B',evidence_ids:[source.evidence_id]}]}]});
      return {id,kind,status:id==='A'&&fixture.running?'running':'completed',created_at:'2026-11-11T00:00:00Z',updated_at:id,
        events:[],document_ids:[doc.id],payload:{question:'Question '+id},progress:1,total:1,result};
    }
    const jobs = {A:makeJob('A',fixture.kind),B:makeJob('B')};
    async function api(url,init={}) {
      fixture.calls.push((init.method||'GET')+' '+url);
      if(url==='/api/account') return {user:{mode:'local'}};
      if(url==='/api/capabilities') return {intelligence:{configured:true}};
      if(url==='/api/documents') return {documents:[docs.B]};
      if(url==='/api/research/tasks'&&init.method==='POST') {
        if(fixture.holdPost) await new Promise(resolve=>fixture.releasePost=resolve);
        jobs.C=makeJob('C');return {job:jobs.C};
      }
      if(url==='/api/research/tasks') return {jobs:Object.values(jobs)};
      if(url.startsWith('/api/research/tasks/')) {
        const id=url.split('/').pop().split('?')[0];
        if(id==='B'&&fixture.holdB) await new Promise(resolve=>fixture.releaseB=()=>{fixture.holdB=false;resolve();});
        return {job:jobs[id]};
      }
      if(url==='/api/documents/doc-A') return new Promise(resolve=>fixture.releaseEvidence=()=>resolve({document:docs.A}));
      throw Error('Unexpected fixture request '+url);
    }
    function refreshDocuments(){} function currentDocument(){return state.documents.find(doc=>doc.id===state.selectedId);}
    function selectDocument(id,options){state.selectedId=id;fixture.opened.push({id,...options});}
    ${bridge}
    window.ResearchModelSettings={open(){}};
  `});
  await page.addScriptTag({path: path.join(staticDir, 'shell.js')});
  await page.evaluate(() => ResearchShell.activate('research', {jobId: 'A'}));
  await selected(page, 'A');
  return page;
}
const row = (page, id) => page.locator(`.rs-history-item[data-job-id="${id}"]`);
const selected = (page, id) => page.locator(`.rs-history-item[data-job-id="${id}"][aria-pressed="true"]`).waitFor();
const settle = page => page.evaluate(() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve))));
async function evidenceButton(page, kind = 'qa') {
  if (kind === 'agent_research') { await page.locator('.rs-agent-graphs summary').click(); return page.locator('.rs-agent-edge-primary'); }
  return page.locator(kind === 'translate' ? '.rb-evidence' : '.rs-citation').first();
}
async function assertResearch(page, id) {
  assert.ok(await page.locator('#workspace').isHidden(), 'a stale source must not replace the selected research task');
  await selected(page, id);
  assert.deepEqual(await page.evaluate(() => fixture.opened), []);
}
async function createTask(page) {
  await page.getByRole('combobox', {name: '任务类型', exact: true}).selectOption('qa');
  await page.getByRole('textbox', {name: '研究问题', exact: true}).fill('Synthetic new question');
  await page.locator('.rs-document-choice input[value="doc-B"]').check();
  await page.getByRole('button', {name: '创建研究任务', exact: true}).click();
}
async function releaseAndReturnA(page) {
  await page.evaluate(() => fixture.releaseEvidence());
  await page.locator('#readerReturnButton').waitFor();
  assert.deepEqual(await page.evaluate(() => fixture.opened), [{id:'doc-A',runId:'run-A',page:2,bbox:[.1,.1,.9,.2],hitKey:'block-A'}]);
  await page.locator('#readerReturnButton').click(); await selected(page, 'A');
}

async function main() {
  const browser = await chromium.launch({headless: true, executablePath: process.env.BROWSER_EXECUTABLE || 'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe'});
  async function scenario(name, setup, action) {
    const page = await mount(browser, setup);
    try { await action(page); report.checks.push({name, passed: true}); }
    finally { await page.close(); }
  }
  try {
    await scenario('late A evidence cannot override explicit history switch to B', {}, async page => {
      await (await evidenceButton(page)).click(); await page.waitForFunction(() => fixture.releaseEvidence);
      await row(page, 'B').click(); await selected(page, 'B');
      await page.evaluate(() => fixture.releaseEvidence()); await settle(page);
      await assertResearch(page, 'B');
    });
    for (const kind of ['qa', 'translate', 'agent_research']) {
      await scenario(`old ${kind} evidence is blocked while B reads, then A retains its own return point`, {kind}, async page => {
        const evidence = await evidenceButton(page, kind);
        await page.evaluate(() => {fixture.holdB = true;}); await row(page, 'B').click();
        await page.waitForFunction(() => fixture.releaseB);
        await evidence.click(); await settle(page);
        assert.equal(await page.evaluate(() => fixture.calls.filter(call => call === 'GET /api/documents/doc-A').length), 0);
        assert.match(await page.locator('.notice-text').last().textContent(), /任务切换中/);
        await page.evaluate(() => fixture.releaseB()); await selected(page, 'B');
        await row(page, 'A').click(); await selected(page, 'A');
        await (await evidenceButton(page, kind)).click(); await page.waitForFunction(() => fixture.releaseEvidence);
        await releaseAndReturnA(page);
        assert.match(await page.locator('.rs-result').innerText(), kind === 'translate' ? /Translation A/ : /Result A/);
      });
    }
    await scenario('accepting a newer created task cancels the previous pending evidence navigation', {}, async page => {
      await (await evidenceButton(page)).click(); await page.waitForFunction(() => fixture.releaseEvidence);
      await createTask(page); await selected(page, 'C');
      await page.evaluate(() => fixture.releaseEvidence()); await settle(page);
      await assertResearch(page, 'C');
    });
    await scenario('an earlier pending creation cannot replace the retained source task on return', {}, async page => {
      await page.evaluate(() => {fixture.holdPost = true;}); await createTask(page);
      await page.waitForFunction(() => fixture.releasePost);
      await (await evidenceButton(page)).click(); await page.waitForFunction(() => fixture.releaseEvidence);
      await page.evaluate(() => fixture.releaseEvidence()); await page.locator('#readerReturnButton').waitFor();
      await page.evaluate(() => fixture.releasePost()); await settle(page);
      await page.locator('#readerReturnButton').click(); await selected(page, 'A');
      assert.match(await page.locator('.rs-result').innerText(), /Result A/);
      await row(page, 'C').waitFor();
    });
    await scenario('same-task polling does not cancel an intentional evidence navigation', {running: true}, async page => {
      await (await evidenceButton(page)).click(); await page.waitForFunction(() => fixture.releaseEvidence);
      await page.waitForFunction(() => fixture.calls.filter(call => call.startsWith('GET /api/research/tasks/A')).length >= 2);
      await releaseAndReturnA(page);
    });
    assert.deepEqual(report.pageErrors, []); assert.deepEqual(report.networkRequests, []);
    report.passed = true;
  } catch (error) {
    report.failure = {message: error.message, stack: error.stack}; process.exitCode = 1;
  } finally {
    await browser.close();
    fs.writeFileSync(path.join(output, 'report.json'), JSON.stringify(report, null, 2), {flag: 'wx'});
    console.log(JSON.stringify({passed: report.passed, baseline, checks: report.checks.length, output, failure: report.failure?.message}, null, 2));
  }
}
main().catch(error => {console.error(error); process.exitCode = 1;});
