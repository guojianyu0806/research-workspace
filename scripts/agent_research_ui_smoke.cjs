/* Offline verification of the real research form and persisted tool receipts.
 * Every API result is a SYNTHETIC fixture; no server, model, arXiv or import runs.
 * Uses the existing Playwright/Edge installation. */
'use strict';
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const crypto = require('node:crypto');
const {chromium} = require(process.env.PLAYWRIGHT_MODULE || 'C:/Users/g2806/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright');
const root = path.resolve(__dirname, '..');
const output = path.join(root, 'artifacts/browser', `agent-ui-${new Date().toISOString().replace(/[:.]/g, '-')}-${crypto.randomBytes(4).toString('hex')}`);
fs.mkdirSync(output, {recursive:true});
const attack = '<img src="https://invalid.example/agent.png" onerror="window.__agentAttack=true">';
const report = {syntheticFixture:true, output, checks:[], pageErrors:[], networkRequests:[], sourceSha256:{}, limitations:['Offline form and DOM fixture only; does not validate model tool selection, backend permissions, imports or answer quality.']};
for (const name of ['research_workspace/static/research.js', 'research_workspace/static/index.html', 'scripts/agent_research_ui_smoke.cjs']) report.sourceSha256[name] = crypto.createHash('sha256').update(fs.readFileSync(path.join(root,name))).digest('hex');
function check(name, passed, details) { report.checks.push({name, passed:Boolean(passed), details}); assert.ok(passed, name); }
async function main() {
  const browser = await chromium.launch({headless:true, executablePath:process.env.BROWSER_EXECUTABLE || 'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe'});
  const context = await browser.newContext({viewport:{width:1440,height:1000}, reducedMotion:'reduce'});
  await context.route('**/*', route => {report.networkRequests.push(route.request().url());return route.abort();});
  const page = await context.newPage(); page.setDefaultTimeout(10000);
  page.on('pageerror', error => report.pageErrors.push(String(error)));
  try {
    await page.setContent('<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><title>Agent UI synthetic fixture</title></head><body><p>SYNTHETIC UI FIXTURE — NOT A RESEARCH RESULT</p><main id="fixture-root"></main></body></html>');
    for (const name of ['styles.css','research.css','modern-theme.css']) await page.addStyleTag({path:path.join(root,'research_workspace/static',name)});
    await page.addStyleTag({content:'body{display:block;overflow:auto;padding:16px}#fixture-root{min-width:0}'});
    await page.addScriptTag({path:path.join(root,'research_workspace/static/notices.js')});
    await page.addScriptTag({path:path.join(root,'research_workspace/static/research.js')});
    await page.evaluate(async attackText => {
      window.__agentAttack = false; window.__agentPosts = []; window.__agentEvidence = []; window.__agentApiCalls = []; window.__failAgentSubscriptions = false;
      const time = '2026-11-10T00:00:00Z';
      const documents = ['fixture-one','fixture-two'].map(id => ({id,title:`SYNTHETIC ${id}`,filename:id+'.pdf',page_count:3,is_current_version:true,available_run:{id:`run-${id}`}}));
      const api = async (url, init = {}) => {
        window.__agentApiCalls.push({url,method:init.method || 'GET'});
        if (url === '/api/capabilities') return {intelligence:{configured:true}};
        if (url === '/api/documents') return {documents};
        if (url === '/api/subscriptions') {
          if (window.__failAgentSubscriptions) throw new Error('SYNTHETIC subscription-list failure');
          return {subscriptions:[{id:'fixture-subscription-id',query:'SYNTHETIC saved query',enabled:false}]};
        }
        if (url === '/api/research/tasks' && init.method !== 'POST') return {jobs:[]};
        if (url === '/api/research/tasks' && init.method === 'POST') {
          const payload = JSON.parse(init.body); window.__agentPosts.push(payload);
          const twoClaims = ['agent_research', 'review'].includes(payload.kind);
          const sources = [
            {evidence_id:'e-one',document_id:'fixture-one',run_id:'run-fixture-one',page:2,block_id:'one-block',bbox:[.1,.1,.9,.2],document_title:'First synthetic paper'},
            {evidence_id:'e-two',document_id:'fixture-two',run_id:'run-fixture-two',page:3,block_id:'two-block',bbox:[.1,.3,.9,.4],document_title:'Second synthetic paper'},
          ].slice(0, twoClaims ? 2 : 1);
          const claims = sources.map((source, index) => ({text:`SYNTHETIC grounded claim ${index + 1}`, citations:[
            {evidence_id:source.evidence_id,quote_id:'Q1',quote:index ? 'Second exact quote\n' + attackText : 'First exact quote\ncontinued line'},
          ]}));
          const catalog = claims.map((claim, index) => ({claim_index:index,claim_id:`fixture-claim-${index}`,text:claim.text,
            citations:[{...sources[index],...claim.citations[0]}]}));
          const reviews = claims.map((_, index) => ({claim_index:index,verdict:'supported',reason:`Own review ${index + 1}`}));
          const grounding = payload.kind === 'review' ? {
            status:'passed',response_kind:'review',
            attempts:[{attempt:2,problems:[],claims:catalog,reviews}],
            review_coverage:[{attempt:2,claim_catalog:catalog}],
          } : twoClaims ? {status:'passed',claim_catalog:catalog,claim_reviews:reviews.map((review,index) =>
            ({...review,claim_id:index ? 'stale-second-review' : catalog[index].claim_id}))} : undefined;
          const anchor = {document_id:'fixture-one',run_id:'run-fixture-one',page:3,bbox:[.1,.5,.9,.6]};
          return {job:{id:`fixture-job-${window.__agentPosts.length}`,kind:payload.kind,status:'completed',created_at:time,updated_at:time,payload,
            progress:2,total:2, events:[{created_at:time,stage:'tool_call',message:'已保存：检索原文证据'},{created_at:time,stage:'tool_return',message:'工具回执：'+attackText}],
            result:{status:'generated',sources,grounding,
              ...(payload.kind === 'review' ? {sections:claims.map((claim,index) => ({heading:`Section ${index + 1}`,claims:[claim]}))} : {claims}),
              evidence:[...sources,{...anchor,evidence_id:'graph-author',block_id:'author-line',text:'Example authors.'},
                {...anchor,evidence_id:'graph-title',block_id:'title-line',text:attackText},
                {...anchor,evidence_id:'graph-other-run',block_id:'wrong-version',run_id:'newer-run',text:attackText}],
              agent_trace:payload.kind === 'agent_research' ? [
                {tool:'search_evidence',status:'ok',code:'ok',attempt:1,elapsed_ms:12,reused:true,
                  retrieval:{mode:'bm25_lexical',semantic:{status:'failed_fallback'}},arguments:{reasoning:'PRIVATE_FIXTURE_REASONING'}},
                {tool:'prepare_parse',status:'waiting',code:'parse_pending',attempt:2},
                {tool:'import_paper',status:'error',code:attackText,attempt:3},
                {tool:'search_evidence',status:'error',code:'tool_budget_exhausted',attempt:0,elapsed_ms:0},
                {tool:attackText,status:'unexpected_status',code:'unknown_tool',attempt:4}] : [],
              agent_public_papers:payload.kind === 'agent_research' ? [
                {id:'arxiv:2601.00001v1',title:'SYNTHETIC public metadata '+attackText,source_url:'https://arxiv.org/abs/2601.00001v1',published:'2026-01-02'},
                {id:'arxiv:2601.00002v1',title:'SYNTHETIC lookalike URL',source_url:'https://arxiv.org.invalid.example/abs/2601.00002v1'},
                {id:'arxiv:2601.00003v1',title:'SYNTHETIC insecure URL',source_url:'http://arxiv.org/abs/2601.00003v1'},
                {id:'arxiv:2601.00004v1',title:'SYNTHETIC mismatched ID',source_url:'https://arxiv.org/abs/2601.00001v1'}] : [],
              agent_graphs:payload.kind === 'agent_research' ? [{
                nodes:[{id:'fixture-two',document_id:'fixture-two',title:attackText,rank:2,pagerank:0.2},
                  {id:'fixture-one',document_id:'fixture-one',run_id:'run-fixture-one',title:'SYNTHETIC ranked first',rank:1,pagerank:0.8}],
                edges:[{source:'fixture-one',target:'fixture-two',evidence_ids:['graph-other-run','graph-author','graph-title','missing-anchor']}],
                evidence_omitted_due_to_budget:1,method:{damping:0.85},
              }] : [],
              reasoning:'PRIVATE_FIXTURE_REASONING'} }};
        }
        throw new Error(`Unexpected synthetic fixture request: ${init.method || 'GET'} ${url}`);
      };
      window.__agentController = window.ResearchTasks.mount(document.getElementById('fixture-root'), {api,onEvidence:value => window.__agentEvidence.push(value)});
      await window.__agentController.ready;
    }, attack);
    const form = page.locator('.rs-task-form'), kind = form.locator('select').first();
    const agent = form.locator('.rs-agent-options'), query = agent.locator('input[type=text]');
    const mode = agent.getByLabel('研究方式',{exact:true}), subscription = agent.getByLabel('检查已有 arXiv 订阅（可选）',{exact:true}), allowImport = agent.locator('input[type=checkbox]');
    const question = form.locator('textarea:visible'), create = form.getByRole('button',{name:'创建研究任务',exact:true});
    const first = form.locator('.rs-document-choice input[value="fixture-one"]'), second = form.locator('.rs-document-choice input[value="fixture-two"]');
    const posts = () => page.evaluate(() => window.__agentPosts);
    const dismiss = async () => {for (const button of await page.locator('.notice-stack .icon-button').all()) await button.click();};
    const submit = async count => {await dismiss();await create.click();await page.waitForFunction(n => window.__agentPosts.length === n,count);};
    check('ordinary QA hides and disables agent-only controls', await agent.isHidden() && await agent.evaluate(el => el.disabled));
    check('ordinary task does not load subscriptions', await page.evaluate(() => !window.__agentApiCalls.some(call => call.url === '/api/subscriptions')));
    await kind.selectOption('agent_research');
    await subscription.locator('option[value="fixture-subscription-id"]').waitFor({state:'attached'});
    check('agent loads existing subscription IDs without creating or checking them', await page.evaluate(() => window.__agentApiCalls.filter(call => call.url.includes('subscriptions')).length === 1 && window.__agentApiCalls.find(call => call.url.includes('subscriptions')).method === 'GET') && await subscription.inputValue() === '');
    check('agent defaults to QA with no public query or import permission', await agent.isVisible() && await mode.inputValue() === 'qa' && await query.inputValue() === '' && !await allowImport.isChecked() && await allowImport.isDisabled());
    await question.fill('What does the selected source show?'); await create.click();
    check('empty source scope blocks task creation', (await posts()).length === 0 && (await page.locator('.notice-text').last().textContent()).includes('请选择至少一篇文献'));
    await first.check(); await submit(1);
    const local = (await posts())[0];
    check('local agent request carries exact scope and default limits', local.kind === 'agent_research' && local.agent_mode === 'qa' && local.agent_public_query === '' && local.agent_import_limit === 0 && JSON.stringify(local.document_ids) === '["fixture-one"]' && local.question === 'What does the selected source show?' && !('instruction' in local), local);
    await page.locator('.rs-agent-trace summary').click();
    const trace = await page.locator('.rs-agent-trace').textContent();
    check('persisted tool receipts show readable statuses attempts and reuse', ['检索原文证据','已完成','解析论文','等待中','导入公开论文','失败','第 3 次尝试','12 毫秒','复用已保存结果','BM25 词法检索','本地向量检索降级'].every(text => trace.includes(text)));
    const skipped = await page.locator('.rs-agent-trace li').filter({hasText:'tool_budget_exhausted'}).innerText();
    check('calls over budget are clearly marked as unexecuted', skipped.includes('超出调用上限，未执行') && !skipped.includes('毫秒') && !skipped.includes('次尝试'));
    const toolLog = page.getByText('执行与工具记录',{exact:true});
    check('detailed tool events start collapsed after completion', !await toolLog.evaluate(el => el.parentElement.open));
    await toolLog.click();
    check('tool events remain readable separately from final receipts', await toolLog.evaluate(el => el.parentElement.open) && (await page.locator('.rs-task-detail').innerText()).includes('已保存：检索原文证据'));
    check('untrusted trace text cannot create executable DOM or expose reasoning', trace.includes(attack) && await page.locator('.rs-task-detail img,.rs-task-detail [onerror]').count() === 0 && !(await page.locator('body').innerText()).includes('PRIVATE_FIXTURE_REASONING') && !await page.evaluate(() => window.__agentAttack));
    await page.locator('.rs-agent-papers summary').click();
    const publicLinks = await page.locator('.rs-agent-papers a').evaluateAll(items => items.map(item => ({href:item.href,rel:item.rel,target:item.target})));
    check('public metadata only links matching HTTPS arXiv identifiers', publicLinks.length === 1 && publicLinks[0].href === 'https://arxiv.org/abs/2601.00001v1' && publicLinks[0].rel.includes('noopener') && publicLinks[0].target === '_blank' && (await page.locator('.rs-agent-papers').textContent()).includes('不能据此认定已阅读全文'), publicLinks);
    await page.locator('.rs-agent-graphs summary').click();
    const graphText = await page.locator('.rs-agent-graphs').textContent();
    check('graph summary renders real counts damping and rank order safely', graphText.includes('2 个节点') && graphText.includes('1 条引用') && graphText.includes('0.85') && (await page.locator('.rs-agent-graphs tbody tr').first().innerText()).includes('SYNTHETIC ranked first') && graphText.includes(attack) && await page.locator('.rs-agent-graphs img').count() === 0);
    const cards = page.locator('.rs-claim-evidence');
    check('one collapsed evidence card per claim', await cards.count() === 2 && await cards.locator('blockquote:visible').count() === 0);
    await cards.nth(0).locator('summary').press('Enter');
    await cards.nth(1).locator('summary').click();
    check('cards use only the claim’s own exact saved quote and page',
      await cards.nth(0).locator('blockquote').textContent() === 'First exact quote\ncontinued line' &&
      await cards.nth(1).locator('blockquote').textContent() === 'Second exact quote\n' + attack &&
      (await cards.nth(0).locator('figcaption').textContent()).includes('第 2 页') &&
      (await cards.nth(1).locator('figcaption').textContent()).includes('第 3 页') &&
      await cards.locator('img').count() === 0);
    check('claim audit matches identity and rejects a stale verdict',
      (await cards.nth(0).locator('.rs-claim-audit').textContent()).includes('原文支持') &&
      (await cards.nth(0).locator('.rs-claim-audit').textContent()).includes('Own review 1') &&
      (await cards.nth(1).locator('.rs-claim-audit').textContent()).includes('未记录可匹配的逐条审核') &&
      !(await cards.nth(1).locator('.rs-claim-audit').textContent()).includes('Own review 2'));
    await page.locator('.rs-claim .rs-citation').first().click();
    check('final agent answer retains existing original-source navigation', (await page.evaluate(() => window.__agentEvidence))[0]?.document_id === 'fixture-one' && (await page.evaluate(() => window.__agentEvidence))[0]?.page === 2);
    const edge = page.locator('.rs-agent-edge');
    await edge.locator('.rs-agent-edge-primary').click();
    await edge.locator('.rs-agent-edge-anchor').click();
    check('graph edge opens only saved anchors from its citing document and run',
      await page.evaluate(() => window.__agentEvidence.slice(1).map(item => [item.run_id,item.page,item.block_id])).then(items =>
        JSON.stringify(items) === JSON.stringify([['run-fixture-one',3,'title-line'],['run-fixture-one',3,'author-line']])) &&
      await edge.locator('.rs-agent-edge-note').isVisible());
    await page.screenshot({path:path.join(output,'agent-local-result.png'),fullPage:true});
    await mode.selectOption('compare'); await create.click();
    check('local comparison requires at least two selected documents', (await posts()).length === 1 && (await page.locator('.notice-text').last().textContent()).includes('至少两篇'));
    await second.check(); await submit(2);
    check('comparison submits both documents and explicit compare mode', (await posts())[1].agent_mode === 'compare' && (await posts())[1].document_ids.length === 2);
    await first.uncheck(); await second.uncheck(); await mode.selectOption('qa'); await query.fill('  retrieval augmented generation  '); await submit(3);
    check('explicit public query permits empty local scope without importing', (await posts())[2].document_ids.length === 0 && (await posts())[2].agent_public_query === 'retrieval augmented generation' && (await posts())[2].agent_import_limit === 0);
    await allowImport.check(); await submit(4);
    check('import limit becomes one only after explicit checkbox selection', (await posts())[3].agent_import_limit === 1);
    await query.fill('');
    check('clearing public query revokes and disables import selection', !await allowImport.isChecked() && await allowImport.isDisabled());
    await first.check(); await question.fill('   '); await create.click();
    check('whitespace-only question is rejected', (await posts()).length === 4 && (await page.locator('.notice-text').last().textContent()).includes('请填写需要研究的问题'));
    await question.fill('Ordinary QA regression'); await kind.selectOption('qa'); await submit(5);
    const ordinary = (await posts())[4];
    check('ordinary QA payload remains unchanged after using agent controls', await agent.isHidden() && ordinary.kind === 'qa' && ordinary.question === 'Ordinary QA regression' && Object.keys(ordinary).sort().join(',') === 'document_ids,kind,question', ordinary);
    await kind.selectOption('agent_research'); await query.fill(''); await first.uncheck();
    await subscription.selectOption('fixture-subscription-id'); await submit(6);
    const subscribed = (await posts())[5];
    check('explicit subscription permits empty local scope and sends its stable ID', subscribed.agent_subscription_id === 'fixture-subscription-id' && subscribed.agent_public_query === '' && subscribed.document_ids.length === 0 && subscribed.agent_import_limit === 0, subscribed);
    await allowImport.check(); await submit(7);
    check('subscription import still requires explicit checkbox permission', (await posts())[6].agent_import_limit === 1);
    await mode.selectOption('compare'); await dismiss(); await create.click();
    check('comparison cannot start with zero local documents and only one allowed import', (await posts()).length === 7);
    await first.check(); await submit(8);
    check('comparison permits one selected document plus one explicitly allowed import', (await posts())[7].document_ids.length === 1 && (await posts())[7].agent_mode === 'compare' && (await posts())[7].agent_import_limit === 1);
    await subscription.selectOption('');
    check('clearing the last public source revokes import permission', !await allowImport.isChecked() && await allowImport.isDisabled());
    await query.fill('transformer'); await dismiss(); await create.click();
    check('query alone does not bypass comparison source-count requirement', (await posts()).length === 8);
    await subscription.selectOption('fixture-subscription-id'); await allowImport.check();
    await page.evaluate(() => {window.__failAgentSubscriptions = true;});
    await agent.getByRole('button',{name:'刷新订阅列表',exact:true}).click();
    await page.waitForFunction(() => document.querySelector('.rs-agent-options').textContent.includes('SYNTHETIC subscription-list failure'));
    check('failed subscription refresh clears unavailable IDs but preserves explicit query', await subscription.inputValue() === '' && await subscription.locator('option').count() === 1 && await query.inputValue() === 'transformer');
    await page.evaluate(() => {window.__failAgentSubscriptions = false;});
    await agent.getByRole('button',{name:'刷新订阅列表',exact:true}).click();
    await subscription.locator('option[value="fixture-subscription-id"]').waitFor({state:'attached'});
    check('subscription list supports explicit retry without automatically selecting one', await subscription.inputValue() === '');
    await mode.selectOption('qa'); await dismiss();
    await page.setViewportSize({width:390,height:844});
    await page.locator('.rs-agent-papers summary').click();
    await page.locator('.rs-agent-graphs summary').click();
    await page.evaluate(() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve))));
    check('agent form and expanded metadata/graph remain within narrow viewport', await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1));
    await page.screenshot({path:path.join(output,'agent-narrow-form.png'),fullPage:true});
    await kind.selectOption('review');
    await submit(9);
    await cards.nth(1).locator('summary').click();
    check('sectioned review keeps one final claim index across sections',
      (await cards.nth(1).locator('.rs-claim-audit').textContent()).includes('Own review 2') &&
      !(await cards.nth(1).locator('.rs-claim-audit').textContent()).includes('Own review 1'));
    check('no browser errors or real HTTP requests', report.pageErrors.length === 0 && report.networkRequests.length === 0, {pageErrors:report.pageErrors,networkRequests:report.networkRequests});
    report.payloads = await posts(); report.passed = true;
  } catch (error) {
    report.passed = false; report.failure = {message:error.message,stack:error.stack}; process.exitCode = 1;
    await page.screenshot({path:path.join(output,'failure.png'),fullPage:true}).catch(() => {});
  } finally {
    await page.evaluate(() => window.__agentController?.destroy()).catch(() => {});
    fs.writeFileSync(path.join(output,'report.json'),JSON.stringify(report,null,2),{flag:'wx'});
    await browser.close();
    console.log(JSON.stringify({passed:report.passed,output,checks:report.checks.length,failure:report.failure?.message},null,2));
  }
}
main().catch(error => {console.error(error);process.exitCode = 1;});
