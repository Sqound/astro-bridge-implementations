import assert from 'node:assert/strict';
import {pathToFileURL} from 'node:url';
import {resolve} from 'node:path';

// The writing of a card is one bar in five segments sized by typical duration,
// with "Phase N of 5 . name" above it. Setup's own files, a simulated write, no card and no network.
const {chromium}=await import(process.env.PLAYWRIGHT_MODULE?pathToFileURL(process.env.PLAYWRIGHT_MODULE).href:'playwright');
const locales=['en','fr','de','es','it','pt-BR','ru','ja','zh-Hans','zh-Hant'];
const keys=['flashTitle','phaseCount','flashErrorAt','phaseName1','phaseName2','phaseName3','phaseName4','phaseName5'];
const browser=await chromium.launch({headless:true,args:['--use-mock-keychain','--password-store=basic'],...process.env.CHROME_PATH?{executablePath:process.env.CHROME_PATH}:{}});
const writing=percent=>({phase:'writing',message:`Writing: [${'='.repeat(Math.round(percent/5))}${' '.repeat(20-Math.round(percent/5))}] ${percent} %`});
const verifying=percent=>({phase:'flash_verifying',message:`Verifying: [${'='.repeat(Math.round(percent/5))}${' '.repeat(20-Math.round(percent/5))}] ${percent} %`});

async function open(locale,width){
  const page=await browser.newPage({viewport:{width,height:900},locale:'en-US'});const errors=[];page.on('pageerror',e=>errors.push(e.message));
  await page.addInitScript(locale=>{localStorage.setItem('astro-bridge-setup.locale',locale);window.__TAURI__={core:{Channel:class {},invoke:async (command,args)=>{switch(command){
    case 'resolve_release':return {target:args.target,source:'published',wifi_seed:true,password_seed:true,driver_drop:true,tag:'test',asset:'fixture.img.xz',size:3,sha256:'a'.repeat(64)};
    case 'download_release':return {name:'fixture.img.xz'};
    case 'list_media':return [{id:'test-media',name:'Test card',size:32000000000}];
    case 'flash_media':return await new Promise((resolve,reject)=>{window.emitFlash=event=>args.onProgress.onmessage(event);window.finishFlash=()=>resolve(null);window.failFlash=message=>reject(new Error(message));});
    default:throw new Error('unexpected '+command);}}}};},locale);
  await page.goto(pathToFileURL(resolve('tools/astro-bridge-setup/ui/index.html')).href);
  const catalog=await page.evaluate(locale=>window.SetupI18n.catalogs[locale],locale);
  await page.getByRole('button',{name:catalog.start,exact:true}).click();await page.locator('#target-pi-zero-w').check();
  await page.getByRole('button',{name:catalog.findRelease,exact:true}).click();await page.getByRole('button',{name:catalog.download,exact:true}).click();
  await page.getByRole('button',{name:catalog.continue,exact:true}).click();await page.getByText('Test card').click();
  await page.getByRole('button',{name:catalog.reviewErase}).click();await page.locator('#erase-dialog input').check();await page.getByRole('button',{name:catalog.eraseWrite}).click();
  await page.waitForFunction(()=>typeof window.emitFlash==='function');
  const emit=event=>page.evaluate(event=>window.emitFlash(event),event);
  const bar=()=>page.locator('#flash-progress').evaluate(e=>({now:e.getAttribute('aria-valuenow'),text:e.getAttribute('aria-valuetext'),fills:[...e.children].map(s=>s.firstElementChild.style.width),widths:[...e.children].map(s=>s.getBoundingClientRect().width),weights:[...e.children].map(s=>Number(s.dataset.weight))}));
  const line=()=>page.locator('#phase-text').textContent();
  return {page,catalog,emit,bar,line,errors};
}
const rankLine=(catalog,rank)=>catalog.phaseCount.replace('{n}',rank).replace('{total}','5')+' · '+catalog['phaseName'+rank];

