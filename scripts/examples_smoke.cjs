/* Demonstration-input UI regression. All HTTP is fulfilled locally from source
 * files or explicit synthetic fixtures; no service, account, or LLM is contacted.
 * Public catalog and CSV are real source-linked inputs. Document readiness and
 * chart responses below are synthetic and do not prove research/backend quality.
 */
'use strict';
const fs = require('node:fs');
const path = require('node:path');
const crypto = require('node:crypto');
const {execFileSync} = require('node:child_process');
const {chromium} = require(process.env.PLAYWRIGHT_MODULE || 'C:/Users/g2806/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright');
const root = path.resolve(__dirname, '..');
const staticRoot = path.join(root, 'research_workspace/static');
const origin = 'http://127.0.0.1:8765';
const output = path.join(root, 'artifacts/browser', `examples-${new Date().toISOString().replace(/[:.]/g, '-')}-${crypto.randomBytes(3).toString('hex')}`);
fs.mkdirSync(output, {recursive:true});
const sha = buffer => crypto.createHash('sha256').update(buffer).digest('hex');
const assets = new Map();
function collect(directory, prefix = '') {
  for (const item of fs.readdirSync(directory, {withFileTypes:true})) {
    const relative = prefix + item.name;
    if (item.isDirectory()) collect(path.join(directory, item.name), relative + '/');
    else assets.set(relative, fs.readFileSync(path.join(directory, item.name)));
  }
}
collect(staticRoot);
const catalog = JSON.parse(assets.get('examples/catalog.json'));
const csv = assets.get(catalog.dataset.csv_path.replace('/static/', ''));
const csvHash = sha(csv);
const report = {
  startedAt:new Date().toISOString(), output, mode:'offline examples UI fixture',
  source:{gitHead:execFileSync('git', ['rev-parse','HEAD'], {cwd:root, encoding:'utf8', windowsHide:true}).trim(), sha256:Object.fromEntries([...assets].map(([name, bytes]) => [name, sha(bytes)]))},
  checks:[], snapshots:[], requests:[], pageErrors:[], consoleErrors:[], blockedRequests:[], expectedFailures:[],
  limitations:['Catalog and CSV are actual repository inputs, but all document and chart API responses are synthetic.', 'This regression does not validate model answers, PDF parsing, real data persistence, or backend chart mathematics.', 'No HTTP request is continued to a service or external host.']
};
report.source.sha256['scripts/examples_smoke.cjs'] = sha(fs.readFileSync(__filename));
const timestamp = '2026-10-09T00:00:00Z';
const validDocs = catalog.papers.map((paper,index) => ({
  id:`fixture-${paper.key}`, filename:paper.filename, title:paper.title, authors:paper.authors,
  page_count:[15,16,19][index], created_at:timestamp, is_current_version:true, archived_at:null,
  sha256:paper.sha256, available_run:{id:`fixture-run-${paper.key}`, status:'completed'},
  current_run:{id:`fixture-run-${paper.key}`, status:'completed'},
}));
const oldAttention = {...validDocs[0], id:'fixture-attention-v1', filename:'1706.03762v1.pdf', sha256:'4'.repeat(64), is_current_version:false};
let docs = [...validDocs, oldAttention];
const rows = csv.toString('utf8').trim().split(/\r?\n/).slice(1).map(line => { const [model,value] = line.split(','); return [model,Number(value)]; });
const dataset = {id:'fixture-dataset', filename:catalog.dataset.filename, sha256:csvHash, created_at:timestamp,
  row_count:rows.length, headers:['Model','BLEU_EN_DE'], encoding:'UTF-8',
  columns:[{name:'Model',type:'string',missing:0,numeric_count:0},{name:'BLEU_EN_DE',type:'number',missing:0,numeric_count:rows.length}], rows};
