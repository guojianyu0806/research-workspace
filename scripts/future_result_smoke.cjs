/* Offline DOM smoke test of the real Future Work result component.
 * All result data below are SYNTHETIC TEST FIXTURES, not research conclusions.
 * Uses existing Playwright/Edge. No service, database, model, or network calls.
 * Usage: node scripts/future_result_smoke.cjs
 */
'use strict';
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const crypto = require('node:crypto');
const { chromium } = require(process.env.PLAYWRIGHT_MODULE ||
  'C:/Users/g2806/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright');

const SYNTHETIC = 'SYNTHETIC TEST FIXTURE — NOT A RESEARCH CONCLUSION';
const ATTACK = '<img src="https://invalid.example/smoke.png" onerror="window.__futureFixtureExecuted=true">';
const QUOTE = 'Synthetic abstract sentence for UI testing only. ' + ATTACK;
const RETRIEVED = '2026-10-07T10:00:00+00:00';

function fixture() {
  const sources = [
    { source_id: 'arxiv:2601.00001v1', title: 'SYNTHETIC arXiv-shaped test metadata', url: 'https://arxiv.org/abs/2601.00001v1', source: 'arxiv',
      published: '2026-01-02T00:00:00Z', retrieved_at: RETRIEVED, abstract: QUOTE, full_text_reviewed: false },
    { source_id: 'arxiv:2601.00002v1', title: 'SYNTHETIC malicious hostname', url: 'https://arxiv.org.invalid.example/abs/2601.00002v1', source: 'arxiv',
      published: null, retrieved_at: RETRIEVED, abstract: 'Synthetic lookalike source; must not become a clickable link.', full_text_reviewed: false },
    { source_id: 'arxiv:2601.00003v1', title: 'SYNTHETIC malicious scheme', url: 'javascript:window.__futureFixtureExecuted=true', source: 'arxiv',
      published: null, retrieved_at: RETRIEVED, abstract: 'Synthetic script scheme; must not become a clickable link.', full_text_reviewed: false },
  ];
  const citations = sources.map(source => ({ source_id: source.source_id, quote: source.abstract, url: source.url, published: source.published }));
  // A citation's untrusted URL must never override the canonical source record.
  citations[0].url = 'https://invalid.example/model-invented-link';
  const retained = { direction_id: 'fixture-retained', section_index: 0, heading: 'SYNTHETIC retained direction ' + ATTACK,
    proposed_work: 'Synthetic proposal for DOM checks only.', status: 'not_found_in_search', coverage: 'related_only',
    citations: [citations[0]], reason: '本合成测试方向保留；有限检索不能证明研究空白。', excluded: false,
    requires_full_text_confirmation: true, query_ids: ['fixture-query'] };
  const excluded = { direction_id: 'fixture-excluded', section_index: 1, heading: 'SYNTHETIC temporarily excluded direction',
    proposed_work: 'Synthetic proposal for DOM checks only.', status: 'potentially_addressed', coverage: 'explicit_direction_coverage',
    citations, reason: '本合成测试方向暂移出，仍须全文确认。', excluded: true,
    requires_full_text_confirmation: true, query_ids: ['fixture-query'] };
  const audit = { status: 'completed', scope: 'bounded_arxiv_abstracts_only', prompt_version: 'future-verification-v1',
    started_at: '2026-10-08T10:00:00Z', completed_at: '2026-10-08T10:01:00Z', full_text_reviewed: false, novelty_proven: false,
    model_called: false, notice: SYNTHETIC + '；这里的检索与结果均为合成 UI 测试夹具。',
    queries: [{ query_id: 'fixture-query', query: 'SYNTHETIC fixture query', direction_ids: [retained.direction_id, excluded.direction_id],
      field: 'all', limit: 5, sort_by: 'submittedDate', status: 'completed', retrieved_at: RETRIEVED, attempted_at: '2026-10-08T10:00:00Z', cached: true,
      source_ids: sources.map(source => source.source_id) }], sources, directions: [retained, excluded], errors: [] };
  const result = { kind: 'future_work', status: 'generated', content: SYNTHETIC,
    sections: [retained, excluded].map(direction => ({ heading: direction.heading, claims: [{ text: 'Synthetic original synthesis ' + ATTACK, citations: [] }], proposed_work: direction.proposed_work })),
    future_work_verification: 'bounded_abstract_review', future_verification: audit,
    retained_directions: [retained], excluded_directions: [excluded],
    search_time_range: { from: null, to: null, date_filter: null, checked_at: audit.completed_at, note: 'Synthetic fixture uses a cached retrieval timestamp.' },
    limitations: ['SYNTHETIC TEST ONLY. No live retrieval or model was invoked.'], trace: [] };
  return { id: 'synthetic-future-dom-fixture', kind: 'future_work', status: 'completed', created_at: '2026-10-08T10:00:00Z',
    updated_at: '2026-10-08T10:01:00Z', payload: {}, result, progress: 0, total: 0, events: [] };
}

