// Setup, step 4 with a fake native layer: the search starts by itself and never blocks the page,
// stop and resume, the IP address dialog and its "Added manually" card, the bridge details read
// with the password once (refreshed without it), "Add" on mounts only, forget the password.
// With UI_ARTIFACTS set, it also writes the launch page screens (fr, 1050x800, 700x600).
import assert from 'node:assert/strict';
import {pathToFileURL} from 'node:url';
import {resolve} from 'node:path';
import {mkdir} from 'node:fs/promises';
const {chromium}=await import(process.env.PLAYWRIGHT_MODULE?pathToFileURL(process.env.PLAYWRIGHT_MODULE).href:'playwright');
const browser=await chromium.launch({headless:true,args:['--use-mock-keychain','--password-store=basic'],...process.env.CHROME_PATH?{executablePath:process.env.CHROME_PATH}:{}});
const output=process.env.UI_ARTIFACTS?resolve(process.env.UI_ARTIFACTS):null;
if(output)await mkdir(output,{recursive:true});
const PASSWORD='Synthetic-Setup-7';

// The fake keeps what Rust keeps: bridges by device_id, a manual bridge between rounds, a session.
const fake=({locale,timing})=>{
  localStorage.setItem('astro-bridge-setup.locale',locale);
  window.__SETUP_TEST_TIMING__=timing;
  window.calls=[];window.visible=[];window.manual=null;window.session=null;window.hold=false;window.addOutcome='connecting';
  const bridge=(id,name,ip,path,authMode)=>({id,name,version:'1.2.0',path,authMode,sshHost:ip,hotspotActive:true,
    addresses:[{path:'home',host:ip,url:'http://'+ip+'/',tls:false},{path:'hotspot',host:'192.168.77.1',url:'http://192.168.77.1/',tls:false}],
    adminUrl:'http://'+ip+'/',manual:false});
  window.bridges={pi5:bridge('0123456789abcdef','astro-bridge-pi5','192.168.50.20','home','https'),
    zero:{...bridge('fedcba9876543210','astro-bridge-zero','192.168.77.1','hotspot','none'),addresses:[{path:'hotspot',host:'192.168.77.1',url:'http://192.168.77.1/',tls:false}],adminUrl:'http://192.168.77.1/'}};
  window.details=()=>({homeSsid:'MonReseau',homeConnected:true,hotspotSsid:'astro-bridge-3f9a2c',hotspotActive:true,sshAccount:'root',
    interfaces:{total:2,builtin:1,usb:1},connectionMode:'hotspot_shared',hotspotSubnet:'192.168.77.0/24',
    found:[{id:'wifi-0123456789abcdef0123456789abcdef',name:'AZ-GTi',kind:'mount',link:'wifi',signal:82,canAdd:true},
      {id:'wifi-fedcba9876543210fedcba9876543210',name:'S50_1234',kind:'telescope',link:'wifi',signal:60,canAdd:false}],
    cameras:[{name:'ZWO ASI',state:'ready'},{name:'Deep sky camera',state:'driver_missing'}],
    added:[{name:'NexStar mount',kind:'mount',linkType:'serial_usb',state:'online',port:2000}]});
  window.__TAURI__={core:{Channel:class {},invoke:async(command,args)=>{
    window.calls.push([command,JSON.parse(JSON.stringify(args||{}))]);
    switch(command){
      case 'discover_bridges':
        await new Promise(r=>setTimeout(r,30));
        while(window.hold)await new Promise(r=>setTimeout(r,20));
        return [...window.visible.map(key=>window.bridges[key]),...(window.manual?[window.manual]:[])];
      case 'add_bridge_ip':
        if(args.address!=='192.168.50.31')throw 'Bridge connection failed';
        window.manual={...bridge('00000000000000aa','astro-bridge-atelier','192.168.50.31','home','none'),hotspotActive:false,manual:true};
        return window.manual;
      case 'unlock_bridge':if(args.password!==window.expected)throw 'Bridge returned HTTP 401';window.session=args.id;return window.details();
      case 'refresh_bridge':if(window.session!==args.id)throw 'SESSION_EXPIRED';return window.details();
      case 'forget_bridge_session':window.session=null;return null;
      case 'add_bridge_device':if(window.session!==args.id)throw 'SESSION_EXPIRED';return window.addOutcome;
      case 'open_bridge_ui':return null;
      case 'resolve_release':return {target:args.target,tag:'v1.2.0',asset:'fixture.img.xz',size:3,sha256:'a'.repeat(64),source:'published',wifi_seed:true,password_seed:true,driver_drop:true};
      case 'download_release':return {name:'fixture.img.xz'};
      case 'list_media':return [{id:'card',name:'Test card',size:32000000000}];
      case 'driver_offers':return {supported:false,offers:[]};
      case 'flash_media':return null;
      case 'change_bridge_password':return {sessionsInvalidated:false,hotspotReconnect:false,sessionKept:true};
      default:throw new Error('unexpected '+command);
    }}}};
};
const count=(page,command)=>page.evaluate(c=>window.calls.filter(x=>x[0]===c).length,command);
async function open({locale='en',width=1050,height=800,timing={pause:40,refresh:250,none:600000}}={}){
  const page=await browser.newPage({viewport:{width,height},locale:'en-US'});const errors=[];page.on('pageerror',e=>errors.push(e.message));
  await page.addInitScript(fake,{locale,timing});
  await page.goto(pathToFileURL(resolve('tools/astro-bridge-setup/ui/index.html')).href);
  page.errors=errors;return page;
}
const noOverflow=page=>page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth);
async function shot(page,name){
  if(!output)return;
  await page.evaluate(()=>{document.activeElement?.blur();window.scrollTo(0,0);});
  await page.screenshot({path:resolve(output,name+'-fenetre.png')});
  // Whole page: the fixed bar is put back in the flow, at the bottom, for this capture only.
  const style=await page.addStyleTag({content:'main > .actions{position:static !important}'});
  await page.screenshot({path:resolve(output,name+'-complet.png'),fullPage:true});
  await style.evaluate(e=>e.remove());
}
try{
  {
    // From the assistant, the Launch step goes straight to the launch page, and Back returns.
    const page=await open();
    await page.getByRole('button',{name:'Get started'}).click();
    await page.locator('#target-pi-5').check();
    await page.getByRole('button',{name:'Check for a release'}).click();
    await page.getByRole('button',{name:'Download and verify',exact:true}).click();
    await page.locator('#step-launch').click();
    await page.locator('#launch-search').waitFor();
    await page.getByRole('button',{name:'Back'}).click();
    await page.getByRole('button',{name:'Continue',exact:true}).waitFor();
    assert.equal(await page.locator('main > .stepper [aria-current=step]').innerText().then(s=>s.includes('Release')),true);
    // A bridge on the home network in plain HTTP takes the password, with the HTTP notice.
    await page.evaluate(p=>{window.expected=p;window.bridges.pi5.authMode='http';window.visible=['pi5'];},PASSWORD);
    await page.locator('#step-launch').click();
    await page.locator('#bridge-current-password').waitFor();
    assert.match(await page.locator('#bridge-panel').innerText(),/unencrypted HTTP on your local network/);
    assert.equal(await page.getByText('Setup needs trusted HTTPS').count(),0);
    await page.locator('#bridge-current-password').fill(PASSWORD);await page.locator('#bridge-unlock button[type=submit]').click();
    await page.locator('#bridge-details').waitFor();
    assert.deepEqual(page.errors,[]);await page.close();
  }
  {
    const page=await open();
    await page.getByRole('button',{name:'Find a running bridge'}).click();
    // The search starts by itself, with a spinner; there is no full-page progress panel.
    await page.waitForFunction(()=>window.calls.filter(c=>c[0]==='discover_bridges').length>=2);
    assert.equal(await page.getByRole('button',{name:'Search local network'}).count(),0);
    assert.equal(await page.locator('#progress-title').count(),0);
    assert.equal(await page.locator('.spinner').count(),1);
    assert.match(await page.locator('#launch-search').innerText(),/Searching for bridges on the network…/);
    assert.equal(await page.getByRole('button',{name:'Back'}).isEnabled(),true);
    assert.equal(await page.getByRole('button',{name:'Set up another device'}).isEnabled(),true);
    assert.equal(await page.locator('#language').isEnabled(),true);
    // Stop, then nothing more is searched until Resume.
    await page.getByRole('button',{name:'Stop',exact:true}).click();
    assert.match(await page.locator('#launch-search').innerText(),/Search stopped/);
    assert.equal(await page.locator('.spinner').count(),0);
    await page.waitForTimeout(150);const stopped=await count(page,'discover_bridges');await page.waitForTimeout(300);
    assert.equal(await count(page,'discover_bridges'),stopped);
    assert.equal(await page.evaluate(()=>document.activeElement?.id),'search-toggle');
    await page.getByRole('button',{name:'Resume'}).click();
    await page.waitForFunction(n=>window.calls.filter(c=>c[0]==='discover_bridges').length>n+1,stopped);
    assert.equal(await page.locator('.spinner').count(),1);
    // Bridges appear as they are found, under the spinner that keeps turning.
    await page.evaluate(()=>{window.visible=['pi5','zero'];});
    await page.getByText('astro-bridge-zero').waitFor();
    assert.match(await page.locator('#launch-search h2').innerText(),/Choose your bridge/);
    assert.equal(await page.locator('#choose-hint').count(),1);
    // The IP address dialog: a real modal dialog, an error keeps it open, a bridge that answers is added.
    await page.getByRole('button',{name:'Enter IP address'}).click();
    assert.equal(await page.locator('#ip-dialog').evaluate(d=>d.open&&d.matches(':modal')),true);
    await page.locator('#manual-ip').fill('192.168.50.99');await page.locator('#ip-dialog button[type=submit]').click();
    await page.locator('#manual-ip-error').waitFor({state:'visible'});
    assert.equal(await page.locator('#ip-dialog').evaluate(d=>d.open),true);
    assert.equal(await page.locator('#manual-ip').inputValue(),'192.168.50.99');
    await page.getByRole('button',{name:'Cancel'}).click();assert.equal(await page.locator('#ip-dialog').evaluate(d=>d.open),false);
    await page.getByRole('button',{name:'Enter IP address'}).click();
    await page.locator('#manual-ip').fill('192.168.50.31');await page.locator('#ip-dialog button[type=submit]').click();
    await page.locator('#ip-dialog').waitFor({state:'hidden'});
    const manualCard=page.locator('.media-option',{hasText:'astro-bridge-atelier'});
    await manualCard.waitFor();
    assert.match(await manualCard.innerText(),/Added manually/);
    // Stays in the list over the next rounds.
    const rounds=await count(page,'discover_bridges');
    await page.waitForFunction(n=>window.calls.filter(c=>c[0]==='discover_bridges').length>n+2,rounds);
    assert.match(await page.locator('.media-option',{hasText:'astro-bridge-atelier'}).innerText(),/Added manually/);
    // A manual bridge without HTTPS: addresses and the administration interface, no password.
    assert.equal(await page.locator('#bridge-unlock').count(),0);
    assert.match(await page.locator('#bridge-panel').innerText(),/http:\/\/192\.168\.50\.31\//);
    // Choose the HTTPS bridge: name, version, hotspot pill, addresses, administration interface.
    await page.locator('input[name=bridge]').first().check({force:true});
    const panelText=await page.locator('#bridge-panel').innerText();
    for(const text of ['astro-bridge-pi5 · version 1.2.0','Hotspot on','Private network (home)','192.168.50.20','Bridge hotspot','192.168.77.1','http://192.168.50.20/'])assert.ok(panelText.includes(text),text);
    await page.getByRole('button',{name:'Open the administration interface'}).click();
    await page.waitForFunction(()=>window.calls.some(c=>c[0]==='open_bridge_ui'&&c[1].id==='0123456789abcdef'));
    // A typed password survives the search rounds.
    await page.locator('#bridge-current-password').fill('typing');
    const before=await count(page,'discover_bridges');
    await page.waitForFunction(n=>window.calls.filter(c=>c[0]==='discover_bridges').length>n+2,before);
    assert.equal(await page.locator('#bridge-current-password').inputValue(),'typing');
    // Wrong password: said, and the form stays.
    await page.evaluate(p=>{window.expected=p;},PASSWORD);
    await page.locator('#bridge-unlock button[type=submit]').click();
    await page.locator('#main').getByText('Could not show the details.',{exact:false}).waitFor();
    await page.locator('#bridge-current-password').fill(PASSWORD);
    await page.locator('#bridge-unlock button[type=submit]').click();
    await page.locator('#bridge-details').waitFor();
    assert.equal(await page.locator('#bridge-current-password').count(),0);
    const details=await page.locator('#bridge-panel').innerText();
    for(const text of ['2 (built-in + USB adapter)','Hotspot with Internet','astro-bridge-3f9a2c · 192.168.77.0/24 · on','MonReseau · connected','1 mount to add','Signal 82 %','Driver ready','Driver missing','NexStar mount','Port 2000','Online','Read with the bridge password, updated every 10 s.'])assert.ok(details.includes(text),text);
    // Online is a green badge, Offline stays neutral.
    assert.equal(await page.locator('#bridge-details .pill.green',{hasText:/^Online$/}).count(),1);
    // "Add" on the mount only: not on the smart telescope, never on cameras.
    const rows=page.locator('#bridge-details .hw-row');
    assert.equal(await rows.count(),5);
    assert.equal(await page.locator('#bridge-details .hw-row .button').count(),1);
    assert.match(await page.locator('#bridge-details .hw-row',{has:page.locator('.button')}).innerText(),/AZ-GTi/);
    assert.equal(await page.locator('#bridge-details .hw-row',{hasText:'ZWO ASI'}).locator('button').count(),0);
    assert.match(await page.locator('.launch-ssh').innerText(),/ssh root@192\.168\.50\.20/);
    // Details refresh without the password, and an open form keeps what is typed.
    await page.getByRole('button',{name:'Change password'}).click();
    await page.locator('#bridge-change-current').fill('kept');
    const refreshes=await count(page,'refresh_bridge');
    await page.waitForFunction(n=>window.calls.filter(c=>c[0]==='refresh_bridge').length>n+1,refreshes);
    assert.equal(await page.locator('#bridge-change-current').inputValue(),'kept');
    assert.ok((await page.evaluate(()=>window.calls.filter(c=>c[0]==='refresh_bridge'))).every(c=>!('password' in c[1])));
    await page.getByRole('button',{name:'Cancel'}).click();
    // Add the mount.
    await page.locator('#bridge-details .hw-row .button').click();
    // The bridge accepted to connect it on a free adapter: said, in green.
    const outcome=page.locator('#main [role=status] .callout');
    await page.locator('#main').getByText('Mount added. The bridge is connecting to it.',{exact:false}).waitFor();
    assert.match(await outcome.getAttribute('class'),/green/);
    assert.deepEqual((await page.evaluate(()=>window.calls.find(c=>c[0]==='add_bridge_device')))[1],{id:'0123456789abcdef',device:'wifi-0123456789abcdef0123456789abcdef'});
    // No free Wi-Fi adapter: added, not connected, and where to connect it, in gold, never "Mount added." alone.
    await page.evaluate(()=>{window.addOutcome='no_free_adapter';});
    await page.locator('#bridge-details .hw-row .button').click();
    await page.locator('#main').getByText('no Wi-Fi adapter of the bridge is free',{exact:false}).waitFor();
    assert.equal(await outcome.innerText(),'Mount added, but not connected: no Wi-Fi adapter of the bridge is free. Connect it from the administration interface.');
    assert.match(await outcome.getAttribute('class'),/gold/);
    // The text exists in the ten languages (never the key, never the English text elsewhere).
    const english=await outcome.innerText();
    for(const locale of ['fr','de','es','it','pt-BR','ru','ja','zh-Hans','zh-Hant']){
      await page.locator('#language').selectOption(locale);
      const said=await page.locator('#main [role=status] .callout').innerText();
      assert.ok(said&&said!==english&&!said.includes('deviceAdded'),locale+': '+said);
    }
    await page.locator('#language').selectOption('en');
    // Refused by the bridge, or an unknown answer: added, not connected.
    for(const answer of ['not_connected',null]){
      await page.evaluate(a=>{window.addOutcome=a;},answer);
      await page.locator('#bridge-details .hw-row .button').click();
      await page.locator('#main').getByText('Mount added, but the bridge could not connect to it.',{exact:false}).waitFor();
      await page.evaluate(()=>{window.addOutcome='connecting';});
      await page.locator('#bridge-details .hw-row .button').click();
      await page.locator('#main').getByText('Mount added. The bridge is connecting to it.',{exact:false}).waitFor();
    }
    // A serial mount: added, the bridge relays it on its own.
    await page.evaluate(()=>{window.addOutcome='added';});
    await page.locator('#bridge-details .hw-row .button').click();
    await page.locator('#main').getByText('Mount added.',{exact:true}).waitFor();
    // Buttons never touch: Change password and its hint, Back and the next button.
    const gap=await page.evaluate(()=>{const a=document.getElementById('change-password').getBoundingClientRect(),b=a&&document.querySelector('#change-password + .field-hint').getBoundingClientRect();return b.left-a.right;});
    assert.ok(gap>=8,String(gap));
    // Forget the password: the token goes, the password form comes back.
    await page.getByRole('button',{name:'Forget the password'}).click();
    await page.locator('#bridge-current-password').waitFor();
    assert.equal(await page.evaluate(()=>window.session),null);
    assert.ok(await count(page,'forget_bridge_session')>=1);
    // Another bridge: its own panel, no password asked for a bridge that cannot use one.
    await page.locator('input[name=bridge]').nth(1).check({force:true});
    assert.match(await page.locator('#bridge-panel').innerText(),/astro-bridge-zero/);
    // Ten languages, no horizontal scroll at the minimal window width.
    await page.setViewportSize({width:700,height:600});
    await page.locator('input[name=bridge]').first().check({force:true});
    await page.locator('#bridge-current-password').fill(PASSWORD);await page.locator('#bridge-unlock button[type=submit]').click();await page.locator('#bridge-details').waitFor();
    for(const locale of ['en','fr','de','es','it','pt-BR','ru','ja','zh-Hans','zh-Hant']){
      await page.locator('#language').selectOption(locale);
      assert.ok(await noOverflow(page),locale);
      // The panel may be redrawn by a search round while it is read: read the attached node again.
      const pre=await page.waitForFunction(()=>{const e=document.querySelector('.launch-ssh pre');return e&&e.isConnected&&getComputedStyle(e).whiteSpace;}).then(h=>h.jsonValue());assert.equal(pre,'pre',locale);
    }
    await page.locator('#language').selectOption('en');
    // Leaving the page stops the search and signs out.
    const forgets=await count(page,'forget_bridge_session');
    await page.getByRole('button',{name:'Back'}).click();
    await page.waitForFunction(n=>window.calls.filter(c=>c[0]==='forget_bridge_session').length>n,forgets);
    const left=await count(page,'discover_bridges');await page.waitForTimeout(300);
    assert.equal(await count(page,'discover_bridges'),left);
    assert.deepEqual(page.errors,[]);
    await page.close();
  }
  {
    // Nothing answers for the configured time: what to check, and the hotspot fallback open.
    const page=await open({timing:{pause:40,refresh:250,none:300}});
    await page.getByRole('button',{name:'Find a running bridge'}).click();
    await page.locator('#launch-search').getByText('No bridge found').waitFor();
    assert.equal(await page.locator('#launch-search .command-list li').count(),3);
    assert.equal(await page.locator('.launch-hotspot').evaluate(d=>d.open),true);
    assert.equal(await page.locator('.spinner').count(),1);
    // The hotspot search is the same continuous search, with the joined hotspot.
    await page.locator('#hotspot-consent').check();
    await page.getByRole('button',{name:'Search joined hotspot'}).click();
    await page.waitForFunction(()=>window.calls.some(c=>c[0]==='discover_bridges'&&c[1].joinedHotspot===true));
    // A single bridge is chosen for the user.
    await page.evaluate(()=>{window.visible=['pi5'];});
    await page.locator('#bridge-panel').waitFor();
    assert.deepEqual(page.errors,[]);
    await page.close();
  }
  console.log('PASS step 4 search starts by itself, never blocks the page, stop and resume, nothing found after the delay, hotspot search');
  console.log('PASS IP address dialog (modal, error kept, Added manually card kept between rounds), bridge addresses and administration interface');
  console.log('PASS details read once with the password then refreshed without it, Add on mounts only, cameras listed, forget the password, ten languages at 700 px');
  console.log('PASS Add says connecting only when the bridge accepted it; no free adapter or a refusal says to connect it from the administration interface, in ten languages');

  if(output){
    // Launch page screens, French, both window sizes, from the real ui/ with this fake.
    for(const [width,height] of [[1050,800],[700,600]]){
      const size=width+'x'+height;
      const at=async(state)=>{
        // Same path as the planche: a Pi 5 card written at step 3, then step 4 in French.
        const page=await open({locale:'en',width,height,timing:{pause:400,refresh:10000,none:600000}});
        await page.evaluate(p=>{window.expected=p;},PASSWORD);
        await page.getByRole('button',{name:'Get started'}).click();
        await page.locator('#target-pi-5').check();
        await page.getByRole('button',{name:'Check for a release'}).click();
        await page.getByRole('button',{name:'Download and verify',exact:true}).click();
        await page.getByRole('button',{name:'Continue',exact:true}).click();
        await page.getByText('Test card').click();
        await page.getByRole('button',{name:'Review and confirm'}).click();
        await page.locator('#erase-dialog input').check();
        await page.locator('#erase-dialog .button.danger').click();
        await page.getByRole('button',{name:'Continue to Launch'}).click();
        await page.locator('#launch-search').waitFor();
        await page.locator('#language').selectOption('fr');
        return page;
      };
      let page=await at();
      await page.waitForTimeout(200);await shot(page,'L1-'+size);
      await page.getByRole('button',{name:'Saisir une adresse IP'}).click();await page.locator('#manual-ip').fill('192.168.50.31');
      await page.screenshot({path:resolve(output,'L6-'+size+'-fenetre.png')});
      await page.getByRole('button',{name:'Annuler'}).click();
      await page.getByRole('button',{name:'Arrêter'}).click();await shot(page,'L5-'+size);
      await page.close();
      page=await at();
      await page.evaluate(()=>{window.visible=['pi5','zero'];});await page.getByText('astro-bridge-zero').waitFor();
      await shot(page,'L2-'+size);
      await page.getByRole('button',{name:'Saisir une adresse IP'}).click();await page.locator('#manual-ip').fill('192.168.50.31');
      await page.locator('#ip-dialog button[type=submit]').click();await page.locator('#ip-dialog').waitFor({state:'hidden'});
      // L7: the list with the card added by hand, nothing chosen yet.
      await page.locator('.media-option',{hasText:'astro-bridge-atelier'}).waitFor();
      await page.close();
      page=await at();
      await page.evaluate(()=>{window.visible=['pi5','zero'];window.manual={...window.bridges.pi5,id:'00000000000000aa',name:'astro-bridge-atelier',sshHost:'192.168.50.31',authMode:'none',hotspotActive:false,addresses:[{path:'home',host:'192.168.50.31',url:'http://192.168.50.31/',tls:false}],adminUrl:'http://192.168.50.31/',manual:true};});
      await page.getByText('astro-bridge-atelier').waitFor();await shot(page,'L7-'+size);
      await page.locator('input[name=bridge]').first().check({force:true});await page.locator('#bridge-unlock').waitFor();
      await shot(page,'L3a-'+size);
      await page.locator('#bridge-current-password').fill(PASSWORD);await page.locator('#bridge-unlock button[type=submit]').click();
      await page.locator('#bridge-details').waitFor();await shot(page,'L3-'+size);
      assert.ok(await noOverflow(page),size);
      assert.deepEqual(page.errors,[]);
      await page.close();
    }
    console.log('PASS captures written to '+output);
  }
}finally{await browser.close();}