let datasets = [dataset], failCharts = 0, failCatalog = 0;
const svg = '<svg xmlns="http://www.w3.org/2000/svg" width="700" height="420"><rect width="700" height="420" fill="white"/><text x="20" y="40" fill="#173e41" font-size="20">SYNTHETIC UI FIXTURE — no model/backend result</text><rect x="40" y="100" width="80" height="250" fill="#187b7b"/><rect x="160" y="80" width="80" height="270" fill="#235b79"/></svg>';
const sameIds = (a,b) => JSON.stringify([...a].sort()) === JSON.stringify([...b].sort());
function check(name, passed, evidence = {}) {
  report.checks.push({name,passed,...evidence});
  console.log(`${passed?'PASS':'FAIL'} ${name}`);
  if (!passed) throw new Error(name);
}
const requestCount = (method, suffix) => report.requests.filter(item => item.method === method && item.path.endsWith(suffix)).length;
async function main() {
  const browser = await chromium.launch({headless:true, executablePath:process.env.BROWSER_EXECUTABLE || 'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe'});
  const context = await browser.newContext({viewport:{width:1440,height:1000}, reducedMotion:'reduce', serviceWorkers:'block'});
  const page = await context.newPage(); page.setDefaultTimeout(10000);
  page.on('pageerror', error => report.pageErrors.push(String(error)));
  page.on('console', message => { if (message.type() === 'error') report.consoleErrors.push(message.text()); });
  await context.route('**/*', async route => {
    const request = route.request(), url = new URL(request.url()), method = request.method(), p = url.pathname;
    if (url.origin !== origin) {report.blockedRequests.push({method,path:url.origin+p}); return route.abort();}
    const entry = {method,path:p,query:url.search}; report.requests.push(entry);
    const json = (body,status=200) => route.fulfill({status,contentType:'application/json; charset=utf-8',body:JSON.stringify(body)});
    if (method === 'GET' && (p === '/' || p.startsWith('/static/'))) {
      const name = p === '/' ? 'index.html' : p.slice('/static/'.length);
      if (name === 'examples/catalog.json' && failCatalog > 0) {failCatalog--;report.expectedFailures.push({method,path:p,status:503});return json({detail:'SYNTHETIC catalog failure'},503);}
      if (!assets.has(name)) {report.blockedRequests.push({method,path:p,reason:'unknown static asset'});return route.abort();}
      return route.fulfill({status:200,contentType:{'.html':'text/html; charset=utf-8','.css':'text/css; charset=utf-8','.js':'text/javascript; charset=utf-8','.json':'application/json; charset=utf-8','.csv':'text/csv; charset=utf-8','.svg':'image/svg+xml'}[path.extname(name)] || 'application/octet-stream',body:assets.get(name)});
    }
    if (method === 'POST' && p === '/api/datasets') {
      entry.fixtureWrite = true; entry.uploadContainsCsv = request.postDataBuffer()?.includes(csv) || false;
      datasets = [dataset]; return json({dataset});
    }
    if (method === 'POST' && p === '/api/datasets/fixture-dataset/charts') {
      entry.fixtureWrite = true; entry.payload = request.postDataJSON();
      await new Promise(resolve => setTimeout(resolve,150));
      if (failCharts > 0) {failCharts--;report.expectedFailures.push({method,path:p,status:503});return json({detail:'SYNTHETIC chart failure; retry is expected'},503);}
      return json({chart:{id:'fixture-chart',spec:{...catalog.dataset.chart},caption:'SYNTHETIC UI chart response, not a backend validation.',transformations:{dropped_csv_rows:[],minmax_normalized:false}}});
    }
    if (method !== 'GET') {report.blockedRequests.push({method,path:p,reason:'unapproved fixture write'});return route.abort();}
    if (p === '/api/capabilities') return json({mode:'local',llm:true,max_upload_mb:50,intelligence:{configured:true}});
    if (p === '/api/account') return json({user:{mode:'local'}});
    if (p === '/api/health') return json({status:'ok',worker_running:true});
    if (p === '/api/documents') return json({documents:docs});
    if (/^\/api\/documents\/[^/]+$/.test(p)) return json({document:docs.find(doc => p.endsWith(doc.id))});
    if (p.endsWith('/image') || p === '/api/charts/fixture-chart/svg') return route.fulfill({status:200,contentType:'image/svg+xml',body:svg});
    if (/\/pages\/\d+$/.test(p)) return json({page:Number(p.split('/').pop()),text:'Synthetic PDF page fixture',blocks:[]});
    if (p === '/api/research/tasks') return json({jobs:[]});
    if (p === '/api/datasets') return json({datasets});
    if (p.startsWith('/api/datasets/')) return json({dataset:datasets.find(item => p.endsWith(item.id))});
    if (p === '/api/graph') {
      entry.documentIds = url.searchParams.getAll('document_id');
      return json({nodes:validDocs.map((doc,i) => ({id:doc.id,document_id:doc.id,title:doc.title,kind:'local_document',fulltext_available:true,pagerank:1/(i+1),rank:i+1})),edges:[],generated_at:timestamp,unresolved_count:0,unresolved:[]});
    }
    report.blockedRequests.push({method,path:p,reason:'unhandled fixture'}); return route.abort();
  });
  const dialog = page.locator('#examplesDialog');
  const scenario = id => dialog.locator('.examples-scenario').filter({has:page.getByRole('heading',{name:catalog.scenarios.find(item => item.id === id).title,exact:true})}).getByRole('button',{name:'准备此任务',exact:true});
  async function open() { for (const close of await page.locator('.notice-stack .app-notice:visible .icon-button').all()) await close.click(); await page.locator('#examplesButton').click(); await dialog.locator('.examples-paper').nth(2).waitFor(); }
  async function closed() { await page.waitForFunction(() => !document.querySelector('#examplesDialog')?.open); }
  async function screenshot(name) {const file=name+'.png';await page.screenshot({path:path.join(output,file),fullPage:true});report.snapshots.push({name,file});}
  try {
    await page.goto(origin); await page.locator('#connectionState.is-connected').waitFor();
    await open();
    const links = await dialog.locator('.examples-paper').evaluateAll(items => items.map(item => ({title:item.querySelector('h4').textContent,state:item.querySelector('.examples-state').textContent,links:[...item.querySelectorAll('a')].map(link => ({href:link.href,rel:link.rel}))})));
    check('real-catalog-three-ready-papers-and-official-versioned-links', links.length === 3 && links.every((item,i) => item.title === catalog.papers[i].title && item.state.startsWith('已就绪') && item.links.some(link => link.href === catalog.papers[i].source_url) && item.links.some(link => link.href === catalog.papers[i].pdf_url) && item.links.every(link => link.rel.includes('noopener'))), {papers:links});
    await screenshot('1440-catalog');
    await page.keyboard.press('Escape'); await closed();
    check('escape-closes-dialog-and-restores-trigger-focus', await page.locator('#examplesButton').evaluate(el => document.activeElement === el));
    for (const id of ['attention-qa','bert-rag-compare','bert-translate','related-work']) {
      await open(); const item = catalog.scenarios.find(entry => entry.id === id);
      await scenario(id).click(); await closed();
      const form = await page.locator('.rs-task-form').evaluate(el => ({kind:el.querySelector('select').value,prompt:el.querySelector('textarea').value,checked:[...el.querySelectorAll('.rs-document-choice input:checked')].map(input => input.value),selects:[...el.querySelectorAll('select')].map(select => select.value),page:el.querySelector('input[type=number]').value}));
      const ids = item.document_keys.map(key => `fixture-${key}`);
      check('prefill-'+item.kind+'-uses-correct-documents-and-input', form.kind === item.kind && sameIds(form.checked, ids) && form.prompt === (item.question || item.instruction || '') && (item.kind !== 'translate' || (form.selects[1] === ids[0] && form.selects[2] === 'page' && form.selects[3] === 'zh' && form.page === String(item.page))), {form});
    }
    check('preparing-four-task-kinds-never-posts-research-tasks', requestCount('POST','/api/research/tasks') === 0);
    await open(); await dialog.getByRole('button',{name:'查看示例引用图谱',exact:true}).click(); await closed();
    const graphRequests = report.requests.filter(item => item.path === '/api/graph');
    check('graph-only-contains-three-curated-document-ids', graphRequests.length === 1 && sameIds(graphRequests[0].documentIds,validDocs.map(doc => doc.id)) && !graphRequests[0].documentIds.includes(oldAttention.id),{requests:graphRequests});
    await open();
    const chartButton = dialog.getByRole('button',{name:'展示图表',exact:true});
    await chartButton.evaluate(button => {button.click();button.click();}); await closed();
    check('matching-csv-sha-reuses-dataset-and-blocks-double-click', requestCount('POST','/api/datasets') === 0 && requestCount('POST','/charts') === 1);
    const chartRequest = report.requests.find(item => item.method === 'POST' && item.path.endsWith('/charts'));
    check('chart-request-matches-real-csv-columns-and-units', chartRequest.payload.kind === 'bar' && chartRequest.payload.x === 'Model' && sameIds(chartRequest.payload.y,['BLEU_EN_DE']) && chartRequest.payload.normalize === false && chartRequest.payload.units.BLEU_EN_DE === 'BLEU', {payload:chartRequest.payload});
    const downloads = await page.locator('.rs-chart-output a[download]').evaluateAll(items => items.map(item => item.getAttribute('href')));
    check('chart-display-keeps-source-and-downloads', await page.locator('.rs-chart-image').first().isVisible() && ['svg','py','json'].every(format => downloads.includes(`/api/charts/fixture-chart/${format}`)) && await page.locator('.rs-example-source a').getAttribute('href') === catalog.dataset.source_url, {downloads});
    await screenshot('1440-synthetic-chart-response');
    failCharts = 1; await open(); await chartButton.click();
    await dialog.locator('.app-notice.is-error').waitFor();
    check('chart-failure-keeps-dialog-open-with-retry-enabled', await dialog.evaluate(el => el.open) && await chartButton.isEnabled() && (await dialog.locator('.app-notice').textContent()).includes('SYNTHETIC chart failure'));
    await chartButton.click(); await closed();
    check('chart-retry-reuses-existing-dataset', requestCount('POST','/api/datasets') === 0 && requestCount('POST','/charts') === 3);
    datasets = [{...dataset,id:'fixture-decoy',sha256:'0'.repeat(64)}];
    await open(); await chartButton.evaluate(button => {button.click();button.click();}); await closed();
    const uploads = report.requests.filter(item => item.method === 'POST' && item.path === '/api/datasets');
    check('same-filename-different-sha-uploads-real-csv-exactly-once', uploads.length === 1 && uploads[0].uploadContainsCsv && requestCount('POST','/charts') === 4, {uploads});
    for (const condition of ['missing','archived','unparsed','wrong-version']) {
      docs = [...validDocs,oldAttention].filter(doc => condition !== 'missing' || doc.id !== 'fixture-bert').map(doc => doc.id !== 'fixture-bert' ? doc : condition === 'archived' ? {...doc,archived_at:timestamp} : condition === 'unparsed' ? {...doc,available_run:null,current_run:{id:'fixture-pending',status:'failed'}} : condition === 'wrong-version' ? {...doc,filename:'1810.04805v1.pdf',sha256:'5'.repeat(64)} : doc);
      await open();
      const bert = dialog.locator('.examples-paper').filter({has:page.getByRole('heading',{name:catalog.papers[1].title,exact:true})});
      check(condition+'-paper-disables-dependent-actions', await bert.getByRole('button',{name:'阅读第 3 页',exact:true}).isDisabled() && await scenario('bert-rag-compare').isDisabled() && await scenario('bert-translate').isDisabled() && await dialog.getByRole('button',{name:'查看示例引用图谱',exact:true}).isDisabled());
      await page.keyboard.press('Escape'); await closed();
    }
    docs = [...validDocs,oldAttention]; await open();
    docs = docs.map(doc => doc.id === 'fixture-bert' ? {...doc,archived_at:timestamp} : doc);
    await scenario('bert-rag-compare').click(); await dialog.locator('.app-notice.is-error').waitFor();
    check('readiness-is-rechecked-after-dialog-was-opened', await dialog.evaluate(el => el.open) && (await dialog.locator('.app-notice').textContent()).includes('BERT') && requestCount('POST','/api/research/tasks') === 0);
    await page.keyboard.press('Escape'); await closed(); docs = [...validDocs,oldAttention];
    failCatalog = 1; await page.locator('#examplesButton').click(); await dialog.getByRole('button',{name:'重新加载',exact:true}).waitFor();
    check('catalog-fetch-failure-offers-retry', await dialog.locator('.app-notice.is-error').isVisible());
    await dialog.getByRole('button',{name:'重新加载',exact:true}).click(); await dialog.locator('.examples-paper').nth(2).waitFor();
    check('catalog-retry-restores-three-paper-cards', await dialog.locator('.examples-paper').count() === 3);
    for (const width of [390,320]) {
      await page.setViewportSize({width,height:844});
      await page.evaluate(() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve))));
      const bounds = await dialog.evaluate(el => {const r=el.getBoundingClientRect();return {left:r.left,right:r.right,top:r.top,bottom:r.bottom,width:innerWidth,height:innerHeight,scrollWidth:el.scrollWidth,clientWidth:el.clientWidth,bodyWidth:document.body.scrollWidth};});
      check('dialog-'+width+'-has-no-horizontal-overflow', bounds.left >= -1 && bounds.right <= width+1 && bounds.top >= -1 && bounds.bottom <= bounds.height+1 && bounds.scrollWidth <= bounds.clientWidth+1 && bounds.bodyWidth <= width+1, {bounds});
      await screenshot(width+'-catalog');
    }
    await page.keyboard.press('Escape'); await closed();
    check('all-network-is-local-fixture-and-no-model-or-other-write-occurs', report.blockedRequests.length === 0 && requestCount('POST','/api/research/tasks') === 0 && report.requests.filter(item => item.method !== 'GET').every(item => item.fixtureWrite), {blocked:report.blockedRequests});
    check('no-javascript-errors-and-only-injected-http-failures', report.pageErrors.length === 0 && report.consoleErrors.length === report.expectedFailures.length && report.consoleErrors.every(message => message.includes('503')), {pageErrors:report.pageErrors,consoleErrors:report.consoleErrors,expectedFailures:report.expectedFailures});
    report.passed = true;
  } catch (error) {report.passed=false;report.failure=String(error.stack || error);process.exitCode=1;await screenshot('failure').catch(() => {});}
  finally {
    report.finishedAt=new Date().toISOString();report.summary={checks:report.checks.length,passedChecks:report.checks.filter(item => item.passed).length,failed:report.checks.filter(item => !item.passed).map(item => item.name),screenshots:report.snapshots.length};
    fs.writeFileSync(path.join(output,'report.json'),JSON.stringify(report,null,2),{flag:'wx'});
    await browser.close(); console.log(JSON.stringify({output,passed:report.passed,...report.summary,failure:report.failure}));
  }
}
main().catch(error => {console.error(error);process.exitCode=1;});
