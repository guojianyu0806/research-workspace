/* Offline regression for extracted-text reflow, translation selection, in-page
 * source dialogs, and outline navigation. Uses existing Playwright/Edge; installs nothing.
 * All requests are fulfilled locally. No application service, credentials, model,
 * or persisted user data is read or changed. Each run keeps separate evidence.
 */
'use strict';
const fs = require('node:fs');
const path = require('node:path');
const crypto = require('node:crypto');
const assert = require('node:assert/strict');
const {execFileSync} = require('node:child_process');
const {chromium} = require(process.env.PLAYWRIGHT_MODULE || 'C:/Users/g2806/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright');

const root = path.resolve(__dirname, '..');
const origin = 'http://127.0.0.1:8765';
const output = path.join(root, 'artifacts/browser', `text-preview-${new Date().toISOString().replace(/[:.]/g, '-')}-${crypto.randomBytes(3).toString('hex')}`);
fs.mkdirSync(output, {recursive:true});
const report = {
  startedAt:new Date().toISOString(), output, mode:'offline synthetic mount harness',
  checks:[], screenshots:[], pageErrors:[], unexpectedRequests:[],
  source:{gitHead:execFileSync('git', ['rev-parse', 'HEAD'], {cwd:root, encoding:'utf8', windowsHide:true}).trim(), sha256:{}},
  limitations:['Synthetic source pages verify image geometry and UI behavior; this does not validate real PDF extraction or model translation quality.']
};
const names = ['notices.js','styles.css', 'shell.css', 'manage.css', 'modern-theme.css', 'manage.js'];
const assets = new Map(names.map(name => [name, fs.readFileSync(path.join(root, 'research_workspace/static', name))]));
for (const [name, content] of assets) report.source.sha256[name] = crypto.createHash('sha256').update(content).digest('hex');
// The harness mounts the module directly; record the normal entrypoint as well
// so the cache-version update is traceable alongside the tested source files.
report.source.sha256['index.html'] = crypto.createHash('sha256').update(fs.readFileSync(path.join(root,'research_workspace/static/index.html'))).digest('hex');
report.source.sha256['scripts/text_preview_smoke.cjs'] = crypto.createHash('sha256').update(fs.readFileSync(__filename)).digest('hex');
const sourceText = 'First source line\ncontinues on the same paragraph.';
const documents = ['alpha', 'beta'].map((name, index) => ({
  id:`fixture-${name}`, title:`SYNTHETIC ${name} · 原文重排与位置预览`, filename:`synthetic-${name}.pdf`,
  authors:['Fixture Author'], page_count:3, created_at:'2026-10-13T00:00:00Z',
  available_run:{id:`run-${name}`,version:1,status:'completed'}, current_run:{id:`run-${name}`,version:1,status:'completed'}
}));
function pageData(documentId, page) {
  const name = documentId.endsWith('beta') ? 'beta' : 'alpha';
  const texts = page === 1 ? [sourceText, 'Second block with punctuation, quoted “words”, and 中文原文。\nLongIdentifier_ABCDEFGHIJKLMNOPQRSTUVWXYZ_0123456789_ABCDEFGHIJKLMNOPQRSTUVWXYZ_0123456789'] : [`${name} page ${page} source\ncontinued paragraph.`, `Another ${name} page ${page} block.`];
  return {document_id:documentId, run_id:`run-${name}`, page, status:'completed', text:texts.join('\n\n'), blocks:texts.map((text, index) => ({
    id:`${name}-${page}-${index}`, ordinal:index, page, text, bbox:index === 0 ? [.1,.15,.85,.30] : page === 3 ? null : [.12,.5,.88,.72]
  }))};
}
function structureData(documentId) {
  const name = documentId.endsWith('beta') ? 'beta' : 'alpha';
  const evidence = (page, extra = {}) => ({document_id:documentId,run_id:`run-${name}`,page,...extra});
  return {headings:[
    {id:`${name}-heading-first`,title:'Fixture opening block',level:1,evidence:evidence(1,{block_id:`${name}-1-0`,bbox:[.1,.15,.85,.3]})},
    {id:`${name}-heading-second`,title:'Fixture second-page block',level:2,evidence:evidence(2,{block_id:`${name}-2-1`,bbox:[.12,.5,.88,.72]})},
    {id:`${name}-heading-box`,title:'Fixture coordinate-only heading',level:2,evidence:evidence(2,{bbox:[.1,.15,.85,.3]})},
    {id:`${name}-heading-unmatched`,title:'Fixture page without reliable block',level:1,evidence:evidence(3,{bbox:[.9,.9,.99,.99]})},
  ],references:[],citations:[],limitations:[]};
}
function pageSvg(label) {
  return `<svg xmlns="http://www.w3.org/2000/svg" width="600" height="800" viewBox="0 0 600 800"><rect width="600" height="800" fill="white"/><text x="45" y="50" font-size="21" fill="#173e41">SYNTHETIC ${label}</text>${Array.from({length:20}, (_,i) => `<rect x="60" y="${100+i*25}" width="${430-i%3*20}" height="6" fill="#526875"/>`).join('')}</svg>`;
}
const harness = `<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">${names.filter(name => name.endsWith('.css')).map(name => `<link rel="stylesheet" href="/static/${name}">`).join('')}</head><body><main id="moduleRoot" class="module-root"></main><script src="/static/notices.js"></script><script src="/static/manage.js"></script><script>
window.fixtureTranslations=[];window.fixtureEvidence=[];
window.fixtureController=ResearchManage.mount(document.querySelector('#moduleRoot'),{
 api:async(url,init)=>{if(init?.method && init.method!=='GET')throw new Error('Fixture forbids writes');const r=await fetch(url);if(!r.ok)throw new Error('Fixture request failed');return r.json();},
 onTranslate:payload=>{window.fixtureTranslations.push(structuredClone(payload));},
 onEvidence:payload=>{window.fixtureEvidence.push(structuredClone(payload));}
});window.fixtureReady=window.fixtureController.ready;
</script></body></html>`;

