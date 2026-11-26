import assert from 'node:assert/strict';
import {pathToFileURL} from 'node:url';
import {resolve} from 'node:path';
const {chromium}=await import(process.env.PLAYWRIGHT_MODULE?pathToFileURL(process.env.PLAYWRIGHT_MODULE).href:'playwright');
const browser=await chromium.launch({headless:true,args:['--use-mock-keychain','--password-store=basic'],...process.env.CHROME_PATH?{executablePath:process.env.CHROME_PATH}:{}});
try {
 const page=await browser.newPage({viewport:{width:375,height:900}}),errors=[];page.on('pageerror',e=>errors.push(e.message));
 await page.addInitScript(()=>{localStorage.setItem('astro-bridge-setup.locale','en');window.__SETUP_TEST_TIMING__={pause:50};window.calls=[];window.__TAURI__={core:{Channel:class {},invoke:async (command,args)=>{window.calls.push({command,args});switch(command){case 'discover_bridges':return window.added?[window.added]:[];case 'add_bridge_ip':if(args.address!=='10.23.45.67')throw new Error('invalid');window.added={id:'0123456789abcdef',name:'Fixture bridge',version:'1.2',path:'home',authMode:'none',sshHost:args.address,hotspotActive:null,addresses:[],adminUrl:'http://10.23.45.67/',manual:true};return window.added;case 'open_bridge_ui':return null;default:throw new Error('unexpected');}}}};});
 await page.goto(pathToFileURL(resolve('setup/ui/index.html')).href);
 // The search starts by itself: no button to start it, the IP address stays one link away.
 await page.getByRole('button',{name:'Find a running bridge'}).click();
 await page.waitForFunction(()=>window.calls.filter(x=>x.command==='discover_bridges').length>=2);
 assert.equal(await page.getByRole('button',{name:'Search local network'}).count(),0);
 for(const locale of ['en','fr','ja','de','es','it','pt-BR','ru','zh-Hans','zh-Hant']){await page.locator('#language').selectOption(locale);assert.equal(await page.locator('#ip-link').isVisible(),true,locale);assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),locale);}
 await page.locator('#language').selectOption('en');
 await page.locator('#ip-link').click();assert.equal(await page.locator('#ip-dialog').evaluate(d=>d.open),true);
 await page.locator('#manual-ip').fill('8.8.8.8');await page.locator('#manual-ip-form button[type=submit]').click();await page.locator('#manual-ip-error').waitFor({state:'visible'});
 assert.equal(await page.locator('#manual-ip').inputValue(),'8.8.8.8');assert.equal(await page.getByRole('button',{name:'Open the administration interface'}).count(),0);
 await page.locator('#manual-ip').fill('10.23.45.67');await page.locator('#manual-ip-form button[type=submit]').click();
 await page.getByRole('button',{name:'Open the administration interface'}).waitFor();
 const text=await page.locator('main').innerText();assert.ok(text.includes('Fixture bridge'));assert.ok(text.includes('10.23.45.67'));assert.ok(text.includes('Added manually'));
 // Plain HTTP on the home network: no password field, browser access only.
 assert.equal(await page.locator('#bridge-unlock').count(),0);
 await page.getByRole('button',{name:'Open the administration interface'}).click();await page.waitForFunction(()=>window.calls.some(x=>x.command==='open_bridge_ui'));
 assert.equal((await page.evaluate(()=>window.calls.find(x=>x.command==='open_bridge_ui'))).args.id,'0123456789abcdef');
 // Still listed after more search rounds.
 const n=await page.evaluate(()=>window.calls.length);await page.waitForFunction(n=>window.calls.length>n+2,n);
 assert.ok((await page.locator('main').innerText()).includes('Added manually'));assert.deepEqual(errors,[]);
 console.log('PASS manual IP dialog during the automatic search, localized UI, error recovery and verified browser-only selection');
}finally{await browser.close();}
