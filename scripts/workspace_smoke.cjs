/* Browser integration checks for an isolated LOCAL test service.
 * Requires existing Playwright and Edge; installs nothing and calls no model.
 * Usage: node scripts/workspace_smoke.cjs [--base-url http://127.0.0.1:8766]
 * Env: BASE_URL, PLAYWRIGHT_MODULE, BROWSER_EXECUTABLE, SMOKE_TIMEOUT_MS.
 * Creates clearly named test accounts/PDF/CSV inside that service's data store.
 * Every run saves new evidence under artifacts/browser/workspace-<unique-id>.
 */
'use strict';
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const crypto = require('node:crypto');
const { execFileSync } = require('node:child_process');
const { chromium } = require(process.env.PLAYWRIGHT_MODULE ||
  'C:/Users/g2806/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright');

function fixturePdf(marker) {
  // Deliberately synthetic test input, never an academic source or deliverable.
  const stream = `BT /F1 14 Tf 50 740 Td (SMOKE TEST FIXTURE - NOT A RESEARCH PAPER) Tj 0 -30 Td (${marker}) Tj ET\n`;
  const objects = ['<< /Type /Catalog /Pages 2 0 R >>', '<< /Type /Pages /Kids [3 0 R] /Count 1 >>',
    '<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>',
    '<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>', `<< /Length ${Buffer.byteLength(stream)} >>\nstream\n${stream}endstream`];
  let result = '%PDF-1.4\n';
  const offsets = [0];
  objects.forEach((object, index) => { offsets.push(Buffer.byteLength(result)); result += `${index + 1} 0 obj\n${object}\nendobj\n`; });
  const start = Buffer.byteLength(result);
  result += `xref\n0 6\n0000000000 65535 f \n${offsets.slice(1).map(value => `${String(value).padStart(10, '0')} 00000 n \n`).join('')}trailer\n<< /Size 6 /Root 1 0 R >>\nstartxref\n${start}\n%%EOF\n`;
  return Buffer.from(result);
}

function sourceSnapshot() {
  const root = path.resolve(__dirname, '..');
  const result = { gitHead: null, dirty: null, sha256: {} };
  try {
    result.gitHead = execFileSync('git', ['-C', root, 'rev-parse', 'HEAD'], { encoding: 'utf8', windowsHide: true }).trim();
    result.dirty = Boolean(execFileSync('git', ['-C', root, 'status', '--porcelain'], { encoding: 'utf8', windowsHide: true }).trim());
  } catch { /* A source checkout may be exported without Git metadata. */ }
  const files = ['scripts/workspace_smoke.cjs'];
  for (const directory of ['research_workspace', 'research_workspace/static']) {
    for (const file of fs.readdirSync(path.join(root, directory))) {
      if (/\.(py|js|css|html)$/.test(file)) files.push(`${directory}/${file}`);
    }
  }
  for (const file of files) result.sha256[file] = crypto.createHash('sha256').update(fs.readFileSync(path.join(root, file))).digest('hex');
  return result;
}