function check(name, actual, detail) {
  report.checks.push({name, passed:Boolean(actual), ...(detail === undefined ? {} : {detail})});
  assert.ok(actual, name + (detail === undefined ? '' : ': '+JSON.stringify(detail)));
}
let hold = null;
let failNextImage = false;
const pageRequests = [];
const pending = [];
async function main() {
  const browser = await chromium.launch({headless:true, executablePath:process.env.BROWSER_EXECUTABLE || 'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe'});
  const context = await browser.newContext({viewport:{width:1440,height:1000}, reducedMotion:'reduce'});
  const page = await context.newPage();
  page.setDefaultTimeout(10000);
  page.on('pageerror', error => report.pageErrors.push(String(error)));
  await context.route('**/*', async route => {
    const request = route.request(), url = new URL(request.url()), p = url.pathname;
    if (url.origin !== origin || !['GET','HEAD'].includes(request.method())) {
      report.unexpectedRequests.push({method:request.method(),url:request.url()});
      return route.abort();
    }
    if (p === '/') return route.fulfill({status:200,contentType:'text/html; charset=utf-8',body:harness});
    if (p.startsWith('/static/')) {
      const name = path.basename(p);
      if (!assets.has(name)) {report.unexpectedRequests.push({path:p});return route.abort();}
      return route.fulfill({status:200,contentType:name.endsWith('.css')?'text/css; charset=utf-8':'text/javascript; charset=utf-8',body:assets.get(name)});
    }
    let response;
    if (p === '/api/documents') response = {documents};
    else if (p.endsWith('/versions')) response = {family:{documents:[]},candidates:[]};
    else if (p.endsWith('/structure')) response = structureData(p.split('/')[3]);
    else {
      const match = p.match(/^\/api\/documents\/(fixture-(?:alpha|beta))\/pages\/(\d+)(\/image)?$/);
      if (match) {
        const [,documentId,number,image] = match;
        if (!image) pageRequests.push({documentId,page:Number(number),runId:url.searchParams.get('run_id')});
        const send = () => {
          if (image) {
            if (failNextImage) {failNextImage=false;return route.fulfill({status:500,contentType:'text/plain',body:'Synthetic image failure'});}
            return route.fulfill({status:200,contentType:'image/svg+xml',body:pageSvg(documentId+' page '+number),headers:{'cache-control':'no-store'}});
          }
          return route.fulfill({status:200,contentType:'application/json',body:JSON.stringify(pageData(documentId,Number(number)))});
        };
        if (hold?.({documentId,page:Number(number),image:Boolean(image)})) {
          pending.push({documentId,page:Number(number),image:Boolean(image),send});
          return;
        }
        return send();
      }
      report.unexpectedRequests.push({path:p}); return route.abort();
    }
    return route.fulfill({status:200,contentType:'application/json; charset=utf-8',body:JSON.stringify(response)});
  });
  const blocks = () => page.locator('.rm-text-block');
  async function clearSelection() {
    await page.evaluate(() => {window.getSelection().removeAllRanges();document.dispatchEvent(new Event('selectionchange'));});
  }
  async function chooseRange(startBlock,start,endBlock,end) {
    return page.evaluate(({startBlock,start,endBlock,end}) => {
      const elements = [...document.querySelectorAll('.rm-block-text')];
      const range = document.createRange(); range.setStart(elements[startBlock].firstChild,start);range.setEnd(elements[endBlock].firstChild,end);
      const selected = window.getSelection();selected.removeAllRanges();selected.addRange(range);document.dispatchEvent(new Event('selectionchange'));
      return {range:range.toString(),rendered:selected.toString()};
    }, {startBlock,start,endBlock,end});
  }
  async function read(number) {
    await page.getByRole('spinbutton',{name:'原文页码'}).fill(String(number));
    await page.getByRole('button',{name:'读取本页文字',exact:true}).click();
  }
  async function preview(index = 0) {
    await blocks().nth(index).getByRole('button',{name:'预览位置',exact:true}).click();
    await page.locator('.rm-page-preview').waitFor({state:'visible'});
  }
  async function imageReady() {
    await page.waitForFunction(() => {const image=document.querySelector('.rm-preview-image');return image && image.complete && image.naturalWidth > 0 && !image.closest('[hidden]');});
    await page.locator('.rm-preview-highlight').waitFor({state:'visible'});
  }
  const dialog = () => page.getByRole('dialog',{name:'原页放大预览',exact:true});
  async function openSource() {
    await page.getByRole('button',{name:'放大查看原页',exact:true}).click();
    await dialog().waitFor({state:'visible'});
  }
  async function sourceReady() {
    await page.waitForFunction(() => {const image=document.querySelector('.rm-source-dialog[open] .rm-source-image');return image && image.complete && image.naturalWidth>0 && !image.hidden;});
  }
  async function openReaderFromPreview() {
    await openSource();
    await dialog().getByRole('button',{name:'在阅读器中打开原文',exact:true}).click();
    await dialog().waitFor({state:'hidden'});
  }
  async function expectPage(documentId,number) {
    const expected = pageData(documentId,number).blocks[0].text;
    await page.waitForFunction(expected => document.querySelector('.rm-block-text')?.textContent === expected, expected);
  }
  async function releaseAll() {
    hold = null;
    const releases = pending.splice(0);
    await Promise.all(releases.map(item => item.send().catch(error => {if(!/closed|handled|Invalid InterceptionId/.test(String(error)))throw error;})));
  }
  async function waitPending() {
    for(let count=0;count<100 && !pending.length;count++) await page.waitForTimeout(20);
    check('delayed fixture request intercepted',pending.length>0,pending.map(({send,...item})=>item));
  }
  try {
    await page.goto(origin);
    await page.evaluate(() => window.fixtureReady);
    await page.getByRole('tab',{name:'本页文字',exact:true}).click();
    await expectPage('fixture-alpha',1);
    const initial = await blocks().first().locator('.rm-block-text').evaluate(el => ({text:el.textContent,nodes:el.childNodes.length,whiteSpace:getComputedStyle(el).whiteSpace,height:el.getBoundingClientRect().height,lineHeight:parseFloat(getComputedStyle(el).lineHeight)}));
    check('reflow keeps one unchanged source TextNode',initial.text === sourceText && initial.nodes === 1,initial);
    check('reflow removes source hard line wrapping',initial.whiteSpace === 'normal' && initial.height < initial.lineHeight*1.9,initial);
    await page.getByRole('checkbox',{name:'保留原文换行',exact:true}).check();
    const wrapped = await blocks().first().locator('.rm-block-text').evaluate(el => ({text:el.textContent,whiteSpace:getComputedStyle(el).whiteSpace,height:el.getBoundingClientRect().height}));
    check('optional original-line mode keeps source newlines',wrapped.text === sourceText && wrapped.whiteSpace === 'pre-wrap' && wrapped.height > initial.height,wrapped);
    await page.getByRole('checkbox',{name:'保留原文换行',exact:true}).uncheck();
    const selected = await chooseRange(0,6,0,34);
    check('fixture range crosses an original newline',selected.range === sourceText.slice(6,34) && selected.range.includes('\n'),selected);
    await preview(0);
    check('preview click retains exact selected source text',await page.evaluate(() => window.getSelection().getRangeAt(0).toString()) === sourceText.slice(6,34));
    await blocks().first().getByRole('button',{name:'翻译所选文字',exact:true}).click();
    let translations = await page.evaluate(() => window.fixtureTranslations);
    check('reflow translation submits exact source substring',translations.length === 1 && translations[0].selected_text === sourceText.slice(6,34),translations);
    check('translation carries source document run and block',translations[0].document_id === 'fixture-alpha' && translations[0].run_id === 'run-alpha' && translations[0].block_ids.join(',') === 'alpha-1-0',translations[0]);
    await clearSelection();
    await blocks().first().getByRole('button',{name:'翻译整块',exact:true}).click();
    translations = await page.evaluate(() => window.fixtureTranslations);
    check('whole-block translation has no selected_text',translations.length === 2 && !Object.hasOwn(translations[1],'selected_text'),translations[1]);
    await chooseRange(0,2,1,12);
    await blocks().first().getByRole('button',{name:'翻译整块',exact:true}).click();
    check('cross-block selections are rejected',await page.evaluate(() => window.fixtureTranslations.length) === 2 && await page.locator('.rm-selection-hint').evaluate(el=>el.classList.contains('is-error')));
    await clearSelection();
    await chooseRange(1,2,1,14);
    await blocks().first().getByRole('button',{name:'翻译整块',exact:true}).click();
    check('translation of a different selected block is rejected',await page.evaluate(() => window.fixtureTranslations.length) === 2);
    await clearSelection();
    await page.evaluate(() => {
      const text = document.querySelector('.rm-block-text').firstChild;
      window.getSelection().setBaseAndExtent(text,34,text,6);
      document.dispatchEvent(new Event('selectionchange'));
    });
    await blocks().first().getByRole('button',{name:'翻译所选文字',exact:true}).click();
    translations = await page.evaluate(() => window.fixtureTranslations);
    check('backward selection preserves the original newline substring',translations.length === 3 && translations[2].selected_text === sourceText.slice(6,34),translations[2]);
    await clearSelection();
    check('translation controls are in block headings',await blocks().evaluateAll(elements => elements.every(el => el.querySelector('.rm-block-actions')?.closest('.rm-text-block-heading') && [...el.querySelectorAll('.rm-primary')].every(button => button.closest('.rm-text-block-heading')))));

    await preview(0); await imageReady();
    const box = await page.locator('.rm-preview-highlight').evaluate(el=>({left:parseFloat(el.style.left),top:parseFloat(el.style.top),width:parseFloat(el.style.width),height:parseFloat(el.style.height)}));
    check('first block normalized bbox matches highlight',Math.abs(box.left-10)<.01 && Math.abs(box.top-15)<.01 && Math.abs(box.width-75)<.01 && Math.abs(box.height-15)<.01,box);
    check('preview caption identifies page and block',(await page.locator('.rm-preview-caption').textContent()).includes('第 1 页 · 文本块 1'));
    await page.getByRole('button',{name:'放大查看原页',exact:true}).focus();
    await chooseRange(0,6,0,34);
    const beforeSource = await page.evaluate(() => ({scroll:document.querySelector('#moduleRoot').scrollTop,evidence:window.fixtureEvidence.length,url:location.href,selection:window.getSelection().getRangeAt(0).toString()}));
    await openSource();await sourceReady();
    check('thumbnail opens a local dialog without navigating or emitting reader evidence',await page.evaluate(before => window.fixtureEvidence.length===before.evidence && location.href===before.url,beforeSource));
    check('source dialog locks background module scrolling',await page.locator('#moduleRoot').evaluate(el=>getComputedStyle(el).overflowY)==='hidden');
    const sourceBox = await page.locator('.rm-source-highlight').evaluate(el=>({left:parseFloat(el.style.left),top:parseFloat(el.style.top),width:parseFloat(el.style.width),height:parseFloat(el.style.height),visible:!el.hidden}));
    check('large source image highlights the same normalized block',sourceBox.visible && Math.abs(sourceBox.left-10)<.01 && Math.abs(sourceBox.top-15)<.01 && Math.abs(sourceBox.width-75)<.01 && Math.abs(sourceBox.height-15)<.01,sourceBox);
    check('large source image identifies the original page',(await dialog().locator('.rm-source-image').getAttribute('src')).includes('/fixture-alpha/pages/1/image'));
    await dialog().getByRole('combobox',{name:'预览缩放',exact:true}).selectOption('2');
    await dialog().getByRole('combobox',{name:'预览缩放',exact:true}).selectOption('page');
    check('source zoom accepts magnified and fit-page views',await dialog().getByRole('combobox',{name:'预览缩放',exact:true}).inputValue()==='page');
    await page.keyboard.press('Tab');
    check('keyboard focus stays inside the modal',await dialog().evaluate(el=>el.contains(document.activeElement)));
    await dialog().getByRole('button',{name:'关闭预览',exact:true}).click();
    await dialog().waitFor({state:'hidden'});
    const afterSource = await page.evaluate(() => ({scroll:document.querySelector('#moduleRoot').scrollTop,overflow:getComputedStyle(document.querySelector('#moduleRoot')).overflowY,focused:document.activeElement===document.querySelector('.rm-preview-page'),selection:window.getSelection().rangeCount ? window.getSelection().getRangeAt(0).toString() : ''}));
    check('closing source returns to the unchanged text selection and scroll position',Math.abs(afterSource.scroll-beforeSource.scroll)<=1 && afterSource.selection===beforeSource.selection && afterSource.overflow!=='hidden',afterSource);
    check('closing source restores thumbnail keyboard focus',afterSource.focused,afterSource);
    await openSource();await sourceReady();await page.keyboard.press('Escape');
    await dialog().waitFor({state:'hidden'});
    check('Escape closes the source dialog and releases background scroll',await page.locator('#moduleRoot').evaluate(el=>getComputedStyle(el).overflowY)!=='hidden');
    await clearSelection();
    await openReaderFromPreview();
    const evidence = await page.evaluate(() => window.fixtureEvidence.at(-1));
    check('explicit dialog action opens matching reader evidence and closes modal',evidence.document_id === 'fixture-alpha' && evidence.run_id === 'run-alpha' && evidence.page === 1 && evidence.block_id === 'alpha-1-0' && JSON.stringify(evidence.bbox) === JSON.stringify([.1,.15,.85,.3]) && !await dialog().isVisible(),evidence);
    await preview(1);await imageReady();
    check('same-page switch updates caption and highlight',(await page.locator('.rm-preview-caption').textContent()).includes('文本块 2') && Math.abs(await page.locator('.rm-preview-highlight').evaluate(el=>parseFloat(el.style.top))-50)<.01);

    // Defer one image, switch blocks before it arrives, and verify newest bbox.
    hold = item => item.documentId === 'fixture-alpha' && item.page === 2 && item.image;
    await read(2);await expectPage('fixture-alpha',2);await preview(0);await waitPending();
    await preview(1);await releaseAll();await imageReady();
    check('late same-page image preserves latest block selection',(await page.locator('.rm-preview-caption').textContent()).includes('第 2 页 · 文本块 2') && Math.abs(await page.locator('.rm-preview-highlight').evaluate(el=>parseFloat(el.style.top))-50)<.01);

    // Force a fresh page request to complete only after a document switch.
    hold = item => item.documentId === 'fixture-alpha' && item.page === 3 && !item.image;
    await read(3);await waitPending();
    check('loading another page preserves old preview while freezing stale actions',await page.locator('.rm-preview-highlight:visible').count() === 1 && await page.locator('.rm-page-blocks').evaluate(el=>el.inert && el.getAttribute('aria-busy')==='true') && (await page.locator('.rm-preview-caption').textContent()).includes('第 2 页'));
    await page.getByRole('combobox',{name:'选择文献，含已归档文献'}).selectOption('fixture-beta');
    await expectPage('fixture-beta',1);await preview(0);await imageReady();
    await releaseAll();
    check('stale page response cannot replace new document',await blocks().first().locator('.rm-block-text').textContent() === pageData('fixture-beta',1).blocks[0].text && (await page.locator('.rm-preview-image').getAttribute('src')).includes('/fixture-beta/'));

    // Broken image is a recoverable preview failure, not loss of text/translation.
    failNextImage = true;
    await read(2);await expectPage('fixture-beta',2);await preview(0);
    await page.getByRole('button',{name:'重试预览',exact:true}).waitFor({state:'visible'});
    check('image failure hides stale highlight',await page.locator('.rm-preview-highlight:visible').count() === 0);
    check('image failure keeps text and translation available',await blocks().count() === 2 && await blocks().first().getByRole('button',{name:'翻译整块',exact:true}).isEnabled());
    check('image failure exposes readable status',Boolean((await page.locator('.rm-preview-status').textContent()).trim()));
    await page.getByRole('button',{name:'重试预览',exact:true}).click();await imageReady();
    check('retry restores preview highlight',await page.locator('.rm-preview-highlight').isVisible());

    // The old image can finish after a different document has already rendered.
    hold = item => item.documentId === 'fixture-beta' && item.page === 3 && item.image;
    await read(3);await expectPage('fixture-beta',3);await preview(0);await waitPending();
    await page.getByRole('combobox',{name:'选择文献，含已归档文献'}).selectOption('fixture-alpha');
    await expectPage('fixture-alpha',1);await preview(1);await imageReady();
    await releaseAll();
    check('stale image cannot overwrite another document preview',(await page.locator('.rm-preview-image').getAttribute('src')).includes('/fixture-alpha/') && (await page.locator('.rm-preview-caption').textContent()).includes('第 1 页 · 文本块 2') && Math.abs(await page.locator('.rm-preview-highlight').evaluate(el=>parseFloat(el.style.top))-50)<.01);

    await read(3);await expectPage('fixture-alpha',3);await preview(1);
    await page.waitForFunction(() => {const image=document.querySelector('.rm-preview-image');return image?.complete && image.naturalWidth>0;});
    check('missing coordinates show full page without a fabricated highlight',await page.locator('.rm-preview-highlight:visible').count() === 0 && await page.locator('.rm-preview-image').isVisible() && (await page.locator('.rm-preview-status').textContent()).includes('缺少可靠坐标'));
    await openSource();await sourceReady();
    check('missing coordinates remain unhighlighted in the large source dialog',await page.locator('.rm-source-highlight:visible').count()===0);
    await dialog().getByRole('button',{name:'在阅读器中打开原文',exact:true}).click();
    await dialog().waitFor({state:'hidden'});
    const noBoxEvidence = await page.evaluate(() => window.fixtureEvidence.at(-1));
    check('missing coordinates still open the correct source page',noBoxEvidence.document_id === 'fixture-alpha' && noBoxEvidence.page === 3 && noBoxEvidence.block_id === 'alpha-3-1' && noBoxEvidence.bbox === null,noBoxEvidence);

    // Outline links locate extracted text in the same module, never the reader.
    const evidenceBeforeOutline = await page.evaluate(() => window.fixtureEvidence.length);
    const requestsBeforeOutline = pageRequests.length;
    await page.getByRole('tab',{name:'标题与引用',exact:true}).click();
    await page.getByRole('tabpanel',{name:'标题与引用',exact:true}).getByRole('button',{name:'Fixture second-page block',exact:true}).click();
    await expectPage('fixture-alpha',2);
    check('structure outline switches to text tab without opening reader',await page.getByRole('tab',{name:'本页文字',exact:true}).getAttribute('aria-selected')==='true' && await page.evaluate(()=>window.fixtureEvidence.length)===evidenceBeforeOutline);
    await page.waitForFunction(expected => document.activeElement?.textContent===expected,pageData('fixture-alpha',2).blocks[1].text);
    check('outline exact block receives text focus and matching preview',(await page.locator('.rm-preview-caption').textContent()).includes('第 2 页 · 文本块 2'));
    check('outline page request carries document and parse-run identity',pageRequests.slice(requestsBeforeOutline).some(item=>item.documentId==='fixture-alpha' && item.page===2 && item.runId==='run-alpha'),pageRequests.slice(requestsBeforeOutline));
    const textOutline = page.locator('.rm-text-outline');
    if (!await textOutline.evaluate(el=>el.open)) await textOutline.locator('summary').click();
    await textOutline.getByRole('button',{name:'Fixture coordinate-only heading',exact:true}).click();
    await page.waitForFunction(expected => document.activeElement?.textContent===expected,pageData('fixture-alpha',2).blocks[0].text);
    check('coordinate-only outline safely resolves corresponding source block',(await page.locator('.rm-preview-caption').textContent()).includes('文本块 1'));
    if (!await textOutline.evaluate(el=>el.open)) await textOutline.locator('summary').click();
    await textOutline.getByRole('button',{name:'Fixture page without reliable block',exact:true}).click();
    await expectPage('fixture-alpha',3);
    check('unmatched outline opens page without inventing a block highlight',await page.locator('.rm-preview-highlight:visible').count()===0 && await page.locator('.rm-text-block.is-previewed').count()===0);
    check('outline navigation never invokes reader callback',await page.evaluate(()=>window.fixtureEvidence.length)===evidenceBeforeOutline);

    // A broken high-resolution image can be retried without losing extracted text.
    // Use a never-opened large-image URL to avoid decoded-image memory reuse.
    await page.getByRole('combobox',{name:'选择文献，含已归档文献'}).selectOption('fixture-beta');
    await expectPage('fixture-beta',1);await read(2);await expectPage('fixture-beta',2);
    await preview(0);await imageReady();
    failNextImage = true;
    await openSource();
    await dialog().getByRole('button',{name:'重试大图',exact:true}).waitFor({state:'visible'});
    check('large-image failure hides stale coordinate overlays',await page.locator('.rm-source-highlight:visible').count()===0);
    await dialog().getByRole('button',{name:'重试大图',exact:true}).click();await sourceReady();
    check('large-image retry restores original page and coordinates',await page.locator('.rm-source-highlight').isVisible());
    await dialog().getByRole('button',{name:'关闭预览',exact:true}).click();
    await dialog().waitFor({state:'hidden'});
    check('large-image failure and retry preserve extracted source text',await blocks().first().locator('.rm-block-text').textContent()===pageData('fixture-beta',2).blocks[0].text);

    // A late large-image response cannot reopen the dialog after navigation.
    await read(3);await expectPage('fixture-beta',3);await preview(0);await imageReady();
    hold = item=>item.documentId==='fixture-beta' && item.page===3 && item.image;
    await openSource();await waitPending();
    await page.keyboard.press('Escape');await dialog().waitFor({state:'hidden'});
    await page.getByRole('combobox',{name:'选择文献，含已归档文献'}).selectOption('fixture-alpha');
    await expectPage('fixture-alpha',1);await preview(0);await imageReady();
    await releaseAll();
    check('late large image cannot reopen a closed dialog or replace another document',await page.locator('dialog[open]').count()===0 && (await page.locator('.rm-preview-image').getAttribute('src')).includes('/fixture-alpha/') && await page.locator('#moduleRoot').evaluate(el=>getComputedStyle(el).overflowY)!=='hidden');

    // Reload to leave screenshots in a clean normal state after rejection tests.
    await page.reload();await page.evaluate(() => window.fixtureReady);
    await page.getByRole('tab',{name:'本页文字',exact:true}).click();
    await expectPage('fixture-alpha',1);await preview(0);await imageReady();
    for (const width of [1440,1024,602,390,320]) {
      await page.setViewportSize({width,height:1000});
      await blocks().first().scrollIntoViewIfNeeded();
      const layout = await page.evaluate(() => {
        const visible = el => el.getClientRects().length && !el.closest('[hidden]');
        const overlaps=[];const escaped=[];
        for(const article of document.querySelectorAll('.rm-text-block')) {
          const text=article.querySelector('.rm-block-text').getBoundingClientRect();
          for(const button of article.querySelectorAll('button')) {const r=button.getBoundingClientRect();if(Math.min(r.right,text.right)-Math.max(r.left,text.left)>1 && Math.min(r.bottom,text.bottom)-Math.max(r.top,text.top)>1)overlaps.push(button.textContent);}
        }
        for(const el of document.querySelectorAll('.rm-page-form,.rm-text-block,.rm-text-block button,.rm-preview-image,.rm-page-preview,.rm-block-text')) {if(!visible(el))continue;const r=el.getBoundingClientRect();if(r.left < -1 || r.right > innerWidth+1)escaped.push({selector:el.className,left:r.left,right:r.right});}
        return {viewport:innerWidth,scrollWidth:document.documentElement.scrollWidth,moduleScrollWidth:document.querySelector('#moduleRoot').scrollWidth,moduleWidth:document.querySelector('#moduleRoot').clientWidth,overlaps,escaped};
      });
      check(`responsive ${width}: no horizontal overflow or controls covering text`,layout.scrollWidth <= width+1 && layout.moduleScrollWidth <= layout.moduleWidth+1 && !layout.overlaps.length && !layout.escaped.length,layout);
      const filename = `text-preview-${width}.png`;await page.screenshot({path:path.join(output,filename),fullPage:false});report.screenshots.push({width,path:path.join(output,filename)});
      await openSource();await sourceReady();
      for (const zoom of ['1','2','page']) {
        await dialog().getByRole('combobox',{name:'预览缩放',exact:true}).selectOption(zoom);
        const sourceLayout = await dialog().evaluate(el=>{
          const bounds=el.getBoundingClientRect();
          const controls=[...el.querySelectorAll('button,select')].filter(control=>control.getClientRects().length && !control.closest('[hidden]')).map(control=>{const r=control.getBoundingClientRect();return {name:control.getAttribute('aria-label')||control.textContent,left:r.left,right:r.right,top:r.top,bottom:r.bottom};});
          return {left:bounds.left,right:bounds.right,top:bounds.top,bottom:bounds.bottom,viewport:innerWidth,height:innerHeight,controls};
        });
        check(`source dialog ${width} zoom ${zoom}: frame and controls stay in viewport`,sourceLayout.left>=-1 && sourceLayout.right<=width+1 && sourceLayout.top>=-1 && sourceLayout.bottom<=sourceLayout.height+1 && sourceLayout.controls.every(control=>control.left>=-1 && control.right<=width+1 && control.top>=-1 && control.bottom<=sourceLayout.height+1),sourceLayout);
      }
      const modalFilename = `source-dialog-${width}.png`;await page.screenshot({path:path.join(output,modalFilename),fullPage:false});report.screenshots.push({width,path:path.join(output,modalFilename)});
      await page.keyboard.press('Escape');await dialog().waitFor({state:'hidden'});
    }
    await openSource();await sourceReady();
    await page.evaluate(()=>window.fixtureController.destroy());
    check('module teardown closes dialog and releases background scroll',await page.locator('dialog[open]').count()===0 && await page.locator('#moduleRoot').evaluate(el=>getComputedStyle(el).overflowY)!=='hidden');
    check('no browser script errors',report.pageErrors.length === 0,report.pageErrors);
    check('no unmatched/external/write requests',report.unexpectedRequests.length === 0,report.unexpectedRequests);
  } catch (error) {
    report.failure = String(error.stack || error);
    process.exitCode = 1;
    try {await page.screenshot({path:path.join(output,'failure.png')});} catch {}
  } finally {
    await releaseAll();
    report.finishedAt = new Date().toISOString();
    report.passed = !report.failure && report.checks.every(item => item.passed);
    fs.writeFileSync(path.join(output,'report.json'),JSON.stringify(report,null,2)+'\n');
    console.log(JSON.stringify({passed:report.passed,checks:report.checks.length,failed:report.checks.filter(item=>!item.passed),output,error:report.failure},null,2));
    await browser.close();
  }
}
main().catch(error => {console.error(error);process.exitCode=1;});