try {
  // Ten languages: every key present, no semicolon, no em dash.
  {const page=await browser.newPage();await page.goto(pathToFileURL(resolve('tools/astro-bridge-setup/ui/index.html')).href);
   const catalogs=await page.evaluate(()=>window.SetupI18n.catalogs);assert.deepEqual(Object.keys(catalogs).sort(),[...locales].sort());
   for(const locale of locales)for(const key of keys){const text=catalogs[locale][key];assert.ok(text&&text.trim(),locale+' '+key);assert.ok(!/[;—]/.test(text),locale+' '+key+' punctuation');}
   for(const locale of locales){assert.match(catalogs[locale].phaseCount,/\{n\}/);assert.match(catalogs[locale].phaseCount,/\{total\}/);assert.match(catalogs[locale].flashErrorAt,/\{name\}/);}
   await page.close();}

  // Rank, bar, no going back, skipped phase, collapsed details, proportional segments, in ten languages at 700 and 375.
  for(const locale of locales)for(const width of [700,375]){
    const {page,catalog,emit,bar,line,errors}=await open(locale,width);
    await emit({phase:'preparing',message:'Checking image integrity'});assert.equal(await line(),rankLine(catalog,1),locale);
    assert.equal(await page.locator('#progress-title').textContent(),catalog.flashTitle);
    let state=await bar();assert.ok(Math.abs(state.weights.reduce((a,b)=>a+b,0)-1)<1e-9);assert.equal(state.now,null);
    // Skipped authorization: the second segment fills at once and the rank counts on 5.
    await emit({phase:'media_preparing',message:'Cleaning disk...'});assert.equal(await line(),rankLine(catalog,3),locale);
    state=await bar();assert.deepEqual(state.fills.slice(0,3),['100%','100%','']);
    // Widths follow the weights, and the shortest segment stays visible.
    const total=state.widths.reduce((a,b)=>a+b,0);
    assert.ok(state.widths[3]>state.widths[4]&&state.widths[4]>state.widths[2],'proportional '+locale+width);
    assert.ok(Math.min(...state.widths)>=14,'min width '+state.widths);
    assert.ok(state.widths[3]/total>0.45,'writing dominates');
    let previous=-1;const seen=[];
    for(const event of [{phase:'writing',message:'Writing'},writing(0),writing(30),writing(64),writing(63),{phase:'authorizing',message:'late'},writing(100),{phase:'flash_verifying',message:'Verifying'},verifying(10),verifying(5),verifying(100)]){
      await emit(event);const now=Number((await bar()).now??'-1');
      // No value while the percentage is unknown; once known, never lower than before.
      if(now>=0){assert.ok(now>=previous,JSON.stringify(event)+' '+now+' < '+previous);previous=now;seen.push(now);}
      assert.equal(await page.locator('#progress-detail').evaluate(e=>e.open),false);
    }
    assert.equal(seen.at(-1),100);assert.ok(seen[0]>=11&&seen[0]<=13);
    assert.equal(await line(),rankLine(catalog,5));
    assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),true,locale+width+' overflow');
    // Phase line, percent and bar stay inside the card.
    assert.equal(await page.locator('#main section.panel').first().evaluate(e=>[...e.querySelectorAll('#phase-text,#percent,#flash-progress')].every(n=>n.getBoundingClientRect().right<=e.getBoundingClientRect().right+0.5)),true);
    assert.deepEqual(errors,[]);await page.close();
  }

  // Error in each phase: the failed rank is named in the red box; the details stay folded.
  const phaseEvents=[[{phase:'preparing',message:''}],[{phase:'preparing',message:''},{phase:'authorizing',message:''}],[{phase:'media_preparing',message:''}],[writing(41)],[verifying(20)]];
  for(const locale of ['fr','de','ja']){
    for(let index=0;index<5;index++){
      const {page,catalog,emit,errors}=await open(locale,700);
      for(const event of phaseEvents[index])await emit(event);
      await page.evaluate(()=>window.failFlash('Imager: I/O error'));await page.getByRole('alert').waitFor();
      const alert=await page.getByRole('alert').first().innerText();
      assert.ok(alert.includes(catalog.flashError),locale);
      assert.ok(alert.includes(catalog.flashErrorAt.replace('{n}',index+1).replace('{total}','5').replace('{name}',catalog['phaseName'+(index+1)])),locale+index+' '+alert);
      assert.equal(await page.locator('.error-box details').evaluate(e=>e.open),false);assert.match(await page.locator('.error-box details').textContent(),/I\/O error/);
      assert.equal(await page.locator('#flash-progress').count(),0);
      assert.deepEqual(errors,[]);await page.close();
    }
  }
  // Success: the existing "media ready" card, no phase line.
  {const {page,catalog,emit}=await open('fr',700);await emit({phase:'flash_verifying',message:''});await page.evaluate(()=>window.finishFlash());await page.getByRole('heading',{name:catalog.mediaReady}).waitFor();assert.equal(await page.locator('#phase-text').count(),0);await page.close();}
  console.log('PASS writing bar: ten languages, rank on 5, skipped phase, proportional segments, monotonic, error per phase, collapsed details');
} finally { await browser.close(); }
