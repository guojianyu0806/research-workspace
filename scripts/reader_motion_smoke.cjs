/* Real local PDF rasters served by an isolated API, with controlled image decode and HTTP faults. */
'use strict';
const assert = require('node:assert/strict');
const fs = require('node:fs/promises');
const path = require('node:path');
const { randomUUID } = require('node:crypto');
const { spawn } = require('node:child_process');
const { createInterface } = require('node:readline');
const { chromium } = require(process.env.PLAYWRIGHT_MODULE || 'C:/Users/g2806/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright');

const root = path.resolve(__dirname, '..');
const output = path.join(root, 'artifacts', 'browser', `reader-motion-${randomUUID()}`);
const fixture = String.raw`
import json, os, socket, sys, tempfile, threading, time
from pathlib import Path

with tempfile.TemporaryDirectory(prefix='research-reader-motion-') as directory:
    for key in ('RESEARCH_MODEL_BASE_URL', 'RESEARCH_MODEL', 'RESEARCH_MODEL_API_KEY'):
        os.environ.pop(key, None)
    os.environ.update(RESEARCH_DATA_DIR=directory, RESEARCH_SEMANTIC_ENABLED='off', RESEARCH_OCR_ENABLED='off')
    import uvicorn
    from research_workspace.api import create_app
    app = create_app(directory, start_worker=False)
    library = app.state.library

    def add(file):
        source = Path('tests/fixtures') / file
        document = library.import_document(source.read_bytes(), source.name)['document']
        library.process_next_job()
        return document['id'], document['current_run']['id']

    large, _ = add('book_reading/20261020-v1/oversize-native.pdf')
    rotated, _ = add('book_reading/20261020-v1/rotated-native.pdf')
    main, historical = add('local_acceptance/20261019-v1/shared.pdf')
    current = library.create_run(main)['id']
    library.process_next_job()
    bbox = library.get_page(main, 4, run_id=historical)['blocks'][0]['bbox']
    sock = socket.socket()
    sock.bind(('127.0.0.1', 0))
    server = uvicorn.Server(uvicorn.Config(app, log_level='error'))
    thread = threading.Thread(target=lambda: server.run(sockets=[sock]), daemon=True)
    thread.start()
    try:
        while not server.started and thread.is_alive():
            time.sleep(.02)
        assert server.started
        print(json.dumps(dict(origin=f'http://127.0.0.1:{sock.getsockname()[1]}', main=main,
                              rotated=rotated, large=large, historical=historical, current=current, bbox=bbox)))
        sys.stdin.readline()
    finally:
        server.should_exit = True
        thread.join(timeout=10)
        assert not thread.is_alive()
`;

const imagePath = (id, number) => `/api/documents/${id}/pages/${number}/image`;

