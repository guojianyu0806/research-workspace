/* Synthetic translation fixture: tests browser synchronization, not model quality. */
const fs = require('node:fs/promises');
const path = require('node:path');
const assert = require('node:assert/strict');
const {chromium} = require(process.env.PLAYWRIGHT_MODULE || 'playwright');

(async () => {
  const output = path.resolve('artifacts/browser', `bilingual-${new Date().toISOString().replace(/[:.]/g,'-')}`);
  await fs.mkdir(output,{recursive:true});
  const browser = await chromium.launch({headless:true,executablePath:process.env.BROWSER_EXECUTABLE});
  const errors=[];
  const page = await browser.newPage({viewport:{width:1440,height:1000}});
  page.on('pageerror',error=>errors.push(error.message));
  const fixture = {status:'partial',target_language:'zh',total_blocks:21,missing_pages:[{page:8,status:'failed'}],
    blocks:Array.from({length:20},(_,i)=>({document_id:'synthetic-test-document',run_id:'synthetic-test-run',block_id:`block-${i}`,
      page:1+Math.floor(i/3),bbox:[0.1,0.1,0.8,0.3],source_text:`TEST FIXTURE paragraph ${i}. `+'A source paragraph with preserved numbers 42. '.repeat(4+i%4),
      translated_text:`合成测试译文第${i}段，数字42。`+'这段内容只用于验证滚动与原文回调，不是模型翻译结果。'.repeat(3+i%5)}))};
  try {
    await page.goto(process.env.BASE_URL || 'http://127.0.0.1:8766');
    await page.waitForFunction(()=>window.ResearchBilingual && window.ResearchWorkspace);
    await page.evaluate(fixture=>{
      document.querySelector('#workspace').hidden=true;
      document.querySelector('.mobile-navigation').hidden=true;
      const root=document.querySelector('#moduleRoot');root.hidden=false;
      window.smokeFixture=fixture;window.smokeEvidence=null;
      window.smokeBilingual=window.ResearchBilingual.render(root,fixture,{onEvidence:value=>{window.smokeEvidence=value;}});
    },fixture);
    await page.waitForFunction(()=>document.querySelectorAll('.rb-block').length===40);
    const move=async(side,id)=>{
      await page.evaluate(({side,id})=>{
        const pane=document.querySelector(`.rb-scroll[data-side="${side}"]`);
        pane.dispatchEvent(new Event('pointerdown'));
        pane.scrollTop=pane.querySelector(`[data-block-id="${id}"]`).offsetTop+20;
      },{side,id});
      await page.waitForFunction(id=>Array.from(document.querySelectorAll('.rb-block-active')).length===2
        && Array.from(document.querySelectorAll('.rb-block-active')).every(b=>b.dataset.blockId===id),id);
    };
    await move('source','block-7');
    await move('translation','block-12');
    await page.evaluate(()=>{
      window.smokeBilingual=window.ResearchBilingual.render(document.querySelector('#moduleRoot'),window.smokeFixture,
        {onEvidence:value=>{window.smokeEvidence=value;}});
    });
    assert.equal(await page.evaluate(()=>window.smokeBilingual.getPosition().block_id),'block-12');
    await page.locator('.rb-panel-source [data-block-id="block-12"] .rb-evidence').click();
    assert.equal(await page.evaluate(()=>window.smokeEvidence.run_id),'synthetic-test-run');
    assert.equal(await page.evaluate(()=>window.smokeEvidence.block_id),'block-12');
    await page.screenshot({path:path.join(output,'desktop.png')});
    await page.setViewportSize({width:390,height:844});
    await move('source','block-5');
    assert.equal(await page.locator('.rb-partial').isVisible(),true);
    assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1),true);
    await page.screenshot({path:path.join(output,'mobile.png')});
    assert.deepEqual(errors,[]);
    await fs.writeFile(path.join(output,'results.json'),JSON.stringify({status:'passed',fixture:'synthetic only; no model call',
      checks:['source-to-translation','translation-to-source','retain-block-on-render','version-bound-evidence-callback','partial-coverage','desktop-mobile'],errors},null,2),{flag:'wx'});
    console.log(output);
  } finally {await browser.close();}
})().catch(error=>{console.error(error);process.exitCode=1;});
