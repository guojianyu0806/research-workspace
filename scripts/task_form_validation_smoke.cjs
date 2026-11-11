/* Offline Edge regression: inactive translation fields must not block another task. */
'use strict';

const assert = require('node:assert/strict');
const path = require('node:path');
const {chromium} = require(process.env.PLAYWRIGHT_MODULE || 'C:/Users/g2806/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright');
const staticDir = path.join(__dirname, '..', 'research_workspace', 'static');

async function main() {
  const browser = await chromium.launch({headless: true, executablePath: process.env.BROWSER_EXECUTABLE || 'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe'});
  try {
    const page = await browser.newPage({viewport: {width: 1360, height: 880}});
    page.setDefaultTimeout(5000);
    const errors = [], network = [];
    page.on('pageerror', error => errors.push(error.message));
    await page.route('**/*', route => { network.push(route.request().url()); return route.abort(); });
    await page.setContent('<!doctype html><html lang="zh-CN"><main id="fixture"></main></html>');
    await page.addStyleTag({path: path.join(staticDir, 'research.css')});
    for (const file of ['notices.js', 'research.js']) await page.addScriptTag({path: path.join(staticDir, file)});
    await page.evaluate(async () => {
      window.fixturePosts = [];
      const api = async (url, init) => {
        if (url === '/api/capabilities') return {intelligence: {configured: true}};
        if (url === '/api/documents') return {documents: [
          {id: 'long', title: 'SYNTHETIC long document', page_count: 100, available_run: {id: 'run-long'}},
          {id: 'short', title: 'SYNTHETIC short document', page_count: 2, available_run: {id: 'run-short'}},
        ]};
        if (url === '/api/research/tasks' && init.method === 'POST') {
          const payload = JSON.parse(init.body);
          window.fixturePosts.push(payload);
          const timestamp = new Date().toISOString();
          return {job: {id: `fixture-${window.fixturePosts.length}`, kind: payload.kind, payload,
            status: 'completed', progress: 0, total: 0, created_at: timestamp, updated_at: timestamp,
            events: [], result: null}};
        }
        if (url === '/api/research/tasks') return {jobs: []};
        throw new Error(`Unexpected synthetic request: ${url}`);
      };
      window.fixtureController = ResearchTasks.mount(document.getElementById('fixture'), {api});
      await window.fixtureController.ready;
    });

    const kind = page.getByRole('combobox', {name: '任务类型', exact: true});
    const scope = page.getByRole('combobox', {name: '翻译范围', exact: true});
    const document = page.getByRole('combobox', {name: '待翻译文献', exact: true});
    const pageNumber = page.locator('.rs-task-form input[type=number]');
    const submit = page.getByRole('button', {name: '创建研究任务', exact: true});
    const posts = () => page.evaluate(() => window.fixturePosts);
    async function create(expectedCount) {
      await submit.click();
      await page.waitForFunction(count => window.fixturePosts.length === count, expectedCount);
      await page.waitForFunction(() => !document.querySelector('.rs-task-form button[type=submit]').disabled);
    }

    assert.ok(await pageNumber.isDisabled());
    await kind.selectOption('translate');
    assert.ok(await pageNumber.isDisabled());
    await scope.selectOption('page');
    assert.ok(await pageNumber.isEnabled());
    assert.equal(await pageNumber.getAttribute('required'), '');
    await pageNumber.fill('99');
    await document.selectOption('short');
    assert.equal(await pageNumber.inputValue(), '2');
    assert.match(await page.locator('.notice-text').last().textContent(), /调整为第 2 页（共 2 页）/);
    await kind.selectOption('qa');
    assert.ok(await pageNumber.isDisabled());
    await page.getByRole('textbox', {name: '研究问题', exact: true}).fill('What does the short document report?');
    await page.locator('.rs-document-choice input[value="short"]').check();
    await create(1);
    assert.deepEqual((await posts())[0], {kind: 'qa', document_ids: ['short'], question: 'What does the short document report?'});

    await kind.selectOption('translate');
    await create(2);
    assert.deepEqual((await posts())[1], {kind: 'translate', document_id: 'short', target_language: 'zh', run_id: 'run-short', page: 2});
    for (const invalid of ['3', '0', '1.5', '']) {
      await pageNumber.fill(invalid);
      assert.equal(await pageNumber.evaluate(element => element.checkValidity()), false);
      await submit.click();
      assert.equal((await posts()).length, 2, `active page validation must reject ${JSON.stringify(invalid)}`);
    }
    await pageNumber.fill('99');
    await scope.selectOption('document');
    assert.ok(await pageNumber.isDisabled());
    await create(3);
    assert.deepEqual((await posts())[2], {kind: 'translate', document_id: 'short', target_language: 'zh', run_id: 'run-short'});
    await scope.selectOption('page');
    assert.ok(await pageNumber.isEnabled());
    assert.equal(await pageNumber.inputValue(), '2');

    // Also prove disabled input validity, independently of document-change correction.
    await pageNumber.fill('3');
    await kind.selectOption('qa');
    assert.equal(await pageNumber.inputValue(), '3');
    assert.ok(await pageNumber.isDisabled());
    await create(4);
    assert.equal((await posts())[3].kind, 'qa');
    await page.getByRole('textbox', {name: '研究问题', exact: true}).fill('');
    await submit.click();
    assert.equal((await posts()).length, 4, 'required QA input still blocks submission');
    for (const taskKind of ['review', 'translate_text', 'draft']) {
      await kind.selectOption(taskKind);
      assert.ok(await pageNumber.isDisabled(), taskKind);
    }
    assert.deepEqual(errors, []);
    assert.deepEqual(network, []);
    console.log('PASS offline Edge task form: document page correction, QA submission, scope switching, active validation; 4 synthetic submissions, 0 network requests');
  } finally {
    await browser.close();
  }
}

main().catch(error => { console.error(error); process.exitCode = 1; });
