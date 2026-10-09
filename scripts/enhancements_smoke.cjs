/* Isolated local integration test. Synthetic PDF/CSV only; no model or real external search. */
'use strict';
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const crypto = require('node:crypto');
const { execFileSync } = require('node:child_process');
const { chromium } = require(process.env.PLAYWRIGHT_MODULE || 'C:/Users/g2806/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright');

async function main() {
  const base = process.env.BASE_URL || 'http://127.0.0.1:8766';
  assert.ok(['127.0.0.1','localhost'].includes(new URL(base).hostname));
  const root = path.resolve(__dirname,'..');
  const directory = path.join(root,'artifacts','browser','enhancements-'+new Date().toISOString().replace(/[:.]/g,'-')+'-'+crypto.randomBytes(3).toString('hex'));
  fs.mkdirSync(directory,{recursive:true});
  const report = {started_at:new Date().toISOString(),directory,checks:[],errors:[],external_requests:[],
    limitations:['PDF/CSV are deliberately synthetic fixtures, not competition evidence or real research.',
      'This browser check exercises local reference ranking only; external lookup is covered separately by offline protocol/UI fixtures.',
      'No paid model call, external search, or PDF download is performed.'],
    source:{git_head:execFileSync('git',['rev-parse','HEAD'],{cwd:root,encoding:'utf8',windowsHide:true}).trim(),sha256:{}}};
  for (const folder of ['research_workspace','research_workspace/static']) for (const name of fs.readdirSync(path.join(root,folder))) {
    if (/\.(py|js|html|css)$/.test(name)) report.source.sha256[folder+'/'+name] = crypto.createHash('sha256').update(fs.readFileSync(path.join(root,folder,name))).digest('hex');
  }
  const pdfPath = path.join(directory,'synthetic-layout-fixture.pdf');
  execFileSync(path.join(root,'.venv/Scripts/python.exe'),['-c',`
import pymupdf, sys
with pymupdf.open() as pdf:
    p = pdf.new_page(width=600,height=800)
    p.insert_text((40,40),'SYNTHETIC LAYOUT TEST FIXTURE',fontsize=20)
    p.insert_text((40,66),'Alice Smith, Bob Jones',fontsize=11)
    p.insert_text((40,87),'Example University',fontsize=10)
    p.insert_text((40,123),'1 Methods',fontsize=13)
    p.insert_text((40,147),'We use retrieval [1] in this experiment.',fontsize=10)
    p.insert_text((40,175),'x = y + 2',fontsize=11)
    p.insert_text((40,205),'Table 1. Synthetic scores',fontsize=10)
    for x in [40,150,260]: p.draw_line((x,220),(x,310))
    for y in [220,250,280,310]: p.draw_line((40,y),(260,y))
    for y,values in [(240,['Method','Score']),(270,['Alpha','10']),(300,['Beta','20'])]:
        for x,text in zip([50,160],values): p.insert_text((x,y),text,fontsize=10)
    pix = pymupdf.Pixmap(pymupdf.csRGB,pymupdf.IRect(0,0,8,8),False)
    pix.clear_with(110)
    p.insert_image(pymupdf.Rect(330,220,470,310),pixmap=pix)
    p.insert_text((330,330),'Figure 1. Synthetic image',fontsize=10)
    p = pdf.new_page(width=600,height=800)
    p.insert_text((40,50),'References',fontsize=13)
    p.insert_text((40,80),'[1] Alice. Retrieval Methods. 2024. arXiv:2401.12345v1.',fontsize=10)
    p.insert_text((40,110),'[2] Bob. Stellar Astronomy Catalogs. 2023.',fontsize=10)
    pdf.save(sys.argv[1])
`,pdfPath],{cwd:root,windowsHide:true});
  const browser = await chromium.launch({headless:true,executablePath:process.env.BROWSER_EXECUTABLE || 'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe'});
  const context = await browser.newContext({viewport:{width:1440,height:1000},acceptDownloads:true});
  const page = await context.newPage(); page.setDefaultTimeout(20000);
  page.on('pageerror',err=>report.errors.push(String(err)));
  page.on('console',msg=>{if(msg.type()==='error') report.errors.push(msg.text());});
  page.on('request',req=>{if(/^https?:/.test(req.url()) && new URL(req.url()).origin!==new URL(base).origin) report.external_requests.push(req.url());});
  const check = name=>{report.checks.push(name);console.log('PASS '+name);};
  async function shot(name) {await page.screenshot({path:path.join(directory,name+'.png'),fullPage:true});}
  async function manage(doc,tab) {
    await page.locator('[data-module="manage"]').click();
    await page.getByLabel('选择文献，含已归档文献').selectOption(doc.id);
    await page.getByRole('tab',{name:tab,exact:true}).click();
  }
  try {
    const imported = await context.request.post(base+'/api/documents',{multipart:{file:{name:'SYNTHETIC-layout-test.pdf',mimeType:'application/pdf',buffer:fs.readFileSync(pdfPath)}}});
    assert.ok(imported.ok()); const doc = (await imported.json()).document;
    let ready = false;
    for(let count=0;count<80;count++) {
      const result = await (await context.request.get(base+'/api/documents/'+doc.id)).json();
      if(result.document.available_run) {ready=true;break;}
      await new Promise(resolve=>setTimeout(resolve,250));
    }
    assert.ok(ready,'fixture parsed');
    await page.goto(base); await page.locator('#connectionState.is-connected').waitFor();
    await manage(doc,'版面结构');
    await page.getByRole('button',{name:'分析本页结构',exact:true}).click();
    await page.getByText('Example University',{exact:true}).first().waitFor();
    assert.ok((await page.locator('[role="tabpanel"]:visible').innerText()).includes('x = y + 2'));
    assert.ok((await page.locator('[role="tabpanel"]:visible').innerText()).includes('Alpha'));
    check('Native layout shows real author/affiliation, table, image, and formula candidates');
    await shot('01-layout');
    const downloadPromise = page.waitForEvent('download');
    await page.getByRole('link',{name:/下载.*JSON/}).click();
    const layoutDownload = await downloadPromise; await layoutDownload.saveAs(path.join(directory,'layout-export.json'));
    const exported = JSON.parse(fs.readFileSync(path.join(directory,'layout-export.json'),'utf8'));
    assert.equal(exported.document_id,doc.id); assert.equal(exported.tables[0].cells[3].text,'10');
    assert.equal(exported.figures.length,1); check('Layout JSON download retains immutable source identity and table values');
    await page.locator('[role="tabpanel"]:visible').getByRole('button',{name:/定位原文/}).first().click();
    await page.locator('[data-module="reader"][aria-pressed="true"]').waitFor();
    check('Layout candidate navigates to the original PDF reader');
    await manage(doc,'参考文献筛选');
    await page.locator('.rr-shell').waitFor();
    // Selectors below are based on the module's public accessible labels.
    await page.getByLabel('参考书目筛选词').fill('retrieval');
    await page.getByRole('button',{name:'应用本地筛选',exact:true}).click();
    await page.getByText(/保留 1 条/).first().waitFor();
    assert.equal(await page.locator('.rr-reference').count(),1);
    await page.getByLabel('显示暂筛出的书目（恢复到当前视图）').check();
    await page.waitForFunction(()=>document.querySelectorAll('.rr-reference').length===2);
    check('Reference filter is explainable and restores excluded entries');
    await shot('02-references');
    await page.setViewportSize({width:390,height:844});
    await shot('03-mobile-references');
    assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1),'mobile reference view fits viewport');
    await page.setViewportSize({width:1440,height:1000});
    await page.locator('[data-module="data"]').click();
    await page.getByLabel('选择CSV文件').setInputFiles({name:'SYNTHETIC-radar.csv',mimeType:'text/csv',buffer:Buffer.from('metric,A,B,C\naccuracy,70,80,90\nrecall,60,65,70\nspeed,30,50,80\nconstant,7,7,7')});
    const importedCsv = page.waitForResponse(response=>new URL(response.url()).pathname==='/api/datasets' && response.request().method()==='POST');
    await page.getByRole('button',{name:'导入CSV',exact:true}).click();
    assert.ok((await importedCsv).ok());
    await page.waitForFunction(()=>[...document.querySelectorAll('button')].find(button=>button.textContent==='导入CSV')?.disabled===false);
    await page.getByLabel('图表类型').selectOption('radar3d');
    await page.locator('.rs-y-choices input[value="A"]').check(); await page.locator('.rs-y-choices input[value="B"]').check(); await page.locator('.rs-y-choices input[value="C"]').check();
    await page.getByText('每个指标行在所选系列间按Min–Max归一化（常数指标取0.5）',{exact:true}).click();
    await page.getByLabel('雷达图方位角（度）').fill('-25');
    await page.getByLabel('雷达图仰角（度）').fill('50');
    await page.getByRole('button',{name:'生成图表与图注',exact:true}).click();
    await page.locator('.rs-chart-output:visible img').waitFor();
    assert.ok(await page.locator('.rs-chart-output img').evaluate(img=>img.complete&&img.naturalWidth>0));
    const radarDownloadPromise = page.waitForEvent('download');
    await page.getByRole('link',{name:'下载数据与转换记录',exact:true}).click();
    await (await radarDownloadPromise).saveAs(path.join(directory,'radar-export.json'));
    const radar = JSON.parse(fs.readFileSync(path.join(directory,'radar-export.json'),'utf8'));
    assert.equal(radar.spec.kind,'radar3d'); assert.equal(radar.spec.radar.camera.yaw_degrees,-25);
    assert.deepEqual(radar.spec.series[1].values,[.5,.5,.4,.5]);
    check('3D radar renders from CSV, preserves per-axis normalization, camera and original values');
    const svg = await (await context.request.get(base+'/api/charts/'+radar.id+'/svg')).text();
    fs.writeFileSync(path.join(directory,'radar.svg'),svg,{flag:'wx'});
    const exportPage = await context.newPage(); await exportPage.setViewportSize({width:1000,height:660});
    await exportPage.setContent('<html><body style="margin:0">'+svg+'</body></html>');
    await exportPage.screenshot({path:path.join(directory,'radar-standalone.png')}); await exportPage.close();
    await shot('04-radar');
    await page.setViewportSize({width:390,height:844}); await shot('05-mobile-radar');
    assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1),'mobile data view fits viewport');
    check('New management and radar controls fit mobile viewport');
    assert.deepEqual(report.errors,[]); assert.deepEqual(report.external_requests,[]); report.status='passed';
  } catch(error) {report.status='failed';report.failure=String(error.stack||error);await shot('failure').catch(()=>{});throw error;}
  finally {report.finished_at=new Date().toISOString();fs.writeFileSync(path.join(directory,'report.json'),JSON.stringify(report,null,2),{flag:'wx'});console.log(directory);await browser.close();}
}
main().catch(error=>{console.error(error);process.exitCode=1;});
