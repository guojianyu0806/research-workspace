// Uses an existing Playwright installation and browser; does not install either.
// Usage: node scripts/browser_smoke.cjs path/to/first.pdf path/to/second.pdf
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { chromium } = require(process.env.PLAYWRIGHT_MODULE || 'playwright');

async function main() {
  const files = process.argv.slice(2).map(file => path.resolve(file));
  assert.equal(files.length, 2, 'Provide Attention Is All You Need v7 and v1 PDFs.');
  const output = path.resolve('artifacts/browser', new Date().toISOString().replace(/[:.]/g, '-'));
  fs.mkdirSync(output, { recursive: true });
  const browser = await chromium.launch({ headless: true, executablePath: process.env.BROWSER_EXECUTABLE || undefined });
  const context = await browser.newContext({ viewport: { width: 1440, height: 1000 }, reducedMotion: 'reduce' });
  const page = await context.newPage();
  const report = { output, checks: [], consoleErrors: [], pageErrors: [] };
  page.on('pageerror', error => report.pageErrors.push(String(error)));
  page.on('console', message => { if (message.type() === 'error') report.consoleErrors.push(message.text()); });
  const base = process.env.SMOKE_URL || 'http://127.0.0.1:8765';
  async function screenshot(name) { await page.screenshot({ path: path.join(output, `${name}.png`), fullPage: true }); }
  async function search(query) {
    await page.locator('#searchQuery').fill(query);
    const response = page.waitForResponse(r => r.url().includes('/api/search?') && r.status() === 200);
    await page.locator('#searchButton').click();
    const result = await (await response).json();
    await page.waitForFunction(() => document.querySelector('#searchResults').getAttribute('aria-busy') === 'false');
    return result;
  }
  try {
    await page.goto(base);
    await page.locator('#connectionState.is-connected').waitFor();
    assert.equal((await (await context.request.get(`${base}/api/health`)).json()).worker_running, true);
    await screenshot('01-initial');
    report.checks.push('Service and background worker are available; initial workspace renders.');

    await page.locator('#fileInput').setInputFiles(files);
    await page.waitForFunction(() => document.querySelector('#uploadStatus').hidden && document.querySelectorAll('.document-row').length >= 2);
    await page.waitForFunction(() => document.querySelectorAll('.document-state.is-completed').length >= 2, null, { timeout: 30000 });
    await page.locator('#paperPage:visible').waitFor();
    const count = await page.locator('.document-row').count();
    report.checks.push('Batch PDF upload, persistent parsing and original page display work.');

    await page.locator('#fileInput').setInputFiles(files[0]);
    await page.waitForFunction(() => document.querySelector('#uploadStatus').hidden && document.querySelector('#noticeText').textContent.includes('已在文献库'));
    assert.equal(await page.locator('.document-row').count(), count);
    report.checks.push('Exact duplicate upload preserves the existing document.');
    await page.locator('#dismissNotice').click();

    const results = await search('dominant sequence');
    assert.ok(new Set(results.hits.map(hit => hit.document_id)).size >= 2, 'Search spans both documents.');
    const first = results.hits[0];
    await page.locator('.hit-card').first().click();
    await page.locator('#evidenceHighlight:visible').waitFor();
    assert.equal(Number(await page.locator('#pageNumber').inputValue()), first.page);
    const image = await page.locator('#pageImage').getAttribute('src');
    assert.ok(image.includes(first.document_id));
    const box = await page.locator('#evidenceHighlight').boundingBox();
    assert.ok(box.width > 0 && box.height > 0, 'Evidence rectangle has real dimensions.');
    await screenshot('02-evidence-desktop');
    report.checks.push('Cross-document keyword search opens the matching page and highlighted evidence region.');

    await page.locator('#searchScope').selectOption('current');
    const scoped = await search('attention');
    assert.ok(scoped.hits.length > 0 && scoped.hits.every(hit => hit.document_id === first.document_id));
    await search('no_such_evidence_8fd917c');
    assert.ok((await page.locator('#searchResults').textContent()).includes('未找到匹配片段'));
    report.checks.push('Current-document scope and empty search results are accurate.');

    await page.locator('#pageNumber').fill('15');
    await page.locator('#pageNumber').press('Enter');
    await page.waitForFunction(() => !document.querySelector('#paperPage').hidden && document.querySelector('#pageImage').src.includes('/pages/15/image'));
    const oldDocument = (await (await context.request.get(`${base}/api/documents/${first.document_id}`)).json()).document;
    const reparseResponse = page.waitForResponse(r => r.url().endsWith('/parse-runs') && r.request().method() === 'POST');
    await page.locator('#reparseButton').click();
    assert.equal((await reparseResponse).status(), 202);
    await page.waitForFunction(() => document.querySelector('#readerSubtitle').textContent.includes('文本已提取'), null, { timeout: 30000 });
    const newDocument = (await (await context.request.get(`${base}/api/documents/${first.document_id}`)).json()).document;
    assert.notEqual(newDocument.current_run.id, oldDocument.current_run.id);
    const oldPage = await context.request.get(`${base}/api/documents/${first.document_id}/pages/1?run_id=${oldDocument.current_run.id}`);
    assert.equal(oldPage.status(), 200);
    report.checks.push('Tail page navigation and reparse preserve accessible history.');

    await search('attention');
    await page.locator('.hit-card').first().click();
    await page.locator('#evidenceHighlight:visible').waitFor();
    await page.setViewportSize({ width: 390, height: 844 });
    for (const view of ['library', 'evidence', 'reader']) {
      await page.locator(`[data-view="${view}"]`).click();
      assert.equal(await page.locator('#workspace').getAttribute('data-mobile-view'), view);
      assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth), true);
      await screenshot(`03-mobile-${view}`);
    }
    report.checks.push('All three mobile views render at 390px without horizontal page overflow.');
    assert.deepEqual(report.pageErrors, []);
    assert.deepEqual(report.consoleErrors, []);
    report.passed = true;
  } catch (error) {
    report.passed = false;
    report.error = String(error.stack || error);
    await screenshot('failure').catch(() => {});
    throw error;
  } finally {
    fs.writeFileSync(path.join(output, 'results.json'), JSON.stringify(report, null, 2));
    console.log(JSON.stringify(report, null, 2));
    await browser.close();
  }
}
main().catch(error => { console.error(error); process.exitCode = 1; });