async function main() {
  const args = process.argv.slice(2);
  assert.ok(!args.length || (args.length === 2 && args[0] === '--base-url'), 'Use --base-url URL or BASE_URL.');
  const base = (args[1] || process.env.BASE_URL || 'http://127.0.0.1:8766').replace(/\/$/, '');
  const baseUrl = new URL(base);
  assert.ok(['127.0.0.1', 'localhost', '[::1]'].includes(baseUrl.hostname), 'This write-capable smoke check only targets a local test service.');
  const nonce = `${Date.now().toString(36)}_${crypto.randomBytes(4).toString('hex')}`;
  const output = path.resolve('artifacts/browser', `workspace-${new Date().toISOString().replace(/[:.]/g, '-')}-${nonce}`);
  fs.mkdirSync(output, { recursive: true });
  const save = (name, content) => fs.writeFileSync(path.join(output, name), content, { flag: 'wx' });
  const report = { base, output, startedAt: new Date().toISOString(), source: sourceSnapshot(), checks: [], consoleErrors: [], pageErrors: [], httpErrors: [], requestFailures: [], unexpectedRequests: [], artifacts: [], limitations: [
    'Uses the already parsed public PDFs present in the isolated service; does not treat them as official competition materials.',
    'Test accounts and synthetic CSV/PDF persist in the isolated test store. Passwords and cookies are not exported.',
    'No model configuration is saved, no research task is submitted, and no external discovery search is triggered.',
    'Browser integration coverage does not establish OCR accuracy, translation quality, RAG grounding, or public deployment security.'
  ] };
  const browser = await chromium.launch({ headless: true, executablePath: process.env.BROWSER_EXECUTABLE || 'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe' });
  const context = await browser.newContext({ viewport: { width: 1440, height: 1000 }, reducedMotion: 'reduce', acceptDownloads: true });
  const page = await context.newPage();
  const evidencePageRequests = [];
  page.setDefaultTimeout(Number(process.env.SMOKE_TIMEOUT_MS || 25000));
  page.on('pageerror', error => report.pageErrors.push(String(error)));
  page.on('console', message => { if (message.type() === 'error') report.consoleErrors.push(message.text()); });
  page.on('requestfailed', request => report.requestFailures.push({ method: request.method(), url: request.url(), error: request.failure()?.errorText }));
  page.on('response', response => {
    if (response.status() >= 400) report.httpErrors.push({ method: response.request().method(), url: response.url(), status: response.status() });
  });
  page.on('request', request => {
    const url = new URL(request.url());
    if (/^\/api\/documents\/[^/]+\/pages\/\d+$/.test(url.pathname)) evidencePageRequests.push({ path: url.pathname, runId: url.searchParams.get('run_id') });
    if ((url.protocol === 'http:' || url.protocol === 'https:') && url.origin !== baseUrl.origin) report.unexpectedRequests.push({ method: request.method(), url: url.origin + url.pathname });
    if (request.method() === 'POST' && ['/api/model', '/api/research/tasks'].includes(url.pathname)) report.unexpectedRequests.push({ method: 'POST', url: url.pathname });
  });
  const check = (name, details = {}) => { report.checks.push({ name, ...details }); console.log(`PASS ${name}`); };
  async function get(route) {
    const response = await context.request.get(base + route);
    assert.equal(response.status(), 200, `${route} should be readable`);
    return response.json();
  }
  async function screenshot(name) {
    const filename = `${name}.png`;
    await page.screenshot({ path: path.join(output, filename), fullPage: true });
    report.artifacts.push(filename);
  }
  async function ready() {
    await page.locator('#connectionState.is-connected').waitFor();
    await page.waitForFunction(() => document.querySelector('#documentList')?.getAttribute('aria-busy') === 'false');
  }
  async function module(name, selector) {
    await page.locator(`.module-navigation [data-module="${name}"]`).click();
    await page.locator(selector).waitFor();
    assert.equal(await page.locator(`.module-navigation [data-module="${name}"]`).getAttribute('aria-pressed'), 'true');
    assert.equal(await page.locator('.module-error').count(), 0);
  }
  async function login(credentials, action) {
    await page.locator('#accountButton').click();
    await page.locator('#accountForm:visible').waitFor();
    await page.locator('#accountForm input[name="name"]').fill(credentials.name);
    await page.locator('#accountForm input[name="password"]').fill(credentials.password);
    const response = page.waitForResponse(r => new URL(r.url()).pathname === `/api/account/${action}` && r.request().method() === 'POST');
    const navigation = page.waitForEvent('load');
    await page.locator(`#accountForm button[value="${action}"]`).click();
    assert.ok((await response).ok(), `${action} succeeds`);
    await navigation;
    await ready();
    await page.waitForFunction(name => document.querySelector('#accountButton').textContent === name, credentials.name);
    assert.equal((await get('/api/account')).user.name, credentials.name);
  }
  async function logout() {
    await page.locator('#accountButton').click();
    await page.locator('#logoutButton:visible').waitFor();
    const navigation = page.waitForEvent('load');
    await page.locator('#logoutButton').click();
    await navigation;
    await ready();
    assert.equal((await get('/api/account')).user.mode, 'local');
  }
  async function manageDocument(id, tab = '资料与版本') {
    await module('manage', '.rm-shell');
    const picker = page.getByRole('combobox', { name: '选择文献，含已归档文献' });
    await picker.locator(`option[value="${id}"]`).waitFor({ state: 'attached' });
    await picker.selectOption(id);
    await page.waitForFunction(() => !document.querySelector('.rm-version-body')?.textContent.includes('正在检查版本关系'));
    await page.getByRole('tab', { name: tab, exact: true }).click();
  }
  async function assertEvidence(evidence) {
    await page.locator('#evidenceHighlight:visible').waitFor();
    assert.equal(Number(await page.locator('#pageNumber').inputValue()), evidence.page);
    assert.ok((await page.locator('#pageImage').getAttribute('src')).includes(evidence.document_id));
    const box = await page.locator('#evidenceHighlight').boundingBox();
    assert.ok(box.width > 0 && box.height > 0);
    const percentages = await page.locator('#evidenceHighlight').evaluate(element => ['left', 'top', 'width', 'height'].map(name => Number.parseFloat(element.style[name])));
    const expected = [evidence.bbox[0], evidence.bbox[1], evidence.bbox[2] - evidence.bbox[0], evidence.bbox[3] - evidence.bbox[1]].map(value => value * 100);
    // CSSOM serializes percentages with reduced precision; 0.001 percentage
    // point is below one rendered pixel even for a 10,000px-wide page.
    percentages.forEach((value, index) => assert.ok(Math.abs(value - expected[index]) < 0.001,
      `PDF highlight component ${index}: expected ${expected[index]}%, observed ${value}%.`));
    const request = evidencePageRequests.filter(item => item.path === `/api/documents/${evidence.document_id}/pages/${evidence.page}`).at(-1);
    assert.equal(request?.runId, evidence.run_id, 'Reader requests the exact processing run associated with the evidence.');
  }

  try {
    await page.goto(base);
    await ready();
    const health = await get('/api/health');
    assert.equal(health.status, 'ok');
    assert.equal(health.activity_worker_running, true, 'Account PDF parsing requires the activity worker.');
    const baseline = (await get('/api/documents')).documents;
    const parsed = baseline.filter(doc => doc.available_run);
    assert.ok(parsed.length >= 2, 'Preload at least two parsed public paper versions into the isolated service.');
    report.preexistingDocuments = parsed.map(doc => ({ id: doc.id, filename: doc.filename, run_id: doc.available_run.id }));
    await screenshot('01-reader');
    check('Reader and background services mount with real parsed papers', { count: parsed.length });

    await page.locator('#modelSettingsButton').click();
    const modal = page.getByRole('dialog', { name: '连接 DeepSeek', exact: true });
    await modal.waitFor();
    await page.waitForFunction(() => document.querySelector('.ms-dialog input[type="password"]')?.disabled === false);
    assert.equal((await get('/api/model')).configured, false, 'Use an unconfigured isolated service.');
    assert.equal(await modal.locator('input[type="password"]').inputValue(), '');
    assert.ok((await modal.textContent()).includes('尚未配置'));
    await screenshot('02-model-unconfigured');
    await modal.locator('input[type="password"]').fill('SMOKE_DUMMY_NOT_A_REAL_KEY');
    await modal.getByRole('button', { name: '关闭模型设置并清空密钥输入' }).click();
    await page.locator('#modelSettingsButton').click();
    await modal.waitFor();
    assert.equal(await modal.locator('input[type="password"]').inputValue(), '');
    await page.waitForFunction(() => document.querySelector('.ms-dialog input[type="password"]')?.disabled === false);
    await modal.getByRole('button', { name: '关闭模型设置并清空密钥输入' }).click();
    check('Model dialog starts without a key and clears unsaved input on close');

    const doc = parsed[0];
    await manageDocument(doc.id);
    const versions = await get(`/api/documents/${doc.id}/versions`);
    assert.ok(versions.candidates?.length || versions.family?.documents?.length > 1, 'Real paper versions have a candidate or confirmed relationship.');
    const compare = page.locator('.rm-candidate').first().getByRole('button', { name: '比较正文', exact: true });
    if (await compare.count()) await compare.click();
    else await page.locator('.rm-family').getByRole('button', { name: '比较', exact: true }).first().click();
    await page.locator('.rm-change').first().waitFor();
    assert.equal(await page.locator('.rm-change').first().locator('.rm-diff-side').count(), 2);
    assert.ok(await page.locator('.rm-change .rm-link-button').count() > 0);
    await screenshot('03-version-comparison');
    check('Version candidate/family and two-sided real text differences render');

    await page.getByRole('tab', { name: '标题与引用', exact: true }).click();
    const structure = await get(`/api/documents/${doc.id}/structure`);
    assert.ok(structure.headings.length > 0 && structure.references.length > 0 && structure.citations.length > 0);
    assert.equal(await page.locator('.rm-outline-item').count(), structure.headings.length);
    assert.equal(await page.locator('.rm-reference').count(), structure.references.length);
    await page.locator('.rm-citation-context summary').first().click();
    await page.locator('.rm-context-body .rm-original-text').first().waitFor();
    assert.ok((await page.locator('.rm-context-body .rm-original-text').first().textContent()).length > 10);
    await screenshot('04-structure-and-reference-context');
    await page.locator('.rm-outline-item').first().click();
    await assertEvidence(structure.headings[0].evidence);
    await screenshot('05-heading-original-evidence');
    await manageDocument(doc.id, '标题与引用');
    const reverse = structure.citations.find(item => item.reference_ids?.length);
    const reference = structure.references.find(item => item.id === reverse.reference_ids[0]);
    const reverseIndex = structure.citations.indexOf(reverse);
    await page.locator('.rm-reverse-row').nth(reverseIndex).getByRole('button', { name: `书目 [${reference.label}] ↗`, exact: true }).click();
    await assertEvidence(reference.evidence);
    check('Heading and reverse citation links open the matching PDF page and highlight', { headings: structure.headings.length, references: structure.references.length, citations: structure.citations.length });

    await manageDocument(doc.id, '本页文字');
    await page.locator('.rm-block-text').first().waitFor();
    await page.locator('.rm-block-text').first().evaluate(element => {
      const range = document.createRange(); range.setStart(element.firstChild, 0); range.setEnd(element.firstChild, Math.min(12, element.firstChild.textContent.length));
      const selection = window.getSelection(); selection.removeAllRanges(); selection.addRange(range);
      document.dispatchEvent(new Event('selectionchange'));
    });
    await page.getByRole('button', { name: '翻译所选文字', exact: true }).waitFor();
    assert.ok((await page.locator('.rm-selection-hint').textContent()).includes('已选择'));
    await screenshot('06-selection-entry-only');
    await page.evaluate(() => window.getSelection().removeAllRanges());
    check('Within-block text selection enables a partial translation entry without submitting a task');

    await module('research', '.rs-task-form');
    await page.waitForFunction(() => !document.querySelector('.rs-configuration')?.textContent.includes('正在'));
    assert.equal(await page.getByRole('button', { name: '创建研究任务', exact: true }).isDisabled(), true);
    await screenshot('07-research-unconfigured');
    check('Research workspace mounts with disabled model tasks when unconfigured');
    await module('discovery', '.explore-discovery');
    await screenshot('08-discovery');
    check('Discovery workspace mounts without an external search');

    await module('graph', '.explore-graph');
    await page.locator('.ex-network-svg').waitFor();
    const graph = await get('/api/graph');
    const locals = graph.nodes.filter(node => node.kind === 'local_document');
    assert.ok(locals.length >= 2);
    assert.ok(locals.every(node => baseline.some(item => item.id === (node.document_id || node.id))));
    assert.equal(await page.locator('.ex-network-node:not(.is-external)').count(), locals.length);
    await page.locator('.ex-network-node:not(.is-external)').first().click();
    await page.locator('.ex-graph-selection h3').waitFor();
    assert.ok(locals.some(node => node.title === undefined) === false);
    assert.ok(locals.map(node => node.title).includes(await page.locator('.ex-graph-selection h3').textContent()));
    await screenshot('09-real-local-citation-graph');
    check('Graph nodes correspond to actual imported papers', { localNodes: locals.length, edges: graph.edges.length, unresolved: graph.unresolved_count });
    await module('data', '.rs-upload-form');
    await screenshot('10-data-initial');
    check('All six navigation modules mount');

    const accountA = { name: `smoke_a_${nonce}`, password: `Smoke-${crypto.randomBytes(20).toString('hex')}` };
    const accountB = { name: `smoke_b_${nonce}`, password: `Smoke-${crypto.randomBytes(20).toString('hex')}` };
    report.testResources = { accountNames: [accountA.name, accountB.name], note: 'Names are generated test accounts; a failure may occur before both registrations.' };
    await login(accountA, 'register');
    assert.equal((await get('/api/documents')).documents.length, 0);
    assert.equal((await get('/api/datasets')).datasets.length, 0);
    const pdfName = `SMOKE-TEST-FIXTURE-${nonce}.pdf`;
    save(pdfName, fixturePdf(`ISOLATION MARKER ${nonce}`));
    const uploaded = page.waitForResponse(r => new URL(r.url()).pathname === '/api/documents' && r.request().method() === 'POST');
    await page.locator('#fileInput').setInputFiles(path.join(output, pdfName));
    const uploadedResponse = await uploaded;
    assert.equal(uploadedResponse.status(), 202);
    const accountDoc = (await uploadedResponse.json()).document;
    report.testResources.documentId = accountDoc.id;
    await page.waitForFunction(() => document.querySelectorAll('.document-state.is-completed').length === 1, null, { timeout: 45000 });
    assert.equal((await get(`/api/documents/${accountDoc.id}`)).document.filename, pdfName);
    await screenshot('11-account-a-own-document');
    check('New account starts empty and parses its own explicitly synthetic test PDF');

    await module('data', '.rs-upload-form');
    const csvName = `SMOKE-TEST-FIXTURE-${nonce}.csv`;
    save(csvName, 'trial,score_a,score_b\n1,0.72,0.68\n2,0.78,0.73\n3,0.83,0.77\n');
    await page.getByLabel('选择CSV文件', { exact: true }).setInputFiles(path.join(output, csvName));
    const datasetResponse = page.waitForResponse(r => new URL(r.url()).pathname === '/api/datasets' && r.request().method() === 'POST');
    await page.getByRole('button', { name: '导入CSV', exact: true }).click();
    const datasetResult = await datasetResponse;
    assert.equal(datasetResult.status(), 201);
    const dataset = (await datasetResult.json()).dataset;
    report.testResources.datasetId = dataset.id;
    await page.locator('.rs-table tbody tr').nth(2).waitFor();
    assert.equal(await page.locator('.rs-table tbody tr').count(), 3);
    await page.getByLabel(/^横轴字段/).selectOption('trial');
    await page.locator('.rs-y-choices input[value="score_a"]').check();
    await page.locator('.rs-y-choices input[value="score_b"]').check();
    const chartIds = [];
    report.testResources.chartIds = chartIds;
    for (const kind of ['line', 'bar']) {
      await page.getByLabel(/^图表类型/).selectOption(kind);
      await page.getByLabel('图表标题', { exact: true }).fill(`SMOKE TEST DATA — ${kind}`);
      const chartResponse = page.waitForResponse(r => new URL(r.url()).pathname === `/api/datasets/${dataset.id}/charts` && r.request().method() === 'POST');
      await page.getByRole('button', { name: '生成图表与图注', exact: true }).click();
      const response = await chartResponse;
      assert.equal(response.status(), 200);
      const chart = (await response.json()).chart;
      chartIds.push(chart.id);
      assert.equal(chart.spec.kind, kind);
      assert.equal(chart.dataset_id, dataset.id);
      assert.deepEqual(chart.spec.series.map(series => series.values), [[0.72, 0.78, 0.83], [0.68, 0.73, 0.77]]);
      assert.deepEqual(chart.transformations.dropped_csv_rows, []);
      await page.waitForFunction(id => document.querySelector('.rs-chart-output a')?.href.includes(id) && document.querySelector('.rs-chart-image')?.naturalWidth > 0, chart.id);
      assert.ok((await page.locator('.rs-caption').textContent()).length > 15);
      // The module is an internal scroll container; fullPage alone would keep
      // the form in view and omit the chart below it.
      await page.locator('.rs-chart-output').evaluate(element => element.scrollIntoView({ block: 'start', behavior: 'instant' }));
      await screenshot(`12-chart-${kind}`);
      for (const [format, label] of [['svg', '下载SVG'], ['py', '下载Python复现脚本'], ['json', '下载数据与转换记录']]) {
        const downloadPromise = page.waitForEvent('download');
        await page.getByRole('link', { name: label, exact: true }).click();
        const download = await downloadPromise;
        const filename = `actual-${kind}.${format}`;
        assert.equal(await download.failure(), null);
        await download.saveAs(path.join(output, filename));
        const content = fs.readFileSync(path.join(output, filename), 'utf8');
        if (format === 'svg') assert.ok(content.includes('<svg'));
        if (format === 'py') assert.ok(content.includes('chart.svg') && content.includes('0.72'));
        if (format === 'json') assert.equal(JSON.parse(content).id, chart.id);
        report.artifacts.push(filename);
      }
      check(`Actual CSV produces ${kind} chart, caption, SVG/script/data downloads`, { datasetId: dataset.id, chartId: chart.id });
    }
    await logout();
    assert.deepEqual((await get('/api/documents')).documents.map(item => item.id).sort(), baseline.map(item => item.id).sort());
    await login(accountB, 'register');
    assert.equal((await get('/api/documents')).documents.length, 0);
    assert.equal((await get('/api/datasets')).datasets.length, 0);
    assert.equal((await get('/api/research/tasks')).jobs.length, 0);
    for (const resource of [`/api/documents/${accountDoc.id}`, `/api/datasets/${dataset.id}`, `/api/charts/${chartIds[0]}/json`]) {
      assert.equal((await context.request.get(base + resource)).status(), 404, 'Another account cannot retrieve private resources by ID.');
    }
    await screenshot('13-account-b-isolated');
    await logout();
    await login(accountA, 'login');
    assert.deepEqual((await get('/api/documents')).documents.map(item => item.id), [accountDoc.id]);
    assert.deepEqual((await get('/api/datasets')).datasets.map(item => item.id), [dataset.id]);
    await screenshot('14-account-a-login-persisted');
    check('Two registered accounts isolate documents/datasets/charts; relogin preserves account A data');
    await logout();

    assert.deepEqual(report.unexpectedRequests, [], 'No browser request to external origins, model configuration writes, or model task submission.');
    assert.deepEqual(report.pageErrors, [], 'No uncaught browser exceptions.');
    assert.deepEqual(report.consoleErrors, [], 'No browser console errors.');
    assert.deepEqual(report.httpErrors, [], 'No browser HTTP errors; deliberate cross-account 404 probes use the separate API request client.');
    const materialFailures = report.requestFailures.filter(item => item.error !== 'net::ERR_ABORTED');
    assert.deepEqual(materialFailures, [], 'No unexpected failed browser requests.');
    check('Browser console and runtime are clean; no model task was invoked');
    report.passed = true;
  } catch (error) {
    report.passed = false;
    report.failure = { message: error.message, stack: error.stack };
    // Clear secret inputs before preserving failure screenshots.
    await page.locator('input[type="password"]').evaluateAll(inputs => inputs.forEach(input => { input.value = ''; })).catch(() => {});
    await screenshot('failure').catch(() => {});
    process.exitCode = 1;
  } finally {
    report.finishedAt = new Date().toISOString();
    save('report.json', JSON.stringify(report, null, 2));
    await browser.close();
    console.log(JSON.stringify({ passed: report.passed, output, checks: report.checks.length, consoleErrors: report.consoleErrors.length, pageErrors: report.pageErrors.length, failure: report.failure?.message }, null, 2));
  }
}
main().catch(error => { console.error(error.message); process.exitCode = 1; });