async function main() {
  const root = path.resolve(__dirname, '..');
  const output = path.join(root, 'artifacts/browser', `future-fixture-${new Date().toISOString().replace(/[:.]/g, '-')}-${crypto.randomBytes(4).toString('hex')}`);
  fs.mkdirSync(output, { recursive: true });
  const report = { syntheticFixture: true, notice: SYNTHETIC, output, checks: [], pageErrors: [], consoleErrors: [], networkRequests: [], sourceSha256: {} };
  for (const name of ['notices.js', 'research.js', 'research.css', 'modern-theme.css']) report.sourceSha256[name] = crypto.createHash('sha256').update(fs.readFileSync(path.join(root, 'research_workspace/static', name))).digest('hex');
  const browser = await chromium.launch({ headless: true, executablePath: process.env.BROWSER_EXECUTABLE || 'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe' });
  const context = await browser.newContext({ viewport: { width: 1440, height: 1000 }, reducedMotion: 'reduce' });
  await context.route('**/*', route => { report.networkRequests.push(route.request().url()); return route.abort(); });
  const page = await context.newPage();
  page.on('pageerror', error => report.pageErrors.push(String(error)));
  page.on('console', message => { if (message.type() === 'error') report.consoleErrors.push(message.text()); });
  try {
    await page.setContent('<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><title>SYNTHETIC FUTURE UI TEST</title></head><body><div id="fixture-banner"></div><main id="fixture-root"></main></body></html>');
    await page.locator('#fixture-banner').evaluate((element, text) => { element.textContent = text + ' / 合成界面测试，非真实研究结果'; }, SYNTHETIC);
    await page.addStyleTag({ content: ':root{--ink:#173842;--muted:#657b83;--line:#d3e2e5;--primary:#146269;--paper:#fff;--body:Arial,"Microsoft YaHei",sans-serif}*{box-sizing:border-box}body{margin:0;padding:24px;background:#edf4f5;color:var(--ink);font-family:var(--body)}#fixture-banner{padding:16px;border:2px solid #a55e0d;background:#fff2cf;font-weight:bold;margin-bottom:20px}#fixture-root{max-width:1440px;margin:auto}' });
    await page.addStyleTag({ path: path.join(root, 'research_workspace/static/research.css') });
    await page.addStyleTag({ path: path.join(root, 'research_workspace/static/modern-theme.css') });
    await page.addScriptTag({ path: path.join(root, 'research_workspace/static/notices.js') });
    await page.addScriptTag({ path: path.join(root, 'research_workspace/static/research.js') });
    await page.evaluate(async job => {
      window.__futureFixtureExecuted = false;
      window.__fixtureApiCalls = [];
      window.__mountFutureFixture = async value => {
        window.__futureFixtureController?.destroy();
        const api = async (url, init = {}) => {
          window.__fixtureApiCalls.push({ url, method: init.method || 'GET' });
          if (init.method && init.method !== 'GET') throw new Error('SYNTHETIC smoke forbids mutations.');
          if (url === '/api/capabilities') return { intelligence: { configured: false }, llm: false };
          if (url === '/api/documents') return { documents: [] };
          if (url === '/api/research/tasks') return { jobs: [value] };
          if (url === `/api/research/tasks/${value.id}?include_result=false`) {
            return { job: { ...value, result: null, completed_items: [], items_deferred: true } };
          }
          if (url === `/api/research/tasks/${value.id}`) return { job: value };
          throw new Error(`Unexpected synthetic fixture API path: ${url}`);
        };
        window.__futureFixtureController = window.ResearchTasks.mount(document.getElementById('fixture-root'), { api });
        await window.__futureFixtureController.ready;
      };
      await window.__mountFutureFixture(job);
    }, fixture());
    await page.locator('.rs-future-verification').waitFor();
    report.checks.push('Real ResearchTasks component mounts using an explicitly synthetic, read-only API stub.');
    assert.equal(await page.locator('.rs-future-direction:not(.is-excluded)').count(), 1);
    assert.equal(await page.locator('.rs-future-direction.is-excluded').count(), 1);
    assert.ok((await page.locator('.rs-future-directions').first().textContent()).includes('保留为待验证方向'));
    assert.ok((await page.locator('.rs-future-directions').nth(1).textContent()).includes('暂移出重点候选'));
    assert.equal(await page.locator('.rs-result-section.is-excluded').count(), 1);
    assert.ok((await page.locator('.rs-result-section.is-excluded h4').textContent()).includes('SYNTHETIC temporarily excluded'));
    assert.ok((await page.locator('.rs-result-section.is-excluded .rs-future-excluded-note').textContent()).includes('不作为当前推荐方向'));
    report.checks.push('Retained and temporarily excluded groups remain distinct, and the corresponding original section is visibly marked. These are result groups, not user override controls.');
    const queryText = await page.locator('.rs-future-query').textContent();
    assert.ok(queryText.includes(RETRIEVED) && queryText.includes('使用缓存') && queryText.includes('2026-10-08T10:00:00Z'));
    assert.ok(queryText.includes('SYNTHETIC fixture query'));
    const quotes = await page.locator('.rs-future-quote').allTextContents();
    assert.ok(quotes.includes(QUOTE), 'Original quoted text is shown verbatim as text.');
    report.checks.push('Original abstract quote, query, exact retrieval/attempt timestamps, and cached status are visible.');
    const links = await page.locator('.rs-future-verification a').evaluateAll(elements => elements.map(element => ({ href: element.href, target: element.target, rel: element.rel })));
    assert.equal(links.length, 3, 'The one valid source appears in the query and both direction cards.');
    assert.ok(links.every(item => item.href === 'https://arxiv.org/abs/2601.00001v1' && item.target === '_blank' && item.rel.includes('noopener') && item.rel.includes('noreferrer')));
    assert.equal(await page.locator('.rs-future-verification').getByText(/官方论文链接未通过校验/).count(), 4);
    report.checks.push('Only the canonical matching official arXiv source is linked; lookalike host, javascript URL, and citation-supplied replacement URL cannot become clickable.');
    assert.equal(await page.locator('.rs-result img, .rs-result script, .rs-result [onerror]').count(), 0);
    assert.ok((await page.locator('.rs-result').textContent()).includes(ATTACK), 'Adversarial HTML is displayed as literal text.');
    assert.equal(await page.evaluate(() => window.__futureFixtureExecuted), false);
    report.checks.push('Adversarial markup in headings, quotes, and original synthesis remains text and creates no executable DOM.');
    await page.screenshot({ path: path.join(output, 'synthetic-future-result.png'), fullPage: true });
    const verificationCheckpoint = fixture();
    const synthesis = { kind: 'future_work', status: 'generated', sections: verificationCheckpoint.result.sections,
      limitations: ['SYNTHETIC checkpoint fixture; public abstract verification is incomplete.'] };
    verificationCheckpoint.id = 'synthetic-saved-verification';
    verificationCheckpoint.status = 'interrupted';
    verificationCheckpoint.completed_items = [{ stage: 'future_synthesis', result: synthesis }, { stage: 'future_verification', result: verificationCheckpoint.result }];
    verificationCheckpoint.result = null;
    await page.evaluate(value => window.__mountFutureFixture(value), verificationCheckpoint);
    assert.equal(await page.locator('.rs-future-direction.is-excluded').count(), 1);
    assert.ok((await page.locator('.rs-result').textContent()).includes('已保存的阶段结果'));
    assert.ok((await page.locator('.rs-job-status').textContent()).includes('调用中断'));
    await page.screenshot({ path: path.join(output, 'synthetic-saved-verification.png'), fullPage: true });
    report.checks.push('An interrupted task with no final result displays its saved verification checkpoint in preference to the earlier synthesis checkpoint.');
    const synthesisCheckpoint = { ...verificationCheckpoint, id: 'synthetic-saved-synthesis', status: 'failed', completed_items: [{ stage: 'future_synthesis', result: synthesis }] };
    await page.evaluate(value => window.__mountFutureFixture(value), synthesisCheckpoint);
    assert.equal(await page.locator('.rs-future-verification').count(), 0);
    assert.equal(await page.locator('.rs-result-section').count(), 2);
    assert.ok((await page.locator('.rs-result').textContent()).includes('公开摘要核验尚未完成'));
    assert.ok((await page.locator('.rs-job-status').textContent()).includes('失败'));
    await page.screenshot({ path: path.join(output, 'synthetic-saved-synthesis.png'), fullPage: true });
    report.checks.push('A synthesis-only checkpoint keeps the original directions visible and explicitly says public abstract verification is incomplete.');
    assert.deepEqual(report.pageErrors, []);
    assert.deepEqual(report.consoleErrors, []);
    assert.deepEqual(report.networkRequests, []);
    assert.equal(await page.evaluate(() => window.__futureFixtureExecuted), false);
    report.apiCalls = await page.evaluate(() => window.__fixtureApiCalls);
    assert.equal(report.apiCalls.length, 15);
    assert.ok(report.apiCalls.every(call => call.method === 'GET'));
    report.checks.push('Each task state reads its header and detail through the read-only stub; zero browser network requests, console errors, or page errors.');
    report.passed = true;
  } catch (error) {
    report.passed = false;
    report.failure = { message: error.message, stack: error.stack };
    await page.screenshot({ path: path.join(output, 'synthetic-failure.png'), fullPage: true }).catch(() => {});
    process.exitCode = 1;
  } finally {
    await page.evaluate(() => window.__futureFixtureController?.destroy()).catch(() => {});
    fs.writeFileSync(path.join(output, 'report.json'), JSON.stringify(report, null, 2), { flag: 'wx' });
    await browser.close();
    console.log(JSON.stringify({ passed: report.passed, output, checks: report.checks.length, failure: report.failure?.message }, null, 2));
  }
}
main().catch(error => { console.error(error.message); process.exitCode = 1; });