async function run() {
  await fs.mkdir(output, { recursive: true });
  const report = { checks: [], requests: [], errors: [], blockedRequests: [] };
  const check = (name, ok, detail) => {
    report.checks.push({ name, passed: Boolean(ok), detail });
    assert.ok(ok, name);
    console.log(`PASS ${name}`);
  };
  const child = spawn(process.env.READER_MOTION_PYTHON || path.join(root, '.venv', 'Scripts', 'python.exe'),
    ['-u', '-c', fixture], { cwd: root, windowsHide: true, stdio: ['pipe', 'pipe', 'pipe'] });
  let stderr = '';
  child.stderr.on('data', chunk => { stderr += chunk; });
  const exited = new Promise(resolve => child.once('close', code => resolve(code)));
  const lines = createInterface({ input: child.stdout });
  let browser, page, startup;
  try {
    const ready = await Promise.race([
      new Promise(resolve => lines.once('line', line => resolve(JSON.parse(line)))),
      exited.then(code => { throw Error(`Fixture exited ${code}: ${stderr}`); }),
      new Promise((_, reject) => { startup = setTimeout(() => reject(Error('Fixture startup timed out')), 30000); }),
    ]);
    clearTimeout(startup);
    const { origin, main, rotated, large, historical, current, bbox } = ready;
    check('two saved runs exist for source-location checks', historical !== current && bbox.length === 4);
    browser = await chromium.launch({ headless: true,
      executablePath: process.env.BROWSER_EXECUTABLE || 'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe' });
    const context = await browser.newContext({ viewport: { width: 1440, height: 900 }, reducedMotion: 'no-preference' });
    await context.addInitScript(first => {
      const pending = new Map();
      const nativeDecode = HTMLImageElement.prototype.decode;
      window.holdPdfDecode = path => {
        const gate = { decoded: false };
        gate.wait = new Promise(resolve => { gate.release = resolve; });
        pending.set(path, gate);
      };
      window.pdfDecodeIsHeld = path => pending.get(path)?.decoded;
      window.releasePdfDecode = path => { pending.get(path).release(); pending.delete(path); };
      holdPdfDecode(first);
      HTMLImageElement.prototype.decode = async function () {
        const gate = pending.get(new URL(this.src, location.origin).pathname);
        await nativeDecode.call(this);
        if (gate) { gate.decoded = true; await gate.wait; }
      };
      window.pdfArrivals = [];
      window.addEventListener('research:page-ready', () => {
        const image = document.getElementById('pageImage');
        const style = getComputedStyle(image);
        pdfArrivals.push({ source: new URL(image.src).pathname, animation: style.animationName, duration: style.animationDuration });
      });
    }, imagePath(main, 1));
    let failOnce = true;
    await context.route('**/*', async route => {
      const request = route.request();
      const url = new URL(request.url());
      if (url.origin !== origin || request.method() !== 'GET') {
        report.blockedRequests.push({ url: url.href, method: request.method() });
        return route.abort();
      }
      if (url.pathname.includes('/pages/')) report.requests.push(url.pathname + url.search);
      if (url.pathname === imagePath(main, 6) && failOnce) {
        failOnce = false;
        return route.fulfill({ status: 503, contentType: 'text/plain', body: 'Controlled image failure' });
      }
      return route.continue();
    });
    page = await context.newPage();
    page.on('pageerror', error => report.errors.push(String(error)));
    page.setDefaultTimeout(10000);

    const hold = (id, number) => page.evaluate(path => holdPdfDecode(path), imagePath(id, number));
    const held = (id, number) => page.waitForFunction(path => pdfDecodeIsHeld(path), imagePath(id, number));
    const release = (id, number) => page.evaluate(path => releasePdfDecode(path), imagePath(id, number));
    const shown = (id, number) => page.waitForFunction(path => {
      const image = document.getElementById('pageImage');
      return !document.getElementById('paperPage').hidden && image.naturalWidth > 0 && new URL(image.src).pathname === path;
    }, imagePath(id, number));
    const jump = (id, number, extra) => page.evaluate(evidence => ResearchWorkspace.onEvidence(evidence),
      { document_id: id, page: number, ...extra });
    const waitForPlaceholderRatio = ratio => page.waitForFunction(ratio => {
      const placeholder = document.getElementById('pagePlaceholder');
      return !placeholder.hidden && Math.abs(placeholder.clientWidth / placeholder.clientHeight - ratio) < .01;
    }, ratio);
    const paperState = () => page.evaluate(() => {
      const image = document.getElementById('pageImage');
      return { paper: !document.getElementById('paperPage').hidden, src: image.getAttribute('src'),
        top: document.getElementById('readerViewport').scrollTop, loading: !document.getElementById('pageLoading').hidden,
        placeholder: !document.getElementById('pagePlaceholder').hidden,
        highlight: !document.getElementById('evidenceHighlight').hidden, footer: document.getElementById('readerFooterText').textContent };
    });

    await page.goto(origin);
    await held(main, 1);
    await waitForPlaceholderRatio(595 / 842);
    await page.locator('#pageLoading').waitFor();
    check('opening first page shows paper-shaped placeholder with its page number',
      (await page.locator('#pageLoading').textContent()).includes('第 1 页') && !(await paperState()).paper);
    await release(main, 1);
    await shown(main, 1);
    check('first PDF raster replaces the placeholder after decode', !(await paperState()).placeholder);

    await hold(main, 2);
    const immediate = await page.evaluate(() => {
      const viewport = document.getElementById('readerViewport');
      viewport.scrollTop = 240;
      const top = viewport.scrollTop;
      document.getElementById('nextPage').click();
      return { top, after: viewport.scrollTop, waiting: !document.getElementById('pageLoading').hidden };
    });
    check('page navigation preserves scroll and defers loading feedback', immediate.top > 100 && immediate.top === immediate.after && !immediate.waiting, immediate);
    await held(main, 2);
    await page.locator('#pageLoading').waitFor();
    const pending = await paperState();
    check('the displayed page stays visible through a slow decode', pending.paper && pending.src.includes(imagePath(main, 1))
      && pending.top === immediate.top && !pending.placeholder && pending.footer.includes('第 1 /')
      && (await page.locator('#pageLoading').textContent()).includes('第 2 页'), pending);
    await page.screenshot({ path: path.join(output, 'waiting-on-page-2.png') });
    await release(main, 2);
    await shown(main, 2);
    check('decoded page is swapped once and resets vertical position', !(await paperState()).loading && (await paperState()).top === 0
      && report.requests.filter(request => request.startsWith(imagePath(main, 2))).length === 1);
    const arrival = await page.evaluate(() => pdfArrivals.at(-1));
    check('normal motion adds a brief opacity animation to the incoming page', arrival.animation === 'reader-page-arrival' && Number.parseFloat(arrival.duration) <= .16, arrival);

    await hold(main, 3);
    await hold(main, 4);
    await jump(main, 3, { bbox: [.1, .1, .3, .3], run_id: current });
    await held(main, 3);
    await jump(main, 4, { bbox, run_id: historical });
    await held(main, 4);
    const beforeCitation = await paperState();
    check('pending citations do not highlight the old paper', !beforeCitation.highlight && beforeCitation.src.includes(imagePath(main, 2)));
    await release(main, 4);
    await shown(main, 4);
    await release(main, 3);
    await page.evaluate(() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve))));
    const citation = await paperState();
    const highlight = await page.locator('#evidenceHighlight').evaluate(element => [element.style.left, element.style.top].map(Number.parseFloat));
    check('late older decode cannot replace the historical-run target or its exact box', citation.highlight
      && citation.src.includes(imagePath(main, 4)) && Math.abs(highlight[0] - bbox[0] * 100) < .001
      && Math.abs(highlight[1] - bbox[1] * 100) < .001 && report.requests.includes(`/api/documents/${main}/pages/4?run_id=${historical}`));

    await page.emulateMedia({ reducedMotion: 'reduce' });
    await page.locator('#nextPage').click();
    await shown(main, 5);
    check('reduced motion swaps pages without a fade or stale highlight', (await page.evaluate(() => pdfArrivals.at(-1).animation)) === 'none' && !(await paperState()).highlight);
    await page.locator('#nextPage').click();
    await page.locator('#pageError').waitFor();
    const error = await paperState();
    check('failed target clears the busy state and offers a page-specific retry', !error.loading && !error.paper
      && !error.placeholder && (await page.locator('#pageErrorText').textContent()).includes('第 6 页'), error);
    await page.locator('#retryPageButton').click();
    await shown(main, 6);
    check('retry opens the same failed page', await page.locator('#pageError').isHidden());

    await hold(rotated, 1);
    await jump(rotated, 1);
    await held(rotated, 1);
    await waitForPlaceholderRatio(792 / 612);
    const switched = await paperState();
    check('document switch clears the old raster and uses the rotated paper ratio', !switched.paper && switched.src === null && switched.placeholder, switched);
    await release(rotated, 1);
    await shown(rotated, 1);
    await jump(large, 1);
    await shown(large, 1);
    check('actual large PDF falls back to the safe render scale', await page.locator('#pageImage').getAttribute('data-render-scale') === '1');
    await jump(large, 3);
    await page.locator('#pageError').waitFor();
    check('unrenderable PDF still reports the safety limit', (await page.locator('#pageErrorText').textContent()).includes('安全渲染上限'));
    check('no speculative page request, script error or external request', !report.errors.length && !report.blockedRequests.length
      && !report.requests.some(request => request.startsWith(imagePath(main, 7))));
  } catch (error) {
    report.errors.push(String(error.stack || error));
    process.exitCode = 1;
    if (page) await page.screenshot({ path: path.join(output, 'failure.png') }).catch(() => {});
  } finally {
    clearTimeout(startup);
    if (browser) await browser.close();
    child.stdin.end();
    const watchdog = setTimeout(() => child.kill(), 15000);
    const code = await exited;
    clearTimeout(watchdog);
    if (code !== 0) { report.errors.push(`Fixture exit ${code}: ${stderr}`); process.exitCode = 1; }
    report.passed = !process.exitCode;
    await fs.writeFile(path.join(output, 'report.json'), JSON.stringify(report, null, 2), { flag: 'wx' });
    console.log(JSON.stringify({ output, passed: report.passed, checks: report.checks.length, errors: report.errors }));
  }
}

run().catch(error => { console.error(error); process.exitCode = 1; });
