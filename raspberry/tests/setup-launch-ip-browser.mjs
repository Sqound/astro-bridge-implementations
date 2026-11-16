import assert from 'node:assert/strict';
import {pathToFileURL} from 'node:url';
import {resolve} from 'node:path';
const {chromium}=await import(process.env.PLAYWRIGHT_MODULE?pathToFileURL(process.env.PLAYWRIGHT_MODULE).href:'playwright');
const browser=await chromium.launch({headless:true,...process.env.CHROME_PATH?{executablePath:process.env.CHROME_PATH}:{}});
try {
 const page=await browser.newPage({viewport:{width:375,height:900}}),errors=[];page.on('pageerror',e=>errors.push(e.message));
 await page.addInitScript(()=>{localStorage.setItem('astro-bridge-setup.locale','en');window.calls=[];window.__TAURI__={core:{Channel:class {},invoke:async (command,args)=>{window.calls.push({command,args});switch(command){case 'discover_bridges':return [];case 'add_bridge_ip':if(args.address!=='10.23.45.67')throw new Error('invalid');return {id:'verified',name:'Fixture bridge',version:'1.2',path:'home',authMode:'none',sshHost:args.address};case 'open_bridge_ui':return null;default:throw new Error('unexpected');}}}};});
 await page.goto(pathToFileURL(resolve('tools/astro-bridge-setup/ui/index.html')).href);
 await page.getByRole('button',{name:'Find a running bridge'}).click();assert.equal(await page.locator('#manual-ip').count(),1);
 await page.getByRole('button',{name:'Search local network'}).click();await page.locator('#main').getByText('No verified bridge found',{exact:false}).waitFor();assert.equal(await page.locator('#manual-ip').isVisible(),true);
 for(const locale of ['en','fr','ja','de','es','it','pt-BR','ru','zh-Hans','zh-Hant']){await page.locator('#language').selectOption(locale);assert.equal(await page.locator('#manual-ip').isVisible(),true);assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),locale);}
 await page.locator('#language').selectOption('en');await page.locator('#manual-ip').fill('8.8.8.8');await page.locator('#manual-ip-form button').click();await page.getByRole('alert').waitFor();assert.equal(await page.locator('#manual-ip').inputValue(),'8.8.8.8');assert.equal(await page.getByRole('button',{name:'Open web interface'}).count(),0);
 await page.locator('#manual-ip').fill('10.23.45.67');await page.locator('#manual-ip-form button').click();await page.getByRole('button',{name:'Open web interface'}).waitFor();assert.ok((await page.locator('main').innerText()).includes('Fixture bridge'));assert.ok((await page.locator('main').innerText()).includes('10.23.45.67'));assert.equal(await page.locator('#launch-credentials input').count(),0);assert.equal(await page.getByRole('button',{name:'Read bridge'}).count(),0);await page.getByRole('button',{name:'Open web interface'}).click();await page.waitForFunction(()=>window.calls.some(x=>x.command==='open_bridge_ui'));assert.equal((await page.evaluate(()=>window.calls.find(x=>x.command==='open_bridge_ui'))).args.id,'verified');assert.deepEqual(errors,[]);
 console.log('PASS manual IP after empty discovery, localized UI, error recovery and verified browser-only selection');
}finally{await browser.close();}
