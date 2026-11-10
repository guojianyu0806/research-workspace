/* Offline Edge regression for task history reads, filters, actions, and follow-ups. */
'use strict';

const assert = require('node:assert/strict');
const path = require('node:path');
const {chromium} = require(process.env.PLAYWRIGHT_MODULE || 'C:/Users/g2806/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright');

const staticDir = path.join(__dirname, '..', 'research_workspace', 'static');
const ids = {running: 'running-A', failed: 'failed-B', other: 'other-C', saved: 'saved-D'};
const savedQuestion = '原问题：<img src=x> 如何比较两篇论文？';
const newQuestion = '进一步比较两篇论文的评价指标。';

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
    await page.evaluate(async ({ids, savedQuestion}) => {
      const jobs = Object.fromEntries(Object.entries(ids).map(([key, id], index) => [id, {
        id, kind: key === 'running' ? 'agent_research' : key === 'failed' ? 'qa' : 'review',
        status: key === 'running' ? 'running' : 'failed', created_at: new Date(Date.now() - 60000 * (1 + index)).toISOString(),
        updated_at: 'initial', events: [], progress: 0, total: 1,
      }]));
      jobs[ids.running].events = [
        {stage: 'model_call', message: '模型返回：AnswerPayload'},
        {stage: 'agent_answer_repair', message: '正在根据审核结果修复回答'},
        {stage: 'model_call', message: '模型返回：AnswerPayload'},
      ];
      const pinned = [
        {document_id: 'paper-A', title: '任务保存的论文甲', run_id: 'original-run-A'},
        {document_id: 'paper-B', title: '任务保存的论文乙', run_id: 'original-run-B'},
      ];
      const documents = [
        {id: 'paper-A', title: '文献库论文甲', filename: 'paper-a.pdf', page_count: 2,
          is_current_version: true, available_run: {id: 'replacement-run-A'}},
        {id: 'paper-B', title: '文献库论文乙旧版本', filename: 'paper-b.pdf', page_count: 3,
          is_current_version: false, available_run: {id: 'replacement-run-B'}},
      ];
      jobs[ids.failed].document_ids = ['paper-A'];
      jobs[ids.other].document_ids = ['paper-archived'];
      jobs[ids.saved] = {...jobs[ids.saved], kind: 'agent_research', status: 'completed', title: savedQuestion,
        document_ids: pinned.map(doc => doc.document_id),
        payload: {question: savedQuestion, agent_public_query: 'old arXiv query', agent_import_limit: 1},
        result: {status: 'generated', claims: []},
        follow_up: {mode: 'compare', documents: pinned}};
      const historyDocuments = documents.map(({id, title}) => ({id, title, archived_at: null}));
      historyDocuments.push({id: 'paper-archived', title: '归档论文', archived_at: '2026-10-01T00:00:00Z'});
      const listResponse = (params = new URLSearchParams()) => ({
        documents: historyDocuments,
        jobs: Object.values(jobs)
          .filter(job => (!params.get('document_id') || job.document_ids?.includes(params.get('document_id'))) &&
            (!params.get('status') || job.status === params.get('status')))
          .map(({payload, result, follow_up, ...summary}) => summary),
      });
      const calls = [], posts = [], hold = new Set(), pending = new Map(), streams = [];
      window.EventSource = class extends EventTarget {
        constructor(url) { super(); this.url = url; streams.push(this); }
        close() { this.closed = true; }
      };
      const api = async (url, init) => {
        const method = init.method || 'GET';
        const {pathname, search, searchParams} = new URL(url, 'https://fixture.invalid');
        const taskList = pathname === '/api/research/tasks';
        const key = `${method} ${pathname}${taskList ? search : ''}`;
        calls.push(key);
        if (hold.delete(key)) return new Promise((resolve, reject) => pending.set(key, {resolve, reject}));
        if (pathname === '/api/capabilities') return {intelligence: {configured: true}};
        if (pathname === '/api/documents') return {documents};
        if (taskList && method === 'POST') {
          const payload = JSON.parse(init.body);
          posts.push(payload);
          if (payload.source_task_id) {
            const job = {...jobs[ids.saved], id: 'follow-up-task', title: payload.question,
              source_task_id: payload.source_task_id, payload,
              created_at: new Date().toISOString()};
            jobs[job.id] = job;
            return {job};
          }
          const job = {...jobs[ids.other], id: 'new-task', kind: 'translate_text', status: 'completed',
            document_ids: [], created_at: new Date().toISOString()};
          jobs[job.id] = job;
          return {job};
        }
        if (taskList) return listResponse(searchParams);
        const [, id, action] = pathname.match(/^\/api\/research\/tasks\/([^/]+)(?:\/(cancel|retry))?$/) || [];
        if (!jobs[id]) throw Error(`Unexpected API ${key}`);
        if (action) jobs[id] = {...jobs[id], status: action === 'cancel' ? 'cancelled' : 'queued'};
        return {job: {...jobs[id]}};
      };
      const controller = window.ResearchTasks.mount(document.querySelector('#fixture'), {api, initialJobId: ids.running});
      window.fixture = {calls, posts, hold, pending, streams, jobs, listResponse, controller};
      await controller.ready;
    }, {ids, savedQuestion});

    const row = id => page.locator(`.rs-history-item[data-job-id="${id}"]`);
    const status = page.locator('.rs-job-status');
    const readNotice = page.locator('.rs-task-read');
    const cancel = page.locator('.rs-actions button').filter({hasText: '取消任务'});
    const retry = page.locator('.rs-actions button').filter({hasText: '继续重试'});
    const exportLink = page.locator('.rs-actions a');
    const key = (id, action) => `${action ? 'POST' : 'GET'} /api/research/tasks/${id}${action ? `/${action}` : ''}`;
    const hold = request => page.evaluate(request => { fixture.hold.add(request); }, request);
    const started = request => page.waitForFunction(request => fixture.pending.has(request), request);
    const postCalls = () => page.evaluate(() => fixture.calls.filter(call => call.startsWith('POST')));
    const release = (request, id, error, patch = {}) => page.evaluate(({request, id, error, patch}) => {
      const gate = fixture.pending.get(request);
      fixture.pending.delete(request);
      if (error) gate.reject(Error(error));
      else gate.resolve({job: {...fixture.jobs[id], ...patch}});
    }, {request, id, error, patch});
    const selected = id => page.locator(`.rs-history-item[data-job-id="${id}"][aria-pressed="true"]`).waitFor();

    await selected(ids.running);
    assert.match(await page.locator('.rs-task-phase').innerText(), /正在根据审核结果修复回答/);
    assert.ok(await cancel.isEnabled());
    const failedRead = key(ids.failed);
    await hold(failedRead); await row(ids.failed).click(); await started(failedRead);
    assert.equal(await readNotice.getAttribute('data-state'), 'loading');
    assert.match(await readNotice.innerText(), /正在读取.*下方仍显示原任务。/);
    assert.equal(await row(ids.running).getAttribute('aria-pressed'), 'true');
    assert.equal(await row(ids.failed).getAttribute('aria-pressed'), 'false');
    assert.match(await row(ids.failed).getAttribute('class'), /is-requested/);
    assert.ok(await cancel.isDisabled());
    assert.equal(await exportLink.getAttribute('href'), null);
    await cancel.dispatchEvent('click');
    await page.evaluate(id => {
      fixture.streams[0].onmessage({data: JSON.stringify({id, status: 'completed'})});
      return fixture.controller.refresh();
    }, ids.running);
    assert.equal(await page.evaluate(id => fixture.calls.filter(call => call === `GET /api/research/tasks/${id}`).length, ids.running), 1);
    await release(failedRead, ids.failed, '合成读取错误');
    await page.locator('.rs-task-read[data-state="error"]').waitFor();
    assert.match(await readNotice.innerText(), /合成读取错误/);
    assert.ok(await cancel.isDisabled());
    await cancel.dispatchEvent('click');
    assert.deepEqual(await postCalls(), []);

    await readNotice.getByRole('button', {name: '重新读取任务'}).click();
    await selected(ids.failed);
    assert.ok(await readNotice.isHidden() && await retry.isEnabled());
    assert.equal(await exportLink.getAttribute('href'), `/api/research/tasks/${ids.failed}/export`);
    const failedRetry = key(ids.failed, 'retry');
    await hold(failedRetry); await retry.click(); await started(failedRetry);
    await row(ids.other).click(); await selected(ids.other);
    await release(failedRetry, ids.failed, null, {status: 'queued'});
    assert.equal(await row(ids.other).getAttribute('aria-pressed'), 'true');
    assert.match(await row(ids.failed).innerText(), /排队中/);
    assert.ok(await retry.isEnabled());
    assert.deepEqual(await postCalls(), [failedRetry]);

    await hold(failedRead); await row(ids.failed).click(); await started(failedRead);
    assert.ok(await retry.isDisabled());
    await retry.dispatchEvent('click');
    await row(ids.running).click(); await selected(ids.running);
    await release(failedRead, ids.failed, null, {status: 'completed'});
    assert.equal(await row(ids.running).getAttribute('aria-pressed'), 'true');
    assert.ok(await readNotice.isHidden() && await cancel.isEnabled());

    await hold(failedRead); await row(ids.failed).click(); await started(failedRead);
    await page.evaluate(() => fixture.controller.suspend());
    await release(failedRead, ids.failed, null, {status: 'completed'});
    assert.equal(await row(ids.running).getAttribute('aria-pressed'), 'true');
    await page.evaluate(() => fixture.controller.resume());
    await selected(ids.failed);
    assert.match(await status.innerText(), /失败/);
    await row(ids.running).click(); await selected(ids.running);
    await cancel.click();
    await page.waitForFunction(() => document.querySelector('.rs-job-status').textContent.includes('已取消'));
    assert.deepEqual(await postCalls(), [failedRetry, key(ids.running, 'cancel')]);

    const listRead = 'GET /api/research/tasks';
    await hold(listRead);
    await page.evaluate(() => {
      fixture.oldList = fixture.listResponse();
      fixture.refresh = fixture.controller.refresh();
    });
    await started(listRead);
    await page.getByRole('combobox', {name: '任务类型'}).selectOption('translate_text');
    await page.getByRole('textbox', {name: '待翻译学术文本'}).fill('SYNTHETIC translation text');
    await page.getByRole('button', {name: '创建研究任务', exact: true}).click();
    await selected('new-task');
    await page.evaluate(request => {
      fixture.pending.get(request).resolve(fixture.oldList);
      fixture.pending.delete(request);
      return fixture.refresh;
    }, listRead);
    assert.equal(await row('new-task').getAttribute('aria-pressed'), 'true');
    assert.equal(await exportLink.getAttribute('href'), '/api/research/tasks/new-task/export');

    assert.equal(await row(ids.saved).locator('strong').textContent(), savedQuestion);
    assert.equal(await row(ids.saved).locator('img').count(), 0);
    assert.match(await row(ids.failed).innerText(), /文献问答/);
    const continueButton = page.getByRole('button', {name: '基于此结果继续', exact: true});
    assert.ok(await continueButton.isHidden());
    const documentFilter = page.getByRole('combobox', {name: '按文献筛选', exact: true});
    const statusFilter = page.getByRole('combobox', {name: '按状态筛选', exact: true});
    const historyIdle = () => page.locator('.rs-history[aria-busy="false"]').waitFor();
    const documentRead = listRead + '?document_id=paper-A';
    await hold(documentRead);
    await documentFilter.selectOption('paper-A');
    await started(documentRead);
    await statusFilter.selectOption('completed');
    await historyIdle();
    await row('new-task').waitFor({state: 'detached'});
    await row(ids.failed).waitFor({state: 'detached'});
    assert.ok(await row(ids.saved).isVisible());
    assert.equal(await exportLink.getAttribute('href'), '/api/research/tasks/new-task/export');
    await page.evaluate(async ({request, saved}) => {
      const stale = fixture.listResponse(new URLSearchParams('document_id=paper-A'));
      stale.jobs.find(job => job.id === saved).title = '迟到的旧问题标题';
      fixture.pending.get(request).resolve(stale);
      fixture.pending.delete(request);
      await new Promise(requestAnimationFrame);
    }, {request: documentRead, saved: ids.saved});
    assert.equal(await row(ids.failed).count(), 0, 'slower document-only response must not replace combined filters');
    assert.equal(await row(ids.saved).locator('strong').textContent(), savedQuestion, 'late response must not replace matching rows');

    await statusFilter.selectOption('failed');
    await row(ids.failed).waitFor();
    await row(ids.saved).waitFor({state: 'detached'});
    await statusFilter.selectOption('running');
    await historyIdle();
    await row(ids.failed).waitFor({state: 'detached'});
    assert.equal(await page.locator('.rs-history-item').count(), 0);
    await statusFilter.selectOption('');
    await documentFilter.selectOption('paper-archived');
    await row(ids.other).waitFor();
    assert.equal(await page.locator('.rs-history-item').count(), 1);
    await documentFilter.selectOption('');
    await row(ids.saved).click();
    await selected(ids.saved);

    const followUp = page.locator('.rs-follow-up-form');
    const question = followUp.getByRole('textbox', {name: '追问', exact: true});
    const submitFollowUp = followUp.getByRole('button', {name: '提交追问', exact: true});
    const taskPosts = () => page.evaluate(() => fixture.posts);
    const originalPosts = await taskPosts();
    await continueButton.click();
    assert.equal(await question.inputValue(), '');
    const pinnedScope = await followUp.innerText();
    for (const value of ['任务保存的论文甲', '任务保存的论文乙', 'original-run-A', 'original-run-B']) {
      assert.ok(pinnedScope.includes(value), `follow-up scope includes ${value}`);
    }
    assert.doesNotMatch(pinnedScope, /replacement-run|文献库论文/);
    assert.deepEqual(await taskPosts(), originalPosts);
    await question.fill('这条草稿将被取消');
    await followUp.getByRole('button', {name: '取消追问', exact: true}).click();
    assert.ok(await followUp.isHidden());
    assert.deepEqual(await taskPosts(), originalPosts);

    await continueButton.click();
    assert.equal(await question.inputValue(), '');
    await question.fill('   ');
    if (await submitFollowUp.isEnabled()) await submitFollowUp.click();
    assert.deepEqual(await taskPosts(), originalPosts);
    await question.fill('切换任务时的草稿');
    await hold(failedRead); await row(ids.failed).click(); await started(failedRead);
    assert.ok(await followUp.isHidden() || await submitFollowUp.isDisabled());
    if (await followUp.count()) await followUp.dispatchEvent('submit');
    assert.deepEqual(await taskPosts(), originalPosts);
    await release(failedRead, ids.failed);
    await selected(ids.failed);
    assert.ok(await continueButton.isHidden());
    assert.deepEqual(await taskPosts(), originalPosts);

    await row(ids.saved).click(); await selected(ids.saved);
    await continueButton.click();
    assert.equal(await question.inputValue(), '');
    await question.fill(`  ${newQuestion}  `);
    await submitFollowUp.click();
    await selected('follow-up-task');
    assert.equal(await row('follow-up-task').locator('strong').textContent(), newQuestion);
    assert.deepEqual(await taskPosts(), [...originalPosts,
      {kind: 'agent_research', source_task_id: ids.saved, question: newQuestion}]);
    assert.deepEqual(errors, []);
    assert.deepEqual(network, []);
    console.log('PASS offline Edge task selection, refresh, SSE, action targets, history filters, and scoped follow-up');
  } finally {
    await browser.close();
  }
}

main().catch(error => { console.error(error); process.exitCode = 1; });
