// Setup, step 3 and 4 with a fake native layer: the same panels on every hardware, the bridge
// password, camera drivers downloaded before writing (success, failure, retry, offline) and the
// single password change of step 4. Nothing is written; no network is used.
import assert from 'node:assert/strict';
import {pathToFileURL} from 'node:url';
import {resolve} from 'node:path';
const {chromium}=await import(process.env.PLAYWRIGHT_MODULE?pathToFileURL(process.env.PLAYWRIGHT_MODULE).href:'playwright');
const browser=await chromium.launch({headless:true,...process.env.CHROME_PATH?{executablePath:process.env.CHROME_PATH}:{}});
const PASSWORD='Synthetic-Setup-7';
const fake=(options)=>{
  localStorage.setItem('astro-bridge-setup.locale','en');
  window.calls=[];window.outcomes=options.outcomes||[];
  const release={pi:{source:'published',wifi_seed:true,password_seed:true,driver_drop:true,camera_service:true,driver_arch:'arm64'},
    zero:{source:'published',wifi_seed:true,password_seed:true,driver_drop:true,camera_service:false},
    old:{source:'published',wifi_seed:true}}[options.release];
  const offers=[{id:'zwo_asi',name:'ZWO ASI',verifiedOnHardware:true,termsUrl:'https://www.zwoastro.com/software/product-sdk/',source:'download',version:'1.42',arch:'arm64',size:114589633},
    {id:'testcam',name:'Test Camera',verifiedOnHardware:false,termsUrl:'https://www.example.com/terms',source:'download',version:'1.0',arch:'arm64',size:1000}];
  window.__TAURI__={core:{Channel:class {},invoke:async(command,args)=>{
    window.calls.push([command,JSON.parse(JSON.stringify({...args,onProgress:undefined}))]);
    switch(command){
      case 'resolve_release':return {target:args.target,tag:'v9.9.9',asset:'fixture.img.xz',size:3,sha256:'a'.repeat(64),...release};
      case 'download_release':return {name:'fixture.img.xz'};
      case 'list_media':return [{id:'card',name:'Test card',size:32000000000}];
      case 'driver_offers':return release.camera_service?{supported:true,offers}:{supported:false,offers:[]};
      case 'download_drivers':{args.onProgress.onmessage({id:'testcam',received:400,total:1000});const outcome=window.outcomes.shift()||'ready';return args.ids.map(id=>({id,outcome}));}
      case 'open_licence':return null;
      case 'flash_media':return null;
      case 'discover_bridges':return [{id:'b1',name:'astro-bridge-test',version:'9.9.9',path:'home',authMode:'https',sshHost:'192.168.50.20',hotspotActive:true,addresses:[],adminUrl:'http://192.168.50.20/',manual:false}];
      case 'unlock_bridge':case 'refresh_bridge':return {homeSsid:'Home',homeConnected:true,hotspotSsid:'AP',hotspotActive:true,sshAccount:options.release==='zero'?'bridge':'root',interfaces:null,connectionMode:null,hotspotSubnet:null,found:[],cameras:[],added:[]};
      case 'forget_bridge_session':return null;
      case 'change_bridge_password':return {sessionsInvalidated:true,hotspotReconnect:true,sessionKept:false};
      default:throw new Error('unexpected '+command);
    }}}};
};
async function step3(release,{outcomes=[],width=1100,target}={}){
  const page=await browser.newPage({viewport:{width,height:900},locale:'en-US'});const errors=[];page.on('pageerror',e=>errors.push(e.message));
  await page.addInitScript(fake,{release,outcomes});
  await page.goto(pathToFileURL(resolve('tools/astro-bridge-setup/ui/index.html')).href);
  await page.getByRole('button',{name:'Get started'}).click();
  await page.locator('#target-'+(target||(release==='zero'?'pi-zero-w':'pi-5'))).check();
  await page.getByRole('button',{name:'Check for a release'}).click();
  await page.getByRole('button',{name:'Download and verify',exact:true}).click();
  await page.getByRole('button',{name:'Continue',exact:true}).click();
  await page.getByText('Test card').waitFor();
  page.errors=errors;return page;
}
async function confirm(page){
  await page.getByText('Test card').click();
  await page.getByRole('button',{name:'Review and confirm'}).click();
  await page.locator('#erase-dialog input').check();
  await page.locator('#erase-dialog .button.danger').click();
}
const flashes=page=>page.evaluate(()=>window.calls.filter(c=>c[0]==='flash_media').map(c=>c[1]));
try{
  // On a short step in a tall window the bar sits on the bottom edge, not under the content.
  {const page=await browser.newPage({viewport:{width:1100,height:1600},locale:'en-US'});
    await page.addInitScript(fake,{release:'pi',outcomes:[]});
    await page.goto(pathToFileURL(resolve('tools/astro-bridge-setup/ui/index.html')).href);
    await page.getByRole('button',{name:'Get started'}).click();await page.locator('#target-pi-5').check();
    await page.getByRole('button',{name:'Check for a release'}).click();await page.getByRole('button',{name:'Download and verify',exact:true}).click();
    await page.getByRole('button',{name:'Continue',exact:true}).waitFor();
    const bar=await page.evaluate(()=>({bottom:document.querySelector('main > .actions').getBoundingClientRect().bottom,inner:innerHeight,doc:document.documentElement.scrollHeight}));
    assert.ok(Math.abs(bar.bottom-bar.inner)<=1&&bar.doc<=bar.inner+1,'tall window '+JSON.stringify(bar));await page.close();}
  // Same three panels on the Pi 5 and the Pi Zero W; a release that only knows Wi-Fi keeps Wi-Fi only.
  for(const release of ['pi','zero']){
    const page=await step3(release);
    // Back and the next step stay at the bottom of the window while the content scrolls.
    assert.equal(await page.locator('main > .actions').evaluate(e=>getComputedStyle(e).position),'sticky');
    await page.evaluate(()=>window.scrollTo(0,0));
    assert.ok(await page.locator('main > .actions').evaluate(e=>Math.abs(e.getBoundingClientRect().bottom-innerHeight)<=1),release);
    // The header and the step indicator stay at the top, right under each other, once scrolled.
    await page.evaluate(()=>window.scrollTo(0,document.documentElement.scrollHeight));
    const top=await page.evaluate(()=>{const h=document.querySelector('.app-header').getBoundingClientRect(),s=document.querySelector('main > .stepper').getBoundingClientRect(),a=document.querySelector('main > .actions').getBoundingClientRect();return {scrolled:scrollY>0,header:h.top,gap:s.top-h.bottom,room:a.top-s.bottom};});
    assert.ok(top.scrolled,release);assert.ok(Math.abs(top.header)<=1,release+' header '+top.header);assert.ok(Math.abs(top.gap)<=1,release+' gap '+top.gap);assert.ok(top.room>=150,release+' room '+top.room);
    await page.evaluate(()=>window.scrollTo(0,0));
    for(const id of ['#wifi-enabled','#bridge-password','#bridge-password-confirm','#drivers-panel'])assert.equal(await page.locator(id).count(),1,release+id);
    assert.equal(await page.locator('#bridge-password').getAttribute('type'),'password');
    if(release==='zero'){
      assert.match(await page.locator('#drivers-panel').innerText(),/Not supported by this hardware\./);
      assert.equal(await page.locator('#drivers-toggle').count(),0);
      assert.match(await page.locator('.panel-note').innerText(),/second USB adapter/);
    }else{
      // The camera panel is open from the start.
      assert.equal(await page.locator('#drivers-toggle').getAttribute('aria-expanded'),'true');
      assert.equal(await page.locator('#drivers-summary').innerText(),'No driver');
      // One line per installable manufacturer, never ticked in advance; the unverified one says so.
      assert.equal(await page.locator('.driver-row input:checked').count(),0);
      assert.equal(await page.locator('.driver-row').count(),2);
      assert.match(await page.locator('.driver-row').nth(1).innerText(),/not verified on hardware/);
      assert.doesNotMatch(await page.locator('.driver-row').nth(0).innerText(),/not verified/);
      await page.locator('.driver-row .link-button').nth(1).click();
      assert.deepEqual((await page.evaluate(()=>window.calls)).filter(c=>c[0]==='open_licence').map(c=>c[1].id),['testcam']);
      // Buttons of the rows share one right edge.
      const rights=await page.locator('.driver-row .link-button').evaluateAll(n=>n.map(e=>Math.round(e.getBoundingClientRect().right)));
      assert.equal(new Set(rights).size,1);
      await page.locator('#driver-zwo_asi').check();await page.locator('#driver-testcam').check();
      assert.equal(await page.locator('#drivers-summary').innerText(),'2 drivers: ZWO ASI, Test Camera');
    }
    for(const locale of ['en','fr','de','es','it','pt-BR','ru','ja','zh-Hans','zh-Hant']){
      await page.locator('#language').selectOption(locale);
      for(const width of [320,375]){await page.setViewportSize({width,height:900});assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),locale+width);}
      await page.setViewportSize({width:1100,height:900});
    }
    assert.deepEqual(page.errors,[]);await page.close();
  }
  {
    const page=await step3('old');
    assert.equal(await page.locator('#wifi-enabled').count(),1);
    assert.equal(await page.locator('#bridge-password').count()+await page.locator('#drivers-panel').count(),0);
    assert.ok(!(await page.evaluate(()=>window.calls)).some(c=>c[0]==='driver_offers'));
    await page.close();
  }
  // Password rule in plain words; the request carries it, then no field keeps it.
  {
    const page=await step3('pi');
    for(const [value,repeat,message] of [['short','short','Use 8 to 63 characters'],['étoile2026','étoile2026','Use 8 to 63 characters'],['12345678','12345678','other than 12345678'],[PASSWORD,PASSWORD+'x','not the same']]){
      await page.locator('#bridge-password').fill(value);await page.locator('#bridge-password-confirm').fill(repeat);
      await page.getByText('Test card').click();await page.getByRole('button',{name:'Review and confirm'}).click();
      assert.match(await page.locator('#bridge-password-error').innerText(),new RegExp(message));
      assert.equal(await page.locator('#erase-dialog').evaluate(d=>d.open),false);
    }
    await page.locator('#bridge-password-confirm').fill(PASSWORD);
    await page.locator('#driver-zwo_asi').check();
    await confirm(page);
    await page.waitForFunction(()=>window.calls.some(c=>c[0]==='flash_media'));
    const [request]=await flashes(page);
    assert.equal(request.password,PASSWORD);assert.deepEqual(request.drivers,['zwo_asi']);
    // No image carries a manufacturer file: ZWO is downloaded on the computer like every other driver, before writing.
    assert.deepEqual((await page.evaluate(()=>window.calls)).filter(c=>c[0]==='download_drivers').map(c=>c[1].ids),[['zwo_asi']]);
    assert.equal(await page.locator('input[type=password]').count(),0);
    await page.close();
  }
  // Download before writing: failure says it, offers to try again or to write without the driver.
  {
    const page=await step3('pi',{outcomes:['changed','failed','ready']});
    await page.locator('#driver-testcam').check();
    await confirm(page);
    await page.locator('.driver-message').waitFor();
    assert.match(await page.locator('.driver-message').innerText(),/Test Camera: the manufacturer’s file changed\. The card can be written without this driver\. You can add it later in the bridge interface: Configuration, Cameras\./);
    assert.equal(await page.locator('#driver-state-testcam').innerText(),'the manufacturer’s file changed');
    assert.equal((await flashes(page)).length,0,'nothing written before a choice');
    const buttons=page.locator('main .actions-end .button');
    assert.deepEqual(await buttons.allInnerTexts(),['Try again','Write the card without this driver']);
    const [a,b]=await buttons.evaluateAll(n=>n.map(e=>e.getBoundingClientRect()));
    assert.ok(b.left-a.right>=8,'buttons are not touching');assert.equal(Math.round(a.height),Math.round(b.height));
    await buttons.first().click();await page.locator('.driver-message').waitFor();
    assert.match(await page.locator('.driver-message').innerText(),/download failed/);
    await buttons.first().click();
    await page.waitForFunction(()=>window.calls.some(c=>c[0]==='flash_media'));
    assert.deepEqual((await flashes(page))[0].drivers,['testcam']);
    await page.close();
  }
  {
    const page=await step3('pi',{outcomes:['failed']});
    await page.locator('#driver-testcam').check();await page.locator('#driver-zwo_asi').check();
    await confirm(page);await page.locator('.driver-message').first().waitFor();
    await page.getByRole('button',{name:'Write the card without these drivers'}).click();
    await page.waitForFunction(()=>window.calls.some(c=>c[0]==='flash_media'));
    assert.ok(!(await flashes(page))[0].drivers?.length,'a driver whose download failed is not written');
    await page.close();
  }
  // Offline: the card is written without the driver, and the page says so.
  {
    const page=await step3('pi',{outcomes:['offline']});
    await page.locator('#driver-testcam').check();
    await confirm(page);
    await page.waitForFunction(()=>window.calls.some(c=>c[0]==='flash_media'));
    assert.equal((await flashes(page))[0].drivers,null);
    await page.getByText('No Internet connection: the card is written without the camera drivers. You can add them later in the bridge interface: Configuration, Cameras.').waitFor();
    await page.close();
  }
  // Step 4: one button changes the single password; SSH shows the account the bridge reports.
  for(const release of ['pi','zero']){
    const page=await step3(release);
    await confirm(page);
    await page.getByRole('button',{name:'Continue to Launch'}).click();
    // The search starts by itself; a single bridge is chosen for the user.
    await page.locator('#bridge-unlock').waitFor();
    assert.match(await page.locator('.launch-ssh').innerText(),new RegExp('ssh '+(release==='zero'?'bridge':'root')+'@192.168.50.20'));
    await page.locator('#bridge-current-password').fill('12345678');await page.locator('#bridge-unlock button[type=submit]').click();
    await page.getByRole('button',{name:'Change password'}).click();
    await page.locator('#bridge-change-current').fill('12345678');
    const fields=page.locator('#launch-credentials input[type=password]');
    await fields.nth(1).fill('avec espace');await fields.nth(2).fill('avec espace');
    await page.locator('#launch-credentials button[type=submit]').click();
    assert.match(await page.locator('.launch-form-error').innerText(),/Use 8 to 63 characters/);
    await page.locator('#bridge-change-current').fill('12345678');await fields.nth(1).fill(PASSWORD);await fields.nth(2).fill(PASSWORD);
    await page.locator('#launch-credentials button[type=submit]').click();
    await page.locator('#main').getByText('Password changed. Join the hotspot again').waitFor();
    const change=(await page.evaluate(()=>window.calls)).find(c=>c[0]==='change_bridge_password')[1];
    assert.equal(change.target,'all');assert.equal(change.newPassword,PASSWORD);
    await page.close();
  }
  console.log('PASS same Wi-Fi, password and camera driver panels on the Pi 5 and the Pi Zero W, not supported said on the Zero W, ten languages at 320 and 375 px');
  console.log('PASS password rule in plain words, drivers never ticked in advance, every driver downloaded before writing, licence link by catalog id');
  console.log('PASS download before writing: changed and failed said, try again, write without, offline written without and said; step 4 single password and SSH account');
}finally{await browser.close();}
