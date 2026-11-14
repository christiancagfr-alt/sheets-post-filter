import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import vm from 'node:vm';
import {webcrypto} from 'node:crypto';
if(!globalThis.crypto)globalThis.crypto=webcrypto;
const load=async path=>import('data:text/javascript;base64,'+Buffer.from(await fs.readFile(new URL(path,import.meta.url),'utf8')).toString('base64'));
for(const folder of ['summary-gallery','promo-site']) {
  const {default:worker}=await load(`../${folder}/gallery-worker.js`);
  test(`${folder}: analytics validates origin, password, and does not access R2`,async()=>{
    const events=[];
    const env={GALLERY_ANALYTICS:{writeDataPoint:value=>events.push(value)},GALLERY_CACHE:new Proxy({},{get(){throw Error('must not read R2');}})};
    const body={visitor:'a'.repeat(32),session:'b'.repeat(32),seconds:30,started:Date.now()/1000-30};
    const request=(value=body,origin='https://example.com')=>new Request('https://example.com/api/visit',{method:'POST',headers:{origin,'content-type':'text/plain;charset=UTF-8'},body:JSON.stringify(value)});
    assert.equal((await worker.fetch(request(body,'https://other.example.com'),env)).status,403);
    assert.equal((await worker.fetch(request(),{...env,ACCESS_PASSWORD:'example-password',CACHE_PUBLISH_SECRET:'example-secret'})).status,401);
    assert.equal((await worker.fetch(request({...body,seconds:Infinity}),env)).status,400);
    assert.equal((await worker.fetch(request(),env)).status,204);
    const textBeacon=new Request('https://example.com/api/visit',{method:'POST',headers:{origin:'https://example.com','content-type':'text/plain;charset=UTF-8'},body:JSON.stringify({...body,session:'c'.repeat(32)})});
    assert.equal((await worker.fetch(textBeacon,env)).status,204);
    assert.equal((await worker.fetch(request(),env)).status,204);
    assert.equal(events.length,2);
    assert.deepEqual(events[0].indexes,['example.com']);
    assert.deepEqual(events[0].doubles,[30,body.started]);
  });
}
test('beacon emits once per foreground session and never starts polling',async()=>{
  const code=await fs.readFile(new URL('../summary-gallery/visit-beacon.js',import.meta.url),'utf8');
  const callbacks={},events=[],storage=new Map();let clock=0;
  const document={visibilityState:'visible',addEventListener:(name,cb)=>callbacks[name]=cb};
  const context={crypto:webcrypto,Date,JSON,Blob,document,performance:{now:()=>clock},
    navigator:{sendBeacon:(url,body)=>events.push({url,body})},
    localStorage:{getItem:key=>storage.get(key),setItem:(key,value)=>storage.set(key,value)},
    sessionStorage:{getItem:()=>''},window:{addEventListener:(name,cb)=>callbacks[name]=cb}};
  vm.runInNewContext(code,context);
  assert.equal(events.length,0);clock=30000;document.visibilityState='hidden';callbacks.visibilitychange();callbacks.pagehide();
  assert.equal(events.length,1);assert.equal(JSON.parse(await events[0].body.text()).seconds,30);
  clock=60000;document.visibilityState='visible';callbacks.visibilitychange();clock=90000;callbacks.pagehide();
  assert.equal(events.length,2);
  assert.equal(JSON.parse(await events[0].body.text()).visitor,JSON.parse(await events[1].body.text()).visitor);
  assert.equal(code.includes('setInterval'),false);
});
